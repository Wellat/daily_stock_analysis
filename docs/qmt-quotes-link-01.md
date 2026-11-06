# QMT 行情桥接文档（DSA 侧开发者）

本文档面向 DSA 系统开发者，说明如何通过 Redis 读取 QMT 提供的行情数据（交易时段内最新价快照、最近 N 日日K线），以及如何管理订阅标的列表。

> 生产者实现：QMT 策略 `cb_quotes_bridge.py`。
> 文档版本：v1.0（2026-10-31）。字段如有变更会在本文档更新并升版本号。

## 1. 链路架构

```
┌──────────────────┐  每30s批量快照+每10s心跳   ┌─────────────────┐   读行情(GET/MGET)
│ QMT 客户端        │ ────────────────────────▶ │  Redis           │ ◀──────────────── DSA
│ cb_quotes_bridge │   盘后15:05写日K           │  localhost:6379  │   写订阅配置(HSET)
│ (行情采集策略)    │ ◀──────────────────────── │  db 0            │
└──────────────────┘   每5s读订阅配置(HGETALL)   └─────────────────┘
```

要点：

- **单向数据流**：QMT 只写行情数据、不读行情数据；DSA 是行情数据的唯一消费方。
- **配置反向流**：订阅哪些标的由 DSA 决定，写入 `qmt:cfg:symbols`，QMT 每 5 秒读取生效。
- QMT 无法暴露 HTTP 服务（客户端安全限制），因此 Redis 是唯一的传输介质；**DSA 侧可自由使用 GET/MGET/HGETALL 等任何命令读取**（下述 QMT 侧读取限制与 DSA 无关，仅为背景说明）。

## 2. 连接与基本约定

| 项 | 值 |
|----|----|
| 地址 | `localhost:6379`（Redis 运行于 Docker 容器 `myredis`） |
| DB | `0` |
| 密码 | 当前未设置；启用 `requirepass` 后 DSA 连接需带密码，并同步修改 QMT 侧 `REDIS_PASSWORD` |
| 数据编码 | 所有 string 值均为 **UTF-8 编码的紧凑 JSON**（无空格分隔符） |
| 时间 | 全部为**北京时间**；两种格式见各字段说明 |
| Redis 版本要求 | ≥ 2.6（仅用 SET/HSET/HGETALL/MGET/DEL，无特殊命令） |

**背景说明（不影响 DSA）**：QMT 客户端内置的 redis-py 被厂商打了安全补丁，QMT 进程内读取行情数据会被拦截，因此链路设计为 QMT 只写、DSA 只读行情。DSA 使用标准 redis 客户端（redis-py / go-redis / jedis 等均无此限制），可正常读取所有 key。

## 3. Key 总览

| Key | 类型 | 生产者 | 用途 | TTL | 写入频率 |
|-----|------|--------|------|-----|---------|
| `qmt:hb` | string | QMT | 心跳（判活） | 30s | 每 10s |
| `qmt:quote:{code}` | string | QMT | 实时快照 JSON | 300s | 每 30s 整 key 覆盖 |
| `qmt:daily:{code}` | string | QMT | 日K JSON | 3 天 | 启动时 / 每日 15:05 / 新增标的时 |
| `qmt:cfg:symbols` | **hash** | QMT（seed）/ DSA | 订阅标的列表 | 无 | DSA 按需 |

## 4. `qmt:hb` —— 心跳

- **类型**：string
- **值格式**：`<写入时间>|<策略名>`，写入时间为 `%Y-%m-%d %H:%M:%S`
- **TTL**：30 秒（每 10 秒重写并续期）

```
GET qmt:hb
→ "2026-10-31 22:43:21|cb_quotes_bridge"
```

**判活逻辑（推荐 DSA 实现）**：`EXISTS qmt:hb` 为真即行情链路存活。心跳频率独立于数据轮询——即使 `qmt:cfg:symbols` 为空（QMT 暂停写数据），心跳依然维持。心跳消失（30 秒内无续期）说明 QMT 策略停止或 Redis/QMT 间链路故障。

## 5. `qmt:quote:{code}` —— 实时快照

### 5.1 Key 构成

`qmt:quote:` + 标的代码。`{code}` 的 pattern：

```
^[0-9]{6}\.(SH|SZ)$
```

当前已验证支持：**A 股股票**（如 `600519.SH`）、**ETF**（如 `510050.SH`）、**可转债**（沪 `11xxxx.SH`、深 `12xxxx.SZ`）。无效/退市代码不会产生该 key（见 5.5）。

### 5.2 更新机制

- QMT **每 30 秒**批量拉取订阅列表内全部标的的 tick 快照，**整 key 覆盖写入**（SET，非增量修改），每次写入重置 TTL 为 300 秒。
- 因此 key 内永远是"最近一轮"的快照，**无需合并、无需 watch**，读侧一个 GET 拿全量。
- 正常运行时 TTL 剩余值在 270~300 之间波动；若 QMT 停写，key 在最后一次写入后 300 秒自动消失。

### 5.3 值格式（JSON 字段表）

示例（600519.SH 真实数据）：

```json
{
  "code": "600519.SH",
  "last": 1392.05,
  "open": 1363.0,
  "high": 1393.37,
  "low": 1352.98,
  "lastClose": 1354.0,
  "volume": 59927,
  "pvolume": 5992729,
  "amount": 8257381800.0,
  "bid1": 1392.03,
  "ask1": 1392.05,
  "bidPrice": [1392.03, 1392.0, 1391.0, 1390.71, 1390.5],
  "askPrice": [1392.05, 1392.06, 1392.08, 1392.09, 1392.1],
  "bidVol": [2, 3, 2, 10, 1],
  "askVol": [1, 1, 1, 11, 1],
  "timetag": "20261030 15:00:01",
  "written_at": "2026-10-31 22:43:21"
}
```

| 字段 | 类型 | 含义 | 单位/说明 |
|------|------|------|-----------|
| `code` | string | 标的代码 | 与 key 后缀一致 |
| `last` | number | 最新价 | 元 |
| `open` / `high` / `low` | number | 当日开盘/最高/最低 | 元 |
| `lastClose` | number | 昨日收盘价 | 元 |
| `volume` | int | 当日累计成交量 | **手**（股票/ETF：1手=100股/份；可转债：1手=10张） |
| `pvolume` | int | 当日累计成交量 | **股/份/张**（精确单位，推荐用此字段换算） |
| `amount` | number | 当日累计成交额 | 元 |
| `bid1` / `ask1` | number | 买一/卖一价 | 元 |
| `bidPrice` / `askPrice` | array[number] | 五档买/卖价，档1→档5 | 元；异常时长度可能 <5 |
| `bidVol` / `askVol` | array[int] | 五档买/卖量，与价格数组下标对应 | 手 |
| `timetag` | string | **交易所快照时刻** | 格式 `YYYYMMDD HH:MM:SS` |
| `written_at` | string | **QMT 写入 Redis 的时刻** | 格式 `YYYY-MM-DD HH:MM:SS` |

数值精度：价格类字段按 4 位小数内 round（清除浮点尾差，如 `201.10000000000002 → 201.1`）；`amount` 2 位小数。

### 5.4 两个时间戳的区别（重要）

- `timetag` 是**行情本身的时间**：数据新鲜度的唯一判据。L1 快照约 3 秒一档，盘中 `timetag` 距当前时间应 ≤ 交易时段判断误差 + 数据链路延迟。
- `written_at` 是**写入时刻**：用于排查链路延迟（`written_at - timetag`过大说明 QMT 轮询/写入积压）。

**端到端延迟预算（盘中）**：交易所快照节奏（~3s）+ QMT 轮询间隔（30s）+ 写入（<1s），最坏约 **35 秒**。建议 DSA 侧判定：交易时段内 `timetag` 距今 > 120 秒视为数据异常（stale），降级处理。

### 5.5 边界行为

- **非交易时段**：key 内是最后一个交易日的收盘定格快照（`timetag` 停在约 15:00:01），只要 QMT 策略运行，key 持续续期存在。**识别方式：解析 `timetag` 与当前时间比较**，交易时段判断由 DSA 自行负责（QMT 不提供交易日历信号）。
- **无效/退市代码**：QMT 拉取后若最新价为 0 或无数据，**不写入该 key**，仅在其日志提示一次；代码恢复有效后自动恢复写入。
- **不在订阅列表的标的**：不会有对应 key；从列表移除后，key 在 300 秒内随 TTL 过期自动消失（QMT 不主动 DEL）。

## 6. `qmt:daily:{code}` —— 日K线

### 6.1 更新时机（全天只有三种触发）

| 时机 | 说明 |
|------|------|
| QMT 策略启动后约 15 秒 | 全量同步订阅列表内所有标的（进程重启自愈） |
| 每个自然日 15:05 | 盘后定时全量同步（**非交易日也会触发**，写入的是最后交易日数据，内容不变仅 `updated_at` 变化） |
| 订阅列表新增标的时 | 秒级回填该标的日K（无需等盘后） |

每次都是**整 key 覆盖重写**（完整 30 根数组），即使某天漏跑，下次写入自愈，读侧无需关心缺数。

### 6.2 值格式

示例（截取尾部 2 根，实际 30 根）：

```json
{
  "code": "600519.SH",
  "dividend_type": "none",
  "updated_at": "2026-10-31 22:43:21",
  "bars": [
    {"date": "20261029", "open": 1349.99, "high": 1356.0,  "low": 1330.01, "close": 1354.0,  "volume": 42992, "amount": 5797980000.0},
    {"date": "20261030", "open": 1363.0,  "high": 1393.37, "low": 1352.98, "close": 1392.05, "volume": 59927, "amount": 8257381800.0}
  ]
}
```

| 字段 | 说明 |
|------|------|
| `dividend_type` | 恒为 `"none"`（不复权原始价）。可转债无复权问题；若 DSA 需要股票前复权数据，另行提出，QMT 侧可增加 `qmt:daily_fq:{code}` |
| `updated_at` | 本次写入时刻，`YYYY-MM-DD HH:MM:SS` |
| `bars` | **按日期升序**的数组，最多 30 根；新上市的标的不足 30 根为实际根数 |
| `bars[].date` | 交易日，`YYYYMMDD` 字符串 |
| `bars[].open/high/low/close` | 元；不复权 |
| `bars[].volume` | **手**（同 5.3 的单位约定：股票/ETF 1手=100股/份，可转债 1手=10张） |
| `bars[].amount` | 元 |

**取"最近 X 日"**：直接取数组尾部切片，如 Python `bars[-X:]`，X 由 DSA 自定（≤30）。

**注意**：盘后 15:05 前如果读到了"今天"的 bar，该 bar 是盘中未收盘的动态值（close 即当时最新价）；15:05 后写入的才是当日定型数据。以 `updated_at` 是否晚于当日 15:05 判断当前 bars 是否含定型当日数据。

## 7. `qmt:cfg:symbols` —— 订阅标的列表

### 7.1 类型与读写方式（注意：是 hash，不是 string）

```
类型：hash，仅一个 field
field：symbols
value：JSON 数组字符串
```

```
# DSA 读取（整组）
HGET qmt:cfg:symbols symbols
→ "[\"600519.SH\", \"510050.SH\", \"123270.SZ\"]"

# DSA 修改（整组覆盖）
HSET qmt:cfg:symbols symbols '["600519.SH", "510050.SH", "123270.SZ", "588000.SH"]'

# 查看（容器内）
docker exec myredis redis-cli HGETALL qmt:cfg:symbols
```

> QMT 侧用 `HGETALL` 读取（hash 类型是该链路设计的一部分，与 QMT 客户端的 redis 补丁有关，DSA 无需关注原因，只需遵守 field 名 `symbols` 不变）。

### 7.2 value 的格式规范

- JSON 字符串数组，元素为标的代码，pattern 同 5.1：`^[0-9]{6}\.(SH|SZ)$`
- **语义是"整组替换"**：每次 HSET 的数组就是完整订阅列表，不是增量追加。想加标的 = 读旧列表 → 合并 → 整组写回；想删标的 = 写回不含它的完整列表。
- QMT 首次启动且该 key 不存在时，会写入默认列表 `["600519.SH", "510050.SH", "123270.SZ"]` 作为初值；此后该 key 是订阅关系的**唯一事实来源**。
- 建议写入前做格式校验（代码 pattern、数组去重），并**保证单一写入者**（多个 DSA 实例并发 HSET 会相互覆盖）。

### 7.3 变更生效时序

```
DSA HSET 新列表
   ↓ ≤5s
QMT writer 线程检测到变更（diff）
   ├─ 新增标的：立即回填 qmt:daily:{code}（秒级）；qmt:quote:{code} 下一轮轮询出现（≤30s）
   └─ 删除标的：停止写入；qmt:quote:{code} 300s 后 TTL 过期消失；qmt:daily:{code} 3 天后消失
```

### 7.4 异常值行为

| 写入内容 | QMT 行为 |
|----------|----------|
| 合法 JSON 数组（含空 `[]`） | 空列表 = 暂停写所有行情数据（心跳 `qmt:hb` 保持） |
| 非法 JSON / 非数组 | 按空列表处理，QMT 日志提示"解析失败"，心跳保持 |
| 数组含无效代码 | 该标的不产生 quote/daily key，QMT 日志提示一次；不影响其他标的 |

即：**配置写坏最坏结果是暂停数据（心跳仍在，可感知），不会产生错误数据**。

## 8. 故障场景判别

| 现象 | 结论 | 恢复方式 |
|------|------|---------|
| `qmt:hb` 消失，quote key 也消失 | QMT 策略停止 / QMT 客户端关闭 / Redis 与 QMT 断链 | 重启 QMT 策略后自动恢复（cfg 保留，启动即全量同步日K） |
| `qmt:hb` 存在，quote key 消失 | 订阅列表为空或解析失败 | 检查 `qmt:cfg:symbols` 内容 |
| `qmt:hb` 存在，quote 存在但 `timetag` 停滞 | 非交易时段（定格快照，正常）或行情源异常 | 交易时段内停滞 > 120s 联系 QMT 侧排查 |
| Redis 完全不可用 | 链路中断 | QMT 侧自动退避重连（1~60s），恢复后自动续写，无需人工干预；期间数据不补（快照语义） |
| 某标的 daily 缺失 | 该标的无效，或刚加入还未回填（<5s） | 等待 5s 后仍无则检查代码有效性 |

## 9. DSA 侧消费参考（Python 示例）

```python
import json
import datetime
import redis

r = redis.Redis(host='localhost', port=6379, db=0, socket_timeout=0.5,
                decode_responses=True)   # decode_responses=True 直接得到 str

# 1) 判活
alive = r.exists('qmt:hb')

# 2) 批量取快照（一次往返）
codes = ['600519.SH', '510050.SH', '123270.SZ']
vals = r.mget(['qmt:quote:%s' % c for c in codes])
quotes = {c: json.loads(v) for c, v in zip(codes, vals) if v}

# 3) 新鲜度判断（交易时段内）
def is_fresh(q, max_lag=120):
    t = datetime.datetime.strptime(q['timetag'], '%Y%m%d %H:%M:%S')
    return (datetime.datetime.now() - t).total_seconds() <= max_lag

# 4) 取最近 X 日日K
d = json.loads(r.get('qmt:daily:600519.SH'))
recent_x = d['bars'][-10:]

# 5) 修改订阅（整组覆盖）
new_list = sorted(set(codes) | {'588000.SH'})
r.hset('qmt:cfg:symbols', 'symbols', json.dumps(new_list))
```

轮询节奏建议：quote 每 5~30 秒按需拉取（数据本身 30 秒一档，拉更快拿到的也是同一份）；判活每次拉取前做即可。

## 10. 常用验证命令（QMT 所在机器）

```bash
docker exec myredis redis-cli GET qmt:hb                                # 心跳值
docker exec myredis redis-cli TTL qmt:hb                                # 恒 < 30
docker exec myredis redis-cli GET qmt:quote:600519.SH                   # 快照 JSON
docker exec myredis redis-cli --raw GET qmt:daily:600519.SH | head -c 300   # 日K
docker exec myredis redis-cli KEYS 'qmt:*'                              # 全部 key（调试用）
docker exec -it myredis redis-cli MONITOR                               # 实时写入流
docker exec myredis redis-cli HGET qmt:cfg:symbols symbols              # 当前订阅列表
```

## 11. 版本记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-10-31 | 初版：quote/daily/cfg/hb 四类 key 契约定稿 |
