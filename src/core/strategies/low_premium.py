from .base import StrategyBase, StrategyDecision
from .context import MarketContext

def _is_st_stock(stock_name) -> bool:
    """正股名称含 ST（覆盖 ST/*ST/S*ST 等风险警示形态）即视为 ST 正股。"""
    return bool(stock_name) and "ST" in str(stock_name).upper()

class LowPremiumStrategy(StrategyBase):
    strategy_id = "low-premium"
    name = "Low Premium Rotation"
    description = "Select convertible bonds by the lowest conversion premium rate."
    version = "v1"
    parameter_definitions = (
        {"key":"max_positions","label":"最大持仓数","type":"integer","default":2,"min":1,"max":50},
        {"key":"per_position_cash","label":"单债目标资金","type":"number","default":10000,"min":100},
        {"key":"per_position_quantity","label":"单债固定张数（0=按目标资金）","type":"integer","default":0,"min":0,"max":5000},
        {"key":"lot_size","label":"最小交易单位","type":"integer","default":10,"min":1},
        {"key":"max_abs_premium","label":"最大溢价率","type":"number","default":200,"min":0},
        {"key":"exclude_event_blocked","label":"排除风险事件","type":"boolean","default":True},
        {"key":"exclude_st","label":"排除正股ST","type":"boolean","default":False},
        {"key":"account_capital","label":"账户基准资金（0=不限）","type":"number","default":0,"min":0},
        {"key":"max_position_pct","label":"单只最大权重%（0=不限）","type":"number","default":0,"min":0,"max":100},
    )
    def evaluate(self, context: MarketContext, *, mode="rebalance", parameters=None):
        self.validate_context(context); p=self.parameters(parameters)
        if mode == "event_check":
            # 事件退出与买入排除共用 exclude_event_blocked：关闭后强赎/下修/回售
            # 提醒既不排除候选也不触发持仓退出；最后交易日强退在服务层，不受此开关影响。
            out=[]
            for symbol,pos in context.positions.items():
                events=[e for e in context.events.get(symbol,[]) if e.blocking]
                if events and p["exclude_event_blocked"]:
                    qty=pos.available
                    out.append(StrategyDecision("exit" if qty>0 else "blocked", symbol=symbol, suggested_quantity=qty if qty>0 else None,
                    reason=";".join(e.event_type for e in events), decision_data={"event_types":[e.event_type for e in events]},
                    risk_status="blocked" if qty<=0 else "passed"))
                else: out.append(StrategyDecision("hold", symbol=symbol, reason="event_exit_disabled" if events else "no_blocking_event"))
            return out
        candidates=[]
        for i in context.instruments:
            if not i.tradable: continue
            f=context.factors.get(i.symbol); bars=context.bars.get(i.symbol,[])
            close=bars[-1].close if bars else None
            if not f or f.premium_rate is None or close is None or close<=0: continue
            blocked=any(e.blocking for e in context.events.get(i.symbol,[]))
            if abs(f.premium_rate)>p["max_abs_premium"] or (p["exclude_event_blocked"] and blocked): continue
            if p["exclude_st"] and _is_st_stock(f.values.get("stock_name")): continue
            candidates.append((f.premium_rate,i,close,f,blocked))
        fixed_qty=int(p.get("per_position_quantity") or 0)
        lot=max(int(p["lot_size"]),1)
        # 单只权重上限：账户权益在 QMT 链路无资金数据，按 account_capital 基准折算，
        # 0 表示不启用；上限只约束新建仓金额，存量持仓漂移不做修剪。
        cap_amount=0.0
        if float(p["account_capital"])>0 and float(p["max_position_pct"])>0:
            cap_amount=float(p["account_capital"])*float(p["max_position_pct"])/100.0
        out=[]
        for rank,(premium,i,close,f,_) in enumerate(sorted(candidates,key=lambda x:x[0])[:p["max_positions"]],1):
            # Quantity conversion is an execution concern.  Emit the portfolio
            # intent (target amount or fixed lots) and let ExecutionPlanner apply
            # prices, lot-size and account-risk constraints consistently in
            # live/backtest.
            if fixed_qty>0:
                qty=int(fixed_qty/lot)*lot
                if cap_amount>0: qty=min(qty,int(cap_amount/close/lot)*lot)
                if qty<=0: continue
                out.append(StrategyDecision("buy",i.symbol,i.name,suggested_quantity=qty,reason="lowest_premium",
                decision_data={"premium_rate":premium,"close":close,"remaining_size":f.remaining_size,"rank":rank,
                "filter_results":{"premium_limit":True,"event_blocked":False},"sizing":"fixed_quantity",
                "position_cap_amount":round(cap_amount,2) if cap_amount>0 else None}))
            else:
                amount=float(p["per_position_cash"])
                if cap_amount>0: amount=min(amount,cap_amount)
                out.append(StrategyDecision("buy",i.symbol,i.name,target_amount=amount,reason="lowest_premium",
                decision_data={"premium_rate":premium,"close":close,"remaining_size":f.remaining_size,"rank":rank,
                "filter_results":{"premium_limit":True,"event_blocked":False},
                "position_cap_amount":round(cap_amount,2) if cap_amount>0 else None}))
        return out
