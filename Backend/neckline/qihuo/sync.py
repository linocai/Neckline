"""交易日晚间 20:15～20:35 温和静默同步 Qihuo V2 只读 JSON（完全静默，绝不发任何 APNs 推送）。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from neckline.morning import db
from neckline.morning.schedule import CST, is_trading_day
from neckline.qihuo.board import fetch_and_persist
from neckline.qihuo.project import _VALID_COLLECT_STATUS
from neckline.qihuo.services import QihuoServices
from neckline.qihuo.source import QihuoError, redact_upstream

DEFAULT_WINDOW_START = time(20, 15, tzinfo=CST)
DEFAULT_DEADLINE = time(20, 35, tzinfo=CST)
DEFAULT_POLL_SECONDS = 120.0


def _normalize_sync_fields(body: dict[str, Any]) -> tuple[str | None, str | None, str]:
    """与看板投影共用同一口径规范化 as_of_date、latest_collect.trade_date 与 latest_collect.status。"""
    raw_as_of = body.get("as_of_date")
    as_of = raw_as_of.strip() if isinstance(raw_as_of, str) and raw_as_of.strip() else None
    collect = body.get("latest_collect") if isinstance(body.get("latest_collect"), dict) else None
    raw_c_date = collect.get("trade_date") if collect else None
    c_date = raw_c_date.strip() if isinstance(raw_c_date, str) and raw_c_date.strip() else None
    raw_c_status = collect.get("status") if collect else None
    if isinstance(raw_c_status, str) and raw_c_status.strip() in _VALID_COLLECT_STATUS:
        c_status = raw_c_status.strip()
    else:
        c_status = "unknown"
    return as_of, c_date, c_status


def _is_today_settled(body: dict[str, Any], today_iso: str) -> tuple[bool, str]:
    """判断 Qihuo 当日采集是否已落定（ok/partial/failed），与文案共用规范化字段。"""
    as_of, c_date, c_status = _normalize_sync_fields(body)
    if c_date == today_iso:
        if c_status == "failed":
            return True, "failed"
        if c_status in ("ok", "partial") and as_of == today_iso:
            return True, c_status
    return False, "pending"


_PENULTIMATE_LEAD = timedelta(seconds=15)
_FINAL_CHECK_LEAD = timedelta(seconds=2)


def _record_error(db_path: Path, *, reason: str, message: str, at: datetime, retries: int = 1) -> bool:
    """持久化同步错误/协议阻断；返回是否成功落库。"""
    for _ in range(max(1, retries)):
        try:
            with db.write_immediate(db_path) as conn:
                db.record_qihuo_bundle_error(conn, reason=reason, error=message, at=at)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _next_sleep_seconds(now_cst: datetime, deadline_dt: datetime, poll_seconds: float) -> tuple[float, bool]:
    """计算下一轮睡眠秒数：先在截止前 15s（20:34:45）做倒数第二次检查，若仍未更新则在截止前 2s（20:34:58）做末次有界检查，
    既不漏掉 20:34 最后 15 秒发布的包，也给调度抖动留足安全余量，绝不越过硬截止。"""
    remaining = (deadline_dt - now_cst).total_seconds()
    if remaining <= 0:
        return 0.0, True
    penultimate = deadline_dt - _PENULTIMATE_LEAD
    if penultimate > now_cst:
        to_pen = (penultimate - now_cst).total_seconds()
        if poll_seconds < to_pen:
            return poll_seconds, False
        return to_pen, False
    final_target = deadline_dt - _FINAL_CHECK_LEAD
    if final_target > now_cst:
        to_final = (final_target - now_cst).total_seconds()
        if poll_seconds < to_final:
            return poll_seconds, False
        return to_final, True
    return 0.0, True


async def run_sync(
    qihuo: QihuoServices,
    *,
    db_path: Path,
    target_date: date | None = None,
    window_start: time = DEFAULT_WINDOW_START,
    deadline_clock: time = DEFAULT_DEADLINE,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    once: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """交易日晚间静默拉取 Qihuo 只读 JSON 并落库缓存（零推送、无 APNs 依赖）。

    默认严格校验交易日与 20:15～20:35 CST 窗口（每次请求前校验）；
    仅在显式传 `force=True`（如部署初次建缓存）时跳过窗口限制。
    """
    now_cst = qihuo.now().astimezone(CST)
    day = target_date or now_cst.date()
    today_iso = day.isoformat()

    if not force:
        if not is_trading_day(day):
            return {"status": "skipped", "date": today_iso, "reason": "non_trading_day"}
        # 非 force 模式只允许收窄窗口，绝不允许通过 --deadline 放宽 20:15～20:35 硬截止
        eff_start = max(window_start, DEFAULT_WINDOW_START)
        eff_deadline = min(deadline_clock, DEFAULT_DEADLINE)
        start_dt = datetime.combine(day, eff_start)
        deadline_dt = datetime.combine(day, eff_deadline)
        if now_cst < start_dt or now_cst > deadline_dt:
            return {"status": "skipped", "date": today_iso, "reason": "outside_window"}
    else:
        start_dt = datetime.combine(day, window_start)
        deadline_dt = datetime.combine(day, deadline_clock)

    attempts = 0
    final_check_done = False
    last_error: str | None = None
    last_as_of: str | None = None
    last_version: str | None = None
    last_collect_date: str | None = None
    last_collect_status: str | None = None
    last_observed_at: datetime | None = None

    def _pending_message(observed_at: datetime | None, fallback_at: datetime, *, single_check: bool) -> str:
        prefix = "单次检查" if single_check else "晚间同步"
        if observed_at is None:
            return f"今日（{today_iso}）{prefix}未取得有效上游响应（止于 {fallback_at.strftime('%H:%M:%S')}），当前展示既有缓存。"
        ts = observed_at.strftime("%H:%M:%S")
        if last_as_of == today_iso:
            if last_collect_date == today_iso:
                if last_collect_status == "running":
                    return f"{prefix}（截至 {ts}）已收到今日（{today_iso}）快照，但上游采集仍在进行中（running）。"
                return f"{prefix}（截至 {ts}）已收到今日（{today_iso}）快照，但今日采集状态尚未落定（{last_collect_status or 'unknown'}）。"
            return f"{prefix}（截至 {ts}）已收到今日（{today_iso}）快照，但上游今日采集记录尚未更新。"
        if last_as_of is None:
            return f"{prefix}（截至 {ts}）尚未检测到今日（{today_iso}）新快照，当前缓存未提供快照日期。"
        if last_as_of < today_iso:
            return f"{prefix}（截至 {ts}）尚未检测到今日（{today_iso}）新快照，当前展示上一期缓存（{last_as_of}）。"
        return f"{prefix}（截至 {ts}）当前缓存快照日期为 {last_as_of}，今日（{today_iso}）采集状态尚未落定。"

    def _within_window() -> bool:
        if force:
            return True
        cur = qihuo.now().astimezone(CST)
        return is_trading_day(day) and start_dt <= cur <= deadline_dt

    while True:
        if not force:
            now_cst = qihuo.now().astimezone(CST)
            if not is_trading_day(day):
                return {"status": "skipped", "date": today_iso, "reason": "non_trading_day", "attempts": attempts}
            if now_cst < start_dt or now_cst > deadline_dt:
                if attempts == 0:
                    return {"status": "skipped", "date": today_iso, "reason": "outside_window", "attempts": 0}
                break

        attempts += 1
        observed_holder: list[datetime] = []
        try:
            body, _fetched_at, not_modified = await fetch_and_persist(
                qihuo, db_path=db_path, allow_fetch=_within_window, observed_at_out=observed_holder,
            )
        except QihuoError as error:
            if error.code == "qihuo_outside_window":
                if attempts == 1:
                    return {"status": "skipped", "date": today_iso, "reason": "outside_window", "attempts": 0}
                break
            if error.code == "qihuo_stale_overtaken":
                # 并发写入判定本次 v3 结果已过期（较新请求已落下协议阻断）：绝不重复写入或刷新阻断代次
                return {
                    "status": "error",
                    "date": today_iso,
                    "reason": "qihuo_unsupported_schema",
                    "message": redact_upstream(error.message),
                    "attempts": attempts,
                }
            now_cst = qihuo.now().astimezone(CST)
            last_error = redact_upstream(error.message)
            is_schema = error.code == "qihuo_unsupported_schema"
            persisted = _record_error(
                db_path,
                reason=error.code,
                message=last_error,
                at=now_cst,
                retries=3 if is_schema else 1,
            )
            if is_schema:
                if not persisted:
                    return {
                        "status": "error",
                        "date": today_iso,
                        "reason": "qihuo_schema_block_persist_failed",
                        "message": f"检测到上游协议不兼容，但阻断状态未成功落库：{last_error}",
                        "attempts": attempts,
                    }
                return {
                    "status": "error",
                    "date": today_iso,
                    "reason": error.code,
                    "message": last_error,
                    "attempts": attempts,
                }
            if once or force or final_check_done or now_cst >= deadline_dt:
                return {
                    "status": "error",
                    "date": today_iso,
                    "reason": error.code,
                    "message": last_error,
                    "attempts": attempts,
                }
            wait_sec, will_be_final = _next_sleep_seconds(now_cst, deadline_dt, poll_seconds)
            if wait_sec > 0:
                await qihuo.sleep(wait_sec)
            if will_be_final:
                final_check_done = True
            continue

        now_cst = qihuo.now().astimezone(CST)
        # 观测时刻严格采用收到上游有效响应时的时刻，不受后续落库耗时推迟
        last_observed_at = observed_holder[0].astimezone(CST) if observed_holder else now_cst
        last_as_of, last_collect_date, last_collect_status = _normalize_sync_fields(body)
        last_version = body.get("version")
        settled, collect_status = _is_today_settled(body, today_iso)
        if settled:
            return {
                "status": "synced",
                "collectStatus": collect_status,
                "date": today_iso,
                "asOfDate": last_as_of,
                "version": last_version,
                "notModified": not_modified,
                "attempts": attempts,
                "observedAt": last_observed_at.isoformat(timespec="seconds"),
            }

        if once or force:
            return {
                "status": "pending",
                "date": today_iso,
                "asOfDate": last_as_of,
                "version": last_version,
                "notModified": not_modified,
                "attempts": attempts,
                "observedAt": last_observed_at.isoformat(timespec="seconds"),
                "note": _pending_message(last_observed_at, now_cst, single_check=True),
            }

        if final_check_done or now_cst >= deadline_dt:
            gap_msg = _pending_message(last_observed_at, now_cst, single_check=False)
            _record_error(db_path, reason="qihuo_sync_pending", message=gap_msg, at=now_cst)
            return {
                "status": "pending",
                "date": today_iso,
                "asOfDate": last_as_of,
                "version": last_version,
                "notModified": not_modified,
                "attempts": attempts,
                "gap": gap_msg,
            }

        wait_sec, will_be_final = _next_sleep_seconds(now_cst, deadline_dt, poll_seconds)
        if wait_sec > 0:
            await qihuo.sleep(wait_sec)
        if will_be_final:
            final_check_done = True

    end_cst = qihuo.now().astimezone(CST)
    gap_msg = _pending_message(last_observed_at, end_cst, single_check=False)
    _record_error(db_path, reason="qihuo_sync_pending", message=gap_msg, at=end_cst)
    return {
        "status": "pending",
        "date": today_iso,
        "asOfDate": last_as_of,
        "version": last_version,
        "attempts": attempts,
        "gap": gap_msg,
    }


__all__ = ["DEFAULT_DEADLINE", "DEFAULT_POLL_SECONDS", "DEFAULT_WINDOW_START", "run_sync"]
