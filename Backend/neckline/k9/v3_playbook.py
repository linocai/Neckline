"""K9-v3 typed pre-plan generation and validation.

The mechanical engine owns selection, channels, ranks and condition *shapes*.
This module only asks the configured ``playbook`` provider to fill price levels
and explanations for that already frozen shape.  A missing provider, malformed
JSON, or a value that violates the frozen shape is a hard failure: callers must
not manufacture a mechanical/sample plan as a fallback.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from neckline.llm.base import ChatMessage
import neckline.llm.prompt_context as _prompt_context  # noqa: F401 - shared LLM call-site discipline
from neckline.llm.factory import get_provider
from neckline.llm.json_block import split_narrative_and_json
from neckline.llm.router import TASK_PLAYBOOK


class PlaybookUnavailable(RuntimeError):
    pass


def _number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise PlaybookUnavailable(f"{name} 必须为价格数值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PlaybookUnavailable(f"{name} 必须为价格数值") from exc
    if result <= 0:
        raise PlaybookUnavailable(f"{name} 必须大于 0")
    return result


def _bounded(value: object, name: str, bounds: Mapping[str, Any], *, lower: str, upper: str) -> float:
    result = _number(value, name) if lower == "minimumMemberCoverage" else None
    if result is None:
        if isinstance(value, bool):
            raise PlaybookUnavailable(f"{name} 必须为有限数值")
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise PlaybookUnavailable(f"{name} 必须为有限数值") from exc
    try:
        lo, hi = float(bounds[f"{lower}Min"]), float(bounds[f"{upper}Max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlaybookUnavailable("P4 参数包缺少预案条件允许范围") from exc
    if not (lo <= result <= hi):
        raise PlaybookUnavailable(f"{name} 超出参数包允许范围")
    return result


def mechanical_skeleton(hits: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Return a merged, frozen condition shape for every selected code.

    No price is calculated here.  The shape pins the LLM to the mechanical
    channels and makes multi-channel semantics explicit.
    """
    grouped: dict[str, list[Any]] = {}
    for hit in hits:
        grouped.setdefault(str(hit.ts_code), []).append(hit)
    result: dict[str, dict[str, Any]] = {}
    for code, items in grouped.items():
        first = items[0]
        baseline = dict(first.baseline)
        limit = baseline.get("limit_up_price")
        _number(limit, f"{code}.baseline.limit_up_price")
        channels = [str(item.channel) for item in items]
        conditions: dict[str, dict[str, Any]] = {}
        for item in items:
            channel = str(item.channel)
            conditions[channel] = {
                "required": (
                    "开盘未透支且30分钟不创新低/守止跌位" if channel == "p2" else
                    "可买、未超高开透支线、守关键位且未快速完全回吐" if channel == "p3" else
                    "行业未继续失速且个股守位/相对行业强"
                ),
                "reject": (
                    "跌破失效" if channel == "p2" else
                    "明显透支或结构失效" if channel == "p3" else "行业假设或个股失效"
                ),
                "mechanicalThresholds": dict(item.thresholds),
            }
        result[code] = {
            "tsCode": code,
            "name": first.name,
            "channels": channels,
            "channelRanks": {str(item.channel): int(item.rank) for item in items},
            "baseline": baseline,
            "conditions": conditions,
            "mergeRule": {
                "reject": "任一通道放弃条件触发即放弃",
                "confirmed": "所有适用通道成立条件均满足才成立",
                "otherwise": "其余可交易状态为观察",
            },
        }
    return result


def _prompt(skeleton: Mapping[str, Mapping[str, Any]]) -> str:
    output_shape = {
        "candidates": [{
            "tsCode": "逐字复制一只 frozenCandidates.tsCode",
            "invalidation": "正数价格",
            "firstResistance": "正数价格",
            "secondResistance": "正数价格",
            "openVerdict": {
                "rejectBelow": "正数价格",
                "confirmRange": {"minimum": "正数价格", "maximum": "正数价格"},
                "overextendedAtOrAbove": "正数价格",
                "unbuyableAtOrAbove": "逐字使用该票 baseline.limit_up_price",
            },
            "conditions": {
                "p2或p3": {"holdAbove": "正数价格"},
                "p4": {"industry": "按下述字段完整填写", "stock": "按下述字段完整填写"},
            },
            "rationale": "非空解释",
        }],
    }
    return (
        "你是 Neckline K9-v3 次日预案填写器。只能为下列冻结候选填写具体价位和解释；"
        "不得新增/删除候选、通道、排名、额度或改变任何机械条件。"
        "每只必须返回 openVerdict.rejectBelow、confirmRange.minimum/maximum、"
        "unbuyableAtOrAbove、overextendedAtOrAbove、invalidation、firstResistance、secondResistance、"
        "conditions（逐通道；p4 必须有 industry 与 stock 条件）和 rationale。"
        "unbuyableAtOrAbove 必须等于冻结 baseline.limit_up_price；所有价格必须为正，"
        "且 invalidation <= rejectBelow <= confirm minimum <= confirm maximum <= overextended <= limit，"
        "firstResistance <= secondResistance。conditions.p2/p3 需要 holdAbove；"
        "conditions.p4 必须填写 industry.minimumMemberCoverage、medianReturnAtOrAbove、breadthAtOrAbove、"
        "relativeBenchmarkReturnAtOrAbove、failBelowMedianReturn、failBelowBreadth、failBelowRelativeBenchmarkReturn，"
        "以及 stock.holdAbove、relativeIndustryReturnAtOrAbove。"
        "只输出 requiredOutputShape 对应的 JSON 对象，不要回显 frozenCandidates，"
        "不要输出分析过程或第二个 JSON。\n"
        + json.dumps(
            {"requiredOutputShape": output_shape,
             "frozenCandidates": list(skeleton.values())},
            ensure_ascii=False, sort_keys=True,
        )
    )


def validate_output(raw: object, skeleton: Mapping[str, Mapping[str, Any]], *, source: str) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("candidates"), list):
        raise PlaybookUnavailable("预案输出不是 candidates JSON")
    seen: dict[str, Mapping[str, Any]] = {}
    for item in raw["candidates"]:
        if not isinstance(item, Mapping) or not isinstance(item.get("tsCode"), str):
            raise PlaybookUnavailable("预案输出缺少 tsCode")
        code = item["tsCode"]
        if code in seen:
            raise PlaybookUnavailable(f"预案输出重复 {code}")
        seen[code] = item
    if set(seen) != set(skeleton):
        raise PlaybookUnavailable("预案候选集合与机械冻结件不一致")
    output: dict[str, dict[str, Any]] = {}
    for code, shape in skeleton.items():
        item = seen[code]
        rules = item.get("openVerdict")
        conditions = item.get("conditions")
        if not isinstance(rules, Mapping) or not isinstance(rules.get("confirmRange"), Mapping):
            raise PlaybookUnavailable(f"{code} 缺少开盘判定")
        if not isinstance(conditions, Mapping) or set(conditions) != set(shape["channels"]):
            raise PlaybookUnavailable(f"{code} 通道条件与冻结件不一致")
        for channel in shape["channels"]:
            condition = conditions.get(channel)
            if not isinstance(condition, Mapping):
                raise PlaybookUnavailable(f"{code} 缺少 {channel} 条件")
            if channel in {"p2", "p3"}:
                _number(condition.get("holdAbove"), f"{code}.{channel}.holdAbove")
        if "p4" in shape["channels"]:
            p4 = conditions.get("p4")
            if not isinstance(p4, Mapping) or not isinstance(p4.get("industry"), Mapping) or not isinstance(p4.get("stock"), Mapping):
                raise PlaybookUnavailable(f"{code} P4 缺少行业和个股条件")
            bounds = ((shape["conditions"].get("p4") or {}).get("mechanicalThresholds") or {}).get("playbookBounds")
            if not isinstance(bounds, Mapping):
                raise PlaybookUnavailable(f"{code} P4 缺少参数包预案条件允许范围")
            industry, stock = p4["industry"], p4["stock"]
            coverage = _bounded(industry.get("minimumMemberCoverage"), f"{code}.p4.minimumMemberCoverage", bounds,
                                lower="minimumMemberCoverage", upper="minimumMemberCoverage")
            median = _bounded(industry.get("medianReturnAtOrAbove"), f"{code}.p4.medianReturnAtOrAbove", bounds,
                              lower="medianReturn", upper="medianReturn")
            breadth = _bounded(industry.get("breadthAtOrAbove"), f"{code}.p4.breadthAtOrAbove", bounds,
                               lower="breadth", upper="breadth")
            relative = _bounded(industry.get("relativeBenchmarkReturnAtOrAbove"), f"{code}.p4.relativeBenchmarkReturnAtOrAbove", bounds,
                                lower="relativeBenchmarkReturn", upper="relativeBenchmarkReturn")
            fail_median = _bounded(industry.get("failBelowMedianReturn"), f"{code}.p4.failBelowMedianReturn", bounds,
                                   lower="medianReturn", upper="medianReturn")
            fail_breadth = _bounded(industry.get("failBelowBreadth"), f"{code}.p4.failBelowBreadth", bounds,
                                    lower="breadth", upper="breadth")
            fail_relative = _bounded(industry.get("failBelowRelativeBenchmarkReturn"), f"{code}.p4.failBelowRelativeBenchmarkReturn", bounds,
                                     lower="relativeBenchmarkReturn", upper="relativeBenchmarkReturn")
            _number(stock.get("holdAbove"), f"{code}.p4.stock.holdAbove")
            _bounded(stock.get("relativeIndustryReturnAtOrAbove"), f"{code}.p4.stock.relativeIndustryReturnAtOrAbove", bounds,
                     lower="relativeIndustryReturn", upper="relativeIndustryReturn")
            if not (0 < coverage <= 1 and fail_median <= median and fail_breadth <= breadth and fail_relative <= relative):
                raise PlaybookUnavailable(f"{code} P4 行业条件关系不合法")
        reject = _number(rules.get("rejectBelow"), f"{code}.rejectBelow")
        floor = _number(rules["confirmRange"].get("minimum"), f"{code}.confirmMinimum")
        ceiling = _number(rules["confirmRange"].get("maximum"), f"{code}.confirmMaximum")
        limit = _number(rules.get("unbuyableAtOrAbove"), f"{code}.unbuyableAtOrAbove")
        over = _number(rules.get("overextendedAtOrAbove"), f"{code}.overextendedAtOrAbove")
        invalidation = _number(item.get("invalidation"), f"{code}.invalidation")
        first = _number(item.get("firstResistance"), f"{code}.firstResistance")
        second = _number(item.get("secondResistance"), f"{code}.secondResistance")
        frozen_limit = _number(shape["baseline"].get("limit_up_price"), f"{code}.baseline.limit_up_price")
        if abs(limit - frozen_limit) > 1e-6 or not (invalidation <= reject <= floor <= ceiling <= over <= limit and first <= second):
            raise PlaybookUnavailable(f"{code} 价位关系或涨停价不合法")
        if not isinstance(item.get("rationale"), str) or not item["rationale"].strip():
            raise PlaybookUnavailable(f"{code} 缺少预案解释")
        output[code] = {
            "revision": 1,
            "source": source,
            "baselineClose": shape["baseline"].get("close"),
            "invalidation": invalidation,
            "firstResistance": first,
            "secondResistance": second,
            "openVerdict": {"rejectBelow": reject, "confirmRange": {"minimum": floor, "maximum": ceiling},
                            "unbuyableAtOrAbove": limit, "overextendedAtOrAbove": over},
            "conditions": {key: dict(value) for key, value in conditions.items()},
            "rationale": item["rationale"].strip(),
            "mechanicalSkeleton": dict(shape),
        }
    return output


def generate(hits: Sequence[Any], *, db_path=None, provider=None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    skeleton = mechanical_skeleton(hits)
    if not skeleton:
        return {}, {"source": "llm", "outcome": "empty"}
    client = provider or get_provider(TASK_PLAYBOOK, db_path=db_path)
    if client is None:
        raise PlaybookUnavailable("playbook LLM 未配置")
    result = client.chat([ChatMessage(role="system", content="严格返回可验证 JSON，不联网。"),
                          ChatMessage(role="user", content=_prompt(skeleton))], enable_search=False)
    if not result.ok:
        raise PlaybookUnavailable(f"playbook LLM 失败：{result.reason}")
    _narrative, parsed = split_narrative_and_json(result.content)
    playbooks = validate_output(parsed, skeleton, source="llm")
    return playbooks, {"source": "llm", "provider": result.provider, "model": result.model,
                       "promptVersion": "k9-v3-playbook-v1", "output": parsed}


__all__ = ["PlaybookUnavailable", "mechanical_skeleton", "validate_output", "generate"]
