# 策略实验室前端重构 + 行情数据页 实施计划

## 1. Summary

对未提交的 `strategy_lab`（策略实验室）功能补齐前端与数据查看能力：

1. **策略实验室页改为顶部 Tab 布局**：策略研究（回测）、参数搜索（批量回测）、数据同步、实盘信号。
2. **左侧菜单新增"行情数据"入口** `/market-data`：展示可转债历史行情（标的列表 + 详情 + 价格/溢价率图表 + 事件）。
3. **后端补可转债查询 API**（现在只有录入 `/data-sync`，没有任何查询接口）。
4. **引入 antd + ECharts**（用户已确认），同时适配现有暗色设计系统。

## 2. Current State Analysis

### 前端
- 路由：`/strategy-lab` 已挂在 [App.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/App.tsx)，页面为单页堆叠的 [StrategyLabPage.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/pages/StrategyLabPage.tsx)（策略回测、参数搜索、数据同步、批次历史、运行记录、策略信号六大区块，无 Tab 划分）。
- 左侧菜单 [SidebarNav.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/components/layout/SidebarNav.tsx) 已有"策略实验室"入口（`FlaskConical` 图标）。
- i18n [uiText.ts](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/i18n/uiText.ts) 已含策略实验室中英文 key。
- 设计系统：Tailwind v4 + 自定义 common 组件（`AppPage`/`PageHeader`/`Card`/`StatCard`/`Select`/`Drawer`/`Pagination` 等），**无 Tab 组件**；依赖已有 `recharts`，**无 antd/echarts**。
- 现有 API 封装 [strategyLab.ts](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/api/strategyLab.ts)，axios client 在 `src/api/index.ts`。
- 测试：vitest + @testing-library/react，已有 [StrategyLabPage.test.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/pages/__tests__/StrategyLabPage.test.tsx)。
- React 版本 **^19.2.0**（antd v5 需 `@ant-design/v5-patch-for-react-19` 兼容补丁）。

### 后端
- 表模型（[storage.py](file:///Users/red/Documents/code/daily_stock_analysis/src/storage.py)）：`strategy_lab_cb_basic`（含 `current_premium_rate`/`convert_price`/`remaining_size`/`terms_json`）、`strategy_lab_cb_terms`（强赎/下修/回售条款）、`strategy_lab_cb_daily_factors`（`close`/`premium_rate`/三个 alert，**只有 close 无 OHLC**）、`strategy_lab_cb_events`（`event_date`/`event_type`/`event_detail`）。
- 仓储 [data_repo.py](file:///Users/red/Documents/code/daily_stock_analysis/src/repositories/strategy_lab/data_repo.py)：只有 upsert 与内部 `load_cb_backtest_rows`/`load_cb_event_study_rows`，**无对外查询方法**。
- API [strategy_lab.py](file:///Users/red/Documents/code/daily_stock_analysis/api/v1/endpoints/strategy_lab.py)：有 `/data-sync`、`/runs`、`/batches`、`/signals`、`/studies/events`，**无 `/instruments` 查询**（迁移文档 §2.7 计划了该路径但未实现）。
- Schema [strategy_lab.py](file:///Users/red/Documents/code/daily_stock_analysis/api/v1/schemas/strategy_lab.py) 无 instrument 查询模型。
- 数据源：AkShare 可拉真实历史日线；Jisilu/OpenCLI 只写当日快照；fixture 有 3 只 × 2 日样例。

## 3. Proposed Changes

### 3.1 后端：可转债数据查询 API（新）

**目标**：为行情数据页提供标的列表、详情（基础+条款）、日线因子、事件四类查询。

**A. 仓储层** [data_repo.py](file:///Users/red/Documents/code/daily_stock_analysis/src/repositories/strategy_lab/data_repo.py) 新增方法：
- `list_cb_instruments(market, keyword, limit, offset)`：查 `strategy_lab_cb_basic`，`keyword` 模糊匹配 `bond_code`/`bond_name`/`stock_code`，返回总数 + 分页行（含 `latest_factor` 最近 close/premium，按 `updated_at` 排序）。
- `get_cb_instrument_detail(bond_code, market)`：基础 + 关联 `strategy_lab_cb_terms`，返回合并字典。
- `list_cb_daily_factors(bond_code, start_date, end_date, limit)`：`close`/`premium_rate`/`remaining_size`/三个 alert，按日期升序。
- `list_cb_events(bond_code, event_type, limit)`：事件按 `event_date` 倒序。

**B. 服务层** [data_sync_service.py](file:///Users/red/Documents/code/daily_stock_analysis/src/services/strategy_lab/data_sync_service.py)：在 `StrategyLabDataSyncService` 上追加 `list_instruments` / `get_instrument_detail` / `list_instrument_bars` / `list_instrument_events` 四个薄封装方法（复用同一 repository，不新建平行 service）。

**C. Schema** [schemas/strategy_lab.py](file:///Users/red/Documents/code/daily_stock_analysis/api/v1/schemas/strategy_lab.py) 新增：
- `StrategyLabInstrumentItem`（基础字段 + latest_close/latest_premium_rate + event_count）
- `StrategyLabInstrumentListResponse`（total/page/limit/items）
- `StrategyLabInstrumentDetailItem`（basic + terms 字段展开 + events 摘要计数）
- `StrategyLabBarItem`（trade_date/close/premium_rate/remaining_size/三个 alert）
- `StrategyLabBarListResponse`（instrument + items）
- `StrategyLabEventItem`（event_date/event_type/event_detail/source）
- `StrategyLabEventListResponse`（instrument + items）

**D. Endpoint** [endpoints/strategy_lab.py](file:///Users/red/Documents/code/daily_stock_analysis/api/v1/endpoints/strategy_lab.py) 新增 4 个 GET（挂载后前缀为 `/api/v1/strategy-lab`）：
- `GET /instruments`（Query：market=cn、keyword、page、limit）
- `GET /instruments/{bond_code}`（404 处理）
- `GET /instruments/{bond_code}/bars`（Query：start_date、end_date、limit）
- `GET /instruments/{bond_code}/events`（Query：event_type、limit）

**E. 测试**：新增 `tests/test_strategy_lab_data_query.py`，覆盖：fixture 入库后列表/详情/日线/事件查询、keyword 过滤、分页、404。

### 3.2 前端：依赖与主题基座

**A. 依赖**（`apps/dsa-web/package.json`）：
- `antd@^5` + `@ant-design/v5-patch-for-react-19`（React 19 兼容补丁，在入口最先引入）。
- `echarts`（ECharts 核心）；**不引入** `echarts-for-react`（维护停滞、React 19 兼容风险），改用一个 ~50 行自定义封装组件（`echarts.init` + `ResizeObserver` + `dispose`），同样满足"ECharts 图表"诉求。

**B. 主题**：在 [App.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/App.tsx) 根部用 `antd ConfigProvider`（`theme.darkAlgorithm` + `zh_CN`/`en_US` locale 跟随 `useUiLanguage`；primary color 取现有 `hsl(var(--primary))` 同色值），整站包一层，antd 组件全局风格一致。

**C. 图表封装**：新增 [components/common/EChart.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/components/common/EChart.tsx)（`option` + `height` props，ResizeObserver 自适应），并 export。

### 3.3 前端：策略实验室 Tab 化

**A. 页面结构**：重写 [StrategyLabPage.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/pages/StrategyLabPage.tsx) 为容器：`PageHeader` + `antd Tabs`（4 个 tab）。保持单路由 `/strategy-lab`，tab 切换用内部 `activeKey` state。

**B. 新增 4 个面板组件**（放 `components/strategy-lab/`，沿用 `components/<domain>/` 现有约定）：
- `StrategyResearchPanel.tsx`（策略研究）：运行回测表单 + 运行列表 + 运行详情。详情含指标卡（antd Descriptions/Statistic）、**ECharts 净值曲线**（`run.equity_curve`）、成交明细（antd Table）。
- `ParameterSearchPanel.tsx`（参数搜索）：批量回测表单 + SSE 进度 + 批次列表/详情 + 重试/恢复/删除。**合并原"批次历史"区块**。
- `DataSyncPanel.tsx`（数据同步）：同步来源表单 + 同步记录列表（antd Table/List）。
- `LiveSignalPanel.tsx`（实盘信号）：信号列表 + 从运行生成信号 + 确认写入 Portfolio 表单。

迁移时从原 186 行单页抽取各区块逻辑与字段契约，视觉改用 antd + 现有 design tokens。

**C. API 客户端** [strategyLab.ts](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/api/strategyLab.ts)：新增 `listInstruments` / `getInstrumentDetail` / `listInstrumentBars` / `listInstrumentEvents` 四个方法 + 对应 TS 类型（复用于行情页）。

**D. 测试**：更新 `pages/__tests__/StrategyLabPage.test.tsx`（渲染 4 个 tab、切 tab、核心请求仍 mock 断言）。

### 3.4 前端：行情数据页（新）

**A. 导航与路由**：
- [SidebarNav.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/components/layout/SidebarNav.tsx)：NAV_ITEMS 加 `{ key: 'market-data', to: '/market-data', icon: CandlestickChart, labelKey: 'layout.nav.marketData' }`（放在策略实验室下方）。
- i18n [uiText.ts](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/i18n/uiText.ts)：新增 `layout.nav.marketData` + `layout.route.marketData.title/description`（中英）。
- [App.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/App.tsx)：加 `lazy(MarketDataPage)` 与 `<Route path="/market-data" .../>`。

**B. 页面** [pages/MarketDataPage.tsx](file:///Users/red/Documents/code/daily_stock_analysis/apps/dsa-web/src/pages/MarketDataPage.tsx)：
- 左侧：标的选择（antd Select 可搜索 / 或 Table），数据来自 `listInstruments`，支持代码/名称关键字过滤。
- 右侧详情（选中后）：
  - 概览区：`getInstrumentDetail` → 转股价、当前溢价率、剩余规模、上市日、到期日、条款（antd Descriptions / StatCard 风格）。
  - 图表区：`listInstrumentBars` → **ECharts 双 Y 轴折线**（收盘价 + 溢价率；因 `strategy_lab_cb_daily_factors` 只有 close，用折线而非 K 线，计划内写明该口径）。
  - 事件区：`listInstrumentEvents` → antd Table（事件日期/类型/详情/来源）。
- 空态：未选标的前展示引导（EmptyState）。

**C. 测试**：新增 `pages/__tests__/MarketDataPage.test.tsx`（mock 查询 API，断言列表加载、选中后图表数据与事件表渲染；ECharts 组件 mock 为轻量 stub 避免 jsdom 兼容问题）。

### 3.5 文档

- [docs/strategy-lab-migration.md](file:///Users/red/Documents/code/daily_stock_analysis/docs/strategy-lab-migration.md)：进度记录补一条"行情数据查询 API + 前端 Tab/行情页"；§2.7 的 `/instruments` 标注为已实现。
- [docs/CHANGELOG.md](file:///Users/red/Documents/code/daily_stock_analysis/docs/CHANGELOG.md) `[Unreleased]` 段扁平格式补：
  - `- [新功能] 策略实验室页面改为顶部 Tab（策略研究/参数搜索/数据同步/实盘信号）`
  - `- [新功能] 新增行情数据页，支持可转债历史行情查看`
  - `- [新功能] 新增可转债数据查询 API（标的/日线/事件/详情）`

## 4. Assumptions & Decisions

1. **antd v5 + React 19 补丁**：`@ant-design/v5-patch-for-react-19` 是官方兼容方案；若安装/构建出问题，退回 `ConfigProvider` 局部包裹策略实验室与行情页（替代整站包裹）。
2. **ECharts 用自定义轻封装**而非 `echarts-for-react`：避免维护停滞库与 React 19 冲突；只新增 `echarts` 一个依赖。
3. **Tab 用单路由 + 内部 state**：不做 `/strategy-lab/research` 等多子路由（用户描述为"页面顶部 tab"，内部切换最简）。
4. **行情图用折线**：daily_factors 只存 `close`，不扩表加 OHLC（K 线属后续扩展，需另改 schema + provider + 同步链路，本次不做）。
5. **批次历史并入"参数搜索" tab**：用户未选"批次历史"独立 tab。
6. **查询 API 挂在 `/api/v1/strategy-lab/instruments...`**：对齐迁移文档 §2.7 既定路径，不另开平行前缀。
7. 后端查询方法加在现有 `StrategyLabDataSyncService` 上（复用 `StrategyLabDataRepository`），不新建平行 service/文件，符合 AGENTS.md"不新增平行实现"。

## 5. Verification Steps

### 后端
```bash
.venv/bin/python -m pytest tests/test_strategy_lab_data_query.py tests/test_strategy_lab_data_sync.py tests/test_strategy_lab_api.py -q
.venv/bin/python -m py_compile api/v1/endpoints/strategy_lab.py api/v1/schemas/strategy_lab.py src/repositories/strategy_lab/data_repo.py src/services/strategy_lab/data_sync_service.py
./scripts/ci_gate.sh
```

### 前端
```bash
cd apps/dsa-web
npm install        # 安装 antd + @ant-design/v5-patch-for-react-19 + echarts
npm run lint
npm run build
npm test
```

### 人工验证
- `npm run dev` 打开 `/strategy-lab`：顶部 4 个 tab 可切换；策略研究跑一次回测看净值曲线（ECharts）+ 成交表；实盘信号生成/确认流程可用。
- 打开 `/market-data`：先 `数据同步`（fixture 或 AkShare）再刷新，能列出标的、点开详情看到价格/溢价率图表与事件表。
- 暗色主题下 antd 组件观感与现有页面一致。

### 交付说明
按 AGENTS.md 默认结构给出：改了什么 / 为什么这么改 / 验证情况 / 未验证项（antd 与 React 19 在真实浏览器行为、ECharts 渲染细节）/ 风险点（antd 引入对整站体积与既有页面的影响、CSS-in-JS 与 Tailwind 冲突、React 19 补丁）/ 回滚方式（依赖与页面改动可单独回退，后端 API 为纯新增）。

## 6. 实施顺序

1. 后端查询 API（仓储 → 服务 → schema → endpoint → 测试）
2. 前端依赖与主题基座（package.json → App.tsx ConfigProvider → EChart 封装）
3. 策略实验室 Tab 化（4 面板 + 容器重写 + API 客户端 + 测试更新）
4. 行情数据页（导航/路由/i18n → 页面 + API 消费 + 测试）
5. 文档更新（migration + CHANGELOG）
6. 全量验证（后端 pytest/ci_gate + 前端 lint/build/test + 浏览器冒烟）
