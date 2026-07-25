"""K4 前置 · 战役三 H7:回调大红反弹(事件研究档,便宜梯)。

预注册见 `research/k4_pre3_report.md` §1。**一个结构假设走两级梯子的第一级**:
    主事件 = 域内 & close≤ma20(回调态)& ret_1d≥5%(大红)& 非一字板 → 买 T+1 开盘。
    2×2 细胞 = 趋势背景(年线上/下)× 触发强度(非涨停大红/涨停大红)。

**趋势背景轴复用 K3 研究面板扩展**(`k3_panel.parquet` 已含 ma250/ma250_slope_up,
不重算),故本 runner 读 **k3_panel**(panel_full 的超集:含 ma250 + 全部共享列),
按 trade_date≤2026-07-17 切到战役一/二冻结窗(行数与 panel_full 逐位一致 7,803,220;
且末周 07-11~07-17 的前瞻收益因 k3_panel 载到 07-24 而**完整**,优于 panel_full 截断尾)。
    · 年线上 = close>ma250 且 ma250_slope_up(承 K3 C1 定义)。
    · 年线下 = ma250 非空 且 ~(close>ma250 且 ma250_slope_up)(与年线上互补,无中间态遗漏)。
    · ma250 需 250 交易日预热,2020 全 null → 趋势轴有效窗 ≈2021+(数据边界,诚实标注)。

对照三件套:域基线 / close≤ma20 全体 / close>ma20 & ret_1d≥5%(战役二 H5 追强镜像)。
涨停判定用面板 `is_limit_up`(源 limit_derived,不重推幅度规则)。左尾/跌停暴露逐细胞必报。

**H7 升级闸门(预注册写死)**:细胞 全期净期望>0 且 左尾不肥于域基线 且 2026 分段非负
→ 升 H8;全灭 → 战役终,H8 记「未达」。

独立可重跑:`python research/k4p_h7_bounce.py`
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Dict, List

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.research.eventstudy import event_study, event_study_grouped  # noqa: E402
from k4p_common import (  # noqa: E402
    base_expr, oneword_event_expr, hold_table, exposure_row, fmt, year_2026_expr,
)

K3_PANEL = Path(__file__).resolve().parent / "_cache" / "k3_panel.parquet"
FROZEN_END = date(2026, 7, 17)              # 战役一/二冻结窗末端(与 panel_full 逐位可比)
HOLDS = (1, 2, 3, 4, 5)

# —— 趋势背景轴(复用 k3_panel 的 ma250/ma250_slope_up)——
TREND_ABOVE = pl.col("ma250").is_not_null() & (pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up")
TREND_BELOW = pl.col("ma250").is_not_null() & ~((pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up"))
# —— 触发强度轴 ——
LIMIT_UP = pl.col("is_limit_up")
NON_LIMIT = ~pl.col("is_limit_up")


def load_panel() -> pl.DataFrame:
    df = pl.read_parquet(K3_PANEL).filter(pl.col("trade_date") <= FROZEN_END)
    return df


def event_pool(dom: pl.DataFrame) -> pl.DataFrame:
    """H7 事件池:域内 & close≤ma20 & ret_1d≥5% & 非一字板(2×2 细胞的母集)。"""
    return dom.filter(
        (pl.col("close") <= pl.col("ma20"))
        & (pl.col("ret_1d") >= 0.05)
        & ~oneword_event_expr()
    )


def cells(pool: pl.DataFrame) -> Dict[str, pl.DataFrame]:
    return {
        "年线上×非涨停大红": pool.filter(TREND_ABOVE & NON_LIMIT),
        "年线上×涨停大红": pool.filter(TREND_ABOVE & LIMIT_UP),
        "年线下×非涨停大红": pool.filter(TREND_BELOW & NON_LIMIT),
        "年线下×涨停大红(诱多复核)": pool.filter(TREND_BELOW & LIMIT_UP),
    }


def _row(ev: pl.DataFrame, label: str, hold: int) -> dict:
    ht = hold_table(ev, (hold,)).to_dicts()[0]
    ex = exposure_row(ev, label)
    return {
        "组": label, "n": ht["n"], "win": ht["win_rate"], "net": ht["mean_net"],
        "p5": ht["p5_net"], "p10": ht["p10_net"],
        "ld_next": ex["next_ld_rate"], "ld_h3": ex["hold3_ld_rate"], "pf": ht["pf"],
    }


def summary_table(groups: Dict[str, pl.DataFrame], hold: int) -> pl.DataFrame:
    return pl.DataFrame([_row(ev, name, hold) for name, ev in groups.items()])


def gate_table(cell_map: Dict[str, pl.DataFrame], base_dom: pl.DataFrame) -> pl.DataFrame:
    """逐细胞升级闸门评估:全期净期望(h3)/ 左尾 vs 基线 / 2026 分段(h3)→ 判定。"""
    b3 = hold_table(base_dom, (3,)).to_dicts()[0]
    b_p5, b_p10 = b3["p5_net"], b3["p10_net"]
    rows: List[dict] = []
    for name, ev in cell_map.items():
        h3 = hold_table(ev, (3,)).to_dicts()[0]
        h3_26 = hold_table(ev.filter(year_2026_expr()), (3,)).to_dicts()[0]
        pass_exp = h3["mean_net"] > 0
        # 左尾"不肥于域基线":p5 与 p10 均不比基线显著更负(容差 +25bp 视为"约等/更干净")
        pass_tail = (h3["p5_net"] >= b_p5 - 0.0025) and (h3["p10_net"] >= b_p10 - 0.0025)
        pass_2026 = (h3_26["mean_net"] is not None) and (h3_26["mean_net"] >= 0)
        survive = pass_exp and pass_tail and pass_2026
        rows.append({
            "细胞": name, "全期net3": h3["mean_net"], "净>0": pass_exp,
            "p5(基线%.4f)" % b_p5: h3["p5_net"], "左尾不肥": pass_tail,
            "2026net3": h3_26["mean_net"], "2026≥0": pass_2026,
            "升H8": survive,
        })
    return pl.DataFrame(rows)


def vol_layer(ev: pl.DataFrame, hold: int) -> pl.DataFrame:
    hi = ev.filter(pl.col("vol_ratio_5") >= 1.5)
    lo = ev.filter(pl.col("vol_ratio_5") < 1.5)
    return pl.DataFrame([_row(hi, "vol_ratio_5≥1.5", hold), _row(lo, "vol_ratio_5<1.5", hold)])


def sensitivity(dom: pl.DataFrame) -> pl.DataFrame:
    """±1 格:ret_1d 阈 {4,5,6}% × 回调态 {close≤ma20, close≤ma60}(星细胞=年线下×非涨停大红,h3)。"""
    rows: List[dict] = []
    for ret_thr in (0.04, 0.05, 0.06):
        for pb_name, pb_expr in (("close≤ma20", pl.col("close") <= pl.col("ma20")),
                                 ("close≤ma60", pl.col("close") <= pl.col("ma60"))):
            ev = dom.filter(
                pb_expr & (pl.col("ret_1d") >= ret_thr) & ~oneword_event_expr()
                & TREND_BELOW & NON_LIMIT
            )
            r = _row(ev, f"ret≥{int(ret_thr*100)}% × {pb_name}", 3)
            ev26 = ev.filter(year_2026_expr())
            r26 = hold_table(ev26, (3,)).to_dicts()[0]
            r["2026net3"] = r26["mean_net"]
            rows.append(r)
    return pl.DataFrame(rows)


def k3_arm4_cross(dom_full: pl.DataFrame) -> pl.DataFrame:
    """诱多复核细胞 vs K3 臂④「年线下·涨停」对拍(解释异同:close≤ma20 是否隔离掉诱多)。
    K3 臂④原定义(k3_b2_portfolio.arm4)= k3_base(MAIN only) & close<ma250 & is_limit_up,不带 close≤ma20。
    此处在**同一战役三全板块域**上复算两版,隔离 close≤ma20 的作用。"""
    below_ma250 = pl.col("close") < pl.col("ma250")   # 臂④口径:仅 close<ma250(不含 ma250_slope)
    rows = []
    variants = {
        "K3臂④口径:年线下(close<ma250)·涨停(不限position)": dom_full.filter(below_ma250 & LIMIT_UP & ~oneword_event_expr()),
        "  +回调态 close≤ma20(=H7诱多复核细胞近似)": dom_full.filter(below_ma250 & LIMIT_UP & (pl.col("close") <= pl.col("ma20")) & ~oneword_event_expr()),
        "  +position close>ma20(涨停突破到均线上=真诱多)": dom_full.filter(below_ma250 & LIMIT_UP & (pl.col("close") > pl.col("ma20")) & ~oneword_event_expr()),
    }
    for name, ev in variants.items():
        h3 = hold_table(ev, (3,)).to_dicts()[0]
        h3_26 = hold_table(ev.filter(year_2026_expr()), (3,)).to_dicts()[0]
        ex = exposure_row(ev, name)
        rows.append({"口径": name, "n": h3["n"], "win": h3["win_rate"], "全期net3": h3["mean_net"],
                     "p5": h3["p5_net"], "ld_next": ex["next_ld_rate"],
                     "2026 n": h3_26["n"], "2026net3": h3_26["mean_net"]})
    return pl.DataFrame(rows)


def main() -> None:
    panel = load_panel()
    dom = panel.filter(base_expr())
    pool = event_pool(dom)
    cell_map = cells(pool)

    print("# H7 回调大红反弹(事件研究档)—— 结果")
    print(f"\n面板 k3_panel 切至冻结窗 ≤{FROZEN_END}:{panel.height} 行(与 panel_full 逐位可比)。")
    print(f"基础域(base_expr,全板块+非次新)行数 {dom.height}。")
    print(f"H7 事件池(close≤ma20 & ret_1d≥5% & 非一字板)= {pool.filter(pl.col('fwd_buyable')).height}(buyable)。")
    print("细胞分布(buyable):", " | ".join(f"{k} {v.filter(pl.col('fwd_buyable')).height}" for k, v in cell_map.items()))
    print(f"  ma250 为空(2020,趋势轴无效)被排除出四细胞的事件:{pool.filter(pl.col('ma250').is_null()).filter(pl.col('fwd_buyable')).height}")

    # 口径交叉核对(与 eventstudy 逐位一致)
    star = cell_map["年线下×非涨停大红"]
    chk = event_study(star, pl.col("ts_code").is_not_null(), hold_days=(3,))
    mine = hold_table(star, (3,))
    assert abs(float(chk["mean_net"][0]) - float(mine["mean_net"][0])) < 1e-9, "口径与 eventstudy 不一致!"
    print("[口径交叉核对通过:hold_table.mean_net == eventstudy.event_study.mean_net]")

    controls = {
        "域基线": dom,
        "close≤ma20全体": dom.filter(pl.col("close") <= pl.col("ma20")),
        "close>ma20&ret1d≥5%(H5追强镜像)": dom.filter((pl.col("close") > pl.col("ma20")) & (pl.col("ret_1d") >= 0.05) & ~oneword_event_expr()),
    }
    main_grp = {**cell_map, **controls}

    for h in (3, 5):
        print(f"\n## 四细胞 + 对照三件套(hold={h};net 扣双边成本 30bp,p5/p10 左尾,ld 跌停暴露)")
        print(fmt(summary_table(main_grp, h), intcols=("n",)))

    print("\n## 全 hold 矩阵(四细胞,hold=1..5)")
    for name, ev in cell_map.items():
        ht = hold_table(ev, HOLDS)
        print(f"\n### {name}")
        print(fmt(ht.select(["hold", "n", "win_rate", "mean_net", "p5_net", "p10_net", "pf"])))

    print("\n## 2026 分段(生存视角单列,hold=3)")
    grp26 = {k: v.filter(year_2026_expr()) for k, v in main_grp.items()}
    print(fmt(summary_table(grp26, 3), intcols=("n",)))

    print("\n## ★ H7 升级闸门评估(全期net>0 且 左尾不肥于域基线 且 2026≥0 → 升 H8)")
    print(fmt(gate_table(cell_map, dom), floatfmt="{:.4f}", intcols=()))

    print("\n## 板块分层(四细胞,board,hold=3)")
    for name, ev in cell_map.items():
        g = event_study_grouped(ev, pl.col("ts_code").is_not_null(), "board", hold_days=(3,))
        print(f"\n### {name}")
        print(fmt(g.select(["board", "hold_days", "n", "win_rate", "mean_net"]), intcols=("n", "hold_days")))

    print("\n## 逐年拆解(星细胞 年线下×非涨停大红,揭示 2024-25 灌水 vs 2026 蒸发)")
    g = event_study_grouped(cell_map["年线下×非涨停大红"], pl.col("ts_code").is_not_null(), "year", hold_days=(3,))
    print(fmt(g.select(["year", "hold_days", "n", "win_rate", "mean_net"]), intcols=("n", "hold_days", "year")))

    print("\n## 量能分层(不改判决;星细胞 年线下×非涨停大红,hold=3)")
    print(fmt(vol_layer(cell_map["年线下×非涨停大红"], 3), intcols=("n",)))

    print("\n## ±1 格敏感性(星细胞:ret_1d {4,5,6}% × 回调态 {ma20,ma60},hold=3 + 2026)")
    print(fmt(sensitivity(dom), intcols=("n",)))

    print("\n## 与 K3 臂④『年线下·涨停』对拍(解释诱多复核细胞的全期正号来自何处)")
    print(fmt(k3_arm4_cross(dom), intcols=("n", "2026 n")))


if __name__ == "__main__":
    main()
