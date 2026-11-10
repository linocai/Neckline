"""板块线：粗筛、收消息、回流、归并、成分股、评估、收口。

主线名单定了之后才跑。任一步出错只记在板块线上，不往外抛（BaseException 除外，留给进程被杀后续跑）。
模型调用走同一张回执；已有终态的事件不再调用。unknown 的回执不重发。
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import httpx

from neckline.evaluation.prepare import _present
from neckline.evaluation.search import _failure, project_results, summarize_search
from neckline.morning import db
from neckline.morning.schedule import CST, CalendarGap, previous_trading_day
from neckline.morning.sector_ferment import FermentOutcome, count_fermenting
from neckline.morning.sector_recognition import (
    LIMIT_INTERVAL_SECONDS, fetch_limit_ups, load_limit_ups, load_members_all, recognition_window,
    score_recognition, store_limit_ups, stored_limit_days,
)
from neckline.morning.sector_prompt import (
    EVALUATION_CONTRACT, EVALUATION_SYSTEM, MAPPING_CONTRACT, MAPPING_SYSTEM, SELECTION_CONTRACT,
    SELECTION_SYSTEM, evaluation_user_prompt, mapping_user_prompt, published_label, selection_user_prompt,
)
from neckline.morning.sectors import concept_list
from neckline.screening.deepseek import ChatError, ChatRejected, ChatUnknown
from neckline.screening.pool import CompanyPool
from neckline.screening.prefilter import Message, Window

logger = logging.getLogger(__name__)

_USAGE = ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "reasoning_tokens")
_VIEWS = ("媒体", "券商", "公司")
_VERDICTS = ("推", "不推")
_SCOPES = ("opening", "openingPrefix", "title")
_PACKAGE_KEYS = (
    "pdfMaxPages", "originalChars", "openingChars", "headingsMax", "mediaChars", "maxMedia", "pdfMaxBytes",
)
_GAP = "收口时刻前没评完"
_CUTOFF_GAP = "板块线没有完成：收口时刻前没评完"
_CONCEPT_NEVER = "板块清单还没有成功刷新过"
_MAPPING_GAP = "板块线没有完成：归并没有完成"
_WAIT_SLICE = 1.0
_MAPPING_HALF = "板块线有一半候选没归并成"
_NO_CARRIER = "判推但核对后没有承接股"
_LIMIT_GAP = "板块线没跑：涨停数据没取全"
_MEMBERS_GAP = "板块线没跑：全 A 成分没取到"
_NO_FULL_MEMBERS = "板块的全 A 成分缺失"
_NO_FULL_GAP = "板块线有 {count} 个事件缺全 A 成分，没评"
_EMPTY_RECOGNITION = "板块里没有题材辨识度名单"
_COUNT_SUFFIX = re.compile(r"｜\d+$")
_INTAKE_LISTS = (
    "publishers", "foreign", "diplomacy", "bigCompanies", "bigEvents", "documents", "documentScope",
    "documentExclude", "macro", "noise",
)
_REPAIR_PROMPT = (
    "你上一条输出没有通过格式检查：{reason}。"
    "请对照系统提示里的输出格式，把同一份评估重新完整输出为一个 JSON 对象。"
    "判断和结论不要改，只修正格式；缺的字段按你原来的分析写全。"
)
_SELECTION_REPAIR = (
    "你上一条输出没有通过格式检查：{reason}。"
    "请对照系统提示里的输出格式，把同一份比较重新完整输出为一个 JSON 对象。"
    "选择不要改，只修正格式。"
)
_SELECTION_GAP = "板块线判推 {count} 个，比较没完成"
_FERMENT_GAP = "板块线 C 路没跑：发酵统计的历史不全"
_ROUTE_ORDER = ("A", "B", "C")
_INTAKE_KEPT = ("publisher", "bigCompany", "document")
_QUERY_LIMIT = 128


@dataclass
class SectorOutcome:
    gap: str | None
    content: dict
    stats: dict


def _zero_usage() -> dict[str, int]:
    return {key: 0 for key in _USAGE}


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _usage(raw: object) -> dict[str, int]:
    data = raw if isinstance(raw, Mapping) else {}
    details = data.get("completion_tokens_details")
    if not isinstance(details, Mapping):
        details = {}
    reasoning = data.get("reasoning_tokens")
    if reasoning is None:
        reasoning = details.get("reasoning_tokens")
    return {
        "prompt_tokens": _as_int(data.get("prompt_tokens")),
        "completion_tokens": _as_int(data.get("completion_tokens")),
        "prompt_cache_hit_tokens": _as_int(data.get("prompt_cache_hit_tokens")),
        "reasoning_tokens": _as_int(reasoning),
    }


def _add_usage(left: Mapping[str, int], right: Mapping[str, int]) -> dict[str, int]:
    return {key: left.get(key, 0) + right.get(key, 0) for key in _USAGE}


def _require(obj: Mapping, key: str, label: str) -> object:
    if key not in obj:
        raise ValueError(f"配置缺少 {label}")
    return obj[key]


def _require_dict(obj: Mapping, key: str, label: str) -> dict:
    value = _require(obj, key, label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} 类型不对")
    return value


def _require_str(obj: Mapping, key: str, label: str) -> str:
    value = _require(obj, key, label)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} 类型不对")
    return value


def _require_int(obj: Mapping, key: str, label: str, minimum: int = 1) -> int:
    value = _require(obj, key, label)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} 类型不对")
    return value


def _require_words(obj: Mapping, key: str, label: str) -> list[str]:
    value = _require(obj, key, label)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{label} 类型不对")
    return list(value)


def _require_nonempty_words(obj: Mapping, key: str, label: str) -> list[str]:
    value = _require_words(obj, key, label)
    if not value:
        raise ValueError(f"{label} 类型不对")
    return value


def _require_positive(obj: Mapping, key: str, label: str) -> int | float:
    value = _require(obj, key, label)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label} 类型不对")
    return value


def _compile(pattern: str, label: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{label} 类型不对") from exc


def validate_sector_settings(sectors: Mapping) -> None:
    """板块线配置。缺了或类型不对就拒绝整份配置，不补默认值。"""
    if not isinstance(sectors, Mapping):
        raise ValueError("sectors 类型不对")
    prefilter = _require_dict(sectors, "prefilter", "sectors.prefilter")
    sources = _require(prefilter, "dropSources", "sectors.prefilter.dropSources")
    if not isinstance(sources, list) or any(not isinstance(item, str) or not item for item in sources):
        raise ValueError("sectors.prefilter.dropSources 类型不对")
    _require_int(prefilter, "bodyHeadChars", "sectors.prefilter.bodyHeadChars")
    rules = _require(prefilter, "rules", "sectors.prefilter.rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("sectors.prefilter.rules 类型不对")
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        label = f"sectors.prefilter.rules[{index}]"
        if not isinstance(rule, Mapping):
            raise ValueError(f"{label} 类型不对")
        name = _require_str(rule, "name", f"{label}.name")
        if name in seen or name == "dropSources":
            raise ValueError(f"{label}.name 类型不对")
        seen.add(name)
        scope = _require_str(rule, "scope", f"{label}.scope")
        if scope not in _SCOPES:
            raise ValueError(f"{label}.scope 类型不对")
        _compile(_require_str(rule, "pattern", f"{label}.pattern"), f"{label}.pattern")
        if scope == "openingPrefix":
            _require_int(rule, "prefixChars", f"{label}.prefixChars")
        if "absentPattern" in rule:
            _compile(_require_str(rule, "absentPattern", f"{label}.absentPattern"), f"{label}.absentPattern")
            _require_int(rule, "absentChars", f"{label}.absentChars")
    if "leaders" in sectors:
        raise ValueError("sectors.leaders 已停用")
    if "market" in sectors:
        raise ValueError("sectors.market 已停用")
    if "candidates" in sectors:
        raise ValueError("sectors.candidates 已停用")
    intake = _require_dict(sectors, "intake", "sectors.intake")
    for key in _INTAKE_LISTS:
        _require_words(intake, key, f"sectors.intake.{key}")
    _require_int(intake, "bigLeadChars", "sectors.intake.bigLeadChars")
    identity = _require_dict(sectors, "identity", "sectors.identity")
    _require_words(identity, "keywords", "sectors.identity.keywords")
    backflow = _require_dict(sectors, "backflow", "sectors.backflow")
    for key in ("relations", "types", "excludeDirections"):
        _require_words(backflow, key, f"sectors.backflow.{key}")
    carriers = _require_dict(sectors, "carriers", "sectors.carriers")
    _require_int(carriers, "max", "sectors.carriers.max")
    mapping = _require_dict(sectors, "mapping", "sectors.mapping")
    if _require_str(mapping, "contract", "sectors.mapping.contract") != MAPPING_CONTRACT:
        raise ValueError(f"sectors.mapping.contract 须为 {MAPPING_CONTRACT}")
    _require_int(mapping, "excerptChars", "sectors.mapping.excerptChars")
    if not isinstance(_require(mapping, "modelOptions", "sectors.mapping.modelOptions"), dict):
        raise ValueError("sectors.mapping.modelOptions 类型不对")
    evaluation = _require_dict(sectors, "evaluation", "sectors.evaluation")
    if _require_str(evaluation, "contract", "sectors.evaluation.contract") != EVALUATION_CONTRACT:
        raise ValueError(f"sectors.evaluation.contract 须为 {EVALUATION_CONTRACT}")
    _require_int(evaluation, "concurrency", "sectors.evaluation.concurrency")
    if not isinstance(_require(evaluation, "modelOptions", "sectors.evaluation.modelOptions"), dict):
        raise ValueError("sectors.evaluation.modelOptions 类型不对")
    _validate_search(_require_dict(evaluation, "search", "sectors.evaluation.search"))
    package = _require_dict(evaluation, "package", "sectors.evaluation.package")
    for key in _PACKAGE_KEYS:
        _require_int(package, key, f"sectors.evaluation.package.{key}")
    delivery = _require_dict(sectors, "delivery", "sectors.delivery")
    _require_int(delivery, "maxEvents", "sectors.delivery.maxEvents")
    _validate_overseas(sectors, {rule["name"] for rule in rules if isinstance(rule, Mapping)})
    _validate_fermenting(sectors, {rule["name"] for rule in rules if isinstance(rule, Mapping)})
    _validate_recognition(sectors)
    _validate_selection(sectors)


def _validate_overseas(sectors: Mapping, rule_names: set[str]) -> None:
    overseas = _require_dict(sectors, "overseas", "sectors.overseas")
    names = _require(overseas, "names", "sectors.overseas.names")
    if not isinstance(names, list) or not names:
        raise ValueError("sectors.overseas.names 类型不对")
    for index, item in enumerate(names):
        label = f"sectors.overseas.names[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} 类型不对")
        _require_str(item, "name", f"{label}.name")
        _require_words(item, "aliases", f"{label}.aliases")
        _require_str(item, "industry", f"{label}.industry")
        _require_str(item, "note", f"{label}.note")
    for key in ("markets", "industries", "moves", "events", "exclude"):
        _require_nonempty_words(overseas, key, f"sectors.overseas.{key}")
    _require_int(overseas, "leadChars", "sectors.overseas.leadChars")
    _require_int(overseas, "bodyChars", "sectors.overseas.bodyChars")
    listed = _require_words(overseas, "prefilterRules", "sectors.overseas.prefilterRules")
    for name in listed:
        if name not in rule_names:
            raise ValueError("sectors.overseas.prefilterRules 类型不对")


def _validate_fermenting(sectors: Mapping, rule_names: set[str]) -> None:
    fermenting = _require_dict(sectors, "fermenting", "sectors.fermenting")
    _require_int(fermenting, "recentDays", "sectors.fermenting.recentDays")
    _require_int(fermenting, "baseDays", "sectors.fermenting.baseDays")
    _require_positive(fermenting, "growth", "sectors.fermenting.growth")
    _require_positive(fermenting, "minDaily", "sectors.fermenting.minDaily")
    _require_int(fermenting, "bodyChars", "sectors.fermenting.bodyChars")
    listed = _require_words(fermenting, "skipRules", "sectors.fermenting.skipRules")
    for name in listed:
        if name not in rule_names:
            raise ValueError("sectors.fermenting.skipRules 类型不对")
    enabled = _require(fermenting, "enabled", "sectors.fermenting.enabled")
    if not isinstance(enabled, bool):
        raise ValueError("sectors.fermenting.enabled 类型不对")
    aliases = _require(fermenting, "aliases", "sectors.fermenting.aliases")
    if not isinstance(aliases, dict):
        raise ValueError("sectors.fermenting.aliases 类型不对")
    for key, value in aliases.items():
        if not isinstance(key, str) or not key or not isinstance(value, list):
            raise ValueError("sectors.fermenting.aliases 类型不对")
        if any(not isinstance(item, str) or not item for item in value):
            raise ValueError("sectors.fermenting.aliases 类型不对")


def _validate_recognition(sectors: Mapping) -> None:
    recognition = _require_dict(sectors, "recognition", "sectors.recognition")
    _require_int(recognition, "lookbackDays", "sectors.recognition.lookbackDays")
    hot = _require_dict(recognition, "hot", "sectors.recognition.hot")
    _require_int(hot, "minOthers", "sectors.recognition.hot.minOthers", minimum=0)
    share = _require(hot, "minShare", "sectors.recognition.hot.minShare")
    if isinstance(share, bool) or not isinstance(share, (int, float)):
        raise ValueError("sectors.recognition.hot.minShare 类型不对")
    number = float(share)
    if number != number or number in (float("inf"), float("-inf")) or not 0 <= number < 1:
        raise ValueError("sectors.recognition.hot.minShare 类型不对")


def _validate_selection(sectors: Mapping) -> None:
    selection = _require_dict(sectors, "selection", "sectors.selection")
    if _require_str(selection, "contract", "sectors.selection.contract") != SELECTION_CONTRACT:
        raise ValueError(f"sectors.selection.contract 须为 {SELECTION_CONTRACT}")
    if not isinstance(_require(selection, "modelOptions", "sectors.selection.modelOptions"), dict):
        raise ValueError("sectors.selection.modelOptions 类型不对")


def _validate_search(search: Mapping) -> None:
    endpoint = _require_str(search, "endpoint", "sectors.evaluation.search.endpoint")
    if not endpoint.startswith("https://"):
        raise ValueError("sectors.evaluation.search.endpoint 须以 https 开头")
    count = _require_int(search, "count", "sectors.evaluation.search.count")
    if not 1 <= count <= 50:
        raise ValueError("sectors.evaluation.search.count 须为 1–50 的整数")
    _require_int(search, "summaryChars", "sectors.evaluation.search.summaryChars")
    timeout = _require(search, "timeoutSeconds", "sectors.evaluation.search.timeoutSeconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("sectors.evaluation.search.timeoutSeconds 类型不对")
    _require_int(search, "maxAttempts", "sectors.evaluation.search.maxAttempts")


def _compile_rules(settings: Mapping) -> list[dict]:
    compiled = []
    for rule in settings["rules"]:
        item = {
            "name": rule["name"],
            "scope": rule["scope"],
            "pattern": re.compile(rule["pattern"]),
            "prefixChars": rule.get("prefixChars"),
            "absent": re.compile(rule["absentPattern"]) if "absentPattern" in rule else None,
            "absentChars": rule.get("absentChars"),
        }
        compiled.append(item)
    return compiled


def opening_text(message: Message, body_head_chars: int) -> str:
    """有标题用标题，没有标题用正文前 body_head_chars 个字。"""
    title = (message.title or "").strip()
    if title:
        return title
    return (message.text or "")[:body_head_chars]


def apply_prefilter(messages: Sequence[Message], settings: Mapping) -> tuple[list[Message], dict[str, int]]:
    """按配置顺序，命中第一条就去掉。返回留下的原对象和每条规则去掉的条数。"""
    drop_sources = set(settings["dropSources"])
    body_chars = int(settings["bodyHeadChars"])
    compiled = _compile_rules(settings)
    dropped = {"dropSources": 0, **{rule["name"]: 0 for rule in compiled}}
    kept: list[Message] = []
    for message in messages:
        if message.source_key in drop_sources:
            dropped["dropSources"] += 1
            continue
        opening = opening_text(message, body_chars)
        hit = None
        for rule in compiled:
            if _rule_hit(rule, message, opening):
                hit = rule["name"]
                break
        if hit is not None:
            dropped[hit] += 1
            continue
        kept.append(message)
    return kept, dropped


def _rule_hit(rule: Mapping, message: Message, opening: str) -> bool:
    scope = rule["scope"]
    if scope == "title":
        target = message.title or ""
    elif scope == "openingPrefix":
        target = opening[:int(rule["prefixChars"])]
    else:
        target = opening
    if rule["pattern"].search(target) is None:
        return False
    absent = rule["absent"]
    if absent is not None:
        haystack = (message.title or "") + (message.text or "")[:int(rule["absentChars"])]
        if absent.search(haystack):
            return False
    return True


def assign_numbers(messages: Sequence[Message]) -> list[tuple[str, Message]]:
    ordered = sorted(messages, key=lambda message: (message.published_at, message.ref))
    return [(f"n{index}", message) for index, message in enumerate(ordered, start=1)]


def _word_pattern(words: Sequence[str]) -> re.Pattern[str]:
    """名单逐词转义，长词优先。空名单不命中任何文本。"""
    unique: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word not in seen:
            seen.add(word)
            unique.append(word)
    unique.sort(key=len, reverse=True)
    if not unique:
        return re.compile(r"(?!)")
    return re.compile("|".join(re.escape(word) for word in unique))


def apply_intake(messages: Sequence[Message], settings: Mapping, *, body_head_chars: int,
                 ) -> tuple[list[Message], dict[str, int]]:
    """粗筛之后收 A 路。命中 macro、noise 或三类都不是的去掉；三类留下。"""
    patterns = _intake_patterns(settings)
    lead = int(settings["bigLeadChars"])
    counts = {"publisher": 0, "bigCompany": 0, "document": 0, "macro": 0, "noise": 0, "outside": 0, "kept": 0}
    kept: list[Message] = []
    for message in messages:
        kind = _intake_class(opening_text(message, body_head_chars), patterns, lead)
        counts[kind] += 1
        if kind in _INTAKE_KEPT:
            kept.append(message)
            counts["kept"] += 1
    return kept, counts


def _intake_patterns(settings: Mapping) -> dict[str, re.Pattern[str]]:
    return {key: _word_pattern(settings[key]) for key in (
        "macro", "noise", "publishers", "foreign", "diplomacy", "bigCompanies", "bigEvents",
        "documents", "documentScope", "documentExclude")}


def _intake_class(opening: str, patterns: Mapping[str, re.Pattern[str]], lead: int) -> str:
    if patterns["macro"].search(opening):
        return "macro"
    if patterns["noise"].search(opening):
        return "noise"
    foreign = patterns["foreign"].search(opening) is not None
    if (patterns["publishers"].search(opening) and not foreign
            and patterns["diplomacy"].search(opening) is None):
        return "publisher"
    company = patterns["bigCompanies"].search(opening)
    if company is not None and company.start() < lead and patterns["bigEvents"].search(opening):
        return "bigCompany"
    if (patterns["documents"].search(opening) and patterns["documentScope"].search(opening)
            and patterns["documentExclude"].search(opening) is None and not foreign):
        return "document"
    return "outside"


def _joined_text(message: Message, body_chars: int) -> str:
    title = (message.title or "").strip()
    body = (message.text or "")[:body_chars]
    return f"{title}\n{body}"


def _names_in_text(text: str, names: Sequence[Mapping]) -> list[dict]:
    found = []
    for item in names:
        words = [item["name"], *(item.get("aliases") or [])]
        if _word_pattern(words).search(text):
            found.append({"name": item["name"], "industry": item["industry"], "note": item["note"]})
    return found


def _event_overseas_notes(messages: Sequence[Message], overseas: Mapping) -> list[dict]:
    body_chars = int(overseas["bodyChars"])
    blob = "\n".join(_joined_text(message, body_chars) for message in messages)
    return _names_in_text(blob, overseas["names"])


def collect_routes(messages: Sequence[Message], sectors: Mapping, *,
                   boards: Sequence[Mapping]) -> tuple[list[Message], dict[str, dict], dict[str, int]]:
    """三路各自判断，合成一份。同一条消息只留一次，带上路集合和 C 路板块。"""
    prefilter = sectors["prefilter"]
    head = int(prefilter["bodyHeadChars"])
    kept, dropped = apply_prefilter(messages, prefilter)
    accepted, intake_counts = apply_intake(kept, sectors["intake"], body_head_chars=head)
    patterns = _intake_patterns(sectors["intake"])
    overseas = sectors["overseas"]
    compiled = _compile_rules(prefilter)
    by_name = {rule["name"]: rule for rule in compiled}
    drop_sources = set(prefilter["dropSources"])
    lead = int(overseas["leadChars"])
    body_chars = int(overseas["bodyChars"])
    exclude = _word_pattern(overseas["exclude"])
    name_words = [word for item in overseas["names"] for word in (item["name"], *(item["aliases"] or []))]
    name_pattern = _word_pattern(name_words)
    moves = _word_pattern(overseas["moves"])
    events = _word_pattern(overseas["events"])
    industries = _word_pattern(overseas["industries"])
    markets = _word_pattern(overseas["markets"])
    named_rules = [by_name[name] for name in overseas["prefilterRules"]]
    board_patterns = [(item["name"], item["pattern"]) for item in boards]
    ferment_chars = int(sectors["fermenting"]["bodyChars"])
    info: dict[str, dict] = {}
    order: list[Message] = []

    def touch(message: Message) -> dict:
        slot = info.get(message.ref)
        if slot is None:
            slot = {"message": message, "routes": set(), "boards": []}
            info[message.ref] = slot
            order.append(message)
        return slot

    for message in accepted:
        touch(message)["routes"].add("A")
    for message in messages:
        if message.source_key in drop_sources:
            continue
        opening = opening_text(message, head)
        if any(_rule_hit(rule, message, opening) for rule in named_rules):
            continue
        text = _joined_text(message, body_chars)
        if exclude.search(text):
            continue
        name_hit = name_pattern.search(opening)
        cond_a = name_hit is not None and name_hit.start() < lead and (
            moves.search(text) is not None or events.search(text) is not None)
        cond_b = (industries.search(opening) is not None and markets.search(text) is not None
                  and moves.search(text) is not None)
        if cond_a or cond_b:
            touch(message)["routes"].add("B")
    for message in kept:
        opening = opening_text(message, head)
        if patterns["macro"].search(opening) or patterns["noise"].search(opening):
            continue
        text = _joined_text(message, ferment_chars)
        hits = [name for name, pattern in board_patterns if pattern.search(text)]
        if not hits:
            continue
        slot = touch(message)
        slot["routes"].add("C")
        for name in hits:
            if name not in slot["boards"]:
                slot["boards"].append(name)
    order.sort(key=lambda message: (message.published_at, message.ref))
    routes = {
        "A": sum(1 for slot in info.values() if "A" in slot["routes"]),
        "B": sum(1 for slot in info.values() if "B" in slot["routes"]),
        "C": sum(1 for slot in info.values() if "C" in slot["routes"]),
        "kept": len(info),
    }
    meta = {
        ref: {"routes": [route for route in _ROUTE_ORDER if route in slot["routes"]], "boards": list(slot["boards"])}
        for ref, slot in info.items()
    }
    return order, meta, {"dropped": dropped, "intake": intake_counts, "routes": routes, "prefilterKept": len(kept)}


def backflow_messages(classification: Any, settings: Mapping) -> dict[str, tuple[Message, set[str]]]:
    """从主线分类结果取出回流消息。返回 ref → (消息, 点名公司六位代码)。"""
    if classification is None:
        return {}
    relations = set(settings["relations"])
    types = set(settings["types"])
    excluded = set(settings["excludeDirections"])
    by_id = classification.by_id
    blocked: set[str] = set()
    for result in classification.results.values():
        for event in result.events:
            if event.relation == "主体" and event.type != "行业动态":
                for mid in event.ids:
                    message = by_id.get(mid)
                    if message is not None:
                        blocked.add(message.ref)
    found: dict[str, tuple[Message, set[str]]] = {}
    for result in classification.results.values():
        code = _short(result.company)
        if not code:
            continue
        for event in result.events:
            if event.direction in excluded:
                continue
            if event.relation not in relations and event.type not in types:
                continue
            for mid in event.ids:
                message = by_id.get(mid)
                if message is None or message.ref in blocked:
                    continue
                slot = found.get(message.ref)
                if slot is None:
                    found[message.ref] = (message, {code})
                else:
                    slot[1].add(code)
    return found


def merge_sector_inputs(unmatched: Sequence[Message], backflow: Mapping[str, tuple[Message, set[str]]],
                        ) -> tuple[list[Message], dict[str, set[str]], set[str]]:
    """未匹配和回流合成一份。同一条消息只留一次，点名公司取并集。"""
    named: dict[str, set[str]] = {}
    order: list[Message] = []
    seen: set[str] = set()
    for message in unmatched:
        if message.ref in seen:
            continue
        seen.add(message.ref)
        order.append(message)
        named[message.ref] = set()
    for ref, (message, codes) in backflow.items():
        if ref in seen:
            named[ref].update(codes)
            continue
        seen.add(ref)
        order.append(message)
        named[ref] = set(codes)
    return order, named, set(backflow)


def event_id(refs: Sequence[str]) -> str:
    """事件所含消息 ref 排序后的 sha256 前 16 位。同一组消息得到同一个编号。"""
    payload = "\n".join(sorted(refs))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def parse_mapping_output(content: str, allowed: set[str], names: set[str]) -> list[dict] | str:
    """板块名必须原样在清单里，否则视同 null。一个编号只留在第一个事件里。"""
    try:
        data = json.loads(content)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return "输出不是 JSON 对象"
    seen: set[str] = set()
    events: list[dict] = []
    for raw in data["events"]:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        if not isinstance(raw.get("ids"), list) or not isinstance(raw.get("repeat"), bool):
            continue
        ids: list[str] = []
        for item in raw["ids"]:
            if isinstance(item, str) and item in allowed and item not in seen:
                seen.add(item)
                ids.append(item)
        if not ids:
            continue
        sector = _sector_name(raw.get("sector"), names)
        reason = raw.get("reason") if isinstance(raw.get("reason"), str) else ""
        events.append({
            "title": title.strip(), "ids": ids, "sector": sector, "reason": reason, "repeat": raw["repeat"],
        })
    return events


def _sector_name(raw: object, names: set[str]) -> str | None:
    """清单名必须完全一致。模型若写成「名称｜数字」，只去掉末尾这一截再认。"""
    if isinstance(raw, str) and raw in names:
        return raw
    if isinstance(raw, str):
        bare = _COUNT_SUFFIX.sub("", raw)
        if bare != raw and bare in names:
            return bare
    return None


def _push_key(row: Mapping) -> tuple[str, str]:
    package = row.get("package") if isinstance(row.get("package"), Mapping) else {}
    event = package.get("event") if isinstance(package.get("event"), Mapping) else {}
    published = event.get("firstPublished") if isinstance(event.get("firstPublished"), str) else ""
    return published, str(row.get("event_id") or "")


def pushed_rows(rows: Sequence[Mapping]) -> list[Mapping]:
    """判推且评估成功的事件，按首次公开时间、事件编号排序。"""
    chosen = [
        row for row in rows
        if row.get("state") == "succeeded" and row.get("verdict") == "推" and isinstance(row.get("output"), dict)
    ]
    chosen.sort(key=_push_key)
    return chosen


def select_pushed(rows: Sequence[Mapping], max_events: int, *,
                  chosen_ids: Sequence[str] | None = None) -> tuple[list[Mapping], int]:
    """不超过上限就全推。超过时只交付比较选出的编号；没选出就一个都不推。展示仍按时间。"""
    chosen = pushed_rows(rows)
    if len(chosen) <= max_events:
        return chosen, 0
    if not chosen_ids:
        return [], len(chosen)
    allowed = set(chosen_ids)
    shown = [row for row in chosen if str(row.get("event_id") or "") in allowed]
    return shown, len(chosen) - len(shown)


def parse_selection_output(content: str, allowed: set[str], limit: int) -> dict | str:
    """chosen 必须正好是上限个、来自材料、且不重复的编号。"""
    try:
        data = json.loads(content)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return "输出不是 JSON 对象"
    chosen = data.get("chosen")
    if not isinstance(chosen, list) or len(chosen) != limit:
        return "chosen 个数不对"
    if any(not isinstance(item, str) for item in chosen) or len(set(chosen)) != len(chosen):
        return "chosen 有重复或不是字符串"
    if any(item not in allowed for item in chosen):
        return "chosen 有材料里没有的编号"
    reason = data.get("reason")
    if not isinstance(reason, str):
        return "reason 不是字符串"
    return {"chosen": list(chosen), "reason": reason}


def _selection_clock(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    text = value.replace("T", " ", 1)
    for mark in ("+", "Z"):
        if mark in text:
            text = text.split(mark, 1)[0]
    return text[:16]


def _selection_material(rows: Sequence[Mapping], pool: CompanyPool) -> list[dict]:
    material = []
    for row in rows:
        output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
        package = row.get("package") if isinstance(row.get("package"), Mapping) else {}
        event = package.get("event") if isinstance(package.get("event"), Mapping) else {}
        sector = package.get("sector") if isinstance(package.get("sector"), Mapping) else {}
        leaders = []
        for item in output.get("leaders") or []:
            if not isinstance(item, Mapping):
                continue
            code = _short(item.get("code"))
            leaders.append({
                "code": code,
                "name": _company_name(pool, code),
                "why": item.get("why") if isinstance(item.get("why"), str) else "",
            })
        material.append({
            "event_id": str(row.get("event_id") or ""),
            "routes": list(package.get("routes") or []),
            "sector": sector.get("name") if isinstance(sector.get("name"), str) else (row.get("sector") or ""),
            "firstPublished": _selection_clock(event.get("firstPublished")),
            "title": output.get("title") if isinstance(output.get("title"), str) else "",
            "conclusion": output.get("conclusion") if isinstance(output.get("conclusion"), str) else "",
            "risk": output.get("risk") if isinstance(output.get("risk"), str) else "",
            "leaders": leaders,
        })
    return material


def _add_selection_hit(block: dict, hit: Mapping) -> None:
    block["calls"] = int(block.get("calls") or 0) + int(hit.get("calls") or 0)
    block["fromReceipt"] = int(block.get("fromReceipt") or 0) + int(hit.get("fromReceipt") or 0)
    block["usage"] = _add_usage(block.get("usage") or _zero_usage(), hit.get("usage") or {})
    block["seconds"] = round(float(block.get("seconds") or 0) + float(hit.get("seconds") or 0), 3)


def _select_delivery(rows: Sequence[Mapping], *, max_events: int, client: Any, options: Mapping,
                     pool: CompanyPool, cutoff: str, clock: Callable[[], datetime],
                     stats: dict) -> tuple[list[Mapping], int, str | None]:
    """判推超过上限，或同一板块有两个以上判推时，比较一次。修一次仍不行、失败、不明或到点，当天一个都不推。"""
    pushed = pushed_rows(rows)
    block = stats["selection"]
    block["candidates"] = len(pushed)
    board_names: list[str] = []
    for row in pushed:
        name = _pushed_sector(row)
        if name not in board_names:
            board_names.append(name)
    block["boards"] = len(board_names)
    repeated = len(board_names) < len(pushed)
    limit = min(max_events, len(board_names)) if pushed else 0
    if not repeated and len(pushed) <= max_events:
        block["chosen"] = [str(row.get("event_id") or "") for row in pushed]
        return pushed, 0, None
    gap = _SELECTION_GAP.format(count=len(pushed))
    if _cutoff_reached(clock(), cutoff):
        block["error"] = "收口时刻前没比较"
        return [], len(pushed), gap
    material = _selection_material(pushed, pool)
    labels = {f"e{index}": str(row.get("event_id") or "") for index, row in enumerate(pushed, start=1)}
    by_id = {str(row.get("event_id") or ""): row for row in pushed}
    messages = [
        {"role": "system", "content": SELECTION_SYSTEM},
        {"role": "user", "content": selection_user_prompt(limit, material)},
    ]
    block["called"] = True
    hit = _call_model(client, messages, options)
    _add_selection_hit(block, hit)
    if hit.get("cutoff") or _cutoff_reached(clock(), cutoff):
        block["error"] = "收口时刻前没比较"
        return [], len(pushed), gap
    if hit.get("fatal") or hit.get("unknown") or hit.get("error"):
        block["error"] = str(hit.get("error") or hit.get("fatal") or "比较没完成")
        return [], len(pushed), gap
    parsed: dict | str
    if hit.get("finish") != "stop":
        parsed = f"finish:{hit.get('finish')}"
    else:
        parsed = parse_selection_output(hit.get("content") or "", set(labels), limit)
    parsed = _unique_boards(parsed, labels, by_id)
    if not isinstance(parsed, str):
        return _chosen_rows(parsed, labels, by_id, block, len(pushed))
    if _cutoff_reached(clock(), cutoff):
        block["error"] = "收口时刻前没比较"
        return [], len(pushed), gap
    repair_messages = [
        messages[0], messages[1],
        {"role": "assistant", "content": hit.get("content") or ""},
        {"role": "user", "content": _SELECTION_REPAIR.format(reason=parsed)},
    ]
    repaired = _call_model(client, repair_messages, options)
    block["repairs"] = 1 if repaired.get("calls") or repaired.get("fromReceipt") or repaired.get("error") else 0
    if repaired.get("unknown") and not repaired.get("calls"):
        block["repairs"] = 0
    _add_selection_hit(block, repaired)
    if repaired.get("cutoff") or _cutoff_reached(clock(), cutoff):
        block["error"] = "收口时刻前没比较"
        return [], len(pushed), gap
    if repaired.get("fatal") or repaired.get("unknown") or (
            repaired.get("error") and not repaired.get("content")):
        block["error"] = str(repaired.get("error") or repaired.get("fatal") or "比较没完成")
        return [], len(pushed), gap
    if repaired.get("finish") != "stop":
        again: dict | str = f"finish:{repaired.get('finish')}"
    else:
        again = parse_selection_output(repaired.get("content") or "", set(labels), limit)
    again = _unique_boards(again, labels, by_id)
    if isinstance(again, str):
        block["error"] = f"{parsed}；修正后仍不合格：{again}"
        return [], len(pushed), gap
    return _chosen_rows(again, labels, by_id, block, len(pushed))


def _pushed_sector(row: Mapping) -> str:
    package = row.get("package") if isinstance(row.get("package"), Mapping) else {}
    sector = package.get("sector") if isinstance(package.get("sector"), Mapping) else {}
    name = sector.get("name")
    if isinstance(name, str) and name:
        return name
    raw = row.get("sector")
    return raw if isinstance(raw, str) else ""


def _unique_boards(parsed: dict | str, labels: Mapping[str, str], by_id: Mapping[str, Mapping]) -> dict | str:
    """选中的事件不能落在同一个板块。重复视为不合格，走现有的修正一次。"""
    if isinstance(parsed, str):
        return parsed
    seen: list[str] = []
    for label in parsed["chosen"]:
        name = _pushed_sector(by_id[labels[label]])
        if name in seen:
            return "选中的板块重复"
        seen.append(name)
    return parsed


def _chosen_rows(parsed: Mapping, labels: Mapping[str, str], by_id: Mapping[str, Mapping],
                 block: dict, total: int) -> tuple[list[Mapping], int, None]:
    ids = [labels[item] for item in parsed["chosen"]]
    block["chosen"] = ids
    block["reason"] = parsed.get("reason")
    block["error"] = None
    shown = [by_id[item] for item in ids if item in by_id]
    shown.sort(key=_push_key)
    return shown, total - len(shown), None


def _cutoff_reached(now: datetime, cutoff: str) -> bool:
    if now.tzinfo is None:
        raise ValueError("时间必须带时区")
    current = now.astimezone(CST)
    hour, minute = cutoff.split(":")
    mark = current.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    return current >= mark


def _published(ts: datetime) -> str:
    return ts.astimezone(CST).isoformat(timespec="seconds")


def _minutes(ts: datetime) -> str:
    return ts.astimezone(CST).isoformat(timespec="minutes")


def _short(value: object) -> str:
    text = str(value or "").strip().upper().split(".")[0]
    return text if len(text) == 6 and text.isdigit() else ""


def _company_name(pool: CompanyPool, code: str) -> str:
    company = pool.companies.get(code)
    if company is not None:
        return company.name
    short = code.split(".")[0]
    for item in pool.companies.values():
        if item.short_code == short:
            return item.name
    return short


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _known_refs(package: Mapping) -> set[str]:
    from neckline.morning.report import _ref_index

    return set(_ref_index(package))


def parse_evaluation_output(content: str, package: Mapping) -> dict | str:
    """格式不合格时返回原因。判推但核对后没有承接股的，改记不推。对不上的观点删掉并计数。"""
    try:
        data = json.loads(content)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return "输出不是 JSON 对象"
    verdict = _text(data.get("verdict"))
    if verdict not in _VERDICTS:
        return "verdict 无效"
    title = _text(data.get("title"))
    if not title:
        return "title 为空"
    conclusion = _text(data.get("conclusion"))
    if not conclusion:
        return "conclusion 为空"
    risk = _text(data.get("risk"))
    if not risk:
        return "risk 为空"
    leaders = _leaders(data.get("leaders"), package)
    if isinstance(leaders, str):
        return leaders
    views = _views(data.get("views"), _known_refs(package))
    if isinstance(views, str):
        return views
    kept, dropped = views
    output = {
        "title": title, "verdict": verdict, "conclusion": conclusion,
        "leaders": leaders, "risk": risk, "views": kept, "droppedViews": dropped,
    }
    if verdict == "推" and not leaders:
        output["verdict"] = "不推"
        output["note"] = _NO_CARRIER
        output["vetoed"] = True
    return output


def _carrier_max(package: Mapping) -> int | str:
    carriers = package.get("carriers")
    if not isinstance(carriers, Mapping):
        return "承接股上限缺失"
    maximum = carriers.get("max")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        return "承接股上限缺失"
    return maximum


def _member_codes(package: Mapping) -> set[str]:
    found: set[str] = set()
    for item in package.get("members") or []:
        if isinstance(item, Mapping):
            code = _short(item.get("code"))
            if code:
                found.add(code)
    return found


def _leaders(raw: object, package: Mapping) -> list[dict] | str:
    """只留名单里的代码，不重复，最多 carriers.max，按名单（辨识度）顺序。点名公司不再自动补上。"""
    if not isinstance(raw, list):
        return "leaders 不是列表"
    maximum = _carrier_max(package)
    if isinstance(maximum, str):
        return maximum
    allowed = _member_codes(package)
    model: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            return "leaders 格式不对"
        why = item.get("why")
        price = item.get("price")
        if not isinstance(why, str) or not why.strip():
            return "leaders 理由为空"
        if not isinstance(price, str):
            return "leaders 价格不是字符串"
        code = _short(item.get("code"))
        if code not in allowed or code in model:
            continue
        model[code] = {"code": code, "why": why.strip(), "price": price}
    order: list[str] = []
    for item in package.get("members") or []:
        if not isinstance(item, Mapping):
            continue
        code = _short(item.get("code"))
        if code and code not in order:
            order.append(code)
    ordered: list[dict] = []
    for code in order:
        if code not in model or len(ordered) >= maximum:
            continue
        ordered.append(model[code])
    return ordered


def _views(raw: object, refs: set[str]) -> tuple[list[dict], int] | str:
    if not isinstance(raw, list):
        return "views 不是列表"
    kept: list[dict] = []
    dropped = 0
    for item in raw:
        if not isinstance(item, Mapping):
            dropped += 1
            continue
        by = _text(item.get("by"))
        name = _text(item.get("name"))
        text = _text(item.get("text"))
        ref = item.get("ref")
        ref_text = ref.strip() if isinstance(ref, str) else ""
        if by not in _VIEWS or not name or not text or ref_text not in refs:
            dropped += 1
            continue
        kept.append({"by": by, "name": name, "text": text, "ref": ref_text})
    return kept, dropped


def _search_query(title: str, sector: str, trade_date: date) -> tuple[str, str]:
    body = f"{title} {sector}".strip()
    suffix = f"before:{(trade_date + timedelta(days=1)).isoformat()}"
    tail = f" {suffix}"
    if not body:
        query = suffix[:_QUERY_LIMIT]
    elif len(body) + len(tail) <= _QUERY_LIMIT:
        query = body + tail
    else:
        query = body[:_QUERY_LIMIT - len(tail)] + tail
    question = f"这件事对{sector}板块影响多大，媒体和券商怎么看"
    return query, question[:_QUERY_LIMIT]


def _search_block(client: Any, settings: Mapping, *, title: str, sector: str, trade_date: date,
                  collected_by: datetime) -> dict:
    query, ori = _search_query(title, sector, trade_date)
    if client is None:
        return _failure(query, "no_key")
    try:
        outcome = client.search(query=query, count=int(settings["count"]), ori_question=ori)
    except _StillOpen:
        return {"cutoff": True}
    if outcome.error:
        return _failure(query, outcome.error, seconds=outcome.seconds, from_receipt=outcome.from_receipt)
    payload = outcome.payload if isinstance(outcome.payload, Mapping) else {}
    raw = payload.get("results") if isinstance(payload, Mapping) else None
    kept, dropped, returned = project_results(
        raw if isinstance(raw, list) else [], collected_by=collected_by,
        summary_chars=int(settings["summaryChars"]))
    return {
        "enabled": True, "query": query, "results": kept, "returned": returned, "dropped": dropped,
        "error": None, "fromReceipt": bool(outcome.from_receipt), "seconds": float(outcome.seconds),
    }


def _lookup_company(pool: CompanyPool, code: str):
    short = _short(code)
    company = pool.companies.get(str(code)) or pool.companies.get(f"{short}.SZ")
    if company is None:
        company = next((item for item in pool.companies.values() if item.short_code == short), None)
    return short, company


def _member_rows(event: Mapping, pool: CompanyPool, skip: Sequence[str], profiles: Mapping,
                 pool_rows: Sequence[Mapping]) -> list[dict]:
    """板块的池内成分股，加上本事件回流点名、且还在池内的公司。两边都去掉当天主线名单。按代码排序。"""
    skipped = {_short(code) for code in skip}
    skipped.discard("")
    named = {_short(code) for code in event.get("named") or ()}
    named.discard("")
    stored: dict[str, Mapping] = {}
    for row in pool_rows:
        if isinstance(row, Mapping):
            short = _short(row.get("code"))
            if short:
                stored[short] = row
    chosen: list[tuple[str, Any]] = []
    seen: set[str] = set()
    codes = list(event.get("members") or [])
    codes.extend(event.get("named") or [])
    for code in codes:
        short, company = _lookup_company(pool, str(code))
        if not short or short in skipped or short in seen or company is None:
            continue
        seen.add(short)
        chosen.append((short, company))
    chosen.sort(key=lambda item: item[0])
    rows = []
    for short, company in chosen:
        detail = stored.get(short) or {}
        summary = detail.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            fallback = profiles.get(company.code) or profiles.get(short)
            summary = fallback if isinstance(fallback, str) and fallback.strip() else None
        circ = detail.get("circMv")
        rows.append({
            "code": short,
            "name": company.name,
            "summary": summary,
            "circMv": circ if isinstance(circ, (int, float)) and not isinstance(circ, bool) else None,
            "named": short in named,
        })
    return rows


def _identity_keyword(name: str, keywords: Sequence[str]) -> str | None:
    for word in keywords:
        if isinstance(word, str) and word and word in name:
            return word
    return None


def build_event_package(*, event: Mapping, day: date, window: Window, pool: CompanyPool,
                         carriers: Mapping, skip: Sequence[str], search_client: Any, search_settings: Mapping,
                         package_settings: Mapping, profiles: Mapping | None = None,
                         pool_rows: Sequence[Mapping] = (), identity_keywords: Sequence[str] = ()) -> dict:
    """一个事件的评估数据包。同样的材料得到同样的 dict。"""
    messages = sorted(event["messages"], key=lambda message: (message.published_at, message.ref))
    numbers = {message.ref: f"m{index}" for index, message in enumerate(messages, start=1)}
    listed = [{
        "id": numbers[message.ref], "ref": f"{message.document_id}@{message.revision}",
        "source": message.source_label,
        "publishedAt": _published(message.published_at), "title": message.title, "url": message.url,
    } for message in messages]
    checked = {key: int(package_settings[key]) for key in _PACKAGE_KEYS}
    gaps: list[str] = []
    documents = []
    if messages:
        message = messages[0]
        identity = {
            "id": numbers[message.ref], "message": numbers[message.ref], "source": message.source_label,
            "title": message.title, "url": message.url, "publishedAt": _published(message.published_at),
        }
        documents.append(_present(message.text or "", None, False, None, checked, identity))
    media_messages = messages[1:]
    limit = checked["maxMedia"]
    if len(media_messages) > limit:
        gaps.append(f"媒体报道超过 {limit} 条，只附了前 {limit} 条")
    media = []
    for message in media_messages[:limit]:
        body = message.text or ""
        media.append({
            "message": numbers[message.ref], "source": message.source_label, "title": message.title,
            "publishedAt": _published(message.published_at), "text": body[:checked["mediaChars"]],
            "chars": len(body), "truncated": len(body) > checked["mediaChars"],
        })
    prepared = event.get("preparedMembers")
    if isinstance(prepared, list):
        member_rows = [dict(item) for item in prepared if isinstance(item, Mapping)]
    else:
        member_rows = _member_rows(event, pool, skip, profiles or {}, pool_rows)
    if not member_rows:
        gaps.append("板块里没有可选的成分股")
    search = _search_block(
        search_client, search_settings, title=str(event["title"]), sector=str(event["sector"]),
        trade_date=day, collected_by=window.collected_by)
    if search.get("cutoff"):
        raise _StillOpen()
    error = search.get("error")
    if isinstance(error, str) and error:
        gaps.append(f"联网搜索失败：{error}")
    return {
        "report": {
            "tradeDate": day.isoformat(),
            "windowStart": _published(window.start),
            "windowEnd": _published(window.end),
        },
        "event": {
            "id": event["id"], "title": event["title"], "reason": event["reason"],
            "firstPublished": event["firstPublished"],
        },
        "sector": {
            "name": event["sector"], "code": event["sectorCode"],
            "memberCount": len(event.get("members") or []),
        },
        "messages": listed,
        "original": {"kind": "news", "documents": documents, "media": media},
        "carriers": {"max": int(carriers["max"])},
        "identityKeyword": _identity_keyword(str(event.get("sector") or ""), identity_keywords),
        "routes": list(event.get("routes") or []),
        "overseasNotes": [dict(item) for item in event.get("overseasNotes") or []],
        "fermenting": [dict(item) for item in event.get("fermenting") or []],
        "recognition": {"lookbackDays": event.get("lookbackDays")},
        "members": member_rows,
        "limits": {key: checked[key] for key in _PACKAGE_KEYS},
        "search": search,
        "gaps": gaps,
    }


def _content(candidates: int, evaluated: int, overflow: int, entries: list[dict]) -> dict:
    return {"candidates": candidates, "evaluated": evaluated, "overflow": overflow, "entries": entries}


def _blank_stats(raw: int, dropped: Mapping[str, int] | None = None, kept: int = 0) -> dict:
    return {
        "raw": raw,
        "dropped": dict(dropped or {}),
        "kept": kept,
        "intake": {"publisher": 0, "bigCompany": 0, "document": 0, "macro": 0, "noise": 0, "outside": 0, "kept": 0},
        "routes": {"A": 0, "B": 0, "C": 0, "kept": 0},
        "backflow": {"messages": 0, "kept": 0},
        "fermenting": {"ran": False, "note": None, "boards": [], "messages": 0},
        "recognition": {"ran": False, "note": None, "lookbackDays": None, "days": [], "missing": [],
                        "fetched": 0, "emptyEvents": 0, "noFullMembers": 0},
        "selection": _blank_selection(),
        "mapping": {"calls": 0, "fromReceipt": 0, "splits": 0, "filtered": 0, "events": 0, "null": 0, "repeat": 0,
                    "usage": _zero_usage(), "seconds": 0.0},
        "evaluation": {"calls": 0, "fromReceipt": 0, "evaluated": 0, "verdicts": {"推": 0, "不推": 0},
                       "invalid": 0, "gap": 0, "vetoed": 0, "droppedViews": 0, "repairs": 0, "repaired": 0,
                       "usage": _zero_usage(), "seconds": 0.0},
        "overflow": 0,
        "search": summarize_search([], enabled=True),
    }


def _blank_selection() -> dict:
    return {
        "called": False, "candidates": 0, "chosen": [], "boards": 0, "reason": None, "error": None,
        "calls": 0, "fromReceipt": 0, "usage": _zero_usage(), "seconds": 0.0, "repairs": 0,
    }


def _load(db_path: Path, report_id: str, event_id: str) -> dict | None:
    with db.read(db_path) as conn:
        return db.get_sector_event(conn, report_id, event_id)


def _insert(db_path: Path, **kwargs: Any) -> dict | None:
    with db.write(db_path) as conn:
        db.insert_sector_event(conn, **kwargs)
        return db.get_sector_event(conn, kwargs["report_id"], kwargs["event_id"])


def _settle(db_path: Path, **kwargs: Any) -> dict | None:
    with db.write(db_path) as conn:
        db.settle_sector_event(conn, **kwargs)
        return db.get_sector_event(conn, kwargs["report_id"], kwargs["event_id"])


def _seal_gap(db_path: Path, report_id: str, event: Mapping, now: datetime) -> None:
    """到点还没结案的收成缺口。已经是终态的不动，迟到的成功因为不再是 prepared 而写不回去。"""
    row = _load(db_path, report_id, event["id"])
    if row is None:
        _insert(db_path, report_id=report_id, event_id=event["id"], title=event["title"],
                sector=event["sector"], reason=event["reason"], repeat=False, state="gap",
                package=_skipped_package(event), prepared_at=now, error=_GAP, settled_at=now,
                routes=list(event.get("routes") or []))
        row = _load(db_path, report_id, event["id"])
    if row is not None and row.get("state") == "prepared":
        _settle(db_path, report_id=report_id, event_id=event["id"], state="gap", settled_at=now, error=_GAP)


def _finish_stats(stats: dict, rows: Sequence[Mapping], *, eval_hits: Sequence[Mapping], overflow: int,
                  seconds: float) -> None:
    usage = _zero_usage()
    calls = from_receipt = repairs = repaired = 0
    for hit in eval_hits:
        calls += int(hit.get("calls") or 0)
        from_receipt += int(hit.get("fromReceipt") or 0)
        repairs += int(hit.get("repairs") or 0)
        repaired += int(hit.get("repaired") or 0)
        if hit.get("usage"):
            usage = _add_usage(usage, hit["usage"])
    verdicts = {"推": 0, "不推": 0}
    invalid = gap = vetoed = dropped = evaluated = 0
    for row in rows:
        state = row.get("state")
        if state == "succeeded" and row.get("verdict") in verdicts:
            evaluated += 1
            verdicts[str(row["verdict"])] += 1
            output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
            if output.get("vetoed"):
                vetoed += 1
            dropped += _as_int(output.get("droppedViews"))
        elif state == "invalid":
            invalid += 1
        elif state == "gap":
            gap += 1
    block = stats["evaluation"]
    block.update({
        "calls": calls, "fromReceipt": from_receipt, "evaluated": evaluated, "verdicts": verdicts,
        "invalid": invalid, "gap": gap, "vetoed": vetoed, "droppedViews": dropped,
        "repairs": repairs, "repaired": repaired, "usage": usage, "seconds": round(seconds, 3),
    })
    stats["overflow"] = overflow
    stats["search"] = summarize_search(list(rows), enabled=True)


def _public_entries(rows: Sequence[Mapping], pool: CompanyPool) -> list[dict]:
    from neckline.morning.report import _public_view, _ref_index

    entries = []
    for row in rows:
        output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
        package = row.get("package") if isinstance(row.get("package"), Mapping) else {}
        sources = _sources(package)
        index = _ref_index(package)
        views = []
        for view in output.get("views") or []:
            if isinstance(view, Mapping):
                views.append(_public_view(view, index, sources))
        leaders = []
        parsed = output.get("leaders") if isinstance(output.get("leaders"), list) else []
        for item in parsed:
            if not isinstance(item, Mapping):
                continue
            code = _short(item.get("code"))
            why = item.get("why") if isinstance(item.get("why"), str) else ""
            price = item.get("price") if isinstance(item.get("price"), str) else ""
            leaders.append({
                "companyCode": code,
                "companyName": _company_name(pool, code),
                "why": why,
                "price": price,
            })
        sector = package.get("sector") if isinstance(package.get("sector"), Mapping) else {}
        event = package.get("event") if isinstance(package.get("event"), Mapping) else {}
        member_count = sector.get("memberCount")
        entries.append({
            "title": output.get("title") if isinstance(output.get("title"), str) else "",
            "sector": {
                "name": sector.get("name") if isinstance(sector.get("name"), str) else (row.get("sector") or ""),
                "code": sector.get("code") if isinstance(sector.get("code"), str) else "",
                # 当期股票池里的成分股只数（App 显示「创业板 N 只」）
                "memberCount": member_count if isinstance(member_count, int) and not isinstance(member_count, bool)
                else None,
                # 身份类板块命中的关键词（App 标「身份类」），其他板块为 null
                "identity": package.get("identityKeyword") if isinstance(package.get("identityKeyword"), str)
                else None,
            },
            "conclusion": output.get("conclusion") if isinstance(output.get("conclusion"), str) else "",
            "leaders": leaders,
            "risk": output.get("risk") if isinstance(output.get("risk"), str) else "",
            "views": views,
            "sources": sources,
            "firstPublished": event.get("firstPublished") if isinstance(event.get("firstPublished"), str) else "",
        })
    return entries


def _sources(package: Mapping) -> list[dict]:
    sources = []
    for message in package.get("messages") or []:
        if not isinstance(message, Mapping):
            continue
        published = message.get("publishedAt")
        shown = published
        if isinstance(published, str):
            try:
                parsed = datetime.fromisoformat(published)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is not None:
                shown = _minutes(parsed)
        sources.append({
            "documentId": message.get("ref") if isinstance(message.get("ref"), str) else None,
            "sourceLabel": message.get("source") if isinstance(message.get("source"), str) else "",
            "publishedAt": shown,
            "title": message.get("title") if isinstance(message.get("title"), str) else None,
            "url": message.get("url") if isinstance(message.get("url"), str) else None,
        })
    return sources


def _previous_events(db_path: Path, day: date) -> list[tuple[str, str]]:
    try:
        prev = previous_trading_day(day)
    except CalendarGap:
        return []
    with db.read(db_path) as conn:
        report = db.get_report(conn, prev.isoformat())
    if not report or report.get("status") != "available":
        return []
    content = report.get("content") if isinstance(report.get("content"), Mapping) else {}
    sectors = content.get("sectors") if isinstance(content.get("sectors"), Mapping) else {}
    entries = sectors.get("entries") if isinstance(sectors.get("entries"), list) else []
    found = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        title = entry.get("title")
        sector = entry.get("sector") if isinstance(entry.get("sector"), Mapping) else {}
        name = sector.get("name")
        if isinstance(title, str) and title and isinstance(name, str) and name:
            found.append((title, name))
    return found


def _concepts(db_path: Path, pool: CompanyPool, rules: Mapping) -> tuple[list[dict], str | None]:
    with db.read(db_path) as conn:
        stored = db.read_sector_concepts(conn)
    note = _concept_note(stored)
    if not stored or not stored.get("concepts"):
        return [], note
    concepts = stored["concepts"]
    members = [{"tsCode": item["tsCode"], "code": code} for item in concepts for code in item.get("members") or []]
    listed = concept_list(concepts, members, list(pool.companies), rules)
    return list(listed["concepts"]), note


def _stored_concept_note(db_path: Path) -> str | None:
    with db.read(db_path) as conn:
        return _concept_note(db.read_sector_concepts(conn))


def _concept_reuse_note(stored: Mapping | None) -> str | None:
    """刷新失败且还留着上一份清单时，晨报缺口里写明沿用哪一天的。"""
    if not isinstance(stored, Mapping):
        return None
    error = stored.get("error")
    if not isinstance(error, str) or not error.strip():
        return None
    if not stored.get("concepts") or not stored.get("refreshedAt"):
        return None
    day = _beijing_day(stored.get("refreshedAt"))
    if not day:
        return None
    return f"板块清单没刷新成功，沿用 {day} 的"


def _concept_note(stored: Mapping | None) -> str | None:
    """刷新失败时的缺口。有上一份就写明沿用哪一天；一份都没有就如实说还没成功过。"""
    reuse = _concept_reuse_note(stored)
    if reuse:
        return reuse
    if not isinstance(stored, Mapping):
        return None
    error = stored.get("error")
    if not isinstance(error, str) or not error.strip():
        return None
    if stored.get("concepts"):
        return None
    return _CONCEPT_NEVER


def _beijing_day(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value[:10]
    if parsed.tzinfo is None:
        return parsed.date().isoformat()
    return parsed.astimezone(CST).date().isoformat()


class _StillOpen(Exception):
    """收口时刻到了，这次外呼还没回来。调用方按没评完处理，不再读它的结果。"""


class _CutoffReceipts:
    """过了收口时刻才落到的成功或失败，改记 unknown。续跑看到 started / unknown 都不重发。"""

    def __init__(self, inner: Any, cutoff: str, clock: Callable[[], datetime]) -> None:
        self._inner = inner
        self._cutoff = cutoff
        self._clock = clock

    def lookup(self, digest: str):
        return self._inner.lookup(digest)

    def begin(self, digest: str) -> None:
        # 过了收口就拒绝这次尝试：抛出后 post_json 不会再 POST，也不留没有回执的请求。
        # 已有的 unknown 不改动。
        if _cutoff_reached(self._clock(), self._cutoff):
            raise _StillOpen()
        self._inner.begin(digest)

    def succeed(self, digest: str, response: Mapping[str, Any], seconds: float) -> None:
        if _cutoff_reached(self._clock(), self._cutoff):
            self._inner.unknown(digest, "cutoff")
            return
        self._inner.succeed(digest, response, seconds)

    def fail(self, digest: str, code: str) -> None:
        if _cutoff_reached(self._clock(), self._cutoff):
            self._inner.unknown(digest, "cutoff")
            return
        self._inner.fail(digest, code)

    def unknown(self, digest: str, code: str) -> None:
        self._inner.unknown(digest, code)


class _Budgeted:
    """每次调用前收紧读超时，并且只等到收口时刻。

    读超时是两段数据之间的空闲时间，对方每隔一小段回一个字节就不会触发。
    所以真正的期限是业务时钟：到点主线程不再等。在途线程不是守护线程，
    进程退出时解释器仍会等它们结束，但晨报记录和推送已经写完。
    httpx 没有「整次请求」的总超时；要让这些线程在收口时断开，得从另一侧
    关掉这次连接。连接建在 post_json 里面，这里拿不到，所以只停止等待。
    同一次调用里的重试也看这个期限：post_json 每次尝试前问 may_attempt，过了就不再发。
    """

    def __init__(self, inner: Any, cutoff: str, clock: Callable[[], datetime]) -> None:
        self._inner = inner
        self._cutoff = cutoff
        self._clock = clock

    def complete(self, messages, options):
        inner = self._bound()
        return self._wait(lambda: inner.complete(messages, options))

    def search(self, **kwargs):
        inner = self._bound()
        return self._wait(lambda: inner.search(**kwargs))

    def _bound(self) -> Any:
        inner = _tighten(self._inner, self._cutoff, self._clock)
        if inner is self._inner:
            inner = copy.copy(self._inner)
        receipts = getattr(self._inner, "receipts", None)
        if receipts is not None:
            inner.receipts = _CutoffReceipts(receipts, self._cutoff, self._clock)
        inner.may_attempt = lambda: not _cutoff_reached(self._clock(), self._cutoff)
        return inner

    def _wait(self, fn: Callable[[], Any]) -> Any:
        return _wait_before_cutoff(fn, self._cutoff, self._clock)


def _wait_before_cutoff(fn: Callable[[], Any], cutoff: str, clock: Callable[[], datetime]) -> Any:
    """在单个工作线程里跑 ``fn``，主线程按分片等，只等到收口时刻。到点抛 ``_StillOpen``。

    不等在途调用。进程退出时这些非守护线程仍会被解释器 join。
    """
    if _cutoff_reached(clock(), cutoff):
        raise _StillOpen()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        while not future.done():
            if _cutoff_reached(clock(), cutoff):
                raise _StillOpen()
            remaining = _seconds_until(clock(), cutoff)
            if remaining <= 0:
                raise _StillOpen()
            wait((future,), timeout=min(_WAIT_SLICE, remaining))
        if _cutoff_reached(clock(), cutoff):
            raise _StillOpen()
        return future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _budget(client: Any, cutoff: str, clock: Callable[[], datetime]) -> Any:
    if client is None or not hasattr(client, "timeout"):
        return client
    return _Budgeted(client, cutoff, clock)


def _tighten(client: Any, cutoff: str, clock: Callable[[], datetime]) -> Any:
    remaining = _seconds_until(clock(), cutoff)
    read = getattr(client.timeout, "read", None)
    cap = 600.0 if read is None else float(read)
    if remaining >= cap:
        return client
    seconds = min(cap, max(1.0, remaining))
    bounded = copy.copy(client)
    bounded.timeout = httpx.Timeout(seconds, connect=min(15.0, seconds))
    return bounded


def _seconds_until(now: datetime, cutoff: str) -> float:
    if now.tzinfo is None:
        raise ValueError("时间必须带时区")
    current = now.astimezone(CST)
    hour, minute = cutoff.split(":")
    mark = current.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    return (mark - current).total_seconds()


def _abandoned_hit(**extra: Any) -> dict:
    hit = {"cutoff": True, "calls": 0, "fromReceipt": 0, "usage": _zero_usage(), "seconds": 0.0}
    hit.update(extra)
    return hit


def _open_hit(hit: Any) -> bool:
    return hit is None or (isinstance(hit, dict) and bool(hit.get("cutoff")))


def _run_calls(jobs: Sequence[Any], workers: int, fn: Callable[[Any], Any], *,
               cutoff: str, clock: Callable[[], datetime]) -> list[Any]:
    """并行或单条都只等到收口时刻。到点还没结束的位置留 None，调用方按没评完处理。

    单条以前在主线程里直等 HTTP，分块保活会把整期晨报拖过截止。现在一律丢进线程池，
    到点 shutdown(wait=False)：写晨报、发推送不再等它们。线程不是守护线程，
    解释器退出时仍会等它们跑完。
    """
    if not jobs:
        return []
    results: list[Any] = [None] * len(jobs)
    executor = ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs))))
    futures = {executor.submit(fn, job): index for index, job in enumerate(jobs)}
    pending = set(futures)
    try:
        while pending:
            if _cutoff_reached(clock(), cutoff):
                break
            remaining = _seconds_until(clock(), cutoff)
            if remaining <= 0:
                break
            done, pending = wait(pending, timeout=min(_WAIT_SLICE, remaining), return_when=FIRST_COMPLETED)
            for fut in done:
                results[futures[fut]] = fut.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return results


def _call_model(client: Any, messages: list[dict], options: Mapping) -> dict:
    """一次调用的计数。ChatRejected 带 fatal，调用方停掉这一段。"""
    try:
        result = client.complete(messages, options)
    except _StillOpen:
        return _abandoned_hit()
    except ChatRejected as exc:
        return {"fatal": str(exc), "error": str(exc), "calls": 0, "fromReceipt": 0,
                "usage": _zero_usage(), "seconds": 0.0}
    except ChatUnknown as exc:
        sent = "上次调用结果不明" not in str(exc)
        return {"error": str(exc), "unknown": True, "calls": 1 if sent else 0, "fromReceipt": 0,
                "usage": _zero_usage(), "seconds": 0.0}
    except ChatError as exc:
        return {"error": f"调用失败：{exc}", "calls": 1, "fromReceipt": 0, "usage": _zero_usage(), "seconds": 0.0}
    receipt = bool(result.from_receipt)
    return {
        "content": result.content, "finish": result.finish_reason, "calls": 0 if receipt else 1,
        "fromReceipt": 1 if receipt else 0, "usage": _usage(result.usage), "seconds": float(result.seconds or 0),
    }


def run_sector_line(db_path: Path, *, report_id: str, day: date, window: Window, messages: Sequence[Message],
                    config: Any, pool: CompanyPool, skip: Sequence[str], client: Any,
                    clock: Callable[[], datetime], search_client: Any = None, sector_live: bool = True,
                    classification: Any = None, limit_post=None, limit_environ=None,
                    limit_sleep: Callable[[float], None] | None = None) -> SectorOutcome:
    """板块线入口。messages 是主线粗筛后没点名池内公司的那些 Message。classification 用来取回流。

    sector_live 为假时只读库里的涨停，不联网。limit_post 注入 TuShare，测试不打真接口。
    """
    try:
        return _run(db_path, report_id=report_id, day=day, window=window, messages=messages, config=config,
                    pool=pool, skip=skip, client=client, clock=clock, search_client=search_client,
                    sector_live=sector_live, classification=classification, limit_post=limit_post,
                    limit_environ=limit_environ, limit_sleep=limit_sleep)
    except Exception:
        logger.exception("板块线出错")
        stats = _blank_stats(len(messages))
        return SectorOutcome(gap="板块线没有完成：出了异常", content=_content(0, 0, 0, []), stats=stats)


def _run(db_path: Path, *, report_id: str, day: date, window: Window, messages: Sequence[Message],
         config: Any, pool: CompanyPool, skip: Sequence[str], client: Any, clock: Callable[[], datetime],
         search_client: Any, sector_live: bool, classification: Any, limit_post, limit_environ,
         limit_sleep: Callable[[float], None] | None) -> SectorOutcome:
    rules = config.rules
    sectors = rules["sectors"]
    concept_note = _stored_concept_note(db_path)
    merged, named, backflow_refs = merge_sector_inputs(
        messages, backflow_messages(classification, sectors["backflow"]))
    if not merged:
        stats = _blank_stats(0)
        stats["backflow"]["messages"] = len(backflow_refs)
        _mark_c_closed(stats, sectors)
        if concept_note:
            stats["conceptNote"] = concept_note
        return _done(stats, _content(0, 0, 0, []), None, [])
    concepts, concept_note = _concepts(db_path, pool, sectors["concepts"])
    ferment = FermentOutcome(False, None, [])
    if sectors["fermenting"]["enabled"] is not True:
        ferment = FermentOutcome(False, "C 路关闭", [])
    elif concepts:
        ferment = count_fermenting(
            db_path, day=day, collected_by=window.collected_by, concepts=concepts,
            prefilter=sectors["prefilter"], intake=sectors["intake"], fermenting=sectors["fermenting"],
            window_end=rules["schedule"]["windowEnd"], opening_text=opening_text, rule_hit=_rule_hit,
            word_pattern=_word_pattern, compile_rules=_compile_rules)
    boards = [{**item, "pattern": _word_pattern(item["keywords"])} for item in ferment.boards]
    accepted, route_meta, route_stats = collect_routes(merged, sectors, boards=boards)
    stats = _blank_stats(len(merged), route_stats["dropped"], route_stats["prefilterKept"])
    stats["intake"] = route_stats["intake"]
    stats["routes"] = route_stats["routes"]
    stats["backflow"]["messages"] = len(backflow_refs)
    stats["backflow"]["kept"] = sum(1 for message in accepted if "A" in route_meta[message.ref]["routes"]
                                    and message.ref in backflow_refs)
    stats["fermenting"] = {
        "ran": ferment.ran, "note": ferment.note,
        "boards": [{key: item[key] for key in ("name", "recent", "baseDaily")} for item in ferment.boards],
        "messages": route_stats["routes"]["C"],
    }
    if concept_note:
        stats["conceptNote"] = concept_note
    ferment_gap = _ferment_gap(ferment)
    if not accepted:
        return _done(stats, _content(0, 0, 0, []), ferment_gap, [])
    if not concepts:
        gap = None if concept_note == _CONCEPT_NEVER else "板块线没有完成：概念清单是空的"
        return _done(stats, _content(0, 0, 0, []), _join(gap, ferment_gap), [])
    cutoff = rules["evaluation"]["cutoff"]
    client = _budget(client, cutoff, clock)
    search_client = _budget(search_client, cutoff, clock)
    numbered = assign_numbers(accepted)
    by_number = {number: message for number, message in numbered}
    if _cutoff_reached(clock(), cutoff):
        return _done(stats, _content(0, 0, 0, []), _join("板块线没有完成：收口时刻前没评完", ferment_gap), [])
    limit_status = _sync_limit_ups(
        db_path, day, int(sectors["recognition"]["lookbackDays"]), live=sector_live, post=limit_post,
        environ=limit_environ, sleep=limit_sleep, cutoff=cutoff, clock=clock)
    _write_recognition_stats(stats, sectors, day, limit_status)
    if limit_status["missing"]:
        return _done(stats, _content(0, 0, 0, []), _join(_LIMIT_GAP, ferment_gap), [])
    with db.read(db_path) as conn:
        members_missing = db.sector_members_all_empty(conn)
    if members_missing:
        stats["recognition"]["ran"] = False
        stats["recognition"]["note"] = _MEMBERS_GAP
        return _done(stats, _content(0, 0, 0, []), _join(_MEMBERS_GAP, ferment_gap), [])
    names = {item["name"] for item in concepts}
    by_name: dict[str, dict] = {}
    for item in concepts:
        by_name.setdefault(item["name"], item)
    mapped, block = _map_candidates(
        set(by_number), by_number=by_number, names=names, concepts=concepts, db_path=db_path, day=day,
        client=client, settings=sectors["mapping"], cutoff=cutoff, clock=clock, stats=stats,
        route_meta=route_meta)
    if block:
        return _done(stats, _content(0, 0, 0, []), _join(block, ferment_gap), [])
    events = _attach(mapped, by_number, by_name, named, route_meta, sectors["overseas"], ferment.boards)
    stats["mapping"]["events"] = len(events)
    stats["mapping"]["null"] = sum(1 for event in events if event["sector"] is None)
    stats["mapping"]["repeat"] = sum(1 for event in events if event["repeat"])
    profiles = getattr(config, "profiles", {}) or {}
    pool_rows = _stored_pool_rows(db_path)
    empty_events, no_full = _apply_recognition(
        events, db_path=db_path, pool=pool, skip=skip, profiles=profiles, pool_rows=pool_rows, day=day,
        lookback=int(sectors["recognition"]["lookbackDays"]), hot=sectors["recognition"]["hot"])
    stats["recognition"]["emptyEvents"] = empty_events
    stats["recognition"]["noFullMembers"] = no_full
    if no_full and not stats["recognition"]["note"]:
        stats["recognition"]["note"] = _NO_FULL_GAP.format(count=no_full)
    now = clock()
    for event in events:
        if not event["skip"]:
            continue
        if _load(db_path, report_id, event["id"]) is None:
            _insert(db_path, report_id=report_id, event_id=event["id"], title=event["title"],
                    sector=event["sector"], reason=event["reason"], repeat=event["repeat"], state="skipped",
                    package=_skipped_package(event), prepared_at=now, settled_at=now, routes=event["routes"])
    pending = [event for event in events if not event["skip"] and not _terminal(_load(db_path, report_id, event["id"]))]
    eval_hits: list[dict] = []
    started = time.monotonic()
    if pending:
        workers = int(sectors["evaluation"]["concurrency"])

        def evaluate(event: Mapping) -> dict:
            return _evaluate_event(
                event, db_path=db_path, report_id=report_id, day=day, window=window, pool=pool,
                carriers=sectors["carriers"], skip=skip, profiles=profiles,
                pool_rows=pool_rows, identity_keywords=sectors["identity"]["keywords"],
                search_client=search_client, search_settings=sectors["evaluation"]["search"],
                package_settings=sectors["evaluation"]["package"], client=client,
                options=sectors["evaluation"]["modelOptions"], cutoff=cutoff, clock=clock)

        eval_hits = _run_calls(pending, workers, evaluate, cutoff=cutoff, clock=clock)
        if any(_open_hit(hit) for hit in eval_hits):
            now = clock()
            for event in pending:
                _seal_gap(db_path, report_id, event, now)
        eval_hits = [hit for hit in eval_hits if isinstance(hit, dict)]
    seconds = time.monotonic() - started
    rows = _rows(db_path, report_id, events)
    max_events = int(sectors["delivery"]["maxEvents"])
    shown, overflow, select_gap = _select_delivery(
        rows, max_events=max_events, client=client, options=sectors["selection"]["modelOptions"], pool=pool,
        cutoff=cutoff, clock=clock, stats=stats)
    _finish_stats(stats, rows, eval_hits=eval_hits, overflow=overflow, seconds=seconds)
    gap = _eval_gap(rows)
    fatal = next((hit.get("fatal") for hit in eval_hits if hit.get("fatal")), None)
    if fatal and gap is None:
        gap = "板块线没有完成：模型拒绝了请求"
    gap = _join(gap, _search_misses(rows))
    gap = _join(gap, select_gap)
    gap = _join(gap, ferment_gap)
    if no_full:
        gap = _join(gap, _NO_FULL_GAP.format(count=no_full))
    return SectorOutcome(_annotate(stats, gap),
                         _render(rows, pool, max_events, shown=shown, overflow=overflow),
                         stats)


def _mark_c_closed(stats: dict, sectors: Mapping) -> None:
    if sectors["fermenting"]["enabled"] is False:
        stats["fermenting"] = {"ran": False, "note": "C 路关闭", "boards": [], "messages": 0}


def _sync_limit_ups(db_path: Path, day: date, lookback: int, *, live: bool, post, environ,
                    sleep: Callable[[float], None] | None, cutoff: str,
                    clock: Callable[[], datetime]) -> dict:
    """回放（live 为假）只读库。缺的日子在收口前补一次，补不上就原样返回。

    联网在工作线程里跑，主线程只等到收口，期间不占库连接。到点丢掉在途结果，不写库。
    """
    window = recognition_window(day, lookback)
    with db.read(db_path) as conn:
        have = stored_limit_days(conn)
    missing = [item for item in window if item.isoformat() not in have]
    missing_iso = [item.isoformat() for item in missing]
    if not missing or not live:
        return {"missing": missing_iso, "fetched": 0, "error": None}

    def fetch() -> dict:
        return fetch_limit_ups(
            missing, post=post, environ=environ, sleep=sleep or time.sleep,
            interval_seconds=LIMIT_INTERVAL_SECONDS,
            before_call=lambda: _cutoff_reached(clock(), cutoff))

    try:
        fetched = _wait_before_cutoff(fetch, cutoff, clock)
    except _StillOpen:
        return {"missing": missing_iso, "fetched": 0, "error": "收口时刻前没取完"}
    with db.write(db_path) as conn:
        written = store_limit_ups(conn, fetched["days"])
    with db.read(db_path) as conn:
        have = stored_limit_days(conn)
    still = [item.isoformat() for item in window if item.isoformat() not in have]
    return {"missing": still, "fetched": written, "error": fetched["error"]}


def _write_recognition_stats(stats: dict, sectors: Mapping, day: date, status: Mapping) -> None:
    lookback = int(sectors["recognition"]["lookbackDays"])
    window = recognition_window(day, lookback)
    missing = [str(item) for item in status.get("missing") or []]
    error = status.get("error")
    stats["recognition"] = {
        "ran": not missing,
        "note": error if error else (None if not missing else "涨停数据没取全"),
        "lookbackDays": lookback,
        "days": [] if missing or not window else [window[0].isoformat(), window[-1].isoformat()],
        "missing": missing,
        "fetched": int(status.get("fetched") or 0),
        "emptyEvents": 0,
        "noFullMembers": 0,
    }


def _stored_pool_rows(db_path: Path) -> list[dict]:
    try:
        with db.read(db_path) as conn:
            stored = db.read_stock_pool(conn)
    except db.NotReady:
        return []
    if not stored:
        return []
    return list(stored["rows"])


def _apply_recognition(events: Sequence[dict], *, db_path: Path, pool: CompanyPool, skip: Sequence[str],
                       profiles: Mapping, pool_rows: Sequence[Mapping], day: date, lookback: int,
                       hot: Mapping) -> tuple[int, int]:
    """只留 count ≥ 1 的候选，按辨识度排序。一个都没有的事件不调模型。

    返回 (辨识度名单为空的事件数, 缺全 A 成分的事件数)。某个板块在全 A 成分里没有一行时，
    不用池内成分代替，直接跳过。
    """
    window = recognition_window(day, lookback)
    with db.read(db_path) as conn:
        members_all = load_members_all(conn)
        ups = load_limit_ups(conn, window)
    empty = 0
    missing_full = 0
    for event in events:
        if event["skip"]:
            continue
        sector_code = str(event.get("sectorCode") or "")
        board = members_all.get(sector_code)
        if not board:
            event["skip"] = True
            event["reason"] = _NO_FULL_MEMBERS
            missing_full += 1
            continue
        rows = _member_rows(event, pool, skip, profiles, pool_rows)
        full = set(board)
        for code in event.get("members") or ():
            short = _short(code)
            if short:
                full.add(short)
        scored = score_recognition(
            [row["code"] for row in rows], full, ups, window,
            min_others=int(hot["minOthers"]), min_share=float(hot["minShare"]))
        by_code = {row["code"]: row for row in rows}
        prepared = []
        for item in scored:
            row = dict(by_code[item["code"]])
            row["recognition"] = {"count": item["count"], "last": item["last"]}
            prepared.append(row)
        event["lookbackDays"] = lookback
        if not prepared:
            event["skip"] = True
            event["reason"] = _EMPTY_RECOGNITION
            empty += 1
        else:
            event["preparedMembers"] = prepared
    return empty, missing_full


def _ferment_gap(ferment: FermentOutcome) -> str | None:
    if ferment.blocked == "coverage":
        return _FERMENT_GAP
    if ferment.blocked == "calendar" and ferment.note:
        return f"板块线 C 路没跑：{ferment.note}"
    return None


def _join(left: str | None, right: str | None) -> str | None:
    if left and right:
        return left if right in left else f"{left}；{right}"
    return left or right


def _search_misses(rows: Sequence[Mapping]) -> str | None:
    """评估已经成功、但联网搜索失败或结果不明的事件，汇总成一行。没发搜索（no_key）不算。"""
    missed = 0
    for row in rows:
        if row.get("state") != "succeeded":
            continue
        package = row.get("package") if isinstance(row.get("package"), Mapping) else {}
        search = package.get("search") if isinstance(package.get("search"), Mapping) else {}
        error = search.get("error")
        if isinstance(error, str) and error and error != "no_key":
            missed += 1
    if not missed:
        return None
    return f"板块线有 {missed} 个事件联网搜索没成"


def _annotate(stats: Mapping, gap: str | None) -> str | None:
    """归并只成了一半时，已经拿到的仍然往下走，并补上缺口。"""
    notes: list[str] = []
    if isinstance(stats, dict):
        concept = stats.pop("conceptNote", None)
        if isinstance(concept, str) and concept:
            notes.append(concept)
    mapping = stats.get("mapping") if isinstance(stats, Mapping) else None
    if isinstance(mapping, Mapping) and mapping.get("incomplete"):
        notes.append(_MAPPING_HALF)
    if not notes:
        return gap
    note = "；".join(notes)
    if not gap:
        return note
    if note in gap:
        return gap
    return f"{note}；{gap}"


def _done(stats: dict, content: dict, gap: str | None, rows: Sequence[Mapping], *,
          stats_rows: Sequence[Mapping] | None = None) -> SectorOutcome:
    gap = _annotate(stats, gap)
    if stats_rows is not None:
        _finish_stats(stats, stats_rows, eval_hits=[], overflow=content["overflow"], seconds=0.0)
        content = _sync_counts(content, stats)
    elif rows:
        _finish_stats(stats, rows, eval_hits=[], overflow=0, seconds=0.0)
    return SectorOutcome(gap, content, stats)


def _sync_counts(content: dict, stats: dict) -> dict:
    evaluation = stats["evaluation"]
    content["evaluated"] = evaluation["evaluated"]
    content["overflow"] = stats["overflow"]
    content["candidates"] = stats["mapping"]["events"]
    return content


def _eval_gap(rows: Sequence[Mapping]) -> str | None:
    pending = [row for row in rows if row.get("state") in ("gap", "invalid")]
    if not pending:
        return None
    if any(row.get("error") == _GAP for row in pending):
        return "板块线没有完成：收口时刻前没评完"
    return f"板块线有 {len(pending)} 条没能完成评估"


def _rows(db_path: Path, report_id: str, events: Sequence[Mapping]) -> list[dict]:
    wanted = {event["id"] for event in events}
    with db.read(db_path) as conn:
        stored = db.list_sector_events(conn, report_id)
    return [row for row in stored if row["event_id"] in wanted]


def _render(rows: Sequence[Mapping], pool: CompanyPool, max_events: int, *,
            shown: Sequence[Mapping] | None = None, overflow: int | None = None) -> dict:
    if shown is None:
        shown, overflow = select_pushed(rows, max_events, chosen_ids=())
    evaluated = sum(1 for row in rows if row.get("state") == "succeeded" and row.get("verdict") in _VERDICTS)
    return _content(len(rows), evaluated, int(overflow or 0), _public_entries(shown, pool))


def _terminal(row: Mapping | None) -> bool:
    return row is not None and row.get("state") != "prepared"


def _skipped_package(event: Mapping) -> dict:
    return {
        "event": {"id": event["id"], "title": event["title"], "reason": event["reason"],
                  "firstPublished": event["firstPublished"]},
        "ids": list(event["ids"]), "refs": list(event["refs"]),
    }


def _attach(mapped: Sequence[Mapping], by_number: Mapping[str, Message], by_name: Mapping[str, Mapping],
            named: Mapping[str, set[str]], route_meta: Mapping[str, Mapping], overseas: Mapping,
            boards: Sequence[Mapping]) -> list[dict]:
    by_board = {item["name"]: item for item in boards}
    events = []
    for item in mapped:
        messages = [by_number[number] for number in item["ids"] if number in by_number]
        if not messages:
            continue
        refs = [message.ref for message in messages]
        first = min(message.published_at for message in messages)
        concept = by_name.get(item["sector"]) if item["sector"] else None
        codes: set[str] = set()
        routes: set[str] = set()
        hit_boards: list[str] = []
        for message in messages:
            codes.update(named.get(message.ref) or ())
            meta = route_meta.get(message.ref) or {}
            routes.update(meta.get("routes") or ())
            for name in meta.get("boards") or []:
                if name not in hit_boards:
                    hit_boards.append(name)
        ordered_routes = [route for route in _ROUTE_ORDER if route in routes]
        fermenting = [
            {key: by_board[name][key] for key in ("name", "keywords", "recent", "baseDaily", "baseDays")}
            for name in hit_boards if name in by_board
        ]
        events.append({
            "id": event_id(refs), "title": item["title"], "ids": list(item["ids"]), "messages": messages,
            "refs": refs, "sector": None if concept is None else concept["name"],
            "sectorCode": None if concept is None else concept["tsCode"],
            "members": [] if concept is None else list(concept["members"]),
            "reason": item["reason"], "repeat": bool(item["repeat"]), "named": sorted(codes),
            "firstPublished": _minutes(first),
            "skip": concept is None or bool(item["repeat"]),
            "routes": ordered_routes,
            "overseasNotes": _event_overseas_notes(messages, overseas),
            "fermenting": fermenting,
        })
    return events


def _add_mapping_hit(stats: dict, hit: Mapping) -> None:
    mapping = stats["mapping"]
    mapping["calls"] += int(hit.get("calls") or 0)
    mapping["fromReceipt"] += int(hit.get("fromReceipt") or 0)
    mapping["usage"] = _add_usage(mapping["usage"], hit.get("usage") or {})
    mapping["seconds"] = round(float(mapping["seconds"]) + float(hit.get("seconds") or 0), 3)


def _mapping_request(items: Sequence[Mapping], concepts: Sequence[Mapping],
                     previous: Sequence[tuple[str, str]]) -> list[dict]:
    boards = [{"name": item["name"], "count": int(item["count"])} for item in concepts]
    user = mapping_user_prompt(items, boards, previous)
    return [{"role": "system", "content": MAPPING_SYSTEM}, {"role": "user", "content": user}]


def _dropped_items(content: str) -> list:
    try:
        data = json.loads(content)
    except ValueError:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("dropped"), list):
        return []
    return list(data["dropped"])


def _earliest_index(ids: Sequence[str], position: Mapping[str, int]) -> int:
    found = [position[item] for item in ids if item in position]
    return min(found) if found else len(position)


def _mapping_usable(event: Mapping) -> bool:
    """不重复的事件才和同板块的其他有效事件合并。"""
    return not bool(event.get("repeat"))


def _copied_mapping(event: Mapping) -> dict:
    return {
        "title": event["title"],
        "ids": list(event["ids"]),
        "sector": event.get("sector"),
        "reason": event["reason"],
        "repeat": bool(event["repeat"]),
    }


def _merge_mapping_halves(left: Sequence[Mapping], right: Sequence[Mapping], ordered: Sequence[str],
                          left_dropped: Sequence, right_dropped: Sequence, names: set[str]) -> list[dict] | str:
    """两半原样拼接（左半在前），按首条消息位置排序。同一编号只留在先出现的事件里，不按板块合并。"""
    position = {number: index for index, number in enumerate(ordered)}
    rows: list[dict] = []
    for event in (*left, *right):
        ids = [item for item in event.get("ids") or [] if item in position]
        if not ids:
            continue
        copied = _copied_mapping(event)
        copied["ids"] = ids
        rows.append(copied)
    rows.sort(key=lambda event: _earliest_index(event["ids"], position))
    seen: set[str] = set()
    events: list[dict] = []
    for event in rows:
        ids = [item for item in event["ids"] if item not in seen]
        if not ids:
            continue
        seen.update(ids)
        event = dict(event)
        event["ids"] = ids
        events.append(event)
    dropped = list(left_dropped) + list(right_dropped)
    return parse_mapping_output(
        json.dumps({"events": events, "dropped": dropped}, ensure_ascii=False), set(ordered), names)


def _split_mapping_length(ordered: Sequence[str], items: Sequence[Mapping], *, concepts: Sequence[Mapping],
                          previous: Sequence[tuple[str, str]], names: set[str], client: Any, options: Mapping,
                          cutoff: str, clock: Callable[[], datetime], stats: dict) -> tuple[list[dict], str | None]:
    """输出被截断时按首次公开时间对半各发一次。只拆这一次；两半都成功就合并，只有一半成功用那一半。"""
    stats["mapping"]["splits"] += 1
    mid = len(ordered) // 2
    parsed: list[tuple[list[dict], list] | None] = []
    for part_ids, part_items in ((ordered[:mid], items[:mid]), (ordered[mid:], items[mid:])):
        if _cutoff_reached(clock(), cutoff):
            return [], "板块线没有完成：收口时刻前没评完"
        hit = _call_model(client, _mapping_request(part_items, concepts, previous), options)
        _add_mapping_hit(stats, hit)
        if hit.get("cutoff") or _cutoff_reached(clock(), cutoff):
            return [], _CUTOFF_GAP
        if hit.get("fatal") or hit.get("error"):
            parsed.append(None)
            continue
        if _cutoff_reached(clock(), cutoff):
            return [], "板块线没有完成：收口时刻前没评完"
        if hit.get("finish") != "stop":
            parsed.append(None)
            continue
        events = parse_mapping_output(hit.get("content") or "", set(part_ids), names)
        if isinstance(events, str):
            parsed.append(None)
            continue
        parsed.append((events, _dropped_items(hit.get("content") or "")))
    succeeded = [item for item in parsed if item is not None]
    if len(succeeded) == 2:
        merged = _merge_mapping_halves(
            succeeded[0][0], succeeded[1][0], ordered, succeeded[0][1], succeeded[1][1], names)
        if isinstance(merged, str):
            return [], _MAPPING_GAP
        return merged, None
    if len(succeeded) == 1:
        stats["mapping"]["incomplete"] = True
        return succeeded[0][0], None
    return [], _MAPPING_GAP


def _map_candidates(selected: set[str], *, by_number: Mapping[str, Message], names: set[str],
                    concepts: Sequence[Mapping], db_path: Path, day: date, client: Any, settings: Mapping,
                    cutoff: str, clock: Callable[[], datetime], stats: dict,
                    route_meta: Mapping[str, Mapping]) -> tuple[list[dict], str | None]:
    if _cutoff_reached(clock(), cutoff):
        return [], "板块线没有完成：收口时刻前没评完"
    ordered = [number for number, _message in sorted(
        ((number, by_number[number]) for number in selected if number in by_number),
        key=lambda item: (item[1].published_at, item[1].ref))]
    items = []
    for number in ordered:
        message = by_number[number]
        title = re.sub(r"\s+", " ", message.title or "").strip()
        excerpt = re.sub(r"\s+", " ", message.text or "").strip()[:int(settings["excerptChars"])]
        meta = route_meta.get(message.ref) or {}
        items.append({
            "id": number, "source": message.source_label, "time": published_label(message.published_at),
            "title": title, "excerpt": excerpt,
            "routes": list(meta.get("routes") or []), "boards": list(meta.get("boards") or []),
        })
    previous = _previous_events(db_path, day)
    hit = _call_model(client, _mapping_request(items, concepts, previous), settings["modelOptions"])
    _add_mapping_hit(stats, hit)
    if hit.get("cutoff") or _cutoff_reached(clock(), cutoff):
        return [], _CUTOFF_GAP
    if hit.get("fatal") or hit.get("error"):
        return [], _MAPPING_GAP
    finish = hit.get("finish")
    if finish in {"length", "content_filter"} and len(ordered) >= 2:
        if finish == "content_filter":
            stats["mapping"]["filtered"] = int(stats["mapping"].get("filtered") or 0) + 1
        return _split_mapping_length(
            ordered, items, concepts=concepts, previous=previous, names=names, client=client,
            options=settings["modelOptions"], cutoff=cutoff, clock=clock, stats=stats)
    if hit.get("finish") != "stop":
        return [], _MAPPING_GAP
    parsed = parse_mapping_output(hit.get("content") or "", set(ordered), names)
    if isinstance(parsed, str):
        return [], _MAPPING_GAP
    return parsed, None


def _evaluate_event(event: Mapping, *, db_path: Path, report_id: str, day: date, window: Window,
                    pool: CompanyPool, carriers: Mapping, skip: Sequence[str], profiles: Mapping,
                    pool_rows: Sequence[Mapping], identity_keywords: Sequence[str], search_client: Any,
                    search_settings: Mapping, package_settings: Mapping, client: Any, options: Mapping,
                    cutoff: str, clock: Callable[[], datetime]) -> dict:
    row = _load(db_path, report_id, event["id"])
    if _terminal(row):
        return {"calls": 0, "fromReceipt": 0, "usage": _zero_usage(), "seconds": 0.0, "repairs": 0, "repaired": 0}
    now = clock()
    if row is None:
        if _cutoff_reached(now, cutoff):
            _seal_gap(db_path, report_id, event, now)
            return _abandoned_hit(repairs=0, repaired=0)
        try:
            package = build_event_package(
                event=event, day=day, window=window, pool=pool, carriers=carriers, skip=skip,
                profiles=profiles, pool_rows=pool_rows, identity_keywords=identity_keywords,
                search_client=search_client, search_settings=search_settings,
                package_settings=package_settings)
        except _StillOpen:
            _seal_gap(db_path, report_id, event, clock())
            return _abandoned_hit(repairs=0, repaired=0)
        now = clock()
        _insert(db_path, report_id=report_id, event_id=event["id"], title=event["title"], sector=event["sector"],
                reason=event["reason"], repeat=False, state="prepared", package=package, prepared_at=now,
                routes=list(event.get("routes") or []))
        row = _load(db_path, report_id, event["id"])
    if row is None or _terminal(row):
        return {"calls": 0, "fromReceipt": 0, "usage": _zero_usage(), "seconds": 0.0, "repairs": 0, "repaired": 0}
    if _cutoff_reached(clock(), cutoff):
        _settle(db_path, report_id=report_id, event_id=event["id"], state="gap", settled_at=clock(), error=_GAP)
        return {"calls": 0, "fromReceipt": 0, "usage": _zero_usage(), "seconds": 0.0, "repairs": 0, "repaired": 0}
    package = row["package"]
    messages = [
        {"role": "system", "content": EVALUATION_SYSTEM},
        {"role": "user", "content": evaluation_user_prompt(package)},
    ]
    hit = _call_model(client, messages, options)
    if hit.get("cutoff"):
        _settle(db_path, report_id=report_id, event_id=event["id"], state="gap", settled_at=clock(), error=_GAP)
        return hit
    if hit.get("fatal"):
        _settle(db_path, report_id=report_id, event_id=event["id"], state="gap", settled_at=clock(),
                error=hit["fatal"])
        return hit
    if hit.get("error"):
        _settle(db_path, report_id=report_id, event_id=event["id"], state="gap", settled_at=clock(),
                error=hit["error"], usage=hit.get("usage"), seconds=hit.get("seconds"))
        return hit
    usage = hit["usage"]
    seconds = float(hit["seconds"])
    if _cutoff_reached(clock(), cutoff):
        _settle(db_path, report_id=report_id, event_id=event["id"], state="gap", settled_at=clock(), error=_GAP,
                output=hit.get("content"), usage=usage, seconds=seconds)
        return hit
    if hit.get("finish") != "stop":
        _settle(db_path, report_id=report_id, event_id=event["id"], state="invalid", settled_at=clock(),
                error=f"finish:{hit.get('finish')}", output=hit.get("content"), usage=usage, seconds=seconds)
        return hit
    parsed = parse_evaluation_output(hit.get("content") or "", package)
    if not isinstance(parsed, str):
        _settle(db_path, report_id=report_id, event_id=event["id"], state="succeeded", settled_at=clock(),
                verdict=parsed["verdict"], output=parsed, usage=usage, seconds=seconds)
        return hit
    if _cutoff_reached(clock(), cutoff):
        _settle(db_path, report_id=report_id, event_id=event["id"], state="gap", settled_at=clock(), error=_GAP,
                output=hit.get("content"), usage=usage, seconds=seconds)
        return hit
    repair_messages = [
        messages[0], messages[1],
        {"role": "assistant", "content": hit.get("content") or ""},
        {"role": "user", "content": _REPAIR_PROMPT.format(reason=parsed)},
    ]
    repaired = _call_model(client, repair_messages, options)
    hit["repairs"] = 1 if repaired.get("calls") or repaired.get("fromReceipt") or repaired.get("error") else 0
    if repaired.get("unknown") and not repaired.get("calls"):
        hit["repairs"] = 0
    hit["calls"] = int(hit["calls"]) + int(repaired.get("calls") or 0)
    hit["fromReceipt"] = int(hit["fromReceipt"]) + int(repaired.get("fromReceipt") or 0)
    hit["usage"] = _add_usage(usage, repaired.get("usage") or {})
    total_seconds = seconds + float(repaired.get("seconds") or 0)
    hit["seconds"] = total_seconds
    if repaired.get("cutoff") or _cutoff_reached(clock(), cutoff):
        hit["cutoff"] = True
        _settle(db_path, report_id=report_id, event_id=event["id"], state="gap", settled_at=clock(), error=_GAP,
               output=repaired.get("content"), usage=hit["usage"], seconds=total_seconds)
        return hit
    if repaired.get("fatal") or (repaired.get("error") and not repaired.get("content")):
        error = f"{parsed}；修正调用结果不明" if repaired.get("unknown") else f"{parsed}；修正调用失败：{repaired.get('error')}"
        _settle(db_path, report_id=report_id, event_id=event["id"], state="invalid", settled_at=clock(),
                error=error, output=hit.get("content"), usage=hit["usage"], seconds=total_seconds)
        return hit
    if repaired.get("finish") != "stop":
        reason = f"finish:{repaired.get('finish')}"
    else:
        again = parse_evaluation_output(repaired.get("content") or "", package)
        if not isinstance(again, str):
            hit["repaired"] = 1
            _settle(db_path, report_id=report_id, event_id=event["id"], state="succeeded", settled_at=clock(),
                    verdict=again["verdict"], output=again, usage=hit["usage"], seconds=total_seconds)
            return hit
        reason = again
    _settle(db_path, report_id=report_id, event_id=event["id"], state="invalid", settled_at=clock(),
            error=f"{parsed}；修正后仍不合格：{reason}",
            output={"first": hit.get("content"), "repair": repaired.get("content")},
            usage=hit["usage"], seconds=total_seconds)
    return hit
