# 周度持仓复盘（Weekly Portfolio Review）

每周六凌晨自动生成上一自然周（周一~周五）的多账户持仓复盘：落库存档、推送飞书摘要，Web 持仓页「周复盘」标签页查看详情并填写人工区。

## 配置

| 配置项 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `weekly_review_enabled` | `WEEKLY_REVIEW_ENABLED` | `true` | 总开关；关闭后调度不再生成 |
| `weekly_review_time` | `WEEKLY_REVIEW_TIME` | `01:00` | 触发时刻（HH:MM），星期固定为周六，不可配置 |

调度语义：

- 每周六 `weekly_review_time` 时刻在服务进程内触发（runtime scheduler 后台任务），生成**上一自然周**的复盘并推送飞书（标题「周度持仓复盘」）。
- 周六为非交易日，该任务不走交易日门控；幂等由内存状态（ISO 周号）+ 落库表 `(period_start, period_end)` 唯一键双保险。
- 若周六该时刻服务未运行，本周自动跳过；可在 Web 周复盘页点「生成上一周」或调 `POST /api/v1/portfolio/weekly-reviews/generate` 补生成（可指定 `period_start` 回填历史周）。
- 重复生成同一周会覆盖自动区 payload，**不会**清空本周手记与下周计划（人工区）。

## 报告内容

自动区（生成时计算并固化进 `portfolio_weekly_reviews.payload`）：

| Section | 内容 |
| --- | --- |
| `overview` | 各账户+合计的期初/期末权益、周收益（剔除非交易资金流）、现金占比、已实现周增量、距历史高点回撤 |
| `attribution` | 逐标的 flow-adjusted 周盈亏 = 期末市值 − 期初市值 − 买入金额 + 卖出净额；Top 贡献/拖累各 5；周涨跌幅 |
| `realized` | 全量 FIFO 重放的本周平仓明细、自动（QMT 同步成交，note 前缀 `qmt_deal`）/手动拆分 |
| `structure` | 类别/账户/行业分布、top1/top5 集中度、跨账户同标的重叠、未填买入逻辑清单 |
| `position_tiers` | 深套（≤ −20%）/浅套/浮盈分层 + 持仓时长（自最早剩余 lot 起算） |
| `cb_zone` | 持仓转债溢价率/价格带/剩余规模/到期天数/强赎进度原文、周内转债事件、自动策略运行与轮动统计、策略 vs 手动交易对比 |
| `signals_vs_trades` | 当周决策信号与同标的有效窗口内实际成交的对照（数据稀疏时空态展示） |
| `risk_list` | 深套持仓、强赎公告/临期转债（≤90 天）、单票集中度（≥20%）、买入逻辑缺失提醒 |

人工区（人工填写，随复盘存档）：

- **本周手记**：自由文本，复盘时记录做对/做错。
- **下周计划**：动作（add/reduce/clear/watch）+ 标的 + 内容，建议带触发条件；下一期复盘自动带出「上周计划」供勾选 done/skipped。

## API

- `GET /api/v1/portfolio/weekly-reviews` — 分页列表（含 summary 摘要与计划完成计数）
- `GET /api/v1/portfolio/weekly-reviews/{period_start}` — 详情（payload + 人工区 + 上周计划）
- `POST /api/v1/portfolio/weekly-reviews/generate` — 手动生成/回填（body：`period_start` 周内任意一天、`push` 是否推送）
- `PUT /api/v1/portfolio/weekly-reviews/{period_start}/note` — 更新本周手记
- `POST/PUT/DELETE /api/v1/portfolio/weekly-reviews/{period_start}/plans[/{plan_id}]` — 下周计划 CRUD 与状态流转

## 指标口径与数据边界

- **成本法固定 fifo**；`portfolio_daily_snapshots` 的权益/浮盈/已实现均为**累计值**，周指标一律「期末 − 期初」差分；期初 = `period_start` 前最后一份快照。
- **周收益剔除外部资金流**：只把现金流水中 note 不带 `qmt_deal_cash` 前缀的条目视为出入金；QMT 成交的配平结算流水（`qmt_deal_cash:*`）不属于外部流入流出。
- **周涨跌幅**由 `stock_daily` 收盘价计算（`pct_chg` 列对股票/ETF/转债为空，不可直接使用）。
- **逐标的已实现盈亏**按全量成交 FIFO 重放计算；若某账户卖出量超出已记录持仓（历史成交不完整），仅按已匹配部分计算并在 `limitations` 标注。
- **持仓时长**从最早剩余 lot 的 `open_date` 起算；早于等于该账户首笔成交日的 lot 视为记账起点导入（`duration_capped`），时长为下界。
- 转债强赎进度为 `strategy_lab_cb_basic.terms_json` 中的原文文本（如 `0/15 | 30`），不做数值解析。
- 账户在周内才开始有快照时，周收益以首日为基期并在 `limitations` 标注。

## 持有逻辑（buy_logic_text）

持仓元数据在原 `buy_logic` 枚举（dividend/index_tracking/automated/undefined/value）之外，新增 `buy_logic_text` 一句话自由文本（≤500 字符）：

- Web 持仓页「持仓明细 → 编辑」弹窗填写，走既有 `PUT /api/v1/portfolio/position-meta`。
- 周复盘的「未填买入逻辑」清单 = `buy_logic` 为空/undefined 且 `buy_logic_text` 为空的持仓。
- 建议每周复盘时对照该字段检查持有逻辑是否仍成立，逻辑失效的持仓列入下周计划处理。

## 存储

- `portfolio_weekly_reviews`：一期一行，`(period_start, period_end)` 唯一；`payload` 存自动区 JSON，`weekly_note`/`feishu_sent`/`error` 为状态列。
- `portfolio_weekly_review_plans`：计划子表，挂在 review 下，`status` 三态（pending/done/skipped）。
- 新表由 `Base.metadata.create_all` 自动创建；`portfolio_position_meta.buy_logic_text` 通过 storage 迁移列表自动补列，老库升级无需手工操作。
