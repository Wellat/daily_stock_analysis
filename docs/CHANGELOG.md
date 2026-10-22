# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

> For user-friendly release highlights, see the [GitHub Releases](https://github.com/ZhuLinsen/daily_stock_analysis/releases) page.

## [Unreleased]
- [改进] 可转债数据同步完成/失败通知改发飞书群（标题「数据同步」）：摘要式展示盘中/盘后、耗时与各阶段失败数/写入量，不再发送原始 JSON 邮件；开关迁移为 `CB_SYNC_NOTIFY_ENABLED`（默认 true，旧键 `CB_SYNC_NOTIFY_EMAIL_ENABLED` 仍生效，语义从邮件改为飞书）。
- [新功能] 盘后实盘成交汇总推送：每个 A 股交易日 `LIVE_TRADE_SUMMARY_TIME`（默认 15:10）汇总当日实盘成交记录（`portfolio_trades`）按买卖方向直发飞书（标题「成交汇总」，🔴 买入/🟢 卖出逐笔数量+名称+金额 + 合计/净额/费用，多账户逐条标注账户），当日无成交不发送；`LIVE_TRADE_SUMMARY_ENABLED` 总开关（默认 true）。标的名称按 成交行自带 → 订单记录 → 转债基础表 → 实时行情 兜底补全，行情失败降级为仅代码。
- [改进] 飞书消息卡片标题按消息类型区分：ETF 信号通知显示「信号通知」，大盘复盘显示「大盘复盘」，个股/合并报告保持「股票智能分析报告」默认标题；`FeishuSender.send_to_feishu` 新增可选 `title` 参数（webhook 与 App Bot、分片路径全量透传），`NotificationService.send` 新增可选 `feishu_title`/`feishu_content`（仅影响飞书渠道，其他渠道仍发送完整内容）。
- [改进] ETF 信号通知买卖信号表情加强：买入 `🔴📈`、卖出 `🟢📉`、无信号 `⚪️`，表情移到行首并置于结论前，通知列表一眼辨别方向；行情异常/跳过行加 `⚠️` 标识。
- [改进] 大盘复盘飞书推送改为结构化数据驱动的精简版（约缩至原报告 1/4 长度）：LLM 总评摘要 + 盘面信号（评分/操作建议）+ 主要指数行（点位+涨跌箭头）+ 涨跌与成交 + 领涨/领跌板块 Top3 + 明日交易计划要点，全部为列表样式、无 markdown 表格（此前表格被逐行展开成 8 组「键：值」长行，手机排版混乱）；结构化数据缺失时自动回退完整报告，完整报告仍保存至 `reports/` 并可经 Web 历史记录/邮件渠道查看。
- [新功能] 新增 `STOCK_ANALYSIS_ENABLED` 配置（默认 `true`）：设为 `false` 暂停个股分析，定时/立即运行仅执行大盘复盘，显式 `--stocks`/`--portfolio` 临时分析不受影响；暂停期间 `STOCK_LIST` 允许留空（不再报配置错误）。GitHub Actions 每日工作流同步支持该变量（repo variables/secrets 均可）。
- [改进] 实时行情补字段按品种区分：ETF 无 PE/PB/市值字段（任何源都补不上），补充字段收窄为量比/换手率/振幅三项——首个源拿齐即返回，不再对后续源发起注定失败的补充请求（东财接口断连时每只 ETF 行情白等约 30s 重试超时，腾讯成功后仍连环重试东财即此因）；股票的 7 字段补充语义不变。
- [修复] 策略实例删除后被"复活"：实验室 presets 一次性迁移此前按同名同模板去重、旧表不删，用户删除迁移来的实例（如「红利ETF·MA210」）后每次服务重启都会被迁移器重建。现 `strategy_lab_strategy_configs` 增加行级 `migrated_to_instance` 标记——拷贝成功或同名实例已存在（存量库回填）即置位、该行永不再处理；损坏行不置位待数据修复后重试。
- [修复] ETF 实时行情源路由：`AkshareFetcher.get_realtime_quote` 对 ETF 代码无视 `source` 参数、无论配置优先级一律走东财 `fund_etf_spot_em`，导致东财接口断连（如 2026-10-19 起 push2.eastmoney.com 不可达）时腾讯/新浪 fallback 槽位全部打到同一死接口，ETF 信号通知全部「行情不可用，跳过评估」（名称也随之丢失）。现 `source=tencent` 时 ETF 走腾讯直连（qt.gtimg.cn，股票/ETF 同格式，含现价/昨收/涨跌幅/名称），新浪全量接口不覆盖 ETF 维持东财一路；默认优先级腾讯首位，东财恢复前后均不影响取数。
- [改进] ETF 信号策略 `dividend_yield_min` 语义对齐本仓库「0=关闭」口径（与 stop_loss_pct / account_capital 一致）：仅正数阈值激活股息率过滤，0 值不再构成「股息率 > 0」买入条件——此前管理页按模板默认创建的信号实例会因股息率无数据源而被永久堵死买入（如实例 ETF·513630·MA30 每日通知恒为"无信号"）；存量带 0 值默认的实例自动恢复，无需改数据。参数标签同步标注「买入股息率下限（0=关闭）」。
- [改进] ETF 信号通知消息的「参数」行只展示当前规则实际引用的参数（按规则的因子推导：均线规则显示 ma_window、MACD 规则显示 macd_* 三件套、区间涨跌幅规则显示 roc_window、激活的股息率过滤显示阈值），不再罗列与该实例规则无关的字段（如 signal 策略恒显示 roc_window 与 macd_fast/slow/signal 全家桶）。
- [修复] 策略实验室「低溢价轮动」回测结果失真（如 2024-01~2026-10 十持仓回测 -98%：只有买入无卖出、持仓市值恒为 0、现金烧完后停摆）：9 月初（5cb05b2）将 low-premium 特判路由到未完成的 `unified_low_premium_v1` 统一引擎（无持仓跨日传递、无组合估值），本次撤掉该特判，low-premium/double-low 回到 `database_double_low_v1` 引擎（首日打分买入、窗口末平仓的 Phase 1 语义，恢复 8 月前的正常行为）；`unified_engine.py` 代码保留待后续完成。真实轮动语义（换仓频率/调仓/风控）的低溢价回测请用「转债轮动」策略 + 打分预设「低溢价」。2026-09-02 之后已产生的 low-premium 回测记录（engine_name=unified_low_premium_v1）结果不可信，可删除重跑。
- [修复] 策略实验室「保存为策略实例」对信号族策略（signal/signal_macd）报 `unknown strategy parameters: entry_rules, exit_rules`：显式规则 `entry_rules`/`exit_rules` 与 `default_rules` 本就是 SignalParams 双轨参数的一部分（通知评估同一消费方），实例参数白名单现对 notify 模板放行并按 `SignalRule` 结构校验归一（非列表/未知因子/非法比较符/非数值拒绝，order 与仅回测模板仍严格白名单）；策略管理页编辑抽屉保存时不再丢掉未占参数面板的结构化参数键（此前编辑信号实例会静默清掉已保存规则），参数摘要与 Tooltip 对规则列表显示 JSON 而非 `[object Object]`。
- [改进] 策略产品模型重构为「策略模板 / 策略实例」两层：代码注册表（`list_builtin_strategies`）即策略模板（中文名/品种/live_mode/参数定义，UI 文案统一改叫模板），`live_strategy_configs` 概念升级为策略实例表（模板 + 参数 + 标的 + 调度的统一载体，新增 `description` 列），同一模板可建多个实例、允许多个 order 实例同时启用定时实盘（调度器逐实例分派、dedup 按 config_id；同账户并发下单存在资金占用与持仓互扰，建议错开各实例 schedule_time）；`live_mode=None` 模板（rotation/ma-crossover 等）也可保存为实例（仅回测复用，调度器直接跳过、enabled 强制 False）。
- [新功能] 策略实例 CRUD API：`POST /live-strategy/configs`（创建，按模板 live_mode 分支校验——order 需 QMT 账户与调仓/风控字段、notify 需非空标的、仅回测模板只校验参数白名单）、`PUT /configs/{id}`（更新，创建后不可换模板）、`DELETE /configs/{id}`（删除，历史运行记录保留）、`POST /configs/{id}/run`（手动立即运行：order→实盘下单/notify→信号通知/仅回测→400）；`GET /configs` 列表新增 `description` 与 `last_run`（每实例最近一次运行）；旧 `PUT /configs/notify` 与 `POST /configs/{id}/notify/run` 下线（校验并入通用分支）；`GET/PUT /config`（单数）保留为兼容层（固定作用于最新 order 实例、允许 order 模板间切换），`POST /runs` 与 `/runs/preview` 新增可选 `config_id` 按实例运行。
- [改进] 策略管理页改版为实例列表为主体：顶部「新建策略」入口（选模板→实例名/备注/标的/元数据驱动参数→调度区：自动运行开关+时刻+order 实例的 QMT 账户）与实例表格（模板/品种/能力/运行状态/调度时刻/最近运行/编辑/复制/删除/立即运行——order 实例带真实下单二次确认；仅回测实例提供「去回测」跳转），策略模板总览折叠在页面下方；汇总卡改实例口径（实例总数/实盘运行中/通知运行中/未启用/模板数）。
- [改进] 策略实验室与策略实例打通：策略下拉改叫「策略模板」，「加载已保存配置」改为「加载策略实例」（选实例回填模板/参数/标的），「保存配置」改为「保存为策略实例」（新建为主、已加载实例时可「用当前参数更新实例」）；从实例发起的回测携带 `instance_id` 血缘（`strategy_lab_runs` 新增可空列并建索引，列表与详情回带实例名，实例删除后血缘仅留 id 不报错）；支持 `/strategy-lab?instance=<id>` 预填；实盘交易页配置卡新增「实盘实例」选择器（多 order 实例切换编辑/预览/下单，按实例保存，实例模板创建后不可换）。
- [改进] 实验室旧「保存配置」presets 机制下线：存量 presets 在启动时一次性拷贝为未启用策略实例（copy 不删、同名同模板幂等跳过、JSON 损坏行保留旧表仅告警），`strategy_lab_strategy_configs` 表与数据保留为只读历史；`/strategy-lab/configs` 四个端点与 `config_repo` 移除，前端改走策略实例 API。
- [修复] 持仓页默认账户解析：多实盘实例并存时优先取已启用（enabled）order 实例的 QMT 账户，其次最新 order 行（此前只按最新行，暂停旧实例换新实例时可能回落到未启用账户）。
- [新功能] ETF 信号实盘通知（`live_mode=notify` 首个落地，signal/signal_macd 策略）：交易日按各行 `schedule_time`（如 14:25）自动评估配置标的的买卖信号并直发飞书群机器人（复用 `FEISHU_WEBHOOK_URL`，不走路由/降噪），只通知、不下单，每日汇总必发（现价/涨跌幅/关键因子/信号结论/持仓状态，兼做链路心跳）。当日 bar 由实时行情折算成后复权口径拼接（优先 `现价/pre_close`，缺省用涨跌幅；两者皆缺或库内历史过期则该标的标注跳过，不产出错误信号）；信号评估复用策略实验室 signal_v1 引擎的因子与规则（同一语义，signal_macd 服务端自动注入金叉/死叉默认规则），股息率因子因无数据源不参与；持仓状态从历史信号决策推导（不落新状态表，决策仅在通知发送成功后落库）。多实盘配置：`live_strategy_configs` 走多行（order/notify 按 `live_mode` 分派，当日幂等 key 含 config_id），新增 `GET /configs`；策略管理页提供信号通知实例的创建/编辑/立即运行入口，运行记录新增 `notify` 模式标签。
- [改进] 实盘策略交易日自动运行触发时刻可配置：`live_strategy_configs` 新增 `schedule_time`（HH:MM，默认 14:35 与原写死值一致），runtime scheduler 每 30s tick 读取配置比对（改完即生效，无需重启），非法时刻保存时报错；「策略管理」页与实盘页策略配置均提供入口；env 总开关 `LIVE_STRATEGY_SCHEDULE_ENABLED` 补入 `.env.example`（此前未记录）。
- [新功能] 可转债止损（实盘与策略实验室 rotation 回测同步生效，参数键一致）：持仓按成本价（实盘 QMT `open_price`、回测含加仓摊薄的持仓均价）亏损达到 `stop_loss_pct`%（0=关闭、默认关）时全量强制卖出（决策 `reason=stop_loss`，实盘为服务层 overlay、每次运行都检查并覆盖策略决策，最后交易日强退优先、对账补卖去重）；止损日起 `stop_loss_cooldown_days`（默认 10）个交易日内该标的不再入选候选——实盘冷却从历史止损决策记录加交易日历推导（不落新表），回测按 bar 序号；冷却动机是低溢价策略「价格跌→溢价更低→排名更靠前→下次换仓买回」的高卖低买往返损耗。回测表单新增「止损」区块。2023-01~2026-10 低溢价轮动（10 持仓/10 日换仓/价格分档）实测：不止损 +48.9%/回撤 -29.8%，3% 止损 +10.8%/-35.8%（过紧、频繁 churn），8% +44.4%/-22.1%，10% +47.3%/-23.6%（收益近乎无损、回撤收窄 6~8 个百分点），冷却 5 日显著劣于 10/20 日。
- [修复] 策略实验室指标 `annualized_return_pct` 对总收益 ≤ -100% 的曲线（相对超额等可去负的序列）直接对负底数取分数次幂产生复数、后续 `round` 崩溃：现返回 None（几何年化在该区间无定义）。
- [改进] 价格分档买入保底一手（实盘与 rotation 回测同步生效）：分档缩放后单债金额/张数不足一手时按一手买入（如 >250 元档 20% 在单债资金 1 万时仅 2000 元、买不进 250 元以上券的一手，此前整笔以 no_available_quantity 跳过），回测按含佣金的单张成本口径折算一手；单只权重/仓位上限仍优先封顶，上限不足一手时照旧跳过；金额→张数换算加浮点容差，恰好一手金额（如 12.82 元 × 10 张 = 128.2 元）不再被浮点除法截成 0 手。
- [新功能] 策略实验室新增 ETF 信号·MACD 策略（`signal_macd`，复用 `signal_v1` 引擎与时序因子注册表）：新增 `macd_dif`/`macd_dea`/`macd_hist`/`macd_cross` 四个时序因子（`macd_cross` 为事件因子——金叉日 +1/死叉日 -1/其余 0），默认规则「金叉买入/死叉卖出」，参数 `macd_fast/macd_slow/macd_signal`（默认 12/26/9）均可作批量网格轴，预热按 (slow+signal)×2 交易日估计；规则可组合零轴过滤（`macd_dif > 0`）与柱状线等因子。Web 信号表单按策略重置默认规则、窗口输入按规则引用的因子条件显示，参数搜索新增 MACD 慢线/快线/信号线候选轴。510880 实测（2018~2026，长持基准 +61.1%）：默认 MACD(12,26,9) +52.4% 跑输长持（155 笔、胜率 42.9%），21 组参数网格最优 MACD(12,34,5) +93.6%（回撤 -12.6%、夏普 0.73），同期 MA210 为 +210.2%（夏普 1.14）——红利类低波动资产上 MACD 的高频信号本身即收益损耗，结论详见 docs/strategy-lab-migration.md §15.5。
- [新功能] 策略实验室新增 ETF 信号策略回测（`signal` 策略，instrument_type=etf）：时序进出场规则引擎 `signal_v1`（每标的独立状态机，买入=全部规则成立、卖出=任一成立；规则为「因子/比较符/值」行，内置均线乖离率/区间涨跌幅/收盘价/股息率四个时序因子，`ma_window` 参数化默认 182）；执行语义与轮动引擎对齐（收盘价成交、双边佣金、整手 100 股、权益/N 预算、期末不强平），指标与基准口径同源（等权池/沪深300/指定ETF买入持有/长持·买入持有——后者为回测首日等权买入策略标的、固定份额持有到期末的期初份额模式，区别于等权池的每日再平衡，理想化无费用）。数据层新增 `strategy_lab_etf_daily` 表与 `etf_daily` 同步（主源 baostock 后复权 2016 年起全深度，腾讯 qfq 仅冷启动兜底；`dividend_yield` 列预留、当前无数据源，引用该因子的规则不触发并在诊断展示覆盖率）。参数双轨（扁平标量供网格寻优 + 显式规则列表优先），参数搜索新增均线窗口×卖出乖离率网格轴；Web 新增信号参数表单、ETF 日线同步入口，instrument_type 改由策略元数据派生。首个用例 510880 红利ETF（2024-01~2026-10，MA182 入<0 出>7）+54.3% vs 持有 +32.5%。
- [新功能] 持仓页持仓维度标签与布局改版：持仓明细新增行业、分类（普通股票/ETF/场外基金/可转债，按代码与转债主表自动推导，可人工覆盖）、买入逻辑（得股息/跟踪指数/自动化/价值/未定义，QMT 同步账户默认自动化，可人工覆盖）、备注四列，行内「编辑」弹窗支持人工调整（新表 `portfolio_position_meta` 按（账户,代码）存储，行业按代码全局共享，`PUT /api/v1/portfolio/position-meta`）；持仓明细支持行业/分类/买入逻辑三个下拉筛选（多条件叠加、显示筛选计数并可一键清除，纯前端过滤快照数据）；持仓列表独占一行，行业/分类/买入逻辑三个市值饼图合并一行展示（行业饼图改为按持仓列表行业聚合，空行业归「未分类」，扇区内标注占比——≥4% 显示，悬浮提示同时显示金额与占比）；新增 `POST /api/v1/portfolio/position-meta/autofill` 自动补全空行业（普通股票直接拉板块、可转债按正股，只填空不覆盖人工值，页面首次加载与「自动补全行业」按钮触发）。
- [新功能] 场外基金持仓估值：Portfolio 持仓中 `.OF` 场外基金接入盘后估值链路——`portfolio_holdings` 同步（每日 20:00 `cb_after_close` 定时链路与手动触发同路径）对 `.OF` 代码改走新增 `FundNavFetcher`（akshare 东财「单位净值走势」），单位净值写入 `stock_daily`（`instrument_type='fund'`，close=单位净值），持仓快照按既有 `history_close` 路径完成现价/市值/盈亏估值；该同步的 `symbols` 过滤兼容裸码与带 `.SH/.SZ/.OF` 后缀形态（此前带后缀输入恒不命中）；新增 `scripts/sync_fund_nav.py` 手动补数入口（自动发现全部 `.OF` 代码增量同步，支持 `--symbols`/`--start-date`/`--full`）；基金展示名称优先取交易流水 `symbol_name`。
- [新功能] 实盘-持仓改版为可转债专注视图（`GET /api/v1/trading/positions/cb`，可选 account、缺省取实盘策略配置账户）：QMT 持仓 × 最新因子 × 条款聚合，仅展示该账户可转债（非转债行计数提示未展示）；现价/成本价 3 位小数，汇总条含账户/持仓只数/合计市值/浮动盈亏，行内新增正股名称、转股溢价率（负值绿）、占比、按成本价浮动盈亏（盈红亏绿）、剩余年限、强赎/下修/回售倒计、评级列，最后交易日附剩余交易日倒计；条款倒计文本压缩为 "N/M" 短形态（已公告强赎保留全文红标）；关键日期分级突显——已公告强赎红、条款计数达 60% 橙、最后交易日剩余 ≤5 红/≤15 橙、剩余年限 <0.5 年橙；条款 Tag 与最后交易日兜底等展示约定抽取为 `cbIndicators` 共享模块（行情可转债列表同步复用，计数达 60% 也橙色提示）；`_remaining_trading_days` 真源收敛到 `src/core/trading_calendar.py`。
- [新功能] 价格分档仓位（实盘与策略实验室 rotation 回测同时生效）：按买入价格分档缩放单债额度——默认 ≤165 全额、(165,185] 80%、(185,220] 60%、(220,250] 40%、>250 20%，通过 8 个参数（`price_tier1_max`~`price_tier4_max`、`price_tier2_pct`~`price_tier5_pct`）配置，实盘与回测参数键一致、共用 `src/core/strategies/sizing.py` 同一语义；计算顺序为先分档缩放再被单只权重/仓位上限封顶，金额与固定张数口径都缩放；默认启用（历史保存配置重跑会默认带上分档），一档上限填 0 整体关闭、中间档上界填 0 其后档位不启用；回测表单新增「价格分档仓位」区块。
- [修复] 可转债补数同步（`cb_premium_history`）解析 opencli `remainingSize` 字段：上游字段名不在解析器候选（`remain_size`/`remainSize`/`剩余规模`）内被静默丢弃，剩余规模历史回填始终为空；溢价率解析不受影响。
- [改进] 数据同步（cb_basic/cb_ohlc/cb_premium_history/cb_stock_ohlc/portfolio_holdings）传入 `symbols` 过滤后无可同步标的时，sync run result 写入 `message` 说明（如退市券需 `include_delisted=true`），避免全零计数静默"成功"空跑。
- [修复] 可转债基础表状态归位：按 `terms_json.delist_date` 将 732 只 akshare 来源 `status=NULL` 的退市转债归位为 `delisted`（无 `delist_date` 的 6 只保持 NULL）；此前这批券不在 active 也不在 delisted 过滤集合内，行情数据页状态筛选不可见、同步链路默认不可遍历。
- [修复] 策略实验室 rotation 回测新增到期/摘牌终局处理：终止交易日前一交易日（T-1）收盘强制平仓持有标的（对齐实盘「T-1 提前平仓」规则；终止日按公告 `last_trading_date` > `min(到期日, 摘牌日-1自然日)` 推导），行情断更的持仓按最后可得收盘价兜底强平（`delisted_exit`）；T-1 起该标的不再新开仓、断更后的前向填充 bar 不参与打分与买入（v2 loader 行新增 `delist_date` 字段，纯追加）。修复已到期/摘牌转债被按冻结价永久持仓、因子冻结霸榜导致净值曲线后段完全走平的问题（此前低溢价回测 2026-06-24 起 70 个交易日净值/持仓/换手全零）。
- [新功能] 实盘低溢价/双低策略新增三个参数：`exclude_st`（排除正股 ST，按 `strategy_lab_cb_basic.stock_name` 含 ST/*ST 判定，候选不入选）、`account_capital` + `max_position_pct`（单只最大权重上限，新建仓金额封顶于资金基准×权重%，任一为 0 不启用；只约束新开仓，存量持仓漂移不修剪）。
- [改进] 实盘「排除风险事件」开关语义收敛为同时控制买入候选排除与持仓事件退出：此前 event_check 模式对带强赎/下修/回售提醒的持仓无条件清仓，不受开关控制；关闭开关后买卖两侧均不响应风险事件提醒（最后交易日强退在服务层，不受影响）。
- [修复] 实盘策略配置保存增加参数键白名单校验并归一布尔值：未知参数键（含键名形态漂移，如驼峰 `excludeEventBlocked`）直接拒绝保存，不再静默绕过默认值合并失效；布尔参数兼容 0/1 与 "true"/"false" 提交形态。此前实盘库中 `excludeEventBlocked: 0` 为死键，实际仍按默认 True 排除风险事件。
- [改进] 实盘订单计划的买入硬顶对齐策略意图：`ExecutionPlanner.plan` 支持透传 `cash`/`max_buy_symbols`/`max_buy_amount_each_symbol`，实盘按 `max_positions` 与买入决策声明的单只金额抬高默认硬顶（5 只、1 万元/只），单批买入预算以 `account_capital` 对齐（未配置时保持默认 5 万），避免更大目标持仓被硬顶悄悄截断或第 N 只起被 `insufficient_cash` 跳单；回测与其他调用方默认行为不变。
- [改进] 实盘 Web 参数面板布尔参数渲染为开关（Switch）并按布尔值提交，替代原先一律数字输入框（填 0/1）的形态，消除键值形态漂移温床。
- [改进] 策略实验室「运行记录」列表改版为分页表格（默认 5 条/页，可切 5/10/20，显示总数）：新增回测区间、总收益率、基准收益、最大回撤、夏普、参数摘要（预设·持仓数·换仓频率）、创建时间与状态等关键列，负值绿色正值红色，点击行查看详情并高亮选中；`GET /runs` 列表项新增紧凑 `metrics` 与 `parameters` 字段（不含 diagnostics，详情接口仍返回完整指标，纯追加）。
- [新功能] 因子框架支持组合因子并新增「双低」因子：`FactorSpec` 支持 `columns` 数据依赖 + `compute` 派生取值（metadata 标注 `kind=composite`，可用于打分、排除规则与百分位变换）；内置 `double_low = 转债价格 + 转股溢价率×100%`（本库溢价率按百分数存储，即价格+溢价率，与双低预设完全等价，已用逐笔一致的回测等价性测试锁定）；溢价率缺失时组合因子整值缺失（遵循 skip/neutral 策略）。
- [新功能] 策略实验室重构可转债回测（对标禄得网）：新增 backtrader 轮动引擎 `rotation`（按交易日/周/月 N 周期换仓、等金额权重、持有数量区间、单标的仓位上限、收盘成交、单边佣金、整手取整、换仓日再平衡开关），配套通用横截面因子框架（打分因子表 factor/方向/权重、排除因子表 factor/比较符/值、percentile/zscore/minmax 变换、双低/低溢价/加权双低/三低预设）；指标扩展为 12 列（索提诺/卡玛/日均换手/交易周期/盈亏周期/累计资产）并新增基准对比（标的池等权默认 / 沪深300 指数日线新表 / 指定转债）与相对超额行；结果页新增三行汇总对比表、双轴走势图（累计收益+回撤、线性/对数）、年/月/周回报分布、逐日持仓明细与导出报告（Markdown/持仓 CSV/成交 CSV）；全部结果从同一 equity/benchmark 曲线派生，口径一致。
- [新功能] 策略实验室新增回测配置保存/加载：`strategy_lab_strategy_configs` 表 + `GET/POST /api/v1/strategy-lab/configs`、`PUT/DELETE /configs/{id}`，Web 端可命名保存当前参数组合并复用；run 创建时仍全量快照参数保证自包含可复现。
- [新功能] 数据同步新增指数日线（`sync_type=index_daily`，`symbols` 传指数代码、缺省 000300，数据同步页新增「指数日线·回测基准」入口）落 `strategy_lab_index_daily` 新表，供回测基准使用；主源 akshare 失败时自动降级腾讯 K 线（约 800 根上限）；`GET /runs/{run_id}/report?format=md|holdings-csv|trades-csv` 导出回测报告。
- [改进] 策略实验室移除「导入实盘配置」入口（重构决策：研究域与实盘域解耦，前端不再依赖 liveStrategy API）；旧 3 个策略（double-low/low-premium/ma-crossover）与历史 run 保持可用，equity 曲线与指标扩展字段全部为可选追加，旧客户端无感。
- [修复] `eastmoney_patch` 的 UserAgent 改为惰性初始化：此前模块导入即构造 `UserAgent()`，在 Python 3.13 + fake-useragent 2.2.0 组合上因 importlib.resources 命名空间扫描死锁，拖挂所有引用 data_provider 的进程与测试（本地复现 `import src.services.strategy_lab.service` 无限挂起）；现仅在实际命中东财域名请求时构造，失败降级固定 UA。
- [新功能] 实盘-交易记录新增「取消」按钮：`pending` / `submitted` 状态订单可在 Web 端手动取消回终态（`POST /api/v1/trading/orders/{id}/cancel` 放开 `submitted`）；用于 QMT 认领后挂单未成交、又不会有终态回调的滞留单兜底（如 2026-09-28 111015 卖出单卡在 submitted、成交价/量一直为空）。该操作仅改写系统记录，不撤销 QMT 侧委托，确认弹窗与文档均已注明。
- [改进] 可转债列表详情改为行内展开：点击行在该行下方直接展开详情（再点收起，同一时间仅展开一行），替代原「列表底部详情区」的脱节交互；表格支持点击表头排序（转股溢价率 / 双低 / 最后交易日，默认溢价率升序，取消排序回到默认），`GET /instruments` 新增 `sort_by`/`sort_order` 参数（服务端全集排序后分页，排序值缺失的排最后）；最后交易日缺失时用到期时间兜底展示（弱色区分）并作为排序兜底键。
- [改进] 行情数据-可转债列表改版为信息表格（服务端分页 50 只/页，active 默认溢价率升序）：列表新增双低、强赎/下修/回售计数（已公告强赎红色标记）、剩余规模、剩余年限、到期时间、最后交易日、评级等列；点击行在表格下方展开详情（原有两张趋势图与事件表保留，详情补充条款计数、评级、转股价值等字段）；`GET /instruments` 列表项新增 terms 扁平化字段（纯追加，旧客户端无感）。
- [改进] 行情数据-可转债列表（未退市）按最新转股溢价率升序排序，溢价率缺失或无因子数据的排最后，低溢价标的优先展示（`GET /api/v1/strategy-lab/instruments?status=active`）；全部/已退市视图保持原有更新时间倒序。
- [新功能] 行情数据-可转债详情新增「正股与溢价率」趋势图：正股收盘价（左轴）与转股溢价率（右轴）按交易日对齐，正股价按因子日期窗口实时拉取腾讯前复权日 K（与盘中因子同步同源，单次约 800 根上限），拉取失败或无正股代码时降级为提示、不影响现有图表；新增 `GET /api/v1/strategy-lab/instruments/{bond_code}/stock-bars`。
- [修复] cb_basic 同步不再用空值覆盖库内已有 `convert_price`/`current_premium_rate`：上游 opencli 详情载荷缺字段时保留原值（与 stock_code/remaining_size 同语义），有新值（转股价下修）仍正常覆盖。2026-09-24 曾因详情载荷缺转股价把 24 只活跃转债转股价清空，次日溢价率整批失效、实盘低溢价策略从残缺候选选债并卖出全部低溢价持仓。
- [新功能] 实盘调仓新增溢价率覆盖率门禁：当日 active 转债溢价率缺失占比超过 5%（覆盖率 < 95%）视为因子数据被污染/未就绪，真实运行抛错阻断下单，preview 返回 `skip_reason=premium_coverage_low` 并附 coverage/missing/total（前端运行记录同步展示中文标签）；event_check 模式不依赖溢价率、不受限。
- [改进] 日线 K 线数据源默认优先级对调：AkshareFetcher 提为主源（Priority 0），EfinanceFetcher 降为兜底（Priority 1）。本机历史诊断显示 efinance 日线（Eastmoney push 接口）10/10 全部失败、akshare 10/10 成功，原顺序每次都白耗一次失败尝试；环境变量 `AKSHARE_PRIORITY`/`EFINANCE_PRIORITY` 覆盖机制不变，配置了 `TUSHARE_TOKEN` 时 Tushare 仍动态提权为绝对最高（Priority -1）。
- [改进] 移除策略实验室「实盘信号」Tab 及其专用接口（`POST/GET /api/v1/strategy-lab/signals`、`POST /signals/{id}/confirm`）：与实盘页的交易指令/策略看板能力重复；后端 signal service/repo、`strategy_lab_signals` 存储模型与前端 LiveSignalPanel 一并移除（表内无数据；存量库中的空表保留无害）。
- [新功能] 低溢价/双低策略新增 `per_position_quantity`（单债固定张数）参数：>0 时买入决策按固定张数发单（整手取整），=0 沿用原「单债目标资金」金额口径；用于固定金额 vs 固定张数两种仓位口径的对比研究与实盘配置。
- [改进] QMT 回调 `POST /api/v1/trading/qmt/orders/{id}/callback` 被 400 拒绝时记录 warning 日志（含订单 id、回调状态、QMT 订单号、成交数量/价格与拒绝原因）：此前拒绝原因只出现在 HTTP 响应体，服务端日志仅有 access 日志的状态码，事后无法定位（如实盘部分成交 `filled_quantity != quantity` 被拒、订单卡在 submitted）。
- [新功能] 策略实验室新增「低溢价跟踪」Tab：基于 `strategy_lab_cb_daily_factors` 每个交易日取转股溢价率最低 N 只（默认 10，可切 5/10/20），可视化榜单成员稳定性——在榜分布散点图（每只上榜转债的在榜日期，按在榜天数排序）、稳定性曲线（与前日重叠/新进/退出只数 + 第 N 名门槛溢价率双轴）、上榜统计表（在榜天数/占比/首末上榜/均溢价/最好名次）；新增 `GET /api/v1/strategy-lab/cb/premium-track`（窗口函数按日排名，默认近 90 天，最大 750 天）。
- [改进] `GET /api/v1/trading/qmt/pending` 默认只返回当天创建的 pending 指令（隔日遗留单是历史交易日的计划，今天执行反而错账）；新增 `include_all=true` 查询参数兜底返回全部，QMT 侧对接文档已同步。
- [新功能] 实盘新增「最后交易日风控」：持仓转债剩余交易日（含当日）≤ 提前量+1（提前量默认 1 个交易日，可配）时，无论调仓还是事件检查模式都强制全量卖出（`reason=last_trading_day_exit`，覆盖策略决策并与对账补卖去重）；剩余交易日 ≤ 提前量+3 的标的禁止新建仓。数据基于 cb_basic `terms_json.last_trading_date`（已公告强赎与临近到期停止交易），修复强赎/到期拖到停止交易后持仓被 active 过滤隐身、永远无法卖出的问题；新增配置 `last_trading_day_exit_enabled`（默认开）与 `last_trading_day_exit_buffer_days`，Web 策略配置页补开关与提前量输入。
- [修复] 可转债强赎事件词表统一为 `strong_redeem`：opencli cb_event_list 原样落库的 `force_redemption` 现在入库前归一化，存量行在下一次事件写入时幂等迁移，修复因子计算 `redeem_alert`（判断 `strong_redeem`）对 600 余条存量强赎事件永远为 False、前端事件标签不着色的问题。
- [新功能] 可转债强赎事件兜底：实测部分已公告强赎的转债（如 123258/123112）cb_event_list 中没有强赎事件，现按 cb-detail `force_redeem_countdown`「已公告…强赎」状态自动合成 `strong_redeem` 事件（事件日期取最后交易日并在详情中标注兜底来源与最后转股日；计数中/暂不强赎不触发），保证事件记录、`redeem_alert` 因子与事件研究可见该状态。
- [新功能] 持仓页新增「趋势」Tab：基于每日快照展示总市值与总收益（已实现+浮动三线）的交易日曲线，支持单账户与全部账户汇总、近7/30/90天与自定义范围；每个 A 股交易日一份快照（`portfolio_daily_snapshots`），新增 `GET /api/v1/portfolio/trend`（缺失日期按需回补，上限 400 账户日，账本回溯修改自动失效自愈）与盘后定时任务 `PORTFOLIO_SNAPSHOT_TIME`（默认 20:05，晚于行情同步，fifo/avg 双口径）。
- [改进] 持仓页 KPI 卡片调整：移除「汇率状态」卡（含手动刷新汇率入口与结果提示），新增「持仓盈亏」卡——展示已实现+浮动总盈亏（按基准币种）及相对当前持仓总成本的盈亏比例，红绿着色；`/portfolio/fx/refresh` 接口保持不变，仅移除页面入口。
- [新功能] 行情数据-数据同步新增「持仓行情·A股/ETF/港股」（`sync_type=portfolio_holdings`）：标的自 Portfolio 持仓重放自动展开（活跃账户、非零数量），转债持仓跳过（由 cb_ohlc 覆盖），A 股落 `stock_daily`（instrument_type=stock）、ETF 支持 159/513/589 等代码（instrument_type=etf）、港股 `HK` 前缀代码走腾讯 hk 前缀 K 线（instrument_type=hk_stock，港币原币收盘价），增量起点复用本地历史（无历史回溯至 2025-01-01），`symbols` 可过滤；正股抓取器市场前缀映射抽为独立函数并扩展支持沪/深 ETF 与港股。同步后持仓页现价/市值即可对股票、ETF 与港股估值；已纳入盘后调度链路（每日 20:00 `cb_after_close_sync_time` 定时任务与 `cb_scheduled` 手动触发，盘中链路不变）。
- [改进] 持仓明细标的列展示「中文名称（代码）」：`/portfolio/snapshot` 持仓行新增 `symbol_name` 字段，名称取本地离线数据（转债主表 `strategy_lab_cb_basic` 优先，QMT 上报持仓名兜底），查不到回退仅展示代码；Web 端同步渲染。
- [改进] 持仓页删除账户增加二次确认：第一层确认后弹出最终确认弹窗（明确提示页面无恢复入口），两次确认后才执行删除，降低误删风险。
- [新功能] 持仓管理接入 QMT 当日成交上报：新增 `POST /api/v1/trading/qmt/deals`（`X-QMT-Token` 鉴权），QMT 收盘后上报当日成交，服务端校验、日志留档并逐笔自动入账 Portfolio 账本（按资金账号同名自动匹配/新建账户），持仓、成本、已实现/浮动盈亏随快照查询自动更新；按 `account + trade_id` 幂等，重发安全；`unknown` 方向跳过、失败笔逐笔回报不阻断整批；每笔入账配平现金使总权益=市值。与「QMT 持仓同步」互斥使用（文档已注明）。
- [新功能] 持仓页新增「QMT 持仓同步」：把实盘页 QMT 每日收盘上报的持仓快照按差额同步进 Portfolio 账本（新增/加仓 → 买入，减仓/清仓 → 卖出），新增 `POST /portfolio/imports/qmt/sync`（支持 `dry_run` 预演）；目标账户默认按资金账号同名自动匹配/新建；diff 基线只统计同步自身写入的交易，手工录入永不被覆盖；每笔事件自动配平现金使总权益=市值。仅手动触发，QMT 上报链路零改动。
- [新功能] Web 实盘新增【策略看板】Tab：基于交易记录中的已成交订单统计选定时间范围内的策略收益——成交笔数/买入卖出金额、FIFO 已实现盈亏、盈利亏损笔数与胜率、累计收益曲线（叠加每日盈亏柱）与分标的盈亏明细；新增 `GET /api/v1/trading/dashboard?start=&end=` 聚合端点，无买入记录的卖出单独统计不计盈亏。
- [修复] 接通实盘配置项 `event_check_enabled`（非调仓日事件检查总闸）：此前该配置只有读写链路、无任何运行时消费，设置与否行为不变。现在 `event_check_enabled=false` 时 auto 模式在非调仓日直接返回 `skip_reason=event_check_disabled`（不落运行记录），显式指定 `mode=event_check` 与调仓日不受影响；Web 策略配置页补上开关。
- [改进] 实盘【调仓预览】明细增强：标的展示“名称（代码）”、方向以 Tag 呈现（买入/卖出）、新增溢价率列；`rebalance` 载荷补 `symbol_name`/`premium_rate` 展示字段（预览与运行结果共用，来源为策略上下文与当日因子快照，缺失回退空）。
- [改进] 策略实验室回测成交明细的标的列展示“名称（代码）”：成交记录仅落代码，`/runs/{id}/trades` 现按转债主表补齐 `symbol_name`（缺失时回退仅代码），Web 成交明细与当日买入汇总同步展示。
- [新功能] 策略实验室-策略研究支持“导入实盘配置”与单日运行：一键复用实盘策略/自选池/参数跑回测（初始资金按 单债目标资金 × 最大持仓 映射，与实盘每仓资金语义一致）；新增“单日运行”模式（开始=结束）基于该日已同步行情复算策略当日目标组合；运行详情展示运行区间与当日买入汇总，并在请求区间无已同步行情（退化为内置样本数据）时给出醒目警示，避免把样本数据当真实回测结论。
- [改进] 实盘策略运行全链路日志：mode 解析（锚点/下次调仓日/是否到期）、evaluate 决策输出（按动作分组）、plan 计划输出（订单/跳过原因/风控检查）、execute 下单结果（含幂等复用旧单的订单号）、run 完成与失败（异常堆栈），均带 `[LiveStrategy]` 前缀便于检索。
- [改进] Web 实盘运行详情按执行管线重构为三段视图：目标组合（策略选债：标的/最新价/溢价率/排名）→ 计划结果（调仓计划 + 被跳过的决策，动作含“跳过”标记，可对账“选了 N 只 → 计划 M 单”的数量差）→ 订单执行（成交回报）；事件检查模式下目标组合位置替换为持仓事件扫描（持有/事件退出/受阻），概要新增单债目标资金。
- [修复] 实盘订单幂等键增加 run 作用域（`{run_id}:{symbol}:{action}:{qty}`）：此前键为裸 `symbol:action:qty`，跨日轮动选中同一标的同一数量会误命中前一日旧单（可能已 rejected），当日订单静默丢失且 run 仍标记 completed；同 run 当日重试仍保持去重。`create_order` 命中幂等键复用旧单时现在会记录日志并在返回值标记 `reused`，不再无感。
- [修复] 实盘策略决策记录 `symbol` 落库恒为空：`StrategyDecisionRepository.create` 将 symbol 声明为自身参数却未传给模型，被静默丢弃（历史运行记录决策代码列全空）；同时补齐对账卖出/事件检查决策与卖出订单的 `symbol_name`（service 统一从策略上下文填充），Web 运行详情中目标组合/策略决策/订单执行的标的列合并为“名称（代码）”展示。
- [修复] 实盘运行记录 API 响应补齐被 `response_model` 过滤的字段（`mode`/`decision_count`/`order_count`/`strategy_id` 等），并新增账户/跳过原因/快照时间/完成时间字段：此前 Web 运行记录的“模式”“订单数”两列恒为空。
- [改进] Web 实盘运行记录与调仓批次重构：运行详情由 JSON 原文改为结构化展示（目标组合、策略决策的动作/溢价率/排名、订单执行状态与成交回报、风控诊断、批次信息）；运行列表增加模式/策略/决策数/完成时间列；批次表增加关联运行与订单进度（按订单状态聚合），点击批次跳转对应运行详情。
- [改进] 实盘 `/batches` 端点附带关联运行交易日/模式与订单状态聚合（`orders: {total, filled, ...}`），`/runs/{id}/orders` 补充 QMT 成交回报字段（成交价/成交量/时间/错误信息）。
- [修复] Web 实盘策略配置保存后参数回显丢失：`parameters` 键被深度驼峰化（`max_positions`→`maxPositions`），刷新后输入框恒显示策略默认值且保存的参数不生效；实盘配置读写现保留策略参数原始键名。
- [改进] Web 实盘策略配置补齐缺失配置项：调仓频率（交易日）、自选池（逗号分隔，留空=全市场）、下单前数据同步检查开关。
- [修复] 统一可转债基础数据 `status` 为 `active`/`delisted` 英文枚举，并自动迁移存量中文状态。
- [改进] 实盘策略运行 `mode` 支持 `auto`（默认）：按"最近一次成功调仓 + N 个交易日"推导调仓节奏，到期自动调仓、未到期自动事件检查；调仓失败不推进锚点，下个交易日自动补跑；调仓频率改为按交易日计（此前为自然日），config 读取接口附带只读 `next_rebalance_date`。
- [修复] 实盘策略运行状态时序修正：run 记录在下单执行前置为 `running`，执行器成功后才置 `completed`，失败落 `failed` 并记录错误信息，当日失败可重试（复用原 run/batch 行）；当日调仓成功后 auto 重复触发改为幂等返回该记录（此前执行前即标记 completed，下单失败也会前移调仓锚点）。
- [改进] Web 实盘工作台新增运行模式下拉（自动/调仓/事件检查），预览与生成订单按所选模式触发，并展示推导出的下次调仓日。
- [修复] 实盘下单前的盘中数据检查与可转债同步记录对齐：`run_scheduled_sync` 自建总 sync run 并落 `run_kind`/`trade_date` 列（此前全部同步记录均为默认 `after_close`/NULL，`latest_sync_run(run_kind='intraday', trade_date=今天)` 恒查不到，实盘策略会被 `intraday_sync_unavailable` 永久拦截）；手动触发的 `cb_scheduled` 记录同样落列。
- [改进] 移除实盘配置项 `data_max_age_minutes` 及对应的数据时效（stale）检查：当日数据新鲜度改由 `trade_date` 列级精确匹配保证，避免“盘中同步 14:00 完成、14:35 检查时被误判过期”的配置坑；存量库中的旧列保留不影响运行。
- [改进] 数据同步 API 与 Web 数据同步页面新增 `sync_type=cb_scheduled`：后台手动触发盘后调度链路（基础数据 → 行情 → 因子计算，等价 `run_scheduled_sync(run_kind='after_close')`），整条链路共享单条同步记录并支持取消，完成后发送盘后通知邮件；调度器定时路径行为不变。
- [新功能] Strategy Lab 新增可转债因子计算独立方法 `sync_cb_factors`：遍历在市转债，用正股当日日K close 与 `cb_basic` 的转股价本地计算溢价率，连同正股价、剩余规模定向写入 `strategy_lab_cb_daily_factors`（新增 `stock_close` 列，存量 SQLite 库自动补列），已接入数据同步 API 与 Web 数据同步页面来源下拉框（`sync_type=cb_factors`，因子日期取 `end_date`），暂不接入 CLI/定时调度入口。
- [新功能] Strategy Lab 数据同步新增可转债正股 OHLC 独立同步方法 `sync_cb_stock_ohlc`：仅同步在市转债对应正股（去重）的腾讯日K，落 `stock_daily`（`instrument_type='stock'`），支持增量起始日期，暂不接入 API/CLI/定时调度入口。
- [改进] SQL 执行工作台仅允许单条 `SELECT`/`UPDATE`，`UPDATE` 必须包含 `WHERE` 且最多影响 50 行，超限自动回滚。
- [新功能] 新增受登录保护的 SQLite SQL 执行工作台，支持表浏览、复制表名及结果展示。

- [修复] 为既有 SQLite 数据库补齐实盘策略新增字段，避免交易订单列表因缺少 `decision_id` 列而查询失败
- [新功能] 新增统一策略上下文、策略基类/注册器、low-premium 决策实现及共享执行规划器，保持 Strategy Lab 既有策略列表接口兼容
- [新功能] 新增回测/实盘执行器适配层与策略决策仓储，支持同一执行计划输出回测成交或 QMT 订单
- [改进] 实盘运行记录按策略模式幂等，支持调仓周期跳过并允许事件检查与调仓在同一交易日分别运行
- [文档] 在 `docs/convertible-bond-trading.md` 增加 DSA 可转债实盘自动调仓闭环、数据对象、风控、API、Web 工作台和分阶段验收方案草稿
- [修复] 可转债实盘调仓过滤 QMT 全量持仓中的股票和基金，避免非可转债标的被误生成卖单
- [新功能] 新增 QMT 持仓上报接口 `POST /api/v1/trading/qmt/positions`（按 account 全量替换，未上报标的自动清理，空列表视为清仓）
- [新功能] 新增持仓查询接口 `GET /api/v1/trading/positions`
- [改进] 前端新增「实盘」页面，交易记录与持仓作为子 tab 展示
- [新功能] 新增交易记录页（`/trading`），展示可转债实盘交易指令列表，支持状态筛选、分页与刷新
- [文档] 新增 `docs/convertible-bond-qmt-api.md`：面向 QMT 脚本开发者的实盘交易对接接口说明（拉取/回调契约、状态约定与接入示例）
- [新功能] 新增可转债实盘交易指令表 `trading_orders` 与创建/查询/取消 API
- [新功能] 新增 QMT 待交易指令拉取与执行结果回调 API（HTTP token 鉴权，支持 `submitted`/`filled`/`rejected` 状态回写）
- [文档] 新增 `docs/convertible-bond-trading.md`：可转债实盘交易链路对接契约与 QMT 接入说明
- [新功能] 策略实验室数据同步新增可转债溢价历史补数任务：通过 opencli `cb-premium-history` 按“转债代码 + 日期”补写 `strategy_lab_cb_daily_factors` 中缺失的 `premium_rate` / `remaining_size`，支持已退市筛选并接入数据同步页面入口
- [修复] 可转债基础数据同步入库改为逐只容错：单只转债的入库失败（basic/terms/events）仅记录错误日志并计入失败清单后跳过，不再因单只异常拖垮整个同步任务
- [修复] 可转债事件落库在同批次出现重复 (bond_code, event_date, event_type) 时触发 UNIQUE 约束冲突：数据源 cb_event_list 可能返回完全相同的重复事件，且 session 使用 autoflush=False 导致未 flush 的新行无法被后续 select 命中；`upsert_cb_events` 现先按去重键在内存去重再入库
- [修复] 可转债数据同步「包含已退市」语义修正：基础数据同步与 OHLC 行情同步在勾选「包含已退市」时改为合并活跃与已退市标的（去重），而非仅处理已退市列表，避免活跃标的被漏掉
- [改进] OpenCLI 可转债详情拉取默认并发降为 1（串行），避免并发启动多个浏览器会话互相串扰或失败导致单只详情拉取异常
- [改进] OpenCLI 可转债详情同步失败日志补充命令、退出码、stderr/stdout 摘要，便于定位单只 `cb-detail` 拉取失败原因
- [新功能] 策略实验室数据同步新增按 run 取消能力：`POST /api/v1/strategy-lab/data-sync/runs/{run_id}/cancel` 可请求后台任务在安全检查点停止，已入库数据保留，前端同步记录为运行中任务提供取消按钮
- [改进] 行情数据页数据同步记录新增 10 秒静默轮询刷新，减少后台同步状态依赖手动刷新
- [改进] 可转债基础同步在传入 `symbols` 时改为直接逐只拉 `cb-detail` 入库，跳过 `cb-list` 全量列表抓取；同时补正 `cb-detail` 中正股代码 / 名称字段映射，避免调试单只同步时正股信息落空
- [改进] 可转债事件落库去重键改为「债券代码 + 事件日期 + 小写 event_type」：写入前统一将 event_type 归一化为小写，并按小写比对已有记录，避免同一事件因大小写变体被重复录入；命中已有记录时更新详情
- [修复] 修复可转债基础数据同步在同步已退市标的时，因 `terms` 元数据混入 `date` 对象导致 `json.dumps` 序列化失败、整个同步任务中断的问题；`last_trade_date` 落库改为 isoformat 字符串，`terms_json` 序列化增加 `default=str` 兜底
- [改进] 可转债同步单只容错：行情同步单只（拉取/落库/因子回填）失败不再中断整体，记入失败清单继续处理；基础数据与行情同步每只转债均输出成功/失败日志
- [新功能] 可转债数据同步重构：新增 opencli（cb-list + cb-detail）基础数据同步与东财优先/腾讯兜底的 OHLC 行情同步，行情支持增量起始日期，两者均支持已退市开关；新增 CLI `scripts/sync_cb_data.py`，并扩展 `POST /api/v1/strategy-lab/data-sync` 支持 `sync_type`/`include_delisted`/`start_date`/`end_date` 参数
- [改进] 行情数据页"数据同步"表单来源选项更新为「可转债基础数据 / 可转债行情」两个能力（替代原 fixture/AkShare/集思录/OpenCLI 选项），新增"包含已退市可转债"开关与行情起止日期输入（留空为增量同步）
- [文档] 新增 `docs/convertible-bond-sync.md`：opencli / 行情接口源字段到数据库表字段的完整映射清单与同步用法
- [改进] AkShare 数据同步改为分批写入：先落转债列表/条款/事件，再逐只拉日线边拉边入库，定期将进度快照（已处理/总数）写入同步记录；同步记录表格实时展示进度，避免"全量拉完再一次性写入"失败即全丢
- [修复] 可转债日线查询接口 `limit` 语义改为"最近 N 条"（按日期降序截取后升序返回），避免长历史标的前端只取到最早数据、曲线停在旧日期；行情页单只取数上限调至 500 条
- [改进] 行情数据页"同步记录"表格支持服务端分页（10/20/50/页），结果与错误列固定宽度、自动换行、超过 200 字符省略展示，hover 显示完整内容
- [修复] 策略实验室数据同步改为后台异步执行（`POST /data-sync` 立即返回 `running`，前端轮询 `GET /data-sync/runs` 直至完成），避免 AkShare 全量同步阻塞请求导致 30s 超时；同一时间仅允许一个数据源同步任务
- [修复] 绕过 akshare `bond_zh_cov` 列名数量不匹配崩溃（akshare 1.17.x 返回 72 列但硬编码 71 列名），改为直接请求东财 RPT_BOND_CB_LIST 接口按英文字段自映射；同步日期/数值解析兼容 NaN 值
- [改进] 策略实验室"数据同步"Tab 移至行情数据页（可转债/股票/数据同步），同步入口与行情数据浏览归为一处
- [新功能] 策略实验室页面改为顶部 Tab 布局（策略研究 / 参数搜索 / 数据同步 / 实盘信号），并引入 antd 与 ECharts 组件体系
- [新功能] 新增行情数据页 `/market-data`，支持可转债历史行情、价格与溢价率图表、事件记录查看
- [新功能] 新增可转债数据查询 API（标的列表 / 详情 / 日线因子 / 事件），补齐策略实验室数据查看能力
- [chore] 新增一次性初始化脚本 `scripts/sync_cb_local_init.py`：从本地 `localhost:5273` 拉取可转债列表/详情/行情/事件并入库（行情共用 `stock_daily`，`instrument_type` 区分品种；`strategy_lab_cb_basic` 新增 `status` 字段）
- [新功能] 行情数据页改为可转债/股票双 Tab：可转债列表支持"未退市/已退市"状态筛选与"仅持仓"筛选，股票 tab 展示 stock_daily 历史 K 线（数据未同步时列表为空）
- [新功能] 新增 `GET /api/v1/stocks/list` 与 `GET /api/v1/stocks/{code}/bars` 本地行情查询接口；`GET /api/v1/strategy-lab/instruments` 新增 `status`/`held_only` 筛选参数
- [Agent Chat 按会话持久化 Skill 选择，支持刷新和会话切换恢复，并区分省略 `skills`、显式空列表与非空选择；无持久化状态的历史会话继续使用运行时默认且不会被静默转为显式选择，复用分析 `context` 中残留的 legacy `skills` / `strategies` 也不会覆盖顶层三态或会话状态，非空但全部无效的 Skill 请求不会被当成显式空列表并清空既有选择
- [改进] 后端 CI 在不跳过离线测试的前提下按完整测试文件分成三个独立 runner 并行执行，由单一 `backend-gate` 汇总门禁结果；实测文件耗时和首分片静态检查成本共同参与负载平衡，新测试文件自动纳入，现有 pip 安装和测试参数保持不变，避免 xdist 进程内并发的全局状态竞态。
- [测试] 后端 CI 默认覆盖所有非 Web 改动，仅对已证明安全的纯 Web 路径跳过，并将整个 Web public 目录及前端渠道模板、设置帮助视为跨层运行合同；补充纯 Web、共享 Web 资产及 Web/非 Web 混合改动的过滤语义回归，明确 `predicate-quantifier: every` 按单文件匹配全部规则、再以任一匹配文件触发门禁。Docker CI 继续按构建输入过滤。离线测试保留稳定的串行执行与慢用例摘要，并移除重复用例和测试内真实等待。

- [修复] Web 分享图改为用户点击“分享”后才按需生成，不再在报告加载时自动请求
- [修复] 将 `SCREENING_ENABLED` 及 Web 选股功能开关归入“基础设置”，选股导航入口继续由该开关控制
- [修复] 飞书交互机器人在 `FEISHU_DOMAIN=lark` 时让 Stream 长连接与消息回复统一使用 Lark 国际版 API 域名，避免 SDK 默认连接飞书国内域名并返回 `Incorrect domain name`（fixes #937）。
- [修复] `scripts/ci_gate.sh` 的 `offline_test_suite` 给 `pytest -m "not network"` 加 `--timeout=120 -o timeout_method=thread` 与 `-o faulthandler_timeout=300`：单个测试（含其 teardown）超过 2 分钟直接 fail，单个测试（含其 teardown）超过 5 分钟时 dump 全部线程栈到 stderr。配合 `.github/requirements-ci.txt` 新增 `pytest-timeout>=2.3.0` 依赖。issue #2131 报告过 backend-gate 在 AlphaSift hotspot 用例附近间歇性无 traceback 卡住直到被 GitHub Actions 取消，此次修复让任何未来 CI hang 都会留下可定位的失败信息或 post-mortem 栈，而不是静默消亡。同步修正 `.github/workflows/docker-publish.yml` 的 `Install backend gate dependencies` 与 `setup-python cache-dependency-path` 对齐 `ci.yml` 的 backend-gate 依赖安装方式，避免发布流程跑同一个 `./scripts/ci_gate.sh` 时因缺少 `pytest-timeout` 而直接 fail。

- [修复] 选股策略栏稳定展示完整中文策略列表，并保留自定义策略 ID 入口
- [修复] 选股热点详情统一使用中文业务文案，不再显示内部类名、字段名和原始数据源错误
- [改进] 选中热点后先展示榜单已有摘要和核心股，后台再补充完整详情，并将单次热点源等待上限收紧为 8 秒
- [修复] 刷新热点榜单并保留当前题材时同步绕过详情缓存重拉该题材，避免新榜单继续搭配旧路线与成分股；详情质量可用且字段完整时不再展示底层数据源尝试失败
- [改进] 热点成分股并行获取东方财富与同花顺数据，并按固定数据源优先级合并；AkShare 调用复用 DSA 的可终止子进程 timeout，同花顺 HTTP 调用设置 connect/read timeout，并以进程级并发槽限制活跃任务、在返回前回收 worker；题材详情可按需复用 DSA 原生搜索服务的数据源优先级、时效过滤、缓存与同请求合并补充安全且带链接的近期消息，真实供应商调用使用限流且可终止回收的子进程，并在本地压缩摘要以避免额外 LLM 等待与降级提示；显式搜索增强不写入热点详情共享缓存或 Web 页面缓存
- [修复] 选股尾部轮换以分析器输入顺序为权威并保留并列分候选顺序；热点消息增强分别追加 `route` 与原始 `timeline`，同键搜索只允许缓存 owner 启动供应商链，子进程启动或清理失败也会释放全局容量；热点默认 provider 的正数超时配置覆盖板块、成分股及直接详情 fallback 并传递剩余硬截止，关闭外层预算仍保留单源安全上限；主动新闻搜索以同一绝对 deadline 覆盖缓存等待、重新竞争和 provider 执行，并区分有效空结果与运行失败
- [改进] 精简选股页面的重复说明，将任务标识、快照统计和排序诊断折叠到运行详情
- [新功能] SkillAggregator 基于独立满足 30 条 evaluated 门槛的真实 Skill Outcome bucket，使用 Beta 先验收缩、unable 惩罚和多周期证据加权生成有界运行时权重；缺失、低样本或异常统计保持中性。
- [改进] 将参考 AlphaSift 实现的选股核心与策略正式纳入 DSA，统一使用 `ScreeningService`、`SCREENING_ENABLED` 和 `/api/v1/screening`，并保留 Apache-2.0 归因与来源版本记录。
- [新功能] 选股结果按 `run_id` 持久化到 DSA 数据库，新增运行历史和数据源历史 API，接入公告事件上下文及其搜索缓存，并支持将候选连同筛选策略映射的 skill 交给单股深度分析。
- [修复] Outcome 候选按上次尝试时间公平调度，避免持续新增的缺失 key 使旧 `pending` outcome 永久得不到重试。
- [新功能] 新增按 skill、horizon 与 outcome engine version 独立聚合的只读 Skill Opinion 表现统计；少于 30 条 evaluated 样本时仅返回观察性计数，不输出表现指标或调整运行时权重。
- [修复] 选股主模型返回空内容、非 JSON 或低覆盖结构时继续尝试备用模型；全部失败时明确展示确定性因子排序状态。最终 JSON 必须在 `content` 或 `output` 块中；`reasoning_content`（链式思考）被视为内部辅助，不作为最终结果。
- [改进] Web 选股使用浏览器匿名种子与运行 ID 在最终评分后的有界近分池中生成每次运行的候选组合；Web Storage 不可用时在页面会话内存中复用同一临时种子；本地评分覆盖完整短名单，远程分析继续遵守数量上限，且只有完成相同后置分析的候选可参与轮换；原 Top-N 前半部分、明显领先候选、硬过滤、风险否决和得分保持不变。
- [改进] 热点榜单刷新与选股长流程解除双向串行等待，热点详情改为选中后按需加载；选股默认复用 5 分钟内且数据源优先级一致的成功全市场快照，同一来源链中的后备源结果也可复用，并在后台任务中展示快照、候选上下文、LLM 重排、最终评分和新闻事件增强阶段。
- [修复] 选股日线增强改用请求级 DSA-first fetcher 注入，不再临时替换进程级函数，避免重叠请求泄漏 wrapper 或重复执行 fallback；多个后置分析器按最新分数逐级重排，远程分析状态跟随实际提交候选，超限候选统一记录为 `skipped`，外部响应也不能改写未提交候选。
- [修复] 统一等价股票代码的本地日线候选与同源窗口解析；冲突沪深交易所代码不再降级匹配裸码，回测仅接受快照或交易日历确认的起点，并在同一起点中优先完整的单一代码窗口。
- [新功能] 新增按 individual SkillAgent 自身 signal、版本化 engine 与本地已存同源日线窗口计算并持久化 `skill_opinion_outcomes` 的核心服务。
- [修复] #1970 关闭认证属于高风险操作，即使携带有效 session cookie 也强制要求再次输入当前管理员密码二次确认；后端 `auth_update_settings` 的 disable 分支统一走 currentPassword 校验，命中 rate limit 时与 enable 路径一致返回 429，前端 `AuthSettingsCard` 在关闭认证时如有缺失当前密码将阻止提交并给出内联提示。
- [改进] 优化首页侧栏任务面板与自选股工作区：支持折叠任务摘要、自选股直接打开最新详情，并压缩头部操作以释放窄侧栏列表空间。
- [修复] 收敛自选股行交互与今日状态语义：详情与移除操作使用独立可访问按钮，详情提示从当前行状态实时派生并区分查找中、查找失败和确认无详情，任何 stock-bar 请求及完成任务后的数据刷新都会在开始时进入待确认状态，旧 stock-bar 或 fallback 报告在重新确认或未知期间不会作为最新详情开放；刷新失败时不再把旧历史记录误标为今日分析，自选股刷新会显式重试列表、stock-bar 与逐股票详情查询，逐股票 fallback 使用固定 worker 并发上限并取消已失效的查询批次。
- [新功能] 新增按 individual SkillAgent 自身 signal、版本化 engine 与本地已存同源日线窗口计算并持久化 `skill_opinion_outcomes` 的核心服务；本阶段不提供管理员 API、表现统计、样本充足度或权重调整。
- [新功能] STOCK_LIST 解析新增 `parse_analysis_target()` 单条目解析契约，支持 sh/sz/bj/hk/us 前缀校验、裸码默认归股票、未命中前缀降级为股票三段语义；保留现有 `split_stock_list()`/`serialize_stock_list()` 行为不变，并对外暴露 `IndexRegistry`、`AnalysisTarget`、`ParseStatus`、`default_index_registry()` 以便上层注入自定义指数白名单（关联 issue #2063 Phase 1）
- [修复] `parse_analysis_target()` 在显式交易所后缀输入被规范化层拒绝时（如 `600519.BJ`、`600000.HK`、`1234567.SH`、`abc.SH`），不再静默改写为 `sh<digits>` 或继续走裸码分类导致误判为 US，而是直接返回 `unsupported` 并携带可定位原因；保留 `000300.SH` / `sh000300.SH` 等已知 INDEX alias 的索引命中路径（关闭 PR #2122 review blocker OR-COR-607f1395 / OR-COR-26596201 / OR-COR-d6afd0d6）
- [修复] `parse_analysis_target()` 收敛显式交易所后缀输入的 3 个新 correctness blocker：畸形混合 alias（如 `sh0x00300.SH`）不再被数字过滤后重建为已注册指数；dotted-prefix 形态（如 `SH.000999`）在 invalid base 时不被 strict-suffix 误拒，亦不静默降级为畸形 canonical stock，统一返回 `unsupported`（白名单交易所的合法 alias 通过 `sz399001.SZ` / `sz399006.SZ` 等命中 INDEX）；外盘半显式 suffix（.T/.KS/.KQ/.TW/.TWO）非法 base 不再静默回退到 US stock，而是返回 `unsupported` 并标记外盘 suffix（关闭 PR #2129 review blocker OR-COR-d83a3580 / OR-COR-b3e32200 / OR-COR-e21e9de5）
- [修复] `parse_analysis_target()` 在 `us` 前缀分支按 Phase 1 contract（issue #2063 maintainer clarification 2026-08-01）统一收紧：`us` 前缀本身大小写不敏感（`us`/`US`/`uS`/`Us` 均可），但 ticker base 必须为 canonical uppercase US symbol 形态（regex `^[A-Z]{1,5}(\.[A-Z]{1,2})?$`，与 `data_provider/us_index_mapping.py` 和 `stock_code_utils._normalize_code_and_exchange` 一致）。`us` 前缀输入只要 base 不 match 该 regex——含任意小写字母（`usfd` / `usm` / `usibm` / `usamd` / `usge` / `usbk` / `usaapl` / `usshop` 全小写、`Usfd` / `USibm` / `Usaapl` / `uSfd` / `USaapl` mixed-case prefix + lowercase base）、含标点（`usbrk.b` / `usshop.us` lowercase base with punctuation）、含数字（`us1` / `us1a` / `us12a` lowercase base with digits、`US1` / `US12345` all-uppercase but invalid US shape）一律直接返回 `unsupported` 并提示用户使用 uppercase base（如 `usAAPL` / `usBRK.B` / `usSHOP.US`）或 bare 大写形态（如 `USFD`）。docstring 同步更新明确「exchange 前缀大小写不敏感，但 `us` 后 base 必须 match canonical US symbol shape」契约。该 contract 一致规则同时关闭 PR #2129 review blocker OR-COR-9c3d2c44（`usfd`/`usm` 全 lower 静默大写）、OR-COR-2f0d1a7e（`usibm`/`usge`/`usbk`/`usaapl` 长度依赖剥前缀分叉）、OR-COR-7b45f5c1（`Usfd`/`USibm`/`Usaapl` mixed-case prefix + lowercase base 绕过 lowercase-only guard）与 OR-COR-us-prefix-nonalpha-guard-gap（`usbrk.b`/`usshop.us`/`us1` lowercase/non-alphabetic base 绕过早期 `raw.isalpha()`-gated guard 走到 `_split_prefix` 异常剥前缀），无需 US ticker 白名单。mixed-case `usBRK` / `UsBRK` / `uSBRK` / `usFD` / `usM`（prefix any case + upper base）与全 upper `USAAPL` 等仍按显式前缀剥前缀；`USFD`/`USM` 等 ≤5 字母全 upper 仍按 bare ticker 保留
- [修复] `_split_prefix()` 对带 `.US` 后缀的 bare 美股代码（`SHOP.US` / `HKD.US` / `BJRI.US` / `USFD.US` / `BRK.B` / `AAPL.US`）应用与裸 1-5 字母代码（`USFD` / `SHAK` / `AAPL`）相同的 short-circuit：只要 token 严格 match canonical US symbol shape regex `^[A-Z]{1,5}(\.[A-Z]{1,2})?$`，就不进入 `_KNOWN_PREFIXES_SORTED` 扫描。此前 short-circuit 只对纯字母 (1-5 ASCII uppercase letters) 生效，导致前两字符碰巧撞上 `sh` / `hk` / `bj` / `us` 已知前缀的 dotted `.US` 代码（`SHOP.US -> sh`+`OP.US`、`HKD.US -> hk`+`D.US`、`BJRI.US -> bj`+`RI.US`、`USFD.US -> us`+`FD.US`）被错误拆分，exchange 与 canonical_id 双双错位。mixed-case 显式前缀代码（`usAAPL` / `hk00700`）仍按原 prefix 路径处理。关闭 PR #2129 round-4 review blocker OR-COR-bare-us-suffix-prefix-collision
<!-- 新条目格式：- [类型] 描述（类型取值：新功能/改进/修复/文档/测试/chore）-->
<!-- 每条独立一行追加到本段末尾，无需分类标题，合并时冲突最小 -->
- [文档] FAQ 补充 macOS 桌面应用被 Gatekeeper quarantine 阻止启动时的受信任安装包临时放行步骤（refs #2113）。
- [文档] 新增策略实验室迁移方案，记录 trading-backtest 迁入 DSA 的命名边界、Portfolio 统一原则、数据层分层、API 路径、前端重做策略、阶段计划与验证命令。
- [新功能] 新增策略实验室 Phase 1 后端最小闭环，提供 `strategy_lab` 领域骨架、fixture 双低策略回测、结果落库以及 `/api/v1/strategy-lab` 运行查询接口；现有 Portfolio 仍作为唯一持仓真源。
- [新功能] 策略实验室继续接入可转债 fixture 数据同步、参数批量运行、策略信号记录和 Web `/strategy-lab` 最小工作台；新增 `strategy_lab_cb_*`、sync、batch、signal 表与对应 API，信号仅引用 Portfolio 账户和交易 ID，不新建持仓真源。
- [改进] 策略实验室信号支持显式确认到现有 Portfolio trades，Web `/strategy-lab` 最小工作台可触发数据同步、样例 run、样例批次、生成信号和确认到持仓。
- [改进] 策略实验室数据同步支持非 fixture payload 直接写入可转债基础信息、条款、日因子和事件；同时收敛 `/api/v1/history` 与 `/api/v1/decision-signals` 根路径 OpenAPI 契约，避免尾斜杠路径漂移。
- [新功能] 策略实验室接入 AkShare 可转债数据 provider，支持条款、溢价率、剩余规模、强赎/下修事件同步；同步数据可直接驱动策略回测，并新增结构化事件研究 API。
- [改进] 策略实验室批次支持失败重试和中断恢复，策略信号增加 Portfolio 账户 active/市场校验，Web 页面升级为真实参数、批次详情、同步历史和 Portfolio 确认工作流。
- [改进] 策略实验室补齐 MA 交叉策略、Jisilu/OpenCLI 可转债快照 provider、批次删除与后台 SSE 进度流，并增加同规范化快照的迁移回归。
- [新功能] LLM 渠道新增显式 Chat Completions / Responses API Surface，支持 Anspire GPT-5.6 系列等 Responses-only 模型，并统一连接测试、主分析、筛选、图片识别与状态诊断路由；所有运行路径先按同一规则解析协议再校验 Surface，混合 Surface 的同名路由按未知能力保守处理；显式 Anspire 渠道独占共享 Key，非法 Surface 或协议不匹配时不会把该 Key 回退为旧版 Chat 部署，同时保留无关的 Gemini/OpenAI 等 legacy provider；本地 loopback 渠道可在图片识别路径继续无 Key 调用，远端渠道仍要求凭据；禁用渠道不会因残留 Surface 配置阻断其他兼容 fallback；Web 编辑器不会静默改写非法历史值，并允许将 Hermes 非法 Surface 修复为 Chat Completions。
- [修复] 将 Responses 渠道的协议、模型 provider、公开 route alias 与 wire-model 构造收敛为统一路由契约，保存校验、运行时加载、状态诊断、选股入口和 Web 编辑器共同使用当前安装的 LiteLLM provider registry，拒绝 `openai` 协议下显式非 OpenAI provider 的模型、拒绝同一 alias 混用 Chat/Responses，并保留 OpenAI-compatible 网关自有的带斜杠模型 ID。

## [3.29.0] - 2026-08-02

### 发布亮点

- feat: 将参考 AlphaSift 实现的选股核心与策略正式纳入 DSA，新增选股运行历史、数据源历史和候选深度分析链路。
- feat: 新增 1080px 个股决策卡与高密度市场复盘分享图，支持 Web 原生分享和下载回退。
- feat: 新增 Skill Opinion Outcome 计算、表现统计与基于真实样本的有界运行时权重。
- improve: 优化选股快照复用、热点按需加载、多源并发和候选轮换，缩短长流程等待并提升结果多样性。
- fix: 加固关闭认证、短凭证诊断脱敏、CI 超时取证和桌面冻结包启动链路。
- fix: 修复 Longbridge 量比、股票代码窗口解析、选股后置重排及分享图交互等稳定性问题。

### 新功能

- SkillAggregator 基于独立满足 30 条 evaluated 门槛的真实 Skill Outcome bucket，使用 Beta 先验收缩、unable 惩罚和多周期证据加权生成有界运行时权重；缺失、低样本或异常统计保持中性。
- 选股结果按 `run_id` 持久化到 DSA 数据库，新增运行历史和数据源历史 API，接入公告事件上下文及其搜索缓存，并支持将候选连同筛选策略映射的 skill 交给单股深度分析。
- 新增按 skill、horizon 与 outcome engine version 独立聚合的只读 Skill Opinion 表现统计；少于 30 条 evaluated 样本时仅返回观察性计数，不输出表现指标或调整运行时权重。
- 新增按 individual SkillAgent 自身 signal、版本化 engine 与本地已存同源日线窗口计算并持久化 `skill_opinion_outcomes` 的核心服务。

### 改进

- 选中热点后先展示榜单已有摘要和核心股，后台再补充完整详情，并将单次热点源等待上限收紧为 8 秒。
- 热点成分股并行获取东方财富与同花顺数据，并按固定数据源优先级合并；真实供应商调用增加限流、可终止 timeout、并发槽和 worker 回收，题材详情可按需复用 DSA 原生搜索服务补充安全且带链接的近期消息。
- 精简选股页面的重复说明，将任务标识、快照统计和排序诊断折叠到运行详情。
- 将参考 AlphaSift 实现的选股核心与策略正式纳入 DSA，统一使用 `ScreeningService`、`SCREENING_ENABLED` 和 `/api/v1/screening`，并保留 Apache-2.0 归因与来源版本记录。
- Web 选股使用浏览器匿名种子与运行 ID 在最终评分后的有界近分池中生成每次运行的候选组合；本地评分覆盖完整短名单，远程分析继续遵守数量上限，硬过滤、风险否决和得分保持不变。
- 热点榜单刷新与选股长流程解除双向串行等待，热点详情改为选中后按需加载；选股默认复用 5 分钟内且数据源优先级一致的成功全市场快照，并展示快照、候选上下文、LLM 重排、最终评分和新闻事件增强阶段。
- 图片报告改用独立的 1080px 个股决策卡和高密度市场复盘卡，优先从结构化 payload 精确填充数据并保留 Markdown 回退；小红书账号与二维码支持关闭或替换，Web 支持原生分享与下载回退。

### 修复

- Web 分享图在按需生成完成后通过第二次用户点击同步打开系统分享，避免首次异步生成使原生分享退化为下载。
- Web 分享图改为用户点击“分享”后才按需生成，不再在报告加载时自动请求。
- 移除基础设置选股卡片中仍跳转到“数据源”的过期“查看配置项”按钮，并将 `SCREENING_ENABLED` 及 Web 选股功能开关归入“基础设置”。
- `scripts/ci_gate.sh` 的离线测试增加单测 timeout 与 faulthandler 取证，并同步 Docker 发布流程的 CI 依赖，避免无 traceback 卡住或发布门禁缺少 `pytest-timeout`。
- 选股策略栏稳定展示完整中文策略列表，并保留自定义策略 ID 入口。
- 选股热点详情统一使用中文业务文案，不再显示内部类名、字段名和原始数据源错误。
- 刷新热点榜单并保留当前题材时同步绕过详情缓存重拉该题材，避免新榜单继续搭配旧路线与成分股。
- 选股尾部轮换以分析器输入顺序为权威并保留并列分候选顺序；热点消息增强、共享缓存 owner、全局并发容量和新闻搜索 deadline 统一收敛。
- Outcome 候选按上次尝试时间公平调度，避免持续新增的缺失 key 使旧 `pending` outcome 永久得不到重试。
- 选股主模型返回空内容、非 JSON 或低覆盖结构时继续尝试备用模型；全部失败时明确展示确定性因子排序状态，且不把 `reasoning_content` 当作最终结果。
- 选股日线增强改用请求级 DSA-first fetcher 注入，多个后置分析器按最新分数逐级重排，远程分析状态跟随实际提交候选，避免重叠请求泄漏 wrapper 或改写未提交候选。
- 统一等价股票代码的本地日线候选与同源窗口解析；冲突沪深交易所代码不再降级匹配裸码，回测仅接受快照或交易日历确认的起点。
- 关闭认证时强制再次校验当前管理员密码，命中 rate limit 时返回 429，前端在当前密码缺失时阻止提交并显示内联提示（#1970）。
- 本地 CLI 的 `stdout_preview` / `stderr_preview` 按环境变量、JSON、YAML/日志标量与 URL 的独立契约脱敏短凭证，避免 API key、secret 或 token 进入诊断（refs #1784）。
- PyInstaller 冻结包在 NLTK 3.10 导入保护下误判内置 `_internal` 标准库时不再启动失败；Windows/macOS 打包脚本统一接入兼容 runtime hook。
- 分享图按字段合并历史结构化数据与 Markdown，多市场逐区域复用持久化 payload，隐藏不可用市场灯号维度并保留配色方案；中英韩模板跟随报告语言，原生分享失败时自动回退下载。
- 飞书文件报告在写入或上传前清理隐藏的市场 metadata；桌面运行时默认隐藏未随包提供 renderer 的 Web 分享按钮。
- `redact_diagnostic_text()` 的命令替换扫描不再吞掉尾随非敏感诊断字段，并统一 `export FOO=$(...)` 与 `FOO=$(...)` 的脱敏行为。
- Longbridge 量比改用 adaptive keyword args 调用 `history_candlesticks_by_offset`，兼容 0.2.74 与 4.x SDK 参数顺序（fixes #2100）。

## [3.28.0] - 2026-07-26

### 发布亮点

- feat: Multi-Agent 多策略综合支持分层 deliberation、mediator/self-review、revision projection 与 multi-round，并统一最终动作和解释契约。
- feat: AI 建议页新增按决策风格分组的历史表现，specialist opinion 样本可持久化并用于后验评估。
- feat: 新增 `--portfolio futu`，可只读导入 Futu OpenD 真实账户的沪深 A 股、港股和美股 LONG 正股持仓。
- feat: Web 首页与 API 支持按单个或多个市场临时触发大盘复盘，不修改全局配置。
- feat: Tushare 支持通过 `TUSHARE_HTTP_URL` 接入自建网关或兼容镜像。
- fix: 改进港股行情路由与缓存、外股英文新闻匹配、数据源兜底顺序及桌面端发包稳定性。

### 新功能

- Multi-Agent 多策略综合新增受控 deliberation v0、可注入 mediator/self-review v1-v2、只读 revision projection v3 与 multi-round v4；增强层相对上一层 baseline 只能保持或继续 softened，不覆盖权威最终信号。
- `specialist` 模式最多选择 4 个策略专家，并通过 `AGENT_SKILL_CONCURRENCY` 控制 1–4 个 worker 并发；worker 继承主管线冻结的 target date 等上下文，单个 skill 失败不阻断其它策略或最终决策。
- Multi-Agent 报告按八态用户 action 追踪 Pipeline 最终调整，排除非法 Agent 意见；仅在 canonical action 可唯一解析时生成 explanation 与 DecisionSignal，并以同一个 `final_action` 统一最终动作契约。
- specialist 在分析历史保存成功后持久化版本化、低敏且幂等的有效 opinion 样本，为后续后验评估提供真实数据；本阶段不计算 outcome、不统计表现、不调整权重。
- AI 建议页新增决策风格历史表现，按每个分组独立的 30 个已完成样本门槛展示命中、区间涨跌、无法评估和最大不利波动，并保持旧统计接口兼容。
- 新增 `--portfolio futu`，只读导入 Futu OpenD 真实账户的沪深 A 股、港股和美股 LONG 正股持仓作为分析列表。
- Web 首页与 `POST /api/v1/analysis/market-review` 支持用严格校验的 `region` 临时选择单个或多个复盘市场；一次性覆盖不读写全局配置，并贯穿任务提交、状态、SSE、结果与历史记录。
- Tushare 数据源支持通过 `TUSHARE_HTTP_URL` 自定义接入地址；留空时继续使用官方默认地址（fixes #1985）。

### 改进

- 暂停 PR Review 的自动触发，仅保留 `workflow_dispatch` 手动入口，避免辅助评审重复运行及评论权限失败产生误导性红灯；正式 CI 检查保持不变。
- `.env.example` 与每日分析 workflow 同步映射 `TUSHARE_HTTP_URL`，保持本地和云端配置入口一致。

### 修复

- 修复外股代码映射到中文显示名时英文新闻相关性漏判，统一外股代码、英文名和别名解析，并对展开后的检索词去重（fixes #2026）。
- 特权 `pull_request_target` 流程不再检出 fork PR head；敏感步骤仅执行主分支可信脚本，PR 元数据与 diff 通过 GitHub API 读取（fixes #2051）。
- PR Review 事件载荷缺失、不可读或 JSON 非法时输出可定位且不泄露载荷的警告，并保留原有降级行为（fixes #2070）。
- 修复 Windows 上 `mimetypes` 冷启动读取注册表导致进程卡死的问题。
- 统一 `DataFetcherManager`、AkShare 与 Longbridge 对 4–5 位裸港股码的识别，避免 4 位代码被错误路由或静默失败（fixes #2091）。
- AkShare 港股实时行情增加 20 分钟全市场缓存与并发冷启动 single-flight，热缓存命中不再等待网络限速，主接口异常时仍保留新浪备用接口降级（refs #1852）。
- 将 `TencentFetcher` 默认优先级调整为 A 股日 K 数据源的最终兜底，并新增 `TENCENT_PRIORITY` 显式覆盖项（refs #2032）。
- Web 设置页和通知测试入口补齐普通钉钉群机器人配置，支持安全遮罩保存 webhook 与 secret、查看帮助并发送测试通知（refs #1957）。
- Agent Chat 普通与流式接口在请求未指定 `report_language` 时继承全局 `REPORT_LANGUAGE`，显式请求值仍优先。
- WebUI 分开展示发布版本、代码版本与构建时间，并用构建输入摘要避免复用时间戳未变化的旧静态资源（fixes #2093）。
- macOS unsigned 打包显式禁用 Electron 签名与 Hardened Runtime，在冻结后端和 electron-builder 阶段清理残缺签名，并审计原始应用与 DMG 产物；该缓解不替代 Apple Developer 签名与公证（refs #2075）。

### 文档

- 修复文档中的失效相对链接。
- [修复] #2026 外股代码映射到中文显示名时英文新闻相关性判定漏判：新增同源 STOCK_ENGLISH_NAME_MAP 单一真源、canonicalize_foreign_stock_code 规范化入口与 _foreign_english_query_terms 别名解析，使 AAPL/00700/BABA 等 ticker 即使 stock_name 为中文也能在查询构建、相关性打分与多维度情报路径上复用 canonical 英文名，并补齐 .US/.HK suffix / HK 前缀全形式的归类与回归用例；同时在 _score_news_relevance 对 alias 展开 term 做去重，避免 legal alias 展开短名与显式 short alias 重复计分。
- [新功能] Tushare 数据源支持通过 `TUSHARE_HTTP_URL` 环境变量自定义接入地址，便于网络无法直达 `api.tushare.pro` 时切换自建网关或第三方兼容镜像；留空保持官方默认地址不变（fixes #1985）
- [文档] `.env.example` 与 `.github/workflows/00-daily-analysis.yml` 同步映射 `TUSHARE_HTTP_URL`，避免出现"配置项有但 workflow 漏映射"的半修状态
- [修复] #2051 PR Review 的特权 `pull_request_target` 流程不再检出 fork PR head：敏感文件、标签、报告与 AI 审查统一通过 GitHub API 将 PR 元数据和 diff 作为数据读取，只执行主分支可信脚本；Python 语法、Flake8、确定性检查和离线测试继续由无 secrets 的 `pull_request` CI / `backend-gate` 执行，兼容 `actions/checkout` 新增的 fork checkout 安全保护。
- [修复] 修复 Windows 上 mimetypes 冷启动时读取注册表导致的进程卡死

## [3.27.0] - 2026-07-19

### 发布亮点

- feat: 新增 Codex App Server single-agent 问股实验原型，并保持 LiteLLM、Multi Agent、普通报告和定时任务等默认链路不变。
- feat: Web AI 建议页支持保存基于历史报告快照重算的决策风格信号，补齐去重、续期、失效和可审计 guardrail 语义。
- feat: 引入多策略观点结构化输出第一阶段契约，覆盖观点标准化、基础冲突检测、聚合元数据和报告兼容边界。
- improve: 报告页明确展示输入数据状态、来源、异常影响、处理建议和诊断码，并区分页面资讯与本次分析输入。
- fix: 修复 MiniMax 推理内容污染最终 JSON、字符串 `<think>` 包装兼容及多 Agent 风险覆盖后结论未按最终信号收敛的问题。
- fix: 补齐美股实时行情 PE/PB 估值字段、多市场工具描述和 macOS Gatekeeper 安装排障说明。

### 新功能

- 新增 #1743 Phase 6 Codex App Server single-agent 问股实验原型，仅开放三个既有只读 Tool Surface 工具；默认 LiteLLM、Multi Agent、Deep Research、普通报告、定时任务与 Phase 1/2 `codex_cli` 路径保持不变。
- Web AI 建议页支持确认保存基于历史报告快照重算的决策风格信号，以 created/existing/refreshed 区分新建、原样复用和既有记录续期或维度补齐，并复用 profile-aware 去重与失效语义。
- 多策略观点结构化输出第一版新增策略观点标准化、基础冲突检测与聚合 metadata，作为 #1964 的阶段性基础契约；本版本不声明完成并发执行、完整策略调度 MVP 或前端完整多语言展示。

### 改进

- Codex 设置页仅检查配置、命令和所需协议是否允许尝试，用户保存后可直接提问；Chat 以服务端 `accepted` 事件提交问题并按实际 backend 停止。
- Web 报告页输入数据块沿用状态、来源、告警和说明字段，在说明中补充异常影响、处理建议与诊断码，并区分报告页资讯和本次分析输入。
- 更新 Anspire 数据源的项目展示信息，并将 `get_stock_info` 工具说明从 A 股限定修正为覆盖 A 股、港股和美股。

### 修复

- 修复 MiniMax 分析与渠道 JSON 测试把推理内容和最终文本拼接后导致结果无法解析、无法持久化的问题；字符串响应仅剥离开头完整的 `<think>` 包装，并保留 JSON 内容中的同名字面标签。
- 修正多 Agent 内部 runtime facts 的 timeout 归因，并让 risk application 覆盖后的 dashboard 决策字段及一句话核心结论基于 post-risk signal 完成 finalization。
- 收敛多策略综合器语义：正确处理 Signal 枚举、缺失 signal、有效 opinion_count 和 deterministic synthesis，并兼容历史与外部 dashboard 的宽松字段形状。
- Codex 问股只接受 App Server 明确完成的终态回答，并统一整体时限、累计输出、事件、工具预算和进程回收边界。
- `codex_cli` 普通分析显式固定无人值守批准策略与只读沙箱，避免新版 Codex 在非交互任务中因请求人工批准而中断。
- yfinance 美股实时行情补齐 `pe_ratio` 和 `pb_ratio`，供估值分析和下游报告使用。

### 文档

- 补充 macOS 未签名、未公证 DMG 被 Gatekeeper 拦截时的架构选择、安全排查与官方安装包临时放行步骤。

## [3.26.1] - 2026-07-12

### 发布亮点

- feat: Web 首页新增历史、自选与今日工作区，支持批量分析、今日覆盖判断和评分排行。
- feat: 新增 A 股市场结构与题材主线上下文，并贯通报告、Agent、DecisionSignal 与 Web 展示。
- feat: 飞书支持文件形式推送报告，多 Agent 支持子 Agent 独立超时钳位。
- feat: 补齐内部 DSA Tool Surface、DecisionAgent 分歧摘要和 DecisionSignal profile 契约。
- fix: 统一报告动作口径，修复按股票代码批量删除历史记录和通知理由静默截断问题。
- fix: 改进 Web、桌面端、数据源缓存及发行包资源的稳定性。

### 新功能

- 新增 A 股市场结构与题材主线上下文，并在报告、Agent、DecisionSignal 和 Web 市场位置卡中复用。
- 飞书推送新增文件上传能力：`FeishuSender.send_feishu_file(file_path)` 通过 App Bot SDK (`im.v1.file.create`) 上传文件并发送文件消息；Webhook 模式回退为发送文件内容文本；新增 `FEISHU_SEND_AS_FILE=true` 配置开关，开启后飞书以文件形式发送报告而非文字消息。
- 多 Agent 编排 Pipeline 新增子 Agent 独立超时钳位：支持 6 个环境变量为 TechnicalAgent、IntelAgent、RiskAgent、DecisionAgent、PortfolioAgent、SkillAgent 各自配置独立硬上限，互不挤占配额；默认 0 表示关闭钳位。

### 改进

- 为 multi-agent DecisionAgent 增加内部低敏分歧摘要输入管线，作为 #1904 P1 解释输出的前置 plumbing；不改变 public API、dashboard schema 或最终解释字段。
- GitHub Actions 每日分析工作流补齐 TickFlow 数据源环境变量映射，并收敛 README 数据源稳定性说明到完整指南。
- Web 首页个股栏新增历史 / 自选 / 今日切换，保留历史分析默认视图，并支持在自选页一键分析全部或仅分析今日未覆盖股票、在今日页按评分查看当天分析排行；分块提交部分失败时保留已确认计数、停止后续提交并刷新任务列表。
- GitHub Actions 每日分析工作流新增钉钉通知环境变量映射，支持在云端定时任务中直接使用钉钉机器人。
- `STOCK_LIST` 自选股解析支持中文逗号、顿号、分号、空格和换行等常见粘贴分隔符，运行时、定时热刷新、CLI `--stocks`、Web 设置保存和自选 API 统一识别，并在写回时规范为英文逗号。
- 新增 `NEWS_INTEL_AUTO_FETCH_ENABLED` 单开关，开启后个股分析、Agent 分析和大盘复盘会 fail-open 自动初始化并刷新 RSS/Atom/NewsNow 本地资讯池。
- Web AI 建议页新增主股票上下文，复用最近分析和股票索引候选，并改进表现统计零样本说明。
- DecisionSignal 将 `decision_profile` 升级为正式 nullable 字段，统一 same-profile 查询、去重、续期和失效语义，并保持 create metadata `null` 兼容与 SQLite 幂等回填诊断。
- 设置页移动端分类导航改为横向滚动列表并保证设置内容首屏可见，桌面端保留分类说明并收紧字段布局层级与间距。
- 新增 #1743 Phase 6a 内部 DSA Tool Surface 契约，统一工具 schema、stock scope fail-closed guard、结构化错误、审计摘要和脱敏诊断边界，并明确外部 AgentBackend 工具能力仍需 wire-level probe 证明。
- `src/services/analysis_service.py` 在 `report` 详情层新增 `details.raw_result` 回填，补齐与 API/历史详情的报告载荷一致性；不改变 provider、model、Base URL 或配置迁移语义。

### 修复

- 按股票代码删除历史记录时分批清理全部匹配项，并拒绝空白代码，避免超过 10000 条后残留记录或无筛选删除。
- 市场结构概念排行为空或超时时复用本轮负结果，避免批量个股分析重复请求同一概念排行数据源。
- Windows/macOS 桌面后端打包显式收集并校验 AkShare `file_fold/calendar.json`，避免发行包因缺少交易日历 package data 导致热点题材和选股日线增强降级。
- 邮件、Telegram 与报告共享的 DecisionSignal 摘要完整展示已脱敏的理由，避免固定 120 字符在句中无提示截断；Telegram 按最终 Markdown payload 长度安全分片。
- 推送报告、Jinja 报告与历史 Markdown 导出复用 Web/API 的评分-action 口径：高分但旧 `operation_advice` 仍为持有且无降级原因时，建议文案与三类统计展示为买入；有明确 guardrail reason 时继续保留持有/观望。
- WebUI 启动时显式 `--host` / `--port` 不再被 `.env` 中的 `WEBUI_HOST` / `WEBUI_PORT` 覆盖，未传 CLI 参数时统一使用解析后的运行时配置。
- Web 首页今日状态与排行使用带时区偏移的历史时间戳和完整分页数据，在查询失败、跨服务器时区边界或任务完成刷新时保持安全且准确。
- Web 首页 stock bar 刷新序列化：并发或乱序返回时仅最新请求可清除 `stockBarRefreshFailed`，避免旧响应覆盖任务完成后的刷新结果。
- Web 持仓页首屏快照改用 `include_realtime=false` 快速估值，跳过逐票实时行情预取后先展示持仓列表，避免外部实时行情源变慢时长时间空白等待。
- 修复任务状态接口重建报告动作字段时把合法情绪分 `0` 当成空值的问题，确保低分报告能按评分口径纠正为卖出建议。
- 修复 Agent 流式回复在未收到完成事件就断开时被显示为“（无内容）”的问题，改为提示流式响应中断并保留用户消息。
- 修复桌面端 `WEBUI_HOST=*` / `WEBUI_HOST=[::]` 会被原样传给端口探测和后端启动导致无法监听的问题，启动前分别规范化为 `0.0.0.0` / `::`。

### 文档

- 在 README 快速开始中补充行情数据源配置说明（`TUSHARE_TOKEN` / Longbridge），明确未配置时仍可使用 AkShare、Baostock、YFinance 等免费兜底源，并同步中英文完整指南。

## 历史版本（≤ 3.25.0，已压缩）

> 2026-07-03 及更早的版本已压缩为一句话摘要。完整明细见本文件的 git 历史（`git log --follow -- docs/CHANGELOG.md`）或对应时期的提交。

- **3.25.0**（2026-07-03）— 新增 `claude_code_cli`、`opencode_cli` generation-only 本地 CLI backend；台股报告完整接入三大法人资料 等
- **3.24.1**（2026-06-28）— 修正 Longbridge SDK 版本约束为按平台选择可安装版本 等
- **3.24.0**（2026-06-28）— 扩展台股、日股、韩股市场支持；新增 GenerationBackend 抽象、`codex_cli` 本地 CLI backend、reserved Hermes 本地 HT 等
- **3.23.0**（2026-06-20）— DecisionSignal 贯通报告提取、Web 展示、反馈/后验、告警通知和组合风险；新增合规 RSS/Atom 与 NewsNow 资讯源情报池 等
- **3.22.0**（2026-06-13）— 新增 DecisionSignal 独立存储与 API、运行流快照 API 和 Web 运行流视图；AlphaSift 热点题材链路升级为新版合约 等
- **3.21.0**（2026-06-07）— 新增 Web UI 中英文界面语言切换和飞书 App Bot 通知模式；大盘复盘报告、历史入口和个股栏继续收口到结构化数据与统一 Markdown/GFM 渲染 等
- **3.20.0**（2026-06-03）— 新增 AlphaSift 选股入口、自动安装与稳定适配层；完善个股历史、自选队列、市场阶段与 AnalysisContextPack 可见性 等
- **3.19.0**（2026-05-29）— 落地 #1391 Phase 1 运行诊断最小链路：任务/SSE 追加 trace_id；告警中心新增 P7 大盘红绿灯结构化规则 等
- **3.18.0**（2026-05-21）— 告警中心扩展到 P2-P6；个股分析支持策略选择 等
- **3.17.1**（2026-05-16）— 桌面端 Windows / macOS 打包脚本显式关闭 electron-builder 自动发布 等
- **3.17.0**（2026-05-16）— 新增 Alert API MVP；通知网关新增 ntfy 与 Gotify 一等渠道 等
- **3.16.0**（2026-05-10）— Web 首页新增“大盘复盘”触发入口、任务轮询与完成后报告直出；新增通知路由策略 等
- **3.15.0**（2026-05-05）— LLM 渠道配置体验继续升级：新增 Anspire OpenAI-compatible 网关接入；Web LLM 配置检测更可诊断：细分错误 reason 等
- **3.14.2**（2026-04-30）— 大盘复盘扩展到港股；问股与 Agent 链路增强配置缺失、决策 fallback 和多策略选择体验 等
- **3.14.1**（2026-04-26）— [测试] 修正大盘复盘 prompt 测试对“明日交易计划”标题的断言 等
- **3.14.0**（2026-04-26）— 📊 **大盘复盘升级为盘后工作台式结构** — A 股复盘固定输出盘面温度、指数明细、板块 Top 表、新闻催化、明日交易计划和风险提示；🖥️ **桌面端新增 GitHub Release 更新提醒** — Windows/macOS 桌面端启动后自动检测新版本 等
- **3.13.0**（2026-04-21）— 🌉 **长桥 OpenAPI 数据源接入** — 美股/港股行情优先使用 Longbridge；📈 **Tushare 港股全链路扩展** — 港股日线通过 `hk_daily` 获取 等
- **3.12.0**（2026-04-01）— 📊 **回测页新增"次日验证"视图** — 可按股票与日期范围查看 AI 预测 vs 次日实际涨跌；🔧 **LLM 接入体验简化** — 用户侧文案统一收口为"主模型 / 备选模型 / 模型渠道" 等
- **3.11.0**（2026-03-27）— 🎨 **Web 工作台 UI 统一与双主题升级**（设计 token / 输入表面 / 状态表达收口）；🤖 **Bot / Agent 能力重新补回主分支**（`/history`、`/strategies`、`/research`） 等
- **3.10.1**（2026-03-24）— 🔔 **Web 端分析推送通知开关**（#808）— 首页分析按钮旁新增「推送通知」复选框；🖥️ **问股 / 回测页面布局与壳层协同优化** — 统一 Chat / Backtest 页面容器、共享 UI 状态和跟随问答交互路径 等
- **3.10.0**（2026-03-24）— 🔎 **自动补全与索引工具扩展到三市场** — 补全索引生成链路现在同时覆盖 A 股、港股、美股；🖥️ **Dashboard 与报告查看体验继续收口** — 首页 Dashboard 面板、状态边界、字体层级和完整报告表格密度完成一轮统一 等
- **3.9.0**（2026-03-20）— 🤖 **模型链路与报告语言更灵活** — Agent 现在可以通过 `AGENT_LITELLM_MODEL` 独立选择模型链路；🔎 **首页分析体验完成一轮闭环优化** — 首页新增 A 股自动补全 等
- **3.8.0**（2026-03-17）— 🎨 **Web 界面完成一轮骨架升级** — 新的 App Shell、侧边导航、主题能力、登录与系统设置流程已经串成统一体验；📈 **分析上下文继续补强** — 美股新增社交舆情情报 等
- **3.7.0**（2026-03-15）— 💼 **持仓管理 P0 全功能上线**（#677）；🎨 **前端设计系统与原子组件库**（#662） 等
- **3.6.0**（2026-03-14）— 📊 **Web UI Design System**（双主题架构与终端风原子组件）；📊 **UI Components Refactoring**（引入 clsx / tailwind-merge） 等
- **3.5.0**（2026-03-12）— 📊 **Web UI 完整报告抽屉**（Fixes #214）；📊 **LLM 成本追踪**（分析/Agent/大盘复盘全覆盖） 等
- **3.4.10**（2026-03-07）— 📊 **个股日报汇总 Markdown 转图片**（#455, #535）；🖼️ `MD2IMG_ENGINE=markdown-to-file` 引擎可选（#455） 等
- **3.4.9**（2026-03-06）— 🧠 **结构化配置校验**（`ConfigIssue` 数据类）；🖼️ **视觉模型配置**（`VISION_MODEL` / `VISION_PROVIDER_PRIORITY`） 等
- **3.4.8**（2026-03-02）— 🐛 修复桌面端 exe 启动 `FileNotFoundError` 崩溃（PyInstaller 打包路径） 等
- **3.4.7**（2026-02-28）— 🧠 **中美市场策略蓝图系统**（#395，大盘复盘 prompt 增强） 等
- **3.4.0**（2026-02-27）— 📡 **LiteLLM Direct Integration + Multi API Key Support** 等
- **3.3.22**（2026-02-26）— 💬 **聊天历史持久化**（Fixes #400, #414）；🎨 项目 VI 资产（logo / 矢量 / banner，#425） 等
- **3.3.12**（2026-02-24）— 📈 **盘中实时技术指标**（Issue #234, #397）；🤖 **Agent 策略问答**（#367，ReAct 全链路 + 11 套策略 YAML） 等
- **3.2.11**（2026-02-23）— 🐛 **StockTrendAnalyzer 从未执行** (Issue #357)
- **3.2.10**（2026-02-22）— ⚙️ 支持 `RUN_IMMEDIATELY` 配置项 等
- **3.2.9**（2026-02-22）— 🐛 **ETF 分析仅关注指数走势**（Issue #274）
- **3.2.8**（2026-02-21）— 🐛 **BOT 与 WEB UI 股票代码大小写统一**（Issue #355）
- **3.2.7**（2026-02-20）— 🔐 **Web 页面密码验证**（Issue #320, #349）
- **3.2.6**（2026-02-20）— 修复美股（如 ADBE）技术指标矛盾：akshare 美股复权数据异常 等
- **3.2.5**（2026-02-19）— 🌍 **大盘复盘可选区域**（Issue #299）
- **3.2.4**（2026-02-18）— 🐛 **统一美股数据源为 YFinance**（Issue #311）
- **3.2.3**（2026-02-18）— 🐛 **标普500实时数据缺失**（Issue #273）
- **3.2.2**（2026-02-16）— 📊 **PE 指标支持**（Issue #296）；📰 **新闻时效性筛查**（Issue #296） 等
- **3.2.1**（2026-02-16）— 🔧 **东财接口补丁可配置开关**
- **3.2.0**（2026-02-15）— 🔒 **CI 门禁统一（P0）**；📦 **发布链路收敛（P0）** 等
- **3.1.13**（2026-02-15）— 📊 **仅分析结果摘要**（Issue #262）
- **3.1.12**（2026-02-15）— 📧 **个股与大盘复盘合并推送**（Issue #190）
- **3.1.11**（2026-02-15）— 🤖 **Anthropic Claude API 支持**（Issue #257）；📷 **从图片识别股票代码**（Issue #257） 等
- **3.1.10**（2026-02-15）— ⚙️ **立即运行配置**（Issue #332）；🐛 修复 Docker 构建问题
- **3.1.9**（2026-02-14）— 🔌 **东财接口补丁机制**
- **3.1.8**（2026-02-14）— 🔐 **Webhook 证书校验开关**（Issue #265）
- **3.1.7**（2026-02-14）— 🐛 修复包导入错误（package import error）
- **3.1.6**（2026-02-13）— 🐛 修复 `news_intel` 中 `query_id` 不一致问题
- **3.1.5**（2026-02-13）— 📷 **Markdown 转图片通知**（Issue #289）
- **3.1.4**（2026-02-12）— 📧 **股票分组发往不同邮箱**（Issue #268）
- **3.1.3**（2026-02-12）— 🐛 修复 Docker 内运行时通过页面修改配置报错 `[Errno 16] Device or resource busy` 的问题
- **3.1.2**（2026-02-11）— 🐛 修复 Docker 一致性问题 等
- **3.1.1**（2026-02-11）— ♻️ `API_HOST` → `WEBUI_HOST`：Docker Compose 配置项统一
- **3.1.0**（2026-02-11）— 📊 **ETF 支持增强与代码规范化**
- **3.0.5**（2026-02-08）— 📊 大盘复盘报告注入结构化数据（涨跌统计、指数表格、板块排名）；🔍 搜索结果 TTL 缓存（500 条上限） 等
- **3.0.4**（2026-02-07）— 📈 **回测引擎** (PR #269)
- **3.0.3**（2026-02-07）— 🐛 修复狙击点位数据解析错误问题 (PR #271)
- **3.0.2**（2026-02-06）— ✉️ 可配置邮件发送者名称 (PR #272)；🌐 外国股票支持英文关键词搜索
- **3.0.1**（2026-02-06）— 🐛 修复 ETF 实时行情获取、市场数据回退、企业微信消息分块问题 等
- **3.0.0**（2026-02-06）— 🗑️ **移除旧版 WebUI** 等
- **2.3.0**（2026-02-01）— 🇺🇸 **增强美股支持** (Issue #153)
- **2.2.5**（2026-02-01）— 🤖 **AstrBot 消息推送** (PR #217)
- **2.2.4**（2026-02-01）— ⚙️ **可配置数据源优先级** (PR #215)
- **2.2.3**（2026-01-31）— 📦 更新 requirements.txt 等
- **2.2.2**（2026-01-31）— 🐛 修复代理配置区分大小写问题 (fixes #211)
- **2.2.1**（2026-01-31）— 🐛 **YFinance 兼容性修复** (PR #210, fixes #209)
- **2.2.0**（2026-01-31）— 🔄 **多源回退策略增强**
- **2.1.14**（2026-01-31）— 📝 更新 README 和优化 auto-tag 规则
- **2.1.13**（2026-01-31）— 🐛 **Tushare 优先级与实时行情** (Fixed #185)
- **2.1.12**（2026-01-30）— 🌐 修复代理配置在某些情况下的区分大小写问题 等
- **2.1.11**（2026-01-30）— 🚀 **飞书消息流优化** (PR #192)
- **2.1.10**（2026-01-30）— 📦 合并 PR #154 贡献
- **2.1.9**（2026-01-30）— 💬 **微信文本消息支持** (PR #137)
- **2.1.8**（2026-01-30）— 🐛 修正日志中 API 提供商显示错误 (PR #197)
- **2.1.7**（2026-01-30）— 🌐 禁用本地环境的代理设置 等
- **2.1.6**（2026-01-29）— 📡 **Pytdx 数据源 (Priority 2)**；🏷️ **多源股票名称解析** 等
- **2.1.5**（2026-01-29）— 📡 新增 Pytdx 数据源和多源股票名称解析功能
- **2.1.4**（2026-01-29）— 📝 更新赞助商信息
- **2.1.3**（2026-01-28）— 🐛 修复 WebUI 无法输入美股代码问题
- **2.1.2**（2026-01-27）— 🐛 修复个股分析推送失败和报告路径问题 (fixes #166) 等
- **2.1.1**（2026-01-26）— 🔧 添加 GitHub Actions auto-tag 工作流；📡 添加 yfinance 兜底数据源及数据缺失警告
- **2.1.0**（2026-01-25）— 🇺🇸 **美股分析支持**；📈 **MACD 和 RSI 技术指标** 等
- **2.0.0**（2026-01-24）— 🇺🇸 **美股分析支持**；🤖 **机器人命令交互** (PR #113) 等
- **1.6.0**（2026-01-19）— 🖥️ WebUI 管理界面及 API 支持（PR #72）；⚙️ GitHub Actions 配置灵活性增强（#79） 等
- **1.5.0**（2026-01-17）— 📲 单股推送模式（#55）；🔐 自定义 Webhook Bearer Token 认证（#51） 等
- **1.4.0**（2026-01-17）— 📱 Pushover 推送支持（PR #26）；🔍 博查搜索 API 集成（PR #27） 等
- **1.3.0**（2026-01-12）— 🔗 自定义 Webhook 支持
- **1.2.0**（2026-01-11）— 📢 多渠道推送支持；统一使用 `NOTIFICATION_URL` 配置 等
- **1.1.0**（2026-01-11）— 🤖 OpenAI 兼容 API 支持
- **1.0.0**（2026-01-10）— 🎯 AI 决策仪表盘分析；📊 大盘复盘功能 等
