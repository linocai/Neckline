"""对照用户 2026-10-18 的 50 条判断和 screening-v2 离线回放。只读文件，不外呼。

用法（在 Backend/ 下）：
  .venv/bin/python scripts/compare_verdicts.py --replay .work/p1-offline/v2

--replay 下每一期一个子目录，须有 events.json。窗口取同目录 stats.json 的 window 结束日期（北京时间）。
判断表默认读 .work/p1-offline/verdicts-20261018.json。先按子目录名对齐判断里的 run，对不上再按窗口日期。
同公司有多条事件时，用判断里的 event 描述和 headline（及 reason）做关键词匹配；匹配不上记为未找到。
选择结果用这些事件按 --config 里的 selection 重放，和 offline 同一套 select。
普通条目比用户「进/不进」和是否入选。typeOnly 条目是旧闻，只比种类该收、该判还是该排除，不看是否入选。
事件金额认 amount_yuan 或 amountYuan。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from neckline.screening.classify import CHANNELS, CompanyResult, Event  # noqa: E402
from neckline.screening.prefilter import Message  # noqa: E402
from neckline.screening.select import select  # noqa: E402

CST = timezone(timedelta(hours=8))


def _tokens(text: str) -> tuple[set[str], set[str]]:
    chars = re.findall(r"[\u4e00-\u9fff]", text or "")
    bigrams = {a + b for a, b in zip(chars, chars[1:])}
    numbers = set(re.findall(r"\d+(?:\.\d+)?", text or ""))
    return bigrams, numbers


def _score(description: str, headline: str, reason: str) -> float:
    wanted_words, wanted_numbers = _tokens(description)
    hay_words, hay_numbers = _tokens(f"{headline}{reason}")
    total = len(wanted_words) + len(wanted_numbers)
    if total == 0:
        return 0.0
    return (len(wanted_words & hay_words) + len(wanted_numbers & hay_numbers)) / total


def _amount(raw: dict):
    if "amount_yuan" in raw:
        return raw["amount_yuan"]
    if "amountYuan" in raw:
        return raw["amountYuan"]
    return None


def _has_amount(raw: dict) -> bool:
    return "amount_yuan" in raw or "amountYuan" in raw


def _event(raw: dict) -> Event | None:
    channel = raw.get("channel")
    amount = _amount(raw)
    ids = [str(item) for item in raw.get("ids") or []]
    if channel not in CHANNELS or not ids or not _has_amount(raw):
        return None
    if amount is not None and (isinstance(amount, bool) or not isinstance(amount, (int, float))):
        return None
    try:
        return Event(
            company=str(raw["company"]), ids=ids, relation=str(raw["relation"]), type=str(raw["type"]),
            direction=str(raw["direction"]),
            first_disclosed=raw.get("first_disclosed") if "first_disclosed" in raw else raw.get("firstDisclosed"),
            fresh=str(raw["fresh"]), headline=str(raw.get("headline") or ""),
            reason=str(raw.get("reason") or ""), channel=channel, amount_yuan=amount)
    except KeyError:
        return None


def _window_label(stats: dict) -> str | None:
    window = stats.get("window")
    if not isinstance(window, list) or len(window) < 2 or not isinstance(window[1], str):
        return None
    try:
        end = datetime.fromisoformat(window[1])
    except ValueError:
        return None
    if end.tzinfo is not None:
        end = end.astimezone(CST)
    return end.date().isoformat()


def _message(raw: dict, index: int, mid: str) -> Message:
    messages = raw.get("messages") or []
    meta = messages[index] if index < len(messages) else {}
    stamp = meta.get("publishedAt") or "2026-10-13T00:00:00+08:00"
    published = datetime.fromisoformat(stamp)
    return Message(
        document_id=str(meta.get("ref") or mid), revision=1, source_key="offline",
        source_label=str(meta.get("source") or ""), url=meta.get("url"), title=str(meta.get("title") or ""),
        text="", published_at=published, first_seen_at=published)


def selected_keys(events: list[dict], rules: dict) -> set[tuple[str, tuple[str, ...], str]]:
    by_id: dict[str, Message] = {}
    grouped: dict[str, list[Event]] = defaultdict(list)
    message_ids: dict[str, list[str]] = defaultdict(list)
    for raw in events:
        event = _event(raw)
        if event is None:
            continue
        grouped[event.company].append(event)
        message_ids[event.company].extend(event.ids)
        for index, mid in enumerate(event.ids):
            by_id.setdefault(mid, _message(raw, index, mid))
    results = {
        code: CompanyResult(company=code, message_ids=message_ids[code], events=found, mentions=[])
        for code, found in grouped.items()}
    if not results:
        return set()
    selection = select(by_id, results, rules)
    chosen = set()
    for entry in selection.candidates:
        for event in (entry.primary, *entry.others):
            chosen.add((event.company, tuple(event.ids), event.headline))
    return chosen


def _load_periods(root: Path, rules: dict) -> list[dict]:
    if (root / "events.json").is_file():
        dirs = [root]
    else:
        dirs = [path for path in sorted(root.iterdir()) if path.is_dir() and (path / "events.json").is_file()]
    periods = []
    for path in dirs:
        events = json.loads((path / "events.json").read_text(encoding="utf-8"))
        stats_path = path / "stats.json"
        stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.is_file() else {}
        periods.append({
            "name": path.name, "label": _window_label(stats), "events": events,
            "selected": selected_keys(events, rules),
        })
    return periods


def _find_period(verdict: dict, periods: list[dict]) -> dict | None:
    named = [period for period in periods if period["name"] == verdict.get("run")]
    if len(named) == 1:
        return named[0]
    labeled = [period for period in periods if period["label"] == verdict.get("window")]
    if len(labeled) == 1:
        return labeled[0]
    return None


def _pick(verdict: dict, events: list[dict]) -> dict | None:
    same = [event for event in events if event.get("company") == verdict.get("company")]
    if not same:
        return None
    if len(same) == 1:
        return same[0]
    scored = [(_score(str(verdict.get("event") or ""), str(item.get("headline") or ""),
                      str(item.get("reason") or "")), item) for item in same]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored[0][0] <= 0 or scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _kind_group(kind: str, rules: dict) -> str:
    if kind in rules["kinds"]["include"]:
        return "该收"
    if kind in rules["kinds"]["judge"]:
        return "该判"
    if kind in rules["kinds"]["exclude"]:
        return "该排除"
    return "未知"


def _rate(ok: int, total: int) -> str:
    if total == 0:
        return "0/0"
    return f"{ok}/{total}（{ok / total * 100:.1f}%）"


def _line(verdict: dict, result: str, event: dict | None) -> str:
    head = f"- {verdict.get('name')} {verdict.get('company')}：用户{verdict.get('user')}｜v2 {result}"
    if event is None:
        return f"{head}｜事件 {verdict.get('event')}"
    amount = _amount(event)
    amount_text = "null" if amount is None else str(amount)
    return (f"{head}｜种类 {event.get('type')}｜渠道 {event.get('channel')}｜金额 {amount_text}"
            f"｜时效 {event.get('fresh')}｜理由 {event.get('reason')}")


def compare(verdicts: list[dict], periods: list[dict], rules: dict) -> str:
    normal_ok = normal_total = type_ok = type_total = 0
    mismatches: list[str] = []
    for verdict in verdicts:
        period = _find_period(verdict, periods)
        raw = _pick(verdict, period["events"]) if period else None
        event = _event(raw) if raw else None
        user = verdict.get("user")
        if verdict.get("typeOnly"):
            type_total += 1
            if event is None:
                mismatches.append(_line(verdict, "未找到", None))
                continue
            group = _kind_group(event.type, rules)
            agrees = (user == "进" and group in ("该收", "该判")) or (user == "不进" and group == "该排除")
            type_ok += agrees
            if not agrees:
                mismatches.append(_line(verdict, f"种类{group}", raw))
            continue
        normal_total += 1
        if event is None:
            mismatches.append(_line(verdict, "未找到", None))
            continue
        entered = (event.company, tuple(event.ids), event.headline) in period["selected"]
        agrees = (user == "进") == entered
        normal_ok += agrees
        if not agrees:
            mismatches.append(_line(verdict, "进" if entered else "不进", raw))
    lines = [
        f"总一致率：{_rate(normal_ok, normal_total)}",
        f"typeOnly：{_rate(type_ok, type_total)}",
        "不一致：",
    ]
    lines.extend(mismatches or ["无"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="对照用户判断与 screening-v2 离线回放")
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--verdicts", type=Path,
                        default=_BACKEND / ".work" / "p1-offline" / "verdicts-20261018.json")
    parser.add_argument("--config", type=Path, default=_BACKEND / "neckline" / "config" / "screening-v3.json")
    args = parser.parse_args(argv)
    if not args.replay.is_dir():
        raise SystemExit(f"找不到回放目录：{args.replay}")
    if not args.verdicts.is_file():
        raise SystemExit(f"找不到判断表：{args.verdicts}")
    rules = json.loads(args.config.read_text(encoding="utf-8"))["selection"]
    periods = _load_periods(args.replay, rules)
    if not periods:
        raise SystemExit(f"回放目录里没有 events.json：{args.replay}")
    verdicts = json.loads(args.verdicts.read_text(encoding="utf-8"))
    print(compare(verdicts, periods, rules))
    return 0


if __name__ == "__main__":
    sys.exit(main())
