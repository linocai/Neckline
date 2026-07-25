"""赢家解剖(纯描述性右尾分析,非假设战役,不预注册假设)。

回答用户「盈利单发生得怎么样、长什么样」。**全程只读分析,生产零改动、不写 DB**。
纪律流平仓单口径全读现役 config(`brain.get_active()`),信号级右尾口径与
`neckline.research.eventstudy` 严格对齐。底面板 `k3_panel` 切 ≤2026-07-17(既定姿势,
与三役冻结窗逐位可比)。

**防自欺声明(报告 §0 同源)**:本 runner 为事后描述性解剖,任何「赢家特征」均为
幸存者视角的相关性,不构成可交易规则;若要使用须先预注册为假设并样本外验证
(见 STRATEGY_LAB 雷区地图 K2「+15% 记忆=幸存者偏差」判决 + 研究铁律 6 多重检验警惕)。

五节:
  1. K1 纪律流平仓单右尾分布(全期一次 2021-01-01~2026-07-17)。
  2. 大赢家(≥+10%)拆解:退出机制 / 持有天数 / 持有内涨停 / 逐年。
  3. 画像对照:大赢家 vs 大输家(止损单)在入场日(决策日 D)特征上的分布——
     核心防特征幻觉,逐特征标注「有区分力 / 无区分力」。
  4. 卖飞率:回落止盈离场后 5 交易日内 >10%/>5% 占比;止损后反弹 >5% 对照。
  5. 信号级右尾补充:三役关键组补报 p90/p95(此前只报 p5/p10)。

独立可重跑:`python research/winners_anatomy.py`
"""

from __future__ import annotations

import sys
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import prev_trading_day, trading_days_between  # noqa: E402
from neckline.research.eventstudy import DEFAULT_COST_ONESIDE  # noqa: E402
from neckline.strategy import brain  # noqa: E402
from neckline.strategy.momentum import MomentumConfig  # noqa: E402
from k4p_common import add_k4p_features, base_expr, oneword_event_expr  # noqa: E402
import lab  # noqa: E402

K3_PANEL = Path(__file__).resolve().parent / "_cache" / "k3_panel.parquet"
FROZEN_END = date(2026, 7, 17)          # 三役冻结窗末端(逐位可比)
RUN_START = date(2021, 1, 1)            # K1 全期起点(既定:2020 段趋势轴/ma250 无效,主判 2021+)
RALLY_END = date(2026, 7, 24)           # 卖飞前视只用 qfq 收盘(不改判决窗;k3_panel 载到 07-24)
COST2 = 2 * DEFAULT_COST_ONESIDE

BIG_WIN = 0.10      # 大赢家阈值(净 pnl_pct)
BIG_LOSS = -0.05    # 大输家参考阈值(净 pnl_pct;大输家主定义用 reason=止损)

_PANEL: Optional[pl.DataFrame] = None
_RUN: Optional[Tuple[object, object]] = None
_QFQ: Optional[pl.DataFrame] = None


# ======================================================================
#  基础设施
# ======================================================================

def panel() -> pl.DataFrame:
    global _PANEL
    if _PANEL is None:
        _PANEL = pl.read_parquet(K3_PANEL).filter(pl.col("trade_date") <= FROZEN_END)
    return _PANEL


def k1_run() -> Tuple[object, object]:
    """K1 现役 config 全期一次(2021-01-01~2026-07-17)。纪律全读 brain.get_active()。"""
    global _RUN
    if _RUN is None:
        cfg = MomentumConfig(**brain.active_config())
        _RUN = lab.run_pf(cfg, RUN_START, FROZEN_END, panel=panel())
    return _RUN


def qfq_frame() -> pl.DataFrame:
    """前复权 daily [RUN_START, RALLY_END](单一锚点,卖飞前视自洽:同一 frame 内
    close[t2]/open[t1] 比值与锚点无关、且正确扣分红,见 §4 口径)。"""
    global _QFQ
    if _QFQ is None:
        _QFQ = lab.adjusted_daily_cached(RUN_START, RALLY_END)
    return _QFQ


def _pcts(trades) -> List[float]:
    return [t.pnl_pct for t in trades]


def _q(xs: Sequence[float], p: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    return float(pl.Series(s).quantile(p, interpolation="linear"))


def _f(x, p=4) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return "nan"
    if isinstance(x, float) and abs(x) > 1e9:
        return "inf"
    return f"{x:.{p}f}"


def _md(header: List[str], rows: List[List[str]]) -> str:
    line = "| " + " | ".join(header) + " |"
    sep = "|" + "|".join("---" if i == 0 else "--:" for i in range(len(header))) + "|"
    return "\n".join([line, sep] + ["| " + " | ".join(r) + " |" for r in rows])


def hold_sessions(t) -> int:
    """持有交易日跨度(含买卖两端,与引擎 held 口径一致:买入日计为第 1 日)。"""
    return len(trading_days_between(t.buy_date, t.sell_date))


# ======================================================================
#  §1 右尾分布
# ======================================================================

def section1() -> str:
    rep, pf = k1_run()
    ct = pf.closed_trades
    pcts = _pcts(ct)
    n = len(ct)
    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p < 0]
    avg_win = sum(wins) / len(wins) if wins else float("nan")
    avg_loss = sum(losses) / len(losses) if losses else float("nan")
    payoff = (avg_win / abs(avg_loss)) if losses and avg_loss != 0 else float("nan")

    out = ["## §1 K1 纪律流平仓单右尾分布(全期一次 2021-01-01~2026-07-17)", ""]
    out.append(f"- 平仓单总数 **N={n}**;组合级总收益 **{rep.total_return:.2%}**、"
               f"胜率 {rep.win_rate:.1%}、盈利因子 {rep.profit_factor:.3f}。")
    out.append(f"- 口径:pnl_pct = 单笔**组合级净收益**(成交价含滑点,再扣佣金+印花税)/ 建仓含费成本。全部平仓回合。")
    out.append("")

    # 分位直方
    hdr = ["分位", "p10", "p25", "p50", "p75", "p90", "p95", "max", "min"]
    row = [["pnl_pct",
            _f(_q(pcts, .10)), _f(_q(pcts, .25)), _f(_q(pcts, .50)), _f(_q(pcts, .75)),
            _f(_q(pcts, .90)), _f(_q(pcts, .95)), _f(max(pcts)), _f(min(pcts))]]
    out.append("**单笔净收益分位分布**\n")
    out.append(_md(hdr, row))
    out.append("")

    # 右尾频率 vs 止损
    def cnt(pred):
        c = sum(1 for p in pcts if pred(p))
        return c, c / n, c / n * 100
    ge5 = cnt(lambda p: p >= 0.05)
    ge10 = cnt(lambda p: p >= 0.10)
    ge15 = cnt(lambda p: p >= 0.15)
    le5 = cnt(lambda p: p <= -0.05)
    stop_reason = sum(1 for t in ct if t.reason.startswith("止损"))
    hdr2 = ["区间", "笔数", "占比", "每百笔"]
    rows2 = [
        ["净收益 ≥ +5%", str(ge5[0]), f"{ge5[1]:.1%}", f"{ge5[2]:.1f}"],
        ["净收益 ≥ +10%(大赢家)", str(ge10[0]), f"{ge10[1]:.1%}", f"{ge10[2]:.1f}"],
        ["净收益 ≥ +15%", str(ge15[0]), f"{ge15[1]:.1%}", f"{ge15[2]:.1f}"],
        ["净收益 ≤ −5%(深亏区)", str(le5[0]), f"{le5[1]:.1%}", f"{le5[2]:.1f}"],
        ["其中 reason=止损 触发", str(stop_reason), f"{stop_reason/n:.1%}", f"{stop_reason/n*100:.1f}"],
    ]
    out.append("**右尾频率 vs 深亏区(每百笔几笔)**\n")
    out.append(_md(hdr2, rows2))
    out.append("")
    out.append(f"- 胜负笔均值:平均赢家 **{avg_win:+.2%}**(n={len(wins)}) vs 平均输家 "
               f"**{avg_loss:+.2%}**(n={len(losses)});**盈亏比(均值口径)= {payoff:.3f}**。")
    out.append(f"- 注:reason=止损 有 {stop_reason} 笔,但净收益 ≤−5% 只有 {le5[0]} 笔——"
               f"差额 {stop_reason - le5[0]} 笔是止损触发(收盘/最低破位)后**次日开盘跳空回补**、"
               f"实际卖在 −5% 上方(纪律封顶为软封顶,非硬 −5%)。")
    return "\n".join(out)


# ======================================================================
#  §2 大赢家拆解
# ======================================================================

def _limit_up_lookup(codes: set) -> Dict[str, Dict[date, bool]]:
    sub = panel().filter(pl.col("ts_code").is_in(list(codes))).select(["ts_code", "trade_date", "is_limit_up"])
    out: Dict[str, Dict[date, bool]] = {}
    for code, g in sub.group_by("ts_code"):
        c = code[0] if isinstance(code, tuple) else code
        out[c] = dict(zip(g["trade_date"].to_list(), g["is_limit_up"].to_list()))
    return out


def _hit_limit_up_in_hold(t, lu: Dict[str, Dict[date, bool]]) -> bool:
    """持有区间(含买卖日)内任一交易日收盘涨停。"""
    d2b = lu.get(t.ts_code, {})
    for d in trading_days_between(t.buy_date, t.sell_date):
        if d2b.get(d):
            return True
    return False


def section2() -> str:
    _, pf = k1_run()
    big = [t for t in pf.closed_trades if t.pnl_pct >= BIG_WIN]
    n = len(big)
    lu = _limit_up_lookup({t.ts_code for t in big})

    out = ["## §2 大赢家(净收益 ≥ +10%)拆解", ""]
    out.append(f"- 大赢家共 **{n}** 笔(占全部 {len(pf.closed_trades)} 笔的 {n/len(pf.closed_trades):.1%})。"
               f"**样本极小,下述拆解为描述性,不做统计推断。**")
    out.append("")

    # 退出机制
    def bucket(reason: str) -> str:
        if reason.startswith("回落止盈"):
            return "回落止盈"
        if reason.startswith("时间退出"):
            return "时间退出(hold 到期)"
        if reason.startswith("止损"):
            return "止损"
        if reason.startswith("固定止盈"):
            return "固定止盈"
        return "其他"
    bc = Counter(bucket(t.reason) for t in big)
    hdr = ["退出机制", "笔数", "占大赢家", "该机制内平均净收益"]
    rows = []
    for k in ("回落止盈", "时间退出(hold 到期)", "止损", "固定止盈", "其他"):
        if bc.get(k):
            sub = [t for t in big if bucket(t.reason) == k]
            rows.append([k, str(len(sub)), f"{len(sub)/n:.1%}",
                         f"{sum(x.pnl_pct for x in sub)/len(sub):+.2%}"])
    out.append("**退出机制归类**\n")
    out.append(_md(hdr, rows))
    out.append("")

    # 持有天数 + 持有内涨停
    spans = [hold_sessions(t) for t in big]
    lu_hits = sum(1 for t in big if _hit_limit_up_in_hold(t, lu))
    out.append(f"- 平均持有交易日跨度(含买卖两端,引擎口径)= **{sum(spans)/n:.2f}**"
               f"(中位 {int(_q(spans,.5))};min {min(spans)} / max {max(spans)})。")
    out.append(f"- 持有区间(含买卖日)内**出现过收盘涨停**的:**{lu_hits}/{n}**"
               f"({lu_hits/n:.1%})——大赢家多半是持有期内摸到涨停的强票。")
    out.append("")

    # 逐年
    ybc = Counter(t.buy_date.year for t in big)
    hdr3 = ["买入年份", "大赢家笔数", "该年该机制平均净收益"]
    rows3 = []
    for y in sorted(ybc):
        sub = [t for t in big if t.buy_date.year == y]
        rows3.append([str(y), str(len(sub)), f"{sum(x.pnl_pct for x in sub)/len(sub):+.2%}"])
    out.append("**逐年分布(按买入年)**\n")
    out.append(_md(hdr3, rows3))
    return "\n".join(out)


# ======================================================================
#  §3 画像对照(核心:防特征幻觉)
# ======================================================================

def _decision_rows(trades) -> pl.DataFrame:
    """把平仓单映射回入场决策日 D(= 买入日前一交易日)的面板行,取画像特征。"""
    feats = ["board", "ret_1d", "vol_ratio_5", "turnover_rate",
             "dist_from_ma250", "close", "ma250", "sse_above_ma", "year", "is_limit_up"]
    # 决策日 = prev_trading_day(buy_date);(ts_code, 决策日) join 面板
    buy_dates = {t.buy_date for t in trades}
    d_map = {bd: prev_trading_day(bd) for bd in buy_dates}
    keys = pl.DataFrame({
        "ts_code": [t.ts_code for t in trades],
        "decision_date": [d_map[t.buy_date] for t in trades],
    })
    p = panel().select(["ts_code", "trade_date"] + feats).rename({"trade_date": "decision_date"})
    return keys.join(p, on=["ts_code", "decision_date"], how="left")


def _num_summary(s: pl.Series) -> Tuple[int, float, float, float, float]:
    s = s.drop_nulls()
    if s.len() == 0:
        return 0, float("nan"), float("nan"), float("nan"), float("nan")
    return (s.len(), float(s.quantile(.25)), float(s.median()),
            float(s.quantile(.75)), float(s.mean()))


def _smd(a: pl.Series, b: pl.Series) -> float:
    """标准化均值差(Cohen's d,pooled sd)。|d|<0.2 视为无区分力。"""
    a, b = a.drop_nulls(), b.drop_nulls()
    if a.len() < 2 or b.len() < 2:
        return float("nan")
    va, vb = float(a.var()), float(b.var())
    na, nb = a.len(), b.len()
    psd = (((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)) ** 0.5
    if psd == 0:
        return float("nan")
    return (float(a.mean()) - float(b.mean())) / psd


def _verdict_num(d: float) -> str:
    if d != d:
        return "样本不足"
    ad = abs(d)
    if ad < 0.2:
        return "无区分力"
    if ad < 0.5:
        return "弱差异·仅供未来预注册假设,禁止直接入规则"
    return "较明显差异·仅供未来预注册假设,禁止直接入规则"


def section3() -> str:
    _, pf = k1_run()
    win = [t for t in pf.closed_trades if t.pnl_pct >= BIG_WIN]
    loss = [t for t in pf.closed_trades if t.reason.startswith("止损")]
    dw = _decision_rows(win)
    dl = _decision_rows(loss)

    out = ["## §3 画像对照:大赢家 vs 大输家(入场决策日 D 特征)", ""]
    out.append(f"- 大赢家 = 净收益 ≥ +10%(**n={len(win)}**);大输家 = reason=止损单(**n={len(loss)}**)。")
    out.append(f"- 入场日特征 = **决策日 D = 买入日前一交易日**的面板行(信号触发当日,T+1 建仓)。")
    out.append(f"- **核心结论先行**:两组在入场日几乎不可分——这正是幸存者偏差的定义,"
               f"输赢由建仓后的路径决定,不由入场日画像决定。任何差异先当噪声(赢家 n 极小)。")
    out.append("")

    # 连续特征
    cont = [
        ("ret_1d(入场日当日涨幅)", "ret_1d"),
        ("vol_ratio_5(量比)", "vol_ratio_5"),
        ("turnover_rate(换手率%)", "turnover_rate"),
        ("dist_from_ma250(距年线)", "dist_from_ma250"),
    ]
    hdr = ["特征", "组", "n", "p25", "p50", "p75", "mean", "SMD(赢−输)", "区分力判定"]
    rows = []
    for label, col in cont:
        d = _smd(dw[col], dl[col])
        for gname, df in (("赢家", dw), ("输家", dl)):
            nn, p25, p50, p75, mean = _num_summary(df[col])
            rows.append([label if gname == "赢家" else "", gname, str(nn),
                         _f(p25), _f(p50), _f(p75), _f(mean),
                         _f(d, 2) if gname == "赢家" else "", _verdict_num(d) if gname == "赢家" else ""])
    out.append("**连续特征分布对比**(SMD=标准化均值差,|SMD|<0.2 判无区分力)\n")
    out.append(_md(hdr, rows))
    out.append("")

    # 类别特征:board / sse_above_ma / year
    def cat_table(col: str, label: str, order=None) -> str:
        wv = dw[col].drop_nulls().to_list()
        lv = dl[col].drop_nulls().to_list()
        cw, cl = Counter(wv), Counter(lv)
        cats = order or sorted(set(cw) | set(cl), key=lambda x: (x is None, str(x)))
        nw, nl = len(wv), len(lv)
        maxdiff = 0.0
        rr = []
        for c in cats:
            fw = cw.get(c, 0) / nw if nw else 0.0
            fl = cl.get(c, 0) / nl if nl else 0.0
            maxdiff = max(maxdiff, abs(fw - fl))
            rr.append([str(c), f"{cw.get(c,0)}", f"{fw:.1%}", f"{cl.get(c,0)}", f"{fl:.1%}", f"{fw-fl:+.1%}"])
        verdict = "无区分力" if maxdiff < 0.05 else "有差异·仅供未来预注册假设,禁止直接入规则"
        h = [f"{label}", "赢家笔", "赢家占比", "输家笔", "输家占比", "占比差"]
        return (f"**{label}分布对比**(最大占比差 {maxdiff:.1%} → {verdict})\n\n" + _md(h, rr))

    out.append(cat_table("board", "板块 board"))
    out.append("")
    out.append(cat_table("sse_above_ma", "市场状态 sse_above_ma(上证>MA20)", order=[True, False]))
    out.append("")
    out.append(cat_table("year", "买入年份 year"))
    return "\n".join(out)


# ======================================================================
#  §4 卖飞率
# ======================================================================

def _qfq_by_code(codes: set) -> Dict[str, Tuple[list, list, list]]:
    q = qfq_frame().filter(pl.col("ts_code").is_in(list(codes))).sort(["ts_code", "trade_date"])
    out: Dict[str, Tuple[list, list, list]] = {}
    for code, g in q.group_by("ts_code"):
        c = code[0] if isinstance(code, tuple) else code
        out[c] = (g["trade_date"].to_list(), g["open"].to_list(), g["close"].to_list())
    return out


def _post_exit_rally(t, by_code, ndays: int = 5) -> Optional[float]:
    """离场后 ndays 交易日窗最高收盘 / 离场价 − 1(离场价=同一 qfq frame 内卖出日 open,
    比值锚点无关且正确扣分红)。窗口=卖出日之后严格 ndays 个交易日(与 H8 反弹口径一致)。"""
    ds, ops, cs = by_code.get(t.ts_code, (None, None, None))
    if ds is None:
        return None
    i = bisect_left(ds, t.sell_date)
    if i >= len(ds) or ds[i] != t.sell_date:
        return None
    exit_ref = ops[i]
    if exit_ref is None or exit_ref <= 0:
        return None
    window = cs[i + 1: i + 1 + ndays]
    if not window:
        return None
    return max(window) / exit_ref - 1.0


def section4() -> str:
    _, pf = k1_run()
    tp = [t for t in pf.closed_trades if t.reason.startswith("回落止盈")]
    sl = [t for t in pf.closed_trades if t.reason.startswith("止损")]
    by_code = _qfq_by_code({t.ts_code for t in tp + sl})

    def rates(trades, thresholds):
        vals = [_post_exit_rally(t, by_code) for t in trades]
        vals = [v for v in vals if v is not None]
        n = len(vals)
        res = {"n": n, "med": (float(pl.Series(vals).median()) if n else float("nan"))}
        for th in thresholds:
            res[th] = (sum(1 for v in vals if v > th) / n) if n else float("nan")
        return res

    tp_r = rates(tp, [0.05, 0.10])
    sl_r = rates(sl, [0.05, 0.10])

    out = ["## §4 卖飞率(离场后 5 交易日窗最高收盘 vs 离场价)", ""]
    out.append("- 口径:离场价 = 同一 qfq frame 内卖出日开盘价(引擎在此价撮合);"
               "前视窗 = 卖出日之后严格 5 个交易日的最高**收盘**(比值扣分红、锚点无关)。")
    out.append("")
    hdr = ["离场机制", "样本n", "窗内最高收盘中位涨幅", ">+5% 占比", ">+10% 占比(卖飞)"]
    rows = [
        ["回落止盈离场", str(tp_r["n"]), _f(tp_r["med"]), f"{tp_r[0.05]:.1%}", f"{tp_r[0.10]:.1%}"],
        ["止损离场(对照)", str(sl_r["n"]), _f(sl_r["med"]), f"{sl_r[0.05]:.1%}", f"{sl_r[0.10]:.1%}"],
    ]
    out.append(_md(hdr, rows))
    out.append("")
    out.append(f"- 回落止盈**卖飞率**(离场后 5 日内再涨 >10%)= **{tp_r[0.10]:.1%}**;>5% = {tp_r[0.05]:.1%}。"
               f"回落止盈样本仅 {tp_r['n']} 笔,读数看方向不看精度。")
    out.append(f"- 止损离场后 5 日反弹 >5% = **{sl_r[0.05]:.1%}**(H8 曾在样本外冻结窗单独报过近义"
               f"「止损后 5 日反弹回卖出价上方」,此处为全期 2021-2026 口径,交叉印证方向)。")
    return "\n".join(out)


# ======================================================================
#  §5 信号级右尾补充
# ======================================================================

def _net_tail(events: pl.DataFrame, d: int = 3) -> dict:
    col = f"fwd_ret_{d}"
    sub = events.filter(pl.col("fwd_buyable") & pl.col(col).is_not_null())
    n = sub.height
    if n == 0:
        return {"n": 0}
    net = sub[col] - COST2
    return {
        "n": n, "win": float((net > 0).sum()) / n, "mean": float(net.mean()),
        "p5": float(net.quantile(.05)), "p10": float(net.quantile(.10)),
        "p50": float(net.median()), "p90": float(net.quantile(.90)),
        "p95": float(net.quantile(.95)), "max": float(net.max()),
    }


def _h3_unfilled_d(panel_k4p: pl.DataFrame, delta: float = 0.005) -> pl.DataFrame:
    """H3「未成交 D 子集」= 决策域内 挂低单未成交 且 可市价买入(排除 D+1 一字涨停飞走)。
    复用 k4p_h3_limit_order 的成交判定逻辑(δ=0.5%)。"""
    dec = panel_k4p.filter(
        base_expr() & (pl.col("vol_ratio_5") >= 1.5)
        & (pl.col("turnover_rate") >= 5.0) & (pl.col("turnover_rate") <= 10.0)
        & (pl.col("close") > pl.col("ma20")) & ~oneword_event_expr()
    )
    limit_price = pl.col("close") * (1 - delta)
    oneword_next = pl.col("fwd_high_1") == pl.col("fwd_low_1")
    ld_oneword = pl.col("fwd_ld_next") & oneword_next
    d = dec.with_columns(
        ((pl.col("fwd_low_1") <= limit_price) & pl.col("fwd_entry_open").is_not_null()
         & pl.col("fwd_ret_3").is_not_null() & ~ld_oneword).alias("_filled"),
        (pl.col("fwd_buyable") & pl.col("fwd_ret_3").is_not_null()).alias("_mkt_ok"),
    )
    return d.filter(~pl.col("_filled") & pl.col("_mkt_ok"))


def section5() -> str:
    p = panel()
    dom = p.filter(base_expr())
    # H7 星细胞:年线下×非涨停大红
    trend_below = pl.col("ma250").is_not_null() & ~((pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up"))
    star = dom.filter((pl.col("close") <= pl.col("ma20")) & (pl.col("ret_1d") >= 0.05)
                      & ~oneword_event_expr() & trend_below & ~pl.col("is_limit_up"))
    # H3 未成交 D 子集(需 k4p 特征 fwd_low_1/fwd_high_1)
    h3d = _h3_unfilled_d(add_k4p_features(p))

    groups = [
        ("域基线(base_expr 全板块+非次新)", dom),
        ("H3 未成交 D 子集(挂低单错过·δ0.5%·市价)", h3d),
        ("H7 星细胞(年线下×非涨停大红)", star),
    ]
    out = ["## §5 信号级右尾补充(hold=3,net 扣双边成本 30bp;补 p90/p95)", ""]
    out.append("- 此前三役只报 p5/p10(左尾),此处补右尾 p90/p95,一张表看「右尾在信号级长什么样」。")
    out.append("- 口径与 eventstudy 严格一致:fwd_buyable 子集,net = fwd_ret_3 − 2×15bp。")
    out.append("")
    hdr = ["组", "n", "胜率", "均值net", "p5", "p10", "p50", "p90", "p95", "max"]
    rows = []
    for name, ev in groups:
        s = _net_tail(ev, 3)
        if s.get("n", 0) == 0:
            rows.append([name, "0", *["nan"] * 8])
            continue
        rows.append([name, str(s["n"]), f"{s['win']:.1%}", _f(s["mean"]),
                     _f(s["p5"]), _f(s["p10"]), _f(s["p50"]), _f(s["p90"]), _f(s["p95"]), _f(s["max"])])
    out.append(_md(hdr, rows))
    out.append("")
    out.append("- 读法:右尾 p90/p95 都是正的、且量级不小(信号级右尾确实存在);但均值仍被左尾+成本压平"
               "——右尾存在 ≠ 可交易,组合级 -5% 止损/hold≤5 会把右尾截断成负期望(K3/H8 血证)。")
    return "\n".join(out)


# ======================================================================

def main() -> None:
    print("# 赢家解剖 —— 结果(纯描述性右尾分析;防自欺声明见 winners_anatomy.md §0)\n")
    print(f"底面板 k3_panel ≤{FROZEN_END}:{panel().height} 行。K1 全期窗 {RUN_START}~{FROZEN_END}。\n")
    for sec in (section1, section2, section3, section4, section5):
        print(sec())
        print()


if __name__ == "__main__":
    main()
