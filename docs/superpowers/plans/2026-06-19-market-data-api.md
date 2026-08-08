# Market Data API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two HTTP endpoints (GET `/api/v1/market/overview` and GET `/api/v1/market/news`) that return market data as JSON, reusing `MarketAnalyzer` without triggering LLM analysis.

**Architecture:** A thin service layer (`MarketDataService`) instantiates `MarketAnalyzer` per request, calls `get_market_overview()` or `search_market_news()`, and converts internal dataclasses/SearchResult objects to plain dicts. A FastAPI router exposes these as JSON endpoints with Pydantic response schemas.

**Tech Stack:** Python, FastAPI, Pydantic, existing `MarketAnalyzer` + `DataFetcherManager`

## Global Constraints

- Follow existing directory structure: endpoints in `api/v1/endpoints/`, schemas in `api/v1/schemas/`, services in `src/services/`
- Use `api_error()` from `api/v1/errors.py` for error responses
- Pydantic models use `Field(description=...)` with `ConfigDict(json_schema_extra={"example": ...})` for OpenAPI docs
- Sync endpoint functions (`def`), not async — FastAPI runs them in thread pool
- No caching at this layer — callers cache as needed
- No `git commit` without explicit user confirmation

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `api/v1/schemas/market.py` | Pydantic response models for market overview and news |
| Create | `src/services/market_data_service.py` | Service layer: wraps `MarketAnalyzer` for data-only access |
| Create | `api/v1/endpoints/market.py` | FastAPI router with overview + news endpoints |
| Modify | `api/v1/router.py:28` | Import and register market router |
| Modify | `api/v1/schemas/__init__.py:117` | Export new schema classes |

---

### Task 1: Pydantic Response Schemas

**Files:**
- Create: `api/v1/schemas/market.py`

**Interfaces:**
- Produces: `MarketIndexItem`, `MarketBreadthStats`, `SectorItem`, `MarketOverviewResponse`, `AllMarketOverviewResponse`, `MarketNewsItem`, `MarketNewsResponse`, `AllMarketNewsResponse` — all Pydantic BaseModel classes used by the endpoint in Task 3.

- [ ] **Step 1: Create schema file**

```python
# -*- coding: utf-8 -*-
"""
===================================
市场数据 API Schemas
===================================

职责：
1. 定义市场行情和新闻接口的 Pydantic 响应模型
"""

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class MarketIndexItem(BaseModel):
    """单个市场指数数据"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "code": "sh000001",
            "name": "上证指数",
            "current": 3200.50,
            "change": 15.30,
            "change_pct": 0.48,
            "open": 3185.00,
            "high": 3210.00,
            "low": 3180.00,
            "prev_close": 3185.20,
            "volume": 2500000000,
            "amount": 350000000000,
            "amplitude": 0.94,
        }
    })

    code: str = Field(description="指数代码")
    name: str = Field(description="指数名称")
    current: float = Field(description="当前点位")
    change: float = Field(description="涨跌点数")
    change_pct: float = Field(description="涨跌幅(%)")
    open: float = Field(description="开盘点位")
    high: float = Field(description="最高点位")
    low: float = Field(description="最低点位")
    prev_close: float = Field(description="昨收点位")
    volume: float = Field(description="成交量")
    amount: float = Field(description="成交额")
    amplitude: float = Field(description="振幅(%)")


class MarketBreadthStats(BaseModel):
    """市场涨跌统计"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "up_count": 2800,
            "down_count": 1500,
            "flat_count": 200,
            "limit_up_count": 45,
            "limit_down_count": 12,
            "total_amount": 8500.50,
        }
    })

    up_count: int = Field(0, description="上涨家数")
    down_count: int = Field(0, description="下跌家数")
    flat_count: int = Field(0, description="平盘家数")
    limit_up_count: int = Field(0, description="涨停家数")
    limit_down_count: int = Field(0, description="跌停家数")
    total_amount: float = Field(0.0, description="两市成交额（亿元）")


class SectorItem(BaseModel):
    """板块涨跌数据"""

    model_config = ConfigDict(json_schema_extra={
        "example": {"name": "半导体", "change_pct": 3.2}
    })

    name: str = Field(description="板块名称")
    change_pct: float = Field(description="涨跌幅(%)")


class MarketOverviewResponse(BaseModel):
    """单个区域市场行情响应"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "region": "cn",
            "date": "2026-06-19",
            "indices": [{"code": "sh000001", "name": "上证指数", "current": 3200.50,
                         "change": 15.30, "change_pct": 0.48, "open": 3185.00,
                         "high": 3210.00, "low": 3180.00, "prev_close": 3185.20,
                         "volume": 2500000000, "amount": 350000000000, "amplitude": 0.94}],
            "stats": {"up_count": 2800, "down_count": 1500, "flat_count": 200,
                      "limit_up_count": 45, "limit_down_count": 12, "total_amount": 8500.50},
            "top_sectors": [{"name": "半导体", "change_pct": 3.2}],
            "bottom_sectors": [{"name": "房地产", "change_pct": -2.1}],
        }
    })

    region: str = Field(description="市场区域 cn/us/hk")
    date: str = Field(description="数据日期")
    indices: List[MarketIndexItem] = Field(default_factory=list, description="主要指数列表")
    stats: MarketBreadthStats = Field(description="涨跌统计")
    top_sectors: List[SectorItem] = Field(default_factory=list, description="涨幅前N板块")
    bottom_sectors: List[SectorItem] = Field(default_factory=list, description="跌幅前N板块")


class AllMarketOverviewResponse(BaseModel):
    """所有区域市场行情响应"""

    regions: List[MarketOverviewResponse] = Field(description="各区域行情列表")


class MarketNewsItem(BaseModel):
    """单条市场新闻"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "title": "标题",
            "summary": "摘要内容",
            "source": "来源媒体",
            "url": "https://...",
            "published_at": "2026-06-19T10:00:00",
        }
    })

    title: str = Field(description="新闻标题")
    summary: str = Field("", description="新闻摘要")
    source: str = Field("", description="来源媒体")
    url: str = Field("", description="新闻链接")
    published_at: str = Field("", description="发布时间")


class MarketNewsResponse(BaseModel):
    """单个区域市场新闻响应"""

    region: str = Field(description="市场区域 cn/us/hk")
    news: List[MarketNewsItem] = Field(default_factory=list, description="新闻列表")


class AllMarketNewsResponse(BaseModel):
    """所有区域市场新闻响应"""

    regions: List[MarketNewsResponse] = Field(description="各区域新闻列表")
```

- [ ] **Step 2: Verify syntax**

Run: `python -m py_compile api/v1/schemas/market.py`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add api/v1/schemas/market.py
git commit -m "feat: add Pydantic response schemas for market data API"
```

---

### Task 2: Market Data Service

**Files:**
- Create: `src/services/market_data_service.py`

**Interfaces:**
- Consumes: `MarketAnalyzer` from `src/market_analyzer.py` (constructor: `MarketAnalyzer(search_service=None, region="cn")`)
- Consumes: `SearchResult` from `src/search_service.py` (attributes: `title`, `snippet`, `source`, `published_date`, `url`)
- Produces:
  - `get_market_overview(region: str) -> dict` — keys: `region`, `date`, `indices` (list of dicts with `code/name/current/change/change_pct/open/high/low/prev_close/volume/amount/amplitude`), `stats` (dict with `up_count/down_count/flat_count/limit_up_count/limit_down_count/total_amount`), `top_sectors` (list of `{name, change_pct}`), `bottom_sectors` (list of `{name, change_pct}`)
  - `get_market_news(region: str, limit: int = 10) -> list` — list of dicts with `title/summary/source/url/published_at`
  - `get_all_regions_overview() -> list` — list of overview dicts for cn/hk/us
  - `get_all_regions_news(limit: int = 10) -> list` — list of `{region, news}` dicts for cn/hk/us

- [ ] **Step 1: Create service file**

```python
# -*- coding: utf-8 -*-
"""
===================================
市场数据服务
===================================

职责：
1. 封装 MarketAnalyzer，提供纯数据获取能力
2. 不触发 LLM 分析和通知
"""

import logging
from typing import Any, Dict, List

from src.market_analyzer import MarketAnalyzer, MarketIndex, MarketOverview

logger = logging.getLogger(__name__)

_VALID_REGIONS = ("cn", "us", "hk")


def _market_index_to_dict(idx: MarketIndex) -> Dict[str, Any]:
    """将 MarketIndex dataclass 转为 dict，包含 prev_close。"""
    return {
        "code": idx.code,
        "name": idx.name,
        "current": idx.current,
        "change": idx.change,
        "change_pct": idx.change_pct,
        "open": idx.open,
        "high": idx.high,
        "low": idx.low,
        "prev_close": idx.prev_close,
        "volume": idx.volume,
        "amount": idx.amount,
        "amplitude": idx.amplitude,
    }


def _overview_to_dict(region: str, overview: MarketOverview) -> Dict[str, Any]:
    """将 MarketOverview dataclass 转为 API 响应 dict。"""
    return {
        "region": region,
        "date": overview.date,
        "indices": [_market_index_to_dict(idx) for idx in overview.indices],
        "stats": {
            "up_count": overview.up_count,
            "down_count": overview.down_count,
            "flat_count": overview.flat_count,
            "limit_up_count": overview.limit_up_count,
            "limit_down_count": overview.limit_down_count,
            "total_amount": overview.total_amount,
        },
        "top_sectors": overview.top_sectors,
        "bottom_sectors": overview.bottom_sectors,
    }


def _serialize_news_item(item: Any) -> Dict[str, str]:
    """将 SearchResult 对象或 dict 序列化为 API 响应 dict。"""
    if hasattr(item, "title"):
        return {
            "title": getattr(item, "title", "") or "",
            "summary": getattr(item, "snippet", "") or "",
            "source": getattr(item, "source", "") or "",
            "url": getattr(item, "url", "") or "",
            "published_at": getattr(item, "published_date", "") or "",
        }
    if isinstance(item, dict):
        return {
            "title": item.get("title", ""),
            "summary": item.get("snippet", item.get("summary", "")),
            "source": item.get("source", ""),
            "url": item.get("url", ""),
            "published_at": item.get("published_date", item.get("published_at", "")),
        }
    return {"title": "", "summary": "", "source": "", "url": "", "published_at": ""}


def get_market_overview(region: str) -> Dict[str, Any]:
    """
    获取单个区域的市场行情数据。

    Args:
        region: 市场区域 cn/us/hk

    Returns:
        包含 indices、stats、sectors 的 dict

    Raises:
        ValueError: region 无效
    """
    if region not in _VALID_REGIONS:
        raise ValueError(f"region must be one of {_VALID_REGIONS}, got '{region}'")

    analyzer = MarketAnalyzer(region=region)
    overview = analyzer.get_market_overview()
    return _overview_to_dict(region, overview)


def get_market_news(region: str, limit: int = 10) -> Dict[str, Any]:
    """
    获取单个区域的市场新闻。

    Args:
        region: 市场区域 cn/us/hk
        limit: 返回条数上限

    Returns:
        {"region": region, "news": [...]}

    Raises:
        ValueError: region 无效
    """
    if region not in _VALID_REGIONS:
        raise ValueError(f"region must be one of {_VALID_REGIONS}, got '{region}'")

    analyzer = MarketAnalyzer(region=region)
    raw_news = analyzer.search_market_news()
    serialized = [_serialize_news_item(item) for item in raw_news[:limit]]
    return {"region": region, "news": serialized}


def get_all_regions_overview() -> List[Dict[str, Any]]:
    """获取所有区域（cn/hk/us）的市场行情数据。"""
    results = []
    for region in _VALID_REGIONS:
        try:
            results.append(get_market_overview(region))
        except Exception:
            logger.exception(
                "market_data_service action=get_overview region=%s status=error", region
            )
            results.append({
                "region": region,
                "date": "",
                "indices": [],
                "stats": {
                    "up_count": 0, "down_count": 0, "flat_count": 0,
                    "limit_up_count": 0, "limit_down_count": 0, "total_amount": 0.0,
                },
                "top_sectors": [],
                "bottom_sectors": [],
            })
    return results


def get_all_regions_news(limit: int = 10) -> List[Dict[str, Any]]:
    """获取所有区域（cn/hk/us）的市场新闻。"""
    results = []
    for region in _VALID_REGIONS:
        try:
            results.append(get_market_news(region, limit=limit))
        except Exception:
            logger.exception(
                "market_data_service action=get_news region=%s status=error", region
            )
            results.append({"region": region, "news": []})
    return results
```

- [ ] **Step 2: Verify syntax**

Run: `python -m py_compile src/services/market_data_service.py`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add src/services/market_data_service.py
git commit -m "feat: add MarketDataService for data-only market access"
```

---

### Task 3: API Endpoints

**Files:**
- Create: `api/v1/endpoints/market.py`

**Interfaces:**
- Consumes: `get_market_overview`, `get_market_news`, `get_all_regions_overview`, `get_all_regions_news` from `src/services/market_data_service.py` (Task 2)
- Consumes: All schema classes from `api/v1/schemas/market.py` (Task 1)
- Consumes: `api_error` from `api/v1/errors.py`
- Produces: `router` — FastAPI APIRouter to be registered in `api/v1/router.py`

- [ ] **Step 1: Create endpoint file**

```python
# -*- coding: utf-8 -*-
"""
===================================
市场数据接口
===================================

职责：
1. GET /api/v1/market/overview 市场行情数据
2. GET /api/v1/market/news 市场新闻数据
"""

import logging

from fastapi import APIRouter, Query

from api.v1.errors import api_error
from api.v1.schemas.market import (
    AllMarketNewsResponse,
    AllMarketOverviewResponse,
    MarketNewsResponse,
    MarketOverviewResponse,
)
from src.services import market_data_service

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_REGIONS = ("cn", "us", "hk")


def _validate_region(region: str) -> str:
    """验证并标准化 region 参数。"""
    region = region.lower().strip()
    if region not in _VALID_REGIONS:
        raise api_error(
            status_code=400,
            error="invalid_region",
            message=f"region must be one of {', '.join(_VALID_REGIONS)}",
        )
    return region


@router.get(
    "/overview",
    response_model=MarketOverviewResponse | AllMarketOverviewResponse,
    summary="获取市场行情数据",
    description="返回指定区域或所有区域的指数行情、涨跌统计和板块排行",
    responses={
        400: {"description": "无效的 region 参数"},
        503: {"description": "数据源不可用"},
    },
)
def get_market_overview(
    region: str = Query("cn", description="市场区域 cn/us/hk"),
    all: bool = Query(False, description="是否返回所有区域数据"),
):
    if all:
        regions_data = market_data_service.get_all_regions_overview()
        return AllMarketOverviewResponse(regions=[
            MarketOverviewResponse(**r) for r in regions_data
        ])

    region = _validate_region(region)
    try:
        data = market_data_service.get_market_overview(region)
    except Exception:
        logger.exception("market endpoint action=get_overview region=%s status=error", region)
        raise api_error(
            status_code=503,
            error="data_source_unavailable",
            message=f"Failed to fetch market data for region {region}",
        )
    return MarketOverviewResponse(**data)


@router.get(
    "/news",
    response_model=MarketNewsResponse | AllMarketNewsResponse,
    summary="获取市场新闻",
    description="返回指定区域或所有区域的市场新闻，含来源信息",
    responses={
        400: {"description": "无效的 region 参数"},
    },
)
def get_market_news(
    region: str = Query("cn", description="市场区域 cn/us/hk"),
    all: bool = Query(False, description="是否返回所有区域数据"),
    limit: int = Query(10, ge=1, le=50, description="每区域返回条数上限"),
):
    if all:
        regions_data = market_data_service.get_all_regions_news(limit=limit)
        return AllMarketNewsResponse(regions=[
            MarketNewsResponse(**r) for r in regions_data
        ])

    region = _validate_region(region)
    data = market_data_service.get_market_news(region, limit=limit)
    return MarketNewsResponse(**data)
```

- [ ] **Step 2: Verify syntax**

Run: `python -m py_compile api/v1/endpoints/market.py`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add api/v1/endpoints/market.py
git commit -m "feat: add market overview and news API endpoints"
```

---

### Task 4: Router Registration & Schema Export

**Files:**
- Modify: `api/v1/router.py` — add `market` import and `include_router`
- Modify: `api/v1/schemas/__init__.py` — add market schema imports and `__all__` entries

**Interfaces:**
- Consumes: `router` from `api/v1/endpoints/market.py` (Task 3)
- Consumes: All schema classes from `api/v1/schemas/market.py` (Task 1)

- [ ] **Step 1: Register market router in `api/v1/router.py`**

Add `market` to the import block (after line 27, before the closing parenthesis):

```python
from api.v1.endpoints import (
    agent,
    alerts,
    alphasift,
    analysis,
    auth,
    backtest,
    decision_signals,
    health,
    history,
    market,
    portfolio,
    stocks,
    system_config,
    usage,
)
```

Add router registration (after the `alphasift` block, before `health`):

```python
router.include_router(
    market.router,
    prefix="/market",
    tags=["Market"]
)
```

- [ ] **Step 2: Export schemas in `api/v1/schemas/__init__.py`**

Add import (after the `decision_signals` import block):

```python
from api.v1.schemas.market import (
    MarketIndexItem,
    MarketBreadthStats,
    SectorItem,
    MarketOverviewResponse,
    AllMarketOverviewResponse,
    MarketNewsItem,
    MarketNewsResponse,
    AllMarketNewsResponse,
)
```

Add to `__all__` list (after the decision signals block):

```python
    # market
    "MarketIndexItem",
    "MarketBreadthStats",
    "SectorItem",
    "MarketOverviewResponse",
    "AllMarketOverviewResponse",
    "MarketNewsItem",
    "MarketNewsResponse",
    "AllMarketNewsResponse",
```

- [ ] **Step 3: Verify syntax**

Run: `python -m py_compile api/v1/router.py api/v1/schemas/__init__.py`
Expected: No output (success)

- [ ] **Step 4: Verify app loads**

Run: `python -c "from api.app import create_app; app = create_app(); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Verify endpoints appear in OpenAPI**

Run: `python -c "
from api.app import create_app
app = create_app()
routes = [r.path for r in app.routes if hasattr(r, 'path')]
assert '/api/v1/market/overview' in routes, 'overview missing'
assert '/api/v1/market/news' in routes, 'news missing'
print('Endpoints registered OK')
"`
Expected: `Endpoints registered OK`

- [ ] **Step 6: Commit**

```bash
git add api/v1/router.py api/v1/schemas/__init__.py
git commit -m "feat: register market endpoints in API router"
```

---

### Task 5: Integration Verification

**Files:** No new files. Verification only.

- [ ] **Step 1: Run full backend gate**

Run: `./scripts/ci_gate.sh`
Expected: All checks pass

- [ ] **Step 2: Manual smoke test (optional, requires network)**

Run: `python -c "
from src.services.market_data_service import get_market_overview
result = get_market_overview('cn')
print(f'Region: {result[\"region\"]}')
print(f'Indices: {len(result[\"indices\"])}')
print(f'Stats keys: {list(result[\"stats\"].keys())}')
print('Smoke test OK')
"`
Expected: Prints region, index count, stats keys, and "Smoke test OK"

- [ ] **Step 3: Verify no regressions in existing endpoints**

Run: `python -m pytest -m "not network" --tb=short -q`
Expected: All tests pass (or pre-existing failures only)
