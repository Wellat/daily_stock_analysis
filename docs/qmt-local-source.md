# QMT 本地行情源接入说明

本文档说明 DSA 侧如何消费 QMT 行情桥（Redis）。桥接链路的原始契约（key 结构、字段、单位、故障场景）见 [`docs/qmt-quotes-link-01.md`](./qmt-quotes-link-01.md)，本文只描述 DSA 的接入实现与配置。

## 1. 链路概览

```
QMT 客户端（cb_quotes_bridge） ──写──▶ Redis localhost:6379 db0 ──读/写──▶ DSA
                                       qmt:hb        心跳（TTL 30s）
                                       qmt:quote:*   实时快照（30s 一档）
                                       qmt:daily:*   最近 300 根日K（不复权）
                                       qmt:daily_fq:* 最近 300 根日K（等比前复权，股票/ETF）
                                       qmt:cfg:*     订阅列表（DSA 唯一写入者）
```

DSA 侧两个消费面：

| 消费面 | 位置 | 说明 |
|--------|------|------|
| 数据源 fetcher | `data_provider/qmt_fetcher.py` | 实时快照 + 日K，接入统一 fallback 链 |
| 订阅管理 | `src/services/qmt_quotes_service.py` | 维护 `qmt:cfg:symbols`（页面 + 定时任务） |

## 2. 行情查询优先级（告警巡检受益）

- **实时行情**：`realtime_source_priority` 未显式配置且 `QMT_QUOTES_ENABLED=true` 时自动在最前面注入 `qmt` 令牌（先于 tushare/tencent 等）。心跳缺失或盘中快照停滞（见 §4）时该令牌静默跳过，链路滑到外部源。
- **日线**：`QmtFetcher` 以 `QMT_PRIORITY`（默认 0）进入日线 fallback 链最前。本地 300 根（股票/ETF 为前复权序列）能覆盖请求窗口时直接返回，盖不住时返回空帧滑落，不影响超长窗口指标计算。
- 支持范围：沪深 6 位码（A 股/ETF/可转债）。北交所（920xxx）、港股、美股不接入。

## 3. 订阅列表管理

`qmt:cfg:symbols` 由 DSA 单一写入（多实例并发写会互相覆盖，**按单实例部署设计**）。目标列表 = 三个来源的并集：

| 来源 | 开关（默认开） | 采集口径 |
|------|----------------|----------|
| 持仓 | `QMT_AUTO_INCLUDE_HOLDINGS` | 全部活跃资金账户的 cn 持仓（`PortfolioPosition.quantity > 0`；自动剔除港股、场外基金 `.OF`、北交所） |
| 告警 | `QMT_INCLUDE_ALERT_SYMBOLS` | 启用中告警规则：single_symbol 规则标的 + watchlist 规则展开的自选股（portfolio scope 已被持仓覆盖，market 为聚合口径，均跳过） |
| 手动 | `QMT_MANUAL_SYMBOLS` | 页面维护（写 .env），接受 `600519` / `600519.SH` 混写形态 |

同步入口：

- 定时任务 `qmt_subscription_sync`：每 `QMT_SUBSCRIPTION_SYNC_MINUTES` 分钟（默认 5），启动即跑一次；心跳可在 Web「行情数据 → 数据同步 → 定时任务总览」观测。
- 手动：页面「立即同步订阅」按钮（`POST /api/v1/qmt-quotes/subscription/sync`，返回新增/移除 diff）。

变更生效时序（QMT 侧）：新增标的秒级回填日K、≤30s 出现快照；移除标的快照 300s 后过期。

## 4. 数据口径与降级策略

- **实时快照**：`pvolume`（股/份/张）作为成交量口径。**交易时段内** `timetag` 距今超过 `QMT_STALE_SECONDS`（默认 120s）视为停滞，返回空让链路降级到外部源；非交易时段的定格快照是合法最新价，照常返回。
- **日K**：股票/ETF **优先前复权** `qmt:daily_fq:{code}`（`dividend_type=front_ratio`，等比前复权，与 akshare qfq 逐点一致；每日 15:10 全量重写，基准随除权动态漂移——DSA 只现算指标、不持久化 fq 序列），key 缺失时回落不复权 `qmt:daily:{code}`；**可转债无除权概念，QMT 不产生 fq key（以 key 存在性判断，非异常），恒用不复权 daily**。共 300 根，请求窗口盖不住时自动滑落外部源。成交量手→股换算：股票/ETF ×100，可转债 ×10。
- **Redis 不可用**：连接失败后进入 60s 退避（期间所有调用直接返回不可用，不刷日志）；`qmt:hb` 心跳缺失时 fetcher 对管理器报告不可用，日线/实时链路均跳过本源。未安装 `redis` 包同样整体优雅降级。

## 5. 配置项

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `QMT_QUOTES_ENABLED` | `true` | 总开关；关闭后不注册本地源也不跑订阅同步 |
| `QMT_REDIS_URL` | `redis://localhost:6379/0` | Redis 地址（启用 requirepass 后在此带密码） |
| `QMT_PRIORITY` | `0` | 日线 fallback 链中的优先级 |
| `QMT_STALE_SECONDS` | `120` | 盘中快照停滞降级阈值 |
| `QMT_AUTO_INCLUDE_HOLDINGS` | `true` | 订阅自动纳入持仓 |
| `QMT_INCLUDE_ALERT_SYMBOLS` | `true` | 订阅自动纳入告警标的 |
| `QMT_MANUAL_SYMBOLS` | 空 | 手动追加订阅（逗号分隔） |
| `QMT_SUBSCRIPTION_SYNC_MINUTES` | `5` | 订阅同步间隔 |

所有开关均"不配置可运行"：无 Redis / 无心跳 / 未装 redis 包时本地源自动不可用，系统行为与未引入本功能时一致。

## 6. 管理页面

Web「行情数据 → 数据同步」页顶部的「本地行情源（QMT 行情桥）」面板：

- 心跳状态（正常/离线/已停用/缺依赖）、Redis 地址、订阅数与各来源计数
- 三个开关（启用本地源 / 自动纳入持仓 / 自动纳入告警标的），写 `.env` 并热加载
- 订阅列表（含来源标签：持仓/告警/手动/残留）
- 手动订阅编辑与「立即同步订阅」

API：`GET /api/v1/qmt-quotes/status`、`GET /api/v1/qmt-quotes/subscription`、`PUT /api/v1/qmt-quotes/subscription/manual`、`POST /api/v1/qmt-quotes/subscription/sync`、`PUT /api/v1/qmt-quotes/settings`。

## 7. 排障

| 现象 | 处置 |
|------|------|
| 面板显示「离线」 | `docker exec myredis redis-cli GET qmt:hb` 检查 QMT 策略与 Redis；DSA 与 Redis 必须同机或网络可达 |
| 面板显示「缺 redis 依赖」 | `pip install -r requirements.txt`（新增 `redis>=5.0`） |
| 订阅列表不更新 | 看「定时任务总览」中 `qmt_subscription_sync` 的最近运行；手动点「立即同步订阅」看报错 |
| 某标的无快照 | 确认代码在订阅列表且为沪深 6 位码；新加入 ≤30s 出现快照；无效/退市代码 QMT 不写 key |
| 巡检仍走外部源 | 确认标的已订阅、心跳正常；盘中停滞 >120s 会自动降级（属预期） |
