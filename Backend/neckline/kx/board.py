"""KX V4 盘后看板缓存：SQLite BEGIN IMMEDIATE 单调持久化 + 单飞并发保护 + 盘中静默。

- 入库仅保留 schema 5 看板白名单字段并清洗诊断文案（丢弃 `change_reason` 等无关自由文本）。
- 写库使用 `BEGIN IMMEDIATE` 原子比较版本与回执完整性，杜绝跨进程竞态倒退。
- 独立的超时状态表（`kx_watch_state`）即使在空缓存或同日未确认快照场景下也不会丢失。
- 手动刷新（含空缓存失败场景）统一受 10 秒防抖保护，冷却期内复用上次结果或错误。
"""
from __future__ import annotations

import asyncio
import copy
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from neckline.kx.project import SCHEMA_VERSION, project_board, require_overview
from neckline.kx.services import KxServices
from neckline.kx.source import KxError, redact_upstream
from neckline.morning import db

_LOG = logging.getLogger("neckline.kx")

QUIET_CACHE_SECONDS = 600
MANUAL_COOLDOWN_SECONDS = 10
FAILURE_SECONDS = 15

_ALLOWED_RAW_KEYS = frozenset((
    "schema_version", "status", "version", "as_of", "generated_at", "generated_by",
    "checked_at", "deepseek_status", "deepseek_notice", "fact_warnings", "push_summary",
    "refresh", "capacity", "sentiment", "factions", "changes", "items",
))


class BoardUnavailable(Exception):
    """看板出不来：reason 是给程序看的短码，message 是如实说明的中文原因。"""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


@dataclass
class _State:
    lock: asyncio.Lock | None = None
    loop: asyncio.AbstractEventLoop | None = None
    last_manual_mono: float | None = None
    fail_mono: float | None = None
    fail_code: str | None = None
    fail_reason: str | None = None


_state = _State()


def reset_board_state() -> None:
    """测试隔离：清掉进程内的冷却与失败状态。"""
    global _state
    _state = _State()


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _state.lock is None or _state.loop is not loop:
        _state.lock = asyncio.Lock()
        _state.loop = loop
    return _state.lock


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _sanitize_raw_document(body: dict) -> dict:
    """仅保留看板所需 schema 5 字段并清洗可能携带上游地址或访问码的诊断文案。"""
    cleaned = {k: copy.deepcopy(v) for k, v in body.items() if k in _ALLOWED_RAW_KEYS}
    if isinstance(cleaned.get("deepseek_notice"), str) and cleaned["deepseek_notice"].strip():
        cleaned["deepseek_notice"] = redact_upstream(cleaned["deepseek_notice"])
    if isinstance(cleaned.get("push_summary"), str) and cleaned["push_summary"].strip():
        cleaned["push_summary"] = redact_upstream(cleaned["push_summary"])
    if isinstance(cleaned.get("fact_warnings"), list):
        cleaned["fact_warnings"] = [
            redact_upstream(w) if isinstance(w, str) and w.strip() else w
            for w in cleaned["fact_warnings"]
        ]
    refresh = cleaned.get("refresh")
    if isinstance(refresh, dict):
        for key in ("message", "notice"):
            val = refresh.get(key)
            if isinstance(val, str) and val.strip():
                refresh[key] = redact_upstream(val)
    return cleaned


def _effective_notice(row: dict) -> str | None:
    return row.get("lastError") or row.get("timeoutNotice")


def _read_db_cache(db_path: Path) -> tuple[dict | None, datetime | None, str | None]:
    with db.read(db_path) as conn:
        row = db.get_kx_board_cache(conn)
    if row is None:
        return None, None, None
    try:
        raw = require_overview(row["raw"])
        fetched_at = _parse_utc(row["fetchedAt"])
    except (KxError, ValueError):
        return None, None, None
    return raw, fetched_at, _effective_notice(row)


def _write_db_cache(db_path: Path, body: dict, fetched_at: datetime) -> dict:
    sanitized = _sanitize_raw_document(body)
    with db.write_immediate(db_path) as conn:
        return db.save_kx_board_cache(
            conn,
            schema_version=SCHEMA_VERSION,
            board_version=sanitized["version"],
            as_of=sanitized["as_of"],
            generated_at=sanitized["generated_at"],
            deepseek_status=sanitized["deepseek_status"],
            status=sanitized["status"],
            raw=sanitized,
            fetched_at=fetched_at,
        )


def _record_db_error(db_path: Path, error: str, at: datetime) -> None:
    try:
        with db.write_immediate(db_path) as conn:
            db.record_kx_board_error(conn, error=error, at=at)
    except Exception:  # noqa: BLE001
        pass


def _failed(kx: KxServices, cached: dict | None, fetched_at: datetime | None) -> dict:
    code = _state.fail_code or "kx_unavailable"
    reason = _state.fail_reason or "KX 暂时没有读到看板。"
    if cached is None or fetched_at is None:
        if code == "kx_unsupported_schema":
            raise BoardUnavailable(code, reason)
        raise BoardUnavailable(code, f"KX 看板还没读到过：{reason}")
    return project_board(cached, now=kx.now(), last_read_at=fetched_at, stale=True, reason=reason)


async def fetch_and_persist(kx: KxServices, *, db_path: Path) -> tuple[dict, dict, datetime]:
    """向 KX 拉取最新 JSON、校验 schema 5、脱敏后在 BEGIN IMMEDIATE 中单调落库。"""
    raw_in = await kx.source.overview(sleep=kx.sleep)
    moment = kx.now()
    try:
        body = require_overview(raw_in)
        project_board(body, now=moment, last_read_at=moment, stale=False, reason=None)
    except KxError:
        raise
    except Exception as error:
        frames = "".join(traceback.format_tb(error.__traceback__))
        _LOG.error("KX 看板投影失败：%s\n%s", type(error).__name__, frames)
        raise KxError("kx_bad_board", "KX 看板数据无法处理，没有采用。") from None

    winner = _write_db_cache(db_path, body, moment)
    winner_body = winner["raw"]
    winner_fetched = _parse_utc(winner["fetchedAt"])
    notice = _effective_notice(winner)
    projected = project_board(
        winner_body,
        now=moment,
        last_read_at=winner_fetched,
        stale=bool(notice),
        reason=notice,
    )
    return winner_body, projected, winner_fetched


async def build_board(kx: KxServices, *, db_path: Path, force_refresh: bool = False) -> dict:
    """读 KX 看板。普通请求优先复用 SQLite 持久化快照；force_refresh=True 时受 10 秒防抖保护即时回源。"""
    async with _lock():
        now = kx.now()
        now_mono = kx.monotonic()
        cached, fetched_at, db_error = _read_db_cache(db_path)

        if not force_refresh and cached is not None and fetched_at is not None:
            age = now - fetched_at
            if timedelta(seconds=-5) <= age < timedelta(seconds=QUIET_CACHE_SECONDS):
                return project_board(
                    cached,
                    now=now,
                    last_read_at=fetched_at,
                    stale=bool(db_error),
                    reason=db_error,
                )

        if (force_refresh and _state.last_manual_mono is not None
                and now_mono - _state.last_manual_mono < MANUAL_COOLDOWN_SECONDS):
            if cached is not None and fetched_at is not None:
                return project_board(
                    cached,
                    now=now,
                    last_read_at=fetched_at,
                    stale=bool(db_error),
                    reason=db_error,
                )
            if _state.fail_mono is not None:
                return _failed(kx, None, None)

        if (not force_refresh and _state.fail_mono is not None
                and now_mono - _state.fail_mono < FAILURE_SECONDS):
            return _failed(kx, cached, fetched_at)

        if force_refresh:
            _state.last_manual_mono = kx.monotonic()

        try:
            _body, projected, _moment = await fetch_and_persist(kx, db_path=db_path)
        except KxError as error:
            _state.fail_mono = kx.monotonic()
            _state.fail_code = error.code
            _state.fail_reason = redact_upstream(error.message)
            _record_db_error(db_path, _state.fail_reason, kx.now())
            return _failed(kx, cached, fetched_at)

        _state.fail_mono = None
        _state.fail_code = None
        _state.fail_reason = None
        return projected
