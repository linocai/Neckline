"""D1 集合竞价确认层的**周度机械聚合**(V2.3.3-⑥-B;K8.md §二十 末段)。

K8 原文:「**周度按行情状态、T 等级、引擎和版本聚合**,重点复核错误确认、错误否决、
数据冲突和边界样本。」→ 本模块出一张**四维交叉表**,单元格 = 六个复盘标签的计数 + 占比。

🔴 **零 LLM、零新指标、零新阈值、零写库**(§五 ⑥-B):
    · 六个标签**不在这里判** —— 它们是 D1 收盘时由 `review/selection_clock.py` 的
      **第十项** `auction_review` 判好、冻进 `selection_clock.mech_json` 的。本模块
      只**数**它们(⛔ 不重判、⛔ 不回读 `auction_verdicts` 再判一次:那会让同一个量
      有两个算法,老病)。
    · 「D1 结果对不对」用的是既有 `tier_accuracy` 四态(源
      `selection/verification_rules.STATE_SCORES`)—— **一行判分逻辑都不写**。
    · 30 / 80 两条分界线读 `eval/iteration.IterationThresholds`(唯一源在骨架包的
      `config.iteration`)—— ⛔ 读不到就**如实说没拍板**,不设默认值。
    · 引擎两维直接复用 `eval/iteration.stratum_of()`(老样本落 `LEGACY` 的退回逻辑
      因此与迭代段**逐字一致**,⛔ 不另写一份)。

🔴 **样本单位 = `D0 日期 × 篮子 × 引擎版本`**(K8 §二十 末段沿用 §十七):
`selection_clock` 的 `basket_id UNIQUE` 就是它的物理承载 —— **一行 = 一个样本**。
⛔ 别改成按票数或按结论条数数:那会让"样本量"在不改任何统计代码的情况下悄悄变成
另一个量,而 30 / 80 是按**篮子数**拍板的。

🔴 **⛔ 零自动回写**(V2.1 裁定 #3 / K8 §二十 末段「达到样本门槛不会自动修改 K8」):
本模块产出的是**观察**,不是动作 —— 不改 K8、不改包、不改任何阈值。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.eval.iteration import (
    LEGACY_ENGINE,
    IterationThresholds,
    stratum_of,
)
from neckline.review.selection_clock import (
    AUCTION_LABEL_DATA_MISSING,
    AUCTION_LABELS,
    AUCTION_UNDETERMINED_D1_UNCLEAR,
    AUCTION_UNDETERMINED_NO_ROW,
    AUCTION_UNDETERMINED_PENDING,
)

logger = logging.getLogger(__name__)

#: 产物形状版本(形状变了就 bump;⛔ 与 `CLOCK_MECH_SPEC_VERSION` 是两回事)。
AUCTION_EVAL_SPEC_VERSION = "auction_eval_v1"

#: 样本量闸的三档(**不是新阈值** —— 两条线都读骨架包的 `config.iteration`)。
GATE_OBSERVE_ONLY = "observe_only"            # n < min_n:只输出观察,样本不足
GATE_MAY_ADJUST = "may_suggest_adjust"        # min_n ≤ n < retire_min_n:可提保留/观察/降权
GATE_MAY_RETIRE = "may_suggest_retire"        # n ≥ retire_min_n:才可提淘汰
GATE_UNDECIDED = "thresholds_undecided"       # 两条线还没人拍板 → ⛔ 一个建议都不提
GATE_ORDER: Tuple[str, ...] = (GATE_OBSERVE_ONLY, GATE_MAY_ADJUST, GATE_MAY_RETIRE,
                               GATE_UNDECIDED)

#: `regime_at_d0` 为空时的哨兵串(D0 当天 `market_regime_daily` 缺行)。
#: ⚠ 它**不是**第四种行情状态,与 `eval/iteration._REGIME_UNKNOWN` 同值同义。
REGIME_UNKNOWN = "(未登记)"

#: `covered_tier` 缺失时的**等级维**哨兵。
#: ⚠ 原先这里借用了 `LEGACY_ENGINE`(复审 🔵-13):**引擎维的哨兵串跑到了等级维**,
#: 读表的人会以为那一格在讲引擎。⛔ 别复用别的维度的哨兵。
TIER_UNKNOWN = "T?"

#: K8 §二十 末段点名要「重点复核」的两个标签 —— 产物里单独拎出来,免得埋在六个计数里。
FOCUS_LABELS: Tuple[str, ...] = ("wrong_confirm", "wrong_veto")


def _auction_item(closure: Mapping[str, Any]) -> Dict[str, Any]:
    v = (closure.get("mech") or {}).get("auction_review")
    return dict(v) if isinstance(v, Mapping) else {}


def label_of(closure: Mapping[str, Any]) -> str:
    """一份结案件的竞价复盘标签。

    ⚠ **老结案件(V2.3.3 之前冻的)压根没有这个键** —— 那不是「数据缺失」这个业务
    结论,而是「那一版还没有竞价层」。两者在这里**刻意合并成 `data_missing`**,
    但**分因不同**:`undetermined_reason_of()` 会返回 `no_auction_row`,而产物里另有
    `withoutAuctionItem` 计数把「连这一项都没有的老样本」单独摆出来(⛔ 不让它们
    混进"竞价层跑了没覆盖到"那一类)。
    """
    item = _auction_item(closure)
    label = item.get("label")
    return str(label) if label in AUCTION_LABELS else AUCTION_LABEL_DATA_MISSING


def undetermined_reason_of(closure: Mapping[str, Any]) -> Optional[str]:
    """`data_missing` 的分因(§七 P0-39 同款纪律:「竞价层没跑」与「D1 结果没判出来」
    是两种相反的成因,⛔ 不许混成一个标签)。非 `data_missing` → `None`。"""
    if label_of(closure) != AUCTION_LABEL_DATA_MISSING:
        return None
    reason = _auction_item(closure).get("undetermined_reason")
    if reason in (AUCTION_UNDETERMINED_NO_ROW, AUCTION_UNDETERMINED_PENDING,
                  AUCTION_UNDETERMINED_D1_UNCLEAR):
        return str(reason)
    return AUCTION_UNDETERMINED_NO_ROW


def cell_key_of(closure: Mapping[str, Any]) -> Tuple[str, str, str, str, str]:
    """K8 §二十 的聚合维:`(行情状态, T 等级, **骨架版本**, 引擎线, 引擎版本)`。

    🔴 **骨架版本这一维是必须的**(复审 🟡-4):升 `K8-V0.6` → `K8-V0.7` 的**唯一理由**
    就是「竞价层上线**前后**的选股时钟样本必须分得开,否则周度按版本归因会把两个不同
    的系统混成一层」(施工图 ⑦-2 逐字)。而 `stratum_of()` 的第一位正是它 ——
    丢掉它,`K8-V0.6` 时代(没有竞价层)与 `K8-V0.7` 时代(有竞价层)的同一
    `(行情状态, T1, C, C1)` 样本会落进**同一个单元格**、共用一个 `n`、共用一条
    30/80 样本量闸,那条升版本的理由就白升了。
    ⚠ `withoutAuctionItem` 是个**全局**计数、不进单元格 —— 它分不出哪一层被稀释了,
    ⛔ 别拿它当替代品。

    ⚠ 骨架 / 引擎三维**复用** `eval/iteration.stratum_of()` 的第 1/2/3 位 —— 老样本落
    `LEGACY` 的退回逻辑因此与迭代段逐字一致(⛔ 不另写一份)。
    """
    skeleton, engine_code, engine_version, _ruleset = stratum_of(closure)
    regime = closure.get("regime_at_d0")
    tier = closure.get("covered_tier")
    return (str(regime) if regime else REGIME_UNKNOWN,
            f"T{int(tier)}" if isinstance(tier, int) and tier else TIER_UNKNOWN,
            skeleton, engine_code, engine_version)


def _counts(closures: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    out = {k: 0 for k in AUCTION_LABELS}
    for c in closures:
        out[label_of(c)] += 1
    return out


def _shares(counts: Mapping[str, int], n: int) -> Dict[str, float]:
    """占比。⚠ 分母是**该层全部样本**(含 `data_missing`)—— 把缺失剔出分母会让
    「我们看见了多少」看起来比实际大(§3.8 诚实披露)。`n == 0` → 空 dict,
    ⛔ 不发一堆 0.0 冒充"都是零"。"""
    if n <= 0:
        return {}
    return {k: round(int(counts.get(k, 0)) / n, 4) for k in AUCTION_LABELS}


def gate_of(n: int, thresholds: Optional[IterationThresholds]) -> str:
    """样本量闸(**零新阈值**:两条线全部来自骨架包的 `config.iteration`)。

    🔴 拍板前(`thresholds is None`)一律 `thresholds_undecided` —— ⛔ 不许"临时用
    30/80 顶一下":那正是「定性需求不许自行定量」禁的事。
    """
    if thresholds is None:
        return GATE_UNDECIDED
    if n < thresholds.min_n:
        return GATE_OBSERVE_ONLY
    if n < thresholds.retire_min_n:
        return GATE_MAY_ADJUST
    return GATE_MAY_RETIRE


_GATE_NOTES: Dict[str, str] = {
    GATE_OBSERVE_ONLY: "样本不足,本层**只输出观察**(⛔ 不提任何调整建议)。",
    GATE_MAY_ADJUST: "样本达到第一条线:可提**保留 / 观察 / 降权**;⛔ 还不到提淘汰的量。",
    GATE_MAY_RETIRE: "样本达到第二条线:**才可**提淘汰 —— 仍然只是建议,"
                     "⛔ 系统不自动改 K8、不改包、不改任何阈值。",
    GATE_UNDECIDED: "30 / 80 两条分界线**还没人拍板**(骨架包 `config.iteration` 未配置)"
                    " —— ⛔ 本层不提任何建议,也不临时用一个默认值顶替。",
}


def _cell(closures: Sequence[Mapping[str, Any]],
          thresholds: Optional[IterationThresholds]) -> Dict[str, Any]:
    n = len(closures)
    counts = _counts(closures)
    gate = gate_of(n, thresholds)
    undetermined: Dict[str, int] = {}
    for c in closures:
        r = undetermined_reason_of(c)
        if r:
            undetermined[r] = undetermined.get(r, 0) + 1
    return {
        "n": n,
        "counts": counts,
        "shares": _shares(counts, n),
        # K8 §二十 末段点名的「重点复核」两类,单独拎出来(⛔ 别埋在六个计数里)。
        "focus": {k: int(counts.get(k, 0)) for k in FOCUS_LABELS},
        "undeterminedReasons": undetermined,
        "gate": gate,
        "gateNote": _GATE_NOTES[gate],
    }


def build_auction_report(
    closures: Sequence[Mapping[str, Any]],
    *,
    thresholds: Optional[IterationThresholds] = None,
    threshold_problems: Sequence[str] = (),
) -> Dict[str, Any]:
    """周度竞价聚合的一份产物(**纯字典、零写库**)。

    入参 `closures` = `review/selection_clock.list_closures(...)` 的原样产物
    (**一行一个样本**)。⛔ 本函数不读库、不重判标签 —— 标签是 D1 收盘那一刻冻的。
    """
    cells: Dict[Tuple[str, str, str, str, str], List[Mapping[str, Any]]] = {}
    without_item = 0
    for c in closures:
        if not _auction_item(c):
            # 老结案件(V2.3.3 之前)连这一项都没有 —— **单独计数**,⛔ 不混进
            # 「竞价层跑了但没覆盖到这一篮」那一类(两者成因完全不同)。
            without_item += 1
        cells.setdefault(cell_key_of(c), []).append(c)

    by_cell = [
        {"regime": k[0], "tier": k[1], "skeletonVersion": k[2],
         "engineCode": k[3], "engineVersion": k[4],
         **_cell(v, thresholds)}
        for k, v in sorted(cells.items())
    ]
    return {
        "specVersion": AUCTION_EVAL_SPEC_VERSION,
        "source": "K8.md §二十(周度按行情状态、T 等级、引擎和版本聚合)",
        "sampleUnit": "D0 日期 × 篮子 × 引擎版本(selection_clock 一行 = 一个样本)",
        "labels": list(AUCTION_LABELS),
        # 🔴 `skeletonVersion` 在里面(复审 🟡-4):竞价层上线前后的样本必须分得开。
        "cellKey": ["regime", "tier", "skeletonVersion", "engineCode", "engineVersion"],
        "byCell": by_cell,
        "overall": _cell(list(closures), thresholds),
        "withoutAuctionItem": without_item,
        "withoutAuctionItemNote": (
            "这些结案件是 V2.3.3 之前冻的,**那一版还没有竞价确认层** —— "
            "它不是「竞价层当天没跑」,更不是策略失败,故单独计数。"
        ),
        "thresholds": (
            {"available": True, "minN": thresholds.min_n,
             "retireMinN": thresholds.retire_min_n, "provenance": thresholds.provenance}
            if thresholds is not None else
            {"available": False,
             "unavailableReason": (
                 "骨架包 `config.iteration` 未配置样本分界线 —— K8 只给定性描述,"
                 "这两个数必须由用户拍板后经四道闸进包;⛔ 系统不设默认值。"),
             "problems": list(threshold_problems)}
        ),
        "disclaimer": (
            "竞价复盘是**回看审计**:三段(D0 原始篮子 / 9:26—9:29 竞价结论 / D1 收盘结果)"
            "**互不回写**,聚合结果⛔ 不改 K8、不改选股包、不改任何阈值,也不进任何在线判据。"
            "中性结论**无对错**,故 `neutral_sample` 不进「正确率」这类比率的分子分母。"
        ),
    }


def build_auction_section(
    date_from: str,
    date_to: str,
    *,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """周度校准产物里的 `auction` 段(**只读**:读已结案的选股时钟 + 现役骨架包)。

    窗口按 **D0** 取,与迭代段 / 分层成绩单同一个区间口径。
    """
    from neckline.eval.iteration import resolve_thresholds
    from neckline.review.selection_clock import list_closures

    closures = list_closures(date_from, date_to, db_path=db_path)
    thresholds, problems = resolve_thresholds(db_path=db_path)
    return build_auction_report(closures, thresholds=thresholds, threshold_problems=problems)


def render_auction_section(section: Optional[Mapping[str, Any]]) -> List[str]:
    """markdown 片段(周度校准报告的一节)。**空段 = 本期没跑到 / 算不出**,
    ⛔ 不拿空段冒充"本期没有竞价样本"。"""
    if not section:
        return ["## 竞价确认层复盘", "",
                "本期这一段**没有产出**(没跑到 / 算不出)—— ⛔ 不等于「本期没有竞价样本」。", ""]
    out: List[str] = ["## 竞价确认层复盘(K8 §二十)", ""]
    overall = section.get("overall") or {}
    counts = overall.get("counts") or {}
    out.append(f"- 样本 **{overall.get('n', 0)}** 个(单位:{section.get('sampleUnit')})")
    out.append("- 六标签:" + " · ".join(f"{k} {counts.get(k, 0)}" for k in AUCTION_LABELS))
    focus = overall.get("focus") or {}
    out.append(f"- 重点复核:错误确认 {focus.get('wrong_confirm', 0)}、"
               f"错误否决 {focus.get('wrong_veto', 0)}")
    out.append(f"- 样本量闸:{overall.get('gateNote')}")
    if section.get("withoutAuctionItem"):
        out.append(f"- 其中 {section['withoutAuctionItem']} 个是 V2.3.3 之前的老结案件"
                   f"(那一版还没有竞价确认层)")
    out.append("")
    rows = section.get("byCell") or []
    if rows:
        # ⚠ 骨架版本单独一列(复审 🟡-4):没有它,V0.6 与 V0.7 的样本会挤在同一行。
        out.append("| 行情状态 | 等级 | 骨架 | 引擎 | 版本 | n | 正确确认 | 错误确认 | 中性 | 正确否决 | 错误否决 | 数据缺失 | 样本量闸 |")
        out.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
        for r in rows:
            c = r.get("counts") or {}
            out.append(
                f"| {r.get('regime')} | {r.get('tier')} | {r.get('skeletonVersion')} | "
                f"{r.get('engineCode')} | "
                f"{r.get('engineVersion')} | {r.get('n')} | "
                f"{c.get('correct_confirm', 0)} | {c.get('wrong_confirm', 0)} | "
                f"{c.get('neutral_sample', 0)} | {c.get('correct_veto', 0)} | "
                f"{c.get('wrong_veto', 0)} | {c.get('data_missing', 0)} | {r.get('gate')} |"
            )
        out.append("")
    out.append(str(section.get("disclaimer") or ""))
    out.append("")
    return out


__all__ = [
    "AUCTION_EVAL_SPEC_VERSION",
    "GATE_OBSERVE_ONLY", "GATE_MAY_ADJUST", "GATE_MAY_RETIRE", "GATE_UNDECIDED", "GATE_ORDER",
    "REGIME_UNKNOWN", "TIER_UNKNOWN", "FOCUS_LABELS",
    "label_of", "undetermined_reason_of", "cell_key_of", "gate_of",
    "build_auction_report", "build_auction_section", "render_auction_section",
]
