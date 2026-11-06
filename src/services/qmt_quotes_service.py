# -*- coding: utf-8 -*-
"""QMT 行情桥订阅管理服务。

维护 Redis 里的订阅列表 ``qmt:cfg:symbols``（hash，field=symbols，整组替换语义，
契约见 ``docs/qmt-quotes-link-01.md`` §7）：

- 自动纳入全部活跃资金账户的沪深持仓（``QMT_AUTO_INCLUDE_HOLDINGS``）
- 自动纳入启用中告警规则的沪深标的（``QMT_INCLUDE_ALERT_SYMBOLS``；
  portfolio/market scope 跳过——持仓 scope 已被上一条覆盖，market 为聚合口径）
- 手动追加 ``QMT_MANUAL_SYMBOLS``（页面维护，经 SystemConfigService 写 .env）

同步入口：定时任务（``runtime_scheduler`` 每 N 分钟）与 ``POST /api/v1/qmt-quotes/subscription/sync``。

注意：订阅列表以"整组覆盖"写入，多实例并发写会互相覆盖（桥接文档 §7.2），
按单实例部署设计。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from data_provider.qmt_fetcher import (
    QMT_SYMBOL_PATTERN,
    QmtRedisClient,
    _to_qmt_symbol,
    get_qmt_client,
)
from src.config import get_config
from src.storage import DatabaseManager
from src.utils.sanitize import sanitize_diagnostic_text

logger = logging.getLogger(__name__)

# 告警规则标的收集时跳过的 scope（持仓 scope 由持仓枚举覆盖；market 为聚合口径）
_ALERT_SYMBOL_SCOPES = {"single_symbol", "watchlist"}


class QmtQuotesServiceError(Exception):
    """订阅管理业务错误（写失败、非法标的等）。"""


class QmtQuotesService:
    """qmt:cfg:symbols 订阅列表的读取、合并与整组写回。"""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        client: Optional[QmtRedisClient] = None,
    ) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        self._client = client or get_qmt_client()

    @property
    def client(self) -> QmtRedisClient:
        return self._client

    # ------------------------------------------------------------------
    # 三个来源集合
    # ------------------------------------------------------------------
    def list_holdings_symbols(self) -> List[str]:
        """全部活跃资金账户的沪深持仓（自动剔除港/美、场外基金、北交所）。"""
        from src.repositories.portfolio_repo import PortfolioRepository

        identities = PortfolioRepository(self.db).list_cached_position_identities()
        symbols: Set[str] = set()
        for market, symbol in identities:
            if str(market or "").lower() != "cn":
                continue
            qmt_symbol = _to_qmt_symbol(symbol)
            if qmt_symbol:
                symbols.add(qmt_symbol)
        return sorted(symbols)

    def list_alert_symbols(self) -> List[str]:
        """启用中告警规则的沪深标的（single_symbol 直取；watchlist 展开自选股）。"""
        from src.repositories.alert_repo import AlertRepository

        repo = AlertRepository(self.db)
        config = get_config()
        symbols: Set[str] = set()

        page, page_size = 1, 100
        while True:
            rules, total = repo.list_rules(enabled=True, page=page, page_size=page_size)
            for rule in rules:
                scope = str(rule.target_scope or "").strip()
                if scope == "single_symbol":
                    candidates: List[str] = [str(rule.target or "")]
                elif scope == "watchlist":
                    refresh = getattr(config, "refresh_stock_list", None)
                    if callable(refresh):
                        try:
                            refresh()
                        except Exception as exc:  # noqa: BLE001 - 自选股刷新失败按当前值处理
                            logger.warning("[QMT订阅] 刷新自选股失败: %s", exc)
                    candidates = list(getattr(config, "stock_list", []) or [])
                else:
                    continue
                for candidate in candidates:
                    qmt_symbol = _to_qmt_symbol(candidate)
                    if qmt_symbol:
                        symbols.add(qmt_symbol)
            if page * page_size >= total:
                break
            page += 1
        return sorted(symbols)

    @staticmethod
    def list_manual_symbols() -> List[str]:
        """手动订阅列表（env ``QMT_MANUAL_SYMBOLS``，接受混写代码形态）。"""
        raw = str(getattr(get_config(), "qmt_manual_symbols", "") or "")
        symbols: Set[str] = set()
        for item in raw.split(","):
            qmt_symbol = _to_qmt_symbol(item.strip())
            if qmt_symbol:
                symbols.add(qmt_symbol)
        return sorted(symbols)

    # ------------------------------------------------------------------
    # 状态与详情
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_qmt_symbol(symbol: str) -> str:
        """校验并归一订阅标的（pattern 之外的输入直接拒绝，防拼 Redis key）。"""
        text = str(symbol or "").strip().upper()
        if not QMT_SYMBOL_PATTERN.match(text):
            raise QmtQuotesServiceError(f"非法标的代码（仅沪深 6 位码）: {symbol}")
        return text

    def get_quote_detail(self, symbol: str) -> Dict[str, Any]:
        """Redis 原始实时快照（排障视图，原样返回不换单位）。"""
        qmt_symbol = self._validate_qmt_symbol(symbol)
        alive = self._client.is_alive()
        data = self._client.get_quote(qmt_symbol) if alive else None
        return {"symbol": qmt_symbol, "alive": alive, "found": data is not None, "data": data}

    def get_daily_detail(self, symbol: str) -> Dict[str, Any]:
        """Redis 原始日K payload（最近 30 根，原样返回）。"""
        qmt_symbol = self._validate_qmt_symbol(symbol)
        alive = self._client.is_alive()
        data = self._client.get_daily(qmt_symbol) if alive else None
        return {"symbol": qmt_symbol, "alive": alive, "found": data is not None, "data": data}

    def get_status(self) -> Dict[str, Any]:
        config = get_config()
        heartbeat = None
        alive = False
        subscribed = 0
        if self._client.redis_lib_available:
            alive = self._client.is_alive()
            heartbeat = self._client.get_heartbeat() if alive else None
            subscribed = len(self._client.get_subscription())
        return {
            "enabled": bool(getattr(config, "qmt_quotes_enabled", False)),
            "redis_url": str(getattr(config, "qmt_redis_url", "") or ""),
            "redis_available": bool(self._client.redis_lib_available),
            "alive": alive,
            "heartbeat": heartbeat,
            "subscribed_count": subscribed,
            "auto_include_holdings": bool(getattr(config, "qmt_auto_include_holdings", False)),
            "include_alert_symbols": bool(getattr(config, "qmt_include_alert_symbols", False)),
            "manual_symbols": self.list_manual_symbols(),
            "holdings_count": len(self.list_holdings_symbols()),
            "alert_count": len(self.list_alert_symbols()),
        }

    def get_subscription_detail(self) -> Dict[str, Any]:
        """当前订阅 + 各来源集合 + 逐标的（名称、来源标签）（供页面展示）。"""
        holdings = set(self.list_holdings_symbols())
        alert_symbols = set(self.list_alert_symbols())
        manual = set(self.list_manual_symbols())
        current = self._client.get_subscription()

        name_map = self._build_symbol_name_map(
            set(current) | holdings | alert_symbols | manual
        )
        items: List[Dict[str, Any]] = []
        for symbol in sorted(set(current) | holdings | alert_symbols | manual):
            origins: List[str] = []
            if symbol in holdings:
                origins.append("holdings")
            if symbol in alert_symbols:
                origins.append("alert")
            if symbol in manual:
                origins.append("manual")
            if not origins:
                origins.append("stale")
            items.append({"symbol": symbol, "name": name_map.get(symbol, ""), "origins": origins})
        return {
            "symbols": items,
            "holdings": sorted(holdings),
            "alert_symbols": sorted(alert_symbols),
            "manual_symbols": sorted(manual),
        }

    def _build_symbol_name_map(self, symbols: Set[str]) -> Dict[str, str]:
        """订阅标的的名称映射（全部本地表，不做实时行情查询）。

        名称来源优先级：QMT 持仓快照（qmt_positions，最准）→ 可转债基础表
        （strategy_lab_cb_basic）→ 本地股票索引（get_index_stock_name）。
        查询失败的来源跳过，不影响其余名称。
        """
        bare_codes = {symbol[:6] for symbol in symbols if len(symbol) == 9}
        name_by_bare: Dict[str, str] = {}

        try:
            from sqlalchemy import select

            from src.storage import QmtPosition

            with self.db.get_session() as session:
                rows = session.execute(
                    select(QmtPosition.symbol, QmtPosition.name)
                ).all()
            for symbol, name in rows:
                text = str(name or "").replace(" ", "").strip()
                key = str(symbol or "").strip()[:6]
                if text and len(key) == 6 and key not in name_by_bare:
                    name_by_bare[key] = text
        except Exception as exc:  # noqa: BLE001 - 名称解析失败不阻塞订阅详情
            logger.debug("[QMT订阅] qmt_positions 名称读取失败: %s", exc)

        missing = bare_codes - set(name_by_bare)
        if missing:
            try:
                from sqlalchemy import select

                from src.storage import StrategyLabCbBasic

                with self.db.get_session() as session:
                    rows = session.execute(
                        select(StrategyLabCbBasic.bond_code, StrategyLabCbBasic.bond_name).where(
                            StrategyLabCbBasic.bond_code.in_(missing)
                        )
                    ).all()
                for bond_code, bond_name in rows:
                    text = str(bond_name or "").replace(" ", "").strip()
                    key = str(bond_code or "").strip()[:6]
                    if text and len(key) == 6 and key not in name_by_bare:
                        name_by_bare[key] = text
            except Exception as exc:  # noqa: BLE001
                logger.debug("[QMT订阅] cb_basic 名称读取失败: %s", exc)

        missing = bare_codes - set(name_by_bare)
        if missing:
            # 成交回报名称（QMT 回报自带，落库于 portfolio_trades.symbol_name）：
            # 覆盖已清仓 ETF 等不在持仓快照里的标的；trade_date 倒序取最新
            try:
                from sqlalchemy import select

                from src.storage import PortfolioTrade

                candidates = missing | {f"{code}.SH" for code in missing} | {f"{code}.SZ" for code in missing}
                with self.db.get_session() as session:
                    rows = session.execute(
                        select(PortfolioTrade.symbol, PortfolioTrade.symbol_name, PortfolioTrade.trade_date)
                        .where(PortfolioTrade.symbol.in_(candidates))
                        .order_by(PortfolioTrade.trade_date.desc())
                        .limit(200)
                    ).all()
                for symbol, symbol_name, _trade_date in rows:
                    text = str(symbol_name or "").replace(" ", "").strip()
                    key = str(symbol or "").strip()[:6]
                    if text and len(key) == 6 and key not in name_by_bare:
                        name_by_bare[key] = text
            except Exception as exc:  # noqa: BLE001
                logger.debug("[QMT订阅] portfolio_trades 名称读取失败: %s", exc)

        still_missing = bare_codes - set(name_by_bare)
        if still_missing:
            try:
                from src.data.stock_index_loader import get_index_stock_name

                for bare in still_missing:
                    name = str(get_index_stock_name(bare) or "").strip()
                    if name and name != bare:
                        name_by_bare[bare] = name
            except Exception as exc:  # noqa: BLE001
                logger.debug("[QMT订阅] 本地股票索引名称解析失败: %s", exc)

        return {symbol: name_by_bare.get(symbol[:6], "") for symbol in symbols}

    # ------------------------------------------------------------------
    # 同步与写回
    # ------------------------------------------------------------------
    def desired_symbols(self) -> List[str]:
        """按当前开关裁剪后的目标订阅列表。"""
        config = get_config()
        desired: Set[str] = set(self.list_manual_symbols())
        if getattr(config, "qmt_auto_include_holdings", False):
            desired.update(self.list_holdings_symbols())
        if getattr(config, "qmt_include_alert_symbols", False):
            desired.update(self.list_alert_symbols())
        return sorted(desired)

    def sync_subscription(self) -> Dict[str, Any]:
        """desired 与当前列表比对，不同则整组写回（返回 diff）。"""
        if not self._client.redis_lib_available:
            raise QmtQuotesServiceError("redis 库未安装，QMT 行情桥不可用")

        desired = self.desired_symbols()
        current = self._client.get_subscription()
        if set(current) == set(desired):
            return {
                "changed": False,
                "added": [],
                "removed": [],
                "total": len(desired),
            }

        added = sorted(set(desired) - set(current))
        removed = sorted(set(current) - set(desired))
        if not self._client.set_subscription(desired):
            raise QmtQuotesServiceError("写入 qmt:cfg:symbols 失败（Redis 不可用或写回出错）")
        logger.info(
            "[QMT订阅] 订阅列表已同步：+%s / -%s，共 %s 个标的",
            len(added),
            len(removed),
            len(desired),
        )
        return {
            "changed": True,
            "added": added,
            "removed": removed,
            "total": len(desired),
        }

    # ------------------------------------------------------------------
    # 设置持久化（经 SystemConfigService 写 .env，watchlist 同款链路）
    # ------------------------------------------------------------------
    @staticmethod
    def set_manual_symbols(symbols: List[str]) -> Dict[str, Any]:
        """规范化手动订阅列表并写回 ``QMT_MANUAL_SYMBOLS``。

        返回 ``{"symbols": [...], "invalid": [...]}``；存在非法项时抛
        ``QmtQuotesServiceError``，不做部分保存。
        """
        normalized: Set[str] = set()
        invalid: List[str] = []
        for item in symbols or []:
            text = str(item or "").strip()
            if not text:
                continue
            qmt_symbol = _to_qmt_symbol(text)
            if qmt_symbol:
                normalized.add(qmt_symbol)
            else:
                invalid.append(text)
        if invalid:
            raise QmtQuotesServiceError(
                "不支持的标的代码（仅沪深 6 位码）: " + ", ".join(invalid[:10])
            )
        QmtQuotesService._write_env_items(
            {"QMT_MANUAL_SYMBOLS": ",".join(sorted(normalized))}
        )
        return {"symbols": sorted(normalized), "invalid": []}

    @staticmethod
    def update_settings(
        *,
        enabled: Optional[bool] = None,
        auto_include_holdings: Optional[bool] = None,
        include_alert_symbols: Optional[bool] = None,
    ) -> None:
        updates: Dict[str, str] = {}
        if enabled is not None:
            updates["QMT_QUOTES_ENABLED"] = "true" if enabled else "false"
        if auto_include_holdings is not None:
            updates["QMT_AUTO_INCLUDE_HOLDINGS"] = "true" if auto_include_holdings else "false"
        if include_alert_symbols is not None:
            updates["QMT_INCLUDE_ALERT_SYMBOLS"] = "true" if include_alert_symbols else "false"
        if updates:
            QmtQuotesService._write_env_items(updates)

    @staticmethod
    def _write_env_items(updates: Dict[str, str]) -> None:
        from src.services.system_config_service import SystemConfigService

        service = SystemConfigService()
        config_data = service.get_config(include_schema=False)
        service.update(
            config_version=config_data.get("config_version", ""),
            items=[{"key": key, "value": value} for key, value in updates.items()],
            reload_now=True,
        )


def sync_subscription_safely(service: Optional[QmtQuotesService] = None) -> Any:
    """调度任务入口：返回真值心跳 / task_failed 标记，不向调度器抛异常。"""
    from src.services.scheduled_task_registry import task_failed

    config = get_config()
    if not getattr(config, "qmt_quotes_enabled", False):
        return None  # 未启用 → 空跳，不记心跳
    try:
        result = (service or QmtQuotesService()).sync_subscription()
        detail = (
            f"added={len(result['added'])}, removed={len(result['removed'])}, total={result['total']}"
            if result.get("changed")
            else f"no change, total={result.get('total', 0)}"
        )
        # 真值 dict → 心跳记 success；detail 不入库（总览已有时间列）
        return {"status": "ok", "changed": bool(result.get("changed")), "detail": sanitize_diagnostic_text(detail)}
    except Exception as exc:  # noqa: BLE001 - 调度路径吞异常，失败记心跳
        logger.warning("[QMT订阅] 订阅同步失败: %s", exc)
        return task_failed(str(exc))
