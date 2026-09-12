# -*- coding: utf-8 -*-
"""QMT 当日成交上报 → Portfolio 账本自动入账。

QMT 每日收盘后上报账户当日全部成交记录；本服务不落原始成交表，校验后
打印日志并逐笔映射为 Portfolio 交易事件（source of truth），持仓、成本、
浮动/已实现盈亏由既有重放机制自动更新。幂等键为
trade_uid=`qmt_deal:{资金账号}:{成交编号}`，重发同一批成交安全。

现金口径与「QMT 持仓快照同步」一致：每笔入账交易配平一笔等额反向现金
流水，使现金余额恒 0、总权益=持仓市值。

注意：同一 QMT 资金账号只能在「快照差额同步」与本成交上报两种入账来源中
选其一，混用会双计持仓（快照同步的 diff 基线只认 `qmt_sync:` 前缀交易）。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_qmt_sync import (
    create_qmt_portfolio_account,
    find_qmt_portfolio_account,
)
from src.services.portfolio_service import (
    PortfolioBusyError,
    PortfolioConflictError,
    PortfolioOversellError,
    PortfolioService,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

DEAL_NOTE_PREFIX = "qmt_deal:"
DEAL_CASH_NOTE_PREFIX = "qmt_deal_cash:"

_VALID_SIDES = {"buy", "sell", "unknown"}

# QMT m_strTradeDate + m_strTradeTime 拼接格式因券商而异，逐个尝试
_TRADE_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y%m%d %H:%M:%S",
    "%Y%m%d %H:%M",
    "%Y%m%d%H%M%S",
    "%Y%m%d%H%M",
    "%Y-%m-%d",
    "%Y%m%d",
)

_END_OF_DAY = datetime.max.time()


def parse_trade_time(raw: str) -> Tuple[date, datetime, bool]:
    """解析成交时间，返回 (入账日期, 批内排序键, 是否解析成功)。

    解析失败时回退上报当日、排序键取当日末尾，保证乱序批次仍按原始顺序入账。
    """
    text = (raw or "").strip()
    if text:
        for fmt in _TRADE_TIME_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.date(), parsed, True
            except ValueError:
                continue
        # 兜底：前 8 位为纯数字日期（如带毫秒等更长的变体）
        head = text[:8]
        if len(head) == 8 and head.isdigit():
            try:
                parsed = datetime.strptime(head, "%Y%m%d")
                return parsed.date(), parsed, True
            except ValueError:
                pass
    fallback = date.today()
    return fallback, datetime.combine(fallback, _END_OF_DAY), False


class QmtDealService:
    """接收 QMT 当日成交并自动写入 Portfolio 账本。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.repo = PortfolioRepository(self.db)
        self.portfolio_service = PortfolioService(repo=self.repo)

    def report_deals(self, *, account: str, deals: List[Dict[str, Any]]) -> Dict[str, Any]:
        """校验、留档日志并逐笔入账；单笔失败不阻断其余笔。"""
        account_norm = (account or "").strip()
        if not account_norm:
            raise ValueError("account must not be empty")
        if not isinstance(deals, list):
            raise ValueError("deals must be a list")

        parsed_deals = [
            self._parse_deal(index, item, account_norm) for index, item in enumerate(deals)
        ]
        # 无原始表，日志即留档：逐笔记录上报内容
        logger.info("[QmtDeal] account=%s received %d deals", account_norm, len(parsed_deals))
        for deal in parsed_deals:
            logger.info(
                "[QmtDeal] account=%s trade_id=%s symbol=%s name=%s side=%s price=%s "
                "volume=%s amount=%s fee=%s order_sys_id=%s trade_time=%s xt_trade=%s",
                account_norm, deal["trade_id"], deal["symbol"], deal["name"], deal["side"],
                deal["price"], deal["volume"], deal["amount"], deal["fee"],
                deal["order_sys_id"], deal["trade_time"], deal["xt_trade"],
            )

        # 全部为 unknown 等不可入账成交时不创建空账户，首次实际入账时再建
        portfolio_account = find_qmt_portfolio_account(self.repo, account_norm)
        account_created = False
        if portfolio_account is None and any(d["side"] != "unknown" for d in parsed_deals):
            portfolio_account = create_qmt_portfolio_account(self.repo, account_norm)
            account_created = True

        result: Dict[str, Any] = {
            "account": account_norm,
            "account_id": int(portfolio_account.id) if portfolio_account is not None else None,
            "account_name": portfolio_account.name if portfolio_account is not None else None,
            "account_created": account_created,
            "received": len(parsed_deals),
            "inserted_count": 0,
            "duplicate_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "cash_entries": 0,
            "items": [],
        }

        note = f"{DEAL_NOTE_PREFIX}{account_norm}"
        for deal in sorted(parsed_deals, key=lambda item: item["sort_key"]):
            item = {
                "trade_id": deal["trade_id"],
                "symbol": deal["symbol"],
                "side": deal["side"],
                "status": "inserted",
                "trade_date": deal["trade_date"].isoformat(),
                "trade_time_parsed": deal["parsed"],
            }

            if deal["side"] == "unknown":
                item["status"] = "skipped"
                item["error"] = "unrecognized side, skipped"
                result["skipped_count"] += 1
                logger.warning("[QmtDeal] account=%s trade_id=%s side unknown, skipped",
                               account_norm, deal["trade_id"])
                result["items"].append(item)
                continue

            trade_uid = f"qmt_deal:{account_norm}:{deal['trade_id']}"
            try:
                self.portfolio_service.record_trade(
                    account_id=result["account_id"],
                    symbol=deal["symbol"],
                    trade_date=deal["trade_date"],
                    side=deal["side"],
                    quantity=deal["volume"],
                    price=deal["price"],
                    fee=deal["fee"],
                    market="cn",
                    currency="CNY",
                    trade_uid=trade_uid,
                    note=note,
                )
            except PortfolioConflictError:
                item["status"] = "duplicate"
                result["duplicate_count"] += 1
                result["items"].append(item)
                continue
            except (PortfolioOversellError, PortfolioBusyError, ValueError) as exc:
                item["status"] = "failed"
                item["error"] = str(exc)
                result["failed_count"] += 1
                result["items"].append(item)
                continue

            result["inserted_count"] += 1
            result["items"].append(item)
            if self._record_balancing_cash(
                deal, account_id=result["account_id"], account=account_norm
            ):
                result["cash_entries"] += 1

        return result

    def _record_balancing_cash(self, deal: Dict[str, Any], *, account_id: int, account: str) -> bool:
        """为刚入账的成交配平等额反向现金流水，使现金余额恒 0。

        仅在交易本次新插入后调用，duplicate 重发不会重复配平。
        """
        gross = float(deal["volume"]) * float(deal["price"])
        amount = gross + float(deal["fee"]) if deal["side"] == "buy" else gross - float(deal["fee"])
        if amount <= 0:
            return False
        try:
            self.portfolio_service.record_cash_ledger(
                account_id=account_id,
                event_date=deal["trade_date"],
                direction="in" if deal["side"] == "buy" else "out",
                amount=round(amount, 6),
                currency="CNY",
                note=f"{DEAL_CASH_NOTE_PREFIX}qmt_deal:{account}:{deal['trade_id']}",
            )
        except Exception:
            # 交易已入账，配平失败只记日志：现金口径短暂失衡，不阻断批内其余笔
            logger.exception(
                "[QmtDeal] account=%s trade_id=%s balancing cash entry failed",
                account, deal["trade_id"],
            )
            return False
        return True

    def _parse_deal(self, index: int, item: Dict[str, Any], account: str) -> Dict[str, Any]:
        """严格校验单笔成交并完成字段规整；不合法抛 ValueError（整批 400）。

        account 为请求体顶层资金账号，笔内 account 字段仅透传用于日志。
        """
        if not isinstance(item, dict):
            raise ValueError(f"deals[{index}] must be an object")

        symbol = str(item.get("symbol") or "").strip()
        if not symbol.isdigit() or len(symbol) != 6:
            raise ValueError(f"deals[{index}].symbol must be a 6-digit code")

        side = str(item.get("side") or "").strip().lower()
        if side not in _VALID_SIDES:
            raise ValueError(f"deals[{index}].side must be buy, sell or unknown")

        price = self._require_positive_number(item.get("price"), f"deals[{index}].price")
        volume = self._require_positive_number(item.get("volume"), f"deals[{index}].volume")
        fee = item.get("fee")
        if fee is None:
            fee = 0.0
        if not isinstance(fee, (int, float)) or isinstance(fee, bool) or fee < 0:
            raise ValueError(f"deals[{index}].fee must be a number >= 0")

        trade_id = str(item.get("trade_id") or "").strip()
        if not trade_id:
            raise ValueError(f"deals[{index}].trade_id must not be empty")
        if len(f"qmt_deal:{account}:{trade_id}") > 128:
            raise ValueError(f"deals[{index}]: account + trade_id too long for trade_uid")

        trade_date, sort_key, parsed = parse_trade_time(str(item.get("trade_time") or ""))

        return {
            "account": account,
            "symbol": symbol,
            "name": str(item.get("name") or ""),
            "side": side,
            "price": float(price),
            "volume": float(volume),
            "amount": item.get("amount"),
            "fee": float(fee),
            "trade_id": trade_id,
            "order_sys_id": str(item.get("order_sys_id") or ""),
            "trade_time": str(item.get("trade_time") or ""),
            "xt_trade": str(item.get("xt_trade") or ""),
            "trade_date": trade_date,
            "sort_key": sort_key,
            "parsed": parsed,
        }

    @staticmethod
    def _require_positive_number(value: Any, field: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{field} must be a number > 0")
        return float(value)
