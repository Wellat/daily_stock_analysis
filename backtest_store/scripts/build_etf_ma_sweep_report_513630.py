# -*- coding: utf-8 -*-
"""ETF 信号策略·均线窗口寻优 HTML 报告（513630 摩根标普港股通低波红利ETF 专属版）。

数据：backtest_store/raw/etf_ma_sweep_513630.json（批次 #15/#16/#17 + MA182 补充 run）。
推荐规则与前两份报告一致：联合收益 =（前半段 + 后半段）/2（互不重叠），
仅统计前半段预热后可交易 ≥60 天的均线；平台区 = 包含最优点的连续段（≥最优 95%）。

用法：.venv/bin/python backtest_store/scripts/build_etf_ma_sweep_report_513630.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "stock_analysis.db"
MANIFEST = REPO_ROOT / "backtest_store" / "raw" / "etf_ma_sweep_513630.json"
OUT_PATH = REPO_ROOT / "backtest_store" / f"ETF信号策略_均线窗口寻优_513630_{date.today():%Y-%m-%d}.html"

WINDOW_KEYS = ["A_full", "C_early", "B_recent"]
MIN_SIGNALABLE_DAYS = 60
BAND_RATIO = 0.95
CURVE_SAMPLES = [20, 40, 60]


def fmt(v, nd=2, suffix=""):
    if v is None:
        return "—"
    return f"{v:.{nd}f}{suffix}"


def build_data() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    by_window, items = {}, {}
    for key in WINDOW_KEYS:
        win = manifest["windows"][key]
        rows = conn.execute(
            """
            SELECT json_extract(i.parameters_json, '$.parameters.ma_window') AS ma, i.status,
                   m.total_return_pct, m.max_drawdown_pct, m.sharpe_ratio, m.win_rate_pct,
                   m.trade_count, r.benchmark_return_pct, r.equity_curve_json
            FROM strategy_lab_batch_items i
            JOIN strategy_lab_run_metrics m ON m.run_id = i.run_id
            JOIN strategy_lab_runs r ON r.id = i.run_id
            WHERE i.batch_id = ? ORDER BY ma
            """,
            (win["batch_id"],),
        ).fetchall()
        if len(rows) != 49 or any(r["status"] != "completed" for r in rows):
            raise SystemExit(f"批次 {win['batch_id']} 数据不完整")
        by_window[key] = {r["ma"]: dict(r) for r in rows}
        items[key] = [dict(r) for r in rows]

    ma_axis = [r["ma"] for r in items["A_full"]]
    rets = {k: [by_window[k][ma]["total_return_pct"] for ma in ma_axis] for k in WINDOW_KEYS}
    sharpes = {k: [by_window[k][ma]["sharpe_ratio"] or 0 for ma in ma_axis] for k in WINDOW_KEYS}
    dds = {k: [by_window[k][ma]["max_drawdown_pct"] for ma in ma_axis] for k in WINDOW_KEYS}
    trades_a = [by_window["A_full"][ma]["trade_count"] for ma in ma_axis]

    c_days = manifest["windows"]["C_early"]["n_days"]
    eligible = [(c_days - ma) >= MIN_SIGNALABLE_DAYS for ma in ma_axis]
    joint = [
        (rets["B_recent"][i] + rets["C_early"][i]) / 2 if eligible[i] else None
        for i in range(len(ma_axis))
    ]
    best_i = max((i for i, j in enumerate(joint) if j is not None), key=lambda i: joint[i])
    best_val = joint[best_i]
    lo = hi = best_i
    while lo - 1 >= 0 and joint[lo - 1] is not None and joint[lo - 1] >= best_val * BAND_RATIO:
        lo -= 1
    while hi + 1 < len(joint) and joint[hi + 1] is not None and joint[hi + 1] >= best_val * BAND_RATIO:
        hi += 1
    rec_ma = ma_axis[round((lo + hi) / 2)]
    band = [ma_axis[lo], ma_axis[hi]]

    curves = {}
    for item in items["A_full"]:
        if item["ma"] in CURVE_SAMPLES or item["ma"] == rec_ma:
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
    extras = {}
    for key, run in (manifest.get("extra_runs_ma182") or {}).items():
        row = conn.execute(
            "SELECT m.total_return_pct, m.max_drawdown_pct, m.sharpe_ratio, m.trade_count, r.equity_curve_json "
            "FROM strategy_lab_run_metrics m JOIN strategy_lab_runs r ON r.id=m.run_id WHERE m.run_id=?",
            (run["run_id"],),
        ).fetchone()
        if row:
            extras[key] = dict(row)
            if key == "A_full":
                c = json.loads(row["equity_curve_json"])
                curves["MA182"] = {
                    "dates": [p["trade_date"] for p in c],
                    "values": [round((p["equity"] / 100000.0 - 1) * 100, 3) for p in c],
                }
    conn.close()

    bench = {k: by_window[k][ma_axis[0]]["benchmark_return_pct"] for k in WINDOW_KEYS}
    return {
        "generated": f"{date.today():%Y-%m-%d}",
        "windows": manifest["windows"],
        "data_range": manifest["data_range"],
        "n_days": manifest["n_days"],
        "ma_axis": ma_axis,
        "rets": rets,
        "sharpes": sharpes,
        "dds": dds,
        "trades_a": trades_a,
        "eligible": eligible,
        "joint": joint,
        "band": band,
        "rec_ma": rec_ma,
        "bench": bench,
        "curves": curves,
        "extras": {k: {kk: vv for kk, vv in v.items() if kk != "equity_curve_json"} for k, v in extras.items()},
        "table_rows": [
            {
                "ma": ma,
                "a_ret": by_window["A_full"][ma]["total_return_pct"],
                "a_dd": by_window["A_full"][ma]["max_drawdown_pct"],
                "a_sharpe": by_window["A_full"][ma]["sharpe_ratio"],
                "a_trades": by_window["A_full"][ma]["trade_count"],
                "b_ret": by_window["B_recent"][ma]["total_return_pct"],
                "c_ret": by_window["C_early"][ma]["total_return_pct"],
                "joint": joint[i],
                "eligible": eligible[i],
            }
            for i, ma in enumerate(ma_axis)
        ],
    }


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    rows_html = []
    for r in data["table_rows"]:
        cls = []
        if 30 <= r["ma"] <= 60:
            cls.append("cluster")
        if r["ma"] == data["rec_ma"]:
            cls.append("rec")
        cls_str = f" class='{' '.join(cls)}'" if cls else ""
        jnt = f"{r['joint']:.1f}" if r["joint"] is not None else "—"
        mark = "" if r["eligible"] else " <span class='dim'>(预热不足)</span>"
        rows_html.append(
            f"<tr{cls_str}><td><b>{r['ma']}</b></td>"
            f"<td>{fmt(r['a_ret'])}</td><td>{fmt(r['a_dd'],1)}</td><td>{fmt(r['a_sharpe'])}</td><td>{r['a_trades']}</td>"
            f"<td>{fmt(r['c_ret'])}</td><td>{fmt(r['b_ret'])}</td><td>{jnt}{mark}</td></tr>"
        )
    rows_html = "\n".join(rows_html)
    w = data["windows"]
    rec_i = data["ma_axis"].index(data["rec_ma"])
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETF 信号策略 · 均线窗口寻优 · 513630 摩根标普港股通低波红利ETF</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  :root{{--bg:#eef1f7;--card:#fff;--ink:#0f172a;--sub:#64748b;--line:#e2e8f0;--good:#059669;--bad:#dc2626;--accent:#1e3a8a}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hagino Sans GB","Microsoft YaHei",sans-serif;line-height:1.55;padding:28px 18px 60px}}
  .wrap{{max-width:1180px;margin:0 auto}}
  header.hero{{background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 60%,#312e81 100%);border-radius:20px;padding:32px 38px;color:#fff;box-shadow:0 10px 30px rgba(15,23,42,.18)}}
  .hero h1{{font-size:24px;font-weight:700;letter-spacing:.5px}}
  .hero p{{color:#c7d2fe;margin-top:8px;font-size:14px}}
  .chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}}
  .chip{{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.22);padding:4px 12px;border-radius:999px;font-size:12.5px;color:#e0e7ff}}
  .chip b{{color:#fff}}
  section{{margin-top:22px}}
  .card{{background:var(--card);border-radius:16px;padding:22px 24px;box-shadow:0 1px 3px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.05)}}
  h2{{font-size:17px;font-weight:700;margin-bottom:4px}}
  .desc{{font-size:12.5px;color:var(--sub);margin-bottom:14px}}
  .dim{{color:var(--sub);font-size:11px}}
  .kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}}
  .kpi{{background:var(--card);border-radius:16px;padding:16px 18px 13px;box-shadow:0 1px 3px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.05)}}
  .kpi .lab{{font-size:12.5px;color:var(--sub)}}
  .kpi .val{{font-size:26px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums}}
  .kpi .sub{{font-size:11.5px;color:var(--sub);margin-top:2px}}
  .up{{color:var(--good)}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
  @media (max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
  .chart{{width:100%;height:360px}}
  .chart-tall{{width:100%;height:420px}}
  table{{width:100%;border-collapse:collapse;font-size:12.8px;font-variant-numeric:tabular-nums}}
  th,td{{padding:6px 8px;text-align:right;border-bottom:1px solid var(--line)}}
  th{{color:var(--sub);font-weight:600;font-size:12px;white-space:nowrap}}
  th:first-child,td:first-child{{text-align:left}}
  tr.cluster td{{background:#eff6ff}}
  tr.rec td{{background:#dbeafe !important}}
  tr.rec td:first-child::after{{content:" ◀ 联合最优";color:var(--accent);font-size:11px}}
  .concl li{{margin:8px 0;font-size:13.5px}}
  .concl ul{{padding-left:20px}}
  .warn{{background:#fffbeb;border:1px solid #fde68a;border-radius:12px;padding:14px 16px;font-size:13px;color:#92400e;margin-top:14px}}
  footer{{margin-top:26px;font-size:12px;color:var(--sub);text-align:center}}
  .pos{{color:var(--good)}}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <h1>513630 摩根标普港股通低波红利ETF · 均线窗口寻优</h1>
  <p>ETF signal 策略（收盘价低于 MA 买入，高于 MA×1.07 卖出）· 网格 MA ∈ 20~260 步长 5（3 窗口 × 49 组）</p>
  <div class="chips">
    <span class="chip">数据 <b>2023-12-08 ~ 2026-10-16（{data['n_days']} 交易日，baostock 后复权含分红）</b></span>
    <span class="chip">引擎 <b>signal_v1</b>（收盘成交 · 佣金 0.02% · 整手 100）</span>
    <span class="chip">基准 <b>长持·买入持有</b>（全区间 +{fmt(data['bench']['A_full'],1)}%）</span>
    <span class="chip">批次 <b>#{w['A_full']['batch_id']}/#{w['C_early']['batch_id']}/{w['B_recent']['batch_id']}</b></span>
  </div>
</header>

<section>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="lab">联合最优（前半+后半平均）</div>
      <div class="val">MA = {data['rec_ma']}</div>
      <div class="sub">平台区 {data['band'][0]}~{data['band'][1]}（单点——短端相邻参数波动大，稳健性弱）</div>
    </div>
    <div class="kpi">
      <div class="lab">全区间 A（{w['A_full']['start']} ~ {w['A_full']['end']}）</div>
      <div class="val up">+{fmt(data['rets']['A_full'][rec_i],1)}%</div>
      <div class="sub">长持 +{fmt(data['bench']['A_full'],1)}% · 回撤 {fmt(data['dds']['A_full'][rec_i],1)}% · 交易 {data['trades_a'][rec_i]} 次</div>
    </div>
    <div class="kpi">
      <div class="lab">前半段 C（{w['C_early']['start']} ~ {w['C_early']['end']}）</div>
      <div class="val up">+{fmt(data['rets']['C_early'][rec_i],1)}%</div>
      <div class="sub">长持 +{fmt(data['bench']['C_early'],1)}%</div>
    </div>
    <div class="kpi">
      <div class="lab">后半段 B（{w['B_recent']['start']} ~ {w['B_recent']['end']}）</div>
      <div class="val">{fmt(data['rets']['B_recent'][rec_i],1)}%</div>
      <div class="sub">长持 +{fmt(data['bench']['B_recent'],1)}%（后半段全部参数跑输长持）</div>
    </div>
  </div>
</section>

<section class="card">
  <h2>总收益率 vs 均线窗口（三窗口）</h2>
  <div class="desc">核心形态与 510880 相反：只有短窗口（20~60）能跟上涨幅；MA80 起衰减，MA200+ 几乎无交易（上市后价格长期位于长均线上方，「跌破买入」无买点）。虚线为各窗口长持基准。</div>
  <div id="chartReturn" class="chart-tall"></div>
</section>

<section>
  <div class="grid2">
    <div class="card">
      <h2>稳健性散点（前半 C × 后半 B）</h2>
      <div class="desc">右下方向（前半强、后半弱）为主——上半场吃到回调买点，下半场单边上行中策略普遍跑输。大点为联合最优 MA{data['rec_ma']}。</div>
      <div id="chartScatter" class="chart"></div>
    </div>
    <div class="card">
      <h2>全区间权益曲线对比</h2>
      <div class="desc">MA{data['rec_ma']}（联合最优）vs MA60 vs MA182（signal 默认）vs 长持。MA182 全程仅 3 笔、几乎空转。</div>
      <div id="chartCurve" class="chart"></div>
    </div>
  </div>
</section>

<section class="card">
  <h2>49 组网格明细</h2>
  <div class="desc">联合% = 前半+后半平均；蓝色底纹为 30~60 可用区间，深蓝为联合最优。「预热不足」= 前半段可交易日不足 {MIN_SIGNALABLE_DAYS} 天，不计入推荐。</div>
  <table>
    <thead><tr>
      <th>MA窗口</th><th>A 收益%</th><th>A 回撤%</th><th>A 夏普</th><th>A 交易</th>
      <th>C 前半%</th><th>B 后半%</th><th>联合%</th>
    </tr></thead>
    <tbody>
{rows_html}
    </tbody>
  </table>
</section>

<section class="card concl">
  <h2>结论与建议</h2>
  <ul>
    <li><b>该标的不适合这套「跌破均线买入」策略的长窗口版本</b>：513630 上市后整体单边上行（长持 +{fmt(data['bench']['A_full'],1)}%），价格长期位于 MA80+ 上方——MA200+ 全区间仅 1 次交易、收益 ≈0；即便是 MA182（signal 策略默认）也只有 <b>+{fmt(data['extras']['A_full']['total_return_pct'],1)}%</b>（3 笔，跑输长持 {fmt(data['bench']['A_full'] - data['extras']['A_full']['total_return_pct'],0)}pp）。</li>
    <li><b>若坚持使用，参数取中短端 30~60</b>：联合最优 MA{data['rec_ma']}（+{fmt(data['joint'][rec_i],1)}%），但平台区为单点、相邻参数差异大（20/30/40 全区间收益 {fmt(data['rets']['A_full'][data['ma_axis'].index(20)],1)}/{fmt(data['rets']['A_full'][rec_i],1)}/{fmt(data['rets']['A_full'][data['ma_axis'].index(40)],1)}%），保守可取 MA60（+{fmt(data['rets']['A_full'][data['ma_axis'].index(60)],1)}%、回撤 {fmt(data['dds']['A_full'][data['ma_axis'].index(60)],1)}%、参数面更平）。</li>
    <li><b>后半段（{w['B_recent']['start']} 起）所有参数跑输长持</b>（最好 {fmt(max(data['rets']['B_recent']),1)}% vs 长持 +{fmt(data['bench']['B_recent'],1)}%）——单边上行段「等回调」天然吃亏，这套策略的收益集中在前半段的回调修复。</li>
    <li><b>与 510880 的差异是行情结构差异，不是参数没调好</b>：510880（2016 年上市、多轮牛熊）同类策略最优在 MA205~220（全区间 +210%）；513630 样本只有上行段，长窗口逻辑没有用武之地。若行情转入震荡/回调，本报告的短端结论需重估。</li>
    <li><b>务实建议</b>：认可港股红利低波逻辑的话，当前样本下「长持/定投」即是近似最优；要用均线体系参与，等回调市样本积累后再寻优，或改用趋势跟随型入场（上穿均线买入）另行回测验证。</li>
  </ul>
  <div class="warn">
    <b>边界与口径</b>：① 样本仅 {data['n_days']} 个交易日且只覆盖上行段，参数结论对行情结构高度敏感；② 每半段约 340 个交易日、策略交易次数少（短端 5~9 笔），单次回合影响大；③ 后复权价含分红再投（月度分红 ETF，立即再投资假设略偏乐观，策略与基准同口径）；④ 卖出阈值固定 7%、佣金 0.02% 未参与网格；⑤ 本报告修正了此前三标的报告中 513630 批次误用 510880 数据的错误（批次 #15~#17 重跑）。
  </div>
</section>

<footer>
  生成于 {data['generated']} · 数据源 strategy_lab_etf_daily（baostock 后复权）·
  脚本 backtest_store/scripts/run_etf_ma_sweep_multi.py + build_etf_ma_sweep_report_513630.py
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
let ch = echarts.init(document.getElementById('chartReturn'));
ch.setOption({{
  backgroundColor:'transparent',
  tooltip:{{trigger:'axis', valueFormatter:pct}},
  legend:{{top:0, textStyle:{{color:'#64748b'}}}},
  grid:{{left:52,right:20,top:44,bottom:36}},
  xAxis:{{type:'category', data:DATA.ma_axis, name:'MA窗口'}},
  yAxis:{{type:'value', scale:true, axisLabel:{{formatter:pct}}}},
  series:Object.keys(WSTYLE).map(k=>({{
    name:WSTYLE[k].name, type:'line', smooth:true, showSymbol:false,
    data:DATA.rets[k], lineStyle:{{width:2, color:WSTYLE[k].color}}, itemStyle:{{color:WSTYLE[k].color}},
    markLine:{{symbol:'none', silent:true, lineStyle:{{type:'dashed', color:WSTYLE[k].color, opacity:.45, width:1}},
      label:{{formatter:'基准'+DATA.bench[k].toFixed(1)+'%', fontSize:10}},
      data:[{{yAxis:DATA.bench[k]}}]}},
  }})),
}});
ch = echarts.init(document.getElementById('chartScatter'));
ch.setOption({{
  backgroundColor:'transparent',
  tooltip:{{formatter:p=>'MA'+p.value[2]+'：前半 '+p.value[0].toFixed(1)+'% · 后半 '+p.value[1].toFixed(1)+'%'}},
  grid:{{left:52,right:24,top:30,bottom:44}},
  xAxis:{{type:'value', name:'前半段收益%', scale:true, splitLine:{{lineStyle:{{color:'#e2e8f0'}}}}}},
  yAxis:{{type:'value', name:'后半段收益%', scale:true, splitLine:{{lineStyle:{{color:'#e2e8f0'}}}}}},
  series:[{{type:'scatter',
    data:DATA.ma_axis.map((ma,i)=>({{
      value:[DATA.rets.C_early[i], DATA.rets.B_recent[i], ma],
      symbolSize: ma===DATA.rec_ma ? 15 : (ma>=30 && ma<=60 ? 11 : 8),
      itemStyle: ma===DATA.rec_ma ? {{color:'#dc2626'}}
        : (ma>=30 && ma<=60) ? {{color:'#3b82f6', opacity:.9}}
        : {{color:'#94a3b8', opacity:.6}},
    }})),
    label:{{show:true, formatter:p=>[20,30,40,60,80,120,182,210].includes(p.value[2])?('MA'+p.value[2]):'', position:'top', fontSize:10, color:'#334155'}},
  }}],
}});
ch = echarts.init(document.getElementById('chartCurve'));
const mk=(name,key,color,dash)=>({{
  name, type:'line', showSymbol:false, data:(DATA.curves[key]||{{values:[]}}).values,
  lineStyle:{{width: key==='benchmark'?1.5:2, color, type:dash||'solid'}}, itemStyle:{{color}},
}});
ch.setOption({{
  backgroundColor:'transparent',
  tooltip:{{trigger:'axis', valueFormatter:v=>Number(v).toFixed(1)+'%'}},
  legend:{{top:0, textStyle:{{color:'#64748b'}}}},
  grid:{{left:52,right:20,top:44,bottom:36}},
  xAxis:{{type:'category', data:DATA.curves.benchmark.dates}},
  yAxis:{{type:'value', scale:true, axisLabel:{{formatter:'{{value}}%'}}}},
  series:[
    mk('长持基准','benchmark','#64748b','dashed'),
    mk('MA182（signal 默认）','MA182','#f59e0b'),
    mk('MA60（保守短端）','MA60','#059669'),
    mk('MA'+DATA.rec_ma+'（联合最优）','MA'+DATA.rec_ma,'#dc2626'),
  ],
}});
window.addEventListener('resize', ()=>document.querySelectorAll('.chart,.chart-tall').forEach(el=>{{
  const i=echarts.getInstanceByDom(el); if(i) i.resize();
}}));
</script>
</body>
</html>"""


def main() -> None:
    data = build_data()
    OUT_PATH.write_text(render(data), encoding="utf-8")
    print(f"报告已生成：{OUT_PATH}")
    print(f"大小：{OUT_PATH.stat().st_size / 1024:.1f} KB")
    print(f"rec=MA{data['rec_ma']} band={data['band']} joint={fmt(data['joint'][data['ma_axis'].index(data['rec_ma'])],1)}%")


if __name__ == "__main__":
    main()
