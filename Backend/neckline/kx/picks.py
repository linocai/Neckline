"""推荐接收：严格校验请求体、核对名单、按交易日规则归属、按 requestId 幂等入库。

返回 (HTTP 状态, 响应体)，响应体与 archive/v4.1.0_execution.md §3.3 的契约一致；日志只记代码和时间，不记密钥。
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from neckline.kx import store
from neckline.kx.days import pick_trade_date
from neckline.kx.services import KxServices
from neckline.morning import db
from neckline.morning.schedule import CalendarGap

_LOG = logging.getLogger("neckline.kx")

MAX_BODY = 16 * 1024
MAX_CODES = 20
FUTURE_SKEW = timedelta(minutes=5)   # KX 与本机的时钟差
MAX_AGE = timedelta(hours=24)        # KX 转发是即时的，一天前的 pickedAt 只会是错误
_FIELDS = frozenset({"requestId", "pickedAt", "stockCodes"})
_CODE = re.compile(r"[0-9]{6}")

Reply = tuple[int, dict[str, Any]]


class PickInvalid(ValueError):
    pass


@dataclass(frozen=True)
class PickRequest:
    request_id: str
    picked_at: datetime        # UTC，截到秒
    codes: tuple[str, ...]


def parse_request(payload: object) -> PickRequest:
    if not isinstance(payload, dict):
        raise PickInvalid("请求体必须是 JSON 对象")
    extra = sorted(set(payload) - _FIELDS)
    missing = sorted(_FIELDS - set(payload))
    if extra:
        raise PickInvalid("不认识的字段：" + "、".join(extra))
    if missing:
        raise PickInvalid("缺少字段：" + "、".join(missing))
    request_id = payload["requestId"]
    try:
        canonical = str(uuid.UUID(request_id)) if isinstance(request_id, str) and len(request_id) == 36 else None
    except ValueError:
        canonical = None
    if canonical is None or canonical != request_id.lower():
        raise PickInvalid("requestId 必须是带连字符的 UUID")
    raw_time = payload["pickedAt"]
    try:
        moment = datetime.fromisoformat(raw_time) if isinstance(raw_time, str) else None
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None or moment.utcoffset() is None:
        raise PickInvalid("pickedAt 必须是带时区的 ISO 8601 时间")
    codes = payload["stockCodes"]
    if not isinstance(codes, list) or not 1 <= len(codes) <= MAX_CODES:
        raise PickInvalid(f"stockCodes 必须是 1 到 {MAX_CODES} 个代码的列表")
    if any(not isinstance(code, str) or not _CODE.fullmatch(code) for code in codes):
        raise PickInvalid("stockCodes 里每个代码必须是 6 位数字")
    if len(set(codes)) != len(codes):
        raise PickInvalid("stockCodes 里的代码不能重复")
    return PickRequest(canonical, moment.astimezone(timezone.utc).replace(microsecond=0), tuple(codes))


def _recorded(status: str, request_id: str, trade_date, codes) -> Reply:
    return 200, {"status": status, "requestId": request_id, "tradeDate": trade_date.isoformat(),
                 "stockCodes": list(codes)}


def _existing_reply(existing: store.PickRecord, request: PickRequest) -> Reply:
    if existing.codes == request.codes and existing.picked_at == request.picked_at:
        return _recorded("duplicate", request.request_id, existing.trade_date, existing.codes)
    return 400, {"status": "invalid", "reason": "requestId 已经用于内容不同的推荐"}


async def record(kx: KxServices, path: Path, request: PickRequest) -> Reply:
    now = kx.now()
    try:
        with db.read(path) as conn:
            existing = store.find_request(conn, request.request_id)
        if existing is not None:
            return _existing_reply(existing, request)
        picked = request.picked_at
        if picked > now.astimezone(timezone.utc) + FUTURE_SKEW:
            return 400, {"status": "invalid", "reason": "pickedAt 在未来，和服务器时间对不上"}
        if picked < now.astimezone(timezone.utc) - MAX_AGE:
            return 400, {"status": "invalid", "reason": "pickedAt 早于一天前，和服务器时间对不上"}
        pool = await kx.pool.ensure(path, now)
        if pool.snapshot is None:
            return 503, {"status": "pool_unverified",
                         "reason": f"KX 名单还没有成功同步过，不能核对推荐：{pool.reason}"}
        members = {member.code for member in pool.snapshot.members}
        outside = [code for code in request.codes if code not in members]
        if outside:
            _LOG.info("kx picks rejected out_of_pool request=%s codes=%s", request.request_id, ",".join(outside))
            hint = "；名单未能与 KX 核对，按上一份名单判断" if pool.stale else ""
            return 409, {"status": "out_of_pool", "reason": "这些代码不在当前 KX 名单里，整批没有记录：" +
                         "、".join(outside) + hint, "codes": outside}
        try:
            trade_date = pick_trade_date(picked)
        except CalendarGap as error:
            return 503, {"status": "calendar_gap", "reason": f"交易日历没有覆盖这一天，不能归属交易日：{error}"}
        with db.write(path) as conn:
            conn.execute("BEGIN IMMEDIATE")  # 同一个 requestId 并发到达时，后到的等前一个提交后再查一次
            again = store.find_request(conn, request.request_id)
            if again is not None:
                return _existing_reply(again, request)
            store.insert_picks(conn, request_id=request.request_id, codes=request.codes, picked_at=picked,
                               trade_date=trade_date, received_at=now)
        _LOG.info("kx picks recorded request=%s trade_date=%s picked_at=%s codes=%s", request.request_id,
                  trade_date.isoformat(), picked.isoformat(), ",".join(request.codes))
        return _recorded("recorded", request.request_id, trade_date, request.codes)
    except db.NotReady:
        return 503, {"status": "db_not_ready", "reason": "库还没准备好。"}
