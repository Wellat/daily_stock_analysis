from datetime import date
from src.core.strategy_lab.engine import StrategyLabEngine
from src.core.strategy_lab.models import StrategyLabRunResult, StrategyLabMetric, StrategyLabTradeResult, StrategyLabEquityPoint
from src.core.strategies import MarketContext, InstrumentSnapshot, Bar, FactorSnapshot, get_strategy
from src.core.strategies.execution_planner import ExecutionPlanner
from src.core.strategies.executors import BacktestExecutor

class UnifiedLowPremiumEngine(StrategyLabEngine):
    name = "unified_low_premium_v1"
    def __init__(self, dataset): self.dataset = dataset
    def run(self, config):
        dates = sorted({b.trade_date for bars in self.dataset.bars.values() for b in bars if config.start_date <= b.trade_date <= config.end_date})
        if not dates: raise ValueError("No instruments available for requested run")
        trades=[]; cash=config.initial_cash; equity=[]
        for day in dates:
            instruments=[]; bars={}; factors={}; events={}
            for item in self.dataset.instruments:
                bs=[b for b in self.dataset.bars[item.canonical_id] if b.trade_date == day]
                if not bs: continue
                b=bs[-1]; instruments.append(InstrumentSnapshot(item.symbol,item.name)); bars[item.symbol]=[Bar(day,b.close)]; factors[item.symbol]=FactorSnapshot(b.cb_premium_rate,b.remaining_size)
                if b.event_blocked: events[item.symbol]=[]
            context=MarketContext(__import__('datetime').datetime.now(),config.market,config.instrument_type,instruments,bars,factors,events, cash=cash)
            decisions=get_strategy("low-premium").evaluate(context, parameters=config.parameters)
            plan=ExecutionPlanner().plan(decisions, context, cash=cash, lot_size=int(config.parameters.get("lot_size",10)))
            fills=BacktestExecutor().execute(plan, trade_date=day, prices={s: bs[-1].close for s,bs in bars.items()})
            for f in fills:
                trades.append(StrategyLabTradeResult(day,f"{config.market}.convertible_bond.{f.symbol}",f.symbol,config.market,config.instrument_type,f.side,f.quantity,f.price,f.amount,reason=f"{f.side}_unified"))
                cash += -f.amount if f.side == "buy" else f.amount
            equity.append(StrategyLabEquityPoint(day,cash,cash,0))
        metric=StrategyLabMetric(0.0,None,0.0,None,None,None,None,len(trades),len(equity),{"engine":self.name})
        return StrategyLabRunResult(cash,None,metric,trades,equity)
