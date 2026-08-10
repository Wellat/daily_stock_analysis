# 策略实验室迁移方案

本文记录 `trading-backtest` 合并进 `daily_stock_analysis` 的已确认方案、阶段拆分、验证命令和进度入口。后续开发按本文推进，并在每个阶段完成后更新进度。

更新时间：2026-08-10

## 1. 目标

将 `/Users/red/Documents/code/trading-backtest` 的策略回测、批量实验、参数搜索、可转债数据能力和策略信号能力迁移到 `/Users/red/Documents/code/daily_stock_analysis`，后续只维护 `daily_stock_analysis`。

迁入能力以新独立域接入，命名为：

- 中文产品名：策略实验室
- 代码域名：`strategy_lab`
- API 前缀：`/api/v1/strategy-lab`
- Web 路由：`/strategy-lab`

## 2. 核心共识

### 2.1 两种回测保持分离

`daily_stock_analysis` 现有回测用于评估模型历史判断质量，继续保留现有命名、接口和页面：

- `src/core/backtest_engine.py`
- `src/services/backtest_service.py`
- `api/v1/endpoints/backtest.py`
- `apps/dsa-web/src/pages/BacktestPage.tsx`

`trading-backtest` 迁入的是策略回测与策略实验能力，不能覆盖或混入现有模型判断回测。

### 2.2 新域不能写死可转债

策略实验室第一阶段从可转债策略开始，但领域命名要支持后续扩展到 A 股和港股：

- 通用域使用 `strategy_lab`。
- 通用 API 不使用 `/cb-backtest`、`/convertible-bond-backtest` 等路径。
- 可转债只是第一个 `instrument_type=convertible_bond` adapter。
- 可转债专有概念可以带 `cb` 命名，例如 `strategy_lab_cb_terms`、`strategy_lab_cb_events`、`cb_premium_rate`、`cb_remaining_size`。

### 2.3 不是目录 copy

迁移不是把 `trading-backtest` 目录简单复制到仓库里。原则是：

- 复用 `daily_stock_analysis` 现有配置、数据库、API、测试、前端和数据源基础设施。
- 抽取共同数据与执行接口，避免出现第二套平行实现。
- 可转债专有数据通过 `strategy_lab` adapter/factor 层承接。

### 2.4 Portfolio 是唯一持仓真源

`daily_stock_analysis` 现有 Portfolio 模块是唯一持仓真源。

`trading-backtest` 里的纸面持仓、实盘收益、策略信号相关能力迁入时，不新建第二套持仓系统：

- 策略实验室可以创建或关联 Portfolio account，例如 `strategy_lab` / `paper_strategy` 类型账户。
- 回测生成历史模拟交易和指标，不直接污染真实持仓。
- 纸面交易、策略跟踪、实盘收益复用现有 portfolio account / position / trade / snapshot 体系。
- `strategy_lab` 只保存策略运行、信号、参数、指标，以及与 Portfolio 账户、交易、持仓的关联 ID。

### 2.5 数据层分层迁入

通用行情尽量复用或扩展当前项目的数据层：

- `data_provider/`
- `src/services/market_data_service.py`
- `stock_daily`
- 现有数据源 fallback、缓存和超时语义

可转债特有数据作为策略实验室的 instrument/factor sync：

- 可转债条款
- 转股溢价率
- 剩余规模
- 强赎、下修、回售、新债发行/上市等事件

不直接迁入独立 `data_pipeline/` 平行目录，避免第二套调度、日志、配置和数据源体系。

### 2.6 存储生命周期统一

不新增第二个数据库连接或 `DatabaseManager`。

策略实验室表模型接入现有 storage 生命周期，领域逻辑单独封装：

- `src/core/strategy_lab/`
- `src/services/strategy_lab/`
- `src/repositories/strategy_lab/`
- `api/v1/endpoints/strategy_lab.py`
- `api/v1/schemas/strategy_lab.py`

如果 `src/storage.py` 后续需要拆分，应作为单独重构评估，不在第一阶段顺手扩大范围。

### 2.7 API 直接开放

策略实验室 API 使用正式路径并直接开放，不增加默认关闭的功能开关：

- `/api/v1/strategy-lab/strategies`
- `/api/v1/strategy-lab/runs`
- `/api/v1/strategy-lab/batches`
- `/api/v1/strategy-lab/signals`
- `/api/v1/strategy-lab/instruments`（标的列表、详情、日线因子、事件查询）
- `/api/v1/strategy-lab/data-sync`

第一阶段只实现最小后端闭环，不隐藏路由，但也不声明完整产品能力。

### 2.8 前端按 DSA 设计系统重做

旧 `trading-backtest/web` 使用 Ant Design。迁入 `daily_stock_analysis` 时不整体搬 UI，也不引入第二套 UI 框架。

前端第二阶段按 `apps/dsa-web` 现有 React、Tailwind、lucide 和 common 组件体系重做，只迁交互逻辑、字段契约和页面结构思路。

## 3. trading-backtest 功能清单

| 功能域 | 现有能力 | 主要来源 |
| --- | --- | --- |
| 数据 ETL | 可转债 universe、退市列表、日线 OHLC、集思录估值、详情、指数日线、一次性强赎事件回填 | `cb_quant/data_pipeline/` |
| 数据模型 | 可转债基础信息、条款、日线、事件、因子、策略、回测结果、批次、信号、纸面持仓 | `cb_quant/app/models/entities.py`、`database_schema.sql` |
| 策略回测 | MA、双低轮动、低溢价策略，支持费用、持仓上限、调仓周期、止损、行业/规模/到期筛选 | `cb_quant/backtest/` |
| 批量实验 | 批量回测、批次历史、参数搜索、批次删除、SSE 进度 | `cb_quant/backtest/batch_*`、`cb_quant/app/api/routes/backtest.py` |
| 事件研究 | 上市日、转股起始日、强赎公告日、下修公告日、回售起始日的事件收益统计 | `cb_quant/backtest/study/event_stat.py` |
| 实盘/纸面信号 | 策略参数、纸面持仓、组合摘要、信号生成、信号确认 | `cb_quant/app/services/live_signal_service.py`、`cb_quant/app/api/routes/live_signal.py` |
| API | `/api/cb`、`/api/factor`、`/api/backtest`、`/api/etl`、`/api/live-signals` | `cb_quant/app/api/routes/` |
| Web | 参数搜索、批次历史、回测结果、图表、实盘信号面板 | `web/src/` |
| 测试 | 回测、策略、筛选、事件研究、fetcher、实盘信号 | `cb_quant/tests/`、`web/src/utils/__tests__/` |
| 文档 | 架构、ETL、回测、计划和规格 | `cb_quant/README.md`、`docs/agent-reference.md`、`docs/etl-overview.md`、`docs/superpowers/` |

## 4. daily_stock_analysis 落点

| 迁入内容 | 目标落点 | 说明 |
| --- | --- | --- |
| 策略实验室核心接口 | `src/core/strategy_lab/` | 定义 engine、data feed、strategy、metrics、run result 等通用协议 |
| 策略实验室服务 | `src/services/strategy_lab/` | 负责 run 创建、状态流转、结果落库、组合与 Portfolio 关联 |
| 策略实验室仓储 | `src/repositories/strategy_lab/` | 封装 run、metrics、trades、curve、instrument/factor 查询 |
| API schema | `api/v1/schemas/strategy_lab.py` | 请求/响应边界，不暴露 ORM |
| API endpoint | `api/v1/endpoints/strategy_lab.py` | 挂载到 `/api/v1/strategy-lab` |
| 数据表模型 | `src/storage.py` 或后续认可的 storage 扩展位置 | 统一使用现有 `DatabaseManager` |
| 通用行情 | 现有 `stock_daily`、`market_data_service`、`data_provider/` | 策略引擎通过统一接口读取 |
| 可转债专有数据 | `strategy_lab_cb_*` 表与 adapter | 可带 `cb` 命名，但归属策略实验室 |
| Portfolio 关联 | 现有 Portfolio 模块 | Portfolio 是唯一持仓真源 |
| 前端入口 | `apps/dsa-web/src/pages/StrategyLabPage.tsx` 等 | 第二阶段实现，不引入 Ant Design |
| 文档 | 本文、`docs/INDEX.md`、必要时 `docs/full-guide.md`、`docs/CHANGELOG.md` | 用户可见能力变化必须同步 |

## 5. 表命名方向

通用策略实验室表使用 `strategy_lab_*` 前缀：

- `strategy_lab_runs`
- `strategy_lab_run_metrics`
- `strategy_lab_trades`
- `strategy_lab_equity_curve`
- `strategy_lab_batches`
- `strategy_lab_batch_items`
- `strategy_lab_signals`

可转债专有表允许带 `cb`：

- `strategy_lab_cb_basic`
- `strategy_lab_cb_terms`
- `strategy_lab_cb_daily_factors`
- `strategy_lab_cb_events`

不复用旧项目的 `backtest_result`、`backtest_batch` 等表名，避免和当前模型判断回测的 `backtest_results`、`backtest_summaries` 混淆。

## 6. 阶段计划

### Phase 0：方案与迁移文档

目标：固化迁移共识、命名、边界和阶段计划。

范围：

- 新增本文。
- 在文档索引挂入口。
- 记录每阶段工作、验证命令、风险和进度。

验收：

- 方案文档可作为后续开发任务清单。
- 不修改业务代码。

验证命令：

```bash
python scripts/check_ai_assets.py
```

如仅修改普通文档且未触碰 AI 协作治理资产，可不运行代码测试，交付说明写明 `Docs only, tests not run`。

### Phase 1：后端最小闭环

目标：策略实验室后端能创建一次策略 run、执行 `double-low` 样例策略、落库并查询结果。

范围：

- 新增 `strategy_lab` 通用领域骨架。
- 新增 engine 抽象接口，以 DSA 内部确定性 adapter 作为第一实现，不把 Backtrader 引入主运行时。
- 迁入 `double-low` 代表策略的核心逻辑。
- 使用 fixture / repository contract 跑通，不接真实 ETL。
- 新增最小数据表：
  - `strategy_lab_runs`
  - `strategy_lab_run_metrics`
  - `strategy_lab_trades`
  - 可选 `strategy_lab_equity_curve`
- 新增最小 API：
  - `POST /api/v1/strategy-lab/runs`
  - `GET /api/v1/strategy-lab/runs/{run_id}`
  - `GET /api/v1/strategy-lab/runs/{run_id}/trades`
- 新增测试覆盖：
  - engine adapter
  - repository
  - service
  - API contract
- 文档补充现有模型判断回测和策略实验室的差异。

不包含：

- 旧历史结果导入。
- 真实数据 provider（在 Phase 2 接入）。
- 参数搜索。
- 批量批次历史。
- 实盘信号确认。
- 前端页面。

验证命令：

```bash
python -m pytest tests/test_strategy_lab_engine.py tests/test_strategy_lab_service.py tests/test_strategy_lab_api.py
python -m py_compile api/v1/endpoints/strategy_lab.py api/v1/schemas/strategy_lab.py
./scripts/ci_gate.sh
```

如果新增 storage 模型或迁移逻辑，还要覆盖：

```bash
python -m pytest tests/test_storage.py tests/test_api_schema_pydantic.py
```

### Phase 2：可转债数据 adapter 与数据同步

目标：接入可转债专有数据同步，支撑真实可转债策略回测。

范围：

- 将旧 ETL 拆分为策略实验室 instrument/factor sync。
- 通用行情优先复用现有行情层。
- 可转债专有数据写入 `strategy_lab_cb_*` 表。
- 迁入可转债数据 provider 逻辑时保留 timeout、错误日志和降级语义。
- 新增数据同步 API：
  - `POST /api/v1/strategy-lab/data-sync`
  - `GET /api/v1/strategy-lab/data-sync/runs`
- 明确 `market`、`instrument_type`、provider、数据口径和更新时间字段。

验证命令：

```bash
python -m pytest tests/test_strategy_lab_data_sync.py tests/test_strategy_lab_service.py tests/test_strategy_lab_phase2_api.py
python -m pytest -m "not network" tests/test_strategy_lab_data_sync.py
./scripts/ci_gate.sh
```

在线数据源 smoke 只在明确需要时执行，并在交付说明中标出依赖、时间和失败影响。

### Phase 3：批量回测与参数搜索

目标：迁入批量 run、参数搜索、批次历史和进度状态。

范围：

- 新增 `strategy_lab_batches`、`strategy_lab_batch_items`。
- 支持参数网格、批量任务状态、失败重试和结果摘要。
- 迁入旧项目参数搜索逻辑，但 API/schema 用 `strategy_lab` 通用契约表达。
- 保留可转债策略为首批策略，接口支持未来 A 股/港股策略。

验证命令：

```bash
python -m pytest tests/test_strategy_lab_batch.py tests/test_strategy_lab_phase2_api.py
./scripts/ci_gate.sh
```

### Phase 4：Portfolio 统一与策略信号

目标：将纸面持仓、实盘收益和策略信号接入现有 Portfolio 模块。

范围：

- 不迁入第二套 `paper_portfolio_position` 真源。
- 策略实验室信号关联现有 portfolio account / trades / positions / snapshots。
- 新增或复用策略账户类型，例如 `strategy_lab` / `paper_strategy`。
- 策略信号记录只保存运行、信号、参数、建议动作和 Portfolio 关联 ID。
- 收敛实盘收益、纸面收益、策略回测收益三者的口径说明。

验证命令：

```bash
python -m pytest tests/test_strategy_lab_signal.py tests/test_portfolio_service.py tests/test_portfolio_api.py
./scripts/ci_gate.sh
```

### Phase 5：前端策略实验室

目标：新增独立 Web 入口 `/strategy-lab`。

范围：

- 新增导航入口：策略实验室。
- 现有 `/backtest` 页面不动。
- 按 DSA 现有设计系统重做页面，不引入 Ant Design。
- 初期页面分区：
  - 策略回测
  - 参数搜索
  - 批次历史
  - 策略信号
  - 数据同步
- 迁移旧 Web 的交互逻辑和字段契约，不整体搬组件。

验证命令：

```bash
cd apps/dsa-web
npm test
npm run lint
npm run build
```

如涉及页面视觉和交互，还应补充 Playwright 或截图证据。

### Phase 6：真实数据回归、文档与旧项目退役

目标：确认 `daily_stock_analysis` 已承接旧项目核心能力，旧 `trading-backtest` 可停止维护。

范围：

- 用真实或脱敏样例数据验证可转债策略回测。
- 对比旧项目同策略同参数输出，解释口径差异。
- 补齐用户文档、API 文档、配置说明和 changelog。
- 明确不迁入旧 HTML 报告、CSV 产物和旧历史结果；如需要，另开一次性 importer。
- 记录旧项目退役清单。

验证命令：

```bash
python -m pytest
cd apps/dsa-web && npm test && npm run lint && npm run build
./scripts/ci_gate.sh
```

如触及桌面端展示或打包，还要执行：

```bash
cd apps/dsa-desktop && npm install && npm run build
```

## 7. 复审补强项

本节记录 2026-08-09 对方案的二次复审结论。以下事项不是 Phase 1 的前置阻断，但后续阶段开始前必须逐项确认，避免隐性漏迁或重新引入平行体系。

| 事项 | 当前结论 | 阶段要求 |
| --- | --- | --- |
| CLI 入口 | Phase 1 先做 API 和测试，不急着加 CLI；后续如需要命令行入口，应接入 `daily_stock_analysis` 现有 `main.py` / `scripts/` 风格，不迁入旧 `python -m data_pipeline.cli` 平行命令体系 | Phase 3 或 Phase 6 前确认 |
| 长任务进度 | 单次小样例 run 可同步返回；批量回测、参数搜索和真实数据同步必须定义进度模型，优先复用 DSA 现有任务队列 / SSE / 运行状态模式，不能只照搬旧项目 batch stream | Phase 3 前确认 |
| benchmark 口径 | 旧项目默认 `sh000300`，迁入后必须把 benchmark 作为 run 参数和落库字段保存；未来 A 股、港股、可转债可以有不同默认 benchmark，不允许在通用层写死沪深 300 | Phase 1 表结构设计时确认 |
| 指标与单位 | `total_return`、`annualized_return`、`max_drawdown`、`sharpe_ratio`、`sortino_ratio`、`calmar_ratio`、`win_rate` 等指标需要统一命名、单位和空值语义；API 返回和落库字段必须一致 | Phase 1 API/schema 设计时确认 |
| 报告和图表产物 | 旧 HTML、CSV、PNG/plot 不是新系统真源，默认不迁旧产物；新系统以结构化 run、metrics、trades、equity curve 为真源，前端按需渲染图表 | Phase 1 即遵守，Phase 5 展示 |
| equity curve 存储 | Phase 1 可先用 JSON 存储曲线点以减少表数量；如真实 run 曲线过大，后续拆为 `strategy_lab_equity_curve` 明细表 | Phase 1 设计时确认 |
| 依赖引入 | 当前使用 DSA 内部确定性 engine adapter，并不将 `backtrader` 引入主运行时；旧项目里的 `langchain`、`langchain-openai`、`claude-agent-sdk`、`matplotlib` 不随手迁入。未来如确有必要引入新执行器，需先更新依赖、第三方归因和验证 | 每次新增依赖时确认 |
| 通知和邮件 | 旧项目的实盘信号邮件、QQ SMTP、硬编码收件逻辑不迁入；策略实验室通知统一走 DSA 现有 notification routing / sender 能力 | Phase 4 前确认 |
| 调度 | 不迁入旧项目独立 APScheduler 生命周期；真实数据同步和策略信号调度应接入 DSA 现有 runtime scheduler / 服务启动链路 | Phase 2/Phase 4 前确认 |
| 可转债涨跌归因 runner | `cb_analysis_runner.py`、`cb_analysis_runner_agent.py` 属于可转债涨跌归因/情报分析，不是策略回测最小闭环；实验性的功能，不计划迁移 | 无需迁移 |
| 事件研究 | `backtest/study/event_stat.py` 是研究工具，不属于策略 run 主链路；可在后续作为 `strategy_lab` studies 工具接入，输出结构化结果和文档，不应阻塞 Phase 1 | Phase 6 前确认 |
| 代码规范化 | 旧项目同时支持裸六码和 `code.market` 可转债代码；迁入时必须定义 `market`、`instrument_type`、`symbol`、`exchange`、`canonical_id` 的统一规则，避免 A 股、港股和可转债相互误判 | Phase 1 数据契约设计时确认 |
| 旧运维脚本 | `backfill_redeem_event_from_basic.py` 等一次性脚本不直接迁入；如果需要保留能力，应改造成幂等 data-sync repair action 并加测试 | Phase 2 后确认 |

## 8. 风险点

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| 回测语义混淆 | 当前项目已有模型判断回测 | 新域统一用 `strategy_lab`，API 和页面独立 |
| 第二套持仓系统 | 旧项目有纸面持仓表 | Portfolio 是唯一持仓真源，策略实验室只存关联 |
| 数据库风格冲突 | 旧项目默认 PostgreSQL，当前项目统一 `DatabaseManager` 生命周期 | 不新增 DB manager，表模型接入现有 storage |
| 可转债命名写死 | 后续要支持 A 股、港股 | 通用域不带 CB，可转债 adapter 和专用表允许带 CB |
| 数据源不稳定 | Jisilu、AkShare、opencli 存在网络、限流和适配器风险 | 真实 ETL 放 Phase 2，保留 timeout、日志和降级 |
| 长任务状态漂移 | 批量回测、参数搜索、数据同步、信号生成都可能耗时较长 | 阶段内必须定义 run/batch/task 状态机和恢复语义 |
| 依赖扩散 | 旧项目依赖 backtrader、matplotlib、langchain、claude-agent-sdk 等 | 每个阶段只引入真实需要的最小依赖并补齐文档 |
| 前端框架冲突 | 旧 Web 使用 Ant Design | 前端重做，不引入 AntD |
| 旧结果口径不一致 | 旧历史结果可能来自旧成本、旧数据、旧假设 | Phase 1 不导旧结果，后续如需 importer 必须标 source/version |
| 迁移范围过大 | ETL、回测、信号、持仓、前端相互牵连 | 按阶段推进，每阶段有独立验收命令 |

## 9. 进度记录

| 阶段 | 状态 | 备注 |
| --- | --- | --- |
| Phase 0：方案与迁移文档 | 已完成 | 已记录迁移共识、阶段计划、验证矩阵和退役边界 |
| Phase 1：后端最小闭环 | 已完成 | 已完成核心域、存储、服务、API 和初始测试；全站 OpenAPI 契约仍有仓库既存尾斜杠差异待后续单独收敛 |
| Phase 2：可转债数据 adapter 与数据同步 | 已完成 | AkShare、Jisilu、OpenCLI provider、payload/fixture 入口、超时降级、条款/因子/事件表、同步历史和数据库回测数据 feed 已完成；Jisilu 当前是实时快照源，历史日线仍需 AkShare 或 payload |
| Phase 3：批量回测与参数搜索 | 已完成 | 参数网格、状态汇总、失败重试、显式恢复 running/pending、批次删除、后台执行和可重连 SSE 进度流已完成 |
| Phase 4：Portfolio 统一与策略信号 | 已完成 | 信号只保存 Portfolio 账户/交易关联；确认通过现有 `PortfolioService.record_trade()`，增加账户 active/market 校验和幂等保护；收益口径已写明 |
| Phase 5：前端策略实验室 | 已完成 | `/strategy-lab` 已改为顶部 Tab 布局（策略研究 / 参数搜索 / 数据同步 / 实盘信号），引入 antd 与 ECharts；行情数据页 `/market-data` 独立于左侧菜单入口；按 DSA 设计系统适配暗色主题并补 Web 测试 |
| Phase 6：真实数据回归、文档与旧项目退役 | 已完成 | 已加入同一规范化快照的双低评分、选券和账户不变性回归；真实历史收益按数据快照、schema、撮合和费用口径分别解释，不宣称跨数据库曲线天然一致 |
| 行情数据查询 API | 已完成 | `GET /instruments`、`GET /instruments/{bond_code}`、`GET /instruments/{bond_code}/bars`、`GET /instruments/{bond_code}/events` 已实现并挂载到 `/api/v1/strategy-lab`，配套查询测试通过 |
| 本地初始化同步脚本 | 已完成 | `scripts/sync_cb_local_init.py`（一次性）：从本地 `localhost:5273` 拉取列表/详情/行情/事件入库；行情 OHLCV 共用 `stock_daily`（新增 `instrument_type` 区分可转债/股票），`strategy_lab_cb_basic` 新增 `status`（退市/上市状态）与详情补充元数据（terms_json）；溢价率/剩余规模/条款来源暂无，留空待其他数据源补充 |
| 行情数据页筛选与股票 Tab | 已完成 | `/market-data` 拆为可转债/股票双 Tab；可转债列表支持 `status`（未退市/已退市）与 `held_only`（仅持仓）筛选（`GET /instruments` 新增参数）；股票 Tab 读取 `stock_daily`（新增 `GET /stocks/list`、`GET /stocks/{code}/bars`），有 OHLCV 时渲染 K 线 |
| 数据同步 Tab 迁移 | 已完成 | 策略实验室"数据同步"Tab 移至行情数据页（可转债/股票/数据同步），`DataSyncPanel` 移入 `components/market-data` |

## 10. 后续开发规则

- 每个阶段开始前，先对照本文确认范围，不夹带后续阶段内容。
- 每个阶段完成后，更新本文进度记录。
- 每次用户可见能力、API、配置、报告结构、Web 行为变化，必须同步 `docs/CHANGELOG.md` 和相关专题文档。
- 涉及新配置项时，同步 `.env.example` 和设置帮助。
- 涉及前端页面时，按项目要求提供截图或等价视觉证据。

## 11. Phase 1 已完成内容

- 新增 `strategy_lab` 领域骨架、fixture 双低引擎、运行仓储和服务。
- 新增 `/api/v1/strategy-lab/strategies`、`/runs`、`/runs/{run_id}`、`/runs/{run_id}/trades`。
- 新增 `strategy_lab_runs`、`strategy_lab_run_metrics`、`strategy_lab_trades` 三张表。
- 新增可执行测试覆盖 engine、service、API contract。
- 保持 `daily_stock_analysis` 现有 Portfolio 为唯一持仓真源。

## 12. Phase 2-6 完成内容

- Phase 2：同步数据既可作为结构化 payload，也可由 AkShare、Jisilu 或 OpenCLI provider 获取；provider 的基础信息、条款、溢价率、剩余规模、日因子和强赎/下修等事件写入 `strategy_lab_cb_*`，同步后的数据会被策略 run 自动读取。Jisilu/OpenCLI 没有历史日线时只写入当前快照，历史回测应使用 AkShare 或 payload。
- Phase 3：批次项状态为 `pending -> running -> completed/failed`，`GET /batches/{id}` 返回逐项进度；`retry` 处理失败项，`resume` 将中断遗留的 running/pending 项回收后继续执行；`run_async=true` 时后台执行并通过 `GET /batches/{id}/stream` 提供可重连 SSE 进度，批次支持删除。
- Phase 4：策略回测收益（run metrics）只表示历史模拟结果；纸面/实盘收益必须从 Portfolio snapshot/trades 计算。Strategy Lab 不存第二套 position/holding 表。信号确认校验账户存在、active、市场匹配，并用 `strategy_lab_signal_{id}` 防止重复写账。
- Phase 5：Web 页面提供策略参数、评分模式参数、数据同步来源、运行/交易明细、批次详情、重试/恢复、信号生成和真实 Portfolio 账户确认；现有 `/backtest` 页面保持模型判断回测语义不变。
- Phase 6：新增 `/api/v1/strategy-lab/studies/events`，以同步事件和日因子输出事件日前后交易日收益的结构化结果；不迁移旧 HTML/CSV/PNG 报告，旧结果如需导入必须另建带 source/version 的 importer。

## 13. 旧项目退役清单与口径对比

- [x] 新系统使用独立 `strategy_lab` 域，旧 `/backtest` 继续只表示模型判断回测。
- [x] 策略 run、batch、CB 数据同步、event study、signal 和 Web 入口均有 DSA API/测试落点。
- [x] Portfolio 是唯一持仓真源；旧 `paper_portfolio_position` 不迁入、不再作为运行时依赖。
- [x] 旧策略的 `double_low`、`low_premium`、`weighted_double_low`、`triple_low` 评分公式已在当前 engine contract 中覆盖；费用、目标暴露、手数、剩余规模和事件过滤参数可由 `parameters` 传入。
- [x] 旧项目 `low-premium` 策略入口保留为 Strategy Lab 的独立策略 ID；`double-low` 仍使用双低默认评分。
- [x] 旧项目双低单元测试：`cd /Users/red/Documents/code/trading-backtest/cb_quant && .venv/bin/python -m pytest tests/backtest/test_double_low_strategy.py`，24 passed。
- [x] 当前项目相同评分公式回归：`tests/test_strategy_lab_engine.py`，并通过数据库 fixture -> run -> trades 的闭环测试。
- [x] 已用同一份规范化 CB 快照回归旧双低公式、选券顺序和无费用账户不变性；真实历史收益曲线仍不跨不同数据库/快照直接宣称一致，若未来需要逐点收益一致性，必须再提供同一日线、benchmark、commission、COC 撮合、手数和筛选输入。
- [x] 旧项目的独立数据库连接、独立 APScheduler、HTML/CSV/PNG 报告和邮件硬编码路径不迁入；旧目录可转为只读归档，后续代码维护以 DSA 为唯一入口。

### 收益口径

`StrategyLabRun.metrics.total_return_pct` 是策略引擎按历史价格、费用和参数计算的模拟收益；它不写入 Portfolio。`PortfolioService` 的 trades/positions/snapshots 是纸面或实盘账户收益的唯一计算来源。两者可通过 run/signal/account/trade ID 关联，但不能把策略回测收益直接当成实盘收益。
