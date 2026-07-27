"""盲选训练·二代考卷管线(考官会话,2026-07-27 起)——纯研究件,生产零改动。

权威规格:`archive/交接_考官Agent_盲选训练规格_20260726.md`(§〇~§九)。
一代呈现层(`drill.py` 的 markdown 表格)废止;取样逻辑与判分引擎**原样复用**
drill.py / h9(`_sim_one` 零改动,判分口径唯一源铁律)。

二代新增(相对 drill.py):
  · 12 选 4(3 主选 + 1 备用),竞价定胜负买入成交层(§九,用户拍板);
  · HTML 考卷:真 K 线(蜡烛+量柱+MA20/MA250)、RS 线、行业分歧线(申万真名)、
    消息面(子代理脱敏)、龙虎榜席位类型摘要、答题卡(强制论点+最高追价上限);
  · 数据源:申万三件套 / top_list+top_inst / kpl_list / major_news / news_cctv
    (2026-07-26 侦察全通,见 STRATEGY_LAB 变更日志)。

文件族(全部落 research/_cache/drills/,gitignore):
  exam_<id>_key.json       密封答案(代号↔代码、决策日、seed)——判分前任何人不读
  exam_<id>_raw.json       身份材料(脱敏子代理专用输入)
  exam_<id>_msg.json       脱敏消息面(子代理产出)
  exam_<id>_display.json   身份无关展示数据(new 时落定,render 不重建、零漂移)
  exam_<id>_scanterms.json 泄漏硬门扫描词(代码/股名/日期串;密封)
  exam_<id>.html           考卷本体(泄漏硬门通过才落盘)
  answer_<id>.json         用户答卷(HTML 答题卡生成下载)

子命令:
  new / render / score / selfcheck(成交层合成用例,不触真实前视数据)。

防泄漏纪律(§五):stdout 不打印决策日/真代码/股名/seed;身份材料只进密封文件;
HTML 落盘前过泄漏硬门(真代码/6位符号/股名/任何日期串→硬失败,写 _LEAKS.txt)。
行业真名**允许**出现(§三.3 用户拍板),与一代 _leak_scan 的行业禁令不同。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drill as d1                      # 取样/信息卡/判分基建(复用,勿改勿抄)  # noqa: E402
import h9_exit_reform as h9             # _sim_one 判分口径唯一源  # noqa: E402
import k4_assembly                      # build_features / add_rule_masks  # noqa: E402
from neckline.backtest.portfolio import ClosedTrade  # noqa: E402
from exam_html import render_html       # 身份无关渲染层  # noqa: E402

DRILLS_DIR = d1.DRILLS_DIR
LEDGER = d1.LEDGER

N_EXAM = 12
MAIN_K = 3
ROLES = ["main1", "main2", "main3", "backup"]
ROLE_LABEL = {"main1": "主1", "main2": "主2", "main3": "主3", "backup": "备用"}
MODE = "v2_auction"                     # ledger 口径标记(§九:无滑点竞价成交)
_EPS = 1e-9                             # 阈值比较容差(项目坑:裸浮点比较禁用)
DISPLAY = d1.DISPLAY_SESSIONS           # 60
EXT_PAD = 40                            # 基准序列多取 40 交易日,容忍个股停牌窗前移
NEWS_DAYS = 3                           # 新闻取 T-2..T0
LHB_DAYS = 5                            # 龙虎榜取 T-4..T0
KPL_LOOKBACK = 10                       # 涨停原因回看 T-9..T0

MARKET_KEYWORDS = [
    "央行", "国务院", "证监会", "美联储", "降准", "降息", "LPR", "关税", "财政部",
    "国常会", "A股", "沪指", "上证指数", "创业板指", "北交所", "IPO", "印花税",
    "汇率", "人民币", "政治局", "发改委", "两融", "社融", "CPI", "PMI", "GDP",
]

_PLACEHOLDER = "(消息面脱敏处理中——终版渲染前补齐)"


def _path(exam_id: str, kind: str) -> Path:
    suffix = {"key": "_key.json", "raw": "_raw.json", "msg": "_msg.json",
              "display": "_display.json", "scan": "_scanterms.json",
              "html": ".html", "leaks": "_LEAKS.txt"}[kind]
    return DRILLS_DIR / f"exam_{exam_id}{suffix}"


# ======================================================================
#  TuShare 拉取(现拉现用,不落生产 Parquet;探针已验证各接口可用性)
# ======================================================================

_PRO = None


def _pro():
    global _PRO
    if _PRO is None:
        import tushare as ts
        from neckline.config import settings
        _PRO = ts.pro_api(settings.tushare_token)   # 直传,禁 set_token(项目坑)
    return _PRO


def _tcall(api: str, **kw):
    last = None
    for i in range(3):
        try:
            return getattr(_pro(), api)(**kw)
        except Exception as e:                       # 限频/网络抖动重试
            last = e
            time.sleep(1.5 * (i + 1))
    print(f"[fetch] {api} 三试未过(该信息位将标'未能取得'):{type(last).__name__}",
          file=sys.stderr)
    return None


def _sw_of(code: str, d_compact: str) -> Optional[dict]:
    """个股 → 截至 D 的申万二级行业(in_date≤D<out_date)。"""
    df = _tcall("index_member_all", ts_code=code)
    if df is None or df.empty:
        return None
    for _, r in df.iterrows():
        ind = str(r.get("in_date") or "").strip()
        outd = r.get("out_date")
        out_ok = outd is None or (isinstance(outd, float) and math.isnan(outd)) \
            or str(outd).strip() in ("", "None") or str(outd).strip() > d_compact
        if ind and ind <= d_compact and out_ok:
            return {"l2_code": str(r["l2_code"]), "l2_name": str(r["l2_name"])}
    return None


def _sw_series(l2_code: str, start_c: str, end_c: str) -> Dict[str, float]:
    df = _tcall("sw_daily", ts_code=l2_code, start_date=start_c, end_date=end_c)
    if df is None or df.empty:
        return {}
    return {str(r["trade_date"]): float(r["close"]) for _, r in df.iterrows()}


def _synth_industry_series(p: pl.DataFrame, industry: str,
                           win_dates: List[date]) -> Dict[str, float]:
    """申万不可得的兜底:行业成员中位数合成(k4p_h6_theme 姿势,规格 §七)。"""
    sub = p.filter((pl.col("industry") == industry)
                   & pl.col("trade_date").is_in(win_dates))
    counts = sub.group_by("ts_code").agg(pl.len().alias("m"))
    full = counts.filter(pl.col("m") == len(win_dates))["ts_code"].to_list()
    if len(full) < 5:
        return {}
    sub = sub.filter(pl.col("ts_code").is_in(full))
    base = (sub.sort("trade_date").group_by("ts_code")
            .agg(pl.col("close").first().alias("b")))
    sub = sub.join(base, on="ts_code").with_columns(
        (pl.col("close") / pl.col("b") * 100.0).alias("ix"))
    med = sub.group_by("trade_date").agg(pl.col("ix").median().alias("v")).sort("trade_date")
    return {dt.strftime("%Y%m%d"): float(v) for dt, v in zip(med["trade_date"], med["v"])}


def _classify_seat(exalter: str) -> str:
    """席位类型脱敏归类(启发式,页面脚注注明)。不输出具体席位名(§三.7)。"""
    s = exalter or ""
    if "机构专用" in s:
        return "机构"
    if "沪股通" in s or "深股通" in s:
        return "沪深股通(北向)"
    if "华鑫证券" in s and "分公司" in s:
        return "量化通道(疑似)"
    if "拉萨" in s:
        return "散户通道营业部"
    if "营业部" in s or "分公司" in s:
        return "游资/普通营业部"
    return "其他席位"


# ======================================================================
#  new:取样(复用 drill)+ 拉数据 + 密封 + 初版渲染
# ======================================================================

def _t_label(gidx: Dict[date, int], d0: date, dt: date) -> str:
    k = gidx[d0] - gidx[dt]
    return "T0" if k == 0 else f"T-{k}"


def _extend_card(masked: pl.DataFrame, code: str, d: date, card: dict,
                 gidx: Dict[date, int]) -> Tuple[dict, List[date]]:
    """在 drill._card_for 之上补 HTML 所需序列:指数化 OHLC 蜡烛/量柱/均线。
    返回 (card, 该股 60 日窗真实日期表——仅密封件与对齐用,绝不进 HTML)。"""
    g = (masked.filter((pl.col("ts_code") == code) & (pl.col("trade_date") <= d))
         .sort("trade_date"))
    rows = g.tail(DISPLAY + 1).to_dicts()
    base = rows[0]["close"]                    # T-60 收盘 = 100
    win = rows[1:]
    ix = lambda v: round(v * 100.0 / base, 2) if v is not None else None  # noqa: E731
    candles, ma20, ma250, dates = [], [], [], []
    for r in win:
        vol = r.get("vol")
        candles.append({
            "t": _t_label(gidx, d, r["trade_date"]),
            "o": ix(r["open"]), "h": ix(r["high"]), "l": ix(r["low"]), "c": ix(r["close"]),
            "v": float(f"{vol:.3g}") if vol else 0.0,      # 3 位有效数字,钝化指纹
            "lu": bool(r["is_limit_up"]),
        })
        ma20.append(ix(r.get("ma20")))
        ma250.append(ix(r.get("ma250")))
        dates.append(r["trade_date"])
    card["kline"] = {"candles": candles, "ma20": ma20, "ma250": ma250}
    card["c0_idx"] = candles[-1]["c"]
    return card, dates


def _ratio_line(stock_win: List[Tuple[date, float]],
                bench: Dict[date, float]) -> Optional[List[float]]:
    """个股/基准比值线,窗首=100。基准缺个别日→沿用前值;首日就缺→放弃。"""
    vals, prev = [], None
    for dt, sc in stock_win:
        b = bench.get(dt, prev)
        if b is None:
            return None
        prev = b
        vals.append(sc / b)
    r0 = vals[0]
    return [round(v / r0 * 100.0, 2) for v in vals]


def _lhb_lines(recs: list) -> List[str]:
    out = []
    for r in recs:
        seats = "、".join(r["buy_seats"]) or "—"
        sells = "、".join(r["sell_seats"]) or "—"
        net = "净买入为正" if r["net_buy_positive"] else "净买入为负"
        out.append(f"{r['t']} 上榜({';'.join(r['reasons'])});买方席位:{seats};"
                   f"卖方席位:{sells};{net}")
    if not out:
        out = ["近 5 日未上龙虎榜"]
    return out


def cmd_new(args: argparse.Namespace) -> None:
    DRILLS_DIR.mkdir(parents=True, exist_ok=True)
    exam_id = args.id
    lo_s, hi_s = args.date_range.split(":")
    lo, hi = date.fromisoformat(lo_s), date.fromisoformat(hi_s)
    import random
    seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 9)
    d1.N_DEFAULT = N_EXAM                       # 取样门槛抬到 12(drill 全局)

    p = d1.panel()
    print(f"[new] 面板 {p.height} 行;区间 {lo}~{hi};n={N_EXAM}。构建 K4 特征…",
          file=sys.stderr)
    feat = k4_assembly.build_features(p)
    masked = k4_assembly.add_rule_masks(feat)

    elig = d1._eligible_dates(p, lo, hi)
    if not elig:
        print("[new] 无合格日期。", file=sys.stderr)
        sys.exit(2)
    rng = random.Random(seed)
    rng.shuffle(elig)
    chosen = None
    for dd in elig:
        c, t = d1._qualified_at(masked, dd)
        if c.height >= N_EXAM:
            chosen = (dd, c, t)
            break
    if chosen is None:
        best = max(elig, key=lambda x: d1._qualified_at(masked, x)[0].height)
        c, t = d1._qualified_at(masked, best)
        chosen = (best, c, t)
    d, cand, tier = chosen

    codes_all = sorted(cand["ts_code"].to_list())
    ok = [c for c in codes_all
          if masked.filter((pl.col("ts_code") == c)
                           & (pl.col("trade_date") <= d)).height >= d1.MIN_PRIOR]
    if len(ok) < N_EXAM:
        print(f"[new] 足历史候选不足 {N_EXAM}(有 {len(ok)})。", file=sys.stderr)
        sys.exit(2)
    picked = rng.sample(ok, N_EXAM)

    # —— 全局交易日历(T 标签与市场级数据源共用;ext 窗容忍个股停牌窗前移)——
    gdays = p.select("trade_date").unique().sort("trade_date")["trade_date"].to_list()
    gidx = {x: i for i, x in enumerate(gdays)}
    d_compact = d.strftime("%Y%m%d")
    win_glb = gdays[gidx[d] - DISPLAY: gidx[d] + 1]          # 全局 T-60..T0
    win_ext = gdays[max(0, gidx[d] - DISPLAY - EXT_PAD): gidx[d] + 1]
    start_c = win_ext[0].strftime("%Y%m%d")

    # —— 信息卡(drill 复用 + K 线扩展)——
    sect_ranks = d1._sector_ranks(masked, d)
    names = d1.stock_names(picked)
    cards, stock_dates, close_of = {}, {}, {}
    for c in picked:
        card = d1._card_for(masked, c, d, sect_ranks)
        card, dts = _extend_card(masked, c, d, card, gidx)
        cards[c] = card
        stock_dates[c] = dts
        g = (masked.filter((pl.col("ts_code") == c) & pl.col("trade_date").is_in(dts))
             .select(["trade_date", "close"]).sort("trade_date"))
        close_of[c] = dict(g.iter_rows())

    # —— RS 线基准(上证,ext 窗)——
    sse = {dt: v for dt, v in
           p.filter(pl.col("trade_date").is_in(win_ext))
            .select(["trade_date", "sse_close"]).unique().sort("trade_date").iter_rows()}

    # —— 申万行业 + 分歧线 ——
    sw_map, sw_cache = {}, {}
    n_sw, n_synth = 0, 0
    for c in picked:
        sw = _sw_of(c, d_compact)
        time.sleep(0.1)
        ind_src = (names.get(c, ("?", "?"))[1] or "").strip()
        label, series, synth = None, {}, False
        if sw:
            if sw["l2_code"] not in sw_cache:
                sw_cache[sw["l2_code"]] = _sw_series(sw["l2_code"], start_c, d_compact)
                time.sleep(0.1)
            series = sw_cache[sw["l2_code"]]
            label = sw["l2_name"]
        if series:
            n_sw += 1
        else:                                    # 兜底:成员中位合成
            series = _synth_industry_series(p, ind_src, win_ext)
            label = ind_src or "行业不明"
            synth = True
            if series:
                n_synth += 1
        bench = {date.fromisoformat(f"{k[:4]}-{k[4:6]}-{k[6:]}"): v
                 for k, v in series.items()}
        stock_win = [(dt, close_of[c][dt]) for dt in stock_dates[c]]
        cards[c]["rs"] = _ratio_line(stock_win, sse)
        cards[c]["div"] = _ratio_line(stock_win, bench) if series else None
        cards[c]["div_label"] = label
        cards[c]["div_synth"] = synth
        sw_map[c] = {"label": label, "synth": synth, "industry_src": ind_src}

    # —— 龙虎榜(T-4..T0)——
    lhb_days = gdays[gidx[d] - (LHB_DAYS - 1): gidx[d] + 1]
    lhb: Dict[str, list] = {c: [] for c in picked}
    for dt in lhb_days:
        dc = dt.strftime("%Y%m%d")
        tl = _tcall("top_list", trade_date=dc)
        ti = _tcall("top_inst", trade_date=dc)
        time.sleep(0.15)
        if tl is None or tl.empty:
            continue
        for c in picked:
            sub = tl[tl["ts_code"] == c]
            if sub.empty:
                continue
            reasons = sorted(set(str(r) for r in sub["reason"].tolist()))
            net_pos = bool(sub["net_amount"].astype(float).sum() > 0)
            buys, sells = [], []
            if ti is not None and not ti.empty:
                si = ti[ti["ts_code"] == c]
                for _, r in si.iterrows():
                    typ = _classify_seat(str(r.get("exalter") or ""))
                    (buys if float(r.get("buy") or 0) >= float(r.get("sell") or 0)
                     else sells).append(typ)
            lhb[c].append({"t": _t_label(gidx, d, dt), "reasons": reasons,
                           "net_buy_positive": net_pos,
                           "buy_seats": buys[:5], "sell_seats": sells[:5]})
    n_lhb_hit = sum(1 for c in picked if lhb[c])

    # —— kpl 涨停原因(候选在 T-9..T0 涨停的日子)——
    kpl_win = gdays[gidx[d] - (KPL_LOOKBACK - 1): gidx[d] + 1]
    lu_rows = (masked.filter(pl.col("ts_code").is_in(picked)
                             & pl.col("trade_date").is_in(kpl_win)
                             & pl.col("is_limit_up"))
               .select(["ts_code", "trade_date"]))
    kpl_needed = sorted({dt for _c, dt in lu_rows.iter_rows()})
    kpl: Dict[str, list] = {c: [] for c in picked}
    for dt in kpl_needed:
        df = _tcall("kpl_list", trade_date=dt.strftime("%Y%m%d"), tag="涨停")
        time.sleep(0.15)
        if df is None or df.empty:
            continue
        for c in picked:
            sub = df[df["ts_code"] == c]
            for _, r in sub.iterrows():
                kpl[c].append({"t": _t_label(gidx, d, dt),
                               "theme": str(r.get("theme") or ""),
                               "desc": str(r.get("lu_desc") or ""),
                               "status": str(r.get("status") or "")})
    n_kpl_hit = sum(1 for c in picked if kpl[c])

    # —— 新闻(major_news T-2..T0 全天翻页 + cctv T0)——
    news_days = gdays[gidx[d] - (NEWS_DAYS - 1): gidx[d] + 1]
    all_titles: List[Tuple[str, str, str]] = []          # (t_label, title, src)
    for dt in news_days:
        ds = dt.isoformat()
        for page in range(6):
            df = _tcall("major_news", start_date=f"{ds} 00:00:00",
                        end_date=f"{ds} 23:59:59", offset=page * 800, limit=800)
            time.sleep(0.15)
            if df is None or df.empty:
                break
            for _, r in df.iterrows():
                all_titles.append((_t_label(gidx, d, dt),
                                   str(r["title"]), str(r.get("src") or "")))
            if len(df) < 800:
                break
    stock_news: Dict[str, list] = {c: [] for c in picked}
    ind_news: Dict[str, list] = {}
    market_titles: List[dict] = []
    for tlab, title, src in all_titles:
        for c in picked:
            nm = names.get(c, ("", ""))[0]
            if nm and nm in title and len(stock_news[c]) < 20:
                stock_news[c].append({"t": tlab, "title": title, "src": src})
        for c in picked:
            lab = sw_map[c]["label"]
            if lab and lab in title:
                ind_news.setdefault(lab, [])
                if (len(ind_news[lab]) < 12
                        and title not in [x["title"] for x in ind_news[lab]]):
                    ind_news[lab].append({"t": tlab, "title": title})
        if any(k in title for k in MARKET_KEYWORDS) and len(market_titles) < 60:
            market_titles.append({"t": tlab, "title": title})
    cctv_items = []
    try:
        import akshare as ak
        cdf = ak.news_cctv(date=d_compact)
        for _, r in cdf.iterrows():
            cctv_items.append({"t": "T0", "title": str(r["title"]),
                               "excerpt": str(r["content"])[:220]})
    except Exception as e:
        print(f"[fetch] news_cctv 失败(大盘消息少一路):{type(e).__name__}",
              file=sys.stderr)
    n_news_hit = sum(1 for c in picked if stock_news[c])

    # —— 字母匿名化(打乱,代号不编码抽样次序)——
    order = list(range(N_EXAM))
    rng.shuffle(order)
    letters = [chr(ord("A") + i) for i in range(N_EXAM)]
    letter_of = {picked[order[i]]: letters[i] for i in range(N_EXAM)}

    mkt = d1._market_ctx(p, d)

    # —— 密封件:key + raw ——
    key = {
        "id": exam_id, "mode": MODE,
        "created": datetime.now().isoformat(timespec="seconds"),
        "seed": seed, "date_range": [lo.isoformat(), hi.isoformat()], "n": N_EXAM,
        "decision_date": d.isoformat(), "tier": tier,
        "entries": [{"letter": letter_of[c], "ts_code": c,
                     "decision_date": d.isoformat(), "macro": cards[c]["macro"],
                     "sw_l2": sw_map[c]["label"],
                     "cards": [lbl for _lvl, lbl in cards[c]["cards"]]}
                    for c in picked],
    }
    _path(exam_id, "key").write_text(
        json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")

    raw = {"exam_id": exam_id, "per_code": {}, "industries": ind_news,
           "market": {"cctv": cctv_items, "titles": market_titles}}
    for c in picked:
        nm, _ind = names.get(c, ("?", "?"))
        raw["per_code"][c] = {
            "name": nm, "industry_src": sw_map[c]["industry_src"],
            "sw_l2": sw_map[c]["label"],
            "t0_limit_up": bool(cards[c]["kline"]["candles"][-1]["lu"]),
            "limit_up_history": kpl[c], "top_list": lhb[c], "news": stock_news[c],
        }
    _path(exam_id, "raw").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    # —— 展示数据(身份无关)+ 扫描词(密封)——
    entries = []
    for c in picked:
        card = cards[c]
        entries.append({
            "letter": letter_of[c], "macro": card["macro"],
            "sw_name": card["div_label"], "div_synth": card["div_synth"],
            "kline": card["kline"], "rs": card["rs"], "div": card["div"],
            "near_tbl": card["near_tbl"], "c0_idx": card["c0_idx"],
            "snapshot": {k: card[k] for k in
                         ("cum60", "dist_hi20", "yearline", "dist_ma250",
                          "vol_ratio_5", "turnover", "persist", "sect_rank_pct",
                          "consec_lu")},
            "cards": card["cards"],
            "stock_msg": _PLACEHOLDER, "industry_msg": _PLACEHOLDER,
            "lhb_lines": _lhb_lines(lhb[c]),
        })
    entries.sort(key=lambda e: e["letter"])
    display = {"entries": entries,
               "mkt": {**mkt, "market_msg": _PLACEHOLDER}, "tier": tier}
    _path(exam_id, "display").write_text(
        json.dumps(display, ensure_ascii=False), encoding="utf-8")

    all_dates = set(win_ext)
    for dts in stock_dates.values():
        all_dates.update(dts)
    scan = {
        "codes": picked,
        "names": [names.get(c, ("", ""))[0] for c in picked],
        "date_strings": sorted({f for dt in all_dates
                                for f in (dt.isoformat(), dt.strftime("%Y%m%d"),
                                          dt.strftime("%Y/%m/%d"),
                                          dt.strftime("%-m月%-d日"))}),
    }
    _path(exam_id, "scan").write_text(
        json.dumps(scan, ensure_ascii=False, indent=1), encoding="utf-8")

    _render_and_write(exam_id, display, msg=None, key=key, scan=scan)

    print(f"[new] 考卷 {exam_id} 已生成(tier={tier};决策日与代码已密封)")
    print(f"  数据覆盖:申万分歧线 {n_sw} 只 / 合成兜底 {n_synth} 只;"
          f"龙虎榜命中 {n_lhb_hit} 只;涨停原因命中 {n_kpl_hit} 只;"
          f"个股新闻命中 {n_news_hit} 只;大盘素材 {len(market_titles)}+{len(cctv_items)} 条")
    print(f"  下一步:子代理脱敏 raw → exam_{exam_id}_msg.json,再 render 出终版")


# ======================================================================
#  render:display + msg → 终版 HTML(泄漏硬门)
# ======================================================================

def _render_and_write(exam_id: str, display: dict, msg: Optional[dict],
                      key: dict, scan: dict) -> None:
    letter_of = {e["ts_code"]: e["letter"] for e in key["entries"]}
    entries = display["entries"]
    if msg:
        per_code = msg.get("per_code", {})
        per_ind = msg.get("per_industry", {})
        by_letter = {letter_of[c]: v for c, v in per_code.items() if c in letter_of}
        for e in entries:
            m = by_letter.get(e["letter"], {})
            e["stock_msg"] = m.get("stock_msg") or "未能取得该日消息记录"
            e["industry_msg"] = per_ind.get(e["sw_name"]) or "无(未检索到该行业当期消息)"
        display["mkt"]["market_msg"] = msg.get("market_msg") or "未能取得当期大盘消息"
    html = render_html(exam_id, entries, display["mkt"], display["tier"])

    leaks = _leak_scan_html(html, scan)
    if leaks:
        _path(exam_id, "leaks").write_text("\n".join(leaks), encoding="utf-8")
        print(f"[render] 泄漏硬门未过({len(leaks)} 处,见 "
              f"{_path(exam_id, 'leaks').name}),未写呈现件。", file=sys.stderr)
        sys.exit(3)
    _path(exam_id, "html").write_text(html, encoding="utf-8")
    print(f"[render] 呈现件已写:{_path(exam_id, 'html')}(泄漏硬门通过)")


def _leak_scan_html(html: str, scan: dict) -> List[str]:
    """硬门:真代码/6位符号/股名/任何真日期串。行业真名允许(§三.3)。"""
    hits = []
    for c in scan["codes"]:
        if c in html:
            hits.append(f"代码 {c}")
        sym = c.split(".")[0]
        if re.search(rf"(?<!\d){sym}(?!\d)", html):
            hits.append(f"符号 {sym}")
    for nm in scan["names"]:
        nm = (nm or "").strip()
        if nm and nm in html:
            hits.append(f"股名 {nm}")
        core = nm.replace("*ST", "").replace("ST", "").strip()
        if len(core) >= 3 and core in html:
            hits.append(f"股名核心 {core}")
    for s in scan["date_strings"]:
        if s in html:
            hits.append(f"日期 {s}")
    for m in re.finditer(r"(?<!\d)20[12]\d(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)",
                         html):
        hits.append(f"疑似紧凑日期 {m.group(0)}")
    for m in re.finditer(r"20[12]\d-\d{2}-\d{2}", html):
        hits.append(f"疑似 ISO 日期 {m.group(0)}")
    return sorted(set(hits))


def cmd_render(args: argparse.Namespace) -> None:
    exam_id = args.id
    display = json.loads(_path(exam_id, "display").read_text(encoding="utf-8"))
    key = json.loads(_path(exam_id, "key").read_text(encoding="utf-8"))
    scan = json.loads(_path(exam_id, "scan").read_text(encoding="utf-8"))
    msg_path = _path(exam_id, "msg")
    if not msg_path.exists():
        print(f"[render] 缺 {msg_path}(先跑脱敏子代理)。", file=sys.stderr)
        sys.exit(2)
    msg = json.loads(msg_path.read_text(encoding="utf-8"))
    _render_and_write(exam_id, display, msg, key, scan)


# ======================================================================
#  score:竞价成交层(§九)→ h9._sim_one(零改动)
# ======================================================================

def _exit_label(u: dict) -> str:
    r = u.get("exit_reason")
    if r is None:
        return u.get("fill_reason") or "—"
    if u.get("exempt"):
        return {"time": "浮盈豁免·满15日到期", "retrace": "浮盈豁免·回落止盈(-8%)",
                "stop": "浮盈豁免·止损(-5%)"}.get(r, d1.EXIT_LABEL.get(r, r))
    return d1.EXIT_LABEL.get(r, r)


def _sim_entry(code: str, d: date, buyable: bool, pm: dict, ld: set, cal: list,
               cal_idx: dict, ceiling_pct: Optional[float]) -> dict:
    """§九 成交判定 + 纪律重放。ceiling_pct=None → 无条件开盘买(基线口径)。
    与 drill._score_pick 的唯一口径差:入场价 = T+1 开盘价(竞价单一价,无滑点)。"""
    base = {"filled": False, "ret": 0.0, "hold": 0, "exempt": False,
            "exit_reason": None, "gap_pct": None}
    if not buyable or code not in pm or d not in cal_idx:
        return {**base, "fill_reason": "买不进(次日一字/停牌)"}
    k0 = cal_idx[d]
    if k0 + 1 >= len(cal):
        return {**base, "fill_reason": "买不进(无 T+1)"}
    t1 = cal[k0 + 1]
    pidx = pm[code]["idx"]
    if t1 not in pidx or d not in pidx:
        return {**base, "fill_reason": "买不进(T+1 停牌)"}
    buy_open = pm[code]["o"][pidx[t1]]
    c0 = pm[code]["c"][pidx[d]]
    gap = (buy_open / c0 - 1.0) * 100.0
    base["gap_pct"] = round(gap, 2)
    if ceiling_pct is not None:
        ceiling_price = c0 * (1.0 + ceiling_pct / 100.0)
        if buy_open > ceiling_price + _EPS:
            return {**base,
                    "fill_reason": f"未成交:高开 {gap:+.2f}% 超上限 {ceiling_pct:+.2f}%"}
    buy_price = round(buy_open, 2)
    shares = int(d1.NOTIONAL // buy_price // 100) * 100
    if shares < 100:
        shares = 100
    buy_fees = h9.BROKER._buy_fees(shares * buy_price)
    t = ClosedTrade(ts_code=code, buy_date=t1, sell_date=t1, shares=shares,
                    buy_price=buy_price, sell_price=buy_price, buy_fees=buy_fees,
                    sell_fees=0.0, reason="")
    rt = h9._sim_one(t, pm, ld, cal, cal_idx, **d1.SCORE_KW)
    if rt is None:
        return {**base, "filled": True, "fill_reason": "价缺失(诚实记 0)"}
    return {"filled": True, "fill_reason": f"成交(开盘 {gap:+.2f}%)",
            "ret": float(rt.pnl_pct), "hold": int(rt.held_sessions),
            "exempt": bool(rt.exempt), "exit_reason": rt.reason,
            "gap_pct": round(gap, 2)}


def cmd_score(args: argparse.Namespace) -> None:
    exam_id = args.id
    key = json.loads(_path(exam_id, "key").read_text(encoding="utf-8"))
    ans = json.loads(Path(args.answer).read_text(encoding="utf-8"))
    by_letter = {e["letter"]: e for e in key["entries"]}
    answers = {a["role"]: a for a in ans["answers"]}
    for role in ROLES:
        if role not in answers:
            print(f"[score] 答卷缺 {role}。", file=sys.stderr)
            sys.exit(2)
    letters_used = [answers[r]["letter"].strip().upper() for r in ROLES]
    if len(set(letters_used)) != 4 or any(x not in by_letter for x in letters_used):
        print(f"[score] 4 只代号须互不相同且有效(收到 {letters_used})。", file=sys.stderr)
        sys.exit(2)
    for r in ROLES:
        if not str(answers[r].get("thesis") or "").strip():
            print(f"[score] {r} 论点为空——强制论点是预注册纪律。", file=sys.stderr)
            sys.exit(2)

    d = date.fromisoformat(key["decision_date"])
    codes = [e["ts_code"] for e in key["entries"]]
    p = d1.panel()
    buymap = {}
    for e in key["entries"]:
        row = p.filter((pl.col("ts_code") == e["ts_code"]) & (pl.col("trade_date") == d))
        buymap[e["letter"]] = bool(row["fwd_buyable"].to_list()[0]) if row.height else False
    pm, ld, cal, cal_idx = d1._build_pm(codes)
    names = d1.stock_names(codes)

    # 基线:全池 12 只无条件开盘买(§九.5)
    uncond = {e["letter"]: _sim_entry(e["ts_code"], d, buymap[e["letter"]],
                                      pm, ld, cal, cal_idx, None)
              for e in key["entries"]}
    pool_rets = [uncond[e["letter"]]["ret"] for e in key["entries"]]
    pool_mean = sum(pool_rets) / len(pool_rets)

    # 用户:主选三只按上限成交,备用按需顶上
    fills = {}
    for role in ROLES:
        a = answers[role]
        let = a["letter"].strip().upper()
        fills[role] = {"role": role, "letter": let,
                       "ceiling_pct": float(a["ceiling_pct"]),
                       "thesis": str(a["thesis"]).strip(),
                       **_sim_entry(by_letter[let]["ts_code"], d, buymap[let],
                                    pm, ld, cal, cal_idx, float(a["ceiling_pct"]))}
    mains = [fills[r] for r in ROLES[:MAIN_K]]
    backup = fills["backup"]
    backup_active = any(not m["filled"] for m in mains)
    portfolio = [m for m in mains if m["filled"]]
    if backup_active and backup["filled"]:
        portfolio.append(backup)
    if not backup_active:
        backup["filled"] = False
        backup["fill_reason"] = "备用作废(主选三只全部成交)"

    user_mean = (sum(x["ret"] for x in portfolio) / len(portfolio)) if portfolio else None
    pctile = (d1._bootstrap_pctile(pool_rets, user_mean, len(portfolio))
              if portfolio else None)
    diag_mean = sum(uncond[m["letter"]]["ret"] for m in mains) / MAIN_K

    # —— 揭盲输出 ——
    print(f"# 判分 · 考卷 {exam_id}(决策日 {key['decision_date']},tier={key['tier']},"
          f"mode={MODE})\n")
    print("## 全池揭盲(无条件开盘买口径)\n")
    print("| 代号 | 名称 | 代码 | 行业 | 纪律收益 | 退出 | 持有 | 开盘缺口 |")
    print("|:--:|---|---|---|---:|---|---:|---:|")
    for e in sorted(key["entries"], key=lambda x: uncond[x["letter"]]["ret"],
                    reverse=True):
        let = e["letter"]
        u = uncond[let]
        nm = names.get(e["ts_code"], ("?", "?"))[0]
        mark = ""
        for r in ROLES:
            if fills[r]["letter"] == let:
                mark = " ★" + ROLE_LABEL[r]
        gap_s = f"{u['gap_pct']:+.2f}%" if u["gap_pct"] is not None else "—"
        print(f"| {let}{mark} | {nm} | {e['ts_code']} | {e['sw_l2']} | "
              f"{u['ret']*100:+.2f}% | {_exit_label(u)} | {u['hold'] or '—'} | {gap_s} |")

    print("\n## 你的成交与成绩\n")
    for r in ROLES:
        f = fills[r]
        res = (f"纪律收益 {f['ret']*100:+.2f}%(退出:{_exit_label(f)},"
               f"持有 {f['hold']})" if f["filled"] else f["fill_reason"])
        print(f"- {ROLE_LABEL[r]} {f['letter']}(上限 {f['ceiling_pct']:+.2f}%):{res}")
    if portfolio:
        print(f"\n- **实际组合 {len(portfolio)} 只均值:{user_mean*100:+.2f}%**")
        print(f"- 全池 12 只均值:{pool_mean*100:+.2f}%"
              f"(差 {(user_mean-pool_mean)*100:+.2f}pp)")
        print(f"- 随机选 {len(portfolio)} 只分位:**{pctile:.0f}%**(50%=掷骰子)")
    else:
        print(f"\n- **空手期**:四只全部未成交,合法入账(全池均值 {pool_mean*100:+.2f}%,"
              f"空手 = 0% 对照自见)")
    print(f"- 区间贡献诊断:主选三只若无上限限制 → 均值 {diag_mean*100:+.2f}%"
          f"(与实际组合的差 = 挂单上限的贡献)")

    rec = {
        "id": exam_id, "mode": MODE,
        "scored_at": datetime.now().isoformat(timespec="seconds"),
        "decision_date": key["decision_date"], "n": key["n"],
        "picks": [dict({k: fills[r][k] for k in
                        ("role", "letter", "ceiling_pct", "thesis", "filled",
                         "fill_reason", "ret", "exit_reason", "hold", "gap_pct")},
                       ts_code=by_letter[fills[r]["letter"]]["ts_code"])
                  for r in ROLES],
        "per_pick": [{"letter": x["letter"],
                      "ts_code": by_letter[x["letter"]]["ts_code"],
                      "ret": x["ret"], "reason": x["exit_reason"],
                      "thesis": x["thesis"]} for x in portfolio],
        "portfolio_letters": [x["letter"] for x in portfolio],
        "user_mean": user_mean, "pool_mean": pool_mean, "pctile": pctile,
        "pctile_k": len(portfolio) or None,
        "diag_uncond_main_mean": diag_mean,
        "all_rets": {e["letter"]: uncond[e["letter"]]["ret"] for e in key["entries"]},
    }
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"\n[score] 已落 ledger:{LEDGER}")


# ======================================================================
#  selfcheck:成交层合成用例(不触真实前视数据)
# ======================================================================

def cmd_selfcheck(_args: argparse.Namespace) -> None:
    print("# 二代考卷 · 成交层自检(合成数据)\n")
    days = [date(2030, 1, i) for i in range(1, 25)]
    cal_idx = {x: i for i, x in enumerate(days)}

    def mk(seq):
        return {"XX": {"idx": {days[i]: i for i in range(len(seq))},
                       "o": [s[0] for s in seq], "l": [s[1] for s in seq],
                       "c": [s[2] for s in seq]}}

    # day0=D:收 10.0;day1=T+1(开盘价按用例覆写);此后温和上行
    base_seq = [(10.0, 9.9, 10.0), (10.2, 10.0, 10.3)] + \
        [(10.3 + 0.05 * i, 10.1 + 0.05 * i, 10.4 + 0.05 * i) for i in range(22)]
    ok = True

    def case(name, ceiling, t1_open, want_filled):
        nonlocal ok
        s2 = list(base_seq)
        s2[1] = (t1_open, min(t1_open, 10.0), max(t1_open, 10.3))
        r = _sim_entry("XX", days[0], True, mk(s2), set(), days, cal_idx, ceiling)
        good = r["filled"] == want_filled
        ok = ok and good
        print(f"- {name}:filled={r['filled']}(期望 {want_filled})"
              f" {'✓' if good else '✗'} | {r['fill_reason']}")

    case("① 开盘 +2% ≤ 上限 +3% → 成交", 3.0, 10.2, True)
    case("② 开盘 +5% > 上限 +3% → 作废", 3.0, 10.5, False)
    case("③ 边界:开盘恰=上限(0.08-0.02 类浮点陷阱)→ 成交", 3.0, 10.3, True)
    case("④ 低开 -4% 照买(用户拍板,下沿不设)", 3.0, 9.6, True)
    case("⑤ 无条件基线口径(ceiling=None)高开 +9% 也买", None, 10.9, True)

    r = _sim_entry("XX", days[0], False, mk(base_seq), set(), days, cal_idx, 3.0)
    good = (not r["filled"]) and "一字" in r["fill_reason"]
    ok = ok and good
    print(f"- ⑥ fwd_buyable=False → 买不进 {'✓' if good else '✗'} | {r['fill_reason']}")

    for mains, want in [([True, True, True], False), ([True, False, True], True),
                        ([False, False, False], True)]:
        got = any(not m for m in mains)
        good = got == want
        ok = ok and good
        print(f"- ⑦ 主选成交={mains} → 备用激活={got}(期望 {want}){'✓' if good else '✗'}")

    r = _sim_entry("XX", days[0], True, mk(base_seq), set(), days, cal_idx, 5.0)
    good = r["filled"] and abs(r["gap_pct"] - 2.0) < 0.01
    ok = ok and good
    print(f"- ⑧ 成交价口径:开盘缺口 {r['gap_pct']}%(期望 +2.0)"
          f"{'✓' if good else '✗'};纪律收益 {r['ret']*100:+.2f}%(h9 引擎重放)")

    print(f"\n=== 自检总判:{'全过' if ok else '有挂'} ===")
    if not ok:
        sys.exit(1)


# ======================================================================

def main() -> None:
    ap = argparse.ArgumentParser(description="盲选训练·二代考卷管线(研究件)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("new", help="生成一期考卷(取样+拉数据+密封+初版渲染)")
    a.add_argument("--id", required=True)
    a.add_argument("--date-range", default="2022-05-09:2026-06-30")
    a.add_argument("--seed", type=int, default=None)
    a.set_defaults(func=cmd_new)

    a = sub.add_parser("render", help="合并脱敏消息面,渲染终版")
    a.add_argument("--id", required=True)
    a.set_defaults(func=cmd_render)

    a = sub.add_parser("score", help="判分(竞价成交层→h9 引擎)")
    a.add_argument("id")
    a.add_argument("--answer", required=True, help="answer_<id>.json 路径")
    a.set_defaults(func=cmd_score)

    a = sub.add_parser("selfcheck", help="成交层合成用例自检")
    a.set_defaults(func=cmd_selfcheck)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
