# 可转债实盘交易（QMT 对接）

本文档说明本项目可转债实盘交易的完整链路、订单表字段契约、API 契约，以及迅投 QMT 侧的对接方式。

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

MVP 阶段交易信号来源为「手动/API 创建」，暂不接入策略引擎自动出信号；自动出信号留待二期。

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

`status` 取值：`submitted` / `filled` / `rejected`。

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
