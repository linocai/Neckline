"""板块线：粗筛、挑候选、归并、成分股、备料、评估、收口。

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
from neckline.morning.sector_prompt import (
    CANDIDATE_SYSTEM, CANDIDATES_CONTRACT, EVALUATION_CONTRACT, EVALUATION_SYSTEM, MAPPING_CONTRACT,
    MAPPING_SYSTEM, candidate_line, candidate_user_prompt, evaluation_user_prompt, mapping_user_prompt,
    published_label,
)
from neckline.morning.sectors import (
    FETCH_CUTOFF, MEMBER_INTERVAL_SECONDS, DailyFill, board_material, commit_market_daily, concept_list,
    member_facts, plan_market_daily, pull_market_days,
)
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
_REPAIR_PROMPT = (
    "你上一条输出没有通过格式检查：{reason}。"
    "请对照系统提示里的输出格式，把同一份评估重新完整输出为一个 JSON 对象。"
    "判断和结论不要改，只修正格式；缺的字段按你原来的分析写全。"
)
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
    market = sectors.get("market")
    if isinstance(market, Mapping):
        if "leaderCount" in market:
            raise ValueError("sectors.market.leaderCount 已停用")
        if "leaderWindow" in market:
            raise ValueError("sectors.market.leaderWindow 已停用")
    carriers = _require_dict(sectors, "carriers", "sectors.carriers")
    for key in ("max", "amountWindow", "limitWindow", "changeWindow"):
        _require_int(carriers, key, f"sectors.carriers.{key}")
    candidates = _require_dict(sectors, "candidates", "sectors.candidates")
    if _require_str(candidates, "contract", "sectors.candidates.contract") != CANDIDATES_CONTRACT:
        raise ValueError(f"sectors.candidates.contract 须为 {CANDIDATES_CONTRACT}")
    _require_int(candidates, "batchChars", "sectors.candidates.batchChars")
    _require_int(candidates, "headChars", "sectors.candidates.headChars")
    _require_int(candidates, "concurrency", "sectors.candidates.concurrency")
    if not isinstance(_require(candidates, "modelOptions", "sectors.candidates.modelOptions"), dict):
        raise ValueError("sectors.candidates.modelOptions 类型不对")
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


def split_batches(numbered: Sequence[tuple[str, Message]], *, batch_chars: int, head_chars: int,
                  ) -> list[list[tuple[str, Message]]]:
    """按渲染后的行长分批。单行超过上限时单独成批。空列表不产生空批。"""
    batches: list[list[tuple[str, Message]]] = []
    current: list[tuple[str, Message]] = []
    total = 0
    for item in numbered:
        size = len(candidate_line(item[0], item[1], head_chars))
        if current and total + size > batch_chars:
            batches.append(current)
            current, total = [], 0
        current.append(item)
        total += size
    if current:
        batches.append(current)
    return batches


def parse_candidate_output(content: str, allowed: set[str]) -> set[str] | str:
    """只收下本批出现过的编号。编号不在本批里的丢掉，不让整批失效。"""
    try:
        data = json.loads(content)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return "输出不是 JSON 对象"
    found: set[str] = set()
    for event in data["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("ids"), list):
            continue
        for item in event["ids"]:
            if isinstance(item, str) and item in allowed:
                found.add(item)
    return found


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
        sector = raw.get("sector")
        if not isinstance(sector, str) or sector not in names:
            sector = None
        reason = raw.get("reason") if isinstance(raw.get("reason"), str) else ""
        weighty = raw.get("weighty")
        if not isinstance(weighty, bool):
            return "weighty 无效"
        weight = raw.get("weight")
        if not isinstance(weight, str) or not weight.strip():
            return "weight 无效"
        events.append({
            "title": title.strip(), "ids": ids, "sector": sector, "reason": reason, "repeat": raw["repeat"],
            "weighty": weighty, "weight": weight.strip(),
        })
    return events


def select_pushed(rows: Sequence[Mapping], max_events: int) -> tuple[list[Mapping], int]:
    """判推的按首次公开时间、再按事件编号取前 max_events 个。展示用同一个顺序。"""
    chosen = [
        row for row in rows
        if row.get("state") == "succeeded" and row.get("verdict") == "推" and isinstance(row.get("output"), dict)
    ]

    def key(row: Mapping) -> tuple[str, str]:
        package = row.get("package") if isinstance(row.get("package"), Mapping) else {}
        event = package.get("event") if isinstance(package.get("event"), Mapping) else {}
        published = event.get("firstPublished") if isinstance(event.get("firstPublished"), str) else ""
        return published, str(row.get("event_id") or "")

    chosen.sort(key=key)
    overflow = max(0, len(chosen) - max_events)
    return chosen[:max_events], overflow


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
    """格式不合格时返回原因。四个 pass 里有 false 却判推的，改记不推。对不上的观点删掉并计数。"""
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
    questions = _questions(data.get("questions"))
    if isinstance(questions, str):
        return questions
    leaders = _leaders(data.get("leaders"), package, carriers_pass=questions["carriers"]["pass"])
    if isinstance(leaders, str):
        return leaders
    views = _views(data.get("views"), _known_refs(package))
    if isinstance(views, str):
        return views
    kept, dropped = views
    vetoed = verdict == "推" and any(not questions[key]["pass"] for key in ("fresh", "weight", "position", "carriers"))
    if vetoed:
        verdict = "不推"
    output = {
        "title": title, "verdict": verdict, "conclusion": conclusion, "questions": questions,
        "leaders": leaders, "risk": risk, "views": kept, "droppedViews": dropped,
    }
    if vetoed:
        output["vetoed"] = True
    return output


def _questions(raw: object) -> dict | str:
    if not isinstance(raw, Mapping):
        return "questions 不是对象"
    parsed: dict[str, Any] = {}
    for key in ("fresh", "weight", "position", "carriers"):
        block = raw.get(key)
        if not isinstance(block, Mapping):
            return f"questions 缺少 {key}"
        if not isinstance(block.get("pass"), bool):
            return f"{key}.pass 不是布尔"
        text = _text(block.get("text"))
        if not text:
            return f"{key}.text 为空"
        parsed[key] = {"pass": block["pass"], "text": text}
    return parsed


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


def _leaders(raw: object, package: Mapping, *, carriers_pass: bool) -> list[dict] | str:
    if not isinstance(raw, list):
        return "leaders 不是列表"
    if not carriers_pass:
        return []
    maximum = _carrier_max(package)
    if isinstance(maximum, str):
        return maximum
    if not 1 <= len(raw) <= maximum:
        return "leaders 数量不对"
    allowed = _member_codes(package)
    seen: set[str] = set()
    parsed = []
    for item in raw:
        if not isinstance(item, Mapping):
            return "leaders 格式不对"
        code = _short(item.get("code"))
        if code not in allowed:
            return "leaders 不在成分股名单"
        if code in seen:
            return "leaders 重复"
        seen.add(code)
        why = _text(item.get("why"))
        price = _text(item.get("price"))
        if not why or not price:
            return "leaders 理由或价格为空"
        parsed.append({"code": code, "why": why, "price": price})
    return parsed


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


def _member_rows(event: Mapping, pool: CompanyPool, skip: Sequence[str], daily_rows: Sequence[Mapping],
                  as_of: date, carriers: Mapping, profiles: Mapping, pool_rows: Sequence[Mapping]) -> list[dict]:
    """池内、属于这个板块、且不在当天主线名单里的成分股，按代码从小到大。"""
    skipped = {_short(code) for code in skip}
    skipped.discard("")
    stored: dict[str, Mapping] = {}
    for row in pool_rows:
        if isinstance(row, Mapping):
            short = _short(row.get("code"))
            if short:
                stored[short] = row
    blob = "\n".join(
        f"{getattr(message, 'title', '') or ''}\n{getattr(message, 'text', '') or ''}"
        for message in event.get("messages") or []
    )
    chosen: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for code in event.get("members") or []:
        short = _short(code)
        if not short or short in skipped or short in seen:
            continue
        company = pool.companies.get(str(code)) or pool.companies.get(f"{short}.SZ")
        if company is None:
            company = next((item for item in pool.companies.values() if item.short_code == short), None)
        if company is None:
            continue
        seen.add(short)
        chosen.append((short, company))
    chosen.sort(key=lambda item: item[0])
    rows = []
    for short, company in chosen:
        facts = member_facts(
            company.code, daily_rows, as_of, amount_window=int(carriers["amountWindow"]),
            limit_window=int(carriers["limitWindow"]), change_window=int(carriers["changeWindow"]))
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
            "averageAmountYuan": facts["averageAmountYuan"],
            "limitUps20": facts["limitUps"],
            "change5Percent": facts["changePercent"],
            "named": bool(company.name) and company.name in blob,
        })
    return rows


def build_event_package(*, event: Mapping, day: date, window: Window, pool: CompanyPool,
                         daily_rows: Sequence[Mapping], as_of: date, market: Mapping, carriers: Mapping,
                         skip: Sequence[str], search_client: Any, search_settings: Mapping,
                         package_settings: Mapping, profiles: Mapping | None = None,
                         pool_rows: Sequence[Mapping] = ()) -> dict:
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
    member_rows = _member_rows(event, pool, skip, daily_rows, as_of, carriers, profiles or {}, pool_rows)
    if not member_rows:
        gaps.append("板块里没有可选的成分股")
    windows = list(market["boardWindows"])
    board = board_material(list(event["members"]), daily_rows, as_of, windows, int(market["limitWindow"]),
                           list(pool.companies))
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
        "sector": {"name": event["sector"], "code": event["sectorCode"]},
        "messages": listed,
        "original": {"kind": "news", "documents": documents, "media": media},
        "board": board,
        "carriers": {
            "max": int(carriers["max"]),
            "amountWindow": int(carriers["amountWindow"]),
            "limitWindow": int(carriers["limitWindow"]),
            "changeWindow": int(carriers["changeWindow"]),
        },
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
        "candidates": {"batches": 0, "calls": 0, "fromReceipt": 0, "messages": 0, "splits": 0,
                       "usage": _zero_usage(), "seconds": 0.0},
        "mapping": {"calls": 0, "fromReceipt": 0, "splits": 0, "events": 0, "null": 0, "repeat": 0, "light": 0,
                    "usage": _zero_usage(), "seconds": 0.0},
        "evaluation": {"calls": 0, "fromReceipt": 0, "evaluated": 0, "verdicts": {"推": 0, "不推": 0},
                       "invalid": 0, "gap": 0, "vetoed": 0, "droppedViews": 0, "repairs": 0, "repaired": 0,
                       "usage": _zero_usage(), "seconds": 0.0},
        "overflow": 0,
        "search": summarize_search([], enabled=True),
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
                package=_skipped_package(event), prepared_at=now, error=_GAP, settled_at=now)
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
        questions = output.get("questions") if isinstance(output.get("questions"), Mapping) else {}
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
        board = package.get("board") if isinstance(package.get("board"), Mapping) else {}
        member_count = board.get("memberCount")
        entries.append({
            "title": output.get("title") if isinstance(output.get("title"), str) else "",
            "sector": {
                "name": sector.get("name") if isinstance(sector.get("name"), str) else (row.get("sector") or ""),
                "code": sector.get("code") if isinstance(sector.get("code"), str) else "",
                # 当期股票池里的成分股只数（App 显示「创业板 N 只」）
                "memberCount": member_count if isinstance(member_count, int) and not isinstance(member_count, bool)
                else None,
            },
            "conclusion": output.get("conclusion") if isinstance(output.get("conclusion"), str) else "",
            "questions": {
                "fresh": _question_text(questions.get("fresh")),
                "weight": _question_text(questions.get("weight")),
                "position": _question_text(questions.get("position")),
                "carriers": _question_text(questions.get("carriers")),
            },
            "leaders": leaders,
            "risk": output.get("risk") if isinstance(output.get("risk"), str) else "",
            "views": views,
            "sources": sources,
            "firstPublished": event.get("firstPublished") if isinstance(event.get("firstPublished"), str) else "",
        })
    return entries


def _question_text(value: object) -> str:
    if isinstance(value, Mapping):
        text = value.get("text")
        return text.strip() if isinstance(text, str) else ""
    return ""


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
        # 过了收口不再把回执改回 started。unknown 保持不动，调用方也不会再发下一次。
        if _cutoff_reached(self._clock(), self._cutoff):
            return
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
        if _cutoff_reached(self._clock(), self._cutoff):
            raise _StillOpen()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(fn)
        try:
            while not future.done():
                if _cutoff_reached(self._clock(), self._cutoff):
                    raise _StillOpen()
                remaining = _seconds_until(self._clock(), self._cutoff)
                if remaining <= 0:
                    raise _StillOpen()
                wait((future,), timeout=min(_WAIT_SLICE, remaining))
            if _cutoff_reached(self._clock(), self._cutoff):
                raise _StillOpen()
            return future.result()
        finally:
            # 不等在途的 HTTP。进程退出时这些非守护线程仍会被解释器 join。
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


def _await_market_daily(db_path: Path, *, as_of: date, lookback_days: int, prefixes: Sequence[str],
                        cutoff: str, clock: Callable[[], datetime]) -> DailyFill:
    """日线补齐以 evaluation.cutoff 为绝对期限。

    先短读算出缺口，再在工作线程里取数，主流程只等到收口。取数不占写事务。
    到点还没回来的这次结果整段丢掉，不写入 market_daily，晚到的行不能改已交付的名单。
    """
    if _cutoff_reached(clock(), cutoff):
        return DailyFill((), 0, FETCH_CUTOFF)
    with db.read(db_path) as conn:
        plan = plan_market_daily(conn, as_of=as_of, lookback_days=lookback_days, prefixes=prefixes)
    if plan.error:
        return DailyFill((), 0, plan.error)
    if not plan.missing or not plan.known:
        with db.write(db_path) as conn:
            return commit_market_daily(conn, plan, (), error=None)

    def work() -> tuple[tuple[tuple[str, list[dict]], ...], str | None]:
        return pull_market_days(
            plan, interval_seconds=MEMBER_INTERVAL_SECONDS,
            before_call=lambda: _cutoff_reached(clock(), cutoff))

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(work)
    try:
        while not future.done():
            if _cutoff_reached(clock(), cutoff):
                return DailyFill((), 0, FETCH_CUTOFF)
            remaining = _seconds_until(clock(), cutoff)
            if remaining <= 0:
                return DailyFill((), 0, FETCH_CUTOFF)
            wait((future,), timeout=min(_WAIT_SLICE, remaining))
        rows, error = future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    if _cutoff_reached(clock(), cutoff) or error == FETCH_CUTOFF:
        return DailyFill((), 0, FETCH_CUTOFF)
    with db.write(db_path) as conn:
        return commit_market_daily(conn, plan, rows, error=error)


def run_sector_line(db_path: Path, *, report_id: str, day: date, window: Window, messages: Sequence[Message],
                    config: Any, pool: CompanyPool, skip: Sequence[str], client: Any,
                    clock: Callable[[], datetime], search_client: Any = None, sector_live: bool = True,
                    ) -> SectorOutcome:
    """板块线入口。messages 是主线粗筛后没点名池内公司的那些 Message。"""
    try:
        return _run(db_path, report_id=report_id, day=day, window=window, messages=messages, config=config,
                    pool=pool, skip=skip, client=client, clock=clock, search_client=search_client,
                    sector_live=sector_live)
    except Exception:
        logger.exception("板块线出错")
        stats = _blank_stats(len(messages))
        return SectorOutcome(gap="板块线没有完成：出了异常", content=_content(0, 0, 0, []), stats=stats)


def _run(db_path: Path, *, report_id: str, day: date, window: Window, messages: Sequence[Message],
         config: Any, pool: CompanyPool, skip: Sequence[str], client: Any, clock: Callable[[], datetime],
         search_client: Any, sector_live: bool) -> SectorOutcome:
    rules = config.rules
    sectors = rules["sectors"]
    concept_note = _stored_concept_note(db_path)
    if not messages:
        stats = _blank_stats(0)
        if concept_note:
            stats["conceptNote"] = concept_note
        return _done(stats, _content(0, 0, 0, []), None, [])
    kept, dropped = apply_prefilter(messages, sectors["prefilter"])
    stats = _blank_stats(len(messages), dropped, len(kept))
    if concept_note:
        stats["conceptNote"] = concept_note
    if not kept:
        return _done(stats, _content(0, 0, 0, []), None, [])
    # 没有可选的板块就不必花钱挑候选
    concepts, concept_note = _concepts(db_path, pool, sectors["concepts"])
    if concept_note:
        stats["conceptNote"] = concept_note
    if not concepts:
        # 刷新失败且没有旧清单时，缺口就是这件事，不再另写「清单是空的」。
        gap = None if concept_note == _CONCEPT_NEVER else "板块线没有完成：概念清单是空的"
        return _done(stats, _content(0, 0, 0, []), gap, [])
    cutoff = rules["evaluation"]["cutoff"]
    client = _budget(client, cutoff, clock)
    search_client = _budget(search_client, cutoff, clock)
    numbered = assign_numbers(kept)
    by_number = {number: message for number, message in numbered}
    candidate_settings = sectors["candidates"]
    batches = split_batches(numbered, batch_chars=int(candidate_settings["batchChars"]),
                            head_chars=int(candidate_settings["headChars"]))
    stats["candidates"]["batches"] = len(batches)
    if _cutoff_reached(clock(), cutoff):
        return _done(stats, _content(0, 0, 0, []), "板块线没有完成：收口时刻前没评完", [])
    selected, block = _pick_candidates(
        batches, client=client, options=candidate_settings["modelOptions"],
        head_chars=int(candidate_settings["headChars"]), workers=int(candidate_settings["concurrency"]),
        cutoff=cutoff, clock=clock, stats=stats)
    if block:
        return _done(stats, _content(0, 0, 0, []), block, [])
    if not selected:
        return _done(stats, _content(0, 0, 0, []), None, [])
    names = {item["name"] for item in concepts}
    by_name: dict[str, dict] = {}
    for item in concepts:
        by_name.setdefault(item["name"], item)
    mapped, block = _map_candidates(
        selected, by_number=by_number, names=names, concepts=concepts, db_path=db_path, day=day,
        client=client, settings=sectors["mapping"], cutoff=cutoff, clock=clock, stats=stats)
    if block:
        return _done(stats, _content(0, 0, 0, []), block, [])
    events = _attach(mapped, by_number, by_name)
    stats["mapping"]["events"] = len(events)
    stats["mapping"]["null"] = sum(1 for event in events if event["sector"] is None)
    stats["mapping"]["repeat"] = sum(1 for event in events if event["repeat"])
    stats["mapping"]["light"] = sum(1 for event in events if event.get("light"))
    now = clock()
    for event in events:
        if not event["skip"]:
            continue
        if _load(db_path, report_id, event["id"]) is None:
            _insert(db_path, report_id=report_id, event_id=event["id"], title=event["title"],
                    sector=event["sector"], reason=event["reason"], repeat=event["repeat"], state="skipped",
                    package=_skipped_package(event), prepared_at=now, settled_at=now)
    pending = [event for event in events if not event["skip"] and not _terminal(_load(db_path, report_id, event["id"]))]
    need_build = [event for event in pending if _load(db_path, report_id, event["id"]) is None]
    daily_rows: list[dict] = []
    as_of = None
    if need_build:
        try:
            as_of = previous_trading_day(day)
        except CalendarGap as exc:
            return _done(stats, _content(0, 0, 0, []), f"板块线没有完成：{exc}", _rows(db_path, report_id, events))
        if sector_live:
            filled = _await_market_daily(
                db_path, as_of=as_of, lookback_days=int(sectors["market"]["lookbackDays"]),
                prefixes=tuple(rules["companyPool"]["prefixes"]), cutoff=cutoff, clock=clock)
            if filled.error == FETCH_CUTOFF:
                return _done(stats, _content(0, 0, 0, []), "板块线没有完成：收口时刻前没备齐行情",
                             _rows(db_path, report_id, events))
            if not filled.ok:
                logger.error("板块线日线没取到：%s", filled.error)
                for event in need_build:
                    _insert(db_path, report_id=report_id, event_id=event["id"], title=event["title"],
                            sector=event["sector"], reason=event["reason"], repeat=False, state="gap",
                            package=_skipped_package(event), prepared_at=clock(), error="日线没取到",
                            settled_at=clock())
                rows = _rows(db_path, report_id, events)
                return _done(stats, _render(rows, pool, int(sectors["delivery"]["maxEvents"])),
                             "板块线没有完成：日线没取到", rows, stats_rows=rows)
        with db.read(db_path) as conn:
            daily_rows = db.read_market_daily(conn)
    elif pending:
        try:
            as_of = previous_trading_day(day)
        except CalendarGap as exc:
            return _done(stats, _content(0, 0, 0, []), f"板块线没有完成：{exc}", _rows(db_path, report_id, events))
    eval_hits: list[dict] = []
    started = time.monotonic()
    if pending and as_of is not None:
        if not daily_rows:
            with db.read(db_path) as conn:
                daily_rows = db.read_market_daily(conn)
        workers = int(sectors["evaluation"]["concurrency"])
        pool_rows: list[dict] = []
        try:
            with db.read(db_path) as conn:
                stored_pool = db.read_stock_pool(conn)
        except db.NotReady:
            stored_pool = None
        if stored_pool:
            pool_rows = list(stored_pool["rows"])

        def evaluate(event: Mapping) -> dict:
            return _evaluate_event(
                event, db_path=db_path, report_id=report_id, day=day, window=window, pool=pool,
                daily_rows=daily_rows, as_of=as_of, market=sectors["market"], carriers=sectors["carriers"],
                skip=skip, profiles=getattr(config, "profiles", {}) or {}, pool_rows=pool_rows,
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
    shown, overflow = select_pushed(rows, int(sectors["delivery"]["maxEvents"]))
    _finish_stats(stats, rows, eval_hits=eval_hits, overflow=overflow, seconds=seconds)
    gap = _eval_gap(rows)
    fatal = next((hit.get("fatal") for hit in eval_hits if hit.get("fatal")), None)
    if fatal and gap is None:
        gap = "板块线没有完成：模型拒绝了请求"
    gap = _join(gap, _search_misses(rows))
    return SectorOutcome(_annotate(stats, gap),
                         _render(rows, pool, int(sectors["delivery"]["maxEvents"]), shown=shown, overflow=overflow),
                         stats)


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
    """有的批次没挑成、或归并只成了一半时，已经拿到的仍然往下走，并补上缺口。"""
    notes: list[str] = []
    if isinstance(stats, dict):
        concept = stats.pop("conceptNote", None)
        if isinstance(concept, str) and concept:
            notes.append(concept)
    missed = int(stats["candidates"].get("failedBatches") or 0)
    if stats["candidates"].get("incomplete") and missed:
        # 板块照常出，只是这几批消息没看过
        notes.append(f"板块线有 {missed} 批消息没挑成（共 {stats['candidates']['batches']} 批）")
    if stats["mapping"].get("incomplete"):
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
        shown, overflow = select_pushed(rows, max_events)
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


def _attach(mapped: Sequence[Mapping], by_number: Mapping[str, Message], by_name: Mapping[str, Mapping]) -> list[dict]:
    events = []
    for item in mapped:
        messages = [by_number[number] for number in item["ids"] if number in by_number]
        if not messages:
            continue
        refs = [message.ref for message in messages]
        first = min(message.published_at for message in messages)
        concept = by_name.get(item["sector"]) if item["sector"] else None
        light = not bool(item.get("weighty"))
        reason = item["weight"] if light else item["reason"]
        events.append({
            "id": event_id(refs), "title": item["title"], "ids": list(item["ids"]), "messages": messages,
            "refs": refs, "sector": None if concept is None else concept["name"],
            "sectorCode": None if concept is None else concept["tsCode"],
            "members": [] if concept is None else list(concept["members"]),
            "reason": reason, "repeat": bool(item["repeat"]),
            "weighty": bool(item.get("weighty")), "weight": item.get("weight") or "", "light": light,
            "firstPublished": _minutes(first),
            "skip": concept is None or bool(item["repeat"]) or light,
        })
    return events


def _split_length_batch(batch: Sequence[tuple[str, Message]], hit: Mapping, *, client: Any, options: Mapping,
                        head_chars: int, cutoff: str, clock: Callable[[], datetime]) -> dict:
    """输出被截断时按顺序对半各发一次。只拆这一次，有一半失败这一批就没挑成。"""
    mid = len(batch) // 2
    calls = int(hit.get("calls") or 0)
    from_receipt = int(hit.get("fromReceipt") or 0)
    usage = dict(hit.get("usage") or _zero_usage())
    seconds = float(hit.get("seconds") or 0)
    merged: set[str] = set()
    for half in (batch[:mid], batch[mid:]):
        if _cutoff_reached(clock(), cutoff):
            return {"ids": set(), "cutoff": True, "calls": calls, "fromReceipt": from_receipt,
                    "usage": usage, "seconds": seconds, "split": True}
        allowed = {number for number, _message in half}
        messages = [
            {"role": "system", "content": CANDIDATE_SYSTEM},
            {"role": "user", "content": candidate_user_prompt(
                [candidate_line(number, message, head_chars) for number, message in half])},
        ]
        second = _call_model(client, messages, options)
        calls += int(second.get("calls") or 0)
        from_receipt += int(second.get("fromReceipt") or 0)
        usage = _add_usage(usage, second.get("usage") or {})
        seconds = round(seconds + float(second.get("seconds") or 0), 3)
        if second.get("cutoff") or _cutoff_reached(clock(), cutoff):
            return {"ids": set(), "cutoff": True, "calls": calls, "fromReceipt": from_receipt,
                    "usage": usage, "seconds": seconds, "split": True}
        if second.get("fatal"):
            second = dict(second)
            second.update(calls=calls, fromReceipt=from_receipt, usage=usage, seconds=seconds, split=True)
            return second
        parsed: set[str] | str = "调用失败"
        if not second.get("error") and second.get("finish") == "stop":
            parsed = parse_candidate_output(second.get("content") or "", allowed)
        if isinstance(parsed, str):
            reason = second.get("error") or (parsed if second.get("finish") == "stop" else f"finish:{second.get('finish')}")
            return {"ids": set(), "error": reason, "calls": calls, "fromReceipt": from_receipt,
                    "usage": usage, "seconds": seconds, "split": True}
        merged.update(parsed)
    return {"ids": merged, "calls": calls, "fromReceipt": from_receipt, "usage": usage,
            "seconds": seconds, "split": True, "finish": "stop"}


def _pick_candidates(batches: Sequence[Sequence[tuple[str, Message]]], *, client: Any, options: Mapping,
                     head_chars: int, workers: int, cutoff: str, clock: Callable[[], datetime],
                     stats: dict) -> tuple[set[str], str | None]:
    def work(batch: Sequence[tuple[str, Message]]) -> dict:
        if _cutoff_reached(clock(), cutoff):
            return {"ids": set(), "cutoff": True, "calls": 0, "fromReceipt": 0, "usage": _zero_usage(), "seconds": 0.0}
        allowed = {number for number, _message in batch}
        messages = [
            {"role": "system", "content": CANDIDATE_SYSTEM},
            {"role": "user", "content": candidate_user_prompt([candidate_line(number, message, head_chars)
                                                               for number, message in batch])},
        ]
        hit = _call_model(client, messages, options)
        if hit.get("cutoff") or _cutoff_reached(clock(), cutoff):
            hit["cutoff"] = True
            hit["ids"] = set()
            return hit
        if hit.get("fatal") or hit.get("error"):
            return hit
        if hit.get("finish") == "length" and len(batch) >= 2:
            return _split_length_batch(batch, hit, client=client, options=options, head_chars=head_chars,
                                       cutoff=cutoff, clock=clock)
        if hit.get("finish") != "stop":
            hit["error"] = f"finish:{hit.get('finish')}"
            return hit
        parsed = parse_candidate_output(hit.get("content") or "", allowed)
        if isinstance(parsed, str):
            hit["error"] = parsed
            return hit
        hit["ids"] = parsed
        return hit

    hits = _run_calls(list(batches), workers, work, cutoff=cutoff, clock=clock)
    if _cutoff_reached(clock(), cutoff) and any(_open_hit(hit) for hit in hits):
        return set(), _CUTOFF_GAP
    selected: set[str] = set()
    failed = 0
    for hit in hits:
        if hit is None:
            failed += 1
            continue
        if hit.get("split"):
            stats["candidates"]["splits"] += 1
        stats["candidates"]["calls"] += int(hit.get("calls") or 0)
        stats["candidates"]["fromReceipt"] += int(hit.get("fromReceipt") or 0)
        stats["candidates"]["usage"] = _add_usage(stats["candidates"]["usage"], hit.get("usage") or {})
        stats["candidates"]["seconds"] = round(stats["candidates"]["seconds"] + float(hit.get("seconds") or 0), 3)
        if hit.get("cutoff"):
            return set(), _CUTOFF_GAP
        if hit.get("fatal"):
            return set(), "板块线没有完成：挑候选没有完成"
        if hit.get("error"):
            failed += 1
            continue
        selected.update(hit.get("ids") or ())
    stats["candidates"]["messages"] = len(selected)
    if failed and not selected:
        return set(), "板块线没有完成：挑候选没有完成"
    if failed:
        stats["candidates"]["incomplete"] = True
        stats["candidates"]["failedBatches"] = failed
    return selected, None


def _add_mapping_hit(stats: dict, hit: Mapping) -> None:
    mapping = stats["mapping"]
    mapping["calls"] += int(hit.get("calls") or 0)
    mapping["fromReceipt"] += int(hit.get("fromReceipt") or 0)
    mapping["usage"] = _add_usage(mapping["usage"], hit.get("usage") or {})
    mapping["seconds"] = round(float(mapping["seconds"]) + float(hit.get("seconds") or 0), 3)


def _mapping_request(items: Sequence[Mapping], concepts: Sequence[Mapping],
                     previous: Sequence[tuple[str, str]]) -> list[dict]:
    user = mapping_user_prompt(items, [item["name"] for item in concepts], previous)
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
    """只有不重复、分量也够的事件才和同板块的其他有效事件合并。"""
    return not bool(event.get("repeat")) and bool(event.get("weighty"))


def _copied_mapping(event: Mapping) -> dict:
    return {
        "title": event["title"],
        "ids": list(event["ids"]),
        "sector": event.get("sector"),
        "reason": event["reason"],
        "repeat": bool(event["repeat"]),
        "weighty": bool(event.get("weighty")),
        "weight": event["weight"],
    }


def _merge_mapping_halves(left: Sequence[Mapping], right: Sequence[Mapping], ordered: Sequence[str],
                          left_dropped: Sequence, right_dropped: Sequence, names: set[str]) -> list[dict] | str:
    """同名板块里，重复或分量不够的各自保留；有效事件才合并，代表取首条消息最早的那件。"""
    position = {number: index for index, number in enumerate(ordered)}
    grouped: dict[str, list[Mapping]] = {}
    loose: list[Mapping] = []
    for event in (*left, *right):
        sector = event.get("sector")
        if isinstance(sector, str) and sector:
            grouped.setdefault(sector, []).append(event)
        else:
            loose.append(event)
    events: list[dict] = []
    for sector, bucket in grouped.items():
        bucket.sort(key=lambda event: _earliest_index(event["ids"], position))
        valid = [event for event in bucket if _mapping_usable(event)]
        valid_ids = {id(event) for event in valid}
        for event in bucket:
            if id(event) not in valid_ids:
                events.append(_copied_mapping(event))
        if not valid:
            continue
        head = valid[0]
        chosen = {item for event in valid for item in event["ids"]}
        merged = _copied_mapping(head)
        merged["ids"] = [number for number in ordered if number in chosen]
        merged["sector"] = sector
        merged["repeat"] = False
        merged["weighty"] = True
        events.append(merged)
    for event in loose:
        events.append({
            "title": event["title"], "ids": list(event["ids"]), "sector": None,
            "reason": event["reason"], "repeat": bool(event["repeat"]),
            "weighty": bool(event.get("weighty")), "weight": event["weight"],
        })
    events.sort(key=lambda event: _earliest_index(event["ids"], position))
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
                    cutoff: str, clock: Callable[[], datetime], stats: dict) -> tuple[list[dict], str | None]:
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
        items.append({
            "id": number, "source": message.source_label, "time": published_label(message.published_at),
            "title": title, "excerpt": excerpt,
        })
    previous = _previous_events(db_path, day)
    hit = _call_model(client, _mapping_request(items, concepts, previous), settings["modelOptions"])
    _add_mapping_hit(stats, hit)
    if hit.get("cutoff") or _cutoff_reached(clock(), cutoff):
        return [], _CUTOFF_GAP
    if hit.get("fatal") or hit.get("error"):
        return [], _MAPPING_GAP
    if hit.get("finish") == "length" and len(ordered) >= 2:
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
                    pool: CompanyPool, daily_rows: Sequence[Mapping], as_of: date, market: Mapping,
                    carriers: Mapping, skip: Sequence[str], profiles: Mapping, pool_rows: Sequence[Mapping],
                    search_client: Any, search_settings: Mapping, package_settings: Mapping, client: Any,
                    options: Mapping, cutoff: str, clock: Callable[[], datetime]) -> dict:
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
                event=event, day=day, window=window, pool=pool, daily_rows=daily_rows, as_of=as_of,
                market=market, carriers=carriers, skip=skip, profiles=profiles, pool_rows=pool_rows,
                search_client=search_client, search_settings=search_settings,
                package_settings=package_settings)
        except _StillOpen:
            _seal_gap(db_path, report_id, event, clock())
            return _abandoned_hit(repairs=0, repaired=0)
        now = clock()
        _insert(db_path, report_id=report_id, event_id=event["id"], title=event["title"], sector=event["sector"],
                reason=event["reason"], repeat=False, state="prepared", package=package, prepared_at=now)
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
