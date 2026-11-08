"""C 路发酵统计：按交易日数报道条数，判断哪些板块在发酵。

不读整篇原文。正文在 SQL 里截断后再转成纯文本。任何一段采集没覆盖，当天不判发酵。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from neckline.morning import db
from neckline.morning.schedule import CST, CalendarGap, previous_trading_day
from neckline.screening.prefilter import to_message

_RAW_FACTOR = 8
_RAW_PAD = 2000


@dataclass
class FermentOutcome:
    ran: bool
    note: str | None
    boards: list[dict]
    blocked: str | None = None  # coverage：采集不全；calendar：交易日历不够


def board_keywords(name: str, aliases: Mapping[str, Sequence[str]]) -> list[str] | None:
    """别名表有这个板块就用别名（替换，空列表不统计）。否则拆括号、去掉概念后缀。"""
    if name in aliases:
        words = [word for word in aliases[name] if isinstance(word, str) and word]
        if not words and not list(aliases[name]):
            return None
        if not words:
            return None
        return _unique(words)
    parts = re.split(r"[()（）]", name)
    found: list[str] = []
    for part in parts:
        text = part.strip()
        if text.endswith("概念股"):
            text = text[: -len("概念股")].strip()
        elif text.endswith("概念"):
            text = text[: -len("概念")].strip()
        if len(text) >= 2:
            found.append(text)
    if not found:
        return None
    return _unique(found)


def history_bounds(day: date, window_end: str, periods: int) -> tuple[datetime, datetime]:
    """含当天在内的 periods 段所覆盖的 (起点, 终点]。起点是最早那段的开端。"""
    days = segment_days(day, periods)
    start = _window_end(previous_trading_day(days[0]), window_end)
    end = _window_end(days[-1], window_end)
    return start, end


def segment_days(day: date, periods: int) -> list[date]:
    """含 day 在内、往前数 periods 个交易日，从早到晚。"""
    if periods < 1:
        raise ValueError("交易日段数必须为正")
    days = [day]
    cursor = day
    for _ in range(periods - 1):
        cursor = previous_trading_day(cursor)
        days.append(cursor)
    days.reverse()
    return days


def segment_windows(day: date, window_end: str, periods: int) -> list[tuple[datetime, datetime]]:
    """交易日 t 的一段是 (上一交易日 windowEnd, t 的 windowEnd]。"""
    return [
        (_window_end(previous_trading_day(trade_day), window_end), _window_end(trade_day, window_end))
        for trade_day in segment_days(day, periods)
    ]


def count_fermenting(db_path: Path, *, day: date, collected_by: datetime, concepts: Sequence[Mapping],
                     prefilter: Mapping, intake: Mapping, fermenting: Mapping, window_end: str,
                     opening_text, rule_hit, word_pattern, compile_rules) -> FermentOutcome:
    """概念清单里的板块，最近每一段都明显高于基线的留下。采集有缺口则不跑。"""
    recent_days = int(fermenting["recentDays"])
    base_days = int(fermenting["baseDays"])
    try:
        windows = segment_windows(day, window_end, recent_days + base_days)
    except CalendarGap as exc:
        return FermentOutcome(False, str(exc), [], "calendar")
    drop_sources = set(prefilter["dropSources"])
    counted = _counted_sources(drop_sources)
    try:
        from neckline.morning.report import coverage_gaps
    except ImportError:  # pragma: no cover - 测试替身不会走到
        raise
    with db.read(db_path) as conn:
        runs = db.collections_since(conn, windows[0][0] - timedelta(days=3))
    reasons: list[str] = []
    for start, end in windows:
        for gap in coverage_gaps(runs, start, end, only=counted):
            if gap not in reasons:
                reasons.append(gap)
    if reasons:
        return FermentOutcome(False, "、".join(reasons), [], "coverage")
    prepared = []
    for concept in concepts:
        name = concept.get("name") if isinstance(concept, Mapping) else None
        if not isinstance(name, str) or not name:
            continue
        keywords = board_keywords(name, fermenting["aliases"])
        if not keywords:
            continue
        prepared.append({"name": name, "keywords": keywords, "pattern": word_pattern(keywords)})
    if not prepared:
        return FermentOutcome(True, None, [])
    body_chars = int(fermenting["bodyChars"])
    head_chars = int(prefilter["bodyHeadChars"])
    raw_chars = max(body_chars, head_chars) * _RAW_FACTOR + _RAW_PAD
    skip_names = set(fermenting["skipRules"])
    compiled = [rule for rule in compile_rules(prefilter) if rule["name"] in skip_names]
    noise = word_pattern(intake["noise"])
    totals = {item["name"]: [0] * len(windows) for item in prepared}
    seen = [set() for _ in windows]
    with db.read(db_path) as conn:
        rows = db.iter_document_heads(
            conn, published_after=windows[0][0], published_to=windows[-1][1],
            fetched_by=collected_by, text_chars=raw_chars)
        for row in rows:
            if row["sourceKey"] in drop_sources:
                continue
            published = datetime.fromisoformat(row["publishedAt"])
            index = _bucket(published, windows)
            if index is None:
                continue
            message = to_message(row)
            title = message.title or ""
            body = (message.text or "")[:body_chars]
            digest = hashlib.sha256(f"{title}{body}".encode()).hexdigest()
            if digest in seen[index]:
                continue
            seen[index].add(digest)
            opening = opening_text(message, head_chars)
            if any(rule_hit(rule, message, opening) for rule in compiled):
                continue
            if noise.search(opening):
                continue
            text = f"{title}\n{body}"
            for item in prepared:
                if item["pattern"].search(text):
                    totals[item["name"]][index] += 1
    growth = float(fermenting["growth"])
    minimum = float(fermenting["minDaily"])
    boards = []
    for item in prepared:
        counts = totals[item["name"]]
        base = counts[:base_days]
        recent = counts[base_days:]
        base_daily = sum(base) / base_days
        threshold = growth * max(minimum, base_daily)
        if recent and all(count >= threshold for count in recent):
            boards.append({
                "name": item["name"],
                "keywords": list(item["keywords"]),
                "recent": list(recent),
                "baseDaily": base_daily,
                "baseDays": base_days,
            })
    return FermentOutcome(True, None, boards)


def _unique(words: Sequence[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word not in seen:
            seen.add(word)
            found.append(word)
    return found


def _window_end(day: date, window_end: str) -> datetime:
    hour, minute = window_end.split(":")
    return datetime.combine(day, time(int(hour), int(minute), tzinfo=CST))


def _bucket(published: datetime, windows: Sequence[tuple[datetime, datetime]]) -> int | None:
    stamp = published.astimezone(CST)
    for index, (start, end) in enumerate(windows):
        if start < stamp <= end:
            return index
    return None


def _counted_sources(drop_sources: set[str]) -> set[str]:
    from neckline.morning.report import SOURCE_LABELS

    return {key for key in SOURCE_LABELS if key not in drop_sources}
