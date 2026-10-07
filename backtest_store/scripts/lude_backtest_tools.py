#!/usr/bin/env python3
"""禄得网回测数据批量工具（独立运行，不依赖 ZCode 会话）。

背景：禄得 MCP 服务为 HTTP 接入（MCP-over-HTTP / JSON-RPC 2.0），
持仓查询接口 backtest_daily_position_get 只有「单回测 × 单交易日」粒度。
逐日拉取属于大批量串行调用，在 AI 会话内逐次调工具既慢又耗上下文与配额，
因此本脚本直接与 MCP 端点通信，把取数、断点续传、校验、合并全部脚本化。

用法：
  # 拉取某回测的逐日持仓（断点续传，可反复执行）
  python3 lude_backtest_tools.py fetch --backtest-id <ID> --name 策略1
  # 试点：只拉前 N 个交易日验证链路
  python3 lude_backtest_tools.py fetch --backtest-id <ID> --name 策略1 --limit 10
  # 把 raw JSONL 展平为逐日逐标的持仓 CSV
  python3 lude_backtest_tools.py merge --raw-dir backtest_store/raw

鉴权（优先级从高到低，不要把 token 写进代码或命令行历史）：
  1. 环境变量 LUDE_URL / LUDE_TOKEN
  2. ~/.zcode/cli/config.json 中 mcp.servers.lude 的 url 与 Authorization 头

零第三方依赖，仅用标准库。
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://api.lude.site/mcp"
CONFIG_CANDIDATES = ["~/.zcode/cli/config.json"]

# 2026 年休市日（来源：禄得 trading_calendar，2026-10-06 拉取）
CLOSED_2026 = {
    "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-10", "2026-01-11",
    "2026-01-17", "2026-01-18", "2026-01-24", "2026-01-25", "2026-01-31", "2026-02-01",
    "2026-02-07", "2026-02-08", "2026-02-14", "2026-02-15", "2026-02-16", "2026-02-17",
    "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    "2026-02-28", "2026-03-01", "2026-03-07", "2026-03-08", "2026-03-14", "2026-03-15",
    "2026-03-21", "2026-03-22", "2026-03-28", "2026-03-29", "2026-04-04", "2026-04-05",
    "2026-04-06", "2026-04-11", "2026-04-12", "2026-04-18", "2026-04-19", "2026-04-25",
    "2026-04-26", "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-05-09", "2026-05-10", "2026-05-16", "2026-05-17", "2026-05-23", "2026-05-24",
    "2026-05-30", "2026-05-31", "2026-06-06", "2026-06-07", "2026-06-13", "2026-06-14",
    "2026-06-19", "2026-06-20", "2026-06-21", "2026-06-27", "2026-06-28", "2026-07-04",
    "2026-07-05", "2026-07-11", "2026-07-12", "2026-07-18", "2026-07-19", "2026-07-25",
    "2026-07-26", "2026-08-01", "2026-08-02", "2026-08-08", "2026-08-09", "2026-08-15",
    "2026-08-16", "2026-08-22", "2026-08-23", "2026-08-29", "2026-08-30", "2026-09-05",
    "2026-09-06", "2026-09-12", "2026-09-13", "2026-09-19", "2026-09-20", "2026-09-25",
    "2026-09-26", "2026-09-27",
}


def resolve_credentials(cli_url: str, cli_token: str):
    """凭据解析：命令行参数 > 环境变量 > ZCode 本地配置。"""
    url = cli_url or os.environ.get("LUDE_URL")
    token = cli_token or os.environ.get("LUDE_TOKEN")
    if url and token:
        return url, token
    for cand in CONFIG_CANDIDATES:
        path = os.path.expanduser(cand)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            srv = (cfg.get("mcp", {}).get("servers", {}) or {}).get("lude", {})
            url = url or srv.get("url")
            auth = ((srv.get("headers", {}) or {}).get("Authorization", "") or "")
            token = token or auth.replace("Bearer", "").strip()
        except (OSError, json.JSONDecodeError):
            continue
        if url and token:
            return url, token
    if not url:
        url = DEFAULT_URL
    if not token:
        sys.exit("错误：未找到 token。请设置 LUDE_TOKEN 环境变量，或在 ~/.zcode/cli/config.json 配置 lude 服务。")
    return url, token


class McpHttpClient:
    """最小可用的 MCP-over-HTTP 客户端（JSON-RPC 2.0）。"""

    def __init__(self, url: str, token: str, timeout: int = 120):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session_id = None
        self._req_id = 0
        self._initialize()

    def _post(self, payload: dict, expect_result: bool = True):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            body = resp.read().decode("utf-8", "replace")
        if not expect_result:
            return None
        return self._parse_body(body)

    @staticmethod
    def _parse_body(body: str) -> dict:
        body = body.strip()
        if body.startswith("{"):
            return json.loads(body)
        # SSE 流式响应：取最后一条 data 行
        for line in reversed(body.splitlines()):
            if line.startswith("data:"):
                data = line[len("data:"):].strip()
                if data and data != "[DONE]":
                    try:
                        return json.loads(data)
                    except json.JSONDecodeError:
                        continue
        raise RuntimeError(f"无法解析 MCP 响应体: {body[:200]!r}")

    def _initialize(self):
        for version in ("2025-03-26", "2024-11-05"):
            resp = self._post({
                "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
                "params": {
                    "protocolVersion": version, "capabilities": {},
                    "clientInfo": {"name": "lude-backtest-tools", "version": "1.0"},
                },
            })
            if "result" in resp:
                self._post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                           expect_result=False)
                return
        raise RuntimeError(f"MCP initialize 失败: {json.dumps(resp, ensure_ascii=False)[:300]}")

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def call_tool(self, name: str, arguments: dict, retries: int = 3) -> dict:
        """调用工具。仅对传输层错误（网络/超时/5xx/429）重试，业务错误直接抛出。"""
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}}
        delay = 2.0
        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = self._post(payload)
                if "error" in resp:
                    raise RuntimeError(f"MCP 错误: {resp['error']}")
                result = resp.get("result", {})
                if result.get("isError"):
                    text = "".join(c.get("text", "") for c in result.get("content", []))
                    raise ToolBusinessError(text[:500])
                structured = result.get("structuredContent")
                if structured is not None:
                    return structured
                text = "".join(c.get("text", "") for c in result.get("content", [])
                               if c.get("type") == "text")
                return json.loads(text)
            except ToolBusinessError:
                raise  # 业务错误（如 11025）不重试
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                    ConnectionError, json.JSONDecodeError) as e:
                if isinstance(e, urllib.error.HTTPError) and e.code not in (429, 500, 502, 503, 504):
                    raise
                last_err = e
                if attempt < retries:
                    print(f"  传输错误（第{attempt + 1}次重试）: {e}", flush=True)
                    time.sleep(delay)
                    delay *= 2
        raise RuntimeError(f"重试 {retries} 次后仍失败: {last_err}")


class ToolBusinessError(RuntimeError):
    """工具执行层面的业务错误（错误码如 11025 持仓日详情不存在），不应重试。"""


def trading_days(start: str, end: str, days_file: str = None) -> list:
    """生成 [start, end] 内的交易日清单。days_file 优先（JSON 数组，全量日历）。"""
    if days_file:
        with open(os.path.expanduser(days_file), encoding="utf-8") as f:
            all_days = json.load(f)
        return [d for d in all_days if start <= d <= end]
    days, cur = [], dt.date.fromisoformat(start)
    stop = dt.date.fromisoformat(end)
    while cur <= stop:
        iso = cur.isoformat()
        if cur.weekday() < 5 and iso not in CLOSED_2026:
            days.append(iso)
        cur += dt.timedelta(days=1)
    return days


def cmd_fetch(args):
    days = trading_days(args.start, args.end, args.days_file)
    if args.limit:
        days = days[:args.limit]
    os.makedirs(args.raw_dir, exist_ok=True)
    out_path = os.path.join(args.raw_dir, f"{args.name}_持仓原始返回.jsonl")

    # 断点续传：已存在的日期跳过
    done = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.add(json.loads(line)["trade_date"])
                except (json.JSONDecodeError, KeyError):
                    continue  # 半行（中断残留）忽略，由后续重写覆盖风险极低

    todo = [d for d in days if d not in done]
    print(f"目标文件: {out_path}")
    print(f"计划 {len(days)} 天，已完成 {len(done)} 天，本次待拉 {len(todo)} 天")

    if not todo:
        print("全部日期已完成，无事可做。")
        return 0

    url, token = resolve_credentials(args.url, args.token)
    client = McpHttpClient(url, token, timeout=args.timeout)

    ok = err = 0
    failed_days = []
    t0 = time.time()
    with open(out_path, "a", encoding="utf-8") as out:
        for i, day in enumerate(todo, 1):
            try:
                data = client.call_tool(
                    "backtest_daily_position_get",
                    {"security_type": 1, "backtest_id": args.backtest_id, "date": day})
                positions = data.get("positions", []) if isinstance(data, dict) else []
                row = dict(data) if isinstance(data, dict) else {"raw": data}
                row["trade_date"] = day
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                ok += 1
                n_pos = len(positions)
                print(f"[{i}/{len(todo)}] {day} OK 持仓{n_pos}条 "
                      f"(累计{time.time() - t0:.0f}s)", flush=True)
            except ToolBusinessError as e:
                row = {"trade_date": day, "note": "business_error",
                       "message": str(e), "positions": []}
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                err += 1
                print(f"[{i}/{len(todo)}] {day} 业务错误: {str(e)[:120]}", flush=True)
            except Exception as e:  # 传输错误重试耗尽：记录并继续，最终汇总
                failed_days.append(day)
                print(f"[{i}/{len(todo)}] {day} 失败: {e}", flush=True)
            if args.sleep > 0:
                time.sleep(args.sleep)

    print(f"\n完成：成功 {ok}，业务错误 {err}，失败 {len(failed_days)}，耗时 {time.time() - t0:.0f}s")
    if failed_days:
        print("失败日期（重跑本命令即可续传）:", ", ".join(failed_days))
        return 1
    return 0


CSV_COLUMNS = ["交易日", "代码", "名称", "持仓量(张)", "成本价", "最新价", "市值",
               "持仓盈亏", "是否停牌", "当日税后分红", "当日送转"]
POS_FIELDS = ["code", "name", "volume", "cost_price", "last_price", "market_value",
              "profit_loss", "suspension", "cash_div", "stk_div"]


def cmd_merge(args):
    raw_dir = os.path.expanduser(args.raw_dir)
    files = sorted(f for f in os.listdir(raw_dir) if f.endswith("_持仓原始返回.jsonl"))
    if not files:
        sys.exit(f"错误：{raw_dir} 下没有 *_持仓原始返回.jsonl 文件")
    for fname in files:
        in_path = os.path.join(raw_dir, fname)
        out_path = os.path.join(raw_dir, fname.replace("_持仓原始返回.jsonl", "_每日持仓.csv"))
        rows = days = 0
        with open(in_path, encoding="utf-8") as fin, \
                open(out_path, "w", encoding="utf-8-sig", newline="") as fout:
            writer = csv.writer(fout)
            writer.writerow(CSV_COLUMNS)
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                day = rec.get("trade_date")
                for pos in rec.get("positions", []):
                    writer.writerow([day] + [pos.get(f, "") for f in POS_FIELDS])
                    rows += 1
                days += 1
        print(f"{fname} -> {out_path}（{days} 天，{rows} 条持仓记录）")
    print("注意：调仓日可能出现 volume=0 的占位记录，已按接口原样保留。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="禄得网回测数据批量工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="逐日拉取回测持仓（断点续传）")
    p_fetch.add_argument("--backtest-id", required=True, help="回测实例 ID（backtest_id）")
    p_fetch.add_argument("--name", required=True, help="输出文件名前缀，如 策略1")
    p_fetch.add_argument("--start", default="2026-01-05", help="起始日（默认 2026-01-05）")
    p_fetch.add_argument("--end", default="2026-09-30", help="截止日（默认 2026-09-30）")
    p_fetch.add_argument("--days-file", default=None,
                         help="交易日清单 JSON（如 /tmp/cb_trading_days_2026.json），优先于内置日历")
    p_fetch.add_argument("--raw-dir", default="backtest_store/raw", help="JSONL 输出目录")
    p_fetch.add_argument("--limit", type=int, default=0, help="只拉前 N 个交易日（试点用）")
    p_fetch.add_argument("--sleep", type=float, default=1.0, help="相邻调用间隔秒数")
    p_fetch.add_argument("--timeout", type=int, default=120, help="单次请求超时秒数")
    p_fetch.add_argument("--url", default=None, help="覆盖 MCP 端点（默认读配置/环境变量）")
    p_fetch.add_argument("--token", default=None, help="覆盖 token（建议用 LUDE_TOKEN 环境变量）")
    p_fetch.set_defaults(func=cmd_fetch)

    p_merge = sub.add_parser("merge", help="把 raw JSONL 展平为每日持仓 CSV")
    p_merge.add_argument("--raw-dir", default="backtest_store/raw", help="raw JSONL 目录")
    p_merge.set_defaults(func=cmd_merge)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
