"""把 KX V3 总览（schema_version 4）投影成 Neckline 看板接口 v2。

整份不合格由 require_overview 抛 KxError。组、股票不合格只剔除并记入 gaps。
字段不合格置 null，不把 null 补成 0。金额保持 KX 给的元。history、rules、note、history_* 不投影。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, time

from neckline.kx.days import in_session
from neckline.kx.source import KxError
from neckline.morning.schedule import CST, CalendarGap

_CODE = re.compile(r"[036]\d{5}")
_CLOCK = re.compile(r"\d{2}:\d{2}(?::\d{2})?")
_ROOMS = ("leader", "waiting", "eliminated")
_TEXT_MAX = 4000
_MISSING = object()

_QUOTE_NUMBERS = (
    ("price", "price"), ("change_percent", "changePercent"), ("previous_close", "previousClose"),
    ("open", "open"), ("high", "high"), ("low", "low"), ("turnover_amount", "turnoverAmount"),
    ("amplitude_percent", "amplitudePercent"), ("turnover_percent", "turnoverPercent"),
)
_LIMIT_NUMBERS = (
    ("consecutive_boards", "consecutiveBoards"), ("break_count", "breakCount"), ("seal_amount", "sealAmount"),
)
_LIMIT_BOOLS = (("in_limit_up", "inLimitUp"), ("in_broken", "inBroken"), ("in_limit_down", "inLimitDown"))
_BOOK_NUMBERS = (("bid1_price", "bid1Price"), ("bid1_volume", "bid1Volume"), ("bid1_amount", "bid1Amount"),
                 ("seal_amount", "sealAmount"))


@dataclass
class _Gaps:
    groups: int = 0
    stocks: int = 0
    long_text: int = 0
    bad_numbers: int = 0
    bad_times: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out: list[str] = []
        if self.groups:
            out.append(f"{self.groups} 组数据不合格，没有显示")
        if self.stocks:
            out.append(f"{self.stocks} 只股票数据不合格，没有显示")
        if self.long_text:
            out.append(f"{self.long_text} 处文字超过 4000 字，没有显示")
        if self.bad_numbers:
            out.append(f"{self.bad_numbers} 处数字不合格，已留空")
        if self.bad_times:
            out.append(f"{self.bad_times} 处时间不合格，已留空")
        out.extend(self.notes)
        return out


def require_overview(body: object) -> dict:
    """整份不用时抛 KxError。schema_version 只认整数 4（布尔、浮点、字符串和其他整数都不认）。"""
    if not isinstance(body, dict):
        raise KxError("kx_bad_board", "KX 看板不是一份完整的数据。")
    version = body["schema_version"] if "schema_version" in body else _MISSING
    if type(version) is not int or version != 4:
        shown = "缺失" if version is _MISSING else repr(version)
        raise KxError("kx_bad_board", f"KX 看板格式变了（schema_version={shown}），没有按旧逻辑读")
    rooms = body.get("rooms")
    if not isinstance(rooms, dict) or any(not isinstance(rooms.get(name), list) for name in _ROOMS):
        raise KxError("kx_bad_board", "KX 看板的房间数据不合格，没有采用。")
    return body


def _text(value: object, gaps: _Gaps) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > _TEXT_MAX:
        gaps.long_text += 1
        return None
    return text


def _number(value: object, gaps: _Gaps) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        gaps.bad_numbers += 1
        return None
    return value


def _time(value: object, gaps: _Gaps) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        gaps.bad_times += 1
        return None
    text = value.strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text)
    except ValueError:
        gaps.bad_times += 1
        return None
    return text


def _seal_time(value: object, gaps: _Gaps) -> str | None:
    """封板时刻是当天的 HH:MM 或 HH:MM:SS，也可以是完整的 ISO 日期时间。合格就原样保留。"""
    if value is None:
        return None
    if not isinstance(value, str):
        gaps.bad_times += 1
        return None
    text = value.strip()
    if not text:
        return None
    if _CLOCK.fullmatch(text):
        try:
            time.fromisoformat(text)
        except ValueError:
            gaps.bad_times += 1
            return None
        return text
    return _time(text, gaps)


def _flag(value: object, gaps: _Gaps) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    gaps.bad_numbers += 1
    return None


def _stamp(moment: datetime) -> str:
    return moment.astimezone(CST).isoformat(timespec="seconds")


def _messages(value: object, gaps: _Gaps) -> list[str]:
    """字符串列表。KX 给对象时取 message，没有就转成一行文字，不丢。"""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            message = item.get("message")
            if isinstance(message, str) and message.strip():
                text = _text(message, gaps)
            else:
                text = _text(json.dumps(item, ensure_ascii=False, sort_keys=True), gaps)
        elif item is None:
            text = None
        else:
            text = _text(item if isinstance(item, str) else str(item), gaps)
        if text:
            out.append(text)
    return out


def _qualified(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    code, name = value.get("stock_code"), value.get("name")
    return isinstance(code, str) and _CODE.fullmatch(code) is not None and isinstance(name, str) and bool(name.strip())


def _flags(value: object, gaps: _Gaps) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        text = _text(item, gaps)
        if text is None:
            continue
        if ":" in text:
            kind, rest = text.split(":", 1)
            kind = kind.strip() or text
            code_text = rest.strip()
            code = code_text if _CODE.fullmatch(code_text) else None
        else:
            kind, code = text, None
        out.append({"kind": kind, "code": code, "text": text})
    return out


def _strings(value: object, gaps: _Gaps, *, codes: bool) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or len(text) > _TEXT_MAX:
            if isinstance(item, str) and len(item.strip()) > _TEXT_MAX:
                gaps.long_text += 1
            continue
        if codes and _CODE.fullmatch(text) is None:
            continue
        out.append(text)
    return out


def _quote(value: object, gaps: _Gaps) -> dict:
    raw = value if isinstance(value, dict) else {}
    projected = {target: _number(raw.get(source), gaps) for source, target in _QUOTE_NUMBERS}
    projected["sourceTime"] = _time(raw.get("source_time"), gaps)
    projected["quoteStatus"] = _text(raw.get("quote_status"), gaps)
    projected["sessionPhase"] = _text(raw.get("session_phase"), gaps)
    projected["warnings"] = _messages(raw.get("warnings"), gaps)
    return projected


def _limit_up(value: object, gaps: _Gaps) -> dict:
    raw = value if isinstance(value, dict) else {}
    projected = {target: _flag(raw.get(source), gaps) for source, target in _LIMIT_BOOLS}
    projected.update({target: _number(raw.get(source), gaps) for source, target in _LIMIT_NUMBERS})
    projected["firstSealTime"] = _seal_time(raw.get("first_seal_time"), gaps)
    projected["lastSealTime"] = _seal_time(raw.get("last_seal_time"), gaps)
    projected["boardTypeText"] = _text(raw.get("board_type_text"), gaps)
    projected["poolFetchedAt"] = _time(raw.get("pool_fetched_at"), gaps)
    projected["warnings"] = _messages(raw.get("warnings"), gaps)
    return projected


def _seal_change(value: object, gaps: _Gaps) -> dict | None:
    if not isinstance(value, dict):
        return None
    raw = value
    return {"amount": _number(raw.get("amount"), gaps),
            "baselineAmount": _number(raw.get("baseline_amount"), gaps),
            "baselineAgeSeconds": _number(raw.get("baseline_age_seconds"), gaps)}


def _order_book(value: object, gaps: _Gaps) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    projected = {target: _number(value.get(source), gaps) for source, target in _BOOK_NUMBERS}
    projected["status"] = _text(value.get("status"), gaps)
    projected["asksClear"] = _flag(value.get("asks_clear"), gaps)
    projected["sealed"] = _flag(value.get("sealed"), gaps)
    projected["sealChange5m"] = _seal_change(value.get("seal_change_5m"), gaps)
    projected["sourceTime"] = _time(value.get("source_time"), gaps)
    projected["warnings"] = _messages(value.get("warnings"), gaps)
    return projected


def _stock(value: dict, gaps: _Gaps, *, with_book: bool) -> dict:
    return {
        "code": value["stock_code"],
        "name": value["name"].strip(),
        "role": _text(value.get("role"), gaps),
        "reason": _text(value.get("reason"), gaps),
        "divergent": value.get("divergent") if isinstance(value.get("divergent"), bool) else False,
        "quote": _quote(value.get("quote"), gaps),
        "limitUp": _limit_up(value.get("limit_up"), gaps),
        "orderBook": _order_book(value.get("order_book"), gaps) if with_book else None,
    }


def _judgment(value: object, gaps: _Gaps) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    return {name: _text(value.get(name), gaps) for name in ("verdict", "reason", "height", "drive", "resilience")}


def _group(value: object, gaps: _Gaps, *, room: str) -> dict | None:
    if not isinstance(value, dict):
        gaps.groups += 1
        return None
    ident, theme = value.get("id"), value.get("theme")
    if _text(ident, gaps) is None or _text(theme, gaps) is None:
        gaps.groups += 1
        return None
    leaders = value.get("leaders")
    if not isinstance(leaders, list) or not 1 <= len(leaders) <= 2 or not all(_qualified(item) for item in leaders):
        gaps.groups += 1
        return None
    if "followers" not in value:
        followers_raw: list = []
    elif not isinstance(value["followers"], list):
        gaps.groups += 1
        return None
    else:
        followers_raw = value["followers"]
    with_book = room in ("leader", "waiting")
    followers: list[dict] = []
    for item in followers_raw:
        if _qualified(item):
            followers.append(_stock(item, gaps, with_book=False))
        else:
            gaps.stocks += 1
    return {
        "id": _text(ident, gaps),
        "theme": _text(theme, gaps),
        "roomSince": _time(value.get("room_since"), gaps),
        "brokeOn": _time(value.get("broke_on"), gaps),
        "observedTradingDays": _number(value.get("observed_trading_days"), gaps),
        "observedText": _text(value.get("observed_text"), gaps),
        "flags": _flags(value.get("flags"), gaps),
        "judgment": _judgment(value.get("judgment"), gaps),
        "note": _text(value.get("note"), gaps),
        "leaders": [_stock(item, gaps, with_book=with_book) for item in leaders],
        "followers": followers,
    }


def _highest(value: object, gaps: _Gaps) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    return {"boards": _number(value.get("boards"), gaps),
            "names": _strings(value.get("names"), gaps, codes=False),
            "codes": _strings(value.get("stock_codes"), gaps, codes=True)}


def _fetched(value: object, gaps: _Gaps) -> dict:
    raw = value if isinstance(value, dict) else {}
    return {"limitUp": _time(raw.get("limit_up"), gaps), "broken": _time(raw.get("broken"), gaps),
            "limitDown": _time(raw.get("limit_down"), gaps),
            "yesterdayLimitUp": _time(raw.get("yesterday_limit_up"), gaps)}


def _sentiment(value: object, gaps: _Gaps) -> dict:
    raw = value if isinstance(value, dict) else {}
    return {
        "tradingDay": _time(raw.get("trading_day"), gaps),
        "basis": _text(raw.get("basis"), gaps),
        "boardPhrases": _text(raw.get("board_phrases"), gaps),
        "limitUpCount": _number(raw.get("limit_up_count"), gaps),
        "brokenCount": _number(raw.get("broken_count"), gaps),
        "brokenRate": _number(raw.get("broken_rate"), gaps),
        "limitDownCount": _number(raw.get("limit_down_count"), gaps),
        "highestBoard": _highest(raw.get("highest_board"), gaps),
        "yesterdayConsecutive": _number(raw.get("yesterday_consecutive"), gaps),
        "yesterdayContinued": _number(raw.get("yesterday_continued"), gaps),
        "yesterdayTradingDay": _time(raw.get("yesterday_trading_day"), gaps),
        "fetchedAt": _fetched(raw.get("fetched_at"), gaps),
    }


def _refresh(value: object, gaps: _Gaps) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    return {"date": _time(value.get("date"), gaps), "result": _text(value.get("result"), gaps),
            "finishedAt": _time(value.get("finished_at"), gaps), "message": _text(value.get("message"), gaps),
            "notice": _text(value.get("notice"), gaps)}


def _fact_warnings(value: object, gaps: _Gaps) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if isinstance(item, str):
            text = _text(item, gaps)
            if text:
                out.append({"groupId": None, "text": text})
            continue
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = _text(item.get("text"), gaps)
        if not text:
            continue
        group = item.get("group_id")
        group_id = group.strip() if isinstance(group, str) else None
        if group_id is not None and len(group_id) > _TEXT_MAX:
            gaps.long_text += 1
            group_id = None
        elif group_id == "":
            group_id = None
        out.append({"groupId": group_id, "text": text})
    return out


def _status(value: object, gaps: _Gaps) -> object:
    if isinstance(value, str):
        return _text(value, gaps)
    return value


def _session(now: datetime, gaps: _Gaps) -> dict:
    try:
        active = in_session(now)
    except CalendarGap:
        gaps.notes.append("交易日历没有覆盖今天，盘中不自动刷新")
        active = False
    return {"inSession": active, "refreshSeconds": 60 if active else None}


def project_board(body: dict, *, now: datetime, last_read_at: datetime, stale: bool, reason: str | None) -> dict:
    """body 必须已经过 require_overview。房间、组、股票保持 KX 给的顺序。"""
    gaps = _Gaps()
    rooms_raw = body["rooms"]
    rooms = {name: [item for item in (_group(group, gaps, room=name) for group in rooms_raw[name]) if item]
             for name in _ROOMS}
    _session_state = _session(now, gaps)
    return {
        "generatedAt": _stamp(now),
        "source": {"checkedAt": _time(body.get("checked_at"), gaps), "stale": stale,
                   "reason": reason if stale else None, "lastReadAt": _stamp(last_read_at)},
        "structure": {
            "version": _text(body.get("version"), gaps),
            "asOf": _time(body.get("as_of"), gaps),
            "generatedAt": _time(body.get("generated_at"), gaps),
            "generatedBy": _text(body.get("generated_by"), gaps),
            "deepseekStatus": _text(body.get("deepseek_status"), gaps),
            "deepseekNotice": _text(body.get("deepseek_notice"), gaps),
            "marketNote": _text(body.get("market_note"), gaps),
            "membershipLocked": _flag(body.get("membership_locked"), gaps),
            "refresh": _refresh(body.get("refresh"), gaps),
        },
        "status": _status(body.get("status"), gaps),
        "errors": _messages(body.get("errors"), gaps),
        "warnings": _messages(body.get("warnings"), gaps),
        "gaps": gaps.lines(),
        "factWarnings": _fact_warnings(body.get("fact_warnings"), gaps),
        "session": _session_state,
        "sentiment": _sentiment(body.get("sentiment"), gaps),
        "rooms": rooms,
    }
