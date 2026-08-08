# Market Data API Design

## Overview

Expose market data fetching (indices, breadth stats, sector rankings) and market news as standalone HTTP JSON endpoints, reusing the existing `MarketAnalyzer` and `DataFetcherManager` infrastructure without triggering LLM analysis or notifications.

## Motivation

`run_market_review()` combines data fetching + LLM analysis + notifications in one flow. The data fetching portion (`MarketAnalyzer.get_market_overview()`, `search_market_news()`) is valuable on its own and should be accessible via API for external consumers.

## API Endpoints

### GET `/api/v1/market/overview`

Returns market overview data for a region.

**Query Parameters:**
- `region` (optional, string): `cn` / `us` / `hk`. Default `cn`.
- `all` (optional, boolean): When `true`, returns data for all regions. Default `false`.

**Response 200 (single region):**

```json
{
  "region": "cn",
  "date": "2026-06-19",
  "indices": [
    {
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
      "amplitude": 0.94
    }
  ],
  "stats": {
    "up_count": 2800,
    "down_count": 1500,
    "flat_count": 200,
    "limit_up_count": 45,
    "limit_down_count": 12,
    "total_amount": 8500.50
  },
  "top_sectors": [
    {"name": "半导体", "change_pct": 3.2},
    {"name": "新能源", "change_pct": 2.8}
  ],
  "bottom_sectors": [
    {"name": "房地产", "change_pct": -2.1},
    {"name": "银行", "change_pct": -1.5}
  ]
}
```

**Response 200 (all regions):**

```json
{
  "regions": [
    { "region": "cn", "date": "2026-06-19", "indices": [...], "stats": {...}, "top_sectors": [...], "bottom_sectors": [...] },
    { "region": "us", "date": "2026-06-19", "indices": [...], "stats": {}, "top_sectors": [], "bottom_sectors": [] },
    { "region": "hk", "date": "2026-06-19", "indices": [...], "stats": {}, "top_sectors": [], "bottom_sectors": [] }
  ]
}
```

**Notes:**
- `stats` is empty `{}` for US/HK markets (not supported by those market profiles).
- `top_sectors`/`bottom_sectors` are empty `[]` for US/HK markets.

### GET `/api/v1/market/news`

Returns market news for a region.

**Query Parameters:**
- `region` (optional, string): `cn` / `us` / `hk`. Default `cn`.
- `all` (optional, boolean): When `true`, returns news for all regions. Default `false`.
- `limit` (optional, integer): Max news items per region. Default `10`.

**Note:** When `SearchService` is unavailable, returns empty `news` list (not an error).

**Response 200 (single region):**

```json
{
  "region": "cn",
  "news": [
    {
      "title": "标题",
      "summary": "摘要内容",
      "source": "来源媒体",
      "url": "https://...",
      "published_at": "2026-06-19T10:00:00"
    }
  ]
}
```

**Response 200 (all regions):**

```json
{
  "regions": [
    { "region": "cn", "news": [...] },
    { "region": "us", "news": [...] },
    { "region": "hk", "news": [...] }
  ]
}
```

### Error Responses

**400 Bad Request:**
```json
{
  "error": "invalid_region",
  "message": "region must be cn, us, or hk"
}
```

**503 Service Unavailable:**
```json
{
  "error": "data_source_unavailable",
  "message": "All data providers failed for region cn"
}
```

## Architecture

### Call Chain

```
GET /api/v1/market/overview?region=cn
  → market endpoint
    → MarketDataService.get_market_overview("cn")
      → MarketAnalyzer(region="cn")
        → .get_market_overview()
          → ._get_main_indices()
            → DataFetcherManager.get_main_indices("cn")
              → EfinanceFetcher / AkshareFetcher / ... (fallback)
          → ._get_market_statistics()
            → DataFetcherManager.get_market_stats()
              → EfinanceFetcher / AkshareFetcher / ... (fallback)
          → ._get_sector_rankings()
            → DataFetcherManager.get_sector_rankings(5)
              → EfinanceFetcher / AkshareFetcher / ... (fallback)
```

### Data Flow

1. **Endpoint** validates query params, delegates to service.
2. **Service** instantiates `MarketAnalyzer(region)` per request (stateless), calls `get_market_overview()` or `search_market_news()`.
3. **MarketAnalyzer** uses its `DataFetcherManager` which tries fetchers in priority order with automatic fallback.
4. **Service** converts `MarketOverview` dataclass to dict (including `prev_close` from `MarketIndex`), serializes `SearchResult` objects for news.
5. **Endpoint** returns Pydantic model response.
6. **SearchService:** Optional dependency for `MarketAnalyzer`. When unavailable, `search_market_news()` returns `[]`. The news endpoint returns empty list rather than erroring.

### Key Design Decisions

1. **Per-request instantiation:** `MarketAnalyzer` is created fresh each time. No caching at this layer — callers cache as needed.
2. **Include `prev_close`:** `MarketIndex.to_dict()` omits `prev_close`, but the API includes it since it's useful for consumers.
3. **News serialization:** `search_market_news()` returns `SearchResult` objects. The service extracts `title`, `snippet`→`summary`, `source`, `url`, `published_date`→`published_at` into plain dicts.
4. **SearchService dependency:** `MarketAnalyzer` requires a `SearchService` for news. The news endpoint must have `SearchService` available; the overview endpoint does not need it.

## Files to Create/Modify

| Action | File | Purpose |
|--------|------|---------|
| Create | `src/services/market_data_service.py` | Service layer: wraps `MarketAnalyzer` for data-only access |
| Create | `api/v1/endpoints/market.py` | FastAPI router with overview + news endpoints |
| Create | `api/v1/schemas/market.py` | Pydantic response models |
| Modify | `api/v1/router.py` | Register market router |
| Modify | `api/v1/schemas/__init__.py` | Export new schemas |

## Schema Details (Pydantic)

### `api/v1/schemas/market.py`

```python
class MarketIndexItem(BaseModel):
    code: str
    name: str
    current: float
    change: float
    change_pct: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float
    amount: float
    amplitude: float

class MarketBreadthStats(BaseModel):
    up_count: int = 0
    down_count: int = 0
    flat_count: int = 0
    limit_up_count: int = 0
    limit_down_count: int = 0
    total_amount: float = 0.0

class SectorItem(BaseModel):
    name: str
    change_pct: float

class MarketOverviewResponse(BaseModel):
    region: str
    date: str
    indices: List[MarketIndexItem] = []
    stats: MarketBreadthStats
    top_sectors: List[SectorItem] = []
    bottom_sectors: List[SectorItem] = []

class AllMarketOverviewResponse(BaseModel):
    regions: List[MarketOverviewResponse]

class MarketNewsItem(BaseModel):
    title: str
    summary: str = ""
    source: str = ""
    url: str = ""
    published_at: str = ""

class MarketNewsResponse(BaseModel):
    region: str
    news: List[MarketNewsItem]

class AllMarketNewsResponse(BaseModel):
    regions: List[MarketNewsResponse]
```

## Verification

- `GET /api/v1/market/overview?region=cn` returns valid JSON with indices, stats, sectors.
- `GET /api/v1/market/overview?all=true` returns all regions.
- `GET /api/v1/market/news?region=cn` returns news with source field.
- `GET /api/v1/market/news?region=cn` without `SearchService` returns `{"region": "cn", "news": []}`.
- Invalid region returns 400.
- Data source failure returns 503 (all providers fail) or partial data (single provider fallback).
- `GET /api/v1/market/overview?region=us` returns indices only, no stats/sectors.
