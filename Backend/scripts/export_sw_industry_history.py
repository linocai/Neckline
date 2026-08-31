#!/usr/bin/env python3
"""Export complete dated SW2021 membership snapshots from audited intervals.

The TuShare ``index_member_all`` endpoint separates current (``is_new=Y``)
and former (``is_new=N``) assignments.  This tool fetches every page of both,
expands their explicit ``in_date``/``out_date`` intervals over an explicit
official trading-day range, and writes the exact JSON contract accepted by
``import_sw_industry_history.py``.

The end date must be today so the final reconstructed snapshot can be checked
byte-for-byte at the normalized identity level against the independent current
membership response.  TuShare's ``out_date`` is the last included membership
date; a replacement begins on the following trading day.  Intervals are
therefore ``[in_date, out_date]``.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import trading_days_between  # noqa: E402
from neckline.config import settings  # noqa: E402
from neckline.data.tushare_client import TushareResult, ts_index_member_all  # noqa: E402

PAGE_LIMIT = 2_000
MAX_PAGES = 20
IDENTITY_FIELDS = (
    "ts_code", "name", "l1_code", "l1_name", "l2_code", "l2_name", "l3_code", "l3_name",
)


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value).strip()


def _records(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [dict(row) for row in data]
    if hasattr(data, "to_dict"):
        return list(data.to_dict("records"))
    raise TypeError(f"无法识别的 TuShare 返回类型:{type(data)!r}")


def fetch_all(state: str, *, fetcher: Callable[..., TushareResult] = ts_index_member_all) -> list[dict[str, Any]]:
    if state not in {"Y", "N"}:
        raise ValueError("is_new 必须为 Y 或 N")
    rows: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        offset = page * PAGE_LIMIT
        result = fetcher(limit=PAGE_LIMIT, offset=offset, is_new=state)
        if not result.ok:
            raise RuntimeError(f"index_member_all(is_new={state},offset={offset}) 失败:{result.reason}")
        batch = _records(result.data)
        rows.extend(batch)
        if len(batch) < PAGE_LIMIT:
            return rows
    raise RuntimeError(f"index_member_all(is_new={state}) 翻到 {MAX_PAGES} 页仍未结束")


def _day_token(value: Any, field: str, *, required: bool) -> date | None:
    text = _text(value)
    if not text and not required:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"{field} 不是 YYYYMMDD:{text!r}") from exc


def _normalize(rows: Iterable[dict[str, Any]], state: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in rows:
        source_state = _text(raw.get("is_new"))
        if source_state and source_state != state:
            raise ValueError(f"is_new={state} 响应混入 {source_state}")
        identity = tuple(_text(raw.get(field)) for field in IDENTITY_FIELDS)
        if any(not value for value in identity):
            raise ValueError(f"申万历史归属身份字段不完整:{identity}")
        in_day = _day_token(raw.get("in_date"), "in_date", required=True)
        out_day = _day_token(raw.get("out_date"), "out_date", required=False)
        if out_day is not None and in_day is not None and out_day < in_day:
            raise ValueError(f"{identity[0]} 的归属区间倒置:{in_day}~{out_day}")
        if state == "Y" and out_day is not None:
            raise ValueError(f"当前归属 {identity[0]} 不应含 out_date:{out_day}")
        if state == "N" and out_day is None:
            raise ValueError(f"历史归属 {identity[0]} 缺少 out_date")
        key = (*identity, in_day.strftime("%Y%m%d"), out_day.strftime("%Y%m%d") if out_day else "")
        if key in seen:
            raise ValueError(f"申万历史归属精确重复:{identity[0]} {key[-2:]} ")
        seen.add(key)
        normalized.append({
            **dict(zip(IDENTITY_FIELDS, identity)),
            "in_day": in_day,
            "out_day": out_day,
        })
    return normalized


def _identity(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row[field]) for field in IDENTITY_FIELDS)


def build_document(days: Sequence[date], current_rows: Iterable[dict[str, Any]],
                   former_rows: Iterable[dict[str, Any]], *, source_time: str) -> dict[str, Any]:
    ordered = sorted(set(days))
    if not ordered or len(ordered) != len(days):
        raise ValueError("交易日范围不能为空、重复或无序")
    current = _normalize(current_rows, "Y")
    former = _normalize(former_rows, "N")
    intervals = [*current, *former]
    expected_current: dict[str, tuple[str, ...]] = {}
    for row in current:
        code = str(row["ts_code"])
        if code in expected_current:
            raise ValueError(f"当前申万归属一票多行:{code}")
        expected_current[code] = _identity(row)

    snapshots: list[dict[str, Any]] = []
    final_identity: dict[str, tuple[str, ...]] = {}
    for trade_day in ordered:
        selected: dict[str, dict[str, Any]] = {}
        for row in intervals:
            if row["in_day"] <= trade_day and (row["out_day"] is None or trade_day <= row["out_day"]):
                code = str(row["ts_code"])
                if code in selected:
                    raise ValueError(f"{trade_day:%Y%m%d} 同票存在重叠申万归属:{code}")
                selected[code] = row
        members = [
            {"trade_date": trade_day.strftime("%Y%m%d"),
             **{field: str(row[field]) for field in IDENTITY_FIELDS}}
            for _code, row in sorted(selected.items())
        ]
        if not members:
            raise ValueError(f"{trade_day:%Y%m%d} 申万历史归属为空")
        snapshots.append({
            "tradeDate": trade_day.strftime("%Y%m%d"),
            "complete": True,
            "expectedMemberCount": len(members),
            "members": members,
        })
        if trade_day == ordered[-1]:
            final_identity = {code: _identity(row) for code, row in selected.items()}

    if final_identity != expected_current:
        missing = sorted(set(expected_current) - set(final_identity))
        extra = sorted(set(final_identity) - set(expected_current))
        changed = sorted(code for code in set(final_identity) & set(expected_current)
                         if final_identity[code] != expected_current[code])
        raise ValueError(
            "区间重建的最后一日与独立当前快照不一致:"
            f"missing={missing[:10]} extra={extra[:10]} changed={changed[:10]}"
        )
    return {
        "source": {
            "id": "tushare-index_member_all-intervals-v1",
            "generatedAt": source_time,
            "fetchedAt": source_time,
        },
        "snapshots": snapshots,
    }


def _atomic_write(path: Path, document: dict[str, Any]) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="first official trading day, YYYYMMDD")
    parser.add_argument("--end", required=True, help="latest official trading day, YYYYMMDD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=None, help="calendar database; defaults to configured DB")
    args = parser.parse_args()
    try:
        start = datetime.strptime(args.start, "%Y%m%d").date()
        end = datetime.strptime(args.end, "%Y%m%d").date()
        if end != date.today():
            raise ValueError("--end 必须是今天，才能与独立当前归属快照对拍")
        db_path = args.db or settings.db_path
        days = trading_days_between(start, end, db_path=db_path)
        if not days or days[0] != start or days[-1] != end:
            raise ValueError("起止日必须都是目标库已验证的官方交易日")
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current = fetch_all("Y")
        former = fetch_all("N")
        document = build_document(days, current, former, source_time=fetched_at)
        digest = _atomic_write(args.output, document)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"[sw-history-export] REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(args.output.resolve()),
        "sha256": digest,
        "dates": len(document["snapshots"]),
        "start": document["snapshots"][0]["tradeDate"],
        "end": document["snapshots"][-1]["tradeDate"],
        "finalMemberCount": document["snapshots"][-1]["expectedMemberCount"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
