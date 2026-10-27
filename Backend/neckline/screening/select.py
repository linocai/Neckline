"""按配置规则从分类结果里选出晨报候选。规则全部来自配置，这里不设默认值；每家公司只占一条。

主事件取该公司入选事件里消息最早发布时间最早的那条，其余入选事件放进 others。
名单按首次公开时间先后、再按代码排。不设待定节，也不按重要程度取舍。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .classify import CHANNELS, DIRECTIONS, EVENT_TYPES, FRESHNESS, RELATIONS, CompanyResult, Event
from .prefilter import Message


@dataclass
class Entry:
    company: str
    primary: Event
    messages: list[Message]          # 主事件的消息，按发布时间排
    others: list[Event] = field(default_factory=list)  # 同一家公司的其他入选事件

    @property
    def first_message(self) -> Message:
        return self.messages[0]


@dataclass
class Selection:
    candidates: list[Entry]
    reasons: Counter
    gaps: list[CompanyResult]


def _strings(values: Any, label: str) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ValueError(f"{label} 的取值无效或重复")
    return list(values)


def validate_rules(rules: Mapping[str, Any]) -> None:
    for key in ("kinds", "relation", "freshness", "direction", "channels", "minimumAmountYuan"):
        if key not in rules:
            raise ValueError(f"配置缺少 {key}（未决定时须显式写 null）")
    if "importanceMinimum" in rules:
        raise ValueError("selection 不再包含 importanceMinimum")
    if "maxItems" in rules:
        raise ValueError("selection 不再包含 maxItems")
    kinds = rules["kinds"]
    if not isinstance(kinds, dict):
        raise ValueError("配置缺少 kinds")
    for key in ("include", "judge", "exclude"):
        if key not in kinds:
            raise ValueError(f"配置缺少 kinds.{key}")
    named = [name for key in ("include", "judge", "exclude") for name in _strings(kinds[key], "kinds")]
    unknown = sorted(set(named) - set(EVENT_TYPES))
    if unknown:
        raise ValueError(f"配置里有未知事件类型：{unknown}")
    if len(named) != len(set(named)):
        raise ValueError("同一事件类型出现在多个分组里")
    missing = sorted(set(EVENT_TYPES) - set(named))
    if missing:
        raise ValueError(f"配置遗漏事件类型：{missing}")
    for group, allowed in (("relation", RELATIONS), ("freshness", FRESHNESS)):
        block = rules[group]
        if not isinstance(block, dict) or set(block) != {"include"}:
            raise ValueError(f"{group} 的取值无效或重复")
        values = _strings(block["include"], group)
        if set(values) - set(allowed) or len(values) != len(set(values)):
            raise ValueError(f"{group} 的取值无效或重复")
    direction = rules["direction"]
    if not isinstance(direction, dict) or set(direction) != {"include"}:
        raise ValueError("direction 的取值无效或重复")
    directions = _strings(direction["include"], "direction")
    if not directions or set(directions) - set(DIRECTIONS) or len(directions) != len(set(directions)):
        raise ValueError("direction 的取值无效或重复")
    channels = rules["channels"]
    if not isinstance(channels, dict) or set(channels) != {"exclude", "requireAmount"}:
        raise ValueError("channels 的配置无效")
    excluded = _strings(channels["exclude"], "channels")
    required = _strings(channels["requireAmount"], "channels")
    if set(excluded) - set(CHANNELS) or set(required) - set(CHANNELS):
        raise ValueError("channels 的取值无效")
    if len(excluded) != len(set(excluded)) or len(required) != len(set(required)) or set(excluded) & set(required):
        raise ValueError("同一渠道出现在多个分组里")
    amounts = rules["minimumAmountYuan"]
    if not isinstance(amounts, dict):
        raise ValueError("minimumAmountYuan 无效")
    for key, value in amounts.items():
        if key not in EVENT_TYPES:
            raise ValueError(f"配置里有未知事件类型：{[key]}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError("minimumAmountYuan 无效")


def _route(event: Event, rules: Mapping[str, Any]) -> str:
    """先看关系、时效、渠道，再看种类和方向，最后看已知金额是否低于门槛。入选返回「入选」。"""
    if event.relation not in rules["relation"]["include"]:
        return f"关系：{event.relation}"
    if event.fresh not in rules["freshness"]["include"]:
        return f"时效：{event.fresh}"
    if event.channel in rules["channels"]["exclude"]:
        return f"渠道：{event.channel}"
    if event.channel in rules["channels"]["requireAmount"] and event.amount_yuan is None:
        return f"渠道：{event.channel}无金额"
    kinds = rules["kinds"]
    if event.type in kinds["exclude"] or event.type not in (*kinds["include"], *kinds["judge"]):
        return f"种类不收：{event.type}"
    if event.type in kinds["include"] and event.direction not in rules["direction"]["include"]:
        return f"方向：{event.direction}"
    threshold = rules["minimumAmountYuan"].get(event.type)
    if threshold is not None and event.amount_yuan is not None and event.amount_yuan < threshold:
        return f"金额：{event.type}低于门槛"
    return "入选"


def _earliest(event: Event, by_id: Mapping[str, Message]) -> datetime:
    return min(by_id[i].published_at for i in event.ids)


def select(by_id: Mapping[str, Message], results: Mapping[str, CompanyResult], rules: Mapping[str, Any]) -> Selection:
    validate_rules(rules)
    chosen: dict[str, list[Event]] = {}
    reasons: Counter = Counter()
    gaps = []
    for code, result in results.items():
        if result.error:
            gaps.append(result)
            continue
        reasons["仅提及或误匹配（消息）"] += len(result.mentions)
        for event in result.events:
            reason = _route(event, rules)
            reasons[reason] += 1
            if reason == "入选":
                chosen.setdefault(code, []).append(event)
    entries = []
    for code, events in chosen.items():
        events.sort(key=lambda event: (_earliest(event, by_id), tuple(event.ids)))
        primary = events[0]
        messages = sorted((by_id[i] for i in primary.ids), key=lambda message: (message.published_at, message.ref))
        entries.append(Entry(company=code, primary=primary, messages=messages, others=events[1:]))
    entries.sort(key=lambda entry: (entry.first_message.published_at, entry.company))
    return Selection(candidates=entries, reasons=reasons, gaps=sorted(gaps, key=lambda gap: gap.company))
