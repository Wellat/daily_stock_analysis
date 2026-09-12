# 可转债实盘交易对接文档（QMT 脚本开发者）

本文档面向迅投 QMT 脚本开发者，说明如何从本项目读取「待交易指令」、在 QMT 中执行下单，并将执行结果回写回来。

## 1. 背景与目标

本项目负责产生可转债交易信号，并把信号落成「待交易指令」。QMT 侧需要完成三件事：

1. 定时拉取待执行的交易指令；
2. 在 QMT 中按指令执行下单；
3. 将执行结果通过 HTTP 回调回本项目，更新指令状态。

整条链路：

```
本项目生成交易信号 → 写入待交易指令（status=pending）
        ↓
QMT 轮询 GET /qmt/pending 拉取
        ↓
QMT 下单执行
        ↓
QMT 回调 POST /qmt/orders/{id}/callback 回写结果（filled/rejected）
```

## 2. 前置条件

- **服务地址**：由部署方提供，默认 `http://<host>:8000`，接口前缀统一为 `/api/v1`。下文示例用 `http://localhost:8000` 占位。
- **鉴权**：QMT 拉取与回调接口使用请求头 `X-QMT-Token` 鉴权。
  - token 由部署方通过环境变量 `QMT_API_TOKEN` 配置。
  - 若部署方未配置（开发/单机环境），则无需携带该头也能访问。
  - 生产环境必须携带部署方给的正确 token。

## 3. 接口说明

### 3.1 拉取待执行指令

`GET /api/v1/trading/qmt/pending`

返回当前所有 `pending` 状态的指令（按创建时间升序）。

请求：

```bash
curl -s http://localhost:8000/api/v1/trading/qmt/pending \
  -H 'X-QMT-Token: <token>'
```

响应示例：

```json
{
  "items": [
    {
      "id": 1,
      "order_uid": "qmt_9f2c...",
      "symbol": "113002",
      "side": "buy",
      "quantity": 10,
      "order_type": "limit",
      "limit_price": 120.5
    }
  ]
}
```

字段含义：

| 字段 | 说明 |
|---|---|
| `id` | 指令主键，回调时用于定位该指令 |
| `order_uid` | 全局唯一幂等键，可用于 QMT 侧去重 |
| `symbol` | 可转债代码（6 位数字，如 `113002`） |
| `side` | 买卖方向：`buy` / `sell` |
| `quantity` | 数量，单位「张」 |
| `order_type` | 订单类型：`limit` / `market` |
| `limit_price` | 限价（`limit` 单有值，`market` 单为 `null`） |

### 3.2 回写执行结果

`POST /api/v1/trading/qmt/orders/{id}/callback`

`{id}` 为 3.1 中拉取到的 `id`。

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | string | 是 | 执行结果：`submitted` / `filled` / `rejected` |
| `qmt_order_id` | string | 否 | QMT 侧生成的订单号（成交/失败时建议带上） |
| `filled_quantity` | number | 否 | 成交数量 |
| `filled_price` | number | 否 | 成交价 |
| `error_message` | string | 否 | 失败原因（`rejected` 时建议带上） |

示例（成交成功）：

```bash
curl -s -X POST http://localhost:8000/api/v1/trading/qmt/orders/1/callback \
  -H 'Content-Type: application/json' \
  -H 'X-QMT-Token: <token>' \
  -d '{
    "status": "filled",
    "qmt_order_id": "QMT202608290001",
    "filled_quantity": 10,
    "filled_price": 120.4
  }'
```

示例（下单失败）：

```bash
curl -s -X POST http://localhost:8000/api/v1/trading/qmt/orders/1/callback \
  -H 'Content-Type: application/json' \
  -H 'X-QMT-Token: <token>' \
  -d '{
    "status": "rejected",
    "qmt_order_id": "QMT202608290001",
    "error_message": "资金不足"
  }'
```

### 3.3 上报持仓

`POST /api/v1/trading/qmt/positions`

QMT 在每日收盘后，将账户全部持仓同步给本项目。dsa 服务端据此接口实现持仓数据的接收与存储。

请求体示例：

```json
{
  "account": "testS",
  "positions": [
    {
      "symbol": "000858",
      "volume": 900,
      "can_use_volume": 900,
      "open_price": 103.657,
      "float_profit": 123.4
    }
  ]
}
```

字段含义：

| 字段 | 类型 | 说明 |
|---|---|---|
| `account` | string | 资金账号 |
| `positions` | array | 持仓列表 |
| `positions[].symbol` | string | 证券代码（6 位数字，不带市场后缀，如 `000858`、`113002`） |
| `positions[].volume` | number | 总持仓数量 |
| `positions[].can_use_volume` | number | 可用数量（可卖） |
| `positions[].open_price` | number | 持仓成本价 |
| `positions[].float_profit` | number | 浮动盈亏 |

设计说明：

- **触发时机**：实盘在每日收盘后（默认 15:05）由 QMT 定时器 `run_time` 触发一次；回测模式下由 `handlebar` 逐交易日触发（用于验证采集逻辑）。
- **数据范围**：账户内全部持仓（股票、可转债等），不做过滤。
- **symbol 约定**：与 3.1 拉取指令中的 `symbol` 保持一致，均为 6 位数字代码，**不带市场后缀**（`.SH`/`.SZ`）。QMT 侧采集时已去掉后缀。
- **幂等**：同一交易日的重复上报，服务端建议按 `account + symbol` 去重或覆盖，以最后一次为准。

### 3.4 上报当日成交记录

`POST /api/v1/trading/qmt/deals`

QMT 在每日收盘后，将账户当日全部成交记录同步给本项目。dsa 服务端据此接口实现成交数据的接收，并自动逐笔入账到 Portfolio 持仓账本（持仓数量、成本、盈亏随下一次持仓快照查询自动更新）。

请求体示例：

```json
{
  "account": "testS",
  "deals": [
    {
      "account": "testS",
      "symbol": "113002",
      "name": "工行转债",
      "side": "buy",
      "price": 120.4,
      "volume": 10,
      "amount": 1204.0,
      "fee": 0.0,
      "trade_id": "1000123",
      "order_sys_id": "202609120001",
      "trade_time": "2026-09-12 14:45:03",
      "xt_trade": "1"
    }
  ]
}
```

字段含义：

| 字段 | 类型 | 说明 |
|---|---|---|
| `account` | string | 资金账号 |
| `deals` | array | 当日成交列表 |
| `deals[].account` | string | 资金账号（QMT `m_strAccountID` 透传） |
| `deals[].symbol` | string | 证券代码（6 位数字，不带市场后缀，如 `113002`） |
| `deals[].name` | string | 证券名称，获取失败时为空字符串 |
| `deals[].side` | string | 买卖方向：`buy` / `sell`（依据 QMT `m_nOffsetFlag`：48 买入、49 卖出），无法识别时为 `unknown` |
| `deals[].price` | number | 成交均价 |
| `deals[].volume` | number | 成交数量（张） |
| `deals[].amount` | number | 成交金额（元） |
| `deals[].fee` | number | 手续费（元） |
| `deals[].trade_id` | string | 成交编号 |
| `deals[].order_sys_id` | string | 委托合同编号（QMT 字段缺失时为空字符串） |
| `deals[].trade_time` | string | 成交时间（QMT `m_strTradeDate` + `m_strTradeTime` 拼接，格式因券商而异） |
| `deals[].xt_trade` | string | 是否迅投交易（QMT `m_strXTTrade` 原始字符串透传） |

响应示例：

```json
{
  "account": "testS",
  "account_id": 12,
  "account_name": "testS",
  "account_created": true,
  "received": 1,
  "inserted_count": 1,
  "duplicate_count": 0,
  "skipped_count": 0,
  "failed_count": 0,
  "cash_entries": 1,
  "items": [
    {
      "trade_id": "1000123",
      "symbol": "113002",
      "side": "buy",
      "status": "inserted",
      "trade_date": "2026-09-12",
      "trade_time_parsed": true,
      "error": null
    }
  ]
}
```

设计说明：

- **触发时机**：与 3.3 上报持仓一致，每日收盘后触发一次；也可在盘中多次上报增量成交，服务端按成交编号幂等去重。
- **幂等**：服务端按 `account + trade_id` 幂等（入账键 `qmt_deal:{account}:{trade_id}`），重发同一批成交安全，已入账的返回 `duplicate`，不会重复计入持仓。
- **入账联动**：成交入账到与资金账号同名的 Portfolio 账户（不存在则自动创建），持仓、成本、已实现/浮动盈亏由账本重放自动更新；每笔入账自动配平等额现金流水，账户现金余额恒为 0、总权益=持仓市值。
- **逐笔结果**：`side=unknown` 的成交返回 `skipped`（仅记日志不入账）；超出持仓数量的卖出等失败笔返回 `failed` 并附原因，不阻断批内其余成交；参数不合法（如 symbol 非 6 位数字）整批返回 400。
- **trade_time 解析**：支持 `2026-09-12 14:45:03`、`20260912 14:45:03`、`20260912144503` 等常见拼接格式；无法解析时回退上报当日入账，响应中 `trade_time_parsed=false` 可见。
- **与持仓快照同步互斥**：同一资金账号只能在「持仓快照差额同步」（见 dsa 持仓页「QMT 持仓同步」）与「本成交上报自动入账」两种来源中选其一，混用会双计持仓。成交上报只覆盖接入后的交易，接入前的存量持仓需先由快照同步或手工录入建底仓（此后不再对同一账号使用快照同步）。
- **原始数据**：服务端不落原始成交表，上报内容以日志留档（`[QmtDeal]` 前缀），入账记录可通过账本交易的 `trade_uid` / `note`（`qmt_deal:{account}`）反查。

## 4. 状态约定

指令状态流转：

```
pending ──> submitted ──> filled
   │            └───────> rejected
   └─────────────────────> filled / rejected   (跳过 submitted 的简化流程)
```

- `pending`：待执行（QMT 拉取到的就是这种状态）。
- `submitted`：已认领/已提交，可选中间态。
- `filled`：成交成功，终态。
- `rejected`：下单失败/被拒绝，终态。

`status` 三种取值使用建议：

- 拉取到指令、准备下单前，可先回调一次 `status="submitted"`，用于标记「我已认领」，降低重复执行风险。
- 下单成功后回调 `status="filled"`，并带上 `qmt_order_id` / `filled_quantity` / `filled_price`。
- 下单失败后回调 `status="rejected"`，并带上 `error_message`。

## 5. QMT 脚本接入示例（Python 伪代码）

以下示例说明轮询与回调的调用顺序，`xtquant` 下单部分按实际券商 API 填写。

```python
import time
import requests

BASE = "http://localhost:8000/api/v1/trading"
TOKEN = "<token>"
HEADERS = {"X-QMT-Token": TOKEN}


def pull_pending():
    resp = requests.get(f"{BASE}/qmt/pending", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json().get("items", [])


def report(order_id, status, **kwargs):
    payload = {"status": status, **kwargs}
    resp = requests.post(
        f"{BASE}/qmt/orders/{order_id}/callback",
        json=payload,
        headers={"X-QMT-Token": TOKEN, "Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def execute(order):
    # 1) 先认领，避免重复执行
    report(order["id"], "submitted")

    # 2) 用 xtquant 下单（此处为伪代码，按实际 API 填写）
    # if order["order_type"] == "limit":
    #     qmt_order_id = xttrader.order(
    #         symbol=order["symbol"], side=order["side"],
    #         volume=order["quantity"], price=order["limit_price"])
    # else:
    #     qmt_order_id = xttrader.order(
    #         symbol=order["symbol"], side=order["side"],
    #         volume=order["quantity"])

    # 3) 下单成功后回写成交
    report(
        order["id"],
        "filled",
        qmt_order_id="QMT202608290001",
        filled_quantity=order["quantity"],
        filled_price=120.4,
    )


def run():
    while True:
        for order in pull_pending():
            try:
                execute(order)
            except Exception as exc:
                report(order["id"], "rejected", error_message=str(exc))
        time.sleep(5)  # 轮询间隔按需调整


if __name__ == "__main__":
    run()
```

## 6. 注意事项

- **数量单位**：`quantity` 单位为「张」，若 QMT 下单接口按「手」计，需自行换算（1 手 = 10 张）。
- **幂等**：`filled` / `rejected` 为终态，重复回调同一指令不会覆盖已有结果，接口会返回当前记录。
- **重复执行**：建议遵循「拉取 → 先 `submitted` 认领 → 下单 → 回调终态」的顺序；若下单后回调失败，指令可能再次被拉取，QMT 侧可用 `order_uid` 自行去重。
- **超时与重试**：回调失败应重试；建议对 `GET /pending` 和 `POST /callback` 都设置超时并处理网络异常。
- **错误码**：
  - `401`：token 缺失或错误（检查 `X-QMT-Token`）。
  - `404`：`{id}` 对应的指令不存在。
  - `400`：请求体字段非法（如 `status` 取值不在允许范围）。
  - `500`：服务端内部错误，稍后重试。

## 7. 联系方式

接口契约如有疑问，或需要调整字段/状态约定，请与部署方（本项目维护者）确认。
