"""K4 前置 · 战役二 H5:当日涨幅带(扣扳机层)。

预注册见 `research/k4_pre2_report.md` §2。审计用户漏斗第③层——"当日涨幅 −1%~+5%
才买,红 2~3% 最顺手;飘红 5 个点以上不追,绿太狠不碰"。

双口径处理(日内近似争议,预注册写死,判决要求双口径同向):
    · 口径 A(次日开盘执行):D 收盘涨幅落带 → T+1 开盘买(面板 fwd 既有口径),
      测"选出当日温和红盘的票"的选股含义。用 hold_table(fwd_ret_N)。
    · 口径 B(当日尾盘执行):D 收盘涨幅落带 → **D 收盘价买、D+N 收盘卖**
      (cc_ret_N = close.shift(-N)/close − 1,研究内现算),测最贴近"盘中带内买入"
      的执行含义。扣同样双边成本 30bp。

分箱(ret_1d 粗档):<-1% / [-1%,0) / [0,2%) / **[2%,3%](核心带)** / (3%,5%] / >5%。
核心带 [2,3] 闭区间;(3,5] 左开(3% 归核心带)。域内全分箱对比 + 域基线。
稳健性:close>ma20 / board / 年份 / 2026 单列。±1 格敏感性:相邻分箱本身即 ±1 网格
邻居,另附核心带定义 [1,3]/[2,4] 平移。

独立可重跑:`python research/k4p_h5_band.py`
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.research.eventstudy import event_study, event_study_grouped  # noqa: E402
from lab import get_panel  # noqa: E402
from k4p_common import (  # noqa: E402
    DEFAULT_COST_ONESIDE, add_k4p_features, base_expr, oneword_event_expr,
    hold_table, exposure_row, fmt, year_2026_expr,
)

HOLDS = (1, 2, 3, 4, 5)
CC_NS = (2, 3)
COST2 = 2 * DEFAULT_COST_ONESIDE
BAND_ORDER = ["<-1%", "[-1,0)", "[0,2)", "[2,3]核心", "(3,5]", ">5%"]


def add_band_and_cc(panel: pl.DataFrame) -> pl.DataFrame:
    """加 ret_1d 分箱标签 + close→close 前向收益(口径 B),per ts_code。"""
    df = panel.sort(["ts_code", "trade_date"])
    r = pl.col("ret_1d")
    band = (
        pl.when(r < -0.01).then(pl.lit("<-1%"))
        .when(r < 0.0).then(pl.lit("[-1,0)"))
        .when(r < 0.02).then(pl.lit("[0,2)"))
        .when(r <= 0.03).then(pl.lit("[2,3]核心"))
        .when(r <= 0.05).then(pl.lit("(3,5]"))
        .otherwise(pl.lit(">5%"))
    )
    df = df.with_columns(band.alias("band"))
    for n in CC_NS:
        cc = pl.col("close").shift(-n).over("ts_code") / pl.col("close") - 1
        df = df.with_columns(cc.alias(f"cc_ret_{n}"))
    return df


def analysis_domain(panel: pl.DataFrame) -> pl.DataFrame:
    """base + 非一字板 + ret_1d 非空(分箱有效)。"""
    return panel.filter(base_expr() & ~oneword_event_expr() & pl.col("ret_1d").is_not_null())


def cc_row(events: pl.DataFrame, n: int, label: str) -> dict:
    """口径 B:close→close N 日净收益统计(cc_ret 非空过滤;扣 30bp)。"""
    sub = events.filter(pl.col(f"cc_ret_{n}").is_not_null())
    m = sub.height
    if m == 0:
        return {"band": label, "N": n, "n": 0, "win": float("nan"), "mean_net": float("nan"),
                "p5": float("nan"), "p10": float("nan")}
    net = sub[f"cc_ret_{n}"] - COST2
    return {
        "band": label, "N": n, "n": m,
        "win": float((net > 0).sum()) / m,
        "mean_net": float(net.mean()),
        "p5": float(net.quantile(0.05)),
        "p10": float(net.quantile(0.10)),
    }


def band_summary_A(dom: pl.DataFrame, hold: int) -> pl.DataFrame:
    """口径 A:各分箱 hold_table(fwd)摘要一行 + 次日跌停。"""
    rows: List[dict] = []
    for b in BAND_ORDER:
        ev = dom.filter(pl.col("band") == b)
        ht = hold_table(ev, (hold,)).to_dicts()[0]
        ex = exposure_row(ev, b)
        rows.append({"band": b, "n": ht["n"], "win_rate": ht["win_rate"], "mean_net": ht["mean_net"],
                     "p5_net": ht["p5_net"], "p10_net": ht["p10_net"], "next_ld": ex["next_ld_rate"]})
    ht = hold_table(dom, (hold,)).to_dicts()[0]
    ex = exposure_row(dom, "域基线")
    rows.append({"band": "域基线", "n": ht["n"], "win_rate": ht["win_rate"], "mean_net": ht["mean_net"],
                 "p5_net": ht["p5_net"], "p10_net": ht["p10_net"], "next_ld": ex["next_ld_rate"]})
    return pl.DataFrame(rows)


def band_summary_B(dom: pl.DataFrame, n: int) -> pl.DataFrame:
    """口径 B:各分箱 close→close N 日摘要一行 + 域基线。"""
    rows = [cc_row(dom.filter(pl.col("band") == b), n, b) for b in BAND_ORDER]
    rows.append(cc_row(dom, n, "域基线"))
    return pl.DataFrame(rows)


def main() -> None:
    panel = add_band_and_cc(add_k4p_features(get_panel()))
    dom = analysis_domain(panel)

    print("# H5 当日涨幅带(扣扳机层)—— 结果")
    print(f"\n分析域(base + 非一字板 + ret_1d 非空)行数 {dom.height}")
    cnt = dom.group_by("band").len()
    dist = {r["band"]: r["len"] for r in cnt.iter_rows(named=True)}
    print("分箱分布:", " | ".join(f"{b} {dist.get(b,0)}" for b in BAND_ORDER))

    # —— 口径交叉核对(口径 A):hold_table 与 eventstudy 在核心带一致 ——
    core = dom.filter(pl.col("band") == "[2,3]核心")
    chk = event_study(core, pl.col("ts_code").is_not_null(), hold_days=(3,))
    mine = hold_table(core, holds=(3,))
    assert abs(float(chk["mean_net"][0]) - float(mine["mean_net"][0])) < 1e-9, "口径与 eventstudy 不一致!"
    print("[口径交叉核对通过:hold_table.mean_net == eventstudy.event_study.mean_net]")

    print("\n## 口径 A(次日开盘执行,选股含义):各分箱 fwd 净收益 + 左尾 + 次日跌停")
    print("\n### hold=3")
    print(fmt(band_summary_A(dom, 3), intcols=("n",)))
    print("\n### hold=5")
    print(fmt(band_summary_A(dom, 5), intcols=("n",)))

    print("\n## 口径 B(当日尾盘执行,close→close,最贴近盘中带内买入):各分箱净收益 + 左尾")
    print("\n### N=2(D收盘买、D+2收盘卖)")
    print(fmt(band_summary_B(dom, 2), intcols=("n", "N")))
    print("\n### N=3(D收盘买、D+3收盘卖)")
    print(fmt(band_summary_B(dom, 3), intcols=("n", "N")))

    print("\n## 双口径对照:核心带[2,3] / 全带[-1,5] / 拒买带 vs 域基线(净期望)")
    def band_set(bands):
        return dom.filter(pl.col("band").is_in(bands))
    full = ["[-1,0)", "[0,2)", "[2,3]核心", "(3,5]"]        # 用户 -1~5 全带
    reject_hi = [">5%"]                                        # 用户拒买:飘红>5
    reject_lo = ["<-1%"]                                       # 用户拒买:绿太狠
    rowsA, rowsB = [], []
    for label, bs in [("核心带[2,3]", ["[2,3]核心"]), ("全带[-1,5]", full),
                      ("拒买>5%", reject_hi), ("拒买<-1%", reject_lo), ("域基线", None)]:
        sub = dom if bs is None else band_set(bs)
        htA = hold_table(sub, (3,)).to_dicts()[0]
        rowsA.append({"组": label, "n": htA["n"], "A_win": htA["win_rate"],
                      "A_net_h3": htA["mean_net"], "A_p5": htA["p5_net"]})
        rb = cc_row(sub, 3, label)
        rowsB.append({"组": label, "n": rb["n"], "B_win": rb["win"],
                      "B_net_N3": rb["mean_net"], "B_p5": rb["p5"]})
    print("\n### 口径 A(fwd,hold=3)")
    print(fmt(pl.DataFrame(rowsA), intcols=("n",)))
    print("\n### 口径 B(close→close,N=3)")
    print(fmt(pl.DataFrame(rowsB), intcols=("n",)))

    print("\n## 2026 分段(生存视角单列)")
    dom26 = dom.filter(year_2026_expr())
    print("\n### 口径 A hold=3")
    print(fmt(band_summary_A(dom26, 3), intcols=("n",)))
    print("\n### 口径 B N=3")
    print(fmt(band_summary_B(dom26, 3), intcols=("n", "N")))

    print("\n## 稳健性 close>ma20(不改判决;口径 A hold=3)")
    print("\n### close>ma20")
    print(fmt(band_summary_A(dom.filter(pl.col("close") > pl.col("ma20")), 3), intcols=("n",)))
    print("\n### close≤ma20")
    print(fmt(band_summary_A(dom.filter(pl.col("close") <= pl.col("ma20")), 3), intcols=("n",)))

    print("\n## 板块分层(核心带[2,3],board;概念板块降级为 board 见 §0)")
    print(fmt(event_study_grouped(core, pl.col("ts_code").is_not_null(), "board", hold_days=(3, 5))
              .select(["board", "hold_days", "n", "win_rate", "mean_net"])))

    print("\n## 年份分层(核心带[2,3],mean_net hold 3/5)")
    print(fmt(event_study_grouped(core, pl.col("ts_code").is_not_null(), "year", hold_days=(3, 5))
              .select(["year", "hold_days", "n", "win_rate", "mean_net"])))

    print("\n## ±1 格敏感性:核心带定义平移([1,3]/[2,3]/[2,4],口径 A hold=3 + 口径 B N=3)")
    r = pl.col("ret_1d")
    variants = {
        "[1,3]": (r >= 0.01) & (r <= 0.03),
        "[2,3]": (r >= 0.02) & (r <= 0.03),
        "[2,4]": (r >= 0.02) & (r <= 0.04),
    }
    rows = []
    for name, expr in variants.items():
        sub = dom.filter(expr)
        htA = hold_table(sub, (3,)).to_dicts()[0]
        rb = cc_row(sub, 3, name)
        rows.append({"核心带": name, "n": htA["n"], "A_net_h3": htA["mean_net"],
                     "A_win": htA["win_rate"], "B_net_N3": rb["mean_net"], "B_win": rb["win"]})
    print(fmt(pl.DataFrame(rows), intcols=("n",)))


if __name__ == "__main__":
    main()
