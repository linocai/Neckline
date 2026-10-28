"""评估用的 DeepSeek 联网搜索。每条候选一次请求，回执按地址加请求体哈希，不和聊天撞键。

搜索失败只记在这一条的数据包里，不让整期出报失败。结果只补背景和各方说法，日期晚于开工时刻的不要。
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping

import httpx

from neckline.morning.schedule import CST
from neckline.screening.deepseek import (
    ChatError, ChatRejected, ChatUnknown, Receipts, canonical, post_json,
)

_QUERY_LIMIT = 128
_ORI_LIMIT = 128
_ORI_TAIL = "：这件事对公司影响多大，媒体和券商怎么看"
_HTTP_STATUS = re.compile(r"http:\d+")


def _search_retryable(status: int) -> bool:
    """408、429 和全部 5xx 可以等一会再试。其余 4xx 不改请求就不要再发。"""
    return status == 408 or status == 429 or 500 <= status <= 599


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _dropped() -> dict[str, int]:
    return {"afterCutoff": 0, "noDate": 0, "badUrl": 0, "empty": 0}


def _failure(query: str, error: str, *, seconds: float = 0.0, from_receipt: bool = False) -> dict:
    return {
        "enabled": True,
        "query": query,
        "results": [],
        "returned": 0,
        "dropped": _dropped(),
        "error": error,
        "fromReceipt": from_receipt,
        "seconds": float(seconds),
    }


def _failure_code(exc: BaseException) -> str:
    if isinstance(exc, ChatRejected):
        if "凭据缺失" in str(exc):
            return "no_key"
        found = _HTTP_STATUS.search(str(exc))
        return found.group(0) if found else "rejected"
    if isinstance(exc, ChatUnknown):
        if isinstance(exc.__cause__, httpx.TimeoutException):
            return "timeout"
        return "unknown"
    text = str(exc)
    if text.startswith("http:"):
        return text
    if "Timeout" in text:
        return "timeout"
    if text.startswith("connect:"):
        return "connect"
    return text or "unknown"


def primary_subject(events: object, messages: object) -> tuple[str, str]:
    """主事件标题，以及主事件第一条消息的标题。没有就返回空字符串。"""
    if not isinstance(events, Mapping):
        return "", ""
    primary = events.get("primary")
    if not isinstance(primary, Mapping):
        return "", ""
    headline = _text(primary.get("headline"))
    title = ""
    mids = primary.get("messages")
    if isinstance(mids, list) and mids and isinstance(messages, list):
        wanted = mids[0]
        for message in messages:
            if isinstance(message, Mapping) and message.get("id") == wanted:
                title = _text(message.get("title"))
                break
    return headline, title


def compose_search(*, name: str, code: str, headline: str, message_title: str, trade_date: date | datetime) -> tuple[str, str]:
    """返回 (query, ori_question)。query 末尾保留 before:次日，超长只截前面的正文。"""
    subject = headline or message_title
    if name and subject:
        body = f"{name} {subject}"
    else:
        body = name or subject
    day = trade_date.date() if isinstance(trade_date, datetime) else trade_date
    if not isinstance(day, date):
        raise TypeError("trade_date 须为日期")
    suffix = f"before:{(day + timedelta(days=1)).isoformat()}"
    if body:
        tail = f" {suffix}"
        if len(body) + len(tail) <= _QUERY_LIMIT:
            query = body + tail
        else:
            query = body[:_QUERY_LIMIT - len(tail)] + tail
    else:
        query = suffix[:_QUERY_LIMIT]
    if subject:
        question = f"{name}（{code}）{subject}{_ORI_TAIL}"
    else:
        question = f"{name}（{code}）{_ORI_TAIL}"
    return query, question[:_ORI_LIMIT]


def _parse_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _web_url(value: object) -> str | None:
    if isinstance(value, str) and (value.startswith("http://") or value.startswith("https://")):
        return value
    return None


def project_results(raw: object, *, collected_by: datetime | None, summary_chars: int) -> tuple[list[dict], dict[str, int], int]:
    """按接口原顺序留下开工时刻之前的网页。一条结果只计入一种丢弃原因。"""
    dropped = _dropped()
    if not isinstance(raw, list):
        return [], dropped, 0
    cutoff = collected_by if isinstance(collected_by, datetime) and collected_by.tzinfo is not None else None
    kept: list[dict] = []
    for item in raw:
        if not isinstance(item, Mapping):
            dropped["empty"] += 1
            continue
        when = _parse_date(item.get("date"))
        if when is None:
            dropped["noDate"] += 1
            continue
        if cutoff is None or when > cutoff:
            dropped["afterCutoff"] += 1
            continue
        url = _web_url(item.get("url"))
        if url is None:
            dropped["badUrl"] += 1
            continue
        title = item.get("title") if isinstance(item.get("title"), str) else ""
        summary = item.get("summary") if isinstance(item.get("summary"), str) else ""
        if not title.strip() and not summary.strip():
            dropped["empty"] += 1
            continue
        truncated = summary_chars > 0 and len(summary) > summary_chars
        shown = summary[:summary_chars] if truncated else summary
        row = {
            "id": f"s{len(kept) + 1}",
            "title": title,
            "url": url,
            "siteName": item.get("site_name") if isinstance(item.get("site_name"), str) else "",
            "date": when.astimezone(CST).isoformat(timespec="seconds"),
            "authority": _number(item.get("authority")),
            "rerankScore": _number(item.get("rerank_score")),
            "summary": shown,
        }
        if truncated:
            row["truncated"] = True
        kept.append(row)
    return kept, dropped, len(raw)


@dataclass
class SearchOutcome:
    payload: Any
    from_receipt: bool
    seconds: float
    error: str | None


class SearchClient:
    """一次搜索一次 POST。回执键含 endpoint，避免和聊天请求体撞上。"""

    def __init__(self, *, endpoint: str, api_key: str | None, receipts: Receipts,
                 timeout_seconds: float, max_attempts: int,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.endpoint = endpoint
        self._api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self.receipts = receipts
        self.timeout = httpx.Timeout(timeout_seconds, connect=15.0)
        self.max_attempts = max_attempts
        self._transport = transport

    def request_body(self, *, query: str, count: int, ori_question: str) -> dict:
        return {"query": query, "count": count, "need_content": False, "ori_question": ori_question}

    def search(self, *, query: str, count: int, ori_question: str) -> SearchOutcome:
        body = self.request_body(query=query, count=count, ori_question=ori_question)
        digest = hashlib.sha256(canonical({"endpoint": self.endpoint, "body": body}).encode()).hexdigest()
        started = time.monotonic()
        try:
            payload, from_receipt, seconds = post_json(
                endpoint=self.endpoint, body=body, digest=digest, api_key=self._api_key,
                receipts=self.receipts, timeout=self.timeout, max_attempts=self.max_attempts,
                transport=self._transport, retryable=_search_retryable,
            )
        except ChatRejected as exc:
            code = _failure_code(exc)
            elapsed = 0.0 if code == "no_key" else round(time.monotonic() - started, 2)
            return SearchOutcome(payload=None, from_receipt=False, seconds=float(elapsed), error=code)
        except ChatUnknown as exc:
            return SearchOutcome(payload=None, from_receipt=False,
                                 seconds=float(round(time.monotonic() - started, 2)), error=_failure_code(exc))
        except ChatError as exc:
            return SearchOutcome(payload=None, from_receipt=False,
                                 seconds=float(round(time.monotonic() - started, 2)), error=_failure_code(exc))
        return SearchOutcome(payload=payload, from_receipt=from_receipt, seconds=float(seconds), error=None)


def run_search(*, client: SearchClient | None, count: int, summary_chars: int, company: Mapping,
               events: Mapping, messages: list, trade_date: date, collected_by: datetime | None) -> dict:
    """备料时调用。没有密钥或搜索失败都返回 enabled 块，不向外抛。"""
    name = _text(company.get("name") if isinstance(company, Mapping) else "")
    code = _text(company.get("code") if isinstance(company, Mapping) else "")
    headline, title = primary_subject(events, messages)
    query, ori = compose_search(name=name, code=code, headline=headline, message_title=title, trade_date=trade_date)
    if client is None:
        return _failure(query, "no_key")
    outcome = client.search(query=query, count=count, ori_question=ori)
    if outcome.error:
        return _failure(query, outcome.error, seconds=outcome.seconds, from_receipt=outcome.from_receipt)
    payload = outcome.payload if isinstance(outcome.payload, Mapping) else {}
    raw = payload.get("results")
    kept, dropped, returned = project_results(
        raw if isinstance(raw, list) else [], collected_by=collected_by, summary_chars=summary_chars)
    return {
        "enabled": True,
        "query": query,
        "results": kept,
        "returned": returned,
        "dropped": dropped,
        "error": None,
        "fromReceipt": bool(outcome.from_receipt),
        "seconds": float(outcome.seconds),
    }


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def summarize_search(rows: object, *, enabled: bool) -> dict:
    """从这一期的评估行汇总。没启用时只有 enabled。调用次数不含回执命中和没发请求的缺密钥。"""
    if not enabled:
        return {"enabled": False}
    calls = from_receipt = failed = returned = kept = views = 0
    seconds = 0.0
    dropped = _dropped()
    items = rows if isinstance(rows, list) else []
    for row in items:
        if not isinstance(row, Mapping):
            continue
        package = row.get("package") if isinstance(row.get("package"), Mapping) else {}
        search = package.get("search") if isinstance(package.get("search"), Mapping) else {}
        if search.get("enabled"):
            error = search.get("error")
            if search.get("fromReceipt"):
                from_receipt += 1
            elif error != "no_key":
                calls += 1
            if error:
                failed += 1
            seconds += float(search.get("seconds") or 0)
            returned += _as_int(search.get("returned"))
            results = search.get("results") if isinstance(search.get("results"), list) else []
            kept += len(results)
            raw_dropped = search.get("dropped") if isinstance(search.get("dropped"), Mapping) else {}
            for key in dropped:
                dropped[key] += _as_int(raw_dropped.get(key))
        output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
        for view in output.get("views") or []:
            if not isinstance(view, Mapping):
                continue
            ref = view.get("ref")
            if isinstance(ref, str) and ref.startswith("s"):
                views += 1
    return {
        "enabled": True,
        "calls": calls,
        "fromReceipt": from_receipt,
        "failed": failed,
        "seconds": round(seconds, 2),
        "returned": returned,
        "kept": kept,
        "dropped": dropped,
        "viewsCited": views,
    }
