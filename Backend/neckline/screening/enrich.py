"""一级初筛之前，给只有标题的定期报告、业绩预告和业绩快报补上 PDF 读数。

同一文档版本先查 store，有就复用，保证续跑时消息文本不变、分类请求命中回执。
下载失败只记在统计里，不中断其他消息。
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Protocol

from neckline.filings.fetch import FetchResult, fetch_pdf
from neckline.filings.financials import (
    filing_kind, parse_express, parse_forecast, parse_periodic, summary_text,
)
from neckline.filings.reader import read_pdf
from neckline.morning import db

from .prefilter import Message

_PREFIX = re.compile(r"^([^：:\n]{1,20})[：:](.*)$")
_HEAD_MARKERS = ("报告", "预告", "快报", "公告", "关于")
_KINDS = {"periodic", "forecast", "express"}
_PARSERS = {"periodic": parse_periodic, "forecast": parse_forecast, "express": parse_express}


@dataclass(frozen=True)
class FilingRecord:
    document_id: str
    revision: int
    url: str
    kind: str
    state: str
    error: str | None
    figures_json: str | None
    summary: str
    read_at: str


class FilingReads(Protocol):
    def get(self, document_id: str, revision: int) -> FilingRecord | None: ...

    def put(self, record: FilingRecord) -> None: ...


class MemoryFilingReads:
    """离线工具用的内存缓存。"""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int], FilingRecord] = {}
        self._lock = threading.Lock()

    def get(self, document_id: str, revision: int) -> FilingRecord | None:
        with self._lock:
            return self._rows.get((document_id, revision))

    def put(self, record: FilingRecord) -> None:
        with self._lock:
            self._rows.setdefault((record.document_id, record.revision), record)


class DbFilingReads:
    """把读数写进晨报库的 filing_reads。同一版本已有记录时不覆盖。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def get(self, document_id: str, revision: int) -> FilingRecord | None:
        with self._lock:
            with db.read(self.path) as conn:
                row = conn.execute(
                    "SELECT document_id, revision, url, kind, state, error, figures_json, summary, read_at "
                    "FROM filing_reads WHERE document_id=? AND revision=?",
                    (document_id, revision)).fetchone()
        if row is None:
            return None
        return FilingRecord(
            document_id=row["document_id"], revision=row["revision"], url=row["url"], kind=row["kind"],
            state=row["state"], error=row["error"], figures_json=row["figures_json"],
            summary=row["summary"] or "", read_at=row["read_at"])

    def put(self, record: FilingRecord) -> None:
        with self._lock:
            with db.write(self.path) as conn:
                conn.execute(
                    "INSERT INTO filing_reads(document_id, revision, url, kind, state, error, figures_json, summary, read_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(document_id, revision) DO NOTHING",
                    (record.document_id, record.revision, record.url, record.kind, record.state, record.error,
                     record.figures_json, record.summary, record.read_at))


@dataclass
class EnrichStats:
    attempted: int = 0
    ok: int = 0
    failed: dict[str, int] = field(default_factory=dict)
    from_store: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {"attempted": self.attempted, "ok": self.ok, "failed": dict(self.failed),
                "fromStore": self.from_store, "seconds": self.seconds}


def announcement_title(title: str) -> str:
    """去掉巨潮标题开头的「简称：」。标题本身带冒号（如「关于…公告」）时保留全文。"""
    text = (title or "").strip()
    matched = _PREFIX.match(text)
    if matched is None:
        return text
    head, rest = matched.group(1).strip(), matched.group(2).strip()
    if not rest or any(word in head for word in _HEAD_MARKERS):
        return text
    return rest


def filing_title_kind(title: str) -> str | None:
    kind = filing_kind(announcement_title(title))
    return kind if kind in _KINDS else None


def purge_old_pdfs(cache_dir: Path, *, cache_days: int, now: datetime) -> None:
    """删掉缓存目录里修改时间早于 cache_days 天的 pdf。只动这个目录下的 pdf 文件。"""
    if not cache_dir.is_dir():
        return
    cutoff = now.timestamp() - cache_days * 86400
    root = cache_dir.resolve()
    for path in cache_dir.glob("*.pdf"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.resolve().parent != root or path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
        except OSError:
            continue


def pdf_fetcher(cache_dir: Path, settings: Mapping) -> Callable[[str], FetchResult]:
    def fetch(url: str) -> FetchResult:
        return fetch_pdf(url, cache_dir=cache_dir, max_bytes=int(settings["maxBytes"]),
                         timeout_seconds=float(settings["timeoutSeconds"]))

    return fetch


def _append(message: Message, suffix: str) -> None:
    message.text = f"{message.text}\n{suffix}" if message.text else suffix


def _failure(reason: str) -> str:
    return f"【公告 PDF 读数】未能读出（{reason}）"


def _call(fetcher: Callable[[str], FetchResult], url: str) -> FetchResult:
    try:
        result = fetcher(url)
    except Exception:
        return FetchResult(None, "transport_error", False)
    if result is None or result.error or result.path is None:
        return FetchResult(None, (result.error if result else None) or "transport_error", False)
    return result


def _download(fetcher: Callable[[str], FetchResult], url: str) -> FetchResult:
    first = _call(fetcher, url)
    if first.error is None:
        return first
    return _call(fetcher, url)


class _StoreBroken(Exception):
    """缓存读写失败。原异常要原样抛出，不能改成失败文本后继续分类。"""

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


def _store_get(store: FilingReads, document_id: str, revision: int) -> FilingRecord | None:
    try:
        return store.get(document_id, revision)
    except Exception as exc:
        raise _StoreBroken(exc) from exc


def _store_put(store: FilingReads, record: FilingRecord) -> None:
    try:
        store.put(record)
    except Exception as exc:
        raise _StoreBroken(exc) from exc


def _read_one(message: Message, kind: str, *, store: FilingReads, fetcher: Callable[[str], FetchResult],
              settings: Mapping, clock: Callable[[], datetime]) -> tuple[str, str | None]:
    """返回 (来源 store|fresh, 失败原因或 None)。消息文本就地追加。"""
    cached = _store_get(store, message.document_id, message.revision)
    if cached is not None:
        _append(message, cached.summary or _failure(cached.error or "未知"))
        return "store", None if cached.state == "ok" else (cached.error or "未知")
    fetched = _download(fetcher, message.url or "")
    reason: str | None = None
    summary = ""
    figures_json = None
    state = "error"
    if fetched.error or fetched.path is None:
        reason = fetched.error or "transport_error"
        summary = _failure(reason)
    else:
        document = read_pdf(fetched.path, max_pages=int(settings["maxPages"]))
        if document.error and not any(page.strip() for page in document.pages):
            reason = document.error or "no_text"
            summary = _failure(reason)
        else:
            figures = _PARSERS[kind]("\n".join(document.pages))
            summary = summary_text(figures)
            figures_json = json.dumps(asdict(figures), ensure_ascii=False, sort_keys=True)
            state = "ok"
    _store_put(store, FilingRecord(
        document_id=message.document_id, revision=message.revision, url=message.url or "", kind=kind,
        state=state, error=reason, figures_json=figures_json, summary=summary, read_at=db.utc(clock())))
    _append(message, summary)
    return "fresh", reason


def enrich_filings(messages: list[Message], *, store: FilingReads, fetcher: Callable[[str], FetchResult],
                   settings: Mapping, clock: Callable[[], datetime]) -> EnrichStats:
    """给巨潮定期报告、业绩预告、业绩快报追加 PDF 读数。返回统计，不抛出下载或解析错误。

    读数缓存的读写失败直接抛出，不改消息文本，避免续跑时分类请求变掉。
    """
    started = time.monotonic()
    jobs = [(message, kind) for message in messages if (kind := _eligible(message))]
    stats = EnrichStats(attempted=len(jobs))
    if not jobs:
        stats.seconds = round(time.monotonic() - started, 3)
        return stats
    failed: Counter = Counter()
    workers = max(1, int(settings["concurrency"]))

    def run(job: tuple[Message, str]) -> tuple[str, str | None]:
        message, kind = job
        try:
            return _read_one(message, kind, store=store, fetcher=fetcher, settings=settings, clock=clock)
        except _StoreBroken as broken:
            raise broken.original from None
        except Exception:
            if "【公告 PDF 读数】" not in message.text:
                _append(message, _failure("transport_error"))
            return "fresh", "transport_error"

    with ThreadPoolExecutor(max_workers=workers) as executor:
        outcomes = list(executor.map(run, jobs))
    for source, reason in outcomes:
        if source == "store":
            stats.from_store += 1
        if reason is None:
            stats.ok += 1
        else:
            failed[reason] += 1
    stats.failed = dict(failed)
    stats.seconds = round(time.monotonic() - started, 3)
    return stats


def _eligible(message: Message) -> str | None:
    if message.source_key != "cninfo-announcement" or not message.url:
        return None
    return filing_title_kind(message.title)
