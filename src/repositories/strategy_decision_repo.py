import json
from datetime import date, datetime
from uuid import uuid4
from sqlalchemy import select
from src.storage import StrategyDecisionRecord

class StrategyDecisionRepository:
    def __init__(self, db): self.db = db
    def create(self, *, strategy_id, mode, trade_date: date, action, symbol=None, **kwargs):
        uid = kwargs.pop("decision_uid", None) or uuid4().hex
        row = StrategyDecisionRecord(decision_uid=uid, strategy_id=strategy_id, mode=mode,
            trade_date=trade_date, action=action, as_of=kwargs.pop("as_of", datetime.now()),
            decision_data_json=json.dumps(kwargs.pop("decision_data", {}), ensure_ascii=False), **kwargs)
        with self.db.get_session() as s:
            s.add(row); s.commit(); s.refresh(row); return row
    def list(self, *, trade_date=None, strategy_id=None):
        with self.db.get_session() as s:
            q=select(StrategyDecisionRecord).order_by(StrategyDecisionRecord.id)
            if trade_date: q=q.where(StrategyDecisionRecord.trade_date==trade_date)
            if strategy_id: q=q.where(StrategyDecisionRecord.strategy_id==strategy_id)
            return list(s.execute(q).scalars())
