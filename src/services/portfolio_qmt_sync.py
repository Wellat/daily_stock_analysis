# -*- coding: utf-8 -*-
"""QMT 持仓快照 → Portfolio 账本同步（手动触发）。

QMT 每日收盘后上报的是账户持仓快照（qmt_positions 全量替换），而 Portfolio
以交易事件流为 source of truth。同步 = 快照差额生成增量交易事件：
新增/加仓 → buy(差额)，减仓 → sell(差额)，标的消失 → sell(全量)。

diff 基线只聚合 note 为 `qmt_sync:{qmt_account}` 的历史同步交易，用户手工
录入/CSV 导入的交易永不被 diff 触碰。回放时 buy 扣现金/sell 加现金，因此
每个写入的事件配平一笔等额现金流水（note=`qmt_sync_cash:{trade_uid}`），
使现金余额恒 0、总权益=持仓市值。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from data_provider.base import canonical_stock_code
from src.repositories.portfolio_repo import PortfolioRepository
from src.repositories.qmt_position_repo import QmtPositionRepository
from src.services.portfolio_service import (
    PortfolioBusyError,
    PortfolioConflictError,
    PortfolioOversellError,
    PortfolioService,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

SYNC_NOTE_PREFIX = "qmt_sync:"
SYNC_CASH_NOTE_PREFIX = "qmt_sync_cash:"
_QTY_EPS = 1e-6


def find_qmt_portfolio_account(repo: PortfolioRepository, qmt_account: str):
    """按资金账号同名约定查找 Portfolio 账户（不区分 broker），未找到返回 None。"""
    for account in repo.list_accounts():
        if account.name == qmt_account:
            return account
    return None


def create_qmt_portfolio_account(repo: PortfolioRepository, qmt_account: str):
    """以资金账号为名创建 Portfolio 账户（broker=qmt, cn, CNY）。"""
    account = repo.create_account(
        name=qmt_account, broker="qmt", market="cn", base_currency="CNY"
    )
    logger.info("[PortfolioQmtSync] created portfolio account %s for QMT %s", account.id, qmt_account)
    return account


class PortfolioQmtSyncService:
    """把 QMT 快照持仓差额同步为 Portfolio 交易事件。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.positions = QmtPositionRepository(self.db)
        self.repo = PortfolioRepository(self.db)
        self.portfolio_service = PortfolioService(repo=self.repo)

    def sync(
        self,
        *,
        qmt_account: Optional[str] = None,
        account_id: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """同步 QMT 快照到 Portfolio 账本。

        - qmt_account：只同步该 QMT 资金账号（默认全部）
        - account_id：写入指定的 Portfolio 账户（默认按资金账号同名自动匹配/创建）
        - dry_run：只返回将生成的事件，不落库
        """
        today = date.today()
        rows = self.positions.list(account=qmt_account)
        by_account: Dict[str, List[Any]] = defaultdict(list)
        for row in rows:
            by_account[row.account].append(row)
        if qmt_account:
            # 显式指定的账户即使本次无上报数据也要评估清仓
            by_account.setdefault(qmt_account, [])
        else:
            # 兜底已同步过的 QMT 账户（快照完全清空时仍要生成清仓事件）：
            # 从 QMT 建的账户（broker=qmt）与历史同步交易 note 反推账户集合
            for account in self.repo.list_accounts():
                if not account.is_active:
                    continue
                if account.broker == "qmt" and account.name:
                    by_account.setdefault(account.name, [])
                for trade in self.repo.list_trades(account.id, as_of=today):
                    note = trade.note or ""
                    if note.startswith(SYNC_NOTE_PREFIX) and note[len(SYNC_NOTE_PREFIX):]:
                        by_account.setdefault(note[len(SYNC_NOTE_PREFIX):], [])

        accounts: List[Dict[str, Any]] = []
        for qmt_acct in sorted(by_account):
            accounts.append(
                self._sync_one(
                    qmt_account=qmt_acct,
                    position_rows=by_account[qmt_acct],
                    target_account_id=account_id,
                    today=today,
                    dry_run=dry_run,
                )
            )
        return {"dry_run": bool(dry_run), "accounts": accounts}

    def _sync_one(
        self,
        *,
        qmt_account: str,
        position_rows: List[Any],
        target_account_id: Optional[int],
        today: date,
        dry_run: bool,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "qmt_account": qmt_account,
            "account_id": None,
            "account_name": None,
            "account_created": False,
            "position_count": len(position_rows),
            "events": [],
            "inserted_count": 0,
            "duplicate_count": 0,
            "failed_count": 0,
            "cash_entries": 0,
        }

        note = f"{SYNC_NOTE_PREFIX}{qmt_account}"
        account, account_created, resolve_error = self._resolve_account(
            qmt_account=qmt_account,
            target_account_id=target_account_id,
            dry_run=dry_run,
        )
        result["account_created"] = account_created
        if resolve_error:
            result["error"] = resolve_error
            result["failed_count"] = 1
            return result
        if account is not None:
            result["account_id"] = account.id
            result["account_name"] = account.name
        else:
            # dry_run 且账户不存在：按空基线预演，标记同步时将创建
            result["account_name"] = qmt_account

        # 基线：note 精确匹配的历史同步交易，按 symbol 聚合净量与最近同步价
        baseline_qty: Dict[str, float] = defaultdict(float)
        last_price: Dict[str, float] = {}
        today_uid_counts: Dict[str, int] = defaultdict(int)
        uid_date = today.strftime("%Y%m%d")
        if result["account_id"] is not None:
            for trade in self.repo.list_trades(result["account_id"], as_of=today):
                if (trade.note or "") != note:
                    continue
                qty = float(trade.quantity or 0.0)
                baseline_qty[trade.symbol] += qty if trade.side == "buy" else -qty
                if trade.price and float(trade.price) > 0:
                    last_price[trade.symbol] = float(trade.price)
                if (trade.trade_uid or "").startswith(
                    f"qmt:{qmt_account}:{trade.symbol}:{uid_date}:"
                ):
                    today_uid_counts[trade.symbol] += 1

        # 事件计划：快照 vs 基线
        planned: List[Dict[str, Any]] = []
        snapshot_symbols = set()
        for row in position_rows:
            symbol = canonical_stock_code(str(row.symbol or ""))
            if not symbol:
                continue
            snapshot_symbols.add(symbol)
            volume = float(row.volume or 0.0)
            diff = volume - baseline_qty.get(symbol, 0.0)
            if diff > _QTY_EPS:
                planned.append({
                    "side": "buy",
                    "symbol": symbol,
                    "symbol_name": row.name,
                    "quantity": diff,
                    "price": float(row.open_price) if row.open_price else None,
                    "error_hint": "快照缺少成本价 open_price",
                })
            elif diff < -_QTY_EPS:
                price = self._estimate_sell_price(row)
                planned.append({
                    "side": "sell",
                    "symbol": symbol,
                    "symbol_name": row.name,
                    "quantity": -diff,
                    "price": price,
                    "error_hint": "快照缺少可用于估价的成本/浮盈数据",
                })
        for symbol, qty in sorted(baseline_qty.items()):
            if symbol in snapshot_symbols or qty <= _QTY_EPS:
                continue
            # 标的已从快照消失：按最近同步价全量清仓
            planned.append({
                "side": "sell",
                "symbol": symbol,
                "symbol_name": None,
                "quantity": qty,
                "price": last_price.get(symbol),
                "error_hint": "标的信息缺失，无最近同步价可用于估价",
            })

        # 现金配平按事件逐笔记账，note 引用 trade_uid 做幂等
        balanced_notes = set()
        if result["account_id"] is not None and not dry_run:
            for entry in self.repo.list_cash_ledger(result["account_id"], as_of=today):
                if entry.note:
                    balanced_notes.add(entry.note)

        for event in planned:
            seq = today_uid_counts.get(event["symbol"], 0) + 1
            today_uid_counts[event["symbol"]] = seq
            trade_uid = f"qmt:{qmt_account}:{event['symbol']}:{uid_date}:{seq}"
            item = {
                "side": event["side"],
                "symbol": event["symbol"],
                "symbol_name": event["symbol_name"],
                "quantity": round(float(event["quantity"]), 6),
                "price": round(float(event["price"]), 6) if event["price"] else None,
                "trade_uid": trade_uid,
                "status": "inserted",
            }

            if event["price"] is None or float(event["price"]) <= 0:
                item["status"] = "failed"
                item["error"] = event["error_hint"]
                result["failed_count"] += 1
                result["events"].append(item)
                continue

            if dry_run:
                item["status"] = "dry_run"
                result["inserted_count"] += 1
                result["events"].append(item)
                continue

            try:
                self.portfolio_service.record_trade(
                    account_id=result["account_id"],
                    symbol=event["symbol"],
                    trade_date=today,
                    side=event["side"],
                    quantity=float(event["quantity"]),
                    price=float(event["price"]),
                    market="cn",
                    currency="CNY",
                    trade_uid=trade_uid,
                    note=note,
                )
            except PortfolioConflictError:
                item["status"] = "duplicate"
                result["duplicate_count"] += 1
                result["events"].append(item)
                continue
            except (PortfolioOversellError, PortfolioBusyError, ValueError) as exc:
                item["status"] = "failed"
                item["error"] = str(exc)
                result["failed_count"] += 1
                result["events"].append(item)
                continue

            result["inserted_count"] += 1
            # 配平现金：buy 回流等额现金 in，sell 抽走等额现金 out
            amount = float(event["quantity"]) * float(event["price"])
            cash_note = f"{SYNC_CASH_NOTE_PREFIX}{trade_uid}"
            if cash_note not in balanced_notes:
                self.portfolio_service.record_cash_ledger(
                    account_id=result["account_id"],
                    event_date=today,
                    direction="in" if event["side"] == "buy" else "out",
                    amount=round(amount, 6),
                    currency="CNY",
                    note=cash_note,
                )
                balanced_notes.add(cash_note)
                result["cash_entries"] += 1
            result["events"].append(item)

        return result

    def _resolve_account(
        self,
        *,
        qmt_account: str,
        target_account_id: Optional[int],
        dry_run: bool,
    ):
        """返回 (account, created, error)；account 为 None 仅出现在 dry_run 未建账户时。"""
        if target_account_id is not None:
            account = self.repo.get_account(target_account_id)
            if account is None or not account.is_active:
                return None, False, f"portfolio account {target_account_id} not found or inactive"
            return account, False, None

        existing = find_qmt_portfolio_account(self.repo, qmt_account)
        if existing is not None:
            return existing, False, None
        if dry_run:
            return None, True, None
        return create_qmt_portfolio_account(self.repo, qmt_account), True, None

    @staticmethod
    def _estimate_sell_price(row: Any) -> Optional[float]:
        """减仓卖出价近似：成本价 + 每张浮盈 ≈ 现价。"""
        open_price = float(row.open_price) if row.open_price else None
        volume = float(row.volume or 0.0)
        float_profit = float(row.float_profit) if row.float_profit is not None else None
        if open_price and open_price > 0:
            if float_profit is not None and volume > _QTY_EPS:
                return open_price + float_profit / volume
            return open_price
        return None
