"""K3 · B2 组合级回测 + 消融 + walk-forward + 2026 生存测试 + 止损交互 + 臂④做局诊断。

四臂(预注册见 k3_report「⏸→▶ B1 用户检查点纪要 + B2 预注册」,commit 64e77ee):
  臂① 降势超卖(留档)  C4=ret_5d≤-0.10&降势 / C2=ret_1d≤-0.05&降势
  臂② A6 20日深跌20    ret_20d≤-0.20(不分趋势)
  臂③ 升势回撤+启动确认(用户偏好,新)  升势 & dist_high≤-0.08 & {收复MA5/MA10×量能/缩量止跌}
  臂④ 量化做局诊断(非策略臂)  降势票突现放量大阳/涨停 → 分年 fwd 期望

全纪律读现役 K1 config 口径(stop/take_profit/hold/仓位不硬编),四臂质量域用现有字段
forbid_high_elasticity=True(MAIN only)+forbid_new_days=120(非次新)+strength=none 表达。
对手 K1(K1 真实 config 在同一 K3 面板 ≤07-17 逐行等价 panel_full,可比)。
★2026 段生存测试:任何臂 2026 分段为负即一票否决(用户裁断②)。

运行:python research/k3_b2_portfolio.py [section]
  section ∈ {arm3_es, arm4, portfolio, strat, ablation, wf, stoploss, sens, all}(默认 all)
"""

from __future__ import annotations

import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from k3_panel import build_k3_panel  # noqa: E402
from neckline.research import eventstudy as es  # noqa: E402
from neckline.research.panel import base_universe_expr  # noqa: E402
from neckline.strategy import brain, signals as S  # noqa: E402
from neckline.strategy.momentum import MomentumConfig  # noqa: E402
from neckline.backtest.walk_forward import generate_walk_forward_windows  # noqa: E402
from research import lab  # noqa: E402

# —— 窗口 ——
IN = (date(2020, 1, 1), date(2024, 12, 31))
OUT = (date(2025, 1, 1), date(2026, 7, 17))       # 样本外冻结(主判,与 K1 严格可比)
EXT = (date(2025, 1, 1), date(2026, 7, 24))       # 延至最新补充
Y2026 = (date(2026, 1, 1), date(2026, 7, 17))     # ★2026 段生存测试(硬门禁)
COST2 = 2 * es.DEFAULT_COST_ONESIDE

_PANEL: Optional[pl.DataFrame] = None


def panel() -> pl.DataFrame:
    global _PANEL
    if _PANEL is None:
        _PANEL = build_k3_panel()
    return _PANEL


# ======================================================================
#  configs(全部从现役 K1 派生 → 卖出/仓位读现役口径,不硬编)
# ======================================================================
def k1_cfg() -> MomentumConfig:
    return MomentumConfig(**brain.active_config())


def _arm(**over) -> MomentumConfig:
    """K3 超跌臂:继承 K1 卖出/仓位口径,买点换 oversold + 非次新质量门。"""
    base = brain.active_config()
    return MomentumConfig(**{**base, "buypoint": "oversold", "forbid_new_days": 120, **over})


def arm_configs() -> Dict[str, MomentumConfig]:
    return {
        "K1(现役对手)": k1_cfg(),
        "臂①C4 5日跌×降势": _arm(oversold_depth_col="ret_5d", oversold_depth_max=-0.10, oversold_trend="down"),
        "臂①C2 急跌×降势": _arm(oversold_depth_col="ret_1d", oversold_depth_max=-0.05, oversold_trend="down"),
        "臂②A6 20日深跌20": _arm(oversold_depth_col="ret_20d", oversold_depth_max=-0.20),
        # 臂③ 由事件研究先筛后确定;此处放全网格供组合回测(portfolio 只跑先筛幸存 + 对照基线)
        "臂③升势回撤直接买(对照)": _arm(oversold_trend="up", oversold_pullback_max=-0.08),
        "臂③收复MA5×1.5": _arm(oversold_trend="up", oversold_pullback_max=-0.08,
                               oversold_confirm="reclaim_ma5", oversold_confirm_vol=1.5),
        "臂③收复MA10×1.5": _arm(oversold_trend="up", oversold_pullback_max=-0.08,
                                oversold_confirm="reclaim_ma10", oversold_confirm_vol=1.5),
        "臂③缩量止跌企稳": _arm(oversold_trend="up", oversold_pullback_max=-0.08,
                              oversold_confirm="stabilize", oversold_vol_max=0.8),
    }


# ======================================================================
#  渲染
# ======================================================================
def _f(x, p=4):
    if x is None or (isinstance(x, float) and x != x):
        return "nan"
    if isinstance(x, float) and abs(x) > 1e6:
        return "inf"
    return f"{x:.{p}f}"


def _md(header: List[str], rows: List[List[str]]) -> str:
    line = "| " + " | ".join(header) + " |"
    sep = "|" + "|".join("---" if i == 0 else "--:" for i in range(len(header))) + "|"
    return "\n".join([line, sep] + ["| " + " | ".join(r) + " |" for r in rows])


# ======================================================================
#  臂③ 事件研究先筛(便宜档,沿 B1 方法;信号级 net 期望 + 左尾 p10)
# ======================================================================
def _k3_base() -> pl.Expr:
    return base_universe_expr() & (pl.col("board") == "MAIN") & (pl.col("days_since_listing") >= 120)


def _es_stats(sig: pl.DataFrame, d: int) -> dict:
    col = f"fwd_ret_{d}"
    sub = sig.filter(pl.col("fwd_buyable") & pl.col(col).is_not_null())
    n = sub.height
    if n == 0:
        return {"n": 0, "win": float("nan"), "mean": float("nan"), "pf": float("nan"), "p10": float("nan")}
    net = sub[col] - COST2
    wins, losses = net.filter(net > 0), net.filter(net < 0)
    gp = float(wins.sum()) if wins.len() else 0.0
    gl = abs(float(losses.sum())) if losses.len() else 0.0
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    return {"n": n, "win": float((net > 0).sum()) / n, "mean": float(net.mean()),
            "pf": pf, "p10": float(net.quantile(0.10))}


def _win(p: pl.DataFrame, w) -> pl.DataFrame:
    a, b = w
    return p.filter((pl.col("trade_date") >= a) & (pl.col("trade_date") <= b))


def arm3_grid() -> Dict[str, pl.Expr]:
    b = _k3_base()
    up = (pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up")
    pull = pl.col("dist_from_high_20d") <= -0.08
    base0 = b & up & pull
    yang = pl.col("ret_1d") > 0
    r5 = pl.col("close") >= pl.col("ma5")
    r10 = pl.col("close") >= pl.col("ma10")
    vr = pl.col("vol_ratio_5")
    return {
        "升势回撤直接买(对照·无确认)": base0,
        "收复MA5×1.5": base0 & yang & r5 & (vr >= 1.5),
        "收复MA5×2.0": base0 & yang & r5 & (vr >= 2.0),
        "收复MA10×1.5": base0 & yang & r10 & (vr >= 1.5),
        "收复MA10×2.0": base0 & yang & r10 & (vr >= 2.0),
        "缩量止跌企稳(≤0.8)": base0 & (pl.col("consec_down_days") == 0) & (vr <= 0.8),
    }


def arm3_eventstudy() -> str:
    p = panel()
    grid = arm3_grid()
    out = ["**臂③ 事件研究先筛(便宜档;信号级 net 期望 + 左尾 p10;样本内 vs 样本外冻结)**", "",
           "> 对照基线 = 升势回撤直接买(≈B1 的 C1 加回撤门槛)。逐格看「等确认」是否救活升势回撤。", ""]
    hdr = ["确认构型", "in n", "in mean3", "in pf3", "out n", "out mean1", "out mean3", "out mean5",
           "out pf3", "out win3", "out p10·3"]
    rows = []
    for name, expr in grid.items():
        si = _win(p, IN).filter(expr)
        so = _win(p, OUT).filter(expr)
        ii = _es_stats(si, 3)
        o1, o3, o5 = _es_stats(so, 1), _es_stats(so, 3), _es_stats(so, 5)
        rows.append([name, str(ii["n"]), _f(ii["mean"]), _f(ii["pf"], 3), str(o3["n"]),
                     _f(o1["mean"]), _f(o3["mean"]), _f(o5["mean"]), _f(o3["pf"], 3),
                     _f(o3["win"], 3), _f(o3["p10"], 3)])
    out.append(_md(hdr, rows))
    return "\n".join(out)


# ======================================================================
#  臂④ 量化做局诊断(降势票突现放量大阳/涨停 → 分年 fwd 期望)
# ======================================================================
def arm4_diagnosis() -> str:
    p = panel()
    b = _k3_base()
    down_ma = pl.col("close") < pl.col("ma250")         # 年线下(降势)
    down_20 = pl.col("ret_20d") <= -0.20                # 20 日深跌
    bigyang = (pl.col("ret_1d") >= 0.05) & (pl.col("vol_ratio_5") >= 2.0)
    limitup = pl.col("is_limit_up")
    events = {
        "年线下·放量大阳(≥5%×2倍)": b & down_ma & bigyang,
        "年线下·涨停": b & down_ma & limitup,
        "20日跌20·放量大阳": b & down_20 & bigyang,
        "20日跌20·涨停": b & down_20 & limitup,
    }
    out = ["**臂④ 量化做局诊断(非策略臂):降势票突现放量大阳/涨停 → 事后 1-5 日 net 期望**", "",
           "> 「诱多做局」印象量化。分年报告(重点看 2024/2025 vs 2026)。net 扣双边成本。", ""]
    for name, expr in events.items():
        sig = panel().filter(expr)
        out.append(f"\n_{name}_(全期 n={sig.filter(pl.col('fwd_buyable')).height})\n")
        hdr = ["year", "n", "mean1", "mean3", "mean5", "pf3", "win3", "p10·3"]
        rows = []
        yrs = sorted([y for y in sig.select("year").unique()["year"].to_list() if y is not None])
        for y in yrs:
            ys = sig.filter(pl.col("year") == y)
            s1, s3, s5 = _es_stats(ys, 1), _es_stats(ys, 3), _es_stats(ys, 5)
            if s3["n"] == 0:
                continue
            rows.append([str(y), str(s3["n"]), _f(s1["mean"]), _f(s3["mean"]), _f(s5["mean"]),
                         _f(s3["pf"], 3), _f(s3["win"], 3), _f(s3["p10"], 3)])
        # 全期汇总行
        a1, a3, a5 = _es_stats(sig, 1), _es_stats(sig, 3), _es_stats(sig, 5)
        rows.append(["**全期**", str(a3["n"]), _f(a1["mean"]), _f(a3["mean"]), _f(a5["mean"]),
                     _f(a3["pf"], 3), _f(a3["win"], 3), _f(a3["p10"], 3)])
        out.append(_md(hdr, rows))
    return "\n".join(out)


# ======================================================================
#  组合级回测:每 (config,window) 跑一次,缓存 (rep,pf)
# ======================================================================
_PF_CACHE: Dict[Tuple[str, tuple], Tuple[object, object]] = {}


def _pf(label: str, cfg: MomentumConfig, w) -> Tuple[object, object]:
    key = (label, w)
    if key not in _PF_CACHE:
        _PF_CACHE[key] = lab.run_pf(cfg, w[0], w[1], panel=panel())
    return _PF_CACHE[key]


def _sum_row(label: str, rep) -> List[str]:
    return [label, str(rep.n_trades), _f(rep.total_return, 4), _f(rep.max_drawdown, 4),
            _f(rep.win_rate, 3), _f(rep.profit_factor, 3), _f(rep.final_equity, 0)]


def portfolio_table(configs: Dict[str, MomentumConfig], w, title: str) -> str:
    hdr = ["config", "n", "total_ret", "max_dd", "win", "pf", "final_eq"]
    rows = [_sum_row(name, _pf(name, cfg, w)[0]) for name, cfg in configs.items()]
    return f"**{title}**\n\n" + _md(hdr, rows)


# ======================================================================
#  ★ 2026 段生存测试(硬门禁:负即否决)
# ======================================================================
def survival_2026(configs: Dict[str, MomentumConfig]) -> str:
    hdr = ["config", "2026 n", "2026 total_ret", "2026 max_dd", "2026 pf", "生存判定"]
    rows = []
    for name, cfg in configs.items():
        rep, _ = _pf(name, cfg, Y2026)
        verdict = "—(基线)" if name.startswith("K1") else ("✅生存" if (rep.total_return or 0) >= 0 else "❌否决")
        rows.append([name, str(rep.n_trades), _f(rep.total_return, 4), _f(rep.max_drawdown, 4),
                     _f(rep.profit_factor, 3), verdict])
    return ("**★ 2026 段生存测试(2026-01-01~2026-07-17,硬门禁:total_ret<0 即一票否决,用户裁断②)**\n\n"
            + _md(hdr, rows))


# ======================================================================
#  分层(out-frozen closed_trades by year / state)
# ======================================================================
def stratify_table(configs: Dict[str, MomentumConfig]) -> str:
    out = ["**分层(样本外冻结组合回测已平仓回合;by 买入年 / 上证MA20 状态)**", ""]
    for name, cfg in configs.items():
        _, pf = _pf(name, cfg, OUT)
        by_year = lab.stratify_by_year(pf.closed_trades)
        by_state = lab.stratify_by_state(pf.closed_trades)
        out.append(f"\n_{name} · by year_\n")
        out.append(lab.fmt(by_year) if not by_year.is_empty() else "(空)")
        out.append(f"\n_{name} · by state_\n")
        out.append(lab.fmt(by_state) if not by_state.is_empty() else "(空)")
    return "\n".join(out)


# ======================================================================
#  消融(逐维度贡献,portfolio-level)
# ======================================================================
def ablation_table() -> str:
    out = ["**消融矩阵(逐维度净贡献,portfolio-level;样本内 + 样本外冻结)**", ""]

    # 臂① 趋势背景层:A3全域(不分趋势) → +降势(C4)
    arm1 = {
        "A3全域 ret5d≤-0.10(不分趋势)": _arm(oversold_depth_col="ret_5d", oversold_depth_max=-0.10),
        "+降势(=C4)": _arm(oversold_depth_col="ret_5d", oversold_depth_max=-0.10, oversold_trend="down"),
        "+升势(反面对照)": _arm(oversold_depth_col="ret_5d", oversold_depth_max=-0.10, oversold_trend="up"),
    }
    # 臂③ 确认层/量能层:升势回撤 → +确认(收复MA5) → +量能确认
    arm3 = {
        "升势回撤直接买(无确认)": _arm(oversold_trend="up", oversold_pullback_max=-0.08),
        "+收复MA5(无量能门)": _arm(oversold_trend="up", oversold_pullback_max=-0.08, oversold_confirm="reclaim_ma5"),
        "+量能确认(×1.5)": _arm(oversold_trend="up", oversold_pullback_max=-0.08,
                              oversold_confirm="reclaim_ma5", oversold_confirm_vol=1.5),
    }
    for title, grp in [("臂① 趋势背景层消融", arm1), ("臂③ 确认层/量能层消融", arm3)]:
        out.append(f"\n_{title}_\n")
        hdr = ["层", "in n", "in ret", "in pf", "out n", "out ret", "out pf", "out dd", "2026 ret"]
        rows = []
        for name, cfg in grp.items():
            ri, _ = _pf(f"ABL:{title}:{name}", cfg, IN)
            ro, _ = _pf(f"ABL:{title}:{name}", cfg, OUT)
            r26, _ = _pf(f"ABL:{title}:{name}", cfg, Y2026)
            rows.append([name, str(ri.n_trades), _f(ri.total_return, 4), _f(ri.profit_factor, 3),
                         str(ro.n_trades), _f(ro.total_return, 4), _f(ro.profit_factor, 3),
                         _f(ro.max_drawdown, 4), _f(r26.total_return, 4)])
        out.append(_md(hdr, rows))
    return "\n".join(out)


# ======================================================================
#  walk-forward(各臂 vs K1;test 窗滚动)
# ======================================================================
def walk_forward(arms: Dict[str, MomentumConfig], train_days=252, test_days=126) -> str:
    wins = generate_walk_forward_windows(IN[0], EXT[1], train_days, test_days)
    k1 = k1_cfg()
    out = [f"**walk-forward(train={train_days}td / test={test_days}td,各臂 test 窗 vs K1)**", ""]
    for aname, acfg in arms.items():
        hdr = ["test_start", "test_end", "K1 ret", f"{aname} ret", "K1 pf", "arm pf", "arm n", "arm跑赢"]
        rows = []
        better = 0
        for w in wins:
            rk1, _ = lab.run_pf(k1, w.test_start, w.test_end, panel=panel())
            ra, _ = lab.run_pf(acfg, w.test_start, w.test_end, panel=panel())
            win = (ra.total_return or -9) > (rk1.total_return or -9)
            better += int(win)
            rows.append([str(w.test_start), str(w.test_end), _f(rk1.total_return, 4),
                         _f(ra.total_return, 4), _f(rk1.profit_factor, 2), _f(ra.profit_factor, 2),
                         str(ra.n_trades), "✓" if win else ""])
        out.append(f"\n_{aname}_ · 跑赢 K1 窗口 {better}/{len(wins)}\n")
        out.append(_md(hdr, rows))
    return "\n".join(out)


# ======================================================================
#  止损扫损率 × 波动天性交互(用户点名)
# ======================================================================
def _stop_rebound_stats(pf, qfq: pl.DataFrame, ndays=5) -> dict:
    """止损触发率 + 止损后 ndays 交易日是否反弹回卖出价上方(扫在地板)。"""
    closed = pf.closed_trades
    n = len(closed)
    if n == 0:
        return {"n": 0, "stop_frac": float("nan"), "n_stop": 0, "rebound_frac": float("nan"),
                "med_rebound": float("nan")}
    stops = [t for t in closed if t.reason.startswith("止损")]
    n_stop = len(stops)
    # 每票 (date, close) 升序表,查止损后第 ndays 个交易日 close
    by_code: Dict[str, Tuple[list, list]] = {}
    for code, sub in qfq.sort("trade_date").group_by("ts_code"):
        c = code[0] if isinstance(code, tuple) else code
        by_code[c] = (sub["trade_date"].to_list(), sub["close"].to_list())
    rebounds = []
    for t in stops:
        ds, cs = by_code.get(t.ts_code, (None, None))
        if ds is None:
            continue
        i = bisect_right(ds, t.sell_date)
        j = i + ndays - 1
        if j < len(cs):
            fwd_close = cs[j]
            rebounds.append(fwd_close / t.sell_price - 1.0)
    reb = pl.Series(rebounds) if rebounds else None
    return {
        "n": n, "n_stop": n_stop, "stop_frac": n_stop / n,
        "rebound_frac": float((reb > 0).mean()) if reb is not None and reb.len() else float("nan"),
        "med_rebound": float(reb.median()) if reb is not None and reb.len() else float("nan"),
    }


def stoploss_table(configs: Dict[str, MomentumConfig]) -> str:
    qfq = lab.adjusted_daily_cached(EXT[0], EXT[1])
    hdr = ["config", "回合n", "止损n", "止损率", "止损后5日反弹率", "止损后5日中位涨跌", "读数"]
    rows = []
    for name, cfg in configs.items():
        _, pf = _pf(f"STOP:{name}", cfg, EXT)
        s = _stop_rebound_stats(pf, qfq, ndays=5)
        note = ""
        if s["rebound_frac"] == s["rebound_frac"] and s["rebound_frac"] >= 0.5:
            note = "过半止损后反弹(扫在地板嫌疑)"
        rows.append([name, str(s["n"]), str(s["n_stop"]), _f(s["stop_frac"], 3),
                     _f(s["rebound_frac"], 3), _f(s["med_rebound"], 4), note])
    return ("**止损扫损率 × 波动天性交互(样本外延展窗;止损后5交易日是否反弹回卖出价上方=扫在地板)**\n\n"
            + _md(hdr, rows)
            + "\n\n> 主板 only(forbid_high_elasticity=True)保 -5% 有效性再证:超跌臂止损率与 K1 对照见上。")


# ======================================================================
#  敏感性 ±1 格(portfolio-level,out-frozen)
# ======================================================================
def sensitivity_table() -> str:
    grid = {
        "A6 ret20≤-0.15": _arm(oversold_depth_col="ret_20d", oversold_depth_max=-0.15),
        "A6 ret20≤-0.20": _arm(oversold_depth_col="ret_20d", oversold_depth_max=-0.20),
        "A6 ret20≤-0.25": _arm(oversold_depth_col="ret_20d", oversold_depth_max=-0.25),
        "臂③回撤≤-0.05": _arm(oversold_trend="up", oversold_pullback_max=-0.05,
                             oversold_confirm="reclaim_ma5", oversold_confirm_vol=1.5),
        "臂③回撤≤-0.08": _arm(oversold_trend="up", oversold_pullback_max=-0.08,
                             oversold_confirm="reclaim_ma5", oversold_confirm_vol=1.5),
        "臂③回撤≤-0.12": _arm(oversold_trend="up", oversold_pullback_max=-0.12,
                             oversold_confirm="reclaim_ma5", oversold_confirm_vol=1.5),
    }
    hdr = ["档", "out n", "out ret", "out pf", "out dd", "2026 ret"]
    rows = []
    for name, cfg in grid.items():
        ro, _ = _pf(f"SENS:{name}", cfg, OUT)
        r26, _ = _pf(f"SENS:{name}", cfg, Y2026)
        rows.append([name, str(ro.n_trades), _f(ro.total_return, 4), _f(ro.profit_factor, 3),
                     _f(ro.max_drawdown, 4), _f(r26.total_return, 4)])
    return "**敏感性 ±1 格(portfolio-level,样本外冻结 + 2026;防悬崖最优)**\n\n" + _md(hdr, rows)


# ======================================================================
def main() -> int:
    section = sys.argv[1] if len(sys.argv) > 1 else "all"
    configs = arm_configs()
    print(f"# K3 B2 组合回测结果(section={section})\n")
    print(f"面板 {panel().height} 行 | 窗口 IN={IN} OUT={OUT} EXT={EXT} 2026={Y2026}\n")

    if section in ("arm3_es", "all"):
        print("\n## 臂③ 事件研究先筛\n"); print(arm3_eventstudy())
    if section in ("arm4", "all"):
        print("\n## 臂④ 量化做局诊断\n"); print(arm4_diagnosis())
    if section in ("portfolio", "all"):
        print("\n## 组合级回测 vs K1\n")
        print(portfolio_table(configs, IN, "样本内 2020-2024")); print()
        print(portfolio_table(configs, OUT, "样本外冻结 2025-01~2026-07-17(主判)")); print()
        print(portfolio_table(configs, EXT, "样本外延展 2025-01~2026-07-24(补充)")); print()
        print(survival_2026(configs))
    if section in ("strat", "all"):
        print("\n## 分层\n"); print(stratify_table(configs))
    if section in ("ablation", "all"):
        print("\n## 消融\n"); print(ablation_table())
    if section in ("stoploss", "all"):
        print("\n## 止损交互\n"); print(stoploss_table(configs))
    if section in ("sens", "all"):
        print("\n## 敏感性\n"); print(sensitivity_table())
    if section in ("wf", "all"):
        print("\n## walk-forward\n")
        wf_arms = {"臂②A6 20日深跌20": configs["臂②A6 20日深跌20"],
                   "臂①C4 5日跌×降势": configs["臂①C4 5日跌×降势"],
                   "臂③收复MA5×1.5": configs["臂③收复MA5×1.5"]}
        print(walk_forward(wf_arms))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
