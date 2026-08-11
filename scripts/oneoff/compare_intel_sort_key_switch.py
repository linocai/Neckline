"""⛔ **本脚本已跑不起来,仅供阅读**(2026-08-04 契约线审计 🔵 B4 标注)——V2-⑬-1
删除单票候选管线时,它 import 的 `report.intel_candidates` / `report.candidates`
一并物理删除,直接执行会 `ImportError`。**刻意不删、不改回去**:`scripts/oneoff/`
是「已执行完毕的一次性脚本」留档区(项目 CLAUDE.md「跑法」节),留的是**当时那次
对拍是怎么做的**这份方法学证据;要重现结论请看它产出的
`archive/对照表/v1.4_排序键切换对照_20260724.md`。

v1.4-③-G 排序键切换对照:同一交易日,情报候选管线分别用**旧排序键**(v1.3 起「板块
资金流强度 → 题材新鲜度(反用) → 展示分 → 代码」)与**新排序键**(v1.4-③ 三级键「行业
强度排名 → 题材持续天数(升序) → K4 黄牌数(升序) → 展示分 → 代码」,需求 8)各跑一遍,
出 20 只候选的名次对照表(进榜/出榜/名次变化)+ 3 例人读说明,产出
`archive/v1.4_排序键切换对照_<date>.md`。

**方法**:两次调用共用**完全相同**的候选生成管线(板块层→个股层→K4安检 三步逻辑一字
不改,universe/hard_cut 拦截结果两次调用**必然一致**),仅**临时替换** `_sort_key` 比较器
(monkeypatch 模块属性,同 `tests/test_intel_candidates.py::test_sector_moneyflow_failure_
degrades_not_crashes` monkeypatch `compute_sector_moneyflow` 的既有姿势)——这是唯一能
让「谁进 20 只/谁被挤出」这类**因排序换位而连锁改变保底与竞争两 pass 结果**的效应被真实
复现的方法(不能只在事后对同一份候选重新排序,那样量不出保底/竞争 pass 的连锁反应)。

**A2 拦截率变化的说明(读者常见困惑,单独澄清)**:A2/hard_cut 拦截发生在**排序之前**的
K4 安检步骤,不受 `_sort_key` 影响——375→11 的降幅是 **v1.4-②**(行业强度唯一源切换)
已完工的效应,记录在先前的 `archive/对照表/v1.4_A2B3口径切换对拍_20260724.md`。本脚本两次调用
读的是**同一份(已切②口径)现役代码**,只换最后一步比较器,故两次运行的 hard_cut 拦截
结果逐位相同——下方「进榜/出榜」差异**纯粹**来自排序换位在保底/竞争两 pass 里的连锁反应,
与 A2 拦截率无关(不要把②已完工的效应误记成本次③引入的新变化)。

**只读本地开发库/parquet(零生产访问、零写库)**——纯审计脚本,跑完即完工,产出物是本次
改动的活体证据(plan §五 v1.4-③-G 硬要求)。

用法:`python scripts/oneoff/compare_intel_sort_key_switch.py [YYYYMMDD]`
(缺省取本地 `daily` 表最新交易日)。
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.data.market_data import table_dir  # noqa: E402
from neckline.report import intel_candidates as ic  # noqa: E402
from neckline.report.candidates import Candidate  # noqa: E402
from neckline.strategy import brain  # noqa: E402

DB_PATH = settings.db_path
PARQUET_DIR = settings.parquet_dir

# —— 旧排序键(v1.3 起「板块资金流强度 → 题材新鲜度反用 → 展示分 → 代码」,v1.4-③ 前的
#    生产公式,原地址 `intel_candidates._sort_key` 改版前实现;此处复刻仅供本对拍脚本
#    monkeypatch 用,不改动现役模块)——————————————————————————————————————————
_OLD_THEME_FRESHNESS = {1: 3, 2: 2, 3: 1}


def _old_theme_freshness_score(persist_days: int) -> int:
    return _OLD_THEME_FRESHNESS.get(persist_days, 0)


def _old_sort_key(e: dict) -> tuple:
    sf = e["sector_flow"] if e["sector_flow"] is not None else float("-inf")
    freshness = _old_theme_freshness_score(e["industry_persist_days"])
    return (-sf, -freshness, -e["base_score"], e["code"])


def _latest_daily_date() -> date:
    d = table_dir("daily", PARQUET_DIR)
    files = sorted(d.glob("year=*/*.parquet"))
    assert files, "本地 daily 表为空,无法确定对拍日"
    return max(datetime.strptime(f.stem, "%Y%m%d").date() for f in files)


def _run(trade_date: date, rule: dict, sort_key) -> List[Candidate]:
    """跑一遍候选情报管线,`sort_key` 临时替换 `ic._sort_key`(跑完立即恢复原值,
    避免脚本进程内状态泄漏到其他调用)。"""
    original = ic._sort_key
    ic._sort_key = sort_key
    try:
        return ic.build_intel_candidates(trade_date, rule, parquet_dir=PARQUET_DIR, db_path=DB_PATH)
    finally:
        ic._sort_key = original


def main(trade_date: Optional[date] = None) -> None:
    trade_date = trade_date or _latest_daily_date()
    print(f"对拍交易日:{trade_date}")

    k1 = brain.get_version("K1", db_path=DB_PATH)
    assert k1 is not None, "本地库无 K1 现役规则行,无法重算候选池"

    old_cands = _run(trade_date, k1.rule, _old_sort_key)
    new_cands = _run(trade_date, k1.rule, ic._sort_key)   # 现役(新)排序键,原样调用不替换

    old_rank = {c.ts_code: c.rank for c in old_cands}
    new_rank = {c.ts_code: c.rank for c in new_cands}
    old_source = {c.ts_code: c.intel_rank.get("source", "") for c in old_cands}
    new_source = {c.ts_code: c.intel_rank.get("source", "") for c in new_cands}
    new_by_code: Dict[str, Candidate] = {c.ts_code: c for c in new_cands}
    old_by_code: Dict[str, Candidate] = {c.ts_code: c for c in old_cands}
    names = {**{c.ts_code: c.name for c in old_cands}, **{c.ts_code: c.name for c in new_cands}}

    all_codes = sorted(set(old_rank) | set(new_rank))
    entered = [c for c in all_codes if c not in old_rank and c in new_rank]     # 出现在新排序、旧排序里没有(进榜)
    exited = [c for c in all_codes if c in old_rank and c not in new_rank]      # 出现在旧排序、新排序里没有(出榜)
    both = [c for c in all_codes if c in old_rank and c in new_rank]
    moved = [c for c in both if old_rank[c] != new_rank[c]]
    unchanged_rank = [c for c in both if old_rank[c] == new_rank[c]]

    print(f"旧排序 {len(old_cands)} 只,新排序 {len(new_cands)} 只")
    print(f"进榜 {len(entered)},出榜 {len(exited)},两榜都在但名次变化 {len(moved)},名次不变 {len(unchanged_rank)}")

    lines: List[str] = []
    lines.append("# v1.4-③-G 正选漏斗排序键切换对照(板块资金流优先 → 行业强度排名三级键)\n")
    lines.append(f"生成时间:{datetime.now().isoformat(timespec='seconds')} · 对拍交易日:**{trade_date}**\n")

    lines.append("## 〇 方法与数据现状(如实标注,不静默)\n")
    lines.append(
        "- 两次调用共用**完全相同**的候选生成管线代码(板块层→个股层→K4 安检三步逻辑一字"
        "不改,universe/hard_cut 拦截结果两次调用**必然一致**),仅临时替换 `_sort_key` 比较器"
        "(monkeypatch,跑完即恢复)。"
    )
    lines.append(
        "- **旧排序键复刻**(`(-sector_flow, -theme_freshness(persist), -base_score, code)`,"
        "v1.3 起生效、v1.4-③ 前的生产公式)与**新排序键**(`(industry_rank ASC, "
        "industry_persist_days ASC, yellow_card_count ASC, -base_score, code)`,本块 v1.4-③-A"
        "定死)均读同一份 `industry_strength`(v1.4-② 已切换的唯一源)——两次运行的"
        "`industry_persist_days` 输入完全相同,差异只在排序公式本身。"
    )
    lines.append(
        "- **A2/hard_cut 拦截率与本次对照无关**:A2 拦截发生在排序**之前**的 K4 安检步骤,"
        "两次调用读同一份现役代码,拦截结果逐位相同。375→11 的降幅是 **v1.4-②** 已完工的"
        "效应(见 `archive/对照表/v1.4_A2B3口径切换对拍_20260724.md`),下方进榜/出榜差异**纯粹**"
        "来自排序换位在保底/竞争两 pass 里的连锁反应,与 A2 拦截率无关——不要把②已完工的"
        "效应误记成本次③引入的新变化。"
    )
    lines.append(f"- 本地 `daily`(全市场 EOD)最新到 **{trade_date}**;本次对拍即取该日。K1 现役规则重算。")
    lines.append("")

    lines.append("## 一、数量小结\n")
    lines.append("| 项目 | 数量 |")
    lines.append("|---|---|")
    lines.append(f"| 旧排序候选数 | {len(old_cands)} |")
    lines.append(f"| 新排序候选数 | {len(new_cands)} |")
    lines.append(f"| 进榜(新排序独有) | {len(entered)} |")
    lines.append(f"| 出榜(旧排序独有) | {len(exited)} |")
    lines.append(f"| 两榜都在 · 名次变化 | {len(moved)} |")
    lines.append(f"| 两榜都在 · 名次不变 | {len(unchanged_rank)} |")
    lines.append("")

    lines.append("## 二、20 只名次对照表\n")
    lines.append("| 代码 | 名称 | 旧名次 | 旧来源 | 新名次 | 新来源 | 状态 | 新行业排名 | 新题材天数 | 新黄牌数 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for code in all_codes:
        nm = names.get(code, code)
        r_old = old_rank.get(code, "-")
        r_new = new_rank.get(code, "-")
        s_old = old_source.get(code, "-")
        s_new = new_source.get(code, "-")
        if code in entered:
            status = "**进榜**"
        elif code in exited:
            status = "**出榜**"
        elif code in moved:
            status = f"名次变化({r_old}→{r_new})"
        else:
            status = "不变"
        nc = new_by_code.get(code)
        ir = nc.intel_rank if nc is not None else {}
        irank = ir.get("industryRank", "-")
        ipersist = ir.get("industryPersistDays", "-")
        iyellow = ir.get("yellowCardCount", "-")
        lines.append(f"| {code} | {nm} | {r_old} | {s_old} | {r_new} | {s_new} | {status} | "
                     f"{irank} | {ipersist} | {iyellow} |")
    lines.append("")

    lines.append("## 三、3 例人读说明\n")
    samples: List[dict] = []
    for code in entered[:1]:
        samples.append({"code": code, "tag": "进榜(新排序独有)"})
    for code in exited[:1]:
        samples.append({"code": code, "tag": "出榜(旧排序独有)"})
    for code in moved[:1]:
        samples.append({"code": code, "tag": "名次变化"})
    # 不够 3 例就从「名次不变」里补(展示两键吻合的情形,同样有信息量)
    for code in unchanged_rank:
        if len(samples) >= 3:
            break
        samples.append({"code": code, "tag": "名次不变"})
    def _side_desc(code: str, rank_map: dict, source_map: dict, ir: dict, *, old: bool) -> str:
        if code not in rank_map:
            return "未入选 20 只"
        if old:
            return (f"第 {rank_map[code]} 位(来源 {source_map.get(code, '-')},依据板块资金流 "
                    f"{ir.get('sectorFlow', '无数据')} 万元 · 题材天数 {ir.get('themePersistDays', '-')})")
        return (f"第 {rank_map[code]} 位(来源 {source_map.get(code, '-')},依据行业强度排名 "
                f"{ir.get('industryRank', '未参与排名')} · 题材持续天数 {ir.get('industryPersistDays', '-')} · "
                f"K4 黄牌 {ir.get('yellowCardCount', '-')} 个;板块资金流 {ir.get('sectorFlow', '无数据')} 万元"
                f"现只作并列展示)")

    for i, s in enumerate(samples[:3], start=1):
        code = s["code"]
        nm = names.get(code, code)
        nc, oc = new_by_code.get(code), old_by_code.get(code)
        nir = nc.intel_rank if nc is not None else {}
        oir = oc.intel_rank if oc is not None else {}
        old_desc = _side_desc(code, old_rank, old_source, oir, old=True)
        new_desc = _side_desc(code, new_rank, new_source, nir, old=False)
        lines.append(f"{i}. **{code} {nm}**({s['tag']}):旧排序{old_desc} → 新排序{new_desc}。")
    lines.append("")

    lines.append("## 四、结论\n")
    lines.append(
        "旧排序键(板块资金流强度优先)与新排序键(行业强度排名优先,需求 8)是两套完全不同的"
        "优先级公式,产出不同的 20 只候选与名次属预期结果,不是 bug。**语义红线重申**:两套"
        "排序都只是**注意力优先级**,不是收益预测,排第一不等于最会涨,终选权始终在用户——"
        "本次切换不改变这条纪律,只改变「哪些票更值得优先看」的判断依据(从「板块资金是否在"
        "流入」换成「所属行业强度是否靠前 + 题材是否新鲜 + 风险标注是否干净」,后者是需求 8"
        "明写的「只用审计过方向的量」,前者的资金流方向从未经过实测审计)。"
    )
    lines.append("")
    lines.append(
        f"**规模**:{len(entered)} 只进榜、{len(exited)} 只出榜(占 20 只候选的 "
        f"{(len(entered) + len(exited)) / 2 / max(len(new_cands), 1):.0%});"
        f"两榜都在的 {len(both)} 只里 {len(moved)} 只发生名次变化。进出榜与名次变化主要发生在"
        "**竞争 pass**(常驻板块保底 pass 里,若某常驻板块合格候选本就不超过 2 只名额,换排序键"
        "不影响其保底结果;当合格候选超过 2 只名额时,换排序键会改变保底 2 只的具体人选,连带"
        "改变退回竞争池的候选构成)。"
    )
    lines.append("")

    out_path = (
        Path(__file__).resolve().parent.parent.parent / "archive"
        / f"v1.4_排序键切换对照_{trade_date.strftime('%Y%m%d')}.md"
    )
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n已写出:{out_path}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    td = datetime.strptime(arg, "%Y%m%d").date() if arg else None
    main(td)
