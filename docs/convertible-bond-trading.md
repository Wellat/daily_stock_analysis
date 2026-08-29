# 可转债实盘交易（QMT 对接）

本文档说明本项目可转债实盘交易的完整链路、订单表字段契约、API 契约，以及迅投 QMT 侧的对接方式。

> 本文同时作为 DSA 可转债实盘能力补齐方案草稿。下面“目标落地方案”描述计划中的自动实盘闭环，当前代码仍以文档后半部分的 MVP 接口为准。实现过程中请同步更新本节与 API 契约，避免方案和现状继续漂移。

## 目标落地方案（待实施）

### 目标与边界

DSA 负责行情/因子获取、策略计算、目标组合、差额调仓、风控、订单持久化和执行状态记录；QMT 负责读取待执行订单、实际下单、回传全成/拒单结果以及上报账户持仓。

第一阶段约束如下：

- 单个 QMT 账户、单个启用中的策略实例；数据模型预留多账户、多策略扩展字段。
- QMT 持仓和执行状态是实盘真相源；Portfolio 继续用于研究和纸面账户，不自动承接实盘成交。
- 每个交易日下午 14:30 自动运行低溢价率轮动策略，按目标组合与当前 QMT 持仓的差额生成订单。
- 订单第一阶段全部使用市价单，数量单位为“张”，只支持全成或拒单，不在 DSA 累计部分成交。
- 实盘默认关闭；启用后由现有 runtime scheduler 负责调度。任何关键数据或风控校验失败均不生成订单（fail-closed）。

### 运行链路

```text
14:30 runtime scheduler
    ↓
拉取并落库最新可转债行情/溢价率/剩余规模
    ↓
交易日、数据新鲜度和可交易状态校验
    ↓
读取 QMT 最新持仓，计算目标组合
    ↓
计算目标持仓与当前持仓的买卖差额
    ↓
资金、仓位、最小单位、重复运行等风控校验
    ↓
写入 live_strategy_run / rebalance_batch / trading_orders
    ↓
QMT 拉取 pending 订单并下单
    ↓
QMT 回调 submitted → filled 或 rejected
    ↓
DSA 更新订单、批次和运行状态；持续接收 QMT 持仓快照
```

同一账户、同一交易日、同一策略版本只能产生一个有效调仓批次。运行 UID、批次 UID、策略版本和数据快照时间必须贯穿所有记录，支持审计、重跑和故障定位。

### 计划新增的数据对象

- `live_strategy_configs`：策略 ID/版本、账户、标的池、最大持仓、目标资金或仓位、最小交易单位、排除规则、启停状态和调度配置。
- `live_strategy_runs`：运行 UID、交易日、触发时间、数据快照时间、状态、目标组合摘要、风控结果和错误信息。
- `live_rebalance_batches`：运行 ID、账户、目标组合、当前组合、买卖差额、批次状态及汇总信息。
- `trading_orders` 扩展 `live_run_id`、`rebalance_batch_id`、`signal_id`、稳定的 `client_order_key` 等关联字段；保留现有 `order_uid` 作为 QMT 幂等键。
- 如需保留完整回调审计，再增加执行事件表，保存原始回调、接收时间和幂等处理结果；订单表只保存最终状态。

### 订单和风控约定

订单状态保持：

```text
pending -> submitted -> filled
pending -> rejected
pending -> cancelled
```

- QMT 认领后先回调 `submitted`，此后订单不再出现在 pending 列表，避免重复下单。
- `filled`、`rejected`、`cancelled` 为终态；重复回调按订单 UID/客户端订单键幂等返回，不覆盖已有结果。
- DSA 在生成订单前校验交易日、行情新鲜度、数据完整性、标的状态、可用资金、单债/总仓上限、可用持仓、最小交易单位和重复运行。
- QMT 上报的是账户全量持仓；可转债策略只读取 `strategy_lab_cb_basic` 中 `market=cn` 的债券代码，股票、基金等非可转债持仓不会参与目标组合或生成卖单。
- 第一阶段使用市价单，`limit_price` 为空；成交价格以 QMT 回报为准。

### 计划 API 与 Web 工作台

计划新增实盘控制面：

- `GET/PUT /api/v1/live-strategy/config`：读取和保存实盘策略配置。
- `POST /api/v1/live-strategy/runs/preview`：只计算目标组合和差额，不写 QMT 订单。
- `POST /api/v1/live-strategy/runs`：执行一次实盘策略运行并生成调仓批次。
- `GET /api/v1/live-strategy/runs`、`/{run_id}`、`/{run_id}/rebalance`、`/{run_id}/orders`：查询运行、目标组合、差额和订单。
- `POST /api/v1/live-strategy/runs/{run_id}/cancel`：取消仍处于 pending 的订单。

现有 `/trading` 页面扩展为实盘工作台，包含策略配置、最近运行、调仓预览、订单批次、QMT 执行状态、持仓快照时间和风险/异常提示；现有订单与持仓 Tab 继续复用。页面上展示策略版本、运行 UID 和批次 UID，保证可追溯。

### 分阶段实施与验收

1. 抽取可复用的目标组合/差额调仓服务，补齐模型、仓储、迁移和离线单元测试。
2. 接入 runtime scheduler、行情快照落库、QMT 持仓读取和基础风控，完成 preview/运行 API。
3. 扩展 trading order 的批次关联、认领幂等和 QMT 回调校验，完成模拟 QMT 端到端测试。
4. 完成 Web 实盘工作台、配置管理、运行详情和异常展示。
5. 更新 QMT 接入文档，以模拟行情和模拟账户完成“运行 → 调仓 → 拉单 → 回调 → 持仓”的重复执行验收。

暂不纳入第一阶段：部分成交撮合、完整审批流、自动对账中心、券商直连、多账户并行调度和完整风控中心。

## 链路概览

```
本项目生成交易信号（MVP：手动/API 创建待交易指令）
        │
        ▼
写入数据库表 trading_orders（status=pending）
        │
        ▼
QMT 定时轮询 GET /api/v1/trading/qmt/pending
        │
        ▼
QMT 在本机执行下单（buy/sell）
        │
        ▼
QMT 通过 HTTP 回调 POST /api/v1/trading/qmt/orders/{id}/callback
        │
        ▼
本项目更新订单状态（submitted/filled/rejected）与执行结果
```

当前代码仍处于 MVP：交易信号主要通过手动/API 创建，策略引擎尚未自动生成上述实盘调仓批次。目标落地方案完成后，应将本段更新为实际实现状态。

## 数据表 `trading_orders`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | 主键 |
| `order_uid` | String(64), unique | 幂等键，服务端生成 `qmt_<uuid>` |
| `symbol` | String(16) | 可转债代码（6 位数字，如 `113002`） |
| `market` | String(8) | 固定 `cn` |
| `instrument_type` | String(32) | 固定 `convertible_bond` |
| `side` | String(8) | `buy` / `sell` |
| `quantity` | Float | 数量（张） |
| `order_type` | String(16) | `limit` / `market` |
| `limit_price` | Float | 限价（`limit` 必填） |
| `status` | String(16) | 见状态机 |
| `qmt_order_id` | String(64) | QMT 侧订单号（回调回写） |
| `filled_quantity` | Float | 成交数量 |
| `filled_price` | Float | 成交价 |
| `error_message` | Text | 失败原因 |
| `source` | String(32) | `api` / `manual` |
| `reason` | Text | 信号理由 |
| `created_at` / `updated_at` | DateTime | |
| `submitted_at` / `completed_at` | DateTime | 认领 / 终态时间 |

数量单位为「张」，QMT 侧如需按「手」下单请自行换算（1 手 = 10 张）。

## 状态机

```
pending ──> submitted ──> filled
   │            └───────> rejected
   └─────────────────────> filled / rejected   (跳过 submitted 的简化流程)
   └──────> cancelled                          (admin 取消)
```

- `submitted` 为可选中间态：QMT 拉取后、下单前可先回调 `submitted` 避免重复执行，也可直接回调终态。
- `filled` / `rejected` / `cancelled` 为终态，不可再变更。
- 重复回调终态为幂等操作，返回当前记录，不覆盖已有结果。

## API 契约

### 管理端（管理员会话 Cookie 鉴权，`ADMIN_AUTH_ENABLED=true` 时生效）

- `POST /api/v1/trading/orders` 创建待交易指令

```json
{
  "symbol": "113002",
  "side": "buy",
  "quantity": 10,
  "order_type": "limit",
  "limit_price": 120.5,
  "source": "api",
  "reason": "双低策略信号"
}
```

- `GET /api/v1/trading/orders?status=pending&page=1&limit=20` 分页查询
- `POST /api/v1/trading/orders/{id}/cancel` 取消 `pending` 指令

### QMT 端（HTTP 头 `X-QMT-Token` 鉴权）

- `GET /api/v1/trading/qmt/pending` 拉取待执行指令

```bash
curl -s localhost:8000/api/v1/trading/qmt/pending \
  -H 'X-QMT-Token: <token>'
```

返回：

```json
{
  "items": [
    {
      "id": 1,
      "order_uid": "qmt_xxx",
      "symbol": "113002",
      "side": "buy",
      "quantity": 10,
      "order_type": "limit",
      "limit_price": 120.5
    }
  ]
}
```

- `POST /api/v1/trading/qmt/orders/{id}/callback` 回写执行结果

```bash
curl -s -X POST localhost:8000/api/v1/trading/qmt/orders/1/callback \
  -H 'Content-Type: application/json' \
  -H 'X-QMT-Token: <token>' \
  -d '{"status":"filled","qmt_order_id":"q123","filled_quantity":10,"filled_price":120.4}'
```

回调请求体：

```json
{
  "status": "filled",
  "qmt_order_id": "q123",
  "filled_quantity": 10,
  "filled_price": 120.4,
  "error_message": null
}
```

`status` 取值：`submitted` / `filled` / `rejected`。当前 DSA 只接受全成或拒单：`filled` 必须携带与指令数量相等的 `filled_quantity` 和正数 `filled_price`；`rejected` 必须携带 `error_message`。

## QMT 鉴权配置

- 环境变量 `QMT_API_TOKEN` 用于 QMT 拉取/回调鉴权，HTTP 头 `X-QMT-Token` 携带。
- 未配置时默认放行（仅适用于开发/单机环境）；生产环境务必设置高熵随机值：

```bash
openssl rand -hex 32
```

## 已知风险与限制

- **重复执行风险**：MVP 中 `submitted` 为非强制中间态；若 QMT 下单后回调失败，同一指令可能被再次拉取并重复执行。建议 QMT 侧自行记录已处理的 `order_uid` 去重；强幂等认领（拉取即认领 + 超时回收）留待二期。
- **鉴权默认放行**：未配置 `QMT_API_TOKEN` 时 QMT 端点不校验 token，仅限可信内网使用。
- **SQLite 并发**：项目默认开启 WAL，降低 QMT 并发回调与本地写库的锁竞争，但高并发场景仍建议评估是否迁移到独立数据库。

## 回滚方式

移除 `api/v1/router.py` 中的 `trading` 路由注册、删除 `trading_orders` 表、还原 `api/middlewares/auth.py` 的 QMT 前缀豁免、还原 `.env.example` 与本说明文档，即可回到无该能力状态；不破坏现有信号 / 回测 / 数据同步链路。
