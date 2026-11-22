"""交易日 20:00 后 KX V4 盘后更新轮询检测与仅推 iPhone 的 APNs 通知。

- 仅通过既有只读接口 `/kx/read/<访问码>?format=json` 检测（KX 零回调）。
- 完成判定须同时核对 `as_of == 当日`、`refresh.result == "updated"`、`refresh.as_of == 当日` 与
  `refresh.pool_version == version`。
- 仅向 `devices.platform == 'ios'` 推送（Mac 绝不推送）。
- 区分 APNs 投递结果：
  - `ok=True` -> `sent`
  - `status == 0`（传输超时/异常，投递结果未知）-> 保留 `sending` 占位，绝不自动重发，记入 `unknown`
  - `status in (429, 500, 502, 503)`（APNs 明确暂时拒绝，未送达）-> 记 `failed` 并纳入截止前重试
  - 其他 4xx（如 `400 BadDeviceToken`、`410 Unregistered`）-> 记 `failed`（永久拒绝，不重试）
- 一旦判定事件（ready / partial / failed / timeout），冻结该事件与文案，仅对可重试设备做截止前重试，
  绝不再回源 KX 或把已就绪事件改报成 timeout；每次外呼前后均重读时钟防越过 20:45。
"""
from __future__ import annotations

import fcntl
import logging
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable

from neckline.kx.board import fetch_and_persist
from neckline.kx.services import KxServices
from neckline.kx.source import KxError, redact_upstream
from neckline.morning import db
from neckline.morning.schedule import CST, is_trading_day
from neckline.notify_kinds import CATEGORY_DIGEST, CATEGORY_IMPORTANT

_LOG = logging.getLogger("neckline.kx.watch")

DEFAULT_DEADLINE = time(20, 45, tzinfo=CST)
DEFAULT_POLL_SECONDS = 60.0
_RETRYABLE_APNS_STATUSES = frozenset((429, 500, 502, 503))

PushSender = Callable[..., Any]


def _default_push_sender() -> PushSender | None:
    from neckline.push.apns import send_push
    return send_push


class KxPushNotifier:
    """只推 iPhone（platform='ios'），发前原子占位；区分未知/可重试/永久失败。"""

    def __init__(self, db_path: Path, *, send: PushSender | None, clock: Callable[[], datetime]) -> None:
        self.db_path = db_path
        self.send = send
        self.clock = clock

    def notify(
        self,
        *,
        trade_date: str,
        kind: str,
        board_version: str,
        title: str,
        body: str,
        category: str,
        as_of: str | None = None,
        skip_if_any_today: bool = False,
        only_tokens: set[str] | None = None,
        deadline_dt: datetime | None = None,
    ) -> dict:
        lock_path = Path(str(self.db_path) + ".push.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                return self._notify_locked(
                    trade_date=trade_date,
                    kind=kind,
                    board_version=board_version,
                    title=title,
                    body=body,
                    category=category,
                    as_of=as_of,
                    skip_if_any_today=skip_if_any_today,
                    only_tokens=only_tokens,
                    deadline_dt=deadline_dt,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _notify_locked(
        self,
        *,
        trade_date: str,
        kind: str,
        board_version: str,
        title: str,
        body: str,
        category: str,
        as_of: str | None,
        skip_if_any_today: bool,
        only_tokens: set[str] | None,
        deadline_dt: datetime | None,
    ) -> dict:
        with db.read(self.db_path) as conn:
            tokens = db.device_tokens(conn)  # 仅 platform='ios'，绝不推 macOS
        if only_tokens is not None:
            tokens = [t for t in tokens if t in only_tokens]
        if not tokens:
            return {
                "devices": 0, "sent": 0, "failed": 0, "retryableFailed": 0,
                "permanentFailed": 0, "unknown": 0, "deadlineMissed": 0, "skipped": 0, "retryTokens": [],
            }
        if self.send is None:
            return {
                "devices": len(tokens), "sent": 0, "failed": 0, "retryableFailed": 0,
                "permanentFailed": 0, "unknown": 0, "deadlineMissed": 0, "skipped": len(tokens),
                "retryTokens": [], "reason": "apns_unavailable",
            }

        sent = retryable_failed = permanent_failed = unknown = deadline_missed = skipped = 0
        retry_tokens: list[str] = []
        custom: dict[str, str] = {"target": "kx", "kind": kind, "version": board_version}
        if as_of:
            custom["as_of"] = as_of
        collapse_id = f"kx-{trade_date}-{kind}"[:64]

        for token in tokens:
            with db.write_immediate(self.db_path) as conn:
                # 先恢复本条通知的持久态（避免 skip_if_any_today 把本条的 unknown/permanent 吞成 skipped）
                prior = db._kx_push_existing_status(
                    conn,
                    trade_date=trade_date,
                    kind=kind,
                    board_version=board_version,
                    token=token,
                )
                if prior == "sent":
                    skipped += 1
                    continue
                if prior == "unknown":
                    unknown += 1
                    continue
                if prior == "permanent":
                    permanent_failed += 1
                    continue
                if skip_if_any_today and db.kx_any_push_done(
                    conn,
                    trade_date=trade_date,
                    kinds=("ready", "partial", "failed", "timeout"),
                    token=token,
                ):
                    skipped += 1
                    continue
                now_moment = self.clock()
                if deadline_dt is not None and now_moment.astimezone(CST) >= deadline_dt:
                    deadline_missed += 1
                    continue
                claim = db.claim_kx_push(
                    conn,
                    trade_date=trade_date,
                    kind=kind,
                    board_version=board_version,
                    token=token,
                    at=now_moment,
                )
                if claim != "claimed":
                    if claim == "sent":
                        skipped += 1
                    elif claim == "unknown":
                        unknown += 1
                    elif claim == "permanent":
                        permanent_failed += 1
                    continue

            result = self.send(
                token,
                title,
                body,
                category=category,
                thread_id="kx",
                collapse_id=collapse_id,
                custom=custom,
            )
            ok = bool(getattr(result, "ok", False))
            status_code = int(getattr(result, "status", 0) or 0)
            dispatched = bool(getattr(result, "dispatched", True))
            reason = str(getattr(result, "reason", ""))[:120]

            if ok:
                sent += 1
                with db.write_immediate(self.db_path) as conn:
                    db.log_kx_push(
                        conn, trade_date=trade_date, kind=kind, board_version=board_version,
                        token=token, state="sent", detail=None, at=self.clock(),
                    )
            elif not dispatched:
                # 尚未发起 HTTP 外呼（本地凭据/签名/依赖故障）：标 failed 允许在修复后重试，不永久占 sending
                retryable_failed += 1
                retry_tokens.append(token)
                with db.write_immediate(self.db_path) as conn:
                    db.log_kx_push(
                        conn, trade_date=trade_date, kind=kind, board_version=board_version,
                        token=token, state="failed", detail=f"not_dispatched:{reason}"[:120], at=self.clock(),
                    )
            elif status_code == 0:
                # 已外呼但传输异常/超时：APNs 是否已受理未知，保留 sending 占位，绝不自动重发
                unknown += 1
            elif status_code in _RETRYABLE_APNS_STATUSES:
                retryable_failed += 1
                retry_tokens.append(token)
                with db.write_immediate(self.db_path) as conn:
                    db.log_kx_push(
                        conn, trade_date=trade_date, kind=kind, board_version=board_version,
                        token=token, state="failed", detail=f"retryable:{status_code}:{reason}"[:120], at=self.clock(),
                    )
            else:
                permanent_failed += 1
                with db.write_immediate(self.db_path) as conn:
                    db.log_kx_push(
                        conn, trade_date=trade_date, kind=kind, board_version=board_version,
                        token=token, state="failed", detail=f"permanent:{status_code}:{reason}"[:120], at=self.clock(),
                    )

        total_failed = retryable_failed + permanent_failed + unknown + deadline_missed
        return {
            "devices": len(tokens),
            "sent": sent,
            "failed": total_failed,
            "retryableFailed": retryable_failed,
            "permanentFailed": permanent_failed,
            "unknown": unknown,
            "deadlineMissed": deadline_missed,
            "skipped": skipped,
            "retryTokens": retry_tokens,
        }


async def _deliver_frozen_event(
    notifier: KxPushNotifier,
    kx: KxServices,
    *,
    deadline_dt: datetime,
    poll_seconds: float,
    once: bool,
    trade_date: str,
    kind: str,
    board_version: str,
    title: str,
    body: str,
    category: str,
    as_of: str | None = None,
    skip_if_any_today: bool = False,
    enforce_deadline: bool = True,
) -> dict:
    """冻结已判定的事件与文案，仅对明确可重试的 APNs 失败设备在截止前重试，绝不回退重拉 KX。"""
    active_deadline = deadline_dt if enforce_deadline else None
    first = notifier.notify(
        trade_date=trade_date,
        kind=kind,
        board_version=board_version,
        title=title,
        body=body,
        category=category,
        as_of=as_of,
        skip_if_any_today=skip_if_any_today,
        deadline_dt=active_deadline,
    )
    total_devices = first["devices"]
    sent = first["sent"]
    skipped = first["skipped"]
    permanent_failed = first["permanentFailed"]
    unknown = first["unknown"]
    deadline_missed = first["deadlineMissed"]
    pending_tokens = list(first["retryTokens"])

    while pending_tokens and not once:
        now_cst = kx.now().astimezone(CST)
        remaining = (deadline_dt - now_cst).total_seconds()
        if remaining <= 0:
            break
        await kx.sleep(min(poll_seconds, remaining))
        if kx.now().astimezone(CST) >= deadline_dt:
            break
        attempt = notifier.notify(
            trade_date=trade_date,
            kind=kind,
            board_version=board_version,
            title=title,
            body=body,
            category=category,
            as_of=as_of,
            skip_if_any_today=skip_if_any_today,
            only_tokens=set(pending_tokens),
            deadline_dt=active_deadline,
        )
        sent += attempt["sent"]
        skipped += attempt["skipped"]
        permanent_failed += attempt["permanentFailed"]
        unknown += attempt["unknown"]
        deadline_missed += attempt["deadlineMissed"]
        pending_tokens = list(attempt["retryTokens"])

    remaining_retryable = len(pending_tokens)
    return {
        "devices": total_devices,
        "sent": sent,
        "failed": remaining_retryable + permanent_failed + unknown + deadline_missed,
        "retryableFailed": remaining_retryable,
        "permanentFailed": permanent_failed,
        "unknown": unknown,
        "deadlineMissed": deadline_missed,
        "skipped": skipped,
    }


def _record_error(db_path: Path, message: str, at: datetime) -> None:
    try:
        with db.write_immediate(db_path) as conn:
            db.record_kx_board_error(conn, error=message, at=at)
    except Exception:  # noqa: BLE001
        pass


def _record_timeout(db_path: Path, trade_date: str, notice: str, at: datetime) -> tuple[bool, dict | None]:
    try:
        with db.write_immediate(db_path) as conn:
            return db.record_kx_board_timeout(conn, trade_date=trade_date, notice=notice, at=at)
    except Exception:  # noqa: BLE001
        return True, None


def _fallback_summary(body: dict) -> str:
    as_of = str(body.get("as_of") or "")
    mmdd = as_of[5:] if len(as_of) >= 10 else as_of
    cap = body.get("capacity") if isinstance(body.get("capacity"), dict) else {}
    used = cap.get("used", len(body.get("items") or []))
    cap_max = cap.get("max", 25)
    if body.get("deepseek_status") != "ok":
        return f"{mmdd} 盘后事实已更新｜DeepSeek 判断未完成（沿用旧结构 {used}/{cap_max}）"
    return f"{mmdd} 盘后整理完成｜候选 {used}/{cap_max}"


def _is_confirmed_update(body: dict, today_iso: str) -> bool:
    """严格核对上游完成回执：as_of == 当日 且 refresh.result == 'updated' 且 pool_version 匹配。"""
    if body.get("as_of") != today_iso:
        return False
    refresh = body.get("refresh")
    if not isinstance(refresh, dict):
        return False
    if refresh.get("result") != "updated":
        return False
    if refresh.get("as_of") != today_iso:
        return False
    if refresh.get("pool_version") != body.get("version"):
        return False
    return True


async def run_watch(
    kx: KxServices,
    *,
    db_path: Path,
    target_date: date | None = None,
    deadline_clock: time = DEFAULT_DEADLINE,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    once: bool = False,
    dry_run: bool = False,
    send_push: PushSender | None = None,
) -> dict:
    """交易日 20:00 后轮询 KX V4 并按结果落库与推送（仅 iPhone）。"""
    now_cst = kx.now().astimezone(CST)
    day = target_date or now_cst.date()
    today_iso = day.isoformat()

    if not is_trading_day(day):
        return {"status": "skipped", "date": today_iso, "reason": "non_trading_day"}

    deadline_dt = datetime.combine(day, deadline_clock)
    sender = None if dry_run else (send_push if send_push is not None else _default_push_sender())
    notifier = KxPushNotifier(db_path, send=sender, clock=kx.now)

    last_error_text: str | None = None
    last_seen_as_of: str | None = None
    attempts = 0

    async def _finish_confirmed(body: dict, *, enforce_deadline: bool) -> dict:
        version = str(body["version"])
        is_ok = body.get("deepseek_status") == "ok"
        kind = "ready" if is_ok else "partial"
        title = "KX 盘后看板已更新" if is_ok else "KX 盘后事实已更新（判断未完成）"
        summary = str(body.get("push_summary") or "").strip() or _fallback_summary(body)
        summary = redact_upstream(summary)[:180]
        push_stats = await _deliver_frozen_event(
            notifier, kx, deadline_dt=deadline_dt, poll_seconds=poll_seconds, once=once,
            trade_date=today_iso, kind=kind, board_version=version,
            title=title, body=summary, category=CATEGORY_DIGEST, as_of=today_iso,
            enforce_deadline=enforce_deadline,
        )
        return {
            "status": "push_failed" if push_stats.get("failed", 0) > 0 else kind,
            "boardStatus": kind,
            "date": today_iso,
            "version": version,
            "deepseekStatus": body.get("deepseek_status"),
            "attempts": attempts,
            "push": push_stats,
        }

    async def _finish_rejected_alert(winner: dict | None) -> dict:
        if winner is not None and _is_confirmed_update(winner["raw"], today_iso):
            return await _finish_confirmed(winner["raw"], enforce_deadline=True)
        return {
            "status": "superseded",
            "date": today_iso,
            "asOf": winner["asOf"] if winner is not None else last_seen_as_of,
            "attempts": attempts,
        }

    async def _finish_timeout(*, now_cst: datetime, reason: str | None = None, timeout_msg: str) -> dict:
        accepted, winner = _record_timeout(db_path, today_iso, timeout_msg, now_cst)
        if not accepted:
            return await _finish_rejected_alert(winner)
        push_stats = await _deliver_frozen_event(
            notifier, kx, deadline_dt=deadline_dt, poll_seconds=poll_seconds, once=True,
            trade_date=today_iso, kind="timeout", board_version=f"timeout-{today_iso}",
            title="KX 盘后整理超时未更新", body=timeout_msg, category=CATEGORY_IMPORTANT,
            as_of=last_seen_as_of, skip_if_any_today=True, enforce_deadline=False,
        )
        out: dict[str, Any] = {
            "status": "timeout",
            "date": today_iso,
            "message": timeout_msg,
            "attempts": attempts,
            "push": push_stats,
        }
        if reason is not None:
            out["reason"] = reason
        else:
            out["asOf"] = last_seen_as_of
        return out

    while True:
        attempts += 1
        try:
            body, _projected, _moment = await fetch_and_persist(kx, db_path=db_path)
        except KxError as error:
            now_cst = kx.now().astimezone(CST)
            last_error_text = redact_upstream(error.message)
            _record_error(db_path, last_error_text, now_cst)
            if error.code == "kx_unsupported_schema":
                push_stats = await _deliver_frozen_event(
                    notifier, kx, deadline_dt=deadline_dt, poll_seconds=poll_seconds, once=once,
                    trade_date=today_iso, kind="failed", board_version=f"schema-{today_iso}",
                    title="KX 盘后看板协议变更", body=last_error_text, category=CATEGORY_IMPORTANT,
                    skip_if_any_today=True,
                )
                return {
                    "status": "failed",
                    "date": today_iso,
                    "reason": error.code,
                    "message": last_error_text,
                    "attempts": attempts,
                    "push": push_stats,
                }
            if once or now_cst >= deadline_dt:
                if now_cst >= deadline_dt:
                    timeout_msg = f"截至 {deadline_dt.strftime('%H:%M')} 未能读取今日盘后看板：{last_error_text}"
                    return await _finish_timeout(now_cst=now_cst, reason=error.code, timeout_msg=timeout_msg)
                return {
                    "status": "error",
                    "date": today_iso,
                    "reason": error.code,
                    "message": last_error_text,
                    "attempts": attempts,
                }
            remaining = (deadline_dt - now_cst).total_seconds()
            if remaining > 0:
                await kx.sleep(min(poll_seconds, remaining))
            now_after_sleep = kx.now().astimezone(CST)
            if now_after_sleep >= deadline_dt:
                timeout_msg = f"截至 {deadline_dt.strftime('%H:%M')} 未能读取今日盘后看板：{last_error_text}"
                return await _finish_timeout(now_cst=now_after_sleep, reason=error.code, timeout_msg=timeout_msg)
            continue

        now_cst = kx.now().astimezone(CST)
        last_seen_as_of = body.get("as_of")
        refresh = body.get("refresh") if isinstance(body.get("refresh"), dict) else None

        # 1. 上游明确报告今日整理失败
        if refresh is not None and refresh.get("date") == today_iso and refresh.get("result") == "failed":
            raw_notice = refresh.get("notice") or refresh.get("message") or "今日 KX 盘后整理失败，看板沿用上一版。"
            fail_msg = redact_upstream(str(raw_notice))
            accepted, winner = _record_timeout(db_path, today_iso, fail_msg, now_cst)
            if not accepted:
                return await _finish_rejected_alert(winner)
            push_stats = await _deliver_frozen_event(
                notifier, kx, deadline_dt=deadline_dt, poll_seconds=poll_seconds, once=once,
                trade_date=today_iso, kind="failed", board_version=f"failed-{today_iso}",
                title="KX 盘后整理失败", body=fail_msg, category=CATEGORY_IMPORTANT,
                as_of=last_seen_as_of, skip_if_any_today=True,
            )
            return {
                "status": "failed",
                "date": today_iso,
                "reason": "upstream_refresh_failed",
                "message": fail_msg,
                "attempts": attempts,
                "push": push_stats,
            }

        # 2. 检测到上游确认完成的今日新盘后快照（正常完成或 DeepSeek 降级刷新事实）
        if _is_confirmed_update(body, today_iso):
            return await _finish_confirmed(body, enforce_deadline=True)

        # 3. 尚未见到今日确认完成的更新（旧版或 status.json 尚未写齐）
        if now_cst >= deadline_dt:
            prev_text = f"仍展示上一版（{last_seen_as_of}）" if last_seen_as_of else "尚未读到有效版本"
            timeout_msg = f"截至 {deadline_dt.strftime('%H:%M')} 未检测到今日 KX 盘后新版本，{prev_text}。"
            return await _finish_timeout(now_cst=now_cst, timeout_msg=timeout_msg)

        if once:
            return {
                "status": "pending",
                "date": today_iso,
                "asOf": last_seen_as_of,
                "attempts": attempts,
            }

        remaining = (deadline_dt - now_cst).total_seconds()
        if remaining > 0:
            await kx.sleep(min(poll_seconds, remaining))
        now_after_sleep = kx.now().astimezone(CST)
        if now_after_sleep >= deadline_dt:
            prev_text = f"仍展示上一版（{last_seen_as_of}）" if last_seen_as_of else "尚未读到有效版本"
            timeout_msg = f"截至 {deadline_dt.strftime('%H:%M')} 未检测到今日 KX 盘后新版本，{prev_text}。"
            return await _finish_timeout(now_cst=now_after_sleep, timeout_msg=timeout_msg)


__all__ = [
    "DEFAULT_DEADLINE",
    "DEFAULT_POLL_SECONDS",
    "KxPushNotifier",
    "run_watch",
]
