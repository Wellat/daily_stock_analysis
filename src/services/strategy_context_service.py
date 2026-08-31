"""Build a MarketContext from the existing Strategy Lab data repository."""
from datetime import date, datetime
from src.core.strategies import MarketContext, InstrumentSnapshot, Bar, FactorSnapshot, MarketEvent, PositionSnapshot
from src.repositories.strategy_lab.data_repo import StrategyLabDataRepository

class StrategyContextService:
    def __init__(self, db_manager):
        self.data = StrategyLabDataRepository(db_manager)

    def convertible_bonds(self, *, trade_date: date, symbols=None, positions=None, account=None) -> MarketContext:
        rows = self.data.load_cb_backtest_rows(market="cn", start_date=trade_date, end_date=trade_date, symbols=symbols or [])
        instruments=[]; bars={}; factors={}; events={}
        for row in rows:
            symbol=row.get("bond_code") or row.get("symbol")
            if not symbol: continue
            instruments.append(InstrumentSnapshot(symbol, row.get("bond_name")))
            bars[symbol]=[Bar(trade_date, close=row.get("close"))]
            factors[symbol]=FactorSnapshot(row.get("premium_rate"), row.get("remaining_size"))
            if row.get("event_blocked"): events[symbol]=[MarketEvent("event_blocked", trade_date)]
        pos={k: (v if isinstance(v, PositionSnapshot) else PositionSnapshot(**v) if isinstance(v,dict) else PositionSnapshot(v)) for k,v in (positions or {}).items()}
        return MarketContext(datetime.now(), "cn", "convertible_bond", instruments, bars, factors, events, pos, account=account)
