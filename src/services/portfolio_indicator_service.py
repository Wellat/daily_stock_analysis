# -*- coding: utf-8 -*-
"""持仓技术指标批量计算（持仓总览增强列 + 行展开详情）。

指标全部由 ``stock_daily`` 收盘/成交量序列**现算**（表内 ma5/ma20/
volume_ratio 列当前不入库，pct_chg 仅场外基金有值，不可直接使用）：
均线与乖离、均线形态、连涨连跌、周/月涨跌、52 周位置、量比、60 日
收盘迷你曲线；转债标的附溢价率/强赎进度/到期天数（strategy_lab_cb_*）。
持仓行情缺失的标的返回全空占位，不阻塞整体响应。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from src.storage import (
    DatabaseManager,
    PortfolioAccount,
    PortfolioPosition,
    PortfolioPositionLot,
    StockDaily,
    StrategyLabCbBasic,
    StrategyLabCbDailyFactor,
)

logger = logging.getLogger(__name__)

COST_METHOD = "fifo"
BARS_LOOKBACK_DAYS = 400       # 行情窗口（自然日），覆盖 52 周位置所需 ~250 根
SPARKLINE_BARS = 60
WEEK_BARS = 5
MONTH_BARS = 20
YEAR_BARS = 250
VOLUME_RATIO_WINDOW = 5


class PortfolioIndicatorService:
    """为当前持仓批量计算展示用技术指标（只读，无副作用）。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def get_holding_indicators(self, account_id: Optional[int] = None) -> Dict[str, Any]:
        """按 symbol 返回当前持仓的技术指标；account_id 缺省为全部活跃账户。"""
        accounts = self._load_accounts(account_id)
        if not accounts:
            return {"as_of": date.today().isoformat(), "items": []}
        account_ids = [account.id for account in accounts]

        positions = self._load_positions(account_ids)
        symbols = sorted({row.symbol for row in positions})
        if not symbols:
            return {"as_of": date.today().isoformat(), "items": []}

        bars = self._load_bars(symbols)
        holding_days = self._holding_days(account_ids)
        cb_extra = self._cb_extras(symbols)

        items: List[Dict[str, Any]] = []
        for symbol in symbols:
            items.append(self._build_item(symbol, bars.get(symbol, []), holding_days, cb_extra.get(symbol)))
        return {"as_of": date.today().isoformat(), "items": items}

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------

    def _build_item(
        self,
        symbol: str,
        bars: List[Dict[str, Any]],
        holding_days: Dict[Any, Dict[str, int]],
        cb_extra: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        closes = [float(bar["close"]) for bar in bars if bar["close"] is not None]
        item: Dict[str, Any] = {
            "symbol": symbol,
            "last_bar_date": bars[-1]["date"].isoformat() if bars else None,
            "last_close": closes[-1] if closes else None,
            "ma5": self._sma(closes, 5),
            "ma10": self._sma(closes, 10),
            "ma20": self._sma(closes, 20),
            "bias_ma5_pct": None,
            "bias_ma10_pct": None,
            "bias_ma20_pct": None,
            "trend_pattern": "unknown",
            "streak_days": None,
            "week_change_pct": None,
            "month_change_pct": None,
            "pos_52w_pct": None,
            "volume_ratio": None,
            "sparkline": [
                {"date": bar["date"].isoformat(), "close": float(bar["close"])}
                for bar in bars[-SPARKLINE_BARS:]
                if bar["close"] is not None
            ],
            "holding_days_by_account": holding_days.get(symbol, {}),
            "cb": cb_extra,
        }
        if not closes:
            return item

        last = closes[-1]
        for window, key in ((5, "bias_ma5_pct"), (10, "bias_ma10_pct"), (20, "bias_ma20_pct")):
            ma = self._sma(closes, window)
            item[key] = round((last / ma - 1.0) * 100.0, 2) if ma else None

        item["trend_pattern"] = self._trend_pattern(
            item["ma5"], item["ma10"], item["ma20"], last
        )
        item["streak_days"] = self._streak(closes)
        item["week_change_pct"] = self._window_change(closes, WEEK_BARS)
        item["month_change_pct"] = self._window_change(closes, MONTH_BARS)
        item["pos_52w_pct"] = self._pos_52w(closes)
        item["volume_ratio"] = self._volume_ratio(bars)
        if cb_extra is not None:
            cb_extra["price_band"] = self._cb_price_band(last)
        return item

    @staticmethod
    def _sma(closes: List[float], window: int) -> Optional[float]:
        if len(closes) < window or window <= 0:
            return None
        return round(sum(closes[-window:]) / window, 6)

    @staticmethod
    def _cb_price_band(close: Optional[float]) -> Optional[str]:
        if close is None:
            return None
        if close <= 110:
            return "<=110"
        if close <= 130:
            return "110-130"
        if close <= 150:
            return "130-150"
        return ">150"

    @staticmethod
    def _trend_pattern(
        ma5: Optional[float], ma10: Optional[float], ma20: Optional[float], last: float
    ) -> str:
        if ma5 is None or ma10 is None or ma20 is None:
            return "unknown"
        if ma5 > ma10 > ma20:
            return "bull"
        if ma5 < ma10 < ma20:
            return "bear"
        return "mixed"

    @staticmethod
    def _streak(closes: List[float]) -> Optional[int]:
        """带符号连涨/连跌天数：正=连涨，负=连跌。"""
        if len(closes) < 2:
            return None
        direction = 0
        if closes[-1] > closes[-2]:
            direction = 1
        elif closes[-1] < closes[-2]:
            direction = -1
        else:
            return 0
        streak = 1
        for prev, curr in zip(reversed(closes[:-2]), reversed(closes[1:-1])):
            if direction == 1 and curr > prev:
                streak += 1
            elif direction == -1 and curr < prev:
                streak += 1
            else:
                break
        return streak * direction

    @staticmethod
    def _window_change(closes: List[float], window: int) -> Optional[float]:
        if len(closes) <= window:
            return None
        base = closes[-(window + 1)]
        if not base:
            return None
        return round((closes[-1] / base - 1.0) * 100.0, 2)

    @staticmethod
    def _pos_52w(closes: List[float]) -> Optional[float]:
        window = closes[-YEAR_BARS:]
        if len(window) < 2:
            return None
        high, low = max(window), min(window)
        if high <= low:
            return 50.0
        return round((window[-1] - low) / (high - low) * 100.0, 1)

    @staticmethod
    def _volume_ratio(bars: List[Dict[str, Any]]) -> Optional[float]:
        """最新成交量 / 前 5 根均量；场外基金（.OF）与缺量标的返回 None。"""
        code = str(bars[-1]["code"]) if bars else ""
        if code.endswith(".OF"):
            return None
        volumes = [bar["volume"] for bar in bars[-(VOLUME_RATIO_WINDOW + 1):]]
        if len(volumes) < VOLUME_RATIO_WINDOW + 1 or any(v is None for v in volumes):
            return None
        latest = float(volumes[-1])
        avg = sum(float(v) for v in volumes[:-1]) / VOLUME_RATIO_WINDOW
        if avg <= 0:
            return None
        return round(latest / avg, 2)

    # ------------------------------------------------------------------
    # 数据装载
    # ------------------------------------------------------------------

    def _load_accounts(self, account_id: Optional[int]) -> List[PortfolioAccount]:
        with self.db.get_session() as session:
            query = select(PortfolioAccount).where(PortfolioAccount.is_active.is_(True))
            if account_id is not None:
                query = query.where(PortfolioAccount.id == account_id)
            return list(session.execute(query.order_by(PortfolioAccount.id.asc())).scalars().all())

    def _load_positions(self, account_ids: List[int]) -> List[PortfolioPosition]:
        with self.db.get_session() as session:
            return list(
                session.execute(
                    select(PortfolioPosition)
                    .where(
                        PortfolioPosition.account_id.in_(account_ids),
                        PortfolioPosition.cost_method == COST_METHOD,
                        PortfolioPosition.quantity > 0,
                    )
                    .order_by(PortfolioPosition.symbol.asc())
                ).scalars().all()
            )

    def _load_bars(self, symbols: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """单条窗口查询拉全部持仓日线，按 symbol 分组（日期升序）。"""
        since = date.today() - timedelta(days=BARS_LOOKBACK_DAYS)
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockDaily.code, StockDaily.date, StockDaily.close, StockDaily.volume)
                .where(StockDaily.code.in_(symbols), StockDaily.date >= since)
                .order_by(StockDaily.code.asc(), StockDaily.date.asc())
            ).all()
        for code, bar_date, close, volume in rows:
            grouped.setdefault(str(code), []).append(
                {"code": code, "date": bar_date, "close": close, "volume": volume}
            )
        return grouped

    def _holding_days(self, account_ids: List[int]) -> Dict[str, Dict[str, int]]:
        """{symbol: {account_id: 持有天数}}，取该账户最早未平仓 lot 的开仓日。"""
        from sqlalchemy import func

        today = date.today()
        result: Dict[str, Dict[str, int]] = {}
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    PortfolioPositionLot.symbol,
                    PortfolioPositionLot.account_id,
                    func.min(PortfolioPositionLot.open_date),
                ).where(
                    PortfolioPositionLot.account_id.in_(account_ids),
                    PortfolioPositionLot.cost_method == COST_METHOD,
                    PortfolioPositionLot.remaining_quantity > 0,
                ).group_by(PortfolioPositionLot.symbol, PortfolioPositionLot.account_id)
            ).all()
        for symbol, account_id, min_open in rows:
            if min_open is None:
                continue
            result.setdefault(str(symbol), {})[str(int(account_id))] = (today - min_open).days
        return result

    def _cb_extras(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """转债持仓的溢价率/强赎进度/到期信息（最新因子行 ≤ 今日）。"""
        result: Dict[str, Dict[str, Any]] = {}
        if not symbols:
            return result
        today = date.today()
        with self.db.get_session() as session:
            basics = {
                row.bond_code: row
                for row in session.execute(
                    select(StrategyLabCbBasic).where(StrategyLabCbBasic.bond_code.in_(symbols))
                ).scalars().all()
            }
            for code, basic in basics.items():
                factor = session.execute(
                    select(
                        StrategyLabCbDailyFactor.trade_date,
                        StrategyLabCbDailyFactor.premium_rate,
                        StrategyLabCbDailyFactor.remaining_size,
                    )
                    .where(
                        StrategyLabCbDailyFactor.bond_code == code,
                        StrategyLabCbDailyFactor.trade_date <= today,
                    )
                    .order_by(StrategyLabCbDailyFactor.trade_date.desc())
                    .limit(1)
                ).first()
                countdown = ""
                if basic.terms_json:
                    try:
                        countdown = str(
                            (json.loads(basic.terms_json) or {}).get("force_redeem_countdown") or ""
                        )
                    except (TypeError, ValueError):
                        countdown = ""
                result[code] = {
                    "premium_rate": (
                        round(float(factor[1]), 2) if factor and factor[1] is not None else None
                    ),
                    "factor_date": factor[0].isoformat() if factor and factor[0] else None,
                    "remaining_size": (
                        factor[2] if factor and factor[2] is not None else basic.remaining_size
                    ),
                    "days_to_maturity": (
                        (basic.maturity_date - today).days if basic.maturity_date else None
                    ),
                    "force_redeem_countdown": countdown or None,
                }
        return result
