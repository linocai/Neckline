"""KX V4（schema_version 5）严格校验与看板投影。

只认 schema_version == 5；遇到其他版本明确报 kx_unsupported_schema，绝不按旧逻辑硬读。
客观程序事实与 DeepSeek 主观判断保持分离；缺失数值保留 None，绝不当成 0。
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date, datetime

from neckline.kx.source import KxError, redact_upstream
from neckline.morning.schedule import CST

SCHEMA_VERSION = 5
ZONES = ("leader", "hot", "elim")
_ZONE_ORDER = {zone: idx for idx, zone in enumerate(ZONES)}
_STATUSES = frozenset(("ok", "partial"))
_DEEPSEEK_STATUSES = frozenset(("ok", "failed", "skipped"))
_GENERATED_BY = frozenset(("deepseek", "program", "engineer"))
_CHANGE_BADGES = frozenset(("up", "new", "return", "elim_new", "elim_out", "stay"))
_RHYTHM_KINDS = frozenset(("surge", "pullback", "divergence", "washout", "dead"))
_BADGE_KINDS = frozenset(("hot", "quant", "lasa", "inst", "north"))
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE_RE = re.compile(r"^\d{6}$")


def _is_int(value: object) -> bool:
    return type(value) is int


def _opt_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _opt_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        return num if math.isfinite(num) else None
    return None


def _req_str(mapping: Mapping, key: str, *, allow_empty: bool = False) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise KxError("kx_bad_board", f"KX 看板缺少字段 {key}，没有采用。")
    text = value.strip()
    if not allow_empty and not text:
        raise KxError("kx_bad_board", f"KX 看板字段 {key} 为空，没有采用。")
    return text


def _opt_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _req_day(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DAY_RE.fullmatch(value):
        raise KxError("kx_bad_board", f"KX 看板日期字段 {field} 格式无效，没有采用。")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise KxError("kx_bad_board", f"KX 看板日期字段 {field} 无效，没有采用。") from None
    return value


def _req_iso_cst(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KxError("kx_bad_board", f"KX 看板时间字段 {field} 缺失，没有采用。")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise KxError("kx_bad_board", f"KX 看板时间字段 {field} 格式无效，没有采用。") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CST)
    return parsed.astimezone(CST).isoformat(timespec="seconds")


def _opt_iso_cst(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CST)
    return parsed.astimezone(CST).isoformat(timespec="seconds")


def require_overview(body: object) -> dict:
    """严格校验 KX V4 schema_version == 5 顶层结构。未知版本明确报错不硬解。"""
    if not isinstance(body, dict):
        raise KxError("kx_bad_board", "KX 返回的内容不是有效的看板对象，没有采用。")
    sv = body.get("schema_version")
    if not _is_int(sv) or sv != SCHEMA_VERSION:
        shown = "缺失" if sv is None else repr(sv)
        raise KxError(
            "kx_unsupported_schema",
            f"KX 看板协议版本暂不支持（收到 schema_version={shown}，需要 {SCHEMA_VERSION}），未强行解析。",
        )
    status = body.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        raise KxError("kx_bad_board", "KX 看板状态字段无效，没有采用。")
    _req_str(body, "version")
    _req_day(body.get("as_of"), "as_of")
    _req_iso_cst(body.get("generated_at"), "generated_at")
    ds_status = body.get("deepseek_status")
    if not isinstance(ds_status, str) or ds_status not in _DEEPSEEK_STATUSES:
        raise KxError("kx_bad_board", "KX 看板 DeepSeek 状态无效，没有采用。")
    if not isinstance(body.get("capacity"), dict):
        raise KxError("kx_bad_board", "KX 看板缺少容量信息，没有采用。")
    sentiment = body.get("sentiment")
    if not isinstance(sentiment, dict) or not isinstance(sentiment.get("days"), list) or not sentiment["days"]:
        raise KxError("kx_bad_board", "KX 看板缺少主板情绪事实，没有采用。")
    if not isinstance(body.get("items"), list):
        raise KxError("kx_bad_board", "KX 看板缺少候选股列表，没有采用。")
    return body


def _project_refresh(raw: object) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    result = _opt_str(raw.get("result"))
    if result not in {"updated", "failed", "skipped", None}:
        result = None
    notice = _opt_str(raw.get("notice"))
    message = _opt_str(raw.get("message"))
    return {
        "date": _opt_str(raw.get("date")),
        "result": result,
        "poolVersion": _opt_str(raw.get("pool_version")),
        "asOf": _opt_str(raw.get("as_of")),
        "deepseekStatus": _opt_str(raw.get("deepseek_status")),
        "finishedAt": _opt_iso_cst(raw.get("finished_at")),
        "message": redact_upstream(message) if message else None,
        "notice": redact_upstream(notice) if notice else None,
        "elapsedSeconds": _opt_float(raw.get("elapsed_seconds")),
    }


def _project_stock_ref(raw: object) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    code = _opt_str(raw.get("code"))
    name = _opt_str(raw.get("name"))
    if not code or not name:
        return None
    return {
        "code": code,
        "name": name,
        "boardType": _opt_str(raw.get("board_type")) or "换手板",
    }


def _project_sentiment(raw: Mapping) -> dict:
    days_out: list[dict] = []
    for entry in raw.get("days") or ():
        if not isinstance(entry, Mapping):
            raise KxError("kx_bad_board", "KX 情绪日数据格式无效，没有采用。")
        highest_out = [
            ref for ref in (_project_stock_ref(item) for item in (entry.get("highest") or ()))
            if ref is not None
        ]
        ladder_out: dict[str, list[dict]] = {}
        raw_ladder = entry.get("ladder")
        if isinstance(raw_ladder, Mapping):
            for level, rows in raw_ladder.items():
                if not isinstance(level, str) or not isinstance(rows, list):
                    continue
                refs = [ref for ref in (_project_stock_ref(r) for r in rows) if ref is not None]
                if refs:
                    ladder_out[level] = refs
        days_out.append({
            "date": _req_day(entry.get("date"), "sentiment.days.date"),
            "zt": _opt_int(entry.get("zt")),
            "zb": _opt_int(entry.get("zb")),
            "zbRate": _opt_float(entry.get("zb_rate")),
            "dt": _opt_int(entry.get("dt")),
            "maxLbc": _opt_int(entry.get("max_lbc")),
            "highest": highest_out,
            "ladder": ladder_out,
            "yZtN": _opt_int(entry.get("y_zt_n")),
            "yCont": _opt_int(entry.get("y_cont")),
            "yOpenMean": _opt_float(entry.get("y_open_mean")),
            "yCloseMean": _opt_float(entry.get("y_close_mean")),
            "yCloseMed": _opt_float(entry.get("y_close_med")),
        })
    if not days_out:
        raise KxError("kx_bad_board", "KX 情绪日数据为空，没有采用。")

    judgment_out = None
    raw_judgment = raw.get("judgment")
    if isinstance(raw_judgment, Mapping):
        stage = _opt_str(raw_judgment.get("stage"))
        market_note = _opt_str(raw_judgment.get("market_note"))
        if stage or market_note:
            judgment_out = {
                "stage": stage or "",
                "marketNote": market_note or "",
            }
    return {"days": days_out, "judgment": judgment_out}


def _project_seat(raw: object) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    org = _opt_str(raw.get("org")) or _opt_str(raw.get("full_org"))
    if not org:
        return None
    badges_out: list[dict] = []
    for badge in raw.get("badges") or ():
        if not isinstance(badge, Mapping):
            continue
        text = _opt_str(badge.get("text"))
        kind = _opt_str(badge.get("kind"))
        if text and kind in _BADGE_KINDS:
            badges_out.append({"text": text, "kind": kind})
    return {
        "org": org,
        "fullOrg": _opt_str(raw.get("full_org")) or org,
        "buyW": _opt_float(raw.get("buy_w")),
        "sellW": _opt_float(raw.get("sell_w")),
        "netW": _opt_float(raw.get("net_w")),
        "badges": badges_out,
    }


def _project_lhb(raw: object) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise KxError("kx_bad_board", "KX 龙虎榜字段格式无效，没有采用。")
    reasons = [r.strip() for r in (raw.get("reasons") or ()) if isinstance(r, str) and r.strip()]
    buys = [s for s in (_project_seat(row) for row in (raw.get("buys") or ())) if s is not None]
    sells = [s for s in (_project_seat(row) for row in (raw.get("sells") or ())) if s is not None]
    return {
        "date": _req_day(raw.get("date"), "facts.lhb.date"),
        "reasons": reasons,
        "netW": _opt_float(raw.get("net_w")),
        "buyTotalW": _opt_float(raw.get("buy_total_w")),
        "sellTotalW": _opt_float(raw.get("sell_total_w")),
        "buys": buys,
        "sells": sells,
    }


def _project_item(raw: object, seen_codes: set[str]) -> dict:
    if not isinstance(raw, Mapping):
        raise KxError("kx_bad_board", "KX 候选条目不是对象，没有采用。")
    code = _req_str(raw, "code")
    if not _CODE_RE.fullmatch(code) or code in seen_codes:
        raise KxError("kx_bad_board", f"KX 候选代码无效或重复：{code}。")
    seen_codes.add(code)
    name = _req_str(raw, "name")
    industry = _opt_str(raw.get("industry")) or "其他"
    zone = raw.get("zone")
    if not isinstance(zone, str) or zone not in ZONES:
        raise KxError("kx_bad_board", f"KX 候选分区无效：{zone!r}。")
    first_seen = _req_day(raw.get("first_seen"), "items.first_seen")
    zone_since = _req_day(raw.get("zone_since"), "items.zone_since")
    days_in_zone = _opt_int(raw.get("days_in_zone"))
    if days_in_zone is None or days_in_zone < 1:
        raise KxError("kx_bad_board", f"KX 候选 {code} 在区天数无效，没有采用。")
    elim_since = _req_day(raw.get("elim_since"), "items.elim_since") if raw.get("elim_since") is not None else None
    elim_days = _opt_int(raw.get("elim_days")) or 0
    next_remove = bool(raw.get("next_remove"))
    change_badge = raw.get("change_badge")
    if not isinstance(change_badge, str) or change_badge not in _CHANGE_BADGES:
        raise KxError("kx_bad_board", f"KX 候选 {code} 变动标记无效，没有采用。")
    cycle_tag = _req_str(raw, "cycle_tag")

    facts = raw.get("facts")
    if not isinstance(facts, Mapping):
        raise KxError("kx_bad_board", f"KX 候选 {code} 缺少程序事实，没有采用。")
    traj_out: list[dict] = []
    for day in facts.get("traj") or ():
        if not isinstance(day, Mapping):
            continue
        day_iso = _opt_str(day.get("date"))
        if not day_iso or not _DAY_RE.fullmatch(day_iso):
            continue
        traj_out.append({
            "date": day_iso,
            "pct": _opt_float(day.get("pct")),
            "tr": _opt_float(day.get("tr")),
            "zt": bool(day.get("zt")),
            "dt": bool(day.get("dt")),
            "zb": bool(day.get("zb")),
            "lhb": bool(day.get("lhb")),
        })

    judgment = raw.get("judgment")
    if not isinstance(judgment, Mapping):
        raise KxError("kx_bad_board", f"KX 候选 {code} 缺少 DeepSeek 判断，没有采用。")
    rhythm = judgment.get("rhythm")
    rhythm_kind = rhythm.get("kind") if isinstance(rhythm, Mapping) else None
    if not isinstance(rhythm, Mapping) or not isinstance(rhythm_kind, str) or rhythm_kind not in _RHYTHM_KINDS:
        raise KxError("kx_bad_board", f"KX 候选 {code} 节奏状态无效，没有采用。")
    rhythm_text = _req_str(rhythm, "text")

    factions_out: list[dict] = []
    for fac in judgment.get("factions") or ():
        if not isinstance(fac, Mapping):
            continue
        fname = _opt_str(fac.get("name"))
        if fname:
            factions_out.append({"name": fname, "primary": bool(fac.get("primary"))})
    if not factions_out:
        raise KxError("kx_bad_board", f"KX 候选 {code} 缺少情绪派系标签，没有采用。")

    return {
        "code": code,
        "name": name,
        "industry": industry,
        "zone": zone,
        "firstSeen": first_seen,
        "zoneSince": zone_since,
        "daysInZone": days_in_zone,
        "elimSince": elim_since,
        "elimDays": elim_days,
        "nextRemove": next_remove,
        "changeBadge": change_badge,
        "cycleTag": cycle_tag,
        "facts": {
            "close": _opt_float(facts.get("close")),
            "pct": _opt_float(facts.get("pct")),
            "tr": _opt_float(facts.get("tr")),
            "circMvYi": _opt_float(facts.get("circ_mv_yi")),
            "boardType": _opt_str(facts.get("board_type")) or "普通",
            "boardStatus": _opt_str(facts.get("board_status")) or "",
            "lbc": _opt_int(facts.get("lbc")),
            "breakDays": _opt_int(facts.get("break_days")),
            "firstSealTime": _opt_str(facts.get("first_seal_time")),
            "lastSealTime": _opt_str(facts.get("last_seal_time")),
            "breakCount": _opt_int(facts.get("break_count")),
            "ma5": _opt_float(facts.get("ma5")),
            "ma5Diff": _opt_float(facts.get("ma5_diff")),
            "ma10": _opt_float(facts.get("ma10")),
            "ma10Diff": _opt_float(facts.get("ma10_diff")),
            "ddPeak": _opt_float(facts.get("dd_peak")),
            "volRatioPeak": _opt_float(facts.get("vol_ratio_peak")),
            "zt5dN": _opt_int(facts.get("zt_5d_n")),
            "dt5dN": _opt_int(facts.get("dt_5d_n")),
            "lhb5dN": _opt_int(facts.get("lhb_5d_n")),
            "suspended": bool(facts.get("suspended")),
            "traj": traj_out,
            "lhbIsToday": bool(facts.get("lhb_is_today")),
            "lhb": _project_lhb(facts.get("lhb")),
        },
        "judgment": {
            "rhythm": {"kind": rhythm["kind"], "text": rhythm_text},
            "factions": factions_out,
            "why": _req_str(judgment, "why"),
            "lhbComment": _req_str(judgment, "lhb_comment"),
            "watchNote": _req_str(judgment, "watch_note"),
        },
    }


def _project_factions(raw_factions: object, items: list[dict]) -> list[dict]:
    if isinstance(raw_factions, list) and raw_factions:
        parsed = []
        for row in raw_factions:
            if not isinstance(row, Mapping):
                continue
            name = _opt_str(row.get("name"))
            count = _opt_int(row.get("count"))
            if name and count is not None and count > 0:
                parsed.append({"name": name, "count": count})
        if parsed:
            return parsed
    counts: dict[str, int] = {}
    for item in items:
        seen: set[str] = set()
        for tag in item["judgment"]["factions"]:
            name = tag["name"]
            if name not in seen:
                seen.add(name)
                counts[name] = counts.get(name, 0) + 1
    return [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _project_changes(raw: object) -> dict:
    if not isinstance(raw, Mapping):
        return {"added": [], "promoted": [], "toElim": [], "returned": [], "removed": []}
    def codes(key: str) -> list[str]:
        return [c.strip() for c in (raw.get(key) or ()) if isinstance(c, str) and c.strip()]
    removed_out = []
    for row in raw.get("removed") or ():
        if not isinstance(row, Mapping):
            continue
        code = _opt_str(row.get("code"))
        if not code:
            continue
        removed_out.append({
            "code": code,
            "name": _opt_str(row.get("name")) or code,
            "reason": _opt_str(row.get("reason")) or "",
        })
    return {
        "added": codes("added"),
        "promoted": codes("promoted"),
        "toElim": codes("to_elim"),
        "returned": codes("returned"),
        "removed": removed_out,
    }


def project_board(
    document: dict,
    *,
    now: datetime,
    last_read_at: datetime,
    stale: bool = False,
    reason: str | None = None,
) -> dict:
    """把校验通过的 KX schema 5 文档投影成 Neckline API 给双端的 JSON 结构。"""
    del now  # V4 盘中彻底静默，不按当前钟点派生盘中时段或自动刷新间隔
    doc = require_overview(document)

    seen_codes: set[str] = set()
    items = [_project_item(row, seen_codes) for row in doc["items"]]
    items.sort(key=lambda item: _ZONE_ORDER[item["zone"]])

    cap_raw = doc["capacity"]
    leader_count = sum(1 for item in items if item["zone"] == "leader")
    hot_count = sum(1 for item in items if item["zone"] == "hot")
    elim_count = sum(1 for item in items if item["zone"] == "elim")
    capacity = {
        "used": _opt_int(cap_raw.get("used")) if _opt_int(cap_raw.get("used")) is not None else len(items),
        "max": _opt_int(cap_raw.get("max")) or 25,
        "leaderCount": _opt_int(cap_raw.get("leader_count")) if _opt_int(cap_raw.get("leader_count")) is not None else leader_count,
        "hotCount": _opt_int(cap_raw.get("hot_count")) if _opt_int(cap_raw.get("hot_count")) is not None else hot_count,
        "elimCount": _opt_int(cap_raw.get("elim_count")) if _opt_int(cap_raw.get("elim_count")) is not None else elim_count,
        "nextFree": _opt_int(cap_raw.get("next_free")) if _opt_int(cap_raw.get("next_free")) is not None else elim_count,
    }

    generated_by = doc.get("generated_by")
    if not isinstance(generated_by, str) or generated_by not in _GENERATED_BY:
        generated_by = "program"
    deepseek_notice = _opt_str(doc.get("deepseek_notice"))
    fact_warnings = [
        redact_upstream(w.strip())
        for w in (doc.get("fact_warnings") or ())
        if isinstance(w, str) and w.strip()
    ]
    raw_push_summary = _opt_str(doc.get("push_summary"))
    push_summary = redact_upstream(raw_push_summary) if raw_push_summary else ""

    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": doc["status"],
        "version": _req_str(doc, "version"),
        "asOf": _req_day(doc.get("as_of"), "as_of"),
        "generatedAt": _req_iso_cst(doc.get("generated_at"), "generated_at"),
        "generatedBy": generated_by,
        "checkedAt": _opt_iso_cst(doc.get("checked_at")),
        "lastReadAt": last_read_at.astimezone(CST).isoformat(timespec="seconds"),
        "stale": bool(stale),
        "staleReason": redact_upstream(reason) if (stale and reason) else None,
        "deepseekStatus": doc["deepseek_status"],
        "deepseekNotice": redact_upstream(deepseek_notice) if deepseek_notice else None,
        "factWarnings": fact_warnings,
        "pushSummary": push_summary,
        "refresh": _project_refresh(doc.get("refresh")),
        "capacity": capacity,
        "sentiment": _project_sentiment(doc["sentiment"]),
        "factions": _project_factions(doc.get("factions"), items),
        "changes": _project_changes(doc.get("changes")),
        "items": items,
    }


__all__ = ["SCHEMA_VERSION", "ZONES", "project_board", "require_overview"]
