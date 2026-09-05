# 可转债数据同步

本文档说明本项目可转债数据同步的各项能力、入口用法，以及 opencli / 行情接口源字段到数据库表字段的完整映射。

## 能力总览

| 能力 | 数据源 | 落库位置 | 说明 |
|---|---|---|---|
| 基础数据同步 | 本机 opencli（`cb-list` + `cb-detail`） | `strategy_lab_cb_basic` / `strategy_lab_cb_terms` / `strategy_lab_cb_events` | 默认先拉列表，再对每只转债逐个填充详情；传入 `symbols` 时直接按目标代码拉详情，跳过列表抓取 |
| 行情同步（OHLC） | 东财优先，腾讯兜底 | `stock_daily`（`instrument_type='convertible_bond'`）+ `strategy_lab_cb_daily_factors.close` 回填 | 支持选择起始日期，非每次全量 |
| 补数同步（溢价率/剩余规模） | 本机 opencli（`cb-premium-history`） | `stock_daily`（仅补 `premium_rate` / `remaining_size` 空值） | 按“可转债代码 + 日期”匹配已有日线记录，不新建行、不覆盖已有非空值 |
| 正股行情同步（OHLC） | 腾讯日K | `stock_daily`（`instrument_type='stock'`） | 独立同步方法 `sync_cb_stock_ohlc`，仅同步**在市转债**对应正股（去重），不回填转债因子表 |
| 因子计算（正股价/溢价率/剩余规模） | 正股当日日K + 本地表计算 | `strategy_lab_cb_daily_factors`（`stock_close` / `premium_rate` / `remaining_size`） | 独立方法 `sync_cb_factors`，仅活跃转债；字段按可得性定向更新，缺失项不覆盖；可经 API / 数据同步页面触发 |
| 盘后调度链路（手动触发） | 组合：盘后=基础+行情+因子，盘中=行情+因子，外加邮件通知 | 同各子能力 | `sync_type="cb_scheduled"`（手动）或定时调度 `run_scheduled_sync`；整条链路共享单条 sync run，`run_kind`/`trade_date` 落列供实盘数据检查（`latest_sync_run`）查询，结束时发通知邮件 |

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
  "sync_type": "cb_basic",          // cb_basic / cb_ohlc / cb_premium_history / cb_factors / cb_scheduled / all
  "include_delisted": false,
  "start_date": "2026-01-01",       // 行情同步有效
  "end_date": "2026-12-31",
  "symbols": ["113709"]
}
```

`symbols` 在 `cb_basic` 场景下会直接触发单只/少量标的详情拉取，不再先执行 `cb-list`。
`cb_premium_history` 会按可转债代码 + 日期补写已有 `strategy_lab_cb_daily_factors` 行中的 `premium_rate` / `remaining_size` 空值；已有值和不存在的行都会跳过。
`cb_factors` 为单日因子计算：因子日期取 `end_date`（缺省 `start_date`，均缺省为今天），页面「数据同步」来源下拉框可直接选择触发。
`cb_scheduled` 为手动触发盘后调度链路：按序执行基础数据 → 行情 → 因子计算（等价定时调度使用的 `run_scheduled_sync`，`run_kind='after_close'`），整条链路复用一条 sync run（取消/进度挂在同一条记录上），完成后按配置发送盘后通知邮件。

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

### 正股行情：腾讯日K → `stock_daily`

`StrategyLabDataSyncService.sync_cb_stock_ohlc`（`src/services/strategy_lab/data_sync_service.py`）是独立的后端同步方法，**没有 CLI / API / 定时调度入口**，按需通过 Python 调用：

- 标的来源：`strategy_lab_cb_basic.stock_code`（去重后逐只同步），仅取 `status='正常'` 的在市转债，已退市转债的正股不同步。
- 数据源：腾讯 `fqkline` 日线（`CbUnderlyingStockOhlcFetcher`），成交量保持腾讯原始单位（手），与 akshare A 股日线口径一致。
- 增量语义与转债行情一致：不传 `start_date` 时，有本地历史从最后日期次日开始，无历史从 `2020-01-01` 开始；`end_date` 缺省为今天。
- 腾讯接口按 count 拉最近 N 根（上限约 800 个交易日），首次初始化无法一次拉全 2020 年以来的历史；同代码同日期重复执行为幂等 UPSERT。

| 数据源字段 | 落库字段 | 说明 |
|---|---|---|
| 日期 | `date` | |
| open / high / low / close | `open` / `high` / `low` / `close` | |
| volume / amount | `volume` / `amount` | volume 单位为手 |
| 正股代码 | `code` | 纯 6 位数字 |
| — | `instrument_type` | 固定 `'stock'`，与主流程 A 股日线同口径 |

正股代码前缀规则：`6xxxxx` → 沪（`sh{code}`）；`0/3xxxxx` → 深（`sz{code}`）；其他前缀（如北交所）不发请求直接跳过。

### 因子计算：本地计算 → `strategy_lab_cb_daily_factors`

`StrategyLabDataSyncService.sync_cb_factors`（`src/services/strategy_lab/data_sync_service.py`）是独立的后端方法，**已接入数据同步 API 与 Web 数据同步页面**（`sync_type="cb_factors"`），**没有 CLI / 定时调度入口**：

- 标的来源：`strategy_lab_cb_basic` 中 `status='正常'` 的转债；`symbols` 为转债代码过滤，语义与其他同步方法一致；`trade_date` 缺省为当天，经 API / 页面触发时取 `end_date`（缺省 `start_date`）。
- 转债价：当日因子行已有的 `close`（由行情同步 `sync_cb_ohlc` 写入）；当日无 `close` 的转债计入跳过，无法计算因子。
- 正股价：`CbUnderlyingStockOhlcFetcher` 拉正股**当日**日 K 的 `close`（盘中"当天那根" close 即实时最新价，已被 `scripts/verify_cb_realtime_snapshot.py` 验证）；同正股多债只拉一次；非交易日/停牌/拉取失败则该债跳过溢价率（正股价可用时仍落 `stock_close`）。
- 剩余规模：取 `strategy_lab_cb_basic.remaining_size`；主数据为空时不覆盖因子行已有值。
- 溢价率（本地计算，存百分数、保留两位小数）：

```
转股价值 = 正股 close × 100 ÷ convert_price     （convert_price 取 strategy_lab_cb_basic.convert_price）
premium_rate = (转债 close ÷ 转股价值 − 1) × 100
```

| 输入 | 落库字段 | 说明 |
|---|---|---|
| 正股当日 close | `stock_close` | 新增列；存量 SQLite 库初始化时自动补列 |
| 溢价率计算值 | `premium_rate` | 覆盖刷新（重算语义） |
| `strategy_lab_cb_basic.remaining_size` | `remaining_size` | 仅在主数据值非空时写入 |

写入语义：走 `update_cb_daily_factor_fields` 按 `(bond_code, trade_date)` 定向更新，**只更新本次实际得到的字段**，不新建行，也不清空 close / 预警位等未涉及字段；单只失败不中断整体，失败清单写入同步记录 `result`。

## 默认行为

- opencli 可执行文件默认取系统 PATH 的 `opencli`，可用环境变量 `OPENCLI_BIN` 覆盖；`ENABLE_EASTMONEY_PATCH=true` 开启东财反爬补丁（东财主源在高频/受限网络下建议开启）。
- 默认仅同步**活跃**可转债；`--include-delisted` 显式开启已退市（已退市约 700+ 只，逐个 opencli 详情耗时长）。
- 行情同步不带 `--start-date` 时为**增量**：有本地历史则从最后日期次日开始，无历史则从 `max(list_date, 2020-01-01)` 开始；`--end-date` 缺省为今天。
- 行情源东财失败自动降级到腾讯；单只失败不中断整体，失败清单写入同步记录 `result`。
- 每个同步任务在 `strategy_lab_sync_runs` 落一条记录，同一时间仅允许一个同步任务写库。
