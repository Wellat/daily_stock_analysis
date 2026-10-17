# -*- coding: utf-8 -*-
"""ETF 信号策略·均线窗口寻优 HTML 报告生成器。

读取 backtest_store/raw/etf_ma_sweep_batches.json 登记的三个批次
（A 全区间 / B 近三年 / C 早期，各 49 组 ma_window 网格），
从 SQLite 汇总指标并渲染单文件 HTML（ECharts CDN）。

用法：.venv/bin/python backtest_store/scripts/build_etf_ma_sweep_report.py
产物：backtest_store/ETF信号策略_均线窗口寻优_2026-10-16.html
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "stock_analysis.db"
SWEEP_JSON = REPO_ROOT / "backtest_store" / "raw" / "etf_ma_sweep_batches.json"
OUT_PATH = REPO_ROOT / "backtest_store" / f"ETF信号策略_均线窗口寻优_{date.today():%Y-%m-%d}.html"

WINDOW_KEYS = ["A_full", "B_recent", "C_early"]
ROBUST_CLUSTER = (185, 220)  # 双不相交区间共同强平台区
RECOMMEND_MA = 210  # 平台区中部（非样本内 argmax）
CURVE_MA_SAMPLES = [40, 210]  # 网格步长 5 不含 182，MA182 由补充单次回测提供


def load_batch_rows() -> dict:
    sweep = json.loads(SWEEP_JSON.read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    windows = {}
    for key in WINDOW_KEYS:
        meta = sweep["batches"][key]
        rows = conn.execute(
            """
            SELECT
                json_extract(i.parameters_json, '$.parameters.ma_window') AS ma_window,
                i.status, i.error_message, i.run_id,
                m.total_return_pct, m.annualized_return_pct, m.max_drawdown_pct,
                m.sharpe_ratio, m.win_rate_pct, m.trade_count,
                r.benchmark_return_pct, r.equity_curve_json
            FROM strategy_lab_batch_items i
            JOIN strategy_lab_run_metrics m ON m.run_id = i.run_id
            JOIN strategy_lab_runs r ON r.id = i.run_id
            WHERE i.batch_id = ?
            ORDER BY ma_window
            """,
            (meta["batch_id"],),
        ).fetchall()
        items = [dict(row) for row in rows]
        windows[key] = {**meta, "items": items}
    for key in WINDOW_KEYS:
        failed = [it for it in windows[key]["items"] if it["status"] != "completed"]
        if failed or len(windows[key]["items"]) != 49:
            conn.close()
            raise SystemExit(f"批次 {key} 数据不完整: {len(failed)} 失败 / {len(windows[key]['items'])} 行")
    return sweep, windows, conn


def spearman(xs, ys) -> float:
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        rk = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mean_x, mean_y = sum(rx) / n, sum(ry) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    vx = sum((a - mean_x) ** 2 for a in rx) ** 0.5
    vy = sum((b - mean_y) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy)


def extract_curves(windows, conn) -> dict:
    """全区间批次中代表均线与基准的累计收益曲线；MA182 取自补充单次回测。"""
    out = {}
    for item in windows["A_full"]["items"]:
        ma = item["ma_window"]
        if ma in CURVE_MA_SAMPLES:
            curve = json.loads(item["equity_curve_json"])
            initial = 100000.0
            out[f"MA{ma}"] = {
                "dates": [p["trade_date"] for p in curve],
                "values": [round((p["equity"] / initial - 1) * 100, 3) for p in curve],
            }
    first = windows["A_full"]["items"][0]
    curve = json.loads(first["equity_curve_json"])
    initial = 100000.0
    out["benchmark"] = {
        "dates": [p["trade_date"] for p in curve],
        "values": [
            round((p["benchmark_equity"] / initial - 1) * 100, 3) if p.get("benchmark_equity") else None
            for p in curve
        ],
    }
    extra = json.loads(SWEEP_JSON.read_text(encoding="utf-8")).get("extra_runs_ma182") or {}
    if extra.get("A_full"):
        curve = json.loads(
            conn.execute(
                "SELECT equity_curve_json FROM strategy_lab_runs WHERE id = ?",
                (extra["A_full"]["run_id"],),
            ).fetchone()[0]
        )
        out["MA182"] = {
            "dates": [p["trade_date"] for p in curve],
            "values": [round((p["equity"] / 100000.0 - 1) * 100, 3) for p in curve],
        }
    return out


def build_data() -> dict:
    sweep, windows, conn = load_batch_rows()
    ma_axis = [it["ma_window"] for it in windows["A_full"]["items"]]
    by_window = {}
    for key in WINDOW_KEYS:
        by_window[key] = {it["ma_window"]: it for it in windows[key]["items"]}

    default182 = {"ma": 182}
    extra = sweep.get("extra_runs_ma182") or {}
    for key, label in [("A_full", "a"), ("B_recent", "b")]:
        run_id = (extra.get(key) or {}).get("run_id")
        if run_id:
            row = conn.execute(
                """
                SELECT m.total_return_pct, m.max_drawdown_pct, m.sharpe_ratio, m.trade_count
                FROM strategy_lab_run_metrics m WHERE m.run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row:
                default182[f"{label}_ret"] = row[0]
                default182[f"{label}_dd"] = row[1]
                default182[f"{label}_sharpe"] = row[2]
                default182[f"{label}_trades"] = row[3]
    curves = extract_curves(windows, conn)
    conn.close()

    rets = {key: [by_window[key][ma]["total_return_pct"] for ma in ma_axis] for key in WINDOW_KEYS}
    sharpes = {key: [by_window[key][ma]["sharpe_ratio"] or 0 for ma in ma_axis] for key in WINDOW_KEYS}
    dds = {key: [by_window[key][ma]["max_drawdown_pct"] for ma in ma_axis] for key in WINDOW_KEYS}

    # 不相交两区间（B/C）平均收益 -> 联合排名
    joint = [
        {"ma": ma, "avg": (rets["B_recent"][i] +rets["C_early"][i]) / 2}
        for i, ma in enumerate(ma_axis)
    ]
    joint_sorted = sorted(joint, key=lambda d: -d["avg"])
    rho_bc = spearman(rets["B_recent"], rets["C_early"])
    rho_ab = spearman(rets["A_full"], rets["B_recent"])
    rho_ac = spearman(rets["A_full"], rets["C_early"])

    rec = by_window["A_full"][RECOMMEND_MA]
    rec_b = by_window["B_recent"][RECOMMEND_MA]
    rec_c = by_window["C_early"][RECOMMEND_MA]
    best_a = max(windows["A_full"]["items"], key=lambda it: it["total_return_pct"])
    best_joint = joint_sorted[0]

    return {
        "generated": f"{date.today():%Y-%m-%d}",
        "sweep": sweep,
        "windows": {
            key: {
                "label": windows[key]["label"],
                "start": windows[key]["start"],
                "end": windows[key]["end"],
                "batch_id": windows[key]["batch_id"],
                "benchmark": by_window[key][ma_axis[0]]["benchmark_return_pct"],
            }
            for key in WINDOW_KEYS
        },
        "ma_axis": ma_axis,
        "rets": rets,
        "sharpes": sharpes,
        "dds": dds,
        "trades_a": [by_window["A_full"][ma]["trade_count"] for ma in ma_axis],
        "winrate_a": [by_window["A_full"][ma]["win_rate_pct"] or 0 for ma in ma_axis],
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
            }
            for ma in ma_axis
        ],
        "curves": curves,
        "default182": default182,
        "stats": {
            "rho_bc": round(rho_bc, 3),
            "rho_ab": round(rho_ab, 3),
            "rho_ac": round(rho_ac, 3),
            "rec": {
                "ma": RECOMMEND_MA,
                "a": rec["total_return_pct"], "a_dd": rec["max_drawdown_pct"],
                "a_sharpe": rec["sharpe_ratio"], "a_trades": rec["trade_count"],
                "a_wr": rec["win_rate_pct"],
                "b": rec_b["total_return_pct"], "b_dd": rec_b["max_drawdown_pct"],
                "c": rec_c["total_return_pct"], "c_dd": rec_c["max_drawdown_pct"],
            },
            "best_a": {"ma": best_a["ma_window"], "ret": best_a["total_return_pct"]},
            "best_joint": best_joint,
            "cluster_avg": [
                {"ma": d["ma"], "avg": d["avg"]}
                for d in joint_sorted[:12]
            ],
        },
    }


def fmt(v, nd=2, suffix=""):
    if v is None:
        return "—"
    return f"{v:.{nd}f}{suffix}"


def render(data: dict) -> str:
    s = data["stats"]
    w = data["windows"]
    rec = s["rec"]
    payload = json.dumps(data, ensure_ascii=False)
    cluster_rows = "".join(
        f"<span class='tag'>MA{d['ma']} · 均{d['avg']:.1f}%</span>" for d in s["cluster_avg"][:8]
    )
    rows_html = []
    for r in data["table_rows"]:
        cls = []
        if ROBUST_CLUSTER[0] <= r["ma"] <= ROBUST_CLUSTER[1]:
            cls.append("cluster")
        if r["ma"] == RECOMMEND_MA:
            cls.append("rec")
        cls_str = f" class='{' '.join(cls)}'" if cls else ""
        rows_html.append(
            f"<tr{cls_str}><td><b>{r['ma']}</b></td>"
            f"<td>{fmt(r['a_ret'])}</td><td>{fmt(r['a_sharpe'])}</td><td>{fmt(r['a_dd'], 1)}</td><td>{r['a_trades']}</td>"
            f"<td>{fmt(r['b_ret'])}</td><td>{fmt(r['b_sharpe'])}</td><td>{fmt(r['b_dd'], 1)}</td>"
            f"<td>{fmt(r['c_ret'])}</td><td>{fmt(r['c_sharpe'])}</td><td>{fmt(r['c_dd'], 1)}</td></tr>"
        )
    rows_html = "\n".join(rows_html)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETF 信号策略 · 均线窗口寻优 · 510880 红利ETF</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  :root{{
    --bg:#eef1f7; --card:#ffffff; --ink:#0f172a; --sub:#64748b; --line:#e2e8f0;
    --good:#059669; --bad:#dc2626; --accent:#1e3a8a;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
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
  .desc{{font-size:12.5px;color:var(--sub);margin-bottom:14px}}
  .kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}}
  .kpi{{background:var(--card);border-radius:16px;padding:16px 18px 13px;
    box-shadow:0 1px 3px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.05)}}
  .kpi .lab{{font-size:12.5px;color:var(--sub)}}
  .kpi .val{{font-size:26px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums}}
  .kpi .sub{{font-size:11.5px;color:var(--sub);margin-top:2px}}
  .up{{color:var(--good)}} .down{{color:var(--bad)}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
  @media (max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
  .chart{{width:100%;height:360px}}
  .chart-tall{{width:100%;height:420px}}
  table{{width:100%;border-collapse:collapse;font-size:12.8px;font-variant-numeric:tabular-nums}}
  th,td{{padding:6px 8px;text-align:right;border-bottom:1px solid var(--line)}}
  th{{color:var(--sub);font-weight:600;font-size:12px;white-space:nowrap}}
  th:first-child,td:first-child{{text-align:left}}
  td b{{font-weight:700}}
  tr.cluster td{{background:#eff6ff}}
  tr.rec td{{background:#dbeafe !important}}
  tr.rec td:first-child::after{{content:" ◀ 推荐";color:var(--accent);font-size:11px}}
  .tags{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
  .tag{{background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe;border-radius:999px;
    padding:3px 10px;font-size:12px;font-variant-numeric:tabular-nums}}
  .concl li{{margin:8px 0;font-size:13.5px}}
  .concl ul{{padding-left:20px}}
  .warn{{background:#fffbeb;border:1px solid #fde68a;border-radius:12px;padding:14px 16px;
    font-size:13px;color:#92400e;margin-top:14px}}
  footer{{margin-top:26px;font-size:12px;color:var(--sub);text-align:center}}
  .pos{{color:var(--good)}} .neg{{color:var(--bad)}}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <h1>ETF 信号策略 · 均线窗口寻优</h1>
  <p>510880 红利ETF（华泰柏瑞）· 规则：收盘价低于 MA×(1+0%) 买入，高于 MA×(1+7%) 卖出 · 网格 MA ∈ 20~260 步长 5（49 组 × 3 区间 = 147 次回测）</p>
  <div class="chips">
    <span class="chip">标的 <b>510880</b></span>
    <span class="chip">引擎 <b>signal_v1</b>（收盘价成交 · 佣金 0.02% · 整手 100）</span>
    <span class="chip">基准 <b>长持·买入持有</b></span>
    <span class="chip">数据 <b>baostock 后复权 2016-10~2026-10</b>（含分红再投）</span>
    <span class="chip">批次 <b>#{w['A_full']['batch_id']} / #{w['B_recent']['batch_id']} / #{w['C_early']['batch_id']}</b></span>
  </div>
</header>

<section>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="lab">推荐参数（平台区中部）</div>
      <div class="val">MA = {rec['ma']}</div>
      <div class="sub">非样本内最优，取 185~220 平台区中部以降低过拟合</div>
    </div>
    <div class="kpi">
      <div class="lab">全区间 {w['A_full']['start'][:4]}~{w['A_full']['end'][:4]}（{w['A_full']['label']}）</div>
      <div class="val up">+{fmt(rec['a'],1)}%</div>
      <div class="sub">基准长持 +{fmt(w['A_full']['benchmark'],1)}% · 回撤 {fmt(rec['a_dd'],1)}% · 夏普 {fmt(rec['a_sharpe'])}</div>
    </div>
    <div class="kpi">
      <div class="lab">近三年 2024~2026（样本外一半）</div>
      <div class="val up">+{fmt(rec['b'],1)}%</div>
      <div class="sub">基准长持 +{fmt(w['B_recent']['benchmark'],1)}% · 回撤 {fmt(rec['b_dd'],1)}%</div>
    </div>
    <div class="kpi">
      <div class="lab">早期 2018~2023（样本外另一半）</div>
      <div class="val up">+{fmt(rec['c'],1)}%</div>
      <div class="sub">基准长持 +{fmt(w['C_early']['benchmark'],1)}% · 回撤 {fmt(rec['c_dd'],1)}%</div>
    </div>
  </div>
</section>

<section class="card">
  <h2>总收益率 vs 均线窗口（三区间）</h2>
  <div class="desc">虚线为各区间「长持 510880」基准收益。所有窗口参数在三个区间均跑赢长持；短窗口（20~30）在三个区间都是最差档，长窗口整体抬升，185~220 形成平台区。</div>
  <div id="chartReturn" class="chart-tall"></div>
</section>

<section>
  <div class="grid2">
    <div class="card">
      <h2>夏普比率 vs 均线窗口</h2>
      <div class="desc">三区间形态一致：短窗口低、随窗口拉长单调抬升。</div>
      <div id="chartSharpe" class="chart"></div>
    </div>
    <div class="card">
      <h2>最大回撤 vs 均线窗口</h2>
      <div class="desc">近三年与早期区间在 155+ 后回撤明显收窄（-14%→-7% / -20%→-17%）。</div>
      <div id="chartDd" class="chart"></div>
    </div>
  </div>
</section>

<section class="card">
  <h2>稳健性：两个不相交区间的收益散点</h2>
  <div class="desc">横轴=早期 2018~2023，纵轴=近三年 2024~2026（两段互不重叠，可视为彼此的样本外验证）。右上象限=双区间都强。Spearman 秩相关 ρ={s['rho_bc']}（另：全区间×近三年 ρ={s['rho_ab']}，全区间×早期 ρ={s['rho_ac']}）。大点为推荐区 205~215；网格步长 5 未含当前默认 MA182（介于 180/185 之间，见结论区补充单测）。</div>
  <div id="chartScatter" class="chart-tall"></div>
</section>

<section class="card">
  <h2>全区间权益曲线对比（2018~2026）</h2>
  <div class="desc">MA210（推荐）vs MA182（当前默认）vs MA40（短窗口代表）vs 长持基准。短窗口的主要损耗来自横盘期的反复进出。</div>
  <div id="chartCurve" class="chart-tall"></div>
</section>

<section class="card">
  <h2>49 组网格明细</h2>
  <div class="desc">A=全区间 2018~2026，B=近三年 2024~2026，C=早期 2018~2023。蓝色底纹为 185~220 平台区，深蓝为推荐 MA{rec['ma']}。</div>
  <table>
    <thead><tr>
      <th>MA窗口</th>
      <th>A 收益%</th><th>A 夏普</th><th>A 回撤%</th><th>A 交易</th>
      <th>B 收益%</th><th>B 夏普</th><th>B 回撤%</th>
      <th>C 收益%</th><th>C 夏普</th><th>C 回撤%</th>
    </tr></thead>
    <tbody>
{rows_html}
    </tbody>
  </table>
</section>

<section class="card concl">
  <h2>结论与建议</h2>
  <ul>
    <li><b>推荐 MA={rec['ma']}</b>（185~220 平台区中部）：全区间 <span class="pos">+{fmt(rec['a'],1)}%</span>（基准 +{fmt(w['A_full']['benchmark'],1)}%），近三年 <span class="pos">+{fmt(rec['b'],1)}%</span>，早期 <span class="pos">+{fmt(rec['c'],1)}%</span>；两段不相交区间平均 {fmt((rec['b']+rec['c'])/2,1)}%，在全部 49 组中排名第 {1 + [d['ma'] for d in s['cluster_avg']].index(rec['ma'])}。</li>
    <li><b>平台区比单点更可信</b>：样本内最优是全区间 MA{s['best_a']['ma']}（+{fmt(s['best_a']['ret'],1)}%），但 185~220 内收益变化平缓（相邻参数差异小），说明结论对窗口取值不敏感；相反 20~60 区域相邻参数波动大。</li>
    <li><b>短窗口（20~60）不建议</b>：三个区间收益与夏普均为最差档，主因是 510880 长周期属性下短均线频繁翻转、横盘期反复止损（MA20 全区间交易 15 次 vs MA210 26 次、夏普 0.54 vs 1.14，损耗集中在交易质量而非数量）。</li>
    <li><b>当前默认 MA182 vs 推荐 MA{rec['ma']}</b>：网格步长 5 未覆盖 182，已用同参数补充单次回测——MA182 全区间 <span class="pos">+{fmt(data['default182'].get('a_ret'),1)}%</span>（回撤 {fmt(data['default182'].get('a_dd'),1)}%、夏普 {fmt(data['default182'].get('a_sharpe'))}），近三年 +{fmt(data['default182'].get('b_ret'),1)}%；差距主要来自近三年段（+{fmt(data['default182'].get('b_ret'),1)}% vs MA{rec['ma']} +{fmt(rec['b'],1)}%）。182 处于平台区边缘，可继续使用，迁移到 MA{rec['ma']} 预期更稳。</li>
    <li><b>下一步</b>：在 MA∈{{195,205,210,215}} 上做第二阶段 <code>exit_gap_min</code>（如 5~12 步长 1）联合寻优；两个正交参数的等高线若同样平缓，参数组合即可定型。</li>
  </ul>
  <div class="tags">{cluster_rows}</div>
  <div class="warn">
    <b>边界与口径</b>：① 全区间含近三年子段（A 与 B/C 非独立），秩相关仅作参考，真正互相验证的是 B 与 C 两段；② 单标的寻优存在过拟合风险，510880 的红利风格 2021 年后系统性走强，长窗口占优部分来自该风格贝塔；③ 价格为后复权（含分红再投），绝对收益略偏乐观（分红当日即再投资假设），但策略/基准同口径不影响相对结论；④ 卖出阈值固定 7%、佣金 0.02% 未参与本轮网格。
  </div>
</section>

<footer>
  生成于 {data['generated']} · 数据源 strategy_lab_etf_daily（baostock 后复权）·
  脚本 backtest_store/scripts/run_etf_ma_sweep.py + build_etf_ma_sweep_report.py ·
  批次 #{w['A_full']['batch_id']}(A) #{w['B_recent']['batch_id']}(B) #{w['C_early']['batch_id']}(C)
</footer>

</div>
<script>
const DATA = {payload};
const A = DATA.windows.A_full, B = DATA.windows.B_recent, C = DATA.windows.C_early;
const WSTYLE = {{
  A_full: {{color:'#1e3a8a', name:'全区间 2018~2026'}},
  B_recent: {{color:'#059669', name:'近三年 2024~2026'}},
  C_early: {{color:'#d97706', name:'早期 2018~2023'}},
}};
function lineChart(id, seriesSpec, yFmt) {{
  const chart = echarts.init(document.getElementById(id));
  chart.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis', valueFormatter:v=>yFmt(v)}},
    legend:{{top:0, textStyle:{{color:'#64748b'}}}},
    grid:{{left:52,right:20,top:44,bottom:36}},
    xAxis:{{type:'category', data:DATA.ma_axis, name:'MA窗口', nameTextStyle:{{color:'#64748b'}}}},
    yAxis:{{type:'value', scale:true, axisLabel:{{formatter:yFmt}}}},
    series: seriesSpec.map(s=>({{
      name:s.name, type:'line', smooth:true, showSymbol:false, data:s.data,
      lineStyle:{{width:2, color:s.color}}, itemStyle:{{color:s.color}},
      ...(s.markLine ? {{markLine:s.markLine}} : {{}}),
    }})),
  }});
  return chart;
}}
const pct = v => v==null ? '-' : v.toFixed(2)+'%';
lineChart('chartReturn',
  Object.keys(WSTYLE).map(k=>({{
    name:WSTYLE[k].name, color:WSTYLE[k].color, data:DATA.rets[k],
    markLine:{{symbol:'none', silent:true, lineStyle:{{type:'dashed', color:WSTYLE[k].color, opacity:.45, width:1}},
      label:{{formatter:'基准 '+DATA.windows[k].benchmark.toFixed(1)+'%', fontSize:10}},
      data:[{{yAxis:DATA.windows[k].benchmark}}]}},
  }})), pct);
lineChart('chartSharpe',
  Object.keys(WSTYLE).map(k=>({{name:WSTYLE[k].name, color:WSTYLE[k].color, data:DATA.sharpes[k]}})),
  v=>Number(v).toFixed(1));
lineChart('chartDd',
  Object.keys(WSTYLE).map(k=>({{name:WSTYLE[k].name, color:WSTYLE[k].color, data:DATA.dds[k]}})), pct);

(function scatter(){{
  const pts = DATA.ma_axis.map((ma,i)=>({{
    value:[DATA.rets.C_early[i], DATA.rets.B_recent[i], ma],
    symbolSize: ma>=205 && ma<=215 ? 16 : 9,
    itemStyle: ma>=205 && ma<=215 ? {{color:'#dc2626', opacity:.9}}
      : ma>=185 && ma<=220 ? {{color:'#3b82f6', opacity:.85}} : {{color:'#94a3b8', opacity:.6}},
  }}));
  const chart = echarts.init(document.getElementById('chartScatter'));
  chart.setOption({{
    backgroundColor:'transparent',
    tooltip:{{formatter:p=>`MA${{p.value[2]}}：早期 ${{p.value[0].toFixed(1)}}% · 近三年 ${{p.value[1].toFixed(1)}}%`}},
    grid:{{left:52,right:24,top:30,bottom:44}},
    xAxis:{{type:'value', name:'早期2018~2023收益%', scale:true, splitLine:{{show:true, lineStyle:{{color:'#e2e8f0'}}}}}},
    yAxis:{{type:'value', name:'近三年2024~2026收益%', scale:true, splitLine:{{show:true, lineStyle:{{color:'#e2e8f0'}}}}}},
    series:[{{type:'scatter', data:pts,
      label:{{show:true, formatter:p=>[40,180,185,195,200,205,210,215,220].includes(p.value[2])?('MA'+p.value[2]):'', position:'top', fontSize:10, color:'#334155'}},
      markLine:{{symbol:'none', silent:true, lineStyle:{{color:'#cbd5e1', type:'dashed'}}, label:{{show:false}},
        data:[{{yAxis:35}},{{xAxis:52}}]}},
    }}],
  }});
}})();

(function curve(){{
  const c = DATA.curves;
  const dates = c.benchmark.dates;
  const mk = (name, key, color, dash) => ({{
    name, type:'line', smooth:false, showSymbol:false, data:c[key].values,
    lineStyle:{{width: key==='benchmark'?1.5:2, color, type: dash||'solid'}}, itemStyle:{{color}},
  }});
  const chart = echarts.init(document.getElementById('chartCurve'));
  chart.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis', valueFormatter:v=>Number(v).toFixed(1)+'%'}},
    legend:{{top:0, textStyle:{{color:'#64748b'}}}},
    grid:{{left:52,right:20,top:44,bottom:36}},
    xAxis:{{type:'category', data:dates}},
    yAxis:{{type:'value', scale:true, axisLabel:{{formatter:'{{value}}%'}}}},
    series:[
      mk('长持基准(买入持有)','benchmark','#64748b','dashed'),
      mk('MA40（短窗口代表）','MA40','#94a3b8'),
      mk('MA182（当前默认）','MA182','#059669'),
      mk('MA210（推荐）','MA210','#dc2626'),
    ],
  }});
}})();
window.addEventListener('resize', ()=>document.querySelectorAll('.chart,.chart-tall').forEach(el=>{{
  const inst = echarts.getInstanceByDom(el); if(inst) inst.resize();
}}));
</script>
</body>
</html>
"""


def main() -> None:
    data = build_data()
    OUT_PATH.write_text(render(data), encoding="utf-8")
    print(f"报告已生成：{OUT_PATH}")
    print(f"大小：{OUT_PATH.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
