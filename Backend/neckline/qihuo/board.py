"""Q 期货只读看板缓存：纯只读读取（App / MCP / 只读网页绝不访问上游或写库）与同步专用写库（BEGIN IMMEDIATE + ETag 304，零推送）。"""
from __future__ import annotations

import copy
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from neckline.morning import db
from neckline.qihuo.project import SCHEMA_VERSION, project_board, require_bundle
from neckline.qihuo.services import QihuoServices
from neckline.qihuo.source import QihuoError, redact_upstream

_LOG = logging.getLogger("neckline.qihuo")

_ALLOWED_RAW_KEYS = frozenset((
    "schema_version", "version", "generated_at", "as_of_date", "retention_days",
    "schedule", "latest_collect", "pool", "agent_prompt", "products",
))


class QihuoUnavailable(Exception):
    """Q 期货看板暂不可用：reason 为短码，message 为脱敏中文说明。"""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


def reset_board_state() -> None:
    """保留给测试隔离的空钩子（读路径无进程内状态，全部由 SQLite 持久缓存驱动）。"""


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _sanitize_raw_bundle(body: dict[str, Any]) -> dict[str, Any]:
    """白名单过滤顶层字段并脱敏上游诊断文案，不修改正常的期货新闻外部原文链接。"""
    cleaned = {k: copy.deepcopy(v) for k, v in body.items() if k in _ALLOWED_RAW_KEYS}
    collect = cleaned.get("latest_collect")
    if isinstance(collect, dict) and isinstance(collect.get("error"), str) and collect["error"].strip():
        collect["error"] = redact_upstream(collect["error"])
    for prod in cleaned.get("products") or []:
        if not isinstance(prod, dict):
            continue
        status = prod.get("status")
        if isinstance(status, dict):
            for col in ("data", "news", "holding"):
                entry = status.get(col)
                if isinstance(entry, dict):
                    for key in ("message", "text"):
                        val = entry.get(key)
                        if isinstance(val, str) and val.strip():
                            entry[key] = redact_upstream(val)
    return cleaned


def _read_db_row(db_path: Path) -> dict[str, Any] | None:
    try:
        with db.read(db_path) as conn:
            return db.get_qihuo_bundle_cache(conn)
    except db.NotReady as exc:
        raise QihuoUnavailable("v4_db_not_ready", "数据缓存库尚未就绪，请稍后再试。") from exc


def _is_valid_bundle(raw: Any, *, moment: datetime | None = None) -> bool:
    """完整契约与投影自检（含嵌套集合字段类型），供发送 ETag 与事务内旧行排序共用。"""
    try:
        checked = require_bundle(raw)
        ref_time = moment or datetime.now().astimezone()
        projected = project_board(checked, now=ref_time, last_read_at=ref_time, stale=False, reason=None)
        json.dumps(projected, ensure_ascii=False, allow_nan=False)
        return True
    except Exception:  # noqa: BLE001
        return False


def _write_db_cache(
    db_path: Path,
    body: dict[str, Any],
    *,
    etag: str | None,
    fetched_at: datetime,
    observed_blocked_at: str | None = None,
) -> dict[str, Any]:
    sanitized = _sanitize_raw_bundle(body)
    with db.write_immediate(db_path) as conn:
        return db.save_qihuo_bundle_cache(
            conn,
            schema_version=SCHEMA_VERSION,
            bundle_version=sanitized["version"],
            as_of_date=sanitized.get("as_of_date"),
            generated_at=sanitized["generated_at"],
            etag=etag,
            raw=sanitized,
            fetched_at=fetched_at,
            observed_blocked_at=observed_blocked_at,
            validate_existing=_is_valid_bundle,
        )


def _touch_db_cache(
    db_path: Path,
    *,
    expected_etag: str | None,
    etag: str | None,
    fetched_at: datetime,
) -> dict[str, Any] | None:
    with db.write_immediate(db_path) as conn:
        return db.touch_qihuo_bundle_cache(
            conn,
            expected_etag=expected_etag,
            etag=etag,
            fetched_at=fetched_at,
        )


async def fetch_and_persist(
    qihuo: QihuoServices,
    *,
    db_path: Path,
    allow_fetch: Any = None,
    allow_refetch: Any = None,
    observed_at_out: list[datetime] | None = None,
) -> tuple[dict[str, Any], datetime, bool]:
    """仅由同步任务调用：向 Qihuo 拉取最新 JSON（带 ETag 条件请求）、校验 schema 3 并单调落库。
    - 仅对通过 require_bundle 且未处于协议阻断的缓存发送 ETag，语义损坏行自动拉取完整体自愈；
    - 每次实际外呼前均通过窗口检查（读库后、外呼前），杜绝越界外呼；
    - 写入时校验 observed_blocked_at，迟到的旧 v3 绝不清除并发较新请求落下的协议阻断。
    返回 (胜出 raw, fetched_at, not_modified)。
    """
    window_guard = allow_fetch or allow_refetch
    row = _read_db_row(db_path)
    observed_blocked_at: str | None = row.get("schemaBlockedAt") if row is not None else None
    expected_etag: str | None = None
    if (
        row is not None
        and not row.get("corrupted")
        and row.get("raw") is not None
        and not row.get("schemaBlockedError")
        and _is_valid_bundle(row["raw"], moment=qihuo.now())
    ):
        expected_etag = row.get("etag")

    if window_guard is not None and not window_guard():
        raise QihuoError("qihuo_outside_window", "已超过同步截止时刻，停止外呼。")

    result = await qihuo.source.fetch(etag=expected_etag)
    moment = qihuo.now()
    if observed_at_out is not None:
        observed_at_out.clear()
        observed_at_out.append(moment)

    if result.not_modified:
        if expected_etag is None:
            raise QihuoError("qihuo_bad_board", "Qihuo 对无条件请求返回了异常的 304 响应。")
        touched = _touch_db_cache(
            db_path,
            expected_etag=expected_etag,
            etag=result.etag,
            fetched_at=moment,
        )
        if (
            touched is not None
            and not touched.get("corrupted")
            and touched.get("raw") is not None
            and _is_valid_bundle(touched["raw"], moment=moment)
        ):
            return touched["raw"], _parse_utc(touched["fetchedAt"]), True
        # 缓存已被清空、嵌套损坏或处于协议阻断：若仍在窗口内则不带 ETag 重拉完整体
        if window_guard is not None and not window_guard():
            raise QihuoError("qihuo_outside_window", "已超过同步截止时刻，停止二次补拉。")
        result = await qihuo.source.fetch(etag=None)
        moment = qihuo.now()
        if observed_at_out is not None:
            observed_at_out.clear()
            observed_at_out.append(moment)
        if result.not_modified:
            raise QihuoError("qihuo_bad_board", "Qihuo 返回了空的 304 响应。")

    try:
        body = require_bundle(result.body)
        # 提前走一次投影与严格 JSON（allow_nan=False）校验，坏结构或非有限值绝不入库
        projected = project_board(body, now=moment, last_read_at=moment, stale=False, reason=None)
        json.dumps(projected, ensure_ascii=False, allow_nan=False)
    except QihuoError:
        raise
    except Exception as error:
        frames = "".join(traceback.format_tb(error.__traceback__))
        _LOG.error("Qihuo 看板投影失败：%s\n%s", type(error).__name__, frames)
        raise QihuoError("qihuo_bad_board", "Qihuo 数据无法处理，没有采用。") from None

    winner = _write_db_cache(
        db_path,
        body,
        etag=result.etag,
        fetched_at=moment,
        observed_blocked_at=observed_blocked_at,
    )
    if winner.get("schemaBlockedError"):
        raise QihuoError("qihuo_stale_overtaken", redact_upstream(winner["schemaBlockedError"]))
    return winner["raw"], _parse_utc(winner["fetchedAt"]), False


async def load_bundle(
    qihuo: QihuoServices,
    *,
    db_path: Path,
    force_refresh: bool = False,  # 保留参数签名兼容：App 手动刷新重新读取 NB 缓存，绝不直接打上游
) -> tuple[dict[str, Any], datetime, bool, str | None]:
    """统一纯只读接口（供 App 看板、MCP 工具与只读 `/read` 复用）：
    只读 NB SQLite 缓存，绝不访问 Qihuo 上游或写库；
    若存在未解除的协议阻断（schemaBlockedError），始终优先报 503，普通网络故障绝不解除它。
    """
    _ = (qihuo, force_refresh)
    row = _read_db_row(db_path)
    if row is None:
        raise QihuoUnavailable("qihuo_unavailable", "Q 期货看板尚未完成同步，请等待交易日晚间同步后再试。")

    schema_blocked = row.get("schemaBlockedError")
    if schema_blocked:
        raise QihuoUnavailable("qihuo_unsupported_schema", redact_upstream(schema_blocked))

    err_reason = row.get("lastErrorReason")
    err_msg = row.get("lastError")
    if err_reason == "qihuo_unsupported_schema":
        raise QihuoUnavailable(
            "qihuo_unsupported_schema",
            redact_upstream(err_msg) if err_msg else "Qihuo 数据协议版本暂不支持，未强行解析。",
        )

    if row.get("corrupted") or row.get("raw") is None:
        reason = err_reason or "qihuo_unavailable"
        msg = redact_upstream(err_msg) if err_msg else "Q 期货缓存快照暂不可用，请等待晚间同步恢复。"
        raise QihuoUnavailable(reason, msg)

    try:
        raw = require_bundle(row["raw"])
        fetched_at = _parse_utc(row["fetchedAt"])
    except QihuoError as err:
        raise QihuoUnavailable(err.code, err.message) from None
    except ValueError:
        raise QihuoUnavailable("qihuo_unavailable", "Q 期货缓存时间字段无效，请等待晚间同步恢复。") from None

    if not _is_valid_bundle(raw, moment=qihuo.now()):
        raise QihuoUnavailable("qihuo_unavailable", "Q 期货缓存结构异常，请等待晚间同步自动恢复。")

    stale = bool(err_msg)
    stale_reason = redact_upstream(err_msg) if err_msg else None
    return raw, fetched_at, stale, stale_reason


async def build_board(qihuo: QihuoServices, *, db_path: Path, force_refresh: bool = False) -> dict[str, Any]:
    """读「Q 期货」看板 JSON（纯只读 NB 缓存，零上游请求、零写库、零推送）。"""
    raw, fetched_at, stale, reason = await load_bundle(qihuo, db_path=db_path, force_refresh=force_refresh)
    return project_board(raw, now=qihuo.now(), last_read_at=fetched_at, stale=stale, reason=reason)


__all__ = [
    "QihuoUnavailable",
    "build_board",
    "fetch_and_persist",
    "load_bundle",
    "reset_board_state",
]
