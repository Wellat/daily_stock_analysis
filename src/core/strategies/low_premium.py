from .base import StrategyBase, StrategyDecision
from .context import MarketContext

class LowPremiumStrategy(StrategyBase):
    strategy_id = "low-premium"
    name = "Low Premium Rotation"
    description = "Select convertible bonds by the lowest conversion premium rate."
    version = "v1"
    parameter_definitions = (
        {"key":"max_positions","label":"最大持仓数","type":"integer","default":2,"min":1,"max":50},
        {"key":"per_position_cash","label":"单债目标资金","type":"number","default":10000,"min":100},
        {"key":"lot_size","label":"最小交易单位","type":"integer","default":10,"min":1},
        {"key":"max_abs_premium","label":"最大溢价率","type":"number","default":200,"min":0},
        {"key":"exclude_event_blocked","label":"排除风险事件","type":"boolean","default":True},
    )
    def evaluate(self, context: MarketContext, *, mode="rebalance", parameters=None):
        self.validate_context(context); p=self.parameters(parameters)
        if mode == "event_check":
            out=[]
            for symbol,pos in context.positions.items():
                events=[e for e in context.events.get(symbol,[]) if e.blocking]
                if events:
                    qty=pos.available
                    out.append(StrategyDecision("exit" if qty>0 else "blocked", symbol=symbol, suggested_quantity=qty if qty>0 else None, reason=";".join(e.event_type for e in events), decision_data={"event_types":[e.event_type for e in events]}, risk_status="blocked" if qty<=0 else "passed"))
                else: out.append(StrategyDecision("hold", symbol=symbol, reason="no_blocking_event"))
            return out
        candidates=[]
        for i in context.instruments:
            if not i.tradable: continue
            f=context.factors.get(i.symbol); bars=context.bars.get(i.symbol,[])
            close=bars[-1].close if bars else None
            if not f or f.premium_rate is None or close is None or close<=0: continue
            blocked=any(e.blocking for e in context.events.get(i.symbol,[]))
            if abs(f.premium_rate)>p["max_abs_premium"] or (p["exclude_event_blocked"] and blocked): continue
            candidates.append((f.premium_rate,i,close,f,blocked))
        out=[]
        for rank,(premium,i,close,f,_) in enumerate(sorted(candidates,key=lambda x:x[0])[:p["max_positions"]],1):
            # Quantity conversion is an execution concern.  Emit the portfolio
            # intent (target amount) and let ExecutionPlanner apply prices,
            # lot-size and account-risk constraints consistently in live/backtest.
            out.append(StrategyDecision("buy",i.symbol,i.name,target_amount=p["per_position_cash"],reason="lowest_premium",decision_data={"premium_rate":premium,"close":close,"remaining_size":f.remaining_size,"rank":rank,"filter_results":{"premium_limit":True,"event_blocked":False}}))
        return out
