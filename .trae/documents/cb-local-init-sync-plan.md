# 可转债本地初始化同步脚本 实施计划

## 1. Summary

编写一次性初始化脚本 `scripts/sync_cb_local_init.py`，从本地 `localhost:5273` 的三个接口（可转债列表 / 历史行情 / 事件）拉取全量数据，映射并写入 DSA 数据库：

- **行情 OHLCV** → 共用现有股票行情表 `stock_daily`（用户已确认）
- **策略因子**（close + 溢价率 + 告警）→ `strategy_lab_cb_daily_factors`（close 与 stock_daily 冗余一份供回测引擎读取）
- **基础信息** → `strategy_lab_cb_basic`（含详情接口补充字段，收进 `terms_json` 元数据）
- **事件** → `strategy_lab_cb_events`
- **`strategy_lab_cb_terms` 不写入**（三个接口均无条款数据）

不走现有 AkShare/Jisilu/OpenCLI 同步链路（`data-sync` 保留用于日常增量），本次为一次性初始化。

## 2. Current State Analysis

### 来源接口（均已实测可访问，本地服务 5273 在运行）

**1) 列表 `GET /api/cb/page`**（`page_size` 上限 **200**，全量 **1037** 只）：
```
items[]: bond_code, bond_name, stock_code, stock_name, industry, list_date, maturity_date, status
```
**2) 详情 `GET /api/cb/{code}`**（补充基础信息）：
```
bond_code, bond_name, stock_code, stock_name, industry, list_date, maturity_date, status,
interest_start_date, issue_size, issuer_rating, bond_rating, redeem_price_at_maturity,
convert_start_date, put_start_date, latest_redeem_date, latest_down_revise_date,
delist_reason, delisted_at
```
**3) 行情 `GET /api/cb/{code}/quotes`**（`page_size` 上限 **200**，单只可达 1000+ 条）：
```
items[]: bond_code, trade_date, open, high, low, close, volume, amount
```
**4) 事件 `GET /api/cb/{code}/events`**（`page_size` 上限 200）：
```
items[]: event_type, event_date, detail(JSON 字符串，含 HTML)
```
事件类型实测：`NEW_ISSUE / REDEEM / DOWN_REVISE / NO_REVISE / BONUS / STOCK_INCENTIVE / OTHER / NO_REDEMPTION`

### 目标表（现有 schema，无需改表）

- `stock_daily`（[storage.py](file:///Users/red/Documents/code/daily_stock_analysis/src/storage.py#L100-L149)）：`code, date, open, high, low, close, volume, amount, pct_chg, ma5/10/20, volume_ratio, data_source`，唯一约束 `(code, date)`；现有 `DatabaseManager.save_daily_data(df, code, data_source)` 已实现按 `(code,date)` 批量 UPSERT（[storage.py](file:///Users/red/Documents/code/daily_stock_analysis/src/storage.py#L3189-L3241)），可直接复用
- `strategy_lab_cb_basic`：`bond_code(PK), bond_name, stock_code, stock_name, market, list_date, maturity_date, remaining_size, current_premium_rate, convert_price, terms_json(Text), source, updated_at`
- `strategy_lab_cb_terms`：条款列（本次不写入）
- `strategy_lab_cb_daily_factors`：`bond_code, trade_date, close, premium_rate, remaining_size, redeem_alert, down_revise_alert, put_alert, source`，唯一约束 `(bond_code, trade_date)`
- `strategy_lab_cb_events`：`bond_code, event_date, event_type, event_detail, source`，唯一约束 `(bond_code, event_date, event_type)`

复用点：`StrategyLabDataRepository`（[data_repo.py](file:///Users/red/Documents/code/daily_stock_analysis/src/repositories/strategy_lab/data_repo.py)）的 `upsert_cb_basic / upsert_cb_daily_factors / upsert_cb_events / create_sync_run / complete_sync_run / fail_sync_run`。

## 3. 字段映射与"放不下"说明

### 3.1 映射表

**列表接口 → `strategy_lab_cb_basic`**

| 源字段 | 目标 | 说明 |
|---|---|---|
| bond_code | bond_code (PK) | |
| bond_name | bond_name | |
| stock_code | stock_code | |
| stock_name | stock_name | |
| list_date | list_date | |
| maturity_date | maturity_date | |
| industry | terms_json 元数据 | 无独立列 |
| status | terms_json 元数据 | 无独立列 |
| — | market | 固定 `'cn'` |

**详情接口 → 同上表（合并写入 terms_json 元数据）**

| 源字段 | 目标 | 说明 |
|---|---|---|
| issue_size | terms_json | **发行规模 ≠ 剩余规模**，不可映射 remaining_size |
| issuer_rating / bond_rating | terms_json | |
| redeem_price_at_maturity | terms_json | 到期赎回价 |
| interest_start_date / convert_start_date / put_start_date | terms_json | |
| latest_redeem_date / latest_down_revise_date | terms_json | |
| delist_reason / delisted_at | terms_json | |

**行情接口 → `stock_daily`（共用）+ `strategy_lab_cb_daily_factors`（close 冗余）**

| 源字段 | 目标 | 说明 |
|---|---|---|
| bond_code | stock_daily.code | 与股票行情共用一张表 |
| trade_date | stock_daily.date | |
| open / high / low | stock_daily.open/high/low | |
| close | stock_daily.close **+** daily_factors.close | 冗余：daily_factors 供回测引擎 `load_cb_backtest_rows` 读取，不改引擎 |
| volume / amount | stock_daily.volume/amount | 源 amount 多为 null |
| — | stock_daily.data_source | `'cb_local_init'` |

**事件接口 → `strategy_lab_cb_events`**

| 源字段 | 目标 | 说明 |
|---|---|---|
| bond_code | bond_code | |
| event_date | event_date | |
| event_type | event_type | 归一化映射（见下） |
| detail | event_detail | 原样存 JSON 字符串（含 HTML） |

event_type 归一化：`NEW_ISSUE→new_issue`、`REDEEM→strong_redeem`、`DOWN_REVISE→down_revise`；其余（`NO_REVISE/BONUS/STOCK_INCENTIVE/OTHER/NO_REDEMPTION`）原样保留（前端事件标签对未知类型有默认色）。

### 3.2 放不下 / 留空清单（重要）

| 项 | 说明 | 处理 |
|---|---|---|
| **`strategy_lab_cb_terms` 整表** | 三个接口都没有强赎/下修/回售条款文本与触发价 | 本次不写入，整表留空，后续另找数据源 |
| **溢价率 premium_rate** | list/detail/quotes 均无此字段 | `cb_basic.current_premium_rate`、`daily_factors.premium_rate` 留 NULL；**双低/低溢价策略评分依赖它，本次同步后此类回测受限**（用户已确认：另找数据源补） |
| **剩余规模 remaining_size** | 源无（issue_size 是发行规模，语义不同） | 留 NULL（triple_low 评分也受影响） |
| **转股价 convert_price** | 源无 | 留 NULL |
| **三个告警 redeem/down_revise/put_alert** | 源无 | 默认 False（不做事件推导，避免误判） |

## 4. Proposed Changes

### 新增 `scripts/sync_cb_local_init.py`

一次性脚本，风格对齐现有 `scripts/fetch_tushare_stock_list.py`（argparse + logging + `load_dotenv` + `sys.path.insert(项目根)`）。

**参数**：
- `--base-url`（默认 `http://localhost:5273`）
- `--page-size`（默认 200，接口上限）
- `--workers`（默认 8，HTTP 抓取并发）
- `--limit N`（只处理前 N 只，调试用）
- `--bond CODE`（只处理单只）
- `--only-empty`（跳过 `stock_daily` 中已有日线的转债，断点续跑）
- `--dry-run`（只打印映射结果不落库）

**流程**：
1. 初始化 `DatabaseManager`；创建 `strategy_lab_sync_runs` 记录（`sync_type='cb_local_init'`，running）
2. 拉列表全部分页（`page_size=200`，约 6 页）→ 全部转债
3. 每只转债：拉详情 → 组装 basic 行（直接字段 + terms_json 元数据）→ `upsert_cb_basic`（分块）
4. 每只转债：并发拉 quotes 全部分页 → 组装 DataFrame → `DatabaseManager.save_daily_data(df, code, 'cb_local_init')` 写 `stock_daily`；同时抽 `close` 组装 daily_factors 行 → `upsert_cb_daily_factors`
5. 每只转债：拉 events 全部分页 → 归一化 event_type → `upsert_cb_events`
6. 完成后 `complete_sync_run`（汇总各表新增/更新行数、失败清单）；异常 `fail_sync_run`

**健壮性**：
- HTTP 请求带 `timeout`（10s）+ 指数退避重试（3 次）；单只失败记录不中断整体
- DB 写入统一用 `threading.Lock` 串行（SQLite 单写），HTTP 抓取用 `ThreadPoolExecutor` 并发
- 幂等：全部走 upsert；`--only-empty` 支持断点续跑；重复执行不产生重复行

**日志与汇总**：打印每阶段计数、失败转债清单、最终各表行数（复用 sqlite `SELECT COUNT(*)`）。

### 文档

- `docs/strategy-lab-migration.md`：进度记录补一行"可转债本地初始化同步脚本 `scripts/sync_cb_local_init.py`（一次性，行情入 stock_daily）"，并注明溢价率/条款缺口
- `docs/CHANGELOG.md` `[Unreleased]` 补一条：`- [chore] 新增可转债本地初始化同步脚本 scripts/sync_cb_local_init.py（行情写入 stock_daily，一次性）`
- 脚本 docstring 内写明用法、来源、字段映射与缺口

## 5. Assumptions & Decisions

1. **行情共用 `stock_daily`**（用户已确认）：OHLCV 进股票行情表，`code` 存转债代码，`data_source='cb_local_init'`；不改任何表结构
2. **close 冗余**：`stock_daily.close` 与 `strategy_lab_cb_daily_factors.close` 各存一份——因子表供策略回测引擎原路径读取，引擎不改
3. **详情补充字段进 `terms_json`**：不新增列，避免 schema 膨胀；后续如需独立列再拆
4. **溢价率/条款/剩余规模缺口接受**（用户已确认）：本数据源无这些字段，留空并标注影响；另找数据源补溢价率
5. **`cb_terms` 不写**：源无条款数据，避免编造
6. **事件类型归一化**：只映射三种已知类型，其余透传
7. **一次性脚本放 `scripts/`**，不进主运行时，不注册调度

## 6. Verification Steps

1. 语法检查：`.venv/bin/python -m py_compile scripts/sync_cb_local_init.py`
2. 小范围试跑：`.venv/bin/python scripts/sync_cb_local_init.py --bond 113708 --dry-run`（核对映射字段）
3. 单只真实入库：`--bond 113708`，核对：
   - `stock_daily` 中 `code='113708'` 的 OHLCV 行
   - `strategy_lab_cb_daily_factors` 的 close 行数一致
   - `strategy_lab_cb_basic` 的 terms_json 含 rating/issue_size 等元数据
   - `strategy_lab_cb_events` 的事件（113708 应为 NEW_ISSUE→new_issue）
4. 重复执行同一单只，确认不产生重复行（幂等）
5. 全量试跑 `--limit 5` 后视情况全量 `--only-empty`；抽查 `stock_daily` 总行数、失败清单为空
6. 交付说明按 AGENTS.md 默认结构：改了什么 / 为什么 / 验证 / 未验证（全量 1037 只的耗时与网络稳定性）/ 风险（溢价率与条款缺口导致双低回测受限；`stock_daily` 与股票行情同表需注意 code 语义）/ 回滚（脚本不入运行时，可删数据重跑；`--only-empty` 保护）
