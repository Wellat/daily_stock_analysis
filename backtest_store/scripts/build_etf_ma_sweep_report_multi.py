# -*- coding: utf-8 -*-
"""ETF 信号策略·均线窗口寻优 HTML 报告生成器（多标的版）。

读取 backtest_store/raw/etf_ma_sweep_multi.json 登记的三标的
（510880 / 159307 / 513630，各三窗口 × 49 组网格），
从 SQLite 汇总指标并渲染单文件 HTML（ECharts CDN）。

推荐规则：
- 每只标的：联合收益 =（后半段 + 前半段）/ 2（两段互不重叠），
  仅统计前半段预热后可交易 ≥ 60 天的均线；平台区 = 包含最优点的
  连续网格段（联合 ≥ 最优 95%），推荐 = 平台区中心。
- 三标的通吃：maximin——逐均线取三只中「联合/各自最优」的最小比值，取最大者。

用法：.venv/bin/python backtest_store/scripts/build_etf_ma_sweep_report_multi.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "stock_analysis.db"
MANIFEST = REPO_ROOT / "backtest_store" / "raw" / "etf_ma_sweep_multi.json"
OUT_PATH = REPO_ROOT / "backtest_store" / f"ETF信号策略_均线窗口寻优_三标的_{date.today():%Y-%m-%d}.html"

WINDOW_KEYS = ["A_full", "C_early", "B_recent"]
MIN_SIGNALABLE_DAYS = 60  # 前半段预热后至少可交易天数，才计入联合排名
BAND_RATIO = 0.95
SYMBOL_NAMES = {"510880": "红利ETF", "159307": "红利类ETF(深)", "513630": "港股红利类ETF(沪)"}


def fmt(v, nd=2, suffix=""):
    if v is None:
        return "—"
    return f"{v:.{nd}f}{suffix}"


def load_symbol(conn: sqlite3.Connection, symbol: str, meta: dict) -> dict:
    by_window, items = {}, {}
    for key in WINDOW_KEYS:
        win = meta["windows"][key]
        rows = conn.execute(
            """
            SELECT json_extract(i.parameters_json, '$.parameters.ma_window') AS ma,
                   i.status, m.total_return_pct, m.annualized_return_pct, m.max_drawdown_pct,
                   m.sharpe_ratio, m.win_rate_pct, m.trade_count,
                   r.benchmark_return_pct, r.equity_curve_json
            FROM strategy_lab_batch_items i
            JOIN strategy_lab_run_metrics m ON m.run_id = i.run_id
            JOIN strategy_lab_runs r ON r.id = i.run_id
            WHERE i.batch_id = ? ORDER BY ma
            """,
            (win["batch_id"],),
        ).fetchall()
        if len(rows) != 49 or any(r["status"] != "completed" for r in rows):
            raise SystemExit(f"{symbol} {key} 批次 {win['batch_id']} 数据不完整")
        by_window[key] = {r["ma"]: dict(r) for r in rows}
        items[key] = [dict(r) for r in rows]

    ma_axis = [r["ma"] for r in items["A_full"]]
    rets = {k: [by_window[k][ma]["total_return_pct"] for ma in ma_axis] for k in WINDOW_KEYS}
    c_days = meta["windows"]["C_early"]["n_days"]
    eligible = [(c_days - ma) >= MIN_SIGNALABLE_DAYS for ma in ma_axis]
    joint = [
        (rets["B_recent"][i] + rets["C_early"][i]) / 2 if eligible[i] else None
        for i in range(len(ma_axis))
    ]
    elig_pairs = [(ma_axis[i], joint[i]) for i in range(len(ma_axis)) if joint[i] is not None]
    best_ma, best_val = max(elig_pairs, key=lambda p: p[1])
    # 平台区：包含最优点的连续段（联合 >= 最优 95%）
    lo = hi = best_ma
    idx = ma_axis.index(best_ma)
    while lo - 5 in ma_axis:
        j = joint[ma_axis.index(lo - 5)]
        if j is None or j < best_val * BAND_RATIO:
            break
        lo -= 5
    while hi + 5 in ma_axis:
        j = joint[ma_axis.index(hi + 5)]
        if j is None or j < best_val * BAND_RATIO:
            break
        hi += 5
    rec_ma = round((lo + hi) / 2 / 5) * 5

    curves = {}
    for item in items["A_full"]:
        if item["ma"] in (40, rec_ma):
            curve = json.loads(item["equity_curve_json"])
            curves[f"MA{item['ma']}"] = {
                "dates": [p["trade_date"] for p in curve],
                "values": [round((p["equity"] / 100000.0 - 1) * 100, 3) for p in curve],
            }
    curve = json.loads(items["A_full"][0]["equity_curve_json"])
    curves["benchmark"] = {
        "dates": [p["trade_date"] for p in curve],
        "values": [
            round((p["benchmark_equity"] / 100000.0 - 1) * 100, 3) if p.get("benchmark_equity") else None
            for p in curve
        ],
    }
    rec_a = by_window["A_full"][rec_ma]
    return {
        "symbol": symbol,
        "name": SYMBOL_NAMES.get(symbol, ""),
        "data_range": meta["data_range"],
        "n_days": meta["n_days"],
        "windows": {
            k: {
                "label": meta["windows"][k]["label"],
                "start": meta["windows"][k]["start"],
                "end": meta["windows"][k]["end"],
                "n_days": meta["windows"][k]["n_days"],
                "batch_id": meta["windows"][k]["batch_id"],
                "benchmark": by_window[k][ma_axis[0]]["benchmark_return_pct"],
            }
            for k in WINDOW_KEYS
        },
        "ma_axis": ma_axis,
        "rets": rets,
        "sharpes": {k: [by_window[k][ma]["sharpe_ratio"] or 0 for ma in ma_axis] for k in WINDOW_KEYS},
        "dds": {k: [by_window[k][ma]["max_drawdown_pct"] for ma in ma_axis] for k in WINDOW_KEYS},
        "eligible": eligible,
        "joint": joint,
        "band": [lo, hi],
        "rec_ma": rec_ma,
        "rec": {
            "a": rec_a["total_return_pct"], "a_dd": rec_a["max_drawdown_pct"],
            "a_sharpe": rec_a["sharpe_ratio"], "a_trades": rec_a["trade_count"],
            "b": by_window["B_recent"][rec_ma]["total_return_pct"],
            "c": by_window["C_early"][rec_ma]["total_return_pct"],
        },
        "curves": curves,
        "table_rows": [
            {
                "ma": ma,
                "a_ret": by_window["A_full"][ma]["total_return_pct"],
                "a_sharpe": by_window["A_full"][ma]["sharpe_ratio"],
                "a_dd": by_window["A_full"][ma]["max_drawdown_pct"],
                "a_trades": by_window["A_full"][ma]["trade_count"],
                "b_ret": by_window["B_recent"][ma]["total_return_pct"],
                "b_sharpe": by_window["B_recent"][ma]["sharpe_ratio"],
                "b_dd": by_window["B_recent"][ma]["max_drawdown_pct"],
                "c_ret": by_window["C_early"][ma]["total_return_pct"],
                "c_sharpe": by_window["C_early"][ma]["sharpe_ratio"],
                "c_dd": by_window["C_early"][ma]["max_drawdown_pct"],
                "joint": joint[i],
                "eligible": eligible[i],
            }
            for i, ma in enumerate(ma_axis)
        ],
    }


def build_data() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    symbols = {}
    for symbol, meta in manifest["symbols"].items():
        info = load_symbol(conn, symbol, meta)
        info["reused"] = bool(meta.get("reused"))
        symbols[symbol] = info
    conn.close()

    # 三标的通吃：maximin 相对联合表现
    ma_axis = symbols["510880"]["ma_axis"]
    best_of = {s: max(v for v in symbols[s]["joint"] if v is not None) for s in symbols}
    maximin = []
    for i, ma in enumerate(ma_axis):
        rels = []
        for s in symbols:
            j = symbols[s]["joint"][i]
            if j is None:
                rels.append(None)
            else:
                rels.append(j / best_of[s])
        maximin.append(min(r for r in rels if r is not None) if any(r is not None for r in rels) else None)
    valid = [(ma_axis[i], m) for i, m in enumerate(maximin) if m is not None]
    cross_ma, cross_rel = max(valid, key=lambda p: p[1])

    return {
        "generated": f"{date.today():%Y-%m-%d}",
        "grid": manifest["grid"],
        "symbols": symbols,
        "cross": {"ma": cross_ma, "min_rel": cross_rel, "maximin": maximin},
    }


def render(data: dict) -> str:
    symbols = data["symbols"]
    cross = data["cross"]
    payload = json.dumps(data, ensure_ascii=False)
    order = ["510880", "159307", "513630"]

    cross_rows = []
    for s in order:
        d = symbols[s]
        cross_rows.append(
            f"<tr><td><b>{s}</b> {d['name']}</td>"
            f"<td>{d['data_range'][0]}~{d['data_range'][1]}<br><span class='dim'>{d['n_days']} 交易日"
            f"{'（复用已有批次）' if d['reused'] else ''}</span></td>"
            f"<td class='pos'>+{fmt(d['rec']['a'],1)}%</td><td>+{fmt(d['windows']['A_full']['benchmark'],1)}%</td>"
            f"<td><b>{d['rec_ma']}</b><br><span class='dim'>{d['band'][0]}~{d['band'][1]}</span></td>"
            f"<td>+{fmt(d['rec']['b'],1)}% / +{fmt(d['rec']['c'],1)}%</td>"
            f"<td>{d['rec']['a_dd']:.1f}% / {fmt(d['rec']['a_sharpe'])}</td></tr>"
        )
    cross_rows = "\n".join(cross_rows)

    sections = []
    for s in order:
        d = symbols[s]
        rows_html = []
        for r in d["table_rows"]:
            cls = []
            if d["band"][0] <= r["ma"] <= d["band"][1]:
                cls.append("cluster")
            if r["ma"] == d["rec_ma"]:
                cls.append("rec")
            cls_str = f" class='{' '.join(cls)}'" if cls else ""
            jnt = f"{r['joint']:.1f}" if r["joint"] is not None else "—"
            elig_mark = "" if r["eligible"] else " <span class='dim'>(预热不足)</span>"
            rows_html.append(
                f"<tr{cls_str}><td><b>{r['ma']}</b></td>"
                f"<td>{fmt(r['a_ret'])}</td><td>{fmt(r['a_sharpe'])}</td><td>{fmt(r['a_dd'],1)}</td><td>{r['a_trades']}</td>"
                f"<td>{fmt(r['b_ret'])}</td><td>{fmt(r['c_ret'])}</td>"
                f"<td>{jnt}{elig_mark}</td></tr>"
            )
        rows_html = "\n".join(rows_html)
        w = d["windows"]
        sections.append(f"""
<section class="card">
  <h2>{s} {d['name']} <span class="badge">推荐 MA{d['rec_ma']}（平台区 {d['band'][0]}~{d['band'][1]}）</span></h2>
  <div class="desc">数据 {d['data_range'][0]}~{d['data_range'][1]}（{d['n_days']} 交易日，baostock 后复权含分红）；
    A={w['A_full']['start']}~{w['A_full']['end']}（{w['A_full']['n_days']}日，基准 +{fmt(w['A_full']['benchmark'],1)}%）；
    前半段 C={w['C_early']['start']}~{w['C_early']['end']}（基准 +{fmt(w['C_early']['benchmark'],1)}%）；
    后半段 B={w['B_recent']['start']}~{w['B_recent']['end']}（基准 +{fmt(w['B_recent']['benchmark'],1)}%）。
    批次 #{w['A_full']['batch_id']}/#{w['C_early']['batch_id']}/#{w['B_recent']['batch_id']}。</div>
  <div class="kpi-grid">
    <div class="kpi"><div class="lab">推荐均线窗口</div><div class="val">MA = {d['rec_ma']}</div>
      <div class="sub">联合收益 {fmt(d['joint'][d['ma_axis'].index(d['rec_ma'])],1)}%（两段平均）</div></div>
    <div class="kpi"><div class="lab">全区间 A</div><div class="val up">+{fmt(d['rec']['a'],1)}%</div>
      <div class="sub">长持 +{fmt(w['A_full']['benchmark'],1)}% · 回撤 {fmt(d['rec']['a_dd'],1)}% · 夏普 {fmt(d['rec']['a_sharpe'])}</div></div>
    <div class="kpi"><div class="lab">后半段 B</div><div class="val up">+{fmt(d['rec']['b'],1)}%</div>
      <div class="sub">长持 +{fmt(w['B_recent']['benchmark'],1)}%</div></div>
    <div class="kpi"><div class="lab">前半段 C</div><div class="val up">+{fmt(d['rec']['c'],1)}%</div>
      <div class="sub">长持 +{fmt(w['C_early']['benchmark'],1)}%</div></div>
  </div>
  <div class="chart" id="ret-{s}"></div>
  <div class="grid2">
    <div><div class="h3">稳健性散点（前半 C × 后半 B）</div><div class="chart" id="sc-{s}"></div></div>
    <div><div class="h3">全区间权益曲线（推荐 vs MA40 vs 长持）</div><div class="chart" id="cu-{s}"></div></div>
  </div>
  <details><summary>49 组网格明细（联合收益 = 两段平均；「预热不足」= 前半段可交易日不足 {MIN_SIGNALABLE_DAYS} 天，不计入推荐）</summary>
  <table>
    <thead><tr><th>MA</th><th>A 收益%</th><th>A 夏普</th><th>A 回撤%</th><th>A 交易</th>
      <th>B 收益%</th><th>C 收益%</th><th>联合%</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table></details>
</section>""")

    sections_html = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETF 信号策略 · 均线窗口寻优（三标的）</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  :root{{
    --bg:#eef1f7; --card:#ffffff; --ink:#0f172a; --sub:#64748b; --line:#e2e8f0;
    --good:#059669; --bad:#dc2626; --accent:#1e3a8a;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hagino Sans GB","Microsoft YaHei",sans-serif;
    line-height:1.55;padding:28px 18px 60px}}
  .wrap{{max-width:1180px;margin:0 auto}}
  header.hero{{background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 60%,#312e81 100%);
    border-radius:20px;padding:32px 38px;color:#fff;box-shadow:0 10px 30px rgba(15,23,42,.18)}}
  .hero h1{{font-size:25px;font-weight:700;letter-spacing:.5px}}
  .hero p{{color:#c7d2fe;margin-top:8px;font-size:14px}}
  .chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}}
  .chip{{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.22);
    padding:4px 12px;border-radius:999px;font-size:12.5px;color:#e0e7ff}}
  .chip b{{color:#fff;font-weight:600}}
  section{{margin-top:22px}}
  .card{{background:var(--card);border-radius:16px;padding:22px 24px;
    box-shadow:0 1px 3px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.05)}}
  h2{{font-size:17px;font-weight:700;margin-bottom:4px}}
  .badge{{display:inline-block;background:#dbeafe;color:#1e40af;border-radius:999px;
    padding:2px 12px;font-size:12px;font-weight:600;vertical-align:middle;margin-left:8px}}
  .desc{{font-size:12.5px;color:var(--sub);margin-bottom:14px}}
  .dim{{color:var(--sub);font-size:11px}}
  .kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:12px 0}}
  .kpi{{background:#f8fafc;border:1px solid var(--line);border-radius:14px;padding:14px 16px 11px}}
  .kpi .lab{{font-size:12px;color:var(--sub)}}
  .kpi .val{{font-size:24px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums}}
  .kpi .sub{{font-size:11.5px;color:var(--sub);margin-top:2px}}
  .up{{color:var(--good)}}
  .pos{{color:var(--good)}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:14px}}
  @media (max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
  .h3{{font-size:13.5px;font-weight:700;margin:6px 0 2px}}
  .chart{{width:100%;height:340px;margin-top:8px}}
  table{{width:100%;border-collapse:collapse;font-size:12.8px;font-variant-numeric:tabular-nums}}
  th,td{{padding:6px 8px;text-align:right;border-bottom:1px solid var(--line)}}
  th{{color:var(--sub);font-weight:600;font-size:12px;white-space:nowrap}}
  th:first-child,td:first-child{{text-align:left}}
  tr.cluster td{{background:#eff6ff}}
  tr.rec td{{background:#dbeafe !important}}
  tr.rec td:first-child::after{{content:" ◀ 推荐";color:var(--accent);font-size:11px}}
  details{{margin-top:14px}}
  summary{{cursor:pointer;font-size:13px;color:var(--accent);font-weight:600;padding:6px 0}}
  .concl li{{margin:8px 0;font-size:13.5px}}
  .concl ul{{padding-left:20px}}
  .warn{{background:#fffbeb;border:1px solid #fde68a;border-radius:12px;padding:14px 16px;
    font-size:13px;color:#92400e;margin-top:14px}}
  footer{{margin-top:26px;font-size:12px;color:var(--sub);text-align:center}}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <h1>ETF 信号策略 · 均线窗口寻优（三标的对比）</h1>
  <p>510880 / 159307 / 513630 · 规则：收盘价低于 MA 买入，高于 MA×1.07 卖出 · 网格 MA ∈ 20~260 步长 5（每标的 3 窗口 × 49 组）</p>
  <div class="chips">
    <span class="chip">引擎 <b>signal_v1</b>（收盘价成交 · 佣金 0.02% · 整手 100）</span>
    <span class="chip">基准 <b>长持·买入持有</b></span>
    <span class="chip">数据 <b>baostock 后复权（含分红再投）</b></span>
    <span class="chip">三标的通吃 <b>MA{cross['ma']}</b>（maximin 最差相对表现 {cross['min_rel']*100:.1f}%）</span>
  </div>
</header>

<section class="card">
  <h2>三标的横向对比</h2>
  <div class="desc">510880 历史长（窗口按日历固定切：全区间 2018 起、早期/近三年），两只年轻 ETF 按各自数据对半切成互不重叠的前/后半段。
  各标的绝对收益不可直接横比（区间不同、长短不同），对比的重点是「收益 vs 均线窗口」的形态与平台区位置。</div>
  <table>
    <thead><tr><th>标的</th><th>数据范围</th><th>全区间 A 收益</th><th>A 长持基准</th><th>推荐 MA（平台区）</th><th>后半 B / 前半 C</th><th>A 回撤 / 夏普</th></tr></thead>
    <tbody>
{cross_rows}
    </tbody>
  </table>
</section>

{sections_html}

<section class="card concl">
  <h2>结论与建议</h2>
  <ul>
    <li><b>三只形态高度一致：长窗口显著优于短窗口</b>。MA20~60 在三只的全部窗口里都处于最差档；收益随窗口拉长整体抬升，并在 200+ 形成平台。红利类 ETF 波动节奏偏慢，短均线在这类资产上只会增加无效进出。</li>
    <li><b>205~220 是三只共同的强平台区</b>：510880 联合前三（205/210/215）、159307 前六含 215/220、513630 的 215 也在其最优的 99% 以内。若想三只共用一套参数，<b>MA{cross['ma']}</b> 是 maximin 最优（三只中最差的相对联合表现仍有 {cross['min_rel']*100:.1f}%）。</li>
    <li><b>逐只推荐</b>（各自平台区中心）：510880 → MA{symbols['510880']['rec_ma']}；159307 → MA{symbols['159307']['rec_ma']}；513630 → MA{symbols['513630']['rec_ma']}。差异来自各自数据长短与节奏，属正常波动。</li>
    <li><b>年轻标的置信度打折</b>：159307（599 交易日）与 513630（689 交易日）每半段仅约 300 个交易日、4~6 次交易，单次回合对结果影响大；前半段开头的 MA 预热死区已按「可交易 ≥ {MIN_SIGNALABLE_DAYS} 天」过滤出推荐范围。这两只的结论建议观察半年后再确认。</li>
    <li><b>下一步</b>：在 205~220 平台区内做 <code>exit_gap_min</code>（5~12 步长 1）二阶段联合寻优；三只若共用参数，优先验证 MA{cross['ma']} 与 MA215 两档。</li>
  </ul>
  <div class="warn">
    <b>边界与口径</b>：① 三只的回测区间不同（510880 日历固定切、两只新标的对半切），跨标的绝对收益不可比；② 510880 的全区间含近三年子段（与其 B/C 非独立），两只新标的前/后半段才是不相交验证；③ 后复权价含分红再投（立即再投资假设），绝对收益略偏乐观，策略与基准同口径不影响相对结论；④ 卖出阈值固定 7%、佣金 0.02% 未参与本轮网格；⑤ 单标的寻优有过拟合风险，红利风格 2021 后系统性走强对长窗口收益有贝塔贡献。
  </div>
</section>

<footer>
  生成于 {data['generated']} · 数据源 strategy_lab_etf_daily（baostock 后复权）·
  脚本 backtest_store/scripts/run_etf_ma_sweep_multi.py + build_etf_ma_sweep_report_multi.py（510880 复用 run_etf_ma_sweep.py 批次）
</footer>

</div>
<script>
const DATA = {payload};
const WSTYLE = {{
  A_full: {{color:'#1e3a8a', name:'全区间 A'}},
  B_recent: {{color:'#059669', name:'后半段 B'}},
  C_early: {{color:'#d97706', name:'前半段 C'}},
}};
const pct = v => v==null ? '-' : Number(v).toFixed(2)+'%';
const charts = [];
for (const [sym, d] of Object.entries(DATA.symbols)) {{
  // 收益 vs MA
  let ch = echarts.init(document.getElementById('ret-'+sym));
  ch.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis', valueFormatter:pct}},
    legend:{{top:0, textStyle:{{color:'#64748b'}}}},
    grid:{{left:52,right:20,top:44,bottom:36}},
    xAxis:{{type:'category', data:d.ma_axis, name:'MA窗口'}},
    yAxis:{{type:'value', scale:true, axisLabel:{{formatter:pct}}}},
    series:Object.keys(WSTYLE).map(k=>({{
      name:WSTYLE[k].name, type:'line', smooth:true, showSymbol:false,
      data:d.rets[k], lineStyle:{{width:2, color:WSTYLE[k].color}}, itemStyle:{{color:WSTYLE[k].color}},
      markLine:{{symbol:'none', silent:true,
        lineStyle:{{type:'dashed', color:WSTYLE[k].color, opacity:.45, width:1}},
        label:{{formatter:'基准'+DATA.symbols[sym].windows[k].benchmark.toFixed(1)+'%', fontSize:10}},
        data:[{{yAxis:DATA.symbols[sym].windows[k].benchmark}}]}},
    }})),
  }});
  charts.push(ch);
  // 散点 B x C
  ch = echarts.init(document.getElementById('sc-'+sym));
  const pts = d.ma_axis.map((ma,i)=>({{
    value:[d.rets.C_early[i], d.rets.B_recent[i], ma],
    symbolSize: ma===d.rec_ma ? 15 : (ma>=d.band[0] && ma<=d.band[1] ? 11 : 8),
    itemStyle: ma===d.rec_ma ? {{color:'#dc2626'}}
      : (d.eligible[i] && ma>=d.band[0] && ma<=d.band[1]) ? {{color:'#3b82f6', opacity:.9}}
      : d.eligible[i] ? {{color:'#94a3b8', opacity:.6}} : {{color:'#cbd5e1', opacity:.5}},
  }}));
  ch.setOption({{
    backgroundColor:'transparent',
    tooltip:{{formatter:p=>'MA'+p.value[2]+'：前半 '+p.value[0].toFixed(1)+'% · 后半 '+p.value[1].toFixed(1)+'%'}},
    grid:{{left:52,right:24,top:30,bottom:44}},
    xAxis:{{type:'value', name:'前半段收益%', scale:true, splitLine:{{lineStyle:{{color:'#e2e8f0'}}}}}},
    yAxis:{{type:'value', name:'后半段收益%', scale:true, splitLine:{{lineStyle:{{color:'#e2e8f0'}}}}}},
    series:[{{type:'scatter', data:pts,
      label:{{show:true, formatter:p=>[40,180,205,210,215,220,d.rec_ma].includes(p.value[2])?('MA'+p.value[2]):'', position:'top', fontSize:10, color:'#334155'}},
    }}],
  }});
  charts.push(ch);
  // 权益曲线
  ch = echarts.init(document.getElementById('cu-'+sym));
  ch.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis', valueFormatter:v=>Number(v).toFixed(1)+'%'}},
    legend:{{top:0, textStyle:{{color:'#64748b'}}}},
    grid:{{left:52,right:20,top:44,bottom:36}},
    xAxis:{{type:'category', data:d.curves.benchmark.dates}},
    yAxis:{{type:'value', scale:true, axisLabel:{{formatter:'{{value}}%'}}}},
    series:[
      {{name:'长持基准', type:'line', showSymbol:false, data:d.curves.benchmark.values,
        lineStyle:{{width:1.5, color:'#64748b', type:'dashed'}}, itemStyle:{{color:'#64748b'}}}},
      {{name:'MA40（短窗口代表）', type:'line', showSymbol:false, data:(d.curves['MA40']||{{values:[]}}).values,
        lineStyle:{{width:2, color:'#94a3b8'}}, itemStyle:{{color:'#94a3b8'}}}},
      {{name:'MA'+d.rec_ma+'（推荐）', type:'line', showSymbol:false, data:(d.curves['MA'+d.rec_ma]||{{values:[]}}).values,
        lineStyle:{{width:2, color:'#dc2626'}}, itemStyle:{{color:'#dc2626'}}}},
    ],
  }});
  charts.push(ch);
}}
window.addEventListener('resize', ()=>charts.forEach(c=>c.resize()));
</script>
</body>
</html>"""


def main() -> None:
    data = build_data()
    OUT_PATH.write_text(render(data), encoding="utf-8")
    print(f"报告已生成：{OUT_PATH}")
    print(f"大小：{OUT_PATH.stat().st_size / 1024:.1f} KB")
    for s in ["510880", "159307", "513630"]:
        d = data["symbols"][s]
        print(f"  {s}: rec=MA{d['rec_ma']} band={d['band']} A={d['rec']['a']:.1f}% B={d['rec']['b']:.1f}% C={d['rec']['c']:.1f}%")
    print(f"  cross maximin: MA{data['cross']['ma']} ({data['cross']['min_rel']*100:.1f}%)")


if __name__ == "__main__":
    main()
