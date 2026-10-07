#!/usr/bin/env python3
"""策略1（低溢价率·无过滤）交易盈亏归因分析。

输入：backtest_store/raw/策略1_无过滤_持仓原始返回.jsonl（逐日持仓）
补强：对每个入场日调用 screen_run(history) 取入场时点的转股溢价率与强赎状态
      （缓存于 backtest_store/raw/策略1_入场截面.jsonl，重复执行不重拉）
输出：backtest_store/策略1_交易盈亏归因_2026Q1-Q3.html
      backtest_store/raw/策略1_交易明细.csv

交易重建口径：
- 每个标的的一段连续持仓（volume>0）记为一笔交易；加仓时成本价被均价化，属可接受近似。
- 入场价 = 段首日 cost_price（≈当日收盘成交）；出场价 = 卖出日（volume=0 残留行）的
  last_price，即停止交易冻结价或当日收盘价；金额盈亏取段末日的 profit_loss。
- 期末仍持有的标的记为「未平仓」，不参与盈亏统计。
"""

import csv
import json
import math
import os
import re
import sys
from collections import defaultdict

RAW = "backtest_store/raw/策略1_无过滤_持仓原始返回.jsonl"
SCREEN_CACHE = "backtest_store/raw/策略1_入场截面.jsonl"
CALL_SCREEN_CACHE = "backtest_store/raw/策略1_入场截面_含强赎.jsonl"
TRADES_CSV = "backtest_store/raw/策略1_交易明细.csv"
HTML_OUT = "backtest_store/策略1_交易盈亏归因_2026Q1-Q3.html"

# 策略1 的筛选参数（与回测完全一致：无强赎/评级排除，转股溢价率小值优先）
STRATEGY_BODY = {
    "exclude_areas": None, "exclude_bonds": None, "exclude_calls": None,
    "exclude_list_days": None, "exclude_markets": None, "exclude_org_types": None,
    "exclude_ratings": None, "exclude_st": True, "exclude_yy_ratings": None,
    "hold_days": 0,
    "hold_method": {"hold_max_weight": 0.1, "hold_range_end": 10, "hold_range_start": 1,
                    "hold_range_type": 1, "hold_threshold": "0", "hold_weight_type": 1},
    "link_factors": [],
    "rank_factors": [{"ascending": False, "enable": True, "factor_key": "conv_prem",
                      "factor_name": "转股溢价率", "factor_type": 0, "index": 0,
                      "selectedID": "conv_prem", "weight": 1}],
    "rebalance_method": {"rebalance_type": 0, "rebalance_value": 10},
    "rotation_type": 0, "security_pools": [],
    "stop_method": {"stop_type": 0, "stop_value": 0},
}


def build_trades():
    rows = [json.loads(l) for l in open(RAW, encoding="utf-8")]
    last_day = rows[-1]["trade_date"]
    # 每个标的的逐日序列（含 vol=0 残留行，用于确定卖出日与结算价）
    series = defaultdict(list)
    for r in rows:
        for p in r["positions"]:
            if p["code"] in series and r["trade_date"] == series[p["code"]][-1][0]:
                continue  # 防御：同日重复行取首条
            series[p["code"]].append((r["trade_date"], p))

    trades, opened = [], []
    times_held = defaultdict(int)
    for code, s in series.items():
        seg, i = [], 0
        while i < len(s):
            d, p = s[i]
            if p["volume"] > 0:
                seg.append((d, p))
            elif seg:  # 首个 vol=0 残留行 = 卖出日
                trades.append(make_trade(code, seg, exit_day=d, exit_price=p["last_price"]))
                seg = []
            i += 1
        if seg:  # 数据结束仍持有
            opened.append(make_trade(code, seg, exit_day=seg[-1][0],
                                     exit_price=seg[-1][1]["last_price"], open_flag=True))
        for _ in range(len([1 for j in range(len(s)) if s[j][1]["volume"] > 0 and
                            (j == 0 or s[j - 1][1]["volume"] == 0)])):
            times_held[code] += 1
    return trades, opened, rows


def make_trade(code, seg, exit_day, exit_price, open_flag=False):
    entry_day, entry_p = seg[0]
    last_day, last_p = seg[-1]
    lasts = [p["last_price"] for _, p in seg]
    rets = []
    for a, b in zip(lasts, lasts[1:]):
        rets.append(b / a - 1 if a > 0 else 0.0)
    mean_r = sum(rets) / len(rets) if rets else 0.0
    vol_ann = math.sqrt(sum((x - mean_r) ** 2 for x in rets) / len(rets)) * math.sqrt(242) if len(rets) > 1 else 0.0
    # 金额盈亏用精确口径（卖出结算价 - 持仓均价）× 数量；
    # 段末日 profit_loss 是 T-1 收盘标记值，快速变动时会失真（如精达第一段低估 5k+）
    yuan_exact = (exit_price - last_p["cost_price"]) * last_p["volume"]
    return {
        "code": code,
        "name": entry_p["name"].lstrip("Z"),
        "entry_date": entry_day, "exit_date": exit_day, "open": open_flag,
        "entry_price": entry_p["cost_price"], "exit_price": exit_price,
        "ret": exit_price / entry_p["cost_price"] - 1 if entry_p["cost_price"] > 0 else 0.0,
        "yuan_pnl": yuan_exact,
        "principal": last_p["cost_price"] * last_p["volume"],
        "hold_days": len(seg),
        "entry_month": entry_day[:7],
        "max_runup": max(lasts) / entry_p["cost_price"] - 1,
        "max_dd": min(lasts) / entry_p["cost_price"] - 1,
        "vol_ann": vol_ann,
        "z_days": sum(1 for _, p in seg if p["name"].startswith("Z")),
        "entry_z": entry_p["name"].startswith("Z"),
        "susp_days": sum(1 for _, p in seg if p.get("suspension")),
    }


EXCLUDE_CALLS_5 = ["已满足强赎条件", "公告提示强赎", "公告实施强赎", "公告到期赎回", "已公告强赎"]


def fetch_screens(entry_dates, traded_codes):
    """拉两类入场截面并缓存：
    1) 无过滤截面：入场溢价率/收盘/成交额（is_call 列此形态下为 null）；
    2) 含强赎排除截面：is_call 列有值；若策略实际买入的标的在该截面缺席，
       说明其入场时处于强赎相关状态（被排除），即「缺席推断法」。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from lude_backtest_tools import McpHttpClient, resolve_credentials

    def pull(cache_path, body, tag):
        have = set()
        if os.path.exists(cache_path):
            for line in open(cache_path, encoding="utf-8"):
                have.add(json.loads(line)["trade_date"])
        todo = [d for d in entry_dates if d not in have]
        if todo:
            url, token = resolve_credentials(None, None)
            client = McpHttpClient(url, token)
            with open(cache_path, "a", encoding="utf-8") as out:
                for d in todo:
                    res = client.call_tool("screen_run", {
                        "security_type": "convertible-bond", "screen_type": "history",
                        "parameters": {"answer_mode": 0, "parameters": body,
                                       "trade_date": d}})
                    items = {}
                    for it in res.get("list_items", []):
                        items[it["code"]] = {"conv_prem": it.get("conv_prem"),
                                             "is_call": it.get("is_call"),
                                             "close": it.get("close"),
                                             "amt": it.get("amt")}
                    out.write(json.dumps({"trade_date": d, "items": items},
                                         ensure_ascii=False) + "\n")
                    out.flush()
                    print(f"{tag}截面 {d}: {len(items)} 只", flush=True)
        screens = {}
        for line in open(cache_path, encoding="utf-8"):
            rec = json.loads(line)
            screens[rec["trade_date"]] = rec["items"]
        return screens

    plain = pull(SCREEN_CACHE, STRATEGY_BODY, "无过滤")
    call_sc = pull(CALL_SCREEN_CACHE, {**STRATEGY_BODY, "exclude_calls": EXCLUDE_CALLS_5}, "含强赎")
    return plain, call_sc


def call_state(is_call):
    """入场强赎状态归类。缺席推断的占位串直接归入强赎相关。"""
    if not is_call:
        return "未知"
    s = str(is_call)
    if "排除" in s:
        return "强赎相关"
    m = re.match(r"^(\d+)/(\d+)", s)
    if m:
        hit, need = int(m.group(1)), int(m.group(2))
        return "临近触发" if hit >= need - 2 else "计数中"
    if "公告不强赎" in s:
        return "公告不强赎"
    if "未到转股期" in s:
        return "未到转股期"
    return "其他状态"


def bucket_prep(tr):
    p = tr["entry_conv_prem"] * 100 if tr["entry_conv_prem"] is not None else None
    tr["prem_bucket"] = (None if p is None else
                         "负溢价" if p < 0 else
                         "0~2%" if p < 2 else
                         "2~5%" if p < 5 else
                         "5~10%" if p < 10 else ">10%")
    e = tr["entry_price"]
    tr["price_bucket"] = ("<110" if e < 110 else "110~130" if e < 130
                          else "130~160" if e < 160 else "≥160")
    tr["hold_bucket"] = ("≤10天" if tr["hold_days"] <= 10 else
                         "11~20天" if tr["hold_days"] <= 20 else ">20天")
    tr["call_bucket"] = call_state(tr.get("entry_is_call"))


def agg(trs):
    if not trs:
        return {"n": 0}
    rets = sorted(t["ret"] for t in trs)
    wins = [t for t in trs if t["ret"] > 0]
    losses = [t for t in trs if t["ret"] <= 0]
    avg_win = sum(t["ret"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["ret"] for t in losses) / len(losses) if losses else 0
    return {
        "n": len(trs),
        "win_rate": len(wins) / len(trs),
        "avg_ret": sum(rets) / len(rets),
        "med_ret": rets[len(rets) // 2],
        "yuan": sum(t["yuan_pnl"] for t in trs),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "payoff": abs(avg_win / avg_loss) if avg_loss else None,
    }


def by_bucket(trs, key, order):
    out = []
    for k in order:
        sub = [t for t in trs if t.get(key) == k]
        if sub:
            a = agg(sub)
            a["key"] = k if k is not None else "缺数据"
            out.append(a)
    return out


def main():
    trades, opened, rows = build_trades()
    entry_dates = sorted({t["entry_date"] for t in trades + opened})
    traded_codes = {t["code"] for t in trades + opened}
    plain_screens, call_screens = fetch_screens(entry_dates, traded_codes)
    for t in trades + opened:
        info = plain_screens.get(t["entry_date"], {}).get(t["code"])
        t["entry_conv_prem"] = info["conv_prem"] if info else None
        t["entry_amt"] = info["amt"] if info else None
        # 缺席推断：含强赎排除的截面中找不到 = 入场时处于强赎相关状态；
        # 若无过滤截面同样缺席，才是真正的数据缺口。
        cinfo = call_screens.get(t["entry_date"], {}).get(t["code"])
        if cinfo is None:
            t["entry_is_call"] = "强赎相关(排除推断)" if info else None
        else:
            t["entry_is_call"] = cinfo["is_call"] or None
        bucket_prep(t)

    # 未平仓不进统计
    closed = [t for t in trades if not t["open"]]
    closed.sort(key=lambda t: t["entry_date"])
    print(f"交易总数 {len(closed)}（未平仓 {len(opened)} 不计入）")

    # 明细 CSV
    cols = ["code", "name", "entry_date", "exit_date", "entry_price", "exit_price",
            "ret", "yuan_pnl", "hold_days", "entry_conv_prem", "entry_is_call",
            "entry_price_bucket", "max_runup", "max_dd", "vol_ann", "z_days",
            "entry_z", "susp_days", "open"]
    with open(TRADES_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["代码", "名称", "入场日", "出场日", "入场价", "出场价", "收益率",
                    "金额盈亏", "持有交易日", "入场溢价率", "入场强赎状态", "价格档",
                    "持有期最大浮盈", "持有期最大浮亏", "年化波动率", "Z天数",
                    "入场即Z", "停牌天数", "未平仓"])
        for t in closed + opened:
            w.writerow([t["code"], t["name"], t["entry_date"], t["exit_date"],
                        round(t["entry_price"], 3), round(t["exit_price"], 3),
                        round(t["ret"], 4), round(t["yuan_pnl"], 0), t["hold_days"],
                        None if t["entry_conv_prem"] is None else round(t["entry_conv_prem"], 4),
                        t["entry_is_call"], t["price_bucket"], round(t["max_runup"], 4),
                        round(t["max_dd"], 4), round(t["vol_ann"], 3), t["z_days"],
                        t["entry_z"], t["susp_days"], t["open"]])

    winners = [t for t in closed if t["ret"] > 0]
    losers = [t for t in closed if t["ret"] <= 0]

    def feat_mean(ts, f):
        vals = [t[f] for t in ts if t.get(f) is not None]
        return sum(vals) / len(vals) if vals else None

    feats = [
        ("入场溢价率(%)", lambda ts: feat_mean(ts, "entry_conv_prem") and feat_mean(ts, "entry_conv_prem") * 100),
        ("入场价(元)", lambda ts: feat_mean(ts, "entry_price")),
        ("持有交易日", lambda ts: feat_mean(ts, "hold_days")),
        ("持有期年化波动率(%)", lambda ts: feat_mean(ts, "vol_ann") and feat_mean(ts, "vol_ann") * 100),
        ("持有期最大浮盈(%)", lambda ts: feat_mean(ts, "max_runup") and feat_mean(ts, "max_runup") * 100),
        ("持有期最大浮亏(%)", lambda ts: feat_mean(ts, "max_dd") and feat_mean(ts, "max_dd") * 100),
    ]
    feat_rows = []
    for label, fn in feats:
        w_v, l_v = fn(winners), fn(losers)
        feat_rows.append({"label": label, "win": w_v, "lose": l_v})
    ratio_rows = [
        {"label": "持有期出现Z阶段占比", "win": sum(1 for t in winners if t["z_days"] > 0) / len(winners) * 100 if winners else 0,
         "lose": sum(1 for t in losers if t["z_days"] > 0) / len(losers) * 100 if losers else 0},
        {"label": "入场即Z标的占比(%)", "win": sum(1 for t in winners if t["entry_z"]) / len(winners) * 100 if winners else 0,
         "lose": sum(1 for t in losers if t["entry_z"]) / len(losers) * 100 if losers else 0},
        {"label": "入场强赎相关状态占比(%)",
         "win": sum(1 for t in winners if t["call_bucket"] == "强赎相关") / len(winners) * 100 if winners else 0,
         "lose": sum(1 for t in losers if t["call_bucket"] == "强赎相关") / len(losers) * 100 if losers else 0},
        {"label": "持有期停牌占比(%)", "win": sum(1 for t in winners if t["susp_days"]) / len(winners) * 100 if winners else 0,
         "lose": sum(1 for t in losers if t["susp_days"]) / len(losers) * 100 if losers else 0},
    ]

    prem_order = ["负溢价", "0~2%", "2~5%", "5~10%", ">10%", "缺数据"]
    price_order = ["<110", "110~130", "130~160", "≥160"]
    hold_order = ["≤10天", "11~20天", ">20天"]
    call_order = ["强赎相关", "临近触发", "计数中", "公告不强赎", "未到转股期", "其他状态", "未知"]
    months = sorted({t["entry_month"] for t in closed})

    data = {
        "meta": {"total": len(closed), "opened": len(opened)},
        "overall": {"win": agg(winners), "lose": agg(losers), "all": agg(closed)},
        "feat_rows": feat_rows, "ratio_rows": ratio_rows,
        "prem_buckets": by_bucket(closed, "prem_bucket", prem_order),
        "price_buckets": by_bucket(closed, "price_bucket", price_order),
        "hold_buckets": by_bucket(closed, "hold_bucket", hold_order),
        "call_buckets": by_bucket(closed, "call_bucket", call_order),
        "month_buckets": by_bucket(closed, "entry_month", months),
        "z_buckets": by_bucket(closed, "z_flag", ["有Z", "无Z"]) if all(
            t.update({"z_flag": "有Z" if t["z_days"] > 0 else "无Z"}) or True for t in closed) else [],
        "hist": histogram([t["ret"] for t in closed]),
        "scatter": [[None if t["entry_conv_prem"] is None else round(t["entry_conv_prem"] * 100, 2),
                     round(t["ret"] * 100, 2), t["name"], round(t["yuan_pnl"], 0)]
                    for t in closed],
        "top_win": table_rows(sorted(closed, key=lambda t: -t["ret"])[:10]),
        "top_lose": table_rows(sorted(closed, key=lambda t: t["ret"])[:10]),
        "month_yuan": [[m, round(sum(t["yuan_pnl"] for t in closed if t["entry_month"] == m), 0)]
                       for m in months],
        "repeat_losers": repeat_losers(closed),
    }
    with open("/tmp/strategy1_analysis.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    render_html(data)
    print("已生成:", HTML_OUT)
    print("已生成:", TRADES_CSV)


def histogram(rets):
    edges = [-40, -30, -20, -15, -10, -5, 0, 5, 10, 15, 20, 30, 40, 1000]
    labels, counts = [], []
    for a, b in zip(edges, edges[1:]):
        if a < -30 or b > 40:
            lab = f"{max(a, -40)}%以下" if b <= -30 else f"{min(b, 40)}%以上"
        else:
            lab = f"{a}%~{b}%"
        labels.append(lab)
        counts.append(sum(1 for r in rets if a <= r * 100 < b))
    return {"labels": labels, "counts": counts}


def table_rows(ts):
    return [{"code": t["code"], "name": t["name"], "entry": t["entry_date"],
             "exit": t["exit_date"], "ret": round(t["ret"] * 100, 2),
             "yuan": round(t["yuan_pnl"], 0),
             "prem": None if t["entry_conv_prem"] is None else round(t["entry_conv_prem"] * 100, 2),
             "price": round(t["entry_price"], 1), "is_call": t["entry_is_call"] or "-",
             "hold": t["hold_days"], "z": t["z_days"]} for t in ts]


def repeat_losers(closed):
    by_code = defaultdict(list)
    for t in closed:
        by_code[t["code"]].append(t)
    out = []
    for code, ts in by_code.items():
        yuan = sum(t["yuan_pnl"] for t in ts)
        if len(ts) >= 2 and yuan < 0:
            detail = " / ".join(
                f"{t['entry_date'][5:]} {t['ret']:+.1%}({t['yuan_pnl']:+,.0f}元·本金{t['principal']/10000:.1f}万)"
                for t in sorted(ts, key=lambda x: x["entry_date"]))
            out.append({"code": code, "name": ts[0]["name"], "n": len(ts),
                        "ret_avg": round(sum(t["ret"] for t in ts) / len(ts) * 100, 2),
                        "yuan": round(yuan, 0), "detail": detail})
    out.sort(key=lambda x: x["yuan"])
    return out[:12]


def render_html(data):
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "strategy1_report_template.html"), encoding="utf-8").read()
    html = tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
