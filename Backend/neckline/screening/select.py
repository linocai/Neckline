"""按配置规则从分类结果里选出晨报。规则全部来自配置，这里不设默认值；每家公司在每一节里只占一条。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from .classify import EVENT_TYPES, FRESHNESS, IMPORTANCE, RELATIONS, CompanyResult, Event
from .prefilter import Message

_RANK = {level: i for i, level in enumerate(IMPORTANCE)}
SECTIONS = ("main", "review_types", "review_relation", "review_fresh", "held_old")


@dataclass
class Entry:
    company: str
    primary: Event
    messages: list[Message]          # 主事件的消息，按发布时间排
    others: list[Event] = field(default_factory=list)  # 同一家公司在本节的其他事件

    @property
    def first_message(self) -> Message:
        return self.messages[0]


@dataclass
class Selection:
    sections: dict[str, list[Entry]]
    reasons: Counter
    gaps: list[CompanyResult]

    @property
    def main(self) -> list[Entry]:
        return self.sections["main"]


def validate_rules(rules: Mapping[str, Any]) -> None:
    types = rules["types"]
    named = [*types["include"], *types["review"], *types["exclude"]]
    unknown = sorted(set(named) - set(EVENT_TYPES))
    if unknown:
        raise ValueError(f"配置里有未知事件类型：{unknown}")
    if len(named) != len(set(named)):
        raise ValueError("同一事件类型出现在多个分组里")
    for group, allowed in (("relation", RELATIONS), ("freshness", FRESHNESS)):
        values = [*rules[group]["include"], *rules[group]["review"]]
        if set(values) - set(allowed) or len(values) != len(set(values)):
            raise ValueError(f"{group} 的取值无效或重复")
    for key in ("importanceMinimum", "maxItems"):
        if key not in rules:
            raise ValueError(f"配置缺少 {key}（未决定时须显式写 null）")


def _route(event: Event, rules: Mapping[str, Any]) -> tuple[str | None, str]:
    relation, freshness, types = rules["relation"], rules["freshness"], rules["types"]
    if event.relation not in (*relation["include"], *relation["review"]):
        return None, f"关系：{event.relation}"
    if event.direction not in rules["direction"]["include"]:
        return None, f"方向：{event.direction}"
    if event.type in types["exclude"]:
        return None, f"类型不收：{event.type}"
    if event.fresh not in (*freshness["include"], *freshness["review"]):
        held = event.relation in relation["include"] and event.type in types["include"]
        return ("held_old" if held else None), "旧事"
    if event.fresh in freshness["review"]:
        return "review_fresh", f"时效待定：{event.fresh}"
    if event.relation in relation["review"]:
        return "review_relation", f"待定关系：{event.relation}"
    if event.type in types["review"]:
        return "review_types", f"待定类型：{event.type}"
    return "main", "入选"


def _key(event: Event, by_id: Mapping[str, Message]) -> tuple:
    earliest = min(by_id[i].published_at for i in event.ids)
    return (_RANK[event.importance], earliest)


def select(by_id: Mapping[str, Message], results: Mapping[str, CompanyResult], rules: Mapping[str, Any]) -> Selection:
    validate_rules(rules)
    buckets: dict[str, dict[str, list[Event]]] = {name: {} for name in SECTIONS}
    reasons: Counter = Counter()
    gaps = []
    for code, result in results.items():
        if result.error:
            gaps.append(result)
            continue
        reasons["仅提及或误匹配（消息）"] += len(result.mentions)
        for event in result.events:
            section, reason = _route(event, rules)
            reasons[reason] += 1
            if section:
                buckets[section].setdefault(code, []).append(event)
    sections: dict[str, list[Entry]] = {}
    for name, per_company in buckets.items():
        entries = []
        for code, events in per_company.items():
            events.sort(key=lambda e: _key(e, by_id))
            primary = events[0]
            messages = sorted((by_id[i] for i in primary.ids), key=lambda m: (m.published_at, m.ref))
            entries.append(Entry(company=code, primary=primary, messages=messages, others=events[1:]))
        entries.sort(key=lambda e: (_RANK[e.primary.importance], e.first_message.published_at, e.company))
        sections[name] = entries
    minimum = rules["importanceMinimum"]
    if minimum is not None:
        sections["main"] = [e for e in sections["main"] if _RANK[e.primary.importance] <= _RANK[minimum]]
    if rules["maxItems"] is not None:
        sections["main"] = sections["main"][: rules["maxItems"]]
    return Selection(sections=sections, reasons=reasons, gaps=sorted(gaps, key=lambda g: g.company))
