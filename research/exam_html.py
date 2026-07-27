"""二代考卷 HTML 渲染层(身份无关:入参全部是匿名化后的展示数据)。

单文件自包含(无外链、无网络请求),`open exam_<id>.html` 即用。
图表 = 手写 SVG(蜡烛+量柱+MA / 比值线);答题卡 = 强制论点 + 最高追价上限
(相对 T0 收盘 %),校验通过后生成 answer_<id>.json 下载(Blob,本地不外发)。
"""

from __future__ import annotations

import html as _html
import json
from typing import Dict, List, Optional


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _fmt(v, suffix="", na="n/a") -> str:
    if v is None:
        return na
    return f"{v}{suffix}"


def _snapshot_rows(e: dict) -> str:
    s = e["snapshot"]
    yl = {"上": "年线上方", "下": "年线下方"}.get(s["yearline"], "年线未成形")
    dm = f"(距年线 {s['dist_ma250']:+.1f}%)" if s["dist_ma250"] is not None else ""
    rp = s["sect_rank_pct"]
    rank = f"前 {100 - rp:.0f}%(百分位 {rp:.0f})" if rp is not None else "n/a"
    dh = f"{s['dist_hi20']:+.1f}%" if s["dist_hi20"] is not None else "n/a"
    cells = [
        ("60日累计", f"{s['cum60']:+.2f}%"),
        ("位置", f"{yl}{dm}"),
        ("距20日高点", dh),
        ("连板数", str(s["consec_lu"])),
        ("量比(5日)", _fmt(s["vol_ratio_5"])),
        ("换手率", _fmt(s["turnover"], "%")),
        ("题材持续", f"{s['persist']} 天"),
        ("行业强度(源库)", rank),
    ]
    return "".join(f"<div class='kv'><span>{_esc(k)}</span><b>{_esc(v)}</b></div>"
                   for k, v in cells)


def _cards_badges(cards: list) -> str:
    if not cards:
        return "<span class='badge none'>无红黄牌</span>"
    out = []
    for lvl, label in cards:
        cls = "red" if lvl == "红" else "yellow"
        out.append(f"<span class='badge {cls}'>{_esc(label)}</span>")
    return "".join(out)


def _near_table(near: list) -> str:
    rows = "".join(
        f"<tr><td>{_esc(r['t'])}</td><td>{_fmt(r['o'])}</td><td>{_fmt(r['h'])}</td>"
        f"<td>{_fmt(r['l'])}</td><td>{_fmt(r['c'])}</td><td>{_fmt(r['vr20'])}</td>"
        f"<td>{r['chg']:+.2f}</td><td>{'✔' if r['lu'] else ''}</td></tr>"
        for r in near)
    return ("<details><summary>近 10 日明细(指数化 OHLC / 量比20 / 涨幅% / 涨停)</summary>"
            "<table class='near'><tr><th>日</th><th>开</th><th>高</th><th>低</th><th>收</th>"
            f"<th>量比20</th><th>涨幅%</th><th>涨停</th></tr>{rows}</table></details>")


def _stock_section(e: dict) -> str:
    let = e["letter"]
    synth_note = "(成员中位合成)" if e["div_synth"] else "(申万二级指数)"
    div_block = (f"<div class='chartbox'><h4>行业分歧线:个股 / "
                 f"{_esc(e['sw_name'])}{synth_note}</h4>"
                 f"<div id='div-{let}' class='linechart'></div></div>"
                 if e["div"] else
                 f"<div class='chartbox'><h4>行业分歧线</h4>"
                 f"<div class='nochart'>行业指数不可得</div></div>")
    lhb = "".join(f"<li>{_esc(x)}</li>" for x in e["lhb_lines"])
    return f"""
<section class="pane" id="pane-{let}">
  <div class="stockhead">
    <span class="letter">{let}</span>
    <span class="macro">{_esc(e['macro'])}</span>
    <span class="swname">行业:{_esc(e['sw_name'])}</span>
    <span class="c0">T0 收盘(指数化):<b>{e['c0_idx']}</b></span>
  </div>
  <div class="badges">{_cards_badges(e['cards'])}</div>
  <div class="kvrow">{_snapshot_rows(e)}</div>
  <div class="chartbox big"><h4>60 日 K 线(指数化 T-60 收盘=100;·=涨停;
    橙=MA20 灰=MA250)</h4><div id="candle-{let}" class="candlechart"></div></div>
  <div class="tworow">
    <div class="chartbox"><h4>相对大盘线(个股/上证,T-59=100)</h4>
      <div id="rs-{let}" class="linechart"></div></div>
    {div_block}
  </div>
  {_near_table(e['near_tbl'])}
  <div class="msgbox"><h4>消息面 · 个股(脱敏)</h4>
    <p class="msg">{_esc(e['stock_msg'])}</p></div>
  <div class="msgbox"><h4>消息面 · 行业(脱敏)</h4>
    <p class="msg">{_esc(e['industry_msg'])}</p></div>
  <div class="msgbox"><h4>龙虎榜(近 5 日,席位类型脱敏)</h4>
    <ul class="lhb">{lhb}</ul>
    <p class="fine">席位归类为启发式:「机构」「沪深股通」为准确标注;「量化通道」仅
    覆盖知名通道;其余营业部统称「游资/普通营业部」。不提供具体席位名。</p></div>
</section>"""


def render_html(exam_id: str, entries: List[dict], mkt: dict, tier: str) -> str:
    letters = [e["letter"] for e in entries]
    data = {
        "examId": exam_id,
        "letters": letters,
        "c0": {e["letter"]: e["c0_idx"] for e in entries},
        "charts": {e["letter"]: {"kline": e["kline"], "rs": e["rs"], "div": e["div"]}
                   for e in entries},
        "sse": mkt["sse_idx_win"],
    }
    data_js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    tabs = "".join(
        f"<button class='tab' data-pane='pane-{let}'>{let}</button>" for let in letters)
    sections = "".join(_stock_section(e) for e in entries)
    above = "上方(偏多)" if mkt["sse_above_ma"] else "下方(偏空)"

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>盲选训练考卷 {exam_id}</title>
<style>
:root {{ --up:#d5303e; --dn:#1a9c6b; --ink:#1c2733; --mut:#6b7a8c; --bg:#f4f6f8;
        --card:#fff; --line:#dde4ea; --acc:#245a8f; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:15px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",
       sans-serif; color:var(--ink); background:var(--bg); }}
header {{ background:#16324c; color:#fff; padding:14px 22px; position:sticky; top:0;
         z-index:9; }}
header h1 {{ font-size:18px; margin:0 0 2px; }}
header p {{ margin:0; font-size:12.5px; opacity:.85; }}
nav {{ position:sticky; top:58px; z-index:9; background:var(--card);
      border-bottom:1px solid var(--line); padding:6px 16px; display:flex;
      flex-wrap:wrap; gap:6px; }}
.tab {{ border:1px solid var(--line); background:#fff; border-radius:8px;
       padding:5px 13px; font-size:14px; cursor:pointer; }}
.tab.on {{ background:var(--acc); color:#fff; border-color:var(--acc); }}
main {{ max-width:1040px; margin:0 auto; padding:16px; }}
.pane {{ display:none; }}
.pane.on {{ display:block; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:16px 18px; margin-bottom:14px; }}
.stockhead {{ display:flex; align-items:center; gap:14px; flex-wrap:wrap; }}
.letter {{ font-size:26px; font-weight:700; color:var(--acc); background:#e8f0f8;
          border-radius:10px; padding:2px 14px; }}
.macro {{ font-weight:600; }}
.swname {{ color:var(--mut); }}
.c0 {{ margin-left:auto; color:var(--mut); font-size:13px; }}
.badges {{ margin:10px 0; display:flex; gap:6px; flex-wrap:wrap; }}
.badge {{ font-size:12.5px; border-radius:7px; padding:2px 9px; }}
.badge.red {{ background:#fdebec; color:#b3212f; border:1px solid #f3c2c6; }}
.badge.yellow {{ background:#fdf6e3; color:#8a6d1a; border:1px solid #ecd9a0; }}
.badge.none {{ background:#eef3ee; color:#3c6e47; }}
.kvrow {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px;
         margin:10px 0 14px; }}
.kv {{ background:#f7f9fb; border:1px solid var(--line); border-radius:8px;
      padding:6px 10px; font-size:13px; }}
.kv span {{ color:var(--mut); display:block; font-size:12px; }}
.chartbox {{ background:var(--card); border:1px solid var(--line);
            border-radius:12px; padding:10px 14px 6px; margin-bottom:12px; }}
.chartbox h4 {{ margin:2px 0 6px; font-size:13.5px; color:var(--mut);
               font-weight:600; }}
.tworow {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
.candlechart svg, .linechart svg {{ width:100%; height:auto; display:block; }}
.nochart {{ color:var(--mut); padding:26px 0; text-align:center; font-size:13px; }}
.grid {{ stroke:#e8edf2; stroke-width:1; }}
.axlbl {{ font-size:10px; fill:#8896a5; }}
.wick.up, .body.up {{ stroke:var(--up); }}
.body.up {{ fill:#fff; stroke-width:1.2; }}
.wick.dn, .body.dn {{ stroke:var(--dn); }}
.body.dn {{ fill:var(--dn); }}
.vol.up {{ fill:#efb8bd; }} .vol.dn {{ fill:#a8d8c3; }}
.ma20 {{ fill:none; stroke:#e8930c; stroke-width:1.4; }}
.ma250 {{ fill:none; stroke:#9aa5b1; stroke-width:1.4; stroke-dasharray:5 3; }}
.ludot {{ fill:#c81e2e; }}
.rsline {{ fill:none; stroke:var(--acc); stroke-width:1.8; }}
.base100 {{ stroke:#c3ccd6; stroke-dasharray:4 4; }}
details {{ margin:0 0 12px; }}
summary {{ cursor:pointer; color:var(--acc); font-size:13.5px; }}
table.near {{ border-collapse:collapse; font-size:12.5px; margin-top:8px;
             background:#fff; }}
table.near th, table.near td {{ border:1px solid var(--line); padding:3px 9px;
                                text-align:right; }}
.msgbox {{ background:var(--card); border:1px solid var(--line);
          border-radius:12px; padding:10px 16px; margin-bottom:12px; }}
.msgbox h4 {{ margin:2px 0 6px; font-size:13.5px; color:var(--mut); }}
.msg {{ margin:4px 0; white-space:pre-wrap; }}
.lhb {{ margin:4px 0; padding-left:20px; }}
.fine {{ color:var(--mut); font-size:12px; margin:6px 0 2px; }}
.rules li {{ margin-bottom:5px; }}
.mktstats {{ display:flex; gap:14px; flex-wrap:wrap; margin:10px 0; }}
.mstat {{ background:#f7f9fb; border:1px solid var(--line); border-radius:8px;
         padding:8px 14px; }}
.mstat span {{ display:block; color:var(--mut); font-size:12px; }}
.answer-slot {{ background:var(--card); border:1px solid var(--line);
               border-radius:12px; padding:12px 16px; margin-bottom:12px; }}
.answer-slot h4 {{ margin:0 0 8px; }}
.arow {{ display:flex; gap:12px; flex-wrap:wrap; align-items:center; }}
.arow select, .arow input {{ font-size:15px; padding:6px 8px;
                             border:1px solid var(--line); border-radius:8px; }}
.arow input[type=number] {{ width:110px; }}
.calc {{ color:var(--mut); font-size:13px; }}
textarea {{ width:100%; margin-top:8px; min-height:64px; font:14px/1.5 inherit;
           border:1px solid var(--line); border-radius:8px; padding:8px; }}
.btn {{ background:var(--acc); color:#fff; border:none; border-radius:9px;
       padding:10px 22px; font-size:15px; cursor:pointer; }}
.btn:disabled {{ background:#9db4c9; }}
#verdict {{ margin:10px 0; font-size:14px; white-space:pre-wrap; }}
code.cmd {{ display:block; background:#16324c; color:#d9e6f2; border-radius:8px;
           padding:10px 12px; margin-top:8px; font-size:13px; overflow-x:auto; }}
</style>
</head>
<body>
<header>
  <h1>盲选训练考卷 · {exam_id}</h1>
  <p>你在 T+1 盘前;全部信息 ≤ T0;12 选 4(3 主选 + 1 备用);竞价定胜负。</p>
</header>
<nav id="nav">
  <button class="tab on" data-pane="pane-rules">考规</button>
  <button class="tab" data-pane="pane-mkt">市场语境</button>
  {tabs}
  <button class="tab" data-pane="pane-answer">答题卡</button>
</nav>
<main>

<section class="pane on" id="pane-rules">
  <div class="card">
    <h3>考试规则(预注册纪律,提交即受约束)</h3>
    <ul class="rules">
      <li><b>时点</b>:时间已拉回历史某真实交易日(T0)收盘后、次日盘前。候选取自
        当日真实候选逻辑,已匿名化(代号 A–L、价格指数化 T-60 收盘=100、日期 T 化;
        行业为真名)。取样档:{_esc(tier)};取样不挑好答案,红牌票照样入池。</li>
      <li><b>答题</b>:选 3 只主选 + 1 只备用,每只<b>必填</b>买入论点与
        <b>最高追价上限</b>(相对 T0 收盘的百分比)。</li>
      <li><b>成交(竞价定胜负)</b>:T+1 开盘价 ≤ 上限 → 按开盘价成交(无滑点);
        高开超上限 → 该票作废,不做盘中追补;低开照买(你拍板的规则:更便宜是
        真实账单);一字板/停牌照旧买不进。</li>
      <li><b>备用票</b>:主选有任一未成交 → 备用按其自身上限顶替一个名额;主选全部
        成交 → 备用作废;四只全未成交 → 空手期,照常入账。</li>
      <li><b>判分纪律(现役新规)</b>:-5% 止损、回落止盈 8%(自峰值)、非浮盈第 5
        日退出、浮盈豁免时间退出至多 15 日、先止损后止盈、跌停卖不出顺延。判的是
        <b>纪律收益</b>,不是你能不能扛。</li>
      <li><b>K4 红黄牌</b>:系统避坑提示,展示不预筛。黄牌不是否决票——用黄牌票
        需写出比统计先验更强的理由;红牌票入场即入池,用不用牌是考题的一部分。</li>
      <li><b>消息面</b>:个股/行业消息已脱敏(隐公司名、金额转相对规模);大盘消息
        照实给(自觉不查证——训练骗的是自己的数据)。「无」= 检索过无消息;
        「未能取得」= 数据源缺失,两者含义不同。</li>
    </ul>
  </div>
</section>

<section class="pane" id="pane-mkt">
  <div class="card">
    <h3>市场语境(匿名)</h3>
    <div class="mktstats">
      <div class="mstat"><span>大盘 60 日累计</span><b>{mkt['sse_cum60']:+.2f}%</b></div>
      <div class="mstat"><span>T0 收盘 vs MA20</span><b>{above}</b></div>
      <div class="mstat"><span>T0 涨停家数</span><b>{mkt['n_limit_up']}</b></div>
      <div class="mstat"><span>T0 跌停家数</span><b>{mkt['n_limit_down']}</b></div>
    </div>
    <div class="chartbox"><h4>上证综指 60 日(指数化 T-60=100)</h4>
      <div id="sse-chart" class="linechart"></div></div>
    <div class="msgbox"><h4>消息面 · 大盘(照实,不显示日期)</h4>
      <p class="msg">{_esc(mkt['market_msg'])}</p></div>
  </div>
</section>

{sections}

<section class="pane" id="pane-answer">
  <div class="card">
    <h3>答题卡</h3>
    <p class="fine">四只代号互不相同;上限范围 -15% ~ +30%;论点每只至少 6 字。
      提交前想一遍:域规则(年线)、黄牌权衡写了没有、读卡有没有想当然。</p>
    <div id="slots"></div>
    <div id="verdict"></div>
    <button class="btn" id="gen">校验并生成答卷文件</button>
    <div id="after" style="display:none">
      <p>答卷已下载(answer_{exam_id}.json)。判分命令(把路径换成你的下载位置):</p>
      <code class="cmd">python research/exam.py score {exam_id} --answer ~/Downloads/answer_{exam_id}.json</code>
    </div>
  </div>
</section>

</main>
<script>
const DATA = {data_js};

/* ---------- tab 切换 ---------- */
document.getElementById('nav').addEventListener('click', ev => {{
  const b = ev.target.closest('.tab'); if (!b) return;
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
  document.querySelectorAll('.pane').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  document.getElementById(b.dataset.pane).classList.add('on');
  window.scrollTo(0, 0);
}});

/* ---------- 蜡烛图 ---------- */
function candleChart(el, k) {{
  const W = 960, H = 300, VH = 84, P = {{l: 46, r: 12, t: 8, b: 20}};
  const n = k.candles.length;
  const xs = i => P.l + (W - P.l - P.r) * (i + 0.5) / n;
  const cw = Math.max(3, (W - P.l - P.r) / n * 0.62);
  let lo = Infinity, hi = -Infinity;
  k.candles.forEach(c => {{ lo = Math.min(lo, c.l); hi = Math.max(hi, c.h); }});
  [k.ma20, k.ma250].forEach(a => a.forEach(v => {{
    if (v != null) {{ lo = Math.min(lo, v); hi = Math.max(hi, v); }} }}));
  const pad = (hi - lo) * 0.05 || 1; lo -= pad; hi += pad;
  const ys = v => P.t + (H - P.t - P.b) * (1 - (v - lo) / (hi - lo));
  let vmax = 0; k.candles.forEach(c => vmax = Math.max(vmax, c.v));
  const vy = v => H + VH - 6 - (VH - 14) * (v / (vmax || 1));
  let s = `<svg viewBox="0 0 ${{W}} ${{H + VH}}" xmlns="http://www.w3.org/2000/svg">`;
  for (let g = 0; g <= 4; g++) {{
    const v = lo + (hi - lo) * g / 4, y = ys(v);
    s += `<line x1="${{P.l}}" y1="${{y}}" x2="${{W - P.r}}" y2="${{y}}" class="grid"/>` +
         `<text x="${{P.l - 4}}" y="${{y + 4}}" class="axlbl" text-anchor="end">${{v.toFixed(0)}}</text>`;
  }}
  k.candles.forEach((c, i) => {{
    if (i % 10 === 0 || i === n - 1)
      s += `<text x="${{xs(i)}}" y="${{H - 5}}" class="axlbl" text-anchor="middle">${{c.t}}</text>`;
  }});
  const path = arr => {{
    let p = "", st = false;
    arr.forEach((v, i) => {{ if (v == null) return;
      p += (st ? "L" : "M") + xs(i).toFixed(1) + " " + ys(v).toFixed(1) + " "; st = true; }});
    return p;
  }};
  if (k.ma250.some(v => v != null)) s += `<path d="${{path(k.ma250)}}" class="ma250"/>`;
  if (k.ma20.some(v => v != null)) s += `<path d="${{path(k.ma20)}}" class="ma20"/>`;
  k.candles.forEach((c, i) => {{
    const up = c.c >= c.o, cls = up ? "up" : "dn", x = xs(i);
    s += `<line x1="${{x}}" y1="${{ys(c.h)}}" x2="${{x}}" y2="${{ys(c.l)}}" class="wick ${{cls}}"/>`;
    const y1 = ys(Math.max(c.o, c.c)), y2 = ys(Math.min(c.o, c.c));
    s += `<rect x="${{x - cw / 2}}" y="${{y1}}" width="${{cw}}" height="${{Math.max(1, y2 - y1)}}" class="body ${{cls}}"/>`;
    if (c.lu) s += `<circle cx="${{x}}" cy="${{ys(c.h) - 5}}" r="2.4" class="ludot"/>`;
    s += `<rect x="${{x - cw / 2}}" y="${{vy(c.v)}}" width="${{cw}}" height="${{H + VH - 6 - vy(c.v)}}" class="vol ${{cls}}"/>`;
  }});
  s += `</svg>`;
  el.innerHTML = s;
}}

/* ---------- 比值线 ---------- */
function lineChart(el, arr) {{
  if (!arr) {{ el.innerHTML = '<div class="nochart">不可得</div>'; return; }}
  const W = 470, H = 170, P = {{l: 44, r: 8, t: 8, b: 20}};
  const v = arr.filter(x => x != null);
  if (!v.length) {{ el.innerHTML = '<div class="nochart">不可得</div>'; return; }}
  let lo = Math.min(...v), hi = Math.max(...v);
  const pad = (hi - lo) * 0.06 || 1; lo -= pad; hi += pad;
  const n = arr.length;
  const xs = i => P.l + (W - P.l - P.r) * i / (n - 1);
  const ys = x => P.t + (H - P.t - P.b) * (1 - (x - lo) / (hi - lo));
  let s = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg">`;
  for (let g = 0; g <= 3; g++) {{
    const val = lo + (hi - lo) * g / 3, y = ys(val);
    s += `<line x1="${{P.l}}" x2="${{W - P.r}}" y1="${{y}}" y2="${{y}}" class="grid"/>` +
         `<text x="${{P.l - 4}}" y="${{y + 4}}" class="axlbl" text-anchor="end">${{val.toFixed(0)}}</text>`;
  }}
  if (lo <= 100 && 100 <= hi)
    s += `<line x1="${{P.l}}" x2="${{W - P.r}}" y1="${{ys(100)}}" y2="${{ys(100)}}" class="base100"/>`;
  let p = "";
  arr.forEach((x, i) => {{ if (x == null) return;
    p += (p ? "L" : "M") + xs(i).toFixed(1) + " " + ys(x).toFixed(1) + " "; }});
  s += `<path d="${{p}}" class="rsline"/>`;
  s += `<text x="${{xs(0)}}" y="${{H - 6}}" class="axlbl">左端=T-59</text>` +
       `<text x="${{xs(n - 1)}}" y="${{H - 6}}" class="axlbl" text-anchor="end">T0</text></svg>`;
  el.innerHTML = s;
}}

DATA.letters.forEach(let_ => {{
  const ch = DATA.charts[let_];
  candleChart(document.getElementById('candle-' + let_), ch.kline);
  lineChart(document.getElementById('rs-' + let_), ch.rs);
  const dv = document.getElementById('div-' + let_);
  if (dv) lineChart(dv, ch.div);
}});
lineChart(document.getElementById('sse-chart'), DATA.sse);

/* ---------- 答题卡 ---------- */
const ROLES = [["main1", "主选 1"], ["main2", "主选 2"], ["main3", "主选 3"],
               ["backup", "备用票"]];
const slots = document.getElementById('slots');
ROLES.forEach(([role, label]) => {{
  const div = document.createElement('div');
  div.className = 'answer-slot';
  div.innerHTML = `<h4>${{label}}</h4>
    <div class="arow">
      <label>代号 <select data-role="${{role}}" class="sel">
        <option value="">—</option>
        ${{DATA.letters.map(l => `<option>${{l}}</option>`).join('')}}
      </select></label>
      <label>最高追价上限(% vs T0 收盘)
        <input type="number" step="0.1" min="-15" max="30" data-role="${{role}}"
               class="ceil" placeholder="如 3.0"></label>
      <span class="calc" data-role="${{role}}"></span>
    </div>
    <textarea data-role="${{role}}" class="thesis"
      placeholder="买入论点(必填,≥6 字):你为什么买它?黄牌票请写出比统计先验更强的理由。"></textarea>`;
  slots.appendChild(div);
}});

function recalc(role) {{
  const sel = document.querySelector(`select[data-role="${{role}}"]`).value;
  const pct = parseFloat(document.querySelector(`input[data-role="${{role}}"]`).value);
  const out = document.querySelector(`span.calc[data-role="${{role}}"]`);
  if (!sel) {{ out.textContent = ''; return; }}
  const c0 = DATA.c0[sel];
  out.textContent = isNaN(pct)
    ? `该票 T0 收盘(指数化)= ${{c0}}`
    : `T0 收盘 ${{c0}} → 上限点位 ${{(c0 * (1 + pct / 100)).toFixed(2)}}(指数化)`;
}}
slots.addEventListener('input', ev => {{
  const r = ev.target.dataset.role; if (r) recalc(r);
}});

document.getElementById('gen').addEventListener('click', () => {{
  const verdict = document.getElementById('verdict');
  const answers = [], errs = [], used = new Set();
  ROLES.forEach(([role, label]) => {{
    const letter = document.querySelector(`select[data-role="${{role}}"]`).value;
    const pct = parseFloat(document.querySelector(`input[data-role="${{role}}"]`).value);
    const thesis = document.querySelector(`textarea[data-role="${{role}}"]`).value.trim();
    if (!letter) errs.push(`${{label}}:未选代号`);
    else if (used.has(letter)) errs.push(`${{label}}:代号 ${{letter}} 重复`);
    used.add(letter);
    if (isNaN(pct) || pct < -15 || pct > 30) errs.push(`${{label}}:上限须在 -15 ~ +30 之间`);
    if (thesis.length < 6) errs.push(`${{label}}:论点不足 6 字(强制论点是预注册纪律)`);
    answers.push({{role, letter, ceiling_pct: pct, thesis}});
  }});
  if (errs.length) {{
    verdict.textContent = '未通过校验:\\n· ' + errs.join('\\n· ');
    verdict.style.color = '#b3212f';
    return;
  }}
  verdict.textContent = '校验通过。答卷文件已生成下载。';
  verdict.style.color = '#2f7d4f';
  const blob = new Blob([JSON.stringify({{exam_id: DATA.examId, answers}}, null, 2)],
                        {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `answer_${{DATA.examId}}.json`;
  a.click();
  document.getElementById('after').style.display = 'block';
}});
</script>
</body>
</html>"""
