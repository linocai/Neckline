"""待定阈值的通过率报告(plan §五 V2.3.2-①-E;策略线裁定 4 的五项)。

**回答的问题**:那些在 V2.3.2 里退出机械硬否决、降为证据输入的市场关 / 板块关阈值,
「若仍按硬门跑」会拦掉多少候选?这是裁定 6「恢复硬否决的七项提交」里第 5 / 6 项
(单关通过率 / 联合通过率)唯一的数据来源。

**五项**(裁定 4 逐条):
  1. 每条规则的单关通过率;2. 市场关与板块关的**联合**通过率(两关 AND);
  3. C / Z / Y 各引擎结果;4. 三种行情状态下的结果;5. 对最终 T1/T2 数量的影响。

🔴 **分母写死 = 「进入市场关、板块关之前的召回候选或篮子」**(裁定 3 明令)——
即当日喂进 `gates.evaluate_day` 的全体候选,**含后来被硬门拒掉的、含最终判 OUT 的**。
⛔ **绝不许拿"最终 T1/T2 的历史快照"当分母**(那会把通过率算成一个必然好看的数)。
落地方式:`threshold_shadow_evals` 的行**本来就是对全体进关候选写的**(见
`selection/threshold_shadow.py`),所以「按该表统计」天然等于这个分母。

🔴 **零写库**:本模块只读 `threshold_shadow_evals`,不写任何表、不回写任何正式结论
(裁定 5:历史影子结果不得回写当时的正式选股结论)。

🔴 **本模块不替用户判"样本够不够"**:样本量如实列出(`evaluable` / `applicable` /
`candidates` 三个计数都在),⛔ 不设"最小样本量"这种没人拍板过的数;分母为 0 时
如实标「样本不足,不出通过率结论」而不是给一个看起来能用的百分数(§七 P3-59)。

⚠ **与 OUT 研究影子对照(`review/out_shadow.py`)是两件完全不同的事,⛔ 不许混名**:
本模块问「这条阈值该不该恢复成硬门」,那个问「被判 OUT 的票是不是被错杀」。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.selection.gates import NOT_APPLICABLE_PREFIX
from neckline.selection.threshold_shadow import load_threshold_shadow

logger = logging.getLogger(__name__)

SPEC_VERSION = "threshold_calibration_v1"

# 裁定 4 的判读口径(⛔ **工程侧不许改写这三句**,原样进产物文案)。
BAND_UNACCEPTABLE = 0.10
BAND_SAMPLE_OK = 0.20
BAND_RULES_TEXT = (
    "联合通过率 <10% = 明显过严,不可接受;10%–20% = 保持证据输入,不允许机械硬否决;"
    "≥20% = 通过**样本可用性检查**,可继续评价有效性,**不代表规则已经有效**。"
)

# 裁定 3 + §七 P3-59:现有 14 个历史 D0、零个 T1 的定性结论(⛔ 原样写进产物)。
HISTORICAL_D0_DISCLAIMER = (
    "现有 14 个历史 D0、零个 T1 的回放结果,**只能作为联合门槛过严的诊断证据,"
    "不足以支持任何待定阈值恢复机械硬否决**。"
)

DISCLAIMER = (
    "本报告只出读数,⛔ 不构成任何阈值恢复机械硬否决的依据。恢复的唯一通道 = "
    "策略线裁定 6 的七项提交 → 用户确认 → 在**新引擎版本**里把该叶子写成 "
    "`source: audited`(零自动升级)。⚠ 样本够不够由你判断:本报告如实列出样本量,"
    "⛔ 不设「最小样本量」这种没人拍板过的数。"
)


def _is_not_applicable(row: Mapping[str, Any]) -> bool:
    return str(row.get("unavailable_reason") or "").startswith(NOT_APPLICABLE_PREFIX)


def _rate(hits: int, denom: int) -> Optional[float]:
    """分母为 0 → `None`(= 样本不足,⛔ 不给 0.0 冒充一个"通过率很低"的结论)。"""
    return None if denom <= 0 else hits / denom


def _tally(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """一组影子行 → 通过率读数(**四个计数全列出来**,分母口径一眼可查)。

    · `rows`       = 该组全部行(= 进关候选 × 该阈值键);
    · `applicable` = 去掉「规则今天不适用」之后还剩多少(适用域分母);
    · `evaluable`  = 适用且**算得出拟判**的(`would_pass` 非 NULL);
    · `wouldPass`  = 其中拟判为「本可通过」的。
    `passRate` 的分母是 `evaluable` —— ⚠ 另外两个计数同时列出,是为了让"这个百分数
    背后有多少票被 not_applicable / 缺数吃掉了"随时可查,⛔ 不许只报 passRate。"""
    total = len(rows)
    applicable = [r for r in rows if not _is_not_applicable(r)]
    evaluable = [r for r in applicable if r.get("would_pass") is not None]
    hits = [r for r in evaluable if int(r["would_pass"]) == 1]
    return {
        "rows": total,
        "applicable": len(applicable),
        "evaluable": len(evaluable),
        "unavailable": len(applicable) - len(evaluable),
        "notApplicable": total - len(applicable),
        "wouldPass": len(hits),
        "passRate": _rate(len(hits), len(evaluable)),
        "sampleInsufficient": len(evaluable) == 0,
    }


def _joint_verdict(rows: Sequence[Mapping[str, Any]]) -> Optional[bool]:
    """一个候选在市场关 + 板块关**全部** evidence 阈值上的联合拟判(两关 AND)。

    三值(Kleene,与 `selection/verification_rules.combine_side` 同一哲学,⛔ 不新造):
    任一条「本可否决」→ `False`;全部适用条都「本可通过」→ `True`;
    一条都判不出 → `None`(**算不出 ≠ 不通过**,⛔ 不许把它计进分子或当 False)。"""
    determinable = [r for r in rows
                    if not _is_not_applicable(r) and r.get("would_pass") is not None]
    if not determinable:
        return None
    return all(int(r["would_pass"]) == 1 for r in determinable)


def _joint_section(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """裁定 4 第 2 项:市场关与板块关的联合通过率。**分母 = 进关候选数**。"""
    by_candidate: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for r in rows:
        by_candidate.setdefault((str(r["trade_date"]), str(r["candidate_key"])), []).append(r)
    verdicts = {k: _joint_verdict(v) for k, v in by_candidate.items()}
    determinable = [v for v in verdicts.values() if v is not None]
    hits = [v for v in determinable if v]
    rate = _rate(len(hits), len(determinable))
    return {
        "candidates": len(verdicts),
        "determinable": len(determinable),
        "undetermined": len(verdicts) - len(determinable),
        "wouldPass": len(hits),
        "passRate": rate,
        "sampleInsufficient": len(determinable) == 0,
        "band": _band_of(rate),
        "bandRules": BAND_RULES_TEXT,
    }


def _band_of(rate: Optional[float]) -> str:
    """裁定 4 的三档判读。⛔ 三条边界(10% / 20%)是裁定给的,不是工程侧选的。"""
    if rate is None:
        return "sample_insufficient"
    if rate < BAND_UNACCEPTABLE:
        return "unacceptable_too_strict"
    if rate < BAND_SAMPLE_OK:
        return "keep_as_evidence"
    return "sample_availability_ok"


def _group(rows: Sequence[Mapping[str, Any]], key: str) -> Dict[str, Any]:
    """按某一列分组出通过率(引擎码 / 行情状态两用)。`None` 值归 `"(未登记)"`。"""
    buckets: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        buckets.setdefault(str(r.get(key) or "(未登记)"), []).append(r)
    return {k: _tally(buckets[k]) for k in sorted(buckets)}


def _final_tier_impact(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """裁定 4 第 5 项:**对最终 T1/T2 数量的影响**。

    问的是「如果把这条(或这两关全部)待定阈值恢复成硬门,今天已经定档的 T1/T2 里
    有几个会当场消失」—— 这才是"恢复硬否决"的真实代价。"""
    tiered = [r for r in rows if r.get("final_tier") in (1, 2)]
    by_candidate: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for r in tiered:
        by_candidate.setdefault((str(r["trade_date"]), str(r["candidate_key"])), []).append(r)

    per_rule: Dict[str, Dict[str, int]] = {}
    for r in tiered:
        k = str(r["threshold_key"])
        slot = per_rule.setdefault(k, {"tieredRows": 0, "wouldBeRejected": 0})
        slot["tieredRows"] += 1
        if not _is_not_applicable(r) and r.get("would_pass") == 0:
            slot["wouldBeRejected"] += 1

    joint_lost = sum(1 for v in by_candidate.values() if _joint_verdict(v) is False)
    t1 = {k for k, v in by_candidate.items() if any(x.get("final_tier") == 1 for x in v)}
    t1_lost = sum(1 for k in t1 if _joint_verdict(by_candidate[k]) is False)
    return {
        "tieredCandidates": len(by_candidate),
        "t1Candidates": len(t1),
        "jointWouldRemove": joint_lost,
        "t1WouldRemove": t1_lost,
        "perRule": {k: per_rule[k] for k in sorted(per_rule)},
    }


def build_threshold_report(
    date_from: date | str, date_to: date | str, *, db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """一个闭区间窗口的待定阈值通过率报告(**零写库**;周度落盘 + 移交件消费)。

    返回的 dict 结构稳定,键名 camelCase(与既有周度产物同体例)。
    ⚠ 窗口无行 = 影子台账还没攒到样本(自 V2.3.2 上产之日起前向累积)——
    如实标 `available=False`,⛔ **不许拿 14 个历史 D0 顶上**(裁定 3 明令:
    那 14 天零 T1,只能作为联合门槛过严的诊断证据)。"""
    lo = date_from if isinstance(date_from, str) else date_from.strftime("%Y%m%d")
    hi = date_to if isinstance(date_to, str) else date_to.strftime("%Y%m%d")
    try:
        rows = load_threshold_shadow(lo, hi, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[threshold_calibration] 影子台账读取失败,本段如实标未取得",
                       exc_info=True)
        return {
            "specVersion": SPEC_VERSION, "window": {"from": lo, "to": hi},
            "available": False,
            "unavailableReason": "阈值影子台账读取失败(详见服务端日志),本段未取得。",
            "disclaimer": DISCLAIMER,
        }

    candidates = {(str(r["trade_date"]), str(r["candidate_key"])) for r in rows}
    if not rows:
        return {
            "specVersion": SPEC_VERSION, "window": {"from": lo, "to": hi},
            "available": False,
            "unavailableReason": (
                "窗口内没有阈值影子行 —— 台账自 V2.3.2 上产之日起**前向**累积,"
                "样本不足,不出通过率结论。⛔ 不得拿历史 D0 回放顶上(策略线裁定 3)。"),
            "historicalD0": HISTORICAL_D0_DISCLAIMER,
            "disclaimer": DISCLAIMER,
        }

    by_key: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        by_key.setdefault(str(r["threshold_key"]), []).append(r)

    return {
        "specVersion": SPEC_VERSION,
        "window": {"from": lo, "to": hi},
        "available": True,
        # 🔴 分母口径,原样写进产物(⛔ 工程侧不许改写)。
        "denominatorRule": (
            "分母 = 进入市场关、板块关**之前**的召回候选或篮子(含后来被硬门拒掉的、"
            "含最终判 OUT 的)。⛔ 绝不使用「最终 T1/T2 的历史快照」当分母。"),
        "candidates": len(candidates),
        # ① 每条规则的单关通过率
        "perThreshold": {k: _tally(by_key[k]) for k in sorted(by_key)},
        # ② 市场关与板块关的联合通过率(两关 AND)
        "joint": _joint_section(rows),
        # ③ C / Z / Y 各引擎结果
        "byEngine": _group(rows, "engine_code"),
        # ④ 三种行情状态下的结果
        "byRegime": _group(rows, "regime"),
        # ⑤ 对最终 T1/T2 数量的影响
        "finalTierImpact": _final_tier_impact(rows),
        "historicalD0": HISTORICAL_D0_DISCLAIMER,
        "disclaimer": DISCLAIMER,
    }


__all__ = [
    "SPEC_VERSION", "BAND_UNACCEPTABLE", "BAND_SAMPLE_OK", "BAND_RULES_TEXT",
    "HISTORICAL_D0_DISCLAIMER", "DISCLAIMER", "build_threshold_report",
]
