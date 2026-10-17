import json
from datetime import date, datetime
from uuid import uuid4
from sqlalchemy import select
from src.storage import StrategyDecisionRecord

class StrategyDecisionRepository:
    def __init__(self, db): self.db = db
    def create(self, *, strategy_id, mode, trade_date: date, action, symbol=None, **kwargs):
        uid = kwargs.pop("decision_uid", None) or uuid4().hex
        # symbol 必须显式传入模型：此前仅作为本函数参数被吞掉，落库恒为 NULL
        row = StrategyDecisionRecord(decision_uid=uid, strategy_id=strategy_id, mode=mode,
            trade_date=trade_date, action=action, symbol=symbol, as_of=kwargs.pop("as_of", datetime.now()),
            decision_data_json=json.dumps(kwargs.pop("decision_data", {}), ensure_ascii=False), **kwargs)
        with self.db.get_session() as s:
            s.add(row); s.commit(); s.refresh(row); return row
    def list(self, *, trade_date=None, strategy_id=None):
        with self.db.get_session() as s:
            q=select(StrategyDecisionRecord).order_by(StrategyDecisionRecord.id)
            if trade_date: q=q.where(StrategyDecisionRecord.trade_date==trade_date)
            if strategy_id: q=q.where(StrategyDecisionRecord.strategy_id==strategy_id)
            return list(s.execute(q).scalars())
    def list_by_reason_since(self, *, reason, trade_date_since, account=None, strategy_id=None):
        """按 reason 拉取 trade_date >= trade_date_since 的决策（止损冷却期推导用）。"""
        with self.db.get_session() as s:
            q=select(StrategyDecisionRecord).where(StrategyDecisionRecord.reason==reason,
                StrategyDecisionRecord.trade_date>=trade_date_since)
            if account: q=q.where(StrategyDecisionRecord.account==account)
            if strategy_id: q=q.where(StrategyDecisionRecord.strategy_id==strategy_id)
            return list(s.execute(q).scalars())
    def latest_signal_actions(self, *, config_id, symbols):
        """每个标的在指定实盘配置下最近一次 buy/sell 信号决策（ETF 信号持仓状态机用）。

        返回 {symbol: "buy"|"sell"}；无历史则不在结果中（视为未持仓）。
        """
        from src.storage import LiveStrategyRun
        rows_in = [str(x).strip().lower().split(".")[-1] for x in symbols if str(x).strip()]
        if not rows_in:
            return {}
        with self.db.get_session() as s:
            q=(select(StrategyDecisionRecord)
                .join(LiveStrategyRun, StrategyDecisionRecord.live_run_id==LiveStrategyRun.id)
                .where(LiveStrategyRun.config_id==config_id,
                       StrategyDecisionRecord.action.in_(("buy","sell")),
                       StrategyDecisionRecord.symbol.in_(rows_in))
                .order_by(StrategyDecisionRecord.id.desc()))
            out={}
            for row in s.execute(q).scalars():
                if row.symbol not in out:
                    out[row.symbol]=row.action
            return out
