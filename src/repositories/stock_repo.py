# -*- coding: utf-8 -*-
"""
===================================
股票数据访问层
===================================

职责：
1. 封装股票数据的数据库操作
2. 提供日线数据查询接口
"""

import logging
from datetime import date
from typing import Optional, List, Dict, Any

import pandas as pd
from sqlalchemy import and_, desc, func, or_, select

from src.storage import DatabaseManager, StockDaily

logger = logging.getLogger(__name__)


class StockRepository:
    """
    股票数据访问层
    
    封装 StockDaily 表的数据库操作
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        初始化数据访问层
        
        Args:
            db_manager: 数据库管理器（可选，默认使用单例）
        """
        self.db = db_manager or DatabaseManager.get_instance()
    
    def get_latest(self, code: str, days: int = 2) -> List[StockDaily]:
        """
        获取最近 N 天的数据
        
        Args:
            code: 股票代码
            days: 获取天数
            
        Returns:
            StockDaily 对象列表（按日期降序）
        """
        try:
            return self.db.get_latest_data(code, days)
        except Exception as e:
            logger.error(f"获取最新数据失败: {e}")
            return []
    
    def get_range(
        self,
        code: str,
        start_date: date,
        end_date: date
    ) -> List[StockDaily]:
        """
        获取指定日期范围的数据
        
        Args:
            code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            StockDaily 对象列表
        """
        try:
            return self.db.get_data_range(code, start_date, end_date)
        except Exception as e:
            logger.error(f"获取日期范围数据失败: {e}")
            return []
    
    def list_codes(
        self,
        *,
        keyword: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List distinct non-CB codes from stock_daily with their latest bar.

        用于行情数据页"股票"列表：按 code 分组取最新日期与收盘价。
        """
        normalized_keyword = str(keyword).strip() if keyword else None
        with self.db.get_session() as session:
            latest = (
                select(StockDaily.code, func.max(StockDaily.date).label("max_date"))
                .where(StockDaily.instrument_type != "convertible_bond")
                .group_by(StockDaily.code)
                .subquery()
            )
            statement = (
                select(
                    StockDaily.code,
                    StockDaily.date,
                    StockDaily.close,
                    StockDaily.instrument_type,
                )
                .join(
                    latest,
                    and_(
                        latest.c.code == StockDaily.code,
                        latest.c.max_date == StockDaily.date,
                    ),
                )
                .order_by(StockDaily.code)
            )
            if normalized_keyword:
                pattern = f"%{normalized_keyword}%"
                statement = statement.where(
                    or_(
                        StockDaily.code.like(pattern),
                        func.lower(StockDaily.code).like(pattern.lower()),
                    )
                )
            total = session.execute(
                select(func.count()).select_from(latest)
            ).scalar_one()
            filtered_total = len(
                session.execute(
                    select(latest.c.code).where(
                        or_(
                            latest.c.code.like(f"%{normalized_keyword}%"),
                            func.lower(latest.c.code).like(f"%{normalized_keyword.lower()}%"),
                        )
                    )
                ).scalars().all()
            ) if normalized_keyword else int(total)
            rows = session.execute(
                statement.offset(offset).limit(limit)
            ).all()
            return {
                "total": filtered_total,
                "items": [
                    {
                        "code": row.code,
                        "instrument_type": row.instrument_type or "stock",
                        "latest_date": row.date.isoformat() if row.date else None,
                        "latest_close": row.close,
                    }
                    for row in rows
                ],
            }

    def get_bars(
        self,
        *,
        code: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """Return daily bars for one code from stock_daily, ascending by date."""
        with self.db.get_session() as session:
            statement = select(StockDaily).where(StockDaily.code == code)
            if start_date is not None:
                statement = statement.where(StockDaily.date >= start_date)
            if end_date is not None:
                statement = statement.where(StockDaily.date <= end_date)
            rows = session.execute(
                statement.order_by(StockDaily.date.asc()).limit(limit)
            ).scalars().all()
            return {
                "code": code,
                "total": len(rows),
                "items": [
                    {
                        "date": row.date.isoformat() if row.date else None,
                        "open": row.open,
                        "high": row.high,
                        "low": row.low,
                        "close": row.close,
                        "volume": row.volume,
                        "amount": row.amount,
                        "data_source": row.data_source,
                    }
                    for row in rows
                ],
            }
    
    def save_dataframe(
        self,
        df: pd.DataFrame,
        code: str,
        data_source: str = "Unknown"
    ) -> int:
        """
        保存 DataFrame 到数据库
        
        Args:
            df: 包含日线数据的 DataFrame
            code: 股票代码
            data_source: 数据来源
            
        Returns:
            保存的记录数
        """
        try:
            return self.db.save_daily_data(df, code, data_source)
        except Exception as e:
            logger.error(f"保存日线数据失败: {e}")
            return 0
    
    def has_today_data(self, code: str, target_date: Optional[date] = None) -> bool:
        """
        检查是否有指定日期的数据
        
        Args:
            code: 股票代码
            target_date: 目标日期（默认今天）
            
        Returns:
            是否存在数据
        """
        try:
            return self.db.has_today_data(code, target_date)
        except Exception as e:
            logger.error(f"检查数据存在失败: {e}")
            return False
    
    def get_analysis_context(
        self, 
        code: str, 
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取分析上下文
        
        Args:
            code: 股票代码
            target_date: 目标日期
            
        Returns:
            分析上下文字典
        """
        try:
            return self.db.get_analysis_context(code, target_date)
        except Exception as e:
            logger.error(f"获取分析上下文失败: {e}")
            return None

    def get_start_daily(self, *, code: str, analysis_date: date) -> Optional[StockDaily]:
        """Return StockDaily for analysis_date (preferred) or nearest previous date."""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date <= analysis_date))
                .order_by(desc(StockDaily.date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def get_daily_on_date(self, *, code: str, target_date: date) -> Optional[StockDaily]:
        """Return StockDaily for the exact target_date without trading-day fallback."""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date == target_date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def get_forward_bars(self, *, code: str, analysis_date: date, eval_window_days: int) -> List[StockDaily]:
        """Return forward daily bars after analysis_date, up to eval_window_days."""
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date > analysis_date))
                .order_by(StockDaily.date)
                .limit(eval_window_days)
            ).scalars().all()
            return list(rows)
