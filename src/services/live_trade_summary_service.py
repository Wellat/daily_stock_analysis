# -*- coding: utf-8 -*-
"""盘后实盘成交汇总（每个交易日收盘后推送飞书）。

数据源为 portfolio_trades（实盘成交记录真源，含 QMT 同步与手工登记），
按买卖方向汇总成一条消息直发飞书群机器人，每日必发一条（无成交发
「今日无成交」）。不落库：每日一次的语义由 runtime scheduler 的任务状态保证。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from src.storage import (
    DatabaseManager,
    PortfolioAccount,
    PortfolioTrade,
    StrategyLabCbBasic,
    TradingOrder,
)

logger = logging.getLogger(__name__)


def _quantity_unit(symbol: str) -> str:
    """按代码形态给数量单位：可转债按张，ETF 按份，其余按股。"""
    code = str(symbol).split(".")[0]
    if len(code) == 6 and code[:2] in {"11", "12", "13"}:
        return "张"
    if len(code) == 6 and code[0] in {"5", "1"}:
        return "份"
    return "股"


class LiveTradeSummaryService:
    """汇总当日实盘成交并推送飞书群通知（FEISHU_WEBHOOK_URL），每日必发一条（无成交发「今日无成交」）。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self._fetcher: Any = None

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def run(self, *, trade_date: date) -> Dict[str, Any]:
        rows = self._load_trades(trade_date)
        if not rows:
            payload = {"trade_date": trade_date.isoformat(), "trade_count": 0,
                       "buy_count": 0, "sell_count": 0, "empty": True}
            message = self.build_message(payload)
            payload["feishu_sent"] = self._send_feishu(message)
            if not payload["feishu_sent"]:
                logger.error("[LiveTradeSummary] feishu send failed for %s", trade_date)
            return payload
        payload = self.summarize(trade_date, rows)
        payload["feishu_sent"] = self._send_feishu(self.build_message(payload))
        if not payload["feishu_sent"]:
            logger.error("[LiveTradeSummary] feishu send failed for %s", trade_date)
        return payload

    # ------------------------------------------------------------------
    # 汇总与消息
    # ------------------------------------------------------------------

    def _load_trades(self, trade_date: date) -> List[PortfolioTrade]:
        with self.db.get_session() as session:
            return list(session.execute(
                select(PortfolioTrade)
                .where(PortfolioTrade.trade_date == trade_date)
                .order_by(PortfolioTrade.id.asc())
            ).scalars().all())

    def summarize(self, trade_date: date, rows: List[PortfolioTrade]) -> Dict[str, Any]:
        account_names = self._account_names({row.account_id for row in rows})
        symbol_names = self._resolve_symbol_names(rows)
        multi_account = len(account_names) > 1

        def _label(row: PortfolioTrade) -> str:
            # 名称优先级：成交行自带 > 本地兜底补全（订单记录/转债基础表/实时行情）
            name = ((row.symbol_name or "").strip()) or symbol_names.get(row.symbol, "")
            text = f"{row.symbol} {name}".strip() if name else row.symbol
            if multi_account:
                return f"[{account_names.get(row.account_id, row.account_id)}] {text}"
            return text

        buys, sells = [], []
        for row in rows:
            entry = {
                "symbol": row.symbol,
                "label": _label(row),
                "quantity": float(row.quantity or 0),
                "price": float(row.price or 0),
                "amount": round(float(row.quantity or 0) * float(row.price or 0), 2),
                "fee": round(float(row.fee or 0) + float(row.tax or 0), 2),
            }
            (sells if row.side == "sell" else buys).append(entry)

        return {
            "trade_date": trade_date.isoformat(),
            "trade_count": len(rows),
            "buy_count": len(buys),
            "sell_count": len(sells),
            "buys": buys,
            "sells": sells,
            "buy_amount": round(sum(item["amount"] for item in buys), 2),
            "sell_amount": round(sum(item["amount"] for item in sells), 2),
            "total_fee": round(sum(item["fee"] for item in buys + sells), 2),
        }

    @staticmethod
    def build_message(payload: Dict[str, Any]) -> str:
        lines = [
            f"实盘成交汇总 · {payload['trade_date']}"
            f"（买入 {payload['buy_count']} ｜卖出 {payload['sell_count']}）",
        ]
        if payload.get("empty"):
            lines.extend(["", "📭 今日无实盘成交"])
            return "\n".join(lines)
        for icon, key in (("🔴 买入", "buys"), ("🟢 卖出", "sells")):
            items = payload.get(key) or []
            if not items:
                continue
            lines.append("")
            lines.append(icon)
            for item in items:
                unit = _quantity_unit(item["symbol"])
                lines.append(
                    f"• {item['label']} {item['quantity']:g}{unit} @ {item['price']:g}"
                    f"＝{item['amount']:.2f}元"
                )
        net = round(payload["buy_amount"] - payload["sell_amount"], 2)
        lines.extend([
            "",
            f"合计：买入 {payload['buy_amount']:.2f} ｜卖出 {payload['sell_amount']:.2f}"
            f" ｜净{'买入' if net >= 0 else '卖出'} {abs(net):.2f} ｜费用 {payload['total_fee']:.2f}（元）",
        ])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------

    @staticmethod
    def _send_feishu(message: str) -> bool:
        try:
            from src.notification import NotificationService
            return bool(NotificationService().send_to_feishu(message, title="成交汇总"))
        except Exception:
            logger.exception("[LiveTradeSummary] feishu send raised")
            return False

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _resolve_symbol_names(self, rows: List[PortfolioTrade]) -> Dict[str, str]:
        """对成交行缺名称的标的做兜底补全：订单记录 → 转债基础表 → 实时行情。

        实时行情（ETF/股票名称唯一来源）失败时静默降级为仅代码展示。
        """
        missing = sorted({row.symbol for row in rows if not (row.symbol_name or "").strip()})
        if not missing:
            return {}

        names: Dict[str, str] = {}
        remaining = set(missing)
        with self.db.get_session() as session:
            for symbol in sorted(remaining):
                order_row = session.execute(
                    select(TradingOrder.symbol_name)
                    .where(
                        TradingOrder.symbol == symbol,
                        TradingOrder.symbol_name.is_not(None),
                        TradingOrder.symbol_name != "",
                    )
                    .order_by(TradingOrder.id.desc())
                    .limit(1)
                ).first()
                if order_row and order_row[0]:
                    names[symbol] = order_row[0]
                    remaining.discard(symbol)
            if remaining:
                for bond_code, bond_name in session.execute(
                    select(StrategyLabCbBasic.bond_code, StrategyLabCbBasic.bond_name)
                    .where(StrategyLabCbBasic.bond_code.in_(remaining))
                ):
                    if bond_name and bond_code in remaining:
                        names[bond_code] = bond_name
                        remaining.discard(bond_code)
        for symbol in sorted(remaining):
            quote_name = self._fetch_quote_name(symbol)
            if quote_name:
                names[symbol] = quote_name
        return names

    def _fetch_quote_name(self, symbol: str) -> str:
        if self._fetcher is None:
            from data_provider import DataFetcherManager
            self._fetcher = DataFetcherManager()
        try:
            quote = self._fetcher.get_realtime_quote(symbol, log_final_failure=False)
            return (getattr(quote, "name", "") or "").strip()
        except Exception:
            logger.warning("[LiveTradeSummary] quote fetch failed for %s, name fallback skipped", symbol)
            return ""

    def _account_names(self, account_ids: set) -> Dict[int, str]:
        if not account_ids:
            return {}
        with self.db.get_session() as session:
            rows = session.execute(
                select(PortfolioAccount.id, PortfolioAccount.name)
                .where(PortfolioAccount.id.in_(account_ids))
            ).all()
        return {row.id: row.name for row in rows}
