# 可转债数据同步

本文档说明本项目可转债数据同步的三个能力、入口用法，以及 opencli / 行情接口源字段到数据库表字段的完整映射。

## 能力总览

| 能力 | 数据源 | 落库位置 | 说明 |
|---|---|---|---|
| 基础数据同步 | 本机 opencli（`cb-list` + `cb-detail`） | `strategy_lab_cb_basic` / `strategy_lab_cb_terms` / `strategy_lab_cb_events` | 默认先拉列表，再对每只转债逐个填充详情；传入 `symbols` 时直接按目标代码拉详情，跳过列表抓取 |
| 行情同步（OHLC） | 东财优先，腾讯兜底 | `stock_daily`（`instrument_type='convertible_bond'`）+ `strategy_lab_cb_daily_factors.close` 回填 | 支持选择起始日期，非每次全量 |
| 补数同步（溢价率/剩余规模） | 本机 opencli（`cb-premium-history`） | `stock_daily`（仅补 `premium_rate` / `remaining_size` 空值） | 按“可转债代码 + 日期”匹配已有日线记录，不新建行、不覆盖已有非空值 |

两个能力统一支持：

- `include_delisted`：是否同步已退市可转债（默认仅活跃）。
- 起止日期：仅行情同步有效（基础数据是当前状态快照，无历史日期概念）。

## 用法

### CLI（推荐）

```bash
python scripts/sync_cb_data.py --basic                     # 基础数据（活跃）
python scripts/sync_cb_data.py --basic --include-delisted  # 基础数据（含已退市）
python scripts/sync_cb_data.py --ohlc                      # 行情，增量（本地最后日期起）
python scripts/sync_cb_data.py --ohlc --start-date 2026-01-01 --end-date 2026-12-31
python scripts/sync_cb_data.py --all --include-delisted    # 基础 + 行情
python scripts/sync_cb_data.py --basic --dry-run           # 只打印映射，不落库
python scripts/sync_cb_data.py --basic --bond 113709       # 单只
```

常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--basic` / `--ohlc` / `--all` | 默认 `--basic` | 同步模式（互斥） |
| `--include-delisted` | 关闭 | 是否包含已退市可转债 |
| `--start-date` / `--end-date` | 增量 / 今天 | 行情同步起止日期 `YYYY-MM-DD` |
| `--bond` / `--symbols` | 空 | 单只 / 逗号分隔代码筛选 |
| `--workers` | 3 | opencli 详情并发数 |
| `--limit` | 0 | 只处理前 N 只（调试） |
| `--dry-run` | 关闭 | 只打印 cb-list 与 cb-detail 映射样例，不落库 |

### API

`POST /api/v1/strategy-lab/data-sync`，`source="opencli"` 且 `sync_type` 非空时走新链路：

```json
{
  "source": "opencli",
  "sync_type": "cb_basic",          // cb_basic / cb_ohlc / cb_premium_history / all
  "include_delisted": false,
  "start_date": "2026-01-01",       // 行情同步有效
  "end_date": "2026-12-31",
  "symbols": ["113709"]
}
```

`symbols` 在 `cb_basic` 场景下会直接触发单只/少量标的详情拉取，不再先执行 `cb-list`。
`cb_premium_history` 会按可转债代码 + 日期补写已有 `strategy_lab_cb_daily_factors` 行中的 `premium_rate` / `remaining_size` 空值；已有值和不存在的行都会跳过。

返回 `running` 后通过 `GET /api/v1/strategy-lab/data-sync/runs` 轮询进度。

## 源数据 → 数据库 映射

### 基础数据：cb-list → `strategy_lab_cb_basic`

| opencli 源字段 | 落库字段 | 处理 |
|---|---|---|
| `bondId` | `bond_code`（PK） | 直接 |
| `bondName` | `bond_name` | 直接 |
| `status`（active/delisted） | `status` | active→`"正常"`，delisted→`"已退市"` |
| `lastPrice` / `lastTradeDate` | `terms_json` 元数据 `last_price` / `last_trade_date` | 仅已退市列表有效 |

### 基础数据：cb-detail → `strategy_lab_cb_basic`（直列字段）

| opencli 源字段 | 落库字段 | 处理 |
|---|---|---|
| `bond_code` / `bond_name` | `bond_code` / `bond_name` | 直接 |
| `stock_code` / `stockCode` / `stockId` / `convert_stock_code` | `stock_code` | 直接，优先保留规范化数字代码 |
| `stock_name` / `stockName` / `stock_nm` | `stock_name` | 直接 |
| `list_date` / `maturity_date` | `list_date` / `maturity_date` | `"-"`（未上市/未知）→ NULL |
| `remaining_size` / `convert_price` | `remaining_size` / `convert_price` | 字符串→浮点 |

### 基础数据：cb-detail → `strategy_lab_cb_basic.terms_json`（元数据）

以下字段无独立列，统一存入 `terms_json` 元数据（与 `sync_cb_local_init.py` 的元数据模式一致）：

`industry`、`start_date`、`convert_start_date`、`put_start_date`、`put_price`、`redemption_price`、`issue_size`、`bond_rating`、`force_redeem_countdown`、`down_revise_countdown`、`put_countdown`、`delist_reason`、`redemption_announcement_date`、`last_trading_date`、`last_conversion_date`、`delisted`

### 基础数据：cb-detail → `strategy_lab_cb_terms`

| opencli 源字段 | 落库字段 |
|---|---|
| `force_redemption_trigger_price` | `redeem_trigger_price` |
| `adjust_trigger_price` | `down_revise_trigger_price` |
| `put_trigger_price` | `put_trigger_price` |
| 条款文本 | 详情接口不提供，`redeem_clause` / `down_revise_clause` / `put_clause` 保持原值（不覆盖） |

### 基础数据：cb-detail.cb_event_list → `strategy_lab_cb_events`

| 源字段 | 落库字段 | 处理 |
|---|---|---|
| `event_time` | `event_date` | 直接 |
| `event_type` | `event_type` | 原样存储（down_revise / no_revise / no_redemption / other / bonus / stock_incentive / issue / bond_rating_change / undefined） |
| `detail` | `event_detail` | 直接 |
| `rating_from` / `rating_to` / `issuer_rating` | `event_detail` | 仅 `bond_rating_change` 事件，拼入详情文本 |

### 行情：东财 / 腾讯 日K → `stock_daily`

| 数据源字段 | 落库字段 | 说明 |
|---|---|---|
| 日期 | `date` | |
| open / high / low / close | `open` / `high` / `low` / `close` | |
| volume / amount | `volume` / `amount` | |
| 转债代码 | `code` | |
| — | `instrument_type` | 固定 `'convertible_bond'`，与股票行情同表共存 |
| close | `strategy_lab_cb_daily_factors.close` | 回填一份，供回测引擎读取 |

代码前缀规则：`11xxxx` → 沪（`sh{code}` / 东财 `secid=1.{code}`）；`12xxxx` → 深（`sz{code}` / 东财 `secid=0.{code}`）。

## 默认行为

- opencli 可执行文件默认取系统 PATH 的 `opencli`，可用环境变量 `OPENCLI_BIN` 覆盖；`ENABLE_EASTMONEY_PATCH=true` 开启东财反爬补丁（东财主源在高频/受限网络下建议开启）。
- 默认仅同步**活跃**可转债；`--include-delisted` 显式开启已退市（已退市约 700+ 只，逐个 opencli 详情耗时长）。
- 行情同步不带 `--start-date` 时为**增量**：有本地历史则从最后日期次日开始，无历史则从 `max(list_date, 2020-01-01)` 开始；`--end-date` 缺省为今天。
- 行情源东财失败自动降级到腾讯；单只失败不中断整体，失败清单写入同步记录 `result`。
- 每个同步任务在 `strategy_lab_sync_runs` 落一条记录，同一时间仅允许一个同步任务写库。
