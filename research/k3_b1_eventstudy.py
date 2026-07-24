"""K3 · B1 超跌定义库事件研究(预注册见 k3_report.md「B1 预注册」,commit 1cec089)。

对预注册的全定义(深度 A / 位置 / 跌法 B / 趋势背景 C)在 K3 扩展面板上跑信号级事件
研究:hold=1/2/3/5 的净期望 + **分布(胜率 / 左尾 p5·p10 / 跌停暴露 / 深亏占比)**——
均值口径测不到接刀左尾,K3 判「会弹 vs 是刀」不只看均值(k3_report §0.3)。

窗口:样本内 2020-2024;样本外**冻结** 2025-01-01~2026-07-17(与 K1 严格可比,总管既定);
延展补充窗 2025-01-01~2026-07-24(只作幸存旁证)。净收益扣双边成本 0.0015×2。

用法:python research/k3_b1_eventstudy.py            # 打印全部 markdown 表
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from k3_panel import build_k3_panel  # noqa: E402
from neckline.research.eventstudy import DEFAULT_COST_ONESIDE  # noqa: E402
from neckline.research.panel import base_universe_expr  # noqa: E402
from neckline.strategy import signals as S  # noqa: E402

IN = (date(2020, 1, 1), date(2024, 12, 31))
OUT_FROZEN = (date(2025, 1, 1), date(2026, 7, 17))
OUT_EXT = (date(2025, 1, 1), date(2026, 7, 24))
COST2 = 2 * DEFAULT_COST_ONESIDE


# —— K3 base 质量域(常开;所有超跌定义共叠加)————————————————————————
def k3_base() -> pl.Expr:
    return (
        base_universe_expr()
        & (pl.col("board") == "MAIN")
        & (pl.col("days_since_listing") >= 120)
    )


# —— 预注册定义(expr 已含 k3_base)————————————————————————————————
def _defs() -> Dict[str, pl.Expr]:
    b = k3_base()
    rd, r5, r10, r20 = (pl.col("ret_1d"), pl.col("ret_5d"), pl.col("ret_10d"), pl.col("ret_20d"))
    vr = pl.col("vol_ratio_5")
    d20 = pl.col("dist_from_high_20d")
    dma = pl.col("dist_from_ma250")
    up = (pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up")
    down = (pl.col("close") < pl.col("ma250")) & ~pl.col("ma250_slope_up")
    mid = (pl.col("close") > pl.col("ma250")) & ~pl.col("ma250_slope_up")
    return {
        "BASE(k3质量域)": b,
        # ① 深度
        "A1 单日急跌5": b & (rd <= -0.05),
        "A2 单日急跌7": b & (rd <= -0.07),
        "A3 5日回落10": b & (r5 <= -0.10),
        "A4 5日深跌15": b & (r5 <= -0.15),
        "A5 10日深跌15": b & (r10 <= -0.15),
        "A6 20日深跌20": b & (r20 <= -0.20),
        "A9 5日跌底5%": b & (pl.col("ret_5d_pct") <= 0.05),
        "A10 5日跌底10%": b & (pl.col("ret_5d_pct") <= 0.10),
        # ⑤ 位置
        "A7 距20高-15": b & (d20 <= -0.15),
        "A8 距20高-20": b & (d20 <= -0.20),
        "A11 距年线-15": b & (dma <= -0.15),
        # ② 跌法
        "B1 放量急跌1.5": b & (rd <= -0.05) & (vr >= 1.5),
        "B2 放量急跌2.0": b & (rd <= -0.05) & (vr >= 2.0),
        "B3 缩量急跌": b & (rd <= -0.05) & (vr <= 0.8),
        "B4 缩量阴跌": b & (pl.col("consec_down_days") >= 3) & (r5 <= -0.08) & (vr <= 0.8),
        "B5 放量5日跌": b & (r5 <= -0.10) & (vr >= 1.5),
        # ③ 趋势背景(★)
        "C1 急跌×升势回撤": b & (rd <= -0.05) & up,
        "C2 急跌×降势下跌": b & (rd <= -0.05) & down,
        "C3 5日跌×升势回撤": b & (r5 <= -0.10) & up,
        "C4 5日跌×降势下跌": b & (r5 <= -0.10) & down,
        "C5 急跌×中间态": b & (rd <= -0.05) & mid,
    }


def _window(panel: pl.DataFrame, w) -> pl.DataFrame:
    a, b = w
    return panel.filter((pl.col("trade_date") >= a) & (pl.col("trade_date") <= b))


def _stats_hold(sig: pl.DataFrame, d: int) -> dict:
    col = f"fwd_ret_{d}"
    sub = sig.filter(pl.col("fwd_buyable") & pl.col(col).is_not_null())
    n = sub.height
    if n == 0:
        return {"hold": d, "n": 0, "win": float("nan"), "mean_net": float("nan"),
                "med_net": float("nan"), "pf": float("nan"), "p5": float("nan"), "p10": float("nan")}
    net = sub[col] - COST2
    wins, losses = net.filter(net > 0), net.filter(net < 0)
    gp = float(wins.sum()) if wins.len() else 0.0
    gl = abs(float(losses.sum())) if losses.len() else 0.0
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    return {
        "hold": d, "n": n, "win": float((net > 0).sum()) / n,
        "mean_net": float(net.mean()), "med_net": float(net.median()), "pf": pf,
        "p5": float(net.quantile(0.05)), "p10": float(net.quantile(0.10)),
    }


def _tail_metrics(sig: pl.DataFrame) -> dict:
    """定义级尾部度量(与 hold 无关 + hold3 深亏):跌停暴露 + hold3 净≤-10% 占比。"""
    buy = sig.filter(pl.col("fwd_buyable"))
    nb = buy.height
    ld_next = float(buy["fwd_ld_next"].mean()) if nb else float("nan")
    ld_h3 = float(buy["fwd_ld_hold3"].mean()) if nb else float("nan")
    h3 = buy.filter(pl.col("fwd_ret_3").is_not_null())
    deep = float(((h3["fwd_ret_3"] - COST2) <= -0.10).mean()) if h3.height else float("nan")
    return {"ld_next": ld_next, "ld_hold3": ld_h3, "deeploss3": deep}


def run_definition(panel: pl.DataFrame, expr: pl.Expr, w, holds=(1, 2, 3, 5)) -> dict:
    sig = _window(panel, w).filter(expr)
    holds_stats = {d: _stats_hold(sig, d) for d in holds}
    return {"holds": holds_stats, "tail": _tail_metrics(sig)}


# ————————————————————————————————————————————————————————————————
#  报表渲染
# ————————————————————————————————————————————————————————————————
def _f(x, p=4):
    if x is None or (isinstance(x, float) and x != x):
        return "nan"
    if isinstance(x, float) and abs(x) > 1e6:
        return "inf"
    return f"{x:.{p}f}"


def headline_table(panel: pl.DataFrame, defs: Dict[str, pl.Expr]) -> str:
    """每定义:样本内/外(冻结)hold=3 净均值·PF·左尾 + 定义级尾部。"""
    lines = [
        "| 定义 | in n | in mean3 | in PF3 | out n | out mean3 | out PF3 | out win3 | out p5·3 | out p10·3 | ld_next | ld_h3 | deep3 |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for name, expr in defs.items():
        ri = run_definition(panel, expr, IN)
        ro = run_definition(panel, expr, OUT_FROZEN)
        hi, ho, to = ri["holds"][3], ro["holds"][3], ro["tail"]
        lines.append(
            f"| {name} | {hi['n']} | {_f(hi['mean_net'])} | {_f(hi['pf'],3)} | "
            f"{ho['n']} | {_f(ho['mean_net'])} | {_f(ho['pf'],3)} | {_f(ho['win'],3)} | "
            f"{_f(ho['p5'],3)} | {_f(ho['p10'],3)} | {_f(to['ld_next'],4)} | "
            f"{_f(to['ld_hold3'],4)} | {_f(to['deeploss3'],3)} |"
        )
    return "\n".join(lines)


def hold_curve_table(panel: pl.DataFrame, defs: Dict[str, pl.Expr], w, label: str) -> str:
    lines = [f"**{label} 净均值持有曲线(mean_net @ hold)**", "",
             "| 定义 | n@3 | h1 | h2 | h3 | h5 |", "|---|--:|--:|--:|--:|--:|"]
    for name, expr in defs.items():
        r = run_definition(panel, expr, w)["holds"]
        lines.append(f"| {name} | {r[3]['n']} | {_f(r[1]['mean_net'])} | {_f(r[2]['mean_net'])} | "
                     f"{_f(r[3]['mean_net'])} | {_f(r[5]['mean_net'])} |")
    return "\n".join(lines)


def layered_table(panel: pl.DataFrame, expr: pl.Expr, name: str, group_col: str, w) -> str:
    sig = _window(panel, w).filter(expr)
    groups = sig.select(group_col).unique().drop_nulls().sort(group_col)[group_col].to_list()
    lines = [f"**{name} · 分层 by {group_col}(hold=3, out-frozen)**", "",
             "| 组 | n | mean_net | PF | win | p10 |", "|---|--:|--:|--:|--:|--:|"]
    for g in groups:
        gs = sig.filter(pl.col(group_col) == g)
        s = _stats_hold(gs, 3)
        lines.append(f"| {g} | {s['n']} | {_f(s['mean_net'])} | {_f(s['pf'],3)} | {_f(s['win'],3)} | {_f(s['p10'],3)} |")
    return "\n".join(lines)


def trend_map(panel: pl.DataFrame) -> str:
    """★ 深度 × 趋势背景 地图(会弹 vs 是刀分水岭)。样本内/外(冻结)hold=3。"""
    rd = pl.col("ret_1d") <= -0.05
    r5 = pl.col("ret_5d") <= -0.10
    b = k3_base()
    up = (pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up")
    down = (pl.col("close") < pl.col("ma250")) & ~pl.col("ma250_slope_up")
    mid = (pl.col("close") > pl.col("ma250")) & ~pl.col("ma250_slope_up")
    grid = {
        ("急跌 ret1d≤-5%", "全域(不分趋势)"): b & rd,
        ("急跌 ret1d≤-5%", "升势回撤(会弹?)"): b & rd & up,
        ("急跌 ret1d≤-5%", "中间态"): b & rd & mid,
        ("急跌 ret1d≤-5%", "降势下跌(接刀?)"): b & rd & down,
        ("5日跌 ret5d≤-10%", "全域(不分趋势)"): b & r5,
        ("5日跌 ret5d≤-10%", "升势回撤(会弹?)"): b & r5 & up,
        ("5日跌 ret5d≤-10%", "中间态"): b & r5 & mid,
        ("5日跌 ret5d≤-10%", "降势下跌(接刀?)"): b & r5 & down,
    }
    lines = ["| 深度锚 | 趋势背景 | in n | in mean3 | out n | out mean3 | out PF3 | out win3 | out p10·3 | ld_h3 | deep3 |",
             "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for (depth, trend), expr in grid.items():
        ri = run_definition(panel, expr, IN)["holds"][3]
        ro = run_definition(panel, expr, OUT_FROZEN)
        h, t = ro["holds"][3], ro["tail"]
        lines.append(f"| {depth} | {trend} | {ri['n']} | {_f(ri['mean_net'])} | {h['n']} | "
                     f"{_f(h['mean_net'])} | {_f(h['pf'],3)} | {_f(h['win'],3)} | {_f(h['p10'],3)} | "
                     f"{_f(t['ld_hold3'],4)} | {_f(t['deeploss3'],3)} |")
    return "\n".join(lines)


def sensitivity_table(panel: pl.DataFrame) -> str:
    """±1 格阈值敏感性(防悬崖)。out-frozen hold=3。"""
    b = k3_base()
    rows = {
        "A1 ret1d ≤ -0.04": b & (pl.col("ret_1d") <= -0.04),
        "A1 ret1d ≤ -0.05": b & (pl.col("ret_1d") <= -0.05),
        "A1 ret1d ≤ -0.06": b & (pl.col("ret_1d") <= -0.06),
        "A1 ret1d ≤ -0.07": b & (pl.col("ret_1d") <= -0.07),
        "A3 ret5d ≤ -0.08": b & (pl.col("ret_5d") <= -0.08),
        "A3 ret5d ≤ -0.10": b & (pl.col("ret_5d") <= -0.10),
        "A3 ret5d ≤ -0.12": b & (pl.col("ret_5d") <= -0.12),
        "A3 ret5d ≤ -0.15": b & (pl.col("ret_5d") <= -0.15),
        "B1 vol_ratio ≥ 1.3": b & (pl.col("ret_1d") <= -0.05) & (pl.col("vol_ratio_5") >= 1.3),
        "B1 vol_ratio ≥ 1.5": b & (pl.col("ret_1d") <= -0.05) & (pl.col("vol_ratio_5") >= 1.5),
        "B1 vol_ratio ≥ 2.0": b & (pl.col("ret_1d") <= -0.05) & (pl.col("vol_ratio_5") >= 2.0),
        "A7 dist20 ≤ -0.10": b & (pl.col("dist_from_high_20d") <= -0.10),
        "A7 dist20 ≤ -0.15": b & (pl.col("dist_from_high_20d") <= -0.15),
        "A7 dist20 ≤ -0.20": b & (pl.col("dist_from_high_20d") <= -0.20),
    }
    lines = ["| 档 | out n | out mean3 | out PF3 | out p10·3 |", "|---|--:|--:|--:|--:|"]
    for name, expr in rows.items():
        s = _stats_hold(_window(panel, OUT_FROZEN).filter(expr), 3)
        lines.append(f"| {name} | {s['n']} | {_f(s['mean_net'])} | {_f(s['pf'],3)} | {_f(s['p10'],3)} |")
    return "\n".join(lines)


def ext_supplement(panel: pl.DataFrame, defs: Dict[str, pl.Expr]) -> str:
    """延至最新补充统计:样本外冻结(≤07-17) vs 延展(≤07-24)对比(hold≤2,末端长持有截断)。"""
    lines = ["| 定义 | frozen n | frozen mean2 | ext n | ext mean2 | Δn | 结论 |", "|---|--:|--:|--:|--:|--:|---|"]
    for name, expr in defs.items():
        rf = run_definition(panel, expr, OUT_FROZEN)["holds"][2]
        re = run_definition(panel, expr, OUT_EXT)["holds"][2]
        dn = re["n"] - rf["n"]
        same = "同向" if (rf["mean_net"] == rf["mean_net"] and re["mean_net"] == re["mean_net"]
                        and (rf["mean_net"] > 0) == (re["mean_net"] > 0)) else "异号/nan"
        lines.append(f"| {name} | {rf['n']} | {_f(rf['mean_net'])} | {re['n']} | {_f(re['mean_net'])} | +{dn} | {same} |")
    return "\n".join(lines)


def main() -> int:
    panel = build_k3_panel()
    defs = _defs()
    print("# K3 B1 事件研究结果\n")
    print("## 表1 · 全定义总览(样本内 vs 样本外冻结,hold=3 + 尾部)\n")
    print(headline_table(panel, defs))
    print("\n## 表2 · 样本外冻结持有曲线\n")
    print(hold_curve_table(panel, defs, OUT_FROZEN, "样本外冻结"))
    print("\n## 表3 · ★深度×趋势背景地图\n")
    print(trend_map(panel))
    print("\n## 表4 · 阈值敏感性(±1格)\n")
    print(sensitivity_table(panel))
    print("\n## 表5 · C1/C4 分层 by year(趋势背景核心分层)\n")
    print(layered_table(panel, defs["C1 急跌×升势回撤"], "C1", "year", OUT_FROZEN))
    print()
    print(layered_table(panel, defs["C4 5日跌×降势下跌"], "C4", "year", OUT_FROZEN))
    print("\n## 表6 · 延至最新补充统计\n")
    key = {k: defs[k] for k in ["A1 单日急跌5", "A3 5日回落10", "B1 放量急跌1.5",
                                 "C1 急跌×升势回撤", "C3 5日跌×升势回撤"]}
    print(ext_supplement(panel, key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
