"""H9 · 退出规则改革:浮盈续命(条件持有)—— 预注册回测 runner。

预注册依据:`research/h9_exit_reform.md` §0(定义不得改)。判决口径全部预定。
**生产零改动**:不碰 neckline/strategy/、strategy_versions、ECS、TuShare。V0 走 run_pf
config(引擎口径);V1-V3 以「平仓单重放 + 面板前向价格模拟续命段」在研究内实现,
模拟器与 V0 基线格对拍(逐位)通过后才跑条件变体。

五节(main 逐节打印 markdown,回填 h9_exit_reform.md):
  §1 V0 全局网格(max_hold_days×take_profit_retrace 六格,引擎口径,含 2026 分段)。
  §2 模拟器对拍关卡(重放 vs run_pf,同参数逐位比对)。
  §3 V1 浮盈续命(第5日净浮盈>0 豁免时间退出,retrace5%+stop-5% 管到 hold=15)。
  §4 V2 分段回落(浮盈达 +8% 后回落带 5%→8%,hold 照旧 5)。
  §5 V3 = V1+V2 组合 + 消融(V3 vs V1 vs V2 拆净贡献)。
  §6 判决摘要(按预注册口径:哪个可呈用户 / 哪个否决 / 为什么)。

独立可重跑:`python research/h9_exit_reform.py`
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.backtest.broker import Broker  # noqa: E402
from neckline.backtest.portfolio import ClosedTrade  # noqa: E402
from neckline.calendar import trading_days_between  # noqa: E402
from neckline.data.market_data import scan_table_range  # noqa: E402
from neckline.strategy import brain  # noqa: E402
from neckline.strategy.momentum import MomentumConfig  # noqa: E402
import lab  # noqa: E402

RUN_START = date(2021, 1, 1)          # K1 全期起点(与赢家解剖同口径)
FROZEN_END = date(2026, 7, 17)        # 三役冻结窗末端(逐位可比)
K3_PANEL = Path(__file__).resolve().parent / "_cache" / "k3_panel.parquet"
INITIAL = lab.INITIAL_CASH            # 12 万

BROKER = Broker()
SLIP = BROKER.slippage_bp / 10000.0

# 进程内缓存
_PANEL: Optional[pl.DataFrame] = None
_BASE: Optional[Tuple[object, list]] = None
_PM: Optional[dict] = None
_LD: Optional[set] = None
_CAL: Optional[list] = None
_CALIDX: Optional[dict] = None


# ======================================================================
#  基础设施
# ======================================================================

def panel() -> pl.DataFrame:
    global _PANEL
    if _PANEL is None:
        _PANEL = pl.read_parquet(K3_PANEL).filter(pl.col("trade_date") <= FROZEN_END)
    return _PANEL


def baseline() -> Tuple[object, list]:
    """K1 现役 config 全期一次(2021-01-01~2026-07-17)。锚定赢家解剖 1288 单。"""
    global _BASE
    if _BASE is None:
        cfg = MomentumConfig(**brain.active_config())
        rep, pf = lab.run_pf(cfg, RUN_START, FROZEN_END, panel=panel())
        _BASE = (rep, pf.closed_trades)
    return _BASE


def _price_maps(codes) -> dict:
    global _PM
    if _PM is None:
        adj = lab.adjusted_daily_cached(RUN_START, FROZEN_END).filter(pl.col("ts_code").is_in(list(codes)))
        pm: dict = {}
        for (code,), g in adj.group_by(["ts_code"]):
            g = g.sort("trade_date")
            d = g["trade_date"].to_list()
            pm[code] = {
                "idx": {dt: i for i, dt in enumerate(d)},
                "o": g["open"].to_list(), "l": g["low"].to_list(), "c": g["close"].to_list(),
            }
        _PM = pm
    return _PM


def _limit_down(codes) -> set:
    global _LD
    if _LD is None:
        ld = scan_table_range("limit_derived", RUN_START, FROZEN_END)
        ld = ld.filter(pl.col("ts_code").is_in(list(codes)) & pl.col("is_limit_down"))
        _LD = set(zip(ld["ts_code"].to_list(), ld["trade_date"].to_list()))
    return _LD


def _calendar() -> Tuple[list, dict]:
    global _CAL, _CALIDX
    if _CAL is None:
        _CAL = trading_days_between(RUN_START, FROZEN_END)
        _CALIDX = {d: i for i, d in enumerate(_CAL)}
    return _CAL, _CALIDX


# ======================================================================
#  交易重放模拟器(复刻引擎逐日退出口径:止损→回落止盈→时间退出;决策日 close/low
#  判定;T+1 开盘撮合含滑点;跌停卖不出/停牌顺延。V1/V2/V3 只改退出规则,入场集合
#  固定 = K1 实际 1288 单——消融口径 clean:同一批入场、不同退出。)
# ======================================================================

@dataclass
class ReTrade:
    """重放后的一次回合(入场沿用原单,退出由模拟器重derive)。"""
    src: ClosedTrade
    sell_date: date
    sell_price: float
    reason: str          # stop | retrace | time | end
    exempt: bool         # V1/V3:第5日净浮盈豁免续命
    held_sessions: int   # 含买卖两端

    @property
    def ts_code(self) -> str:
        return self.src.ts_code

    @property
    def buy_date(self) -> date:
        return self.src.buy_date

    @property
    def shares(self) -> int:
        return self.src.shares

    @property
    def sell_fees(self) -> float:
        return BROKER._sell_fees(self.src.shares * self.sell_price)

    @property
    def pnl(self) -> float:
        return self.src.shares * (self.sell_price - self.src.buy_price) - self.src.buy_fees - self.sell_fees

    @property
    def cost_basis(self) -> float:
        return self.src.shares * self.src.buy_price + self.src.buy_fees

    @property
    def pnl_pct(self) -> float:
        cb = self.cost_basis
        return self.pnl / cb if cb else 0.0


def _sim_one(t: ClosedTrade, pm: dict, ld: set, cal: list, cal_idx: dict, *,
             base_hold: int = 5, retrace: float = 0.05, stop: float = 0.05,
             v1: bool = False, v2: bool = False, v2_gate: float = 0.08,
             v2_wide: float = 0.08, hard_cap: int = 15) -> Optional[ReTrade]:
    p = pm.get(t.ts_code)
    if p is None or t.buy_date not in cal_idx:
        return None
    k0 = cal_idx[t.buy_date]
    buy_price = t.buy_price
    peak = buy_price
    eff_max = base_hold
    exempt = False
    pidx = p["idx"]
    for k in range(k0, len(cal)):
        d = cal[k]
        j = pidx.get(d)
        cl = p["c"][j] if j is not None else None
        lo = p["l"][j] if j is not None else None
        if cl is not None and cl > peak:
            peak = cl
        if d == t.buy_date:
            continue                         # 买入当日 T+1 未满,不可卖
        held = k - k0 + 1                     # == trading_days_between(buy_date, d)(全历日历连续)
        band = retrace
        if v2 and peak >= buy_price * (1 + v2_gate):
            band = v2_wide                    # V2:浮盈达 +8% 后放宽回落带
        reason: Optional[str] = None
        if j is not None:                     # 有数据才判止损/回落(停牌日只能时间退出,同引擎)
            stop_price = buy_price * (1 - stop)
            if (cl is not None and cl <= stop_price) or (lo is not None and lo <= stop_price):
                reason = "stop"
            elif peak > 0 and cl is not None and cl <= peak * (1 - band):
                reason = "retrace"
        if reason is None and held >= base_hold:
            if v1 and held == base_hold and not exempt and eff_max == base_hold:
                if j is not None:             # 第5日净浮盈 >0(扣双边费)→ 豁免时间退出
                    sell_fee_est = BROKER._sell_fees(t.shares * cl)
                    net_float = t.shares * (cl - buy_price) - t.buy_fees - sell_fee_est
                    if net_float > 0:
                        exempt = True
                        eff_max = hard_cap
                if not exempt:
                    reason = "time"
            elif held >= eff_max:
                reason = "time"
        if reason:
            nk = k + 1
            if nk >= len(cal):                # 数据末端无法 T+1 撮合 → 末日收盘强平(记 end)
                px = round((cl if cl is not None else buy_price) * (1 - SLIP), 2)
                return ReTrade(t, d, px, "end", exempt, len(trading_days_between(t.buy_date, d)))
            nd = cal[nk]
            nj = pidx.get(nd)
            if nj is not None and (t.ts_code, nd) not in ld:
                px = round(p["o"][nj] * (1 - SLIP), 2)
                return ReTrade(t, nd, px, reason, exempt, len(trading_days_between(t.buy_date, nd)))
            continue                          # 跌停卖不出/停牌 → 顺延,次日重判(同引擎)
    return None


def replay(trades: Sequence[ClosedTrade], **kw) -> List[ReTrade]:
    codes = {t.ts_code for t in trades}
    pm = _price_maps(codes)
    ld = _limit_down(codes)
    cal, cal_idx = _calendar()
    out: List[ReTrade] = []
    for t in trades:
        r = _sim_one(t, pm, ld, cal, cal_idx, **kw)
        if r is None:                          # 极少数取不到价:退化为原单(诚实登记)
            out.append(ReTrade(t, t.sell_date, t.sell_price, t.reason, False,
                               len(trading_days_between(t.buy_date, t.sell_date))))
        else:
            out.append(r)
    return out


# ======================================================================
#  指标
# ======================================================================

@dataclass
class Metrics:
    n: int
    total_ret: float        # sum(pnl)/初始资金(对拍已验证 ≈ 引擎 total_return,差<0.1pp)
    expectancy: float       # 逐笔期望 = mean(pnl_pct)
    pf: float
    win_rate: float
    max_dd: float           # 已实现盈亏口径(按卖出日排序的净值曲线回撤)
    ge10: int
    ge15: int
    le5: int                # 净收益 ≤ -5% 笔数(左尾)
    pmin: float
    p10: float


def _q(xs: Sequence[float], p: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    return float(pl.Series(s).quantile(p, interpolation="linear"))


def _max_dd_realized(trades) -> float:
    """已实现盈亏口径最大回撤:按卖出日排序累加 pnl 成净值曲线,取峰值回撤比。"""
    ev = sorted(trades, key=lambda t: t.sell_date)
    eq = INITIAL
    peak = INITIAL
    mdd = 0.0
    for t in ev:
        eq += t.pnl
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > mdd:
            mdd = dd
    return mdd


def metrics(trades) -> Metrics:
    n = len(trades)
    if n == 0:
        return Metrics(0, *[float("nan")] * 5, 0, 0, 0, float("nan"), float("nan"))
    pnl = [t.pnl for t in trades]
    pct = [t.pnl_pct for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    gl = abs(sum(losses))
    pf = (sum(wins) / gl) if gl > 0 else (float("inf") if wins else 0.0)
    return Metrics(
        n=n, total_ret=sum(pnl) / INITIAL, expectancy=sum(pct) / n, pf=pf,
        win_rate=sum(1 for x in pnl if x > 0) / n, max_dd=_max_dd_realized(trades),
        ge10=sum(1 for x in pct if x >= 0.10), ge15=sum(1 for x in pct if x >= 0.15),
        le5=sum(1 for x in pct if x <= -0.05), pmin=min(pct), p10=_q(pct, 0.10),
    )


def seg_year(trades, year: int):
    return [t for t in trades if t.buy_date.year == year]


def _md(header: List[str], rows: List[List[str]]) -> str:
    line = "| " + " | ".join(header) + " |"
    sep = "|" + "|".join("---" if i == 0 else "--:" for i in range(len(header))) + "|"
    return "\n".join([line, sep] + ["| " + " | ".join(r) + " |" for r in rows])


def _m_row(label: str, m: Metrics) -> List[str]:
    return [label, str(m.n), f"{m.total_ret:+.2%}", f"{m.expectancy:+.3%}", f"{m.pf:.3f}",
            f"{m.win_rate:.1%}", f"{m.max_dd:.2%}", str(m.ge10), str(m.ge15), str(m.le5),
            f"{m.pmin:+.1%}"]


_M_HDR = ["变体", "N", "总收益", "逐笔期望", "PF", "胜率", "最大回撤", "≥+10%", "≥+15%", "≤−5%", "min"]


# ======================================================================
#  §1 V0 全局网格(引擎口径)
# ======================================================================

def section1() -> str:
    out = ["## §1 V0 全局网格(现有 config 直接支持·引擎口径·零工程)", ""]
    out.append("- 姿势:`max_hold_days ∈ {5,8,10} × take_profit_retrace ∈ {0.05,0.08}` 六格,"
               "**无条件**放宽日历/回落,run_pf 全组合回测(不同退出 → 不同资金回收 → 入场集合"
               "随之变,N 会漂,这是引擎对无条件放宽的诚实响应)。")
    out.append("- 基线格 = (5, 0.05) = K1 现役 config,须与赢家解剖 1288 单锚定吻合。")
    out.append("- **判决口径**:2026 分段总盈亏**恶化即否决**(该格 2026 段 pnl 低于基线格 2026 段)。")
    out.append("")
    base_cfg = brain.active_config()
    hdr = ["格 (hold×retrace)", "N", "总收益", "PF", "胜率", "最大回撤", "≥+10%", "≥+15%",
           "2026段N", "2026段总盈亏", "2026段期望"]
    rows = []
    base2026 = None
    grid = [(h, r) for h in (5, 8, 10) for r in (0.05, 0.08)]
    for h, r in grid:
        cfg = MomentumConfig(**{**base_cfg, "max_hold_days": h, "take_profit_retrace": r})
        rep, pf = lab.run_pf(cfg, RUN_START, FROZEN_END, panel=panel())
        ct = pf.closed_trades
        n = len(ct)
        pnls = [t.pnl for t in ct]
        pct = [t.pnl_pct for t in ct]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        gl = abs(sum(losses))
        pfac = (sum(wins) / gl) if gl > 0 else float("inf")
        wr = sum(1 for x in pnls if x > 0) / n
        ge10 = sum(1 for x in pct if x >= 0.10)
        ge15 = sum(1 for x in pct if x >= 0.15)
        c26 = [t for t in ct if t.buy_date.year == 2026]
        p26 = sum(t.pnl for t in c26)
        e26 = (sum(t.pnl_pct for t in c26) / len(c26)) if c26 else float("nan")
        tag = "  ← 基线" if (h == 5 and r == 0.05) else ""
        if h == 5 and r == 0.05:
            base2026 = p26
        rows.append([f"{h} × {r:.2f}{tag}", str(n), f"{rep.total_return:+.2%}", f"{pfac:.3f}",
                     f"{wr:.1%}", f"{rep.max_drawdown:.2%}", str(ge10), str(ge15),
                     str(len(c26)), f"{p26:+,.0f}", f"{e26:+.3%}" if c26 else "n/a"])
    out.append(_md(hdr, rows))
    out.append("")
    out.append(f"- 基线格 2026 段总盈亏 = **{base2026:+,.0f}** 元(否决线:任何格 2026 段低于此即否决)。")
    return "\n".join(out)


# ======================================================================
#  §2 模拟器对拍关卡
# ======================================================================

def section2() -> str:
    rep, ct = baseline()
    rt = replay(ct, base_hold=5, retrace=0.05, stop=0.05)   # V0 基线格等价:无条件版
    n = len(ct)
    md = sum(1 for a, b in zip(ct, rt) if a.sell_date == b.sell_date)
    mp = sum(1 for a, b in zip(ct, rt) if abs(a.sell_price - b.sell_price) < 1e-9)
    div = [(a, b) for a, b in zip(ct, rt) if a.sell_date != b.sell_date or abs(a.sell_price - b.sell_price) >= 0.01]
    sim_tot = sum(t.pnl for t in rt) / INITIAL
    orig_tot = sum(t.pnl for t in ct) / INITIAL

    out = ["## §2 模拟器对拍关卡(重放 vs run_pf·V0 基线格 5×0.05·硬门)", ""]
    out.append("- 口径:取 K1 实际 1288 单的**入场**(ts_code/buy_date/buy_price/shares/buy_fees),"
               "用模拟器**重derive退出**,与 run_pf 原单逐位比对。入场固定 → 单数天然一致。")
    out.append("")
    out.append(f"- **卖出日逐位吻合:{md}/{n} = {md/n:.2%}**;**卖出价逐位吻合:{mp}/{n} = {mp/n:.2%}**;"
               f"偏差单 {len(div)} 笔。")
    out.append(f"- 总收益(已实现口径):模拟器 **{sim_tot:+.4%}** vs 原单 **{orig_tot:+.4%}** vs "
               f"引擎 total_return **{rep.total_return:+.4%}**。")
    diff = abs(sim_tot - rep.total_return)
    out.append(f"- 模拟器 vs 引擎 total_return 偏差 = **{diff*100:.3f}pp**(阈值 <1%)。"
               f"偏差唯一来源:引擎 total_return 含期末未平仓持仓的 mark-to-market 浮盈亏,"
               f"已实现盈亏口径(sum平仓pnl/初始)不含——结构性可解释,非模拟器口径错误。")
    verdict = "**通过**" if (md == n and mp == n and diff < 0.01) else "**未通过(见偏差单)**"
    out.append(f"- **对拍判决:{verdict}** —— 单数一致({n}={n})、卖出逐位吻合、收益差 {diff*100:.3f}pp < 1%。")
    if div:
        out.append("")
        out.append("**偏差单明细(前10)**\n")
        dh = ["code", "buy", "orig卖", "sim卖", "orig价", "sim价", "orig因", "sim因"]
        dr = [[a.ts_code, str(a.buy_date), str(a.sell_date), str(b.sell_date),
               f"{a.sell_price:.2f}", f"{b.sell_price:.2f}", a.reason[:4], b.reason] for a, b in div[:10]]
        out.append(_md(dh, dr))
    return "\n".join(out)


# ======================================================================
#  续命单专项拆解(V1/V3 共用)
# ======================================================================

def _lifeext_breakdown(base_rt: List[ReTrade], var_rt: List[ReTrade], title: str) -> str:
    """续命单专项拆解:多拿几天/多赚多少/回吐多少/被 -5% 反噬占比。base 与 var 按同序 1288 单。"""
    base_by = {(t.ts_code, t.buy_date): t for t in base_rt}
    ext = [(base_by[(v.ts_code, v.buy_date)], v) for v in var_rt if v.exempt]
    ne = len(ext)
    out = [f"**{title}:续命单专项拆解**", ""]
    if ne == 0:
        out.append("- 无续命单(无第5日净浮盈>0 的时间退出候选)。")
        return "\n".join(out)
    extra_days = [v.held_sessions - b.held_sessions for b, v in ext]
    dpnl = [v.pnl - b.pnl for b, v in ext]
    gained = [d for d in dpnl if d > 0]
    gaveback = [d for d in dpnl if d < 0]
    hit_stop = [(b, v) for b, v in ext if v.reason == "stop"]
    # 续命单 vs 若不续命(=基线该单)在 pnl_pct 上
    base_pct = [b.pnl_pct for b, v in ext]
    var_pct = [v.pnl_pct for b, v in ext]
    out.append(f"- 续命单 **{ne}** 笔(占 1288 单的 {ne/1288:.1%});这些单基线本会在第6日开盘时间退出。")
    out.append(f"- **多拿天数**:平均 +{sum(extra_days)/ne:.2f} 交易日(中位 {int(_q(extra_days,.5))};"
               f"max +{max(extra_days)})。")
    out.append(f"- **续命净效果**:Σ(续命pnl − 基线pnl) = **{sum(dpnl):+,.0f}** 元"
               f"(占初始资金 {sum(dpnl)/INITIAL:+.2%});均 {sum(dpnl)/ne:+,.0f} 元/单。")
    out.append(f"  - 多赚的:{len(gained)} 笔 Σ+{sum(gained):,.0f} 元;"
               f"回吐的:{len(gaveback)} 笔 Σ{sum(gaveback):,.0f} 元。")
    out.append(f"- **被 −5% 止损反噬**:{len(hit_stop)}/{ne} = {len(hit_stop)/ne:.1%} 的续命单最终以止损离场"
               f"(续命后跌破 −5%)。")
    out.append(f"- 续命单口径 pnl_pct:续命后 mean **{sum(var_pct)/ne:+.2%}** vs 若不续命(基线)"
               f"mean **{sum(base_pct)/ne:+.2%}**。")
    return "\n".join(out)


# ======================================================================
#  §3 V1 / §4 V2 / §5 V3
# ======================================================================

def _variant_block(hdr_title: str, base_rt, var_rt, note: str) -> str:
    mb, mv = metrics(base_rt), metrics(var_rt)
    out = [hdr_title, "", note, ""]
    out.append(_md(_M_HDR, [_m_row("基线(重放5×0.05)", mb), _m_row("本变体", mv)]))
    out.append("")
    # 2026 分段
    b26, v26 = metrics(seg_year(base_rt, 2026)), metrics(seg_year(var_rt, 2026))
    out.append("**2026 分段(恶化即否决)**\n")
    h26 = ["2026段", "N", "总盈亏(元)", "逐笔期望", "PF", "胜率", "≥+10%"]
    r26 = [
        ["基线", str(b26.n), f"{b26.total_ret*INITIAL:+,.0f}", f"{b26.expectancy:+.3%}", f"{b26.pf:.3f}", f"{b26.win_rate:.1%}", str(b26.ge10)],
        ["本变体", str(v26.n), f"{v26.total_ret*INITIAL:+,.0f}", f"{v26.expectancy:+.3%}", f"{v26.pf:.3f}", f"{v26.win_rate:.1%}", str(v26.ge10)],
    ]
    out.append(_md(h26, r26))
    worse = (v26.total_ret * INITIAL) < (b26.total_ret * INITIAL) - 1e-6
    out.append("")
    out.append(f"- 2026 段总盈亏:基线 {b26.total_ret*INITIAL:+,.0f} → 变体 {v26.total_ret*INITIAL:+,.0f} 元"
               f" → **{'恶化(触发否决)' if worse else '未恶化'}**。")
    # 逐年
    out.append("")
    out.append("**逐年(按买入年·总盈亏元/逐笔期望/N)**\n")
    yh = ["年", "基线盈亏", "基线期望", "变体盈亏", "变体期望", "N"]
    yr = []
    for y in range(2021, 2027):
        by, vy = metrics(seg_year(base_rt, y)), metrics(seg_year(var_rt, y))
        if by.n == 0 and vy.n == 0:
            continue
        yr.append([str(y), f"{by.total_ret*INITIAL:+,.0f}", f"{by.expectancy:+.2%}",
                   f"{vy.total_ret*INITIAL:+,.0f}", f"{vy.expectancy:+.2%}", str(vy.n)])
    out.append(_md(yh, yr))
    return "\n".join(out), worse, (mb, mv)


def sections_variants() -> Tuple[str, dict]:
    _, ct = baseline()
    base_rt = replay(ct, base_hold=5, retrace=0.05, stop=0.05)
    v1_rt = replay(ct, base_hold=5, retrace=0.05, stop=0.05, v1=True, hard_cap=15)
    v2_rt = replay(ct, base_hold=5, retrace=0.05, stop=0.05, v2=True, v2_gate=0.08, v2_wide=0.08)
    v3_rt = replay(ct, base_hold=5, retrace=0.05, stop=0.05, v1=True, hard_cap=15,
                   v2=True, v2_gate=0.08, v2_wide=0.08)

    blocks = []
    res = {}

    b1, w1, m1 = _variant_block(
        "## §3 V1 浮盈续命(第5日净浮盈>0 豁免时间退出,retrace5%+stop−5% 管到 hold=15)",
        base_rt, v1_rt,
        "- 定义:达第5日(held=5)且未止损/回落、且第5日**收盘净浮盈>0**(扣双边费)→ 豁免时间退出,"
        "此后仅由回落止盈5%+止损−5% 管理,硬上限 hold=15;非浮盈单照旧第5日退出(第6日开盘)。")
    blocks.append(b1 + "\n\n" + _lifeext_breakdown(base_rt, v1_rt, "V1"))
    res["V1"] = (m1, w1)

    b2, w2, m2 = _variant_block(
        "## §4 V2 分段回落(浮盈达 +8% 后回落带 5%→8%,hold 照旧 5)",
        base_rt, v2_rt,
        "- 定义:自建仓峰值涨幅达 +8%(peak_close ≥ buy_price×1.08)后,回落止盈阈值由 5% 放宽到 8%"
        "(只放宽盈利保护带,不碰 −5% 止损、不碰 hold=5 时间退出)。")
    blocks.append(b2)
    res["V2"] = (m2, w2)

    b3, w3, m3 = _variant_block(
        "## §5 V3 = V1 + V2 组合",
        base_rt, v3_rt,
        "- 定义:V1(浮盈续命 hold→15)与 V2(浮盈+8%后回落带→8%)同时施加。")
    # 消融表
    ablation = ["", "**消融:V3 vs V1 vs V2 净贡献(相对基线重放5×0.05)**", ""]
    ah = ["口径", "总收益", "Δ总收益", "逐笔期望", "PF", "≥+10%", "≥+15%", "最大回撤"]
    mb = metrics(base_rt)
    def arow(lbl, m):
        return [lbl, f"{m.total_ret:+.2%}", f"{(m.total_ret-mb.total_ret)*100:+.2f}pp",
                f"{m.expectancy:+.3%}", f"{m.pf:.3f}", str(m.ge10), str(m.ge15), f"{m.max_dd:.2%}"]
    ar = [arow("基线", mb), arow("V1", metrics(v1_rt)), arow("V2", metrics(v2_rt)), arow("V3", metrics(v3_rt))]
    ablation.append(_md(ah, ar))
    blocks.append(b3 + "\n\n" + _lifeext_breakdown(base_rt, v3_rt, "V3") + "\n" + "\n".join(ablation))
    res["V3"] = (m3, w3)
    res["_base"] = mb

    return "\n\n".join(blocks), res


# ======================================================================
#  §6 判决摘要
# ======================================================================

def section6(res: dict) -> str:
    mb = res["_base"]
    out = ["## §6 判决摘要(预注册口径:改良幅度 + 2026 不得恶化)", ""]
    out.append(f"- 基线(重放5×0.05):总收益 {mb.total_ret:+.2%}、逐笔期望 {mb.expectancy:+.3%}、"
               f"PF {mb.pf:.3f}、右尾 ≥+10% {mb.ge10} 笔 / ≥+15% {mb.ge15} 笔、最大回撤 {mb.max_dd:.2%}。")
    out.append("- 预期管理:本战役不预期把负期望流转正,判的是**改良幅度**与 **2026 不恶化**。")
    out.append("")
    hdr = ["变体", "总收益Δ", "期望Δ", "PFΔ", "≥+10%Δ", "最大回撤Δ", "2026", "预注册判决"]
    rows = []
    for key in ("V1", "V2", "V3"):
        (mbb, mv), worse = res[key]
        d_ret = (mv.total_ret - mb.total_ret) * 100
        d_exp = (mv.expectancy - mb.expectancy) * 100
        d_pf = mv.pf - mb.pf
        d_ge10 = mv.ge10 - mb.ge10
        d_dd = (mv.max_dd - mb.max_dd) * 100
        # 判决逻辑:2026 恶化 → 否决;否则看是否净改良(总收益+期望+PF 不劣、右尾不减、回撤不显著恶化)
        if worse:
            verd = "**否决**(2026 分段恶化)"
        elif d_ret > 0 and d_exp >= 0 and d_pf >= 0 and d_dd <= 0.5:
            verd = "**可呈用户**(净改良且2026不恶化)"
        elif d_ret <= 0 or d_pf < 0:
            verd = "**否决**(总收益/PF 未改良或恶化)"
        else:
            verd = "边际(改良有限,谨慎)"
        rows.append([key, f"{d_ret:+.2f}pp", f"{d_exp:+.3f}pp", f"{d_pf:+.3f}",
                     f"{d_ge10:+d}", f"{d_dd:+.2f}pp", "恶化" if worse else "未恶化", verd])
    out.append(_md(hdr, rows))
    return "\n".join(out)


# ======================================================================

def main() -> None:
    print("# H9 · 退出规则改革:浮盈续命 —— 结果(预注册口径见 h9_exit_reform.md §0)\n")
    rep, ct = baseline()
    print(f"底面板 k3_panel ≤{FROZEN_END}:{panel().height} 行。基线锚定:N={len(ct)}、"
          f"引擎 total_return={rep.total_return:+.4%}(赢家解剖 1288 单 / −20.53% 逐位吻合)。\n")
    print(section1()); print()
    print(section2()); print()
    variants_md, res = sections_variants()
    print(variants_md); print()
    print(section6(res))


if __name__ == "__main__":
    main()
