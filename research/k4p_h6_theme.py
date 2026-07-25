"""K4 前置 · 战役二 H6:题材持续性(板块层;紧邻 K2 坟,新证据声明)。

预注册见 `research/k4_pre2_report.md` §3。审计用户战法总结 §五——"能持续两三天的
题材说明认可度高;一日游题材赚钱效应差"。

**新证据声明**:K2 判"主线成员**无条件**次日无领先性";本假设测**按题材持续天数
条件化**的成员表现差异(K2 未测此维度)。若条件化也无差异 → K2 坟扩界,如实记录。

**数据可行性先行评估(预授权降级链)**:板块映射首选 `stock_basic.industry`。
实测股票级缺失 5.67% < 10%、判决域按行覆盖 ~99.7% → **用 industry 正常施工,不降级
board**。110 个行业静态当前快照,回填偏差(行业变更股按当前行业回溯)已声明——行业
相对概念板块稳定,噪声可接受。

**定义(粗档)**:
    · 行业每日强度 = 该行业当日成员 ret_1d 中位数(市场宽度信号,over 全体有 ret_1d
      且有 industry 的票,非域限;当日成员数 <5 的行业不参与排名,中位数不稳)。
    · 强度日 = 当日行业中位数 ∈ 全行业前 20%(top quintile,逐日 quantile(0.8) 阈)。
    · 持续天数 = 连续处于强度日的天数(计到当日为止;断裂重置)。
    · 事件 = 强度日的**域内(base+非一字板)成员股**,分组:持续第 1 天(一日游候选)/
      持续第 2-3 天(认可题材)/ 持续 ≥4 天(过热候选,单列)。
**防未来函数**:强度排名用当日 ret_1d 判"今天是强度日"(合法);持续天数计到当日为止
(合法);成员前向收益从 T+1 开盘起(fwd_ret,合法)。
**敏感性 ±1 格**:强度阈值 top 15%/20%/30%(quantile 0.85/0.80/0.70)。

独立可重跑:`python research/k4p_h6_theme.py`
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import List

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.research.eventstudy import event_study, event_study_grouped  # noqa: E402
from lab import get_panel  # noqa: E402
from k4p_common import (  # noqa: E402
    add_k4p_features, base_expr, oneword_event_expr, hold_table, exposure_row, fmt, year_2026_expr,
)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "neckline.db"
HOLDS = (1, 2, 3)
MIN_MEMBERS = 5           # 当日行业成员数下限(中位数稳健)
STRENGTH_Q = 0.80         # 主判决:top 20%
GRID_Q = (0.85, 0.80, 0.70)  # 敏感性 top 15/20/30
GROUP_ORDER = ["持续1天(一日游)", "持续2-3天(认可)", "持续≥4天(过热)"]


def load_industry_map() -> pl.DataFrame:
    """stock_basic 的 ts_code→industry(仅非空 industry)。"""
    con = sqlite3.connect(str(DB_PATH))
    rows = con.execute("SELECT ts_code, industry FROM stock_basic").fetchall()
    con.close()
    data = [(tc, ind.strip()) for tc, ind in rows if ind and str(ind).strip()]
    return pl.DataFrame(data, schema=["ts_code", "industry"], orient="row")


def industry_persistence(panel: pl.DataFrame, q: float = STRENGTH_Q) -> pl.DataFrame:
    """算 (industry, trade_date) → 强度日标记 + 持续天数。返回仅强度日的行。"""
    # 行业每日中位数(市场宽度:全体有 industry & ret_1d 的票)。
    ind_daily = (
        panel.filter(pl.col("industry").is_not_null() & pl.col("ret_1d").is_not_null())
        .group_by(["trade_date", "industry"])
        .agg(pl.col("ret_1d").median().alias("med"), pl.len().alias("m"))
        .filter(pl.col("m") >= MIN_MEMBERS)
    )
    # 逐日阈值 quantile(q);强度 = med >= 阈。
    thr = pl.col("med").quantile(q).over("trade_date")
    ind_daily = ind_daily.with_columns(thr.alias("thr"))
    ind_daily = ind_daily.with_columns((pl.col("med") >= pl.col("thr")).alias("is_str"))
    # 持续天数(连续强度日;断裂重置)。sort → flip cumsum → cum_count。
    ind_daily = ind_daily.sort(["industry", "trade_date"])
    flip = (pl.col("is_str") != pl.col("is_str").shift(1).fill_null(False)).over("industry")
    ind_daily = ind_daily.with_columns(flip.cum_sum().over("industry").alias("_run_id"))
    ind_daily = ind_daily.with_columns(
        pl.col("trade_date").cum_count().over(["industry", "_run_id"]).alias("persist")
    )
    return ind_daily.filter(pl.col("is_str")).select(["trade_date", "industry", "persist"])


def attach_events(panel: pl.DataFrame, persist: pl.DataFrame) -> pl.DataFrame:
    """把强度日持续天数 join 回域内成员;分组标签。"""
    dom = panel.filter(base_expr() & ~oneword_event_expr() & pl.col("industry").is_not_null())
    ev = dom.join(persist, on=["trade_date", "industry"], how="inner")
    grp = (
        pl.when(pl.col("persist") == 1).then(pl.lit("持续1天(一日游)"))
        .when(pl.col("persist") <= 3).then(pl.lit("持续2-3天(认可)"))
        .otherwise(pl.lit("持续≥4天(过热)"))
    )
    return ev.with_columns(grp.alias("persist_grp")), dom


def _grouped_net(events: pl.DataFrame, group_col: str, holds=(3,)) -> pl.DataFrame:
    g = event_study_grouped(events, pl.col("ts_code").is_not_null(), group_col, hold_days=holds)
    if g.is_empty():
        return g
    return g.select([group_col, "hold_days", "n", "win_rate", "mean_net"])


def group_table(ev: pl.DataFrame, dom: pl.DataFrame, holds=HOLDS) -> None:
    for g in GROUP_ORDER:
        sub = ev.filter(pl.col("persist_grp") == g)
        print(f"\n### {g}(n={sub.height})")
        print(fmt(hold_table(sub, holds)))
    print(f"\n### 强度日全体(所有 persist,n={ev.height})")
    print(fmt(hold_table(ev, holds)))
    print(f"\n### 域基线(所有域内成员,不问强度,n={dom.height})")
    print(fmt(hold_table(dom, holds)))


def main() -> None:
    base = add_k4p_features(get_panel())
    imap = load_industry_map()
    n_stock = imap.height
    panel = base.join(imap, on="ts_code", how="left")

    # —— 数据可行性:判决域按行覆盖率 ——
    dom_all = panel.filter(base_expr() & ~oneword_event_expr())
    cov = float(dom_all["industry"].is_not_null().mean())

    print("# H6 题材持续性(板块层)—— 结果")
    print(f"\n[数据可行性] stock_basic 有 industry 的股票 {n_stock};判决域(base+非一字板)"
          f"按行 industry 覆盖率 {cov:.4f}({'≥0.90 → 用 industry 不降级' if cov >= 0.90 else '触发降级'})")

    persist = industry_persistence(panel, STRENGTH_Q)
    n_str_days = persist.select(["trade_date", "industry"]).unique().height
    print(f"强度日(行业×日,top {1-STRENGTH_Q:.0%})总数 {n_str_days};"
          f"持续天数分布 {persist.group_by('persist').len().sort('persist').head(8).to_dicts()}")

    ev, dom = attach_events(panel, persist)
    cnt = ev.group_by("persist_grp").len()
    dist = {r["persist_grp"]: r["len"] for r in cnt.iter_rows(named=True)}
    print("成员事件分组:", " | ".join(f"{g} {dist.get(g,0)}" for g in GROUP_ORDER))

    # —— 口径交叉核对 ——
    g23 = ev.filter(pl.col("persist_grp") == "持续2-3天(认可)")
    chk = event_study(g23, pl.col("ts_code").is_not_null(), hold_days=(3,))
    mine = hold_table(g23, holds=(3,))
    assert abs(float(chk["mean_net"][0]) - float(mine["mean_net"][0])) < 1e-9, "口径与 eventstudy 不一致!"
    print("[口径交叉核对通过:hold_table.mean_net == eventstudy.event_study.mean_net]")

    print("\n## 主判据 A:持续天数分组 前瞻收益 + 左尾(扣双边成本,hold 1..3)")
    group_table(ev, dom)

    print("\n## B:次日/持有内跌停暴露(D+1 / D+1~D+3 收盘跌停率)")
    rows = [exposure_row(ev.filter(pl.col("persist_grp") == g), g) for g in GROUP_ORDER]
    rows.append(exposure_row(ev, "强度日全体"))
    rows.append(exposure_row(dom, "域基线"))
    print(fmt(pl.DataFrame(rows)))

    print("\n## C:2026 分段(生存视角单列,hold 1..3)")
    ev26 = ev.filter(year_2026_expr())
    dom26 = dom.filter(year_2026_expr())
    for g in GROUP_ORDER:
        sub = ev26.filter(pl.col("persist_grp") == g)
        print(f"\n### {g} 2026(n={sub.height})")
        print(fmt(hold_table(sub, HOLDS)))
    print(f"\n### 域基线 2026(n={dom26.height})")
    print(fmt(hold_table(dom26, HOLDS)))

    print("\n## D:年份分层(持续2-3天组 vs 持续1天组,mean_net hold=3)")
    print("\n### 持续2-3天(认可)")
    print(fmt(_grouped_net(ev.filter(pl.col("persist_grp") == "持续2-3天(认可)"), "year")))
    print("\n### 持续1天(一日游)")
    print(fmt(_grouped_net(ev.filter(pl.col("persist_grp") == "持续1天(一日游)"), "year")))

    print("\n## E:板块分层(持续2-3天组,board;概念板块降级为 board 见 §0)")
    print(fmt(_grouped_net(ev.filter(pl.col("persist_grp") == "持续2-3天(认可)"), "board")))

    print("\n## F:±1 格敏感性(强度阈 top 15%/20%/30%;持续1天 vs 2-3天 mean_net hold=3)")
    rows = []
    for q in GRID_Q:
        p = industry_persistence(panel, q)
        e, _ = attach_events(panel, p)
        d1 = hold_table(e.filter(pl.col("persist_grp") == "持续1天(一日游)"), (3,)).to_dicts()[0]
        d23 = hold_table(e.filter(pl.col("persist_grp") == "持续2-3天(认可)"), (3,)).to_dicts()[0]
        d4 = hold_table(e.filter(pl.col("persist_grp") == "持续≥4天(过热)"), (3,)).to_dicts()[0]
        rows.append({
            "top%": f"{1-q:.0%}", "n_1天": d1["n"], "net_1天": d1["mean_net"],
            "n_2-3天": d23["n"], "net_2-3天": d23["mean_net"],
            "n_≥4天": d4["n"], "net_≥4天": d4["mean_net"],
            "diff(2-3 − 1)": d23["mean_net"] - d1["mean_net"],
        })
    print(fmt(pl.DataFrame(rows), intcols=("n_1天", "n_2-3天", "n_≥4天")))


if __name__ == "__main__":
    main()
