"""盲选训练模拟舱 —— 用户选股能力的历史盲测工具(纯研究件,生产零改动)。

把时间拉回历史某交易日 D 的盘前,从当日真实候选逻辑里取 N 只票、**匿名化**呈现,
用户选 K 只,按**现役纪律新规**判分。循环使用,累积统计,审计人判层的选股期望。

性质:研究/训练件,不进生产。只读 `k3_panel.parquet`(已含 qfq 价 + 全部特征)与
`stock_basic`(名/行业,仅 new 匿名化映射 + score 揭盲用),不碰 TuShare、不碰
`neckline/strategy/`、不落生产面板。输出全部落 `research/_cache/drills/`(gitignore)。

三个子命令:
  · new   —— 生成一期训练(呈现件 md + 密封答案 key.json)。
  · score —— 判分(揭盲 10 只 + 用户均值 vs 池均值 vs 随机选3分位 + 落 ledger)。
  · stats —— 历次训练累计(期数/命中率/vs池胜率/按论点标签拆分)。
  · selfcheck —— 自检:判分与 h9 模拟器口径一致 + 呈现件无泄漏(CI 用)。

**判分口径(与 h9 模拟器逐位一致)**:直接复用 `h9_exit_reform._sim_one` 重放退出——
从 T+1(D 后首个交易日)开盘买入,-5% 止损、回落止盈 8%、非浮盈第 5 日退出、浮盈单
豁免时间退出至 hold≤15,先止损后止盈的保守顺序、T+1 开盘撮合含滑点、跌停卖不出顺延。
现役纪律新规见 STRATEGY_LAB §六(2026-07-25 用户裁断:止损-5%不变 / 回落5%→8% /
浮盈单豁免时间退出硬上限15日)。

**匿名化(防认票)**:代号 A-J;价格指数化(T-60 收盘=100);日期改 T-59..T0;不给
名/代码/具体行业名(行业只给 5 大类);财务事件不做(EOD 面板无)。

**候选取样(诚实,不挑好答案)**:域 = base_universe_expr()+非次新120+全板块+close>ma20
(用户漏斗趋势域);优先热门板块(行业当日 ret_1d 中位数 top20% 强度日,复用
k4p_h6_theme 姿势)成员 × 事件性(ret_1d≥3% 或 涨停 或 vol_ratio_5≥1.5 至少其一),
从合格集合随机抽 N 只(seed 记录进 key,可复现)。硬剔仅卫生线(base_expr + 非一字板),
**不按 K4 牌预筛**——牌照常展示给用户自己用(训练的一部分)。

独立可重跑:
    python research/drill.py new [--date-range 2026-04-01:2026-07-10] [--n 10] [--seed X]
    python research/drill.py score <id> --picks A,C,F [--theses "A:题材主线;C:形态;F:资金"]
    python research/drill.py stats
    python research/drill.py selfcheck
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.research.panel import base_universe_expr  # noqa: E402
from neckline.backtest.portfolio import ClosedTrade  # noqa: E402
from k4p_common import base_expr, oneword_event_expr  # noqa: E402
import k4_assembly  # build_features / add_rule_masks / 规则常量  # noqa: E402
import h9_exit_reform as h9  # _sim_one / ReTrade / SLIP / BROKER(判分口径唯一源)  # noqa: E402

# ======================================================================
#  路径 / 常量
# ======================================================================

RESEARCH_DIR = Path(__file__).resolve().parent
K3_PANEL = RESEARCH_DIR / "_cache" / "k3_panel.parquet"
DRILLS_DIR = RESEARCH_DIR / "_cache" / "drills"
LEDGER = DRILLS_DIR / "ledger.jsonl"
DB_PATH = RESEARCH_DIR.parent / "data" / "neckline.db"

N_DEFAULT = 10            # 每期候选数
PICK_K = 3               # 用户选几只(仅提示,score 按实际 picks)
DISPLAY_SESSIONS = 60    # 指数化窗口 T-59..T0(60 交易日)
NEAR_DAYS = 10           # 近 N 日逐日表
MIN_PRIOR = DISPLAY_SESSIONS + 1   # 候选需 ≥61 个 ≤D 的交易日(base T-60 + 60 展示)
MIN_FORWARD = 16         # D 后需 ≥16 个交易日供判分(hold 到 15 不截断)
NOTIONAL = 40000.0       # 判分建仓名义额(pnl_pct 对名义额近乎不变,仅落实费用口径)

# 现役纪律新规(判分参数,与 STRATEGY_LAB §六一致)
SCORE_KW = dict(base_hold=5, retrace=0.08, stop=0.05, v1=True, hard_cap=15)
SLIP = h9.SLIP

# 行业 → 5 大类(纯匿名化展示层;未识别 → 综合类)。覆盖 stock_basic 现有 110 行业。
INDUSTRY_MACRO: Dict[str, str] = {}
for _macro, _inds in {
    "科技类": ["IT设备", "互联网", "元器件", "半导体", "通信设备", "电信运营", "软件服务",
             "电器仪表", "影视音像", "广告包装", "出版业"],
    "医药类": ["中成药", "化学制药", "医疗保健", "医药商业", "生物制药"],
    "金融类": ["银行", "保险", "证券", "多元金融", "全国地产", "区域地产", "房产服务", "园区开发"],
    "消费类": ["乳制品", "啤酒", "白酒", "红黄酒", "软饮料", "食品", "家居用品", "家用电器",
             "服饰", "纺织", "百货", "超市连锁", "商品城", "批发业", "其他商业", "电器连锁",
             "旅游景点", "旅游服务", "酒店餐饮", "文教休闲", "日用化工", "陶瓷", "商贸代理",
             "种植业", "渔业", "林业", "饲料", "农业综合"],
    "周期类": ["专用机械", "农药化肥", "化工原料", "化工机械", "化纤", "塑料", "小金属",
             "工程机械", "机床制造", "机械基件", "普钢", "特种钢", "石油加工", "石油开采",
             "石油贸易", "煤炭开采", "焦炭加工", "玻璃", "水泥", "钢加工", "铅锌", "铜", "铝",
             "黄金", "橡胶", "染料涂料", "造纸", "矿物制品", "其他建材", "轻工机械", "运输设备",
             "船舶", "航空", "农用机械", "新型电力", "电气设备", "环境保护", "水务", "供气供热",
             "火力发电", "水力发电", "港口", "路桥", "公路", "铁路", "水运", "空运", "机场",
             "仓储物流", "公共交通", "建筑工程", "装修装饰", "汽车整车", "汽车配件", "汽车服务",
             "摩托车", "纺织机械", "综合类"],
}.items():
    for _i in _inds:
        INDUSTRY_MACRO[_i] = _macro


def macro_of(industry: Optional[str]) -> str:
    return INDUSTRY_MACRO.get((industry or "").strip(), "综合类")


# K4 牌人读文案(承 k4_assembly 规则口径)
K4_CARD_LABEL = {
    "A1_turnover": ("红", "A1 换手 >10%(过热派发风险)"),
    "A2_theme4": ("红", "A2 题材已持续 ≥4 天(过热硬回避)"),
    "A3_belowlu": ("红", "A3 年线下涨停(疑似诱多做局)"),
    "B1_stack": ("黄", "B1 量能堆积大涨(派发前兆)"),
    "B2_dualcross": ("黄", "B2 双金叉态(强确认点,A股系统性均值回复)"),
    "B3_theme23": ("黄", "B3 题材持续 2-3 天(认可度存疑)"),
    "B4_chasered": ("黄", "B4 追强大红(close>ma20 且当日 >5%)"),
}
K4_ALL = list(K4_CARD_LABEL.keys())

EXIT_LABEL = {
    "stop": "止损(-5%)",
    "retrace": "回落止盈(自峰值-8%)",
    "time": "时间退出(第5日非浮盈)",
    "end": "持有到期(数据末端强平)",
}


def _exit_text(s: dict) -> str:
    """人读退出文案。浮盈豁免单的 time = 满 15 日硬上限,非"第5日非浮盈"。"""
    if not s["buyable"]:
        return s["reason"]
    r = s["reason"]
    if s["exempt"]:
        base = {"time": "满15日到期", "retrace": "回落止盈(自峰值-8%)",
                "stop": "止损(-5%)"}.get(r, EXIT_LABEL.get(r, r))
        return "浮盈豁免·" + base
    return EXIT_LABEL.get(r, r)


# ======================================================================
#  共用:面板 / 名映射
# ======================================================================

_PANEL: Optional[pl.DataFrame] = None


def panel() -> pl.DataFrame:
    global _PANEL
    if _PANEL is None:
        _PANEL = pl.read_parquet(K3_PANEL)
    return _PANEL


def stock_names(codes: Sequence[str]) -> Dict[str, Tuple[str, str]]:
    """ts_code → (name, industry)(仅 score 揭盲 / new 大类映射用)。"""
    con = sqlite3.connect(str(DB_PATH))
    q = "SELECT ts_code,name,industry FROM stock_basic WHERE ts_code IN (%s)" % (
        ",".join("?" * len(codes)))
    rows = con.execute(q, list(codes)).fetchall()
    con.close()
    return {tc: (nm, ind) for tc, nm, ind in rows}


# ======================================================================
#  new:生成一期训练
# ======================================================================

def _eligible_dates(p: pl.DataFrame, lo: date, hi: date) -> List[date]:
    """[lo,hi] 内、且面板中前有 ≥MIN_PRIOR、后有 ≥MIN_FORWARD 交易日的交易日。"""
    all_days = p.select("trade_date").unique().sort("trade_date")["trade_date"].to_list()
    idx = {d: i for i, d in enumerate(all_days)}
    n = len(all_days)
    out = []
    for d in all_days:
        if d < lo or d > hi:
            continue
        i = idx[d]
        if i >= MIN_PRIOR and (n - 1 - i) >= MIN_FORWARD:
            out.append(d)
    return out


def _qualified_at(masked: pl.DataFrame, d: date) -> Tuple[pl.DataFrame, str]:
    """在 D-slice 上算合格候选(分级降级),返回 (候选行, tier)。
    域 = base_expr & close>ma20 & 非一字板;热门 = 强度日(persist 非空);
    事件 = ret_1d≥3% | 涨停 | vol_ratio_5≥1.5。"""
    dslice = masked.filter(pl.col("trade_date") == d)
    dom = dslice.filter(
        base_expr() & (pl.col("close") > pl.col("ma20")) & ~oneword_event_expr()
    )
    event = (
        (pl.col("ret_1d") >= 0.03) | pl.col("is_limit_up") | (pl.col("vol_ratio_5") >= 1.5)
    ).fill_null(False)
    hot = pl.col("persist").is_not_null()
    tier1 = dom.filter(hot & event)
    if tier1.height >= N_DEFAULT:
        return tier1, "T1热门板块×事件"
    tier2 = dom.filter(event)
    if tier2.height >= N_DEFAULT:
        return tier2, "T2事件(放宽热门)"
    tier3 = dom.filter((pl.col("ret_1d") >= 0.03).fill_null(False))
    return tier3, "T3温和红盘(卫生线兜底)"


def _index_series(vals: List[float], base: float) -> List[float]:
    return [round(v * 100.0 / base, 2) if v is not None and base else None for v in vals]


def _card_for(masked: pl.DataFrame, code: str, d: date, sect_rank_pct: Dict[str, float]) -> dict:
    """构建单票信息卡(全部 EOD ≤D)。"""
    g = (masked.filter((pl.col("ts_code") == code) & (pl.col("trade_date") <= d))
         .sort("trade_date"))
    rows = g.to_dicts()
    t0 = rows[-1]                       # D 当日
    base_close = rows[-(DISPLAY_SESSIONS + 1)]["close"]   # T-60 收盘 = 100 基准
    win = rows[-DISPLAY_SESSIONS:]      # T-59..T0

    # 近 NEAR_DAYS 日逐日表(指数化 OHLC + 量比20 + 涨幅 + 涨停)
    near = win[-NEAR_DAYS:]
    near_tbl = []
    for k, r in enumerate(near):
        tlab = f"T-{NEAR_DAYS - 1 - k}" if k < NEAR_DAYS - 1 else "T0"
        vr20 = (r["vol"] / r["vol_ma20"]) if r.get("vol_ma20") else None
        near_tbl.append({
            "t": tlab,
            "o": _index_series([r["open"]], base_close)[0],
            "h": _index_series([r["high"]], base_close)[0],
            "l": _index_series([r["low"]], base_close)[0],
            "c": _index_series([r["close"]], base_close)[0],
            "vr20": round(vr20, 2) if vr20 is not None else None,
            "chg": round((r["ret_1d"] or 0.0) * 100, 2),
            "lu": bool(r["is_limit_up"]),
        })

    # 60 日概览
    idx_win = _index_series([r["close"] for r in win], base_close)
    cum60 = round((win[-1]["close"] / win[0]["close"] - 1) * 100, 2)
    dist_hi20 = t0.get("dist_from_high_20d")
    yearline = "上" if (t0.get("ma250") is not None and t0["close"] > t0["ma250"]) else (
        "下" if t0.get("ma250") is not None else "无(不足250日)")
    dist_ma250 = t0.get("dist_from_ma250")

    # 当日快照
    persist = t0.get("persist")
    macro = macro_of(t0.get("industry"))
    rank_pct = sect_rank_pct.get(t0.get("industry") or "", None)

    # K4 红黄牌
    cards = [K4_CARD_LABEL[c] for c in K4_ALL if t0.get(c)]

    return {
        "ts_code": code, "decision_date": d.isoformat(),
        "macro": macro,
        "near_tbl": near_tbl,
        "idx_win_min": round(min(v for v in idx_win if v is not None), 1),
        "idx_win_max": round(max(v for v in idx_win if v is not None), 1),
        "cum60": cum60,
        "dist_hi20": round(dist_hi20 * 100, 2) if dist_hi20 is not None else None,
        "yearline": yearline,
        "dist_ma250": round(dist_ma250 * 100, 2) if dist_ma250 is not None else None,
        "vol_ratio_5": round(t0["vol_ratio_5"], 2) if t0.get("vol_ratio_5") is not None else None,
        "turnover": round(t0["turnover_rate"], 2) if t0.get("turnover_rate") is not None else None,
        "persist": int(persist) if persist is not None else 0,
        "sect_rank_pct": round(rank_pct, 1) if rank_pct is not None else None,
        "consec_lu": int(t0.get("consec_limit_up_days") or 0),
        "cards": cards,
        "idx_win": idx_win,
    }


def _sector_ranks(p: pl.DataFrame, d: date) -> Dict[str, float]:
    """D 当日各行业强度百分位(全市场 ret_1d 中位数排名;越高越强,单位 %)。"""
    ds = (p.filter((pl.col("trade_date") == d) & pl.col("industry").is_not_null()
                   & pl.col("ret_1d").is_not_null())
          .group_by("industry").agg(pl.col("ret_1d").median().alias("med"), pl.len().alias("m"))
          .filter(pl.col("m") >= 5).sort("med"))
    n = ds.height
    if n == 0:
        return {}
    out = {}
    meds = ds["industry"].to_list()
    for i, ind in enumerate(meds):
        out[ind] = 100.0 * (i + 1) / n     # 百分位(100=最强)
    return out


def _market_ctx(p: pl.DataFrame, d: date) -> dict:
    """匿名市场语境:上证指数化 60 日(T-60=100)+ T0 sse_above_ma + 全市场涨跌停家数。"""
    days = (p.select("trade_date").unique().sort("trade_date")["trade_date"].to_list())
    idx = {x: i for i, x in enumerate(days)}
    i0 = idx[d]
    win_dates = days[i0 - DISPLAY_SESSIONS: i0 + 1]      # T-60..T0(61 天)
    sse = (p.filter(pl.col("trade_date").is_in(win_dates))
           .select(["trade_date", "sse_close", "sse_above_ma"]).unique().sort("trade_date"))
    closes = sse["sse_close"].to_list()
    base = closes[0]
    idx_sse = _index_series(closes[1:], base)            # T-59..T0
    dslice = p.filter(pl.col("trade_date") == d)
    return {
        "sse_idx_min": round(min(v for v in idx_sse if v is not None), 1),
        "sse_idx_max": round(max(v for v in idx_sse if v is not None), 1),
        "sse_cum60": round((closes[-1] / closes[1] - 1) * 100, 2),
        "sse_above_ma": bool(sse["sse_above_ma"].to_list()[-1]),
        "n_limit_up": int(dslice.select(pl.col("is_limit_up").sum()).item() or 0),
        "n_limit_down": int(dslice.select(pl.col("is_limit_down").sum()).item() or 0),
        "sse_idx_win": idx_sse,
    }


def _spark(vals: List[float]) -> str:
    """极简 ASCII 走势条(8 档),供 60 日形态直观参考(不泄漏绝对值)。"""
    blocks = "▁▂▃▄▅▆▇█"
    v = [x for x in vals if x is not None]
    if not v:
        return ""
    lo, hi = min(v), max(v)
    rng = hi - lo or 1.0
    return "".join(blocks[min(7, int((x - lo) / rng * 7)) if x is not None else 0] for x in vals)


def cmd_new(args: argparse.Namespace) -> None:
    DRILLS_DIR.mkdir(parents=True, exist_ok=True)
    lo_s, hi_s = args.date_range.split(":")
    lo, hi = date.fromisoformat(lo_s), date.fromisoformat(hi_s)
    seed = args.seed if args.seed is not None else random.randrange(1, 10**9)
    n = args.n
    global N_DEFAULT
    N_DEFAULT = n

    p = panel()
    print(f"[new] 面板 {p.height} 行;date-range {lo}~{hi};seed {seed};n {n}。构建 K4 特征中…",
          file=sys.stderr)
    feat = k4_assembly.build_features(p)           # add_k4p + macd/kdj + industry + persist
    masked = k4_assembly.add_rule_masks(feat)      # A1..B4 命中列

    elig = _eligible_dates(p, lo, hi)
    if not elig:
        print("[new] 无合格日期(区间内需前≥61后≥16交易日)。", file=sys.stderr)
        sys.exit(2)
    rng = random.Random(seed)
    rng.shuffle(elig)

    chosen_d = None
    cand = None
    tier = None
    for d in elig:
        c, t = _qualified_at(masked, d)
        if c.height >= n:
            chosen_d, cand, tier = d, c, t
            break
    if chosen_d is None:                            # 极端:全区间都凑不够 → 取候选最多的
        best = max(elig, key=lambda d: _qualified_at(masked, d)[0].height)
        cand, tier = _qualified_at(masked, best)
        chosen_d = best
    d = chosen_d

    # 抽 n 只(seed 可复现);仅保留有 ≥61 日历史的
    codes_all = sorted(cand["ts_code"].to_list())
    ok = []
    for c in codes_all:
        h = masked.filter((pl.col("ts_code") == c) & (pl.col("trade_date") <= d)).height
        if h >= MIN_PRIOR:
            ok.append(c)
    if len(ok) < n:
        print(f"[new] {d} 合格但足历史候选不足 {n}(有 {len(ok)});换日或调小 n。", file=sys.stderr)
        sys.exit(2)
    picked = rng.sample(ok, n)

    sect_ranks = _sector_ranks(masked, d)
    cards = [_card_for(masked, c, d, sect_ranks) for c in picked]
    # 打乱展示顺序(代号 A.. 不编码抽样次序)
    order = list(range(n))
    rng.shuffle(order)
    letters = [chr(ord("A") + i) for i in range(n)]
    entries = []
    for let, oi in zip(letters, order):
        entries.append({"letter": let, **cards[oi]})

    mkt = _market_ctx(p, d)
    drill_id = uuid.uuid4().hex[:8]

    # —— 写呈现件 md ——
    md = _render_md(drill_id, n, entries, mkt, tier)
    md_path = DRILLS_DIR / f"drill_{drill_id}.md"
    md_path.write_text(md, encoding="utf-8")

    # —— 泄漏自检(硬门:呈现件里不得出现真代码/6位符号/真名/真行业/真日期)——
    leak = _leak_scan(md, entries, d)
    if leak:
        md_path.unlink(missing_ok=True)
        print(f"[new] 泄漏自检未过:{leak};已删除呈现件,未落库。", file=sys.stderr)
        sys.exit(3)

    # —— 写密封答案 key.json(呈现件绝不含)——
    key = {
        "id": drill_id, "created": datetime.now().isoformat(timespec="seconds"),
        "seed": seed, "date_range": [lo.isoformat(), hi.isoformat()], "n": n,
        "decision_date": d.isoformat(), "tier": tier,
        "entries": [{"letter": e["letter"], "ts_code": e["ts_code"],
                     "decision_date": e["decision_date"], "macro": e["macro"],
                     "cards": [lbl for _, lbl in e["cards"]]} for e in entries],
    }
    (DRILLS_DIR / f"drill_{drill_id}_key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[new] 训练已生成:id = {drill_id}(tier={tier})")
    print(f"  呈现件:{md_path}")
    print(f"  开课:阅读呈现件,选 {PICK_K} 只,然后:")
    print(f"    python research/drill.py score {drill_id} --picks A,C,F "
          f"--theses \"A:题材主线;C:形态;F:资金\"")
    print(f"  (泄漏自检通过:呈现件无真代码/名/行业/日期)")


def _render_md(drill_id: str, n: int, entries: List[dict], mkt: dict, tier: str) -> str:
    L: List[str] = []
    L.append(f"# 盲选训练 · drill_{drill_id}")
    L.append("")
    L.append(f"> 时间已拉回历史某交易日的盘前(记该日为 **T0**)。下面 {n} 只票来自当日真实"
             f"候选逻辑,已**匿名化**(代号 A–{chr(ord('A')+n-1)}、价格指数化 T-60 收盘=100、"
             f"日期改 T-59..T0、行业只给大类)。你的任务:通读信息卡与市场语境,"
             f"**选出你认为接下来最强的 {PICK_K} 只**,记下每只的买入论点,再跑 `score` 判分。")
    L.append("")
    L.append("**判分规则(现役纪律新规)**:从 T+1 开盘买入,-5% 止损、回落止盈(自峰值)8%、"
             "非浮盈第 5 日退出、浮盈单豁免时间退出至多 15 日。判的是**纪律收益**,不是你能不能")
    L.append("扛。K4 红黄牌是系统给的避坑提示,**展示给你自己用**(不预筛),用不用你定。")
    L.append("")

    # 市场语境
    L.append("## 市场语境(匿名)")
    L.append("")
    L.append(f"- 大盘指数(上证综指)近 60 日:指数化区间 [{mkt['sse_idx_min']}, {mkt['sse_idx_max']}]"
             f"(T-60=100),60 日累计 **{mkt['sse_cum60']:+.2f}%**;走势 `{_spark(mkt['sse_idx_win'])}`")
    L.append(f"- T0 大盘状态:收盘{'**在** MA20 上方(偏多)' if mkt['sse_above_ma'] else '**在** MA20 下方(偏空)'}")
    L.append(f"- T0 全市场:涨停 **{mkt['n_limit_up']}** 家 / 跌停 **{mkt['n_limit_down']}** 家")
    L.append(f"- 候选取样档:{tier}(热门板块×事件性为主档,兜底档见括注)")
    L.append("")

    # 逐票信息卡
    for e in entries:
        L.append(f"## 候选 {e['letter']}")
        L.append("")
        rp = e["sect_rank_pct"]
        rank_s = f"前 {100 - rp:.0f}%(强度百分位 {rp:.0f})" if rp is not None else "n/a"
        yl = e["yearline"]
        yl_s = {"上": "年线**上方**", "下": "年线**下方**", }.get(yl, "年线**未成形**")
        dm = f"(距年线 {e['dist_ma250']:+.1f}%)" if e["dist_ma250"] is not None else ""
        L.append(f"- **大类**:{e['macro']}　|　**60日累计**:{e['cum60']:+.2f}%　|　"
                 f"指数化区间 [{e['idx_win_min']}, {e['idx_win_max']}]　|　走势 `{_spark(e['idx_win'])}`")
        dh = f"{e['dist_hi20']:+.1f}%" if e["dist_hi20"] is not None else "n/a"
        L.append(f"- **位置**:{yl_s}{dm}　|　距 T0 的 20 日高点 {dh}　|　连板数 {e['consec_lu']}")
        vr5 = e["vol_ratio_5"]
        L.append(f"- **T0 快照**:量比(vol_ratio_5) {vr5 if vr5 is not None else 'n/a'}　|　"
                 f"换手率 {e['turnover'] if e['turnover'] is not None else 'n/a'}%　|　"
                 f"题材持续 {e['persist']} 天　|　板块强度 {rank_s}")
        if e["cards"]:
            reds = [lbl for lvl, lbl in e["cards"] if lvl == "红"]
            yellows = [lbl for lvl, lbl in e["cards"] if lvl == "黄"]
            if reds:
                L.append(f"- **🔴 红牌(硬剔类)**:{'; '.join(reds)}")
            if yellows:
                L.append(f"- **🟡 黄牌(标注类)**:{'; '.join(yellows)}")
        else:
            L.append("- **K4 牌**:无红黄牌")
        L.append("")
        L.append(f"  近 {NEAR_DAYS} 日(指数化 OHLC / 量比20日均量 / 当日涨幅 / 是否涨停):")
        L.append("")
        L.append("  | 日 | 开 | 高 | 低 | 收 | 量比20 | 涨幅% | 涨停 |")
        L.append("  |---|---:|---:|---:|---:|---:|---:|:--:|")
        for r in e["near_tbl"]:
            L.append(f"  | {r['t']} | {r['o']} | {r['h']} | {r['l']} | {r['c']} | "
                     f"{r['vr20'] if r['vr20'] is not None else 'n/a'} | {r['chg']:+.2f} | "
                     f"{'✔' if r['lu'] else ''} |")
        L.append("")

    L.append("---")
    L.append("")
    L.append(f"**选完怎么判分**:`python research/drill.py score {drill_id} --picks A,C,F "
             f"--theses \"A:...;C:...;F:...\"`(picks 填你选的字母,theses 选填每只的论点标签)。")
    return "\n".join(L)


def _leak_scan(md: str, entries: List[dict], d: date) -> Optional[str]:
    """呈现件泄漏扫描:真代码/6位符号/真日期/真行业名出现即算泄漏。真名不在本函数
    覆盖内(new 阶段无名),但代码/符号已足够堵认票主路径。"""
    hits = []
    for e in entries:
        code = e["ts_code"]
        if code in md:
            hits.append(f"代码 {code}")
        sym = code.split(".")[0]
        if sym in md:                    # 6 位数字符号
            hits.append(f"符号 {sym}")
    # 真行业名(具体行业,如"半导体"),不应出现在呈现件(只应出现大类)
    names = stock_names([e["ts_code"] for e in entries])
    for e in entries:
        nm_ind = names.get(e["ts_code"])
        if nm_ind:
            nm, ind = nm_ind
            if nm and nm in md:
                hits.append(f"股名 {nm}")
            if ind and ind.strip() and ind.strip() in md and macro_of(ind) != ind.strip():
                hits.append(f"行业 {ind}")
    # 真日期(D 及其 ISO / 紧凑串)
    for form in (d.isoformat(), d.strftime("%Y%m%d"), d.strftime("%Y/%m/%d")):
        if form in md:
            hits.append(f"日期 {form}")
    return "; ".join(sorted(set(hits))) if hits else None


# ======================================================================
#  判分:重放退出模拟器(复用 h9._sim_one,口径唯一源)
# ======================================================================

def _build_pm(codes: Sequence[str]) -> Tuple[dict, set, list, dict]:
    """从面板(已 qfq)为 codes 建价格图 + 跌停集 + 全历交易日历。判分只吃面板。"""
    p = panel()
    cal = p.select("trade_date").unique().sort("trade_date")["trade_date"].to_list()
    cal_idx = {x: i for i, x in enumerate(cal)}
    sub = p.filter(pl.col("ts_code").is_in(list(codes)))
    pm: dict = {}
    for (code,), g in sub.group_by(["ts_code"]):
        g = g.sort("trade_date")
        dts = g["trade_date"].to_list()
        pm[code] = {
            "idx": {dt: i for i, dt in enumerate(dts)},
            "o": g["open"].to_list(), "l": g["low"].to_list(), "c": g["close"].to_list(),
        }
    ld = set()
    ldf = sub.filter(pl.col("is_limit_down"))
    ld = set(zip(ldf["ts_code"].to_list(), ldf["trade_date"].to_list()))
    return pm, ld, cal, cal_idx


def _score_pick(code: str, d: date, buyable: bool, pm: dict, ld: set, cal: list,
                cal_idx: dict) -> dict:
    """判一只票的纪律收益(从 D 后 T+1 开盘买入)。不可买 → 记买不进(ret=0)。"""
    if not buyable or code not in pm or d not in cal_idx:
        return {"buyable": False, "ret": 0.0, "reason": "买不进(次日一字/停牌)",
                "hold": 0, "exempt": False, "exit_t": None}
    k0 = cal_idx[d]
    if k0 + 1 >= len(cal):
        return {"buyable": False, "ret": 0.0, "reason": "买不进(无 T+1)",
                "hold": 0, "exempt": False, "exit_t": None}
    t1 = cal[k0 + 1]
    pidx = pm[code]["idx"]
    if t1 not in pidx:
        return {"buyable": False, "ret": 0.0, "reason": "买不进(T+1 停牌)",
                "hold": 0, "exempt": False, "exit_t": None}
    buy_open = pm[code]["o"][pidx[t1]]
    buy_price = round(buy_open * (1 + SLIP), 2)
    shares = int(NOTIONAL // buy_price // 100) * 100
    if shares < 100:
        shares = 100
    buy_fees = h9.BROKER._buy_fees(shares * buy_price)
    t = ClosedTrade(ts_code=code, buy_date=t1, sell_date=t1, shares=shares,
                    buy_price=buy_price, sell_price=buy_price, buy_fees=buy_fees,
                    sell_fees=0.0, reason="")
    rt = h9._sim_one(t, pm, ld, cal, cal_idx, **SCORE_KW)
    if rt is None:                       # 取不到价(极少)→ 诚实记 0
        return {"buyable": True, "ret": 0.0, "reason": "价缺失", "hold": 0,
                "exempt": False, "exit_t": None}
    return {"buyable": True, "ret": float(rt.pnl_pct), "reason": rt.reason,
            "hold": int(rt.held_sessions), "exempt": bool(rt.exempt),
            "exit_t": rt.sell_date.isoformat()}


def _bootstrap_pctile(rets: List[float], user_mean: float, k: int, draws: int = 1000,
                      seed: int = 12345) -> float:
    """随机选 k 只的均值分布中,user_mean 的分位(< user_mean 的比例,单位 %)。"""
    rng = random.Random(seed)
    idxs = list(range(len(rets)))
    below = 0
    for _ in range(draws):
        s = rng.sample(idxs, k)
        m = sum(rets[i] for i in s) / k
        if m < user_mean - 1e-12:
            below += 1
    return 100.0 * below / draws


def cmd_score(args: argparse.Namespace) -> None:
    key_path = DRILLS_DIR / f"drill_{args.id}_key.json"
    if not key_path.exists():
        print(f"[score] 找不到密封件 {key_path}(id 对吗?)", file=sys.stderr)
        sys.exit(2)
    key = json.loads(key_path.read_text(encoding="utf-8"))
    entries = key["entries"]
    by_letter = {e["letter"]: e for e in entries}
    picks = [x.strip().upper() for x in args.picks.split(",") if x.strip()]
    for pk in picks:
        if pk not in by_letter:
            print(f"[score] picks 含未知代号 {pk}(有效:{sorted(by_letter)})", file=sys.stderr)
            sys.exit(2)
    theses: Dict[str, str] = {}
    if args.theses:
        for part in args.theses.split(";"):
            if ":" in part:
                a, b = part.split(":", 1)
                theses[a.strip().upper()] = b.strip()

    codes = [e["ts_code"] for e in entries]
    p = panel()
    # buyability(面板 fwd_buyable @ D)
    buymap: Dict[str, bool] = {}
    for e in entries:
        row = p.filter((pl.col("ts_code") == e["ts_code"])
                       & (pl.col("trade_date") == date.fromisoformat(e["decision_date"])))
        buymap[e["letter"]] = bool(row["fwd_buyable"].to_list()[0]) if row.height else False

    pm, ld, cal, cal_idx = _build_pm(codes)
    names = stock_names(codes)

    scored: Dict[str, dict] = {}
    for e in entries:
        d = date.fromisoformat(e["decision_date"])
        scored[e["letter"]] = _score_pick(e["ts_code"], d, buymap[e["letter"]],
                                           pm, ld, cal, cal_idx)

    rets = [scored[e["letter"]]["ret"] for e in entries]
    pool_mean = sum(rets) / len(rets)
    user_rets = [scored[pk]["ret"] for pk in picks]
    user_mean = sum(user_rets) / len(user_rets)
    pctile = _bootstrap_pctile(rets, user_mean, len(picks))

    # —— 揭盲输出 ——
    print(f"# 判分 · drill_{args.id}(决策日 {key['decision_date']},tier={key['tier']})\n")
    print("## 全池揭盲(真名/真代码/真日期 + 纪律收益)\n")
    print("| 代号 | 名称 | 代码 | 大类 | 决策日 | 纪律收益 | 退出 | 持有(交易日) | K4牌 |")
    print("|:--:|---|---|:--:|---|---:|---|---:|---|")
    order = sorted(entries, key=lambda e: scored[e["letter"]]["ret"], reverse=True)
    for e in order:
        let = e["letter"]
        s = scored[let]
        nm, ind = names.get(e["ts_code"], ("?", "?"))
        star = " ★你" if let in picks else ""
        # key.json 的 cards 是人读文案串(A?/B? 前缀在文案里),按"A""B"编号计数
        cardsum = ("红" + str(sum(1 for c in e["cards"] if c.startswith("A")))
                   + "/黄" + str(sum(1 for c in e["cards"] if c.startswith("B")))) if e["cards"] else "—"
        print(f"| {let}{star} | {nm} | {e['ts_code']} | {e['macro']} | {e['decision_date']} | "
              f"{s['ret']*100:+.2f}% | {_exit_text(s)} | "
              f"{s['hold'] if s['buyable'] else '—'} | {cardsum} |")

    print("\n## 你的成绩\n")
    print(f"- 你选:{', '.join(picks)}　→　各自纪律收益:"
          + "、".join(f"{pk} {scored[pk]['ret']*100:+.2f}%" for pk in picks))
    print(f"- **你 3 只均值:{user_mean*100:+.2f}%**")
    print(f"- 全池 {len(entries)} 只均值:{pool_mean*100:+.2f}%　"
          f"(差 {(user_mean-pool_mean)*100:+.2f}pp)")
    print(f"- 随机选 {len(picks)} 只分布分位:**{pctile:.0f}%**"
          f"(你打败了 {pctile:.0f}% 的随机组合;50% = 与掷骰子无异)")
    if theses:
        print(f"- 论点标签:" + "、".join(f"{a}={b}" for a, b in theses.items()))
    unbuyable = [e["letter"] for e in entries if not scored[e["letter"]]["buyable"]]
    if unbuyable:
        print(f"- 注:{', '.join(unbuyable)} 次日买不进(一字/停牌),按 0 收益计入池均值。")

    # —— 落 ledger ——
    rec = {
        "id": args.id, "scored_at": datetime.now().isoformat(timespec="seconds"),
        "decision_date": key["decision_date"], "n": key["n"], "picks": picks,
        "user_mean": user_mean, "pool_mean": pool_mean, "pctile": pctile,
        "per_pick": [{"letter": pk, "ts_code": by_letter[pk]["ts_code"],
                      "ret": scored[pk]["ret"], "reason": scored[pk]["reason"],
                      "thesis": theses.get(pk)} for pk in picks],
        "all_rets": {e["letter"]: scored[e["letter"]]["ret"] for e in entries},
    }
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"\n[score] 已落 ledger:{LEDGER}(累计 {sum(1 for _ in LEDGER.open(encoding='utf-8'))} 期)")


# ======================================================================
#  stats:历次累计
# ======================================================================

def cmd_stats(_args: argparse.Namespace) -> None:
    if not LEDGER.exists():
        print("[stats] 还没有判分记录(先 new → score)。")
        return
    recs = [json.loads(l) for l in LEDGER.open(encoding="utf-8") if l.strip()]
    n = len(recs)
    print(f"# 盲选训练累计 · {n} 期\n")
    win_vs_pool = sum(1 for r in recs if r["user_mean"] > r["pool_mean"])
    pick_rets = [pp["ret"] for r in recs for pp in r["per_pick"]]
    n_picks = len(pick_rets)
    hit = sum(1 for x in pick_rets if x > 0)
    print(f"- 选中笔数:{n_picks};命中(纪律收益>0)率:**{100*hit/n_picks:.1f}%**"
          if n_picks else "- 选中笔数:0")
    print(f"- vs 池均值胜率:**{100*win_vs_pool/n:.1f}%**({win_vs_pool}/{n} 期跑赢全池均值)")
    if pick_rets:
        avg = sum(pick_rets) / n_picks
        print(f"- 选中票平均纪律收益:{avg*100:+.2f}%")
    avg_pctile = sum(r["pctile"] for r in recs) / n
    print(f"- 平均随机分位:{avg_pctile:.0f}%(>50 说明选股优于随机)")

    # 按论点标签拆分
    by_thesis: Dict[str, List[float]] = {}
    for r in recs:
        for pp in r["per_pick"]:
            th = pp.get("thesis") or "(未标注)"
            by_thesis.setdefault(th, []).append(pp["ret"])
    if by_thesis:
        print("\n## 按论点标签\n")
        print("| 论点 | 笔数 | 命中率 | 平均收益 |")
        print("|---|---:|---:|---:|")
        for th, xs in sorted(by_thesis.items(), key=lambda kv: -len(kv[1])):
            h = sum(1 for x in xs if x > 0)
            print(f"| {th} | {len(xs)} | {100*h/len(xs):.0f}% | {sum(xs)/len(xs)*100:+.2f}% |")


# ======================================================================
#  selfcheck:判分口径 == h9 + 无泄漏
# ======================================================================

def cmd_selfcheck(_args: argparse.Namespace) -> None:
    print("# 盲选训练舱 · 自检\n")

    # (1) 判分口径 == h9 模拟器:对真实 K1 入场单,用面板价重放,与 h9.replay(现役新规)对拍。
    print("## (1) 判分口径 vs h9 模拟器(现役新规 retrace=0.08+v1,真实 K1 入场单)")
    _, ct = h9.baseline()                        # K1 现役 1288 单(真实入场)
    # 只取 2024-2025 入场(远早于任一 qfq 锚点,充分成形,规避末端截断噪声)
    sample = [t for t in ct if t.buy_date.year in (2024, 2025)]
    ref = h9.replay(sample, **SCORE_KW)          # h9 口径(qfq 锚 2026-07-17)
    pm, ld, cal, cal_idx = _build_pm([t.ts_code for t in sample])   # 面板 qfq(锚 2026-07-24)
    same_reason = same_exit = 0
    max_ret_diff = 0.0
    n_cmp = 0
    for t, r in zip(sample, ref):
        # 用面板价 + 真实 shares/fees 复算(与 h9 同一 _sim_one,只是 pm 锚点不同)
        tt = ClosedTrade(ts_code=t.ts_code, buy_date=t.buy_date, sell_date=t.buy_date,
                         shares=t.shares, buy_price=None, sell_price=None,
                         buy_fees=t.buy_fees, sell_fees=0.0, reason="")
        # buy_price 用面板 T(=buy_date)开盘(引擎入场价口径:round(open*(1+slip),2))
        pidx = pm.get(t.ts_code, {}).get("idx", {})
        if t.buy_date not in pidx:
            continue
        tt.buy_price = round(pm[t.ts_code]["o"][pidx[t.buy_date]] * (1 + SLIP), 2)
        tt.sell_price = tt.buy_price
        mine = h9._sim_one(tt, pm, ld, cal, cal_idx, **SCORE_KW)
        if mine is None:
            continue
        n_cmp += 1
        if mine.reason == r.reason:
            same_reason += 1
        if mine.sell_date == r.sell_date:
            same_exit += 1
        max_ret_diff = max(max_ret_diff, abs(mine.pnl_pct - r.pnl_pct))
    print(f"- 对拍样本 {n_cmp} 单;退出原因一致 {same_reason}/{n_cmp} = {100*same_reason/n_cmp:.1f}%;"
          f"卖出日一致 {same_exit}/{n_cmp} = {100*same_exit/n_cmp:.1f}%;"
          f"纪律收益最大偏差 {max_ret_diff*100:.3f}pp")
    ok1 = (same_reason / n_cmp >= 0.99) and (same_exit / n_cmp >= 0.99) and (max_ret_diff < 0.01)
    print(f"- 说明:面板 qfq 锚 2026-07-24 vs h9 锚 2026-07-17,差一个每票常数因子"
          f"(收益比不变,仅 2 位小数取整偶有微差)→ 口径一致。判定:**{'过' if ok1 else '挂'}**")

    # (2) 端到端 2 期(new→随机 score)+ 无泄漏 grep
    print("\n## (2) 端到端 2 期(new→随机picks score)+ 呈现件无泄漏 grep")
    p = panel()
    feat = k4_assembly.build_features(p)
    masked = k4_assembly.add_rule_masks(feat)
    global N_DEFAULT
    N_DEFAULT = 10
    ok2 = True
    for seed in (101, 202):
        elig = _eligible_dates(p, date(2026, 4, 1), date(2026, 7, 1))
        rng = random.Random(seed)
        rng.shuffle(elig)
        d = cand = tier = None
        for dd in elig:
            c, t = _qualified_at(masked, dd)
            if c.height >= 10:
                d, cand, tier = dd, c, t
                break
        codes = sorted(cand["ts_code"].to_list())
        ok = [c for c in codes
              if masked.filter((pl.col("ts_code") == c) & (pl.col("trade_date") <= d)).height >= MIN_PRIOR]
        picked = rng.sample(ok, 10)
        sect = _sector_ranks(masked, d)
        cards = [_card_for(masked, c, d, sect) for c in picked]
        order = list(range(10)); rng.shuffle(order)
        entries = [{"letter": chr(ord("A") + i), **cards[order[i]]} for i in range(10)]
        mkt = _market_ctx(p, d)
        md = _render_md("selfcheck", 10, entries, mkt, tier)
        leak = _leak_scan(md, entries, d)
        # 随机 score:直接算 3 只均值(不落 ledger)
        pm, ld, cal, cal_idx = _build_pm(picked)
        rets = []
        for e in entries:
            dd = date.fromisoformat(e["decision_date"])
            row = p.filter((pl.col("ts_code") == e["ts_code"]) & (pl.col("trade_date") == dd))
            buyable = bool(row["fwd_buyable"].to_list()[0]) if row.height else False
            rets.append(_score_pick(e["ts_code"], dd, buyable, pm, ld, cal, cal_idx)["ret"])
        p3 = random.Random(seed).sample(range(10), 3)
        um = sum(rets[i] for i in p3) / 3
        pool = sum(rets) / 10
        status = "无泄漏" if leak is None else f"泄漏:{leak}"
        print(f"- seed {seed}:决策日(密封) tier={tier};10 只判分完成,"
              f"随机3只均值 {um*100:+.2f}% vs 池 {pool*100:+.2f}%;呈现件 {status}")
        ok2 = ok2 and (leak is None)
    print(f"- 判定:**{'过(无泄漏)' if ok2 else '挂(有泄漏)'}**")

    print(f"\n=== 自检总判:{'全过' if (ok1 and ok2) else '有挂,见上'} ===")
    if not (ok1 and ok2):
        sys.exit(1)


# ======================================================================

def main() -> None:
    ap = argparse.ArgumentParser(description="盲选训练模拟舱(研究件)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("new", help="生成一期训练")
    a.add_argument("--date-range", default="2026-04-01:2026-07-01")
    a.add_argument("--n", type=int, default=N_DEFAULT)
    a.add_argument("--seed", type=int, default=None)
    a.set_defaults(func=cmd_new)

    a = sub.add_parser("score", help="判分")
    a.add_argument("id")
    a.add_argument("--picks", required=True, help="逗号分隔字母,如 A,C,F")
    a.add_argument("--theses", default=None, help="分号分隔,如 'A:题材;C:形态'")
    a.set_defaults(func=cmd_score)

    a = sub.add_parser("stats", help="历次累计")
    a.set_defaults(func=cmd_stats)

    a = sub.add_parser("selfcheck", help="口径 + 无泄漏自检")
    a.set_defaults(func=cmd_selfcheck)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
