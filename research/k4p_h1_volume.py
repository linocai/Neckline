"""K4 前置 H1:量能堆积判主升 vs 突然启动判轮动(启动性质判别层)。

预注册见 `research/k4_pre_report.md` §1。事件 = 放量上行日(ret_1d≥阈 & vol>vol_ma20×倍),
域内、当日非一字板。堆积组(前3日≥2日放量)vs 突拉组(前3日全平量)前瞻收益/胜率/
左尾/次日跌停暴露对比。粗网格 {4,5,6}% × {1.5,2.0} 作 ±1 格敏感性,主判决用 5%×1.5。

独立可重跑:`python research/k4p_h1_volume.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.research.eventstudy import event_study, event_study_grouped  # noqa: E402
from lab import get_panel  # noqa: E402
from k4p_common import (  # noqa: E402
    add_k4p_features, base_expr, oneword_event_expr, hold_table, exposure_row, fmt, year_2026_expr,
)

GRID_UP = (0.04, 0.05, 0.06)
GRID_MULT = (1.5, 2.0)
PRIMARY_UP, PRIMARY_MULT = 0.05, 1.5
HOLDS = (1, 2, 3, 4, 5)


def event_expr(up: float, mult: float) -> pl.Expr:
    return (
        base_expr()
        & (pl.col("ret_1d") >= up)
        & (pl.col("vol") > pl.col("vol_ma20") * mult)
        & ~oneword_event_expr()
    )


def _grouped_net(events: pl.DataFrame, group_col: str, holds=(3, 5)) -> pl.DataFrame:
    """在事件子集上按 group_col 分层,取 mean_net(每组×每持有天数)。复用 eventstudy 口径。"""
    g = event_study_grouped(events, pl.col("ts_code").is_not_null(), group_col, hold_days=holds)
    if g.is_empty():
        return g
    return g.select([group_col, "hold_days", "n", "win_rate", "mean_net"])


def main() -> None:
    panel = add_k4p_features(get_panel())
    ev = panel.filter(event_expr(PRIMARY_UP, PRIMARY_MULT))
    dui = ev.filter(pl.col("vol_above_ma20_cnt3") >= 2)      # 堆积
    mid = ev.filter(pl.col("vol_above_ma20_cnt3") == 1)      # 中间态(单列不参与主对比)
    tu = ev.filter(pl.col("vol_above_ma20_cnt3") == 0)       # 突拉

    print("# H1 量能堆积 vs 突然启动 —— 结果")
    print(f"\n主判决事件:ret_1d≥{PRIMARY_UP:.0%} & vol>vol_ma20×{PRIMARY_MULT} & 域内 & 非一字板")
    print(f"事件总数 {ev.height} | 堆积(cnt3≥2) {dui.height} | 中间(=1) {mid.height} | 突拉(=0) {tu.height}")

    # —— 交叉核对:hold_table 与 eventstudy.event_study 在堆积组上均值/胜率一致 ——
    chk = event_study(dui, pl.col("ts_code").is_not_null(), hold_days=(3,))
    mine = hold_table(dui, holds=(3,))
    assert abs(float(chk["mean_net"][0]) - float(mine["mean_net"][0])) < 1e-9, "口径与 eventstudy 不一致!"
    print("[口径交叉核对通过:hold_table.mean_net == eventstudy.event_study.mean_net]")

    print("\n## 主对比 A:全期前瞻收益 + 左尾(扣双边成本 0.0015×2)")
    print("\n### 堆积组(前3日≥2日放量)")
    print(fmt(hold_table(dui, HOLDS)))
    print("\n### 突拉组(前3日全平量)")
    print(fmt(hold_table(tu, HOLDS)))
    print("\n### 中间态(=1,参考)")
    print(fmt(hold_table(mid, HOLDS)))

    print("\n## B:次日/持有内跌停暴露(买入后 D+1 / D+1~D+3 收盘跌停率)")
    exp = pl.DataFrame([exposure_row(dui, "堆积"), exposure_row(mid, "中间"), exposure_row(tu, "突拉")])
    print(fmt(exp))

    print("\n## C:2026 分段(生存视角单列)")
    dui26, tu26 = dui.filter(year_2026_expr()), tu.filter(year_2026_expr())
    print(f"2026 堆积 {dui26.height} | 突拉 {tu26.height}")
    print("\n### 堆积组 2026")
    print(fmt(hold_table(dui26, HOLDS)))
    print("\n### 突拉组 2026")
    print(fmt(hold_table(tu26, HOLDS)))
    print("\n### 2026 跌停暴露")
    print(fmt(pl.DataFrame([exposure_row(dui26, "堆积26"), exposure_row(tu26, "突拉26")])))

    print("\n## D:年份分层(mean_net,hold 3/5)")
    print("\n### 堆积组")
    print(fmt(_grouped_net(dui, "year")))
    print("\n### 突拉组")
    print(fmt(_grouped_net(tu, "year")))

    print("\n## E:市场状态分层(sse_above_ma;hold 3/5)")
    print("\n### 堆积组")
    print(fmt(_grouped_net(dui, "sse_above_ma")))
    print("\n### 突拉组")
    print(fmt(_grouped_net(tu, "sse_above_ma")))

    print("\n## F:板块分层(board;secondary 概念板块因 ths_member 已知洞降级为 board,见 §0)")
    print("\n### 堆积组")
    print(fmt(_grouped_net(dui, "board")))
    print("\n### 突拉组")
    print(fmt(_grouped_net(tu, "board")))

    print("\n## G:±1 格敏感性(堆积−突拉 mean_net 差,越正越支持假设)")
    rows = []
    for up in GRID_UP:
        for mult in GRID_MULT:
            e = panel.filter(event_expr(up, mult))
            d = e.filter(pl.col("vol_above_ma20_cnt3") >= 2)
            t = e.filter(pl.col("vol_above_ma20_cnt3") == 0)
            dt = hold_table(d, (3, 5))
            tt = hold_table(t, (3, 5))
            rows.append({
                "up": up, "mult": mult, "n_dui": d.height, "n_tu": t.height,
                "diff_h3": float(dt["mean_net"][0]) - float(tt["mean_net"][0]),
                "diff_h5": float(dt["mean_net"][1]) - float(tt["mean_net"][1]),
                "dui_h3": float(dt["mean_net"][0]), "tu_h3": float(tt["mean_net"][0]),
            })
    print(fmt(pl.DataFrame(rows), intcols=("n_dui", "n_tu")))


if __name__ == "__main__":
    main()
