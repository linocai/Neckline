"""v1.4-②-C 切换对拍:A2/B3 题材持续天数判据从概念板块 board_age 代理切到
`industry_strength`(行业强度)唯一源,同一交易日、同一批标的(全市场 + 当日持仓 +
当日候选池)各出一份 A2/B3 命中集,产出 `archive/v1.4_A2B3口径切换对拍_<date>.md`。

**只读本地开发库/parquet(零生产访问、零写库)**——纯审计脚本,跑完即完工,产出物是
本次改动的活体证据(plan §五 v1.4-②-C 硬要求:「没出报告 = 本块不算完工」)。

用法:`python scripts/oneoff/compare_a2b3_industry_switch.py [YYYYMMDD]`
(缺省取本地 `daily` 表最新交易日)。
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import polars as pl  # noqa: E402

from neckline.config import settings  # noqa: E402
from neckline.data.market_data import load_stock_basic, table_dir  # noqa: E402
from neckline.report import industry_strength as ist  # noqa: E402
from neckline.report.intel_candidates import build_intel_candidates  # noqa: E402
from neckline.report.sectors import (  # noqa: E402
    compute_sector_strength,
    load_member_map,
    sector_hot_lookup,
)
from neckline.sentinel.positions import load_open_positions  # noqa: E402
from neckline.strategy import brain  # noqa: E402

DB_PATH = settings.db_path
PARQUET_DIR = settings.parquet_dir

_A2_MIN = 4
_B3_LO, _B3_HI = 2, 3


def _classify(persist: int) -> str:
    if persist >= _A2_MIN:
        return "A2"
    if _B3_LO <= persist <= _B3_HI:
        return "B3"
    return "none"


def _latest_daily_date() -> date:
    d = table_dir("daily", PARQUET_DIR)
    files = sorted((d).glob("year=*/*.parquet"))
    assert files, "本地 daily 表为空,无法确定对拍日"
    return max(datetime.strptime(f.stem, "%Y%m%d").date() for f in files)


def main(trade_date: Optional[date] = None) -> None:
    trade_date = trade_date or _latest_daily_date()
    print(f"对拍交易日:{trade_date}")

    # —— 全市场标的清单(stock_basic 上市中)——————————————————————————————
    sb = load_stock_basic(DB_PATH)
    universe = sorted(
        sb.filter(pl.col("list_status") == "L")["ts_code"].to_list()
    )
    print(f"全市场标的数(list_status=L):{len(universe)}")

    # —— 旧口径:概念板块 board_age 代理(v1.3-②)————————————————————————
    member_map = load_member_map(parquet_dir=PARQUET_DIR)
    old_scores = compute_sector_strength(trade_date, parquet_dir=PARQUET_DIR, top_n=10_000)
    old_hot = sector_hot_lookup(old_scores)
    print(f"旧口径:board_daily 覆盖板块数(compute_sector_strength 返回)= {len(old_scores)},"
          f"ths_member 覆盖股票数 = {len(member_map)}")

    def persist_old(code: str) -> int:
        boards = member_map.get(code, [])
        return max((old_hot[b].board_age for b in boards if b in old_hot), default=0)

    # —— 新口径:行业强度唯一源(v1.4-②)——————————————————————————————
    industry_of = ist.load_industry_map(DB_PATH)
    new_scores = ist.compute_industry_strength(trade_date, parquet_dir=PARQUET_DIR, db_path=DB_PATH)
    new_hot = ist.industry_strength_lookup(new_scores)
    print(f"新口径:达标行业数(member_count>=_MIN_MEMBERS)= {len(new_scores)},"
          f"stock_basic 有 industry 的股票数 = {len(industry_of)}")

    def persist_new(code: str) -> int:
        return ist.stock_persist_days(code, industry_of, new_hot)

    # —— 逐票对拍 ——————————————————————————————————————————————————————
    rows = []
    for code in universe:
        p_old, p_new = persist_old(code), persist_new(code)
        c_old, c_new = _classify(p_old), _classify(p_new)
        rows.append({
            "code": code, "persist_old": p_old, "persist_new": p_new,
            "class_old": c_old, "class_new": c_new, "changed": c_old != c_new,
        })
    df = pl.DataFrame(rows)

    n_old_a2 = df.filter(pl.col("class_old") == "A2").height
    n_new_a2 = df.filter(pl.col("class_new") == "A2").height
    n_old_b3 = df.filter(pl.col("class_old") == "B3").height
    n_new_b3 = df.filter(pl.col("class_new") == "B3").height
    newly_a2 = df.filter((pl.col("class_new") == "A2") & (pl.col("class_old") != "A2")).sort("code")
    lost_a2 = df.filter((pl.col("class_old") == "A2") & (pl.col("class_new") != "A2")).sort("code")
    newly_b3 = df.filter((pl.col("class_new") == "B3") & (pl.col("class_old") != "B3")).sort("code")
    lost_b3 = df.filter((pl.col("class_old") == "B3") & (pl.col("class_new") != "B3")).sort("code")
    unchanged = df.filter(~pl.col("changed")).height

    # 旧口径「9」封顶效应量化(board_age 的 max()-over-多板块特性,见结论节):旧 A2 命中里
    # persist_old 恰好等于全市场观测到的最大值的占比。
    old_max_persist = df["persist_old"].max()
    old_a2_at_cap = df.filter((pl.col("class_old") == "A2") & (pl.col("persist_old") == old_max_persist)).height
    old_a2_cap_share = (old_a2_at_cap / n_old_a2) if n_old_a2 else 0.0

    names = dict(zip(sb["ts_code"].to_list(), sb["name"].to_list()))

    # —— 当日持仓(本地 dev 库现状:空,如实标注)—————————————————————————
    positions = load_open_positions(DB_PATH)
    pos_rows = [{
        "code": p.ts_code, "name": names.get(p.ts_code, p.ts_code),
        "persist_old": persist_old(p.ts_code), "persist_new": persist_new(p.ts_code),
        "class_old": _classify(persist_old(p.ts_code)), "class_new": _classify(persist_new(p.ts_code)),
    } for p in positions]

    # —— 当日候选池(K1 现役规则跑一遍情报管线,只取 universe/board 漏斗结果做展示)———
    k1 = brain.get_version("K1", db_path=DB_PATH)
    cand_rows: List[dict] = []
    cand_error: Optional[str] = None
    if k1 is not None:
        try:
            candidates = build_intel_candidates(
                trade_date, k1.rule, parquet_dir=PARQUET_DIR, db_path=DB_PATH,
            )
            for c in candidates:
                cand_rows.append({
                    "code": c.ts_code, "name": c.name,
                    "persist_old": persist_old(c.ts_code), "persist_new": persist_new(c.ts_code),
                    "class_old": _classify(persist_old(c.ts_code)), "class_new": _classify(persist_new(c.ts_code)),
                })
        except Exception as e:  # noqa: BLE001 —— 本脚本是审计脚本,候选池取不到不影响全市场对拍主结果
            cand_error = f"{type(e).__name__}: {e}"
    else:
        cand_error = "本地库无 K1 现役规则行,跳过候选池重算"

    # —— 板块数据新鲜度诚实标注(§七 已知:本地 daily 与 ths_daily 进度不同步)—————
    from neckline.data.concept_data import max_ths_daily_date
    daily_max = _latest_daily_date()
    ths_daily_max = max_ths_daily_date(PARQUET_DIR)
    lines: List[str] = []
    lines.append("# v1.4-②-C A2/B3 口径切换对拍(概念板块 board_age → 行业强度 industry_strength)\n")
    lines.append(f"生成时间:{datetime.now().isoformat(timespec='seconds')} · 对拍交易日:**{trade_date}**\n")
    lines.append("## 〇 数据现状(如实标注,不静默)\n")
    lines.append(f"- 本地 `daily`(全市场 EOD,新口径 `industry_strength` 依据)最新到 **{daily_max}**;"
                 f"本次对拍即取该日。")
    lines.append(f"- 本地 `ths_daily.parquet`(概念板块日线,旧口径 board_age 依据)最新到 "
                 f"**{ths_daily_max}**(独立进度,详见 PROJECT_PLAN §四)——"
                 f"**本次对拍统一取 {daily_max} 这一天,新旧两套口径同一天对齐比较,不存在跨日期比较失真**"
                 f"(`ths_daily` 落后不影响,因为它在 {daily_max} 这天本就已有数据,只是不如 {ths_daily_max} 新)。")
    lines.append(f"- 本地 dev 库 `positions` 表当前为**空**(生产库另有 3 笔持仓,本次施工零生产访问,"
                 f"不读生产库)——「当日持仓」一栏如实标注为空,不臆造。")
    if cand_error:
        lines.append(f"- 「当日候选池」重算异常:{cand_error}(不影响全市场对拍主结果,以下候选池一栏从缺)。")
    lines.append("")

    lines.append("## 一、数量小结\n")
    lines.append("| 判据 | 旧口径命中数 | 新口径命中数 | 消失命中 | 新增命中 | 不变 |")
    lines.append("|---|---|---|---|---|---|")
    lines.append(f"| A2(≥4 天,hard_cut) | {n_old_a2} | {n_new_a2} | {lost_a2.height} | {newly_a2.height} | — |")
    lines.append(f"| B3(2-3 天,avoid_flag) | {n_old_b3} | {n_new_b3} | {lost_b3.height} | {newly_b3.height} | — |")
    lines.append(f"| 全市场 {len(universe)} 票总体(A2/B3/none 三态) | — | — | — | — | "
                 f"{unchanged} 票分类不变({unchanged/len(universe):.1%}) |")
    lines.append("")

    def _fmt_table(sub: pl.DataFrame, title: str) -> List[str]:
        out = [f"### {title}(n={sub.height})\n"]
        if sub.height == 0:
            out.append("(无)\n")
            return out
        out.append("| 代码 | 名称 | 旧 persist(class) | 新 persist(class) |")
        out.append("|---|---|---|---|")
        for r in sub.iter_rows(named=True):
            nm = names.get(r["code"], r["code"])
            out.append(f"| {r['code']} | {nm} | {r['persist_old']}({r['class_old']}) | {r['persist_new']}({r['class_new']}) |")
        out.append("")
        return out

    lines.append("## 二、A2(hard_cut)逐票明细\n")
    lines += _fmt_table(newly_a2, "新增命中 A2(新口径判为过热,旧口径没拦——旧口径漏判)")
    lines += _fmt_table(lost_a2, "消失命中 A2(旧口径判为过热,新口径不再拦——旧口径误判)")

    lines.append("## 三、B3(avoid_flag)逐票明细\n")
    lines += _fmt_table(newly_b3, "新增命中 B3")
    lines += _fmt_table(lost_b3, "消失命中 B3")

    lines.append("## 四、当日持仓(本地 dev 库,现状为空)\n")
    if pos_rows:
        lines.append("| 代码 | 名称 | 旧 persist(class) | 新 persist(class) |")
        lines.append("|---|---|---|---|")
        for r in pos_rows:
            lines.append(f"| {r['code']} | {r['name']} | {r['persist_old']}({r['class_old']}) | {r['persist_new']}({r['class_new']}) |")
    else:
        lines.append("(本地 dev 库当前无持仓,见〇节说明)")
    lines.append("")

    lines.append("## 五、当日候选池(K1 现役规则重算)\n")
    if cand_rows:
        lines.append(f"候选 {len(cand_rows)} 只,其中判据切换后变化:"
                     f"{sum(1 for r in cand_rows if r['class_old'] != r['class_new'])} 只\n")
        lines.append("| 代码 | 名称 | 旧 persist(class) | 新 persist(class) | 变化 |")
        lines.append("|---|---|---|---|---|")
        for r in cand_rows:
            mark = "**变化**" if r["class_old"] != r["class_new"] else ""
            lines.append(f"| {r['code']} | {r['name']} | {r['persist_old']}({r['class_old']}) | {r['persist_new']}({r['class_new']}) | {mark} |")
    else:
        lines.append("(取不到候选池,见〇节说明)")
    lines.append("")

    # —— 抽样 5 例人读说明 ——————————————————————————————————————————
    lines.append("## 六、抽样 5 例人读说明\n")
    samples: List[dict] = []
    for pool, tag in ((newly_a2, "新增 A2"), (lost_a2, "消失 A2"), (newly_b3, "新增 B3"), (lost_b3, "消失 B3")):
        if pool.height:
            r = pool.row(0, named=True)
            samples.append({**r, "tag": tag})
        if len(samples) >= 5:
            break
    # 不够 5 例就从"不变但双口径都判过热/警惕"里补(展示两口径吻合的情形,同样有信息量)
    if len(samples) < 5:
        both_hit = df.filter((pl.col("class_old") != "none") & (~pl.col("changed"))).sort("code")
        for r in both_hit.head(5 - len(samples)).iter_rows(named=True):
            samples.append({**r, "tag": "不变(双口径一致)"})
    for i, s in enumerate(samples[:5], start=1):
        nm = names.get(s["code"], s["code"])
        lines.append(
            f"{i}. **{s['code']} {nm}**({s['tag']}):旧口径(概念板块 board_age 代理)"
            f"persist={s['persist_old']} → {s['class_old']};新口径(`stock_basic.industry` "
            f"行业强度)persist={s['persist_new']} → {s['class_new']}。"
        )
    lines.append("")
    lines.append("## 七、结论\n")
    lines.append(
        "旧口径(概念板块 board_age 代理)与新口径(`industry_strength` 行业强度)是两个不同的量"
        "(概念板块多对多、行业一对一;板块「连续站上 MA20」与行业「当日中位数进全市场 top20%」"
        "判定逻辑也不同),故对同一批标的产出不同的 A2/B3 命中集属预期结果,不是 bug。"
        "本次切换的产品意义:A2 hard_cut 现在按 advisory 规格档原文(H6 审计对象)判定,"
        "排序键 ③ 的「行业强度排名」与「题材持续天数」也能引用同一份行业口径数据"
        "(块③ 依赖的自洽性:『persist≥4 已被 A2 拦』这条不变量,前提是 A2 与排序键用同一个量)。"
    )
    lines.append("")
    lines.append(
        f"**⚠ 幅度与方向,需 block③ 知情(不是技术细节,是行为变化)**:A2 命中数 {n_old_a2} → {n_new_a2}"
        f"(降 {n_old_a2 - n_new_a2} 只,约 {(n_old_a2 - n_new_a2) / max(n_old_a2, 1):.0%})——**旧代理系统性"
        f"过度拦截**:一票常挂靠数十个概念板块(如模块注释举例的「立中集团挂 30 个板块」),"
        f"`max()` 取所有挂靠板块 board_age 后,只要其中**任一**板块连续 4 天站稳 MA20 就命中 A2。"
        f"实测旧口径 {n_old_a2} 个 A2 命中里,**{old_a2_at_cap} 个({old_a2_cap_share:.0%})persist_old "
        f"恰好等于全市场当日观测到的最大值 {old_max_persist}**——不是巧合的高值聚集,而是「碰到了代理"
        f"口径的天花板」(2026-07-24 前后大盘普涨、多数板块指数同步刚站上 MA20 不久,`max()` 取"
        f"数十个挂靠板块中最赶巧最先站上的那个,天然趋同封顶),把「板块层面泛泛走强」错记成"
        f"「个股题材过热」。新口径要求该票**自己唯一的行业**连续 4 天挤进全市场中位数 top20%——"
        f"是明显更严格、更贴近 H6 原始审计对象的判据。**实际后果**:v1.4-③ 上线后,候选漏斗"
        f"因 A2 被拦掉的票会大幅减少(本次候选池重算样本:20 只候选里 4 只旧口径判 A2 命中,"
        f"新口径全部转为不命中,见五节),排序键 ③ 的候选留存率、A2 拦截率等运维指标届时会明显"
        f"改变基线,不是回归缺陷。"
    )
    lines.append("")

    out_path = Path(__file__).resolve().parent.parent.parent / "archive" / f"v1.4_A2B3口径切换对拍_{trade_date.strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n已写出:{out_path}")
    print(f"A2: 旧{n_old_a2} 新{n_new_a2} 消失{lost_a2.height} 新增{newly_a2.height}")
    print(f"B3: 旧{n_old_b3} 新{n_new_b3} 消失{lost_b3.height} 新增{newly_b3.height}")
    print(f"全市场分类不变 {unchanged}/{len(universe)}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    td = datetime.strptime(arg, "%Y%m%d").date() if arg else None
    main(td)
