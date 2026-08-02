#!/usr/bin/env python3
"""④b-D 对拍脚本:`research/k7p_h11_stage.py`(研究参考实现)vs
`neckline/scan/stage.py`(生产实现)在研究面板可覆盖的真实交易日上逐行业比对。

**不进生产链路**(④b-D 明文),纯本地一次性对拍工具;不在 `scripts/` 顶层。

用法:
    python scripts/oneoff/compare_industry_stage_with_research.py --db <温n临时库路径>

`--db` 指向一个已用 `stage.refresh_industry_stage` 算好、且日期区间与
`research/lab.get_panel()` 缓存面板重叠的隔离库(真实生产库全程不参与本脚本任何
写操作)。研究侧面板缓存范围有限(本次施工时实测覆盖至 2026-07-17),重叠交易日
即比对样本,不足三天照实报告样本数,不强凑。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "research"))

import argparse  # noqa: E402
from datetime import date  # noqa: E402

import polars as pl  # noqa: E402

from neckline.db import connection  # noqa: E402
from neckline.scan import stage  # noqa: E402

_RESEARCH_TO_PROD = {
    "1启动": stage.IGNITION,
    "2发酵": stage.FERMENTATION,
    "3过热": stage.OVERHEAT,
    "4分歧回调": stage.DIVERGENCE,
    "5退潮": stage.EBB,
    "0无题材": stage.NONE_STAGE,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True, help="已算好 industry_stage_daily 的隔离库路径")
    args = ap.parse_args()

    from k7p_common import add_k7p_features
    from k7p_h11_stage import STRENGTH_Q, industry_states, load_industry_map
    from lab import get_panel

    panel = get_panel()
    panel_max = panel["trade_date"].max()
    panel_min = panel["trade_date"].min()
    print(f"研究面板覆盖:{panel_min} ~ {panel_max}")

    with connection(args.db) as conn:
        prod_days = sorted(
            r[0] for r in conn.execute(f"SELECT DISTINCT trade_date FROM {stage.TABLE}")
        )
    if not prod_days:
        print("隔离库 industry_stage_daily 为空,先跑 stage.refresh_industry_stage 灌数据")
        return 1
    prod_dates = [date(int(d[:4]), int(d[4:6]), int(d[6:])) for d in prod_days]
    overlap = sorted(d for d in prod_dates if panel_min <= d <= panel_max)
    print(f"生产库已算 {len(prod_dates)} 天,与研究面板重叠 {len(overlap)} 天:{overlap}")
    if not overlap:
        print("样本数=0(生产库区间与研究面板缓存无重叠)——如实报告,不强凑对拍。")
        return 0

    imap = load_industry_map()
    panel2 = add_k7p_features(panel).join(imap, on="ts_code", how="left")
    research_states = industry_states(panel2, STRENGTH_Q)

    total = agree = 0
    disagreements = []
    for d in overlap:
        d_s = d.strftime("%Y%m%d")
        with connection(args.db) as conn:
            prod_rows = conn.execute(
                "SELECT industry, stage FROM industry_stage_daily WHERE trade_date=?", (d_s,)
            ).fetchall()
        prod_by_ind = dict(prod_rows)
        res_day = research_states.filter(pl.col("trade_date") == d)
        res_by_ind = {
            r["industry"]: _RESEARCH_TO_PROD[r["stage"]] for r in res_day.iter_rows(named=True)
        }
        common = set(prod_by_ind) & set(res_by_ind)
        for ind in sorted(common):
            total += 1
            if prod_by_ind[ind] == res_by_ind[ind]:
                agree += 1
            else:
                disagreements.append((d_s, ind, prod_by_ind[ind], res_by_ind[ind]))
        only_prod = set(prod_by_ind) - set(res_by_ind)
        only_res = set(res_by_ind) - set(prod_by_ind)
        if only_prod or only_res:
            print(f"{d_s}: 生产独有行业 {len(only_prod)}(研究侧该行业当日样本不足5被跳过),"
                  f"研究独有 {len(only_res)}(生产侧当日缺行/未评级)")

    print(f"\n共同覆盖 {total} 个 (行业,交易日) 组合,一致 {agree} 个"
          f"({100*agree/total:.1f}%),分歧 {len(disagreements)} 个")
    if disagreements:
        print("分歧明细(生产 vs 研究):")
        for d_s, ind, p, r in disagreements[:40]:
            print(f"  {d_s} {ind}: 生产={p} 研究={r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
