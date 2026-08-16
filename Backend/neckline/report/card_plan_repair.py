"""Targeted repair primitives for a frozen report's missing card price plans.

This module deliberately cannot run scan, selection, Tavily research, Tiering,
report generation, or notifications.  It appends a new immutable card version
and replaces only the matching cards inside the already-frozen report snapshot.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from neckline.db import connection, init_schema
from neckline.report.basket_daily import BasketDaily, BasketView, card_to_public_dict
from neckline.report.render import _render_today_baskets
from neckline.selection.basket_card import (
    CARD_SYSTEM_PROMPT,
    CLAMP_OK,
    EXIT_CLAMP_OK,
    LLM_OK,
    clamp_entry_zone,
    clamp_exit_reference,
    clamp_max_chase,
    clamp_reason_text,
)
from neckline.selection.basket_store import append_basket_card_version_from_existing
from neckline.selection.deep_reason import validate_card_material


TARGETED_REPAIR_SYSTEM_PROMPT = CARD_SYSTEM_PROMPT + """

【定向补录的额外硬约束】
这是对一张已有冻结卡的缺项修复，不是普通降级生成。上下文所列每个成员都已有 D0 收盘价和
次日涨跌停参考价，因此 entries 必须恰好覆盖全部成员，且 low、high、max_chase、exit_low、
exit_high 五个字段全部给出正数，任何一个都不得为 null。仍须满足 low ≤ high ≤ max_chase、
建仓与追价落在给定涨跌停闭区间、exit_low > D0 收盘价、exit_low ≤ exit_high。不要改变篮子、
成员、角色、档位或证据。
"""


@dataclass(frozen=True)
class CardRepair:
    basket_id: int
    from_version: int
    to_version: int
    expected_card_sha256: str
    card: Mapping[str, Any]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_repair_context(card: Mapping[str, Any], *, trade_date: date) -> str:
    """Build one no-search prompt solely from an existing frozen card."""
    lines = [
        f"数据锚定日:{trade_date.isoformat()}。这是已有篮子卡的定向补全，不重新选股。",
        f"篮子:{card.get('name') or ''}(basket_key {card.get('basket_key') or ''})",
        f"共同驱动:{card.get('driver') or ''}",
        f"为什么是现在:{card.get('why_now') or ''}",
        f"当前档位:T{card.get('tier') if card.get('tier') is not None else '?'}；档位不可更改。",
    ]
    evidence = card.get("evidence") or []
    if evidence:
        lines.append("已有证据(只可使用这些内容):")
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                f"  · [{item.get('date') or '日期未记'}] {item.get('claim') or ''}"
                f"(来源:{item.get('source') or '未记'})"
            )
    else:
        lines.append("已有证据:无；不得自行补新闻或联网事实。")
    lines.append("成员与机械价格锚(原价，不是百分比):")
    for member in card.get("members") or []:
        if not isinstance(member, Mapping):
            continue
        mech = member.get("mech") if isinstance(member.get("mech"), Mapping) else {}
        lines.append(
            f"  · {member.get('ts_code') or ''} {member.get('name') or ''};"
            f"角色={member.get('role_llm') or ''}/{member.get('role_mech') or '未判定'};"
            f"D0收盘={mech.get('close')};MA20={mech.get('ma20')};"
            f"次日涨跌停参考价=[{mech.get('limit_down')},{mech.get('limit_up')}];"
            f"章程警戒线={mech.get('stop_price')}"
        )
    lines.append("机械验证条件:" + canonical_json(card.get("verification_spec") or {}))
    lines.append("机械失效条件:" + canonical_json(card.get("invalidation_spec") or {}))
    lines.append("请只补齐卡片输出，不要改变篮子、成员、角色、档位或证据。")
    return "\n".join(lines)


def repair_frozen_card(
    card: Mapping[str, Any], payload: Mapping[str, Any], *, narrative: str, version: int,
) -> Dict[str, Any]:
    """Patch only human card material and mechanically clamp all price fields."""
    members = [m for m in (card.get("members") or []) if isinstance(m, Mapping)]
    codes = [str(member.get("ts_code") or "") for member in members]
    material = validate_card_material(payload, member_codes=codes)
    by_code = {str(item["ts_code"]): item for item in material["entries"]}
    repaired = copy.deepcopy(dict(card))
    repaired_members = repaired.get("members") or []
    errors = []
    for member in repaired_members:
        code = str(member.get("ts_code") or "")
        raw = by_code[code]
        mech = member.get("mech") if isinstance(member.get("mech"), Mapping) else {}
        low, high, zone_clamp = clamp_entry_zone(
            {"low": raw["low"], "high": raw["high"]},
            mech.get("limit_up"), mech.get("limit_down"),
        )
        chase, chase_clamp = clamp_max_chase(
            raw["max_chase"], mech.get("limit_up"), mech.get("limit_down"), zone_high=high,
        )
        exit_low, exit_high, exit_clamp = clamp_exit_reference(
            {"low": raw["exit_low"], "high": raw["exit_high"]}, mech.get("close"),
        )
        if zone_clamp != CLAMP_OK or chase_clamp != CLAMP_OK or exit_clamp != EXIT_CLAMP_OK:
            errors.append(f"{code}:entry={zone_clamp},chase={chase_clamp},exit={exit_clamp}")
            continue
        member.update({
            "entry_zone": {"low": low, "high": high, "why": str(raw["why"]).strip()},
            "entry_zone_clamp": zone_clamp,
            "entry_zone_unavailable_reason": clamp_reason_text(zone_clamp),
            "max_chase": chase,
            "max_chase_clamp": chase_clamp,
            "max_chase_unavailable_reason": clamp_reason_text(chase_clamp),
            "exit_reference": {"low": exit_low, "high": exit_high},
            "exit_reference_clamp": exit_clamp,
            "exit_reference_unavailable_reason": clamp_reason_text(exit_clamp),
        })
    if errors:
        raise ValueError("price material rejected by mechanical clamps: " + ";".join(errors))
    repaired.update({
        "version": int(version),
        "upside_path": str(material["upside_path"]).strip(),
        "upside_path_unavailable_reason": None,
        "verification_text": str(material["verification"]).strip(),
        "invalidation_text": str(material["invalidation"]).strip(),
        "risks": [str(item).strip() for item in material["risks"]],
        "tier_note": (str(material["tier_note"]).strip()
                      if material.get("tier_note") is not None else None),
        "narrative": narrative.strip() or str(card.get("narrative") or ""),
        "llm_stage": LLM_OK,
        "generation_source": "targeted_card_repair",
        "degraded": False,
        "notes": list(card.get("notes") or []) + ["targeted_card_plan_repair"],
    })
    return repaired


def patch_report_snapshot(
    snapshot: Mapping[str, Any], repairs: Mapping[int, CardRepair],
) -> Dict[str, Any]:
    out = copy.deepcopy(dict(snapshot))
    seen = set()
    for basket in out.get("baskets") or []:
        if not isinstance(basket, dict):
            continue
        basket_id = int(basket.get("basketId"))
        repair = repairs.get(basket_id)
        if repair is None:
            continue
        basket["card"] = card_to_public_dict(repair.card)
        basket["cardVersion"] = repair.to_version
        basket["cardUnavailableReason"] = None
        seen.add(basket_id)
    if seen != set(repairs):
        raise ValueError("report snapshot basket ids do not match repair set")
    return out


def render_today_baskets_from_snapshot(snapshot: Mapping[str, Any], *, trade_date: date) -> str:
    daily = BasketDaily(trade_date=trade_date)
    daily.baskets_available = bool(snapshot.get("basketsAvailable"))
    daily.baskets_unavailable_reason = snapshot.get("basketsUnavailableReason")
    for item in snapshot.get("baskets") or []:
        if not isinstance(item, Mapping):
            continue
        daily.baskets.append(BasketView(
            basket_id=int(item.get("basketId")), basket_key=str(item.get("basketKey") or ""),
            name=str(item.get("name") or ""), tier=item.get("tier"),
            member_codes=tuple(str(code) for code in (item.get("memberCodes") or [])),
            card=dict(item.get("card")) if isinstance(item.get("card"), Mapping) else None,
            card_version=item.get("cardVersion"),
            card_unavailable_reason=item.get("cardUnavailableReason"),
            engine_code=item.get("engineCode"), engine_version=item.get("engineVersion"),
            skeleton_version=item.get("skeletonVersion"),
            exec_hints={str(k): list(v) for k, v in (item.get("execHints") or {}).items()},
            score={
                "scorePercent": item.get("scorePercent"),
                "contributions": list(item.get("scoreContributions") or []),
            },
        ))
    return _render_today_baskets(daily)


def patch_report_markdown(markdown: str, snapshot: Mapping[str, Any], *, trade_date: date) -> str:
    start = markdown.find("## ③ 今日篮子")
    end = markdown.find("### ③b 今日未定档篮子", start + 1)
    if start < 0 or end < 0:
        raise ValueError("report markdown does not contain the expected basket section boundaries")
    section = render_today_baskets_from_snapshot(snapshot, trade_date=trade_date).rstrip()
    return markdown[:start] + section + "\n\n" + markdown[end:]


def apply_report_card_repairs(
    *, trade_date: str, report_date: str, expected_snapshot_sha256: str,
    repairs: Mapping[int, CardRepair], snapshot: Mapping[str, Any], markdown: str,
    db_path: Path,
) -> None:
    """Append card versions and patch one report snapshot in one SQLite transaction."""
    if not repairs:
        raise ValueError("repair set is empty")
    init_schema(db_path)
    with connection(db_path) as conn:
        report = conn.execute(
            "SELECT report_date,basket_daily_json FROM reports WHERE trade_date=?", (trade_date,)
        ).fetchone()
        if report is None or str(report[0] or "") != report_date:
            raise ValueError("target report identity changed before repair")
        current_snapshot = json.loads(report[1])
        if json_sha256(current_snapshot) != expected_snapshot_sha256:
            raise ValueError("target report snapshot changed before repair")
        for basket_id, repair in sorted(repairs.items()):
            row = conn.execute(
                "SELECT version,card_json FROM basket_cards WHERE basket_id=? "
                "ORDER BY version DESC LIMIT 1", (int(basket_id),),
            ).fetchone()
            if row is None or int(row[0]) != repair.from_version:
                raise ValueError(f"basket {basket_id} card version changed before repair")
            if json_sha256(json.loads(row[1])) != repair.expected_card_sha256:
                raise ValueError(f"basket {basket_id} frozen card changed before repair")
            result = append_basket_card_version_from_existing(
                basket_id, repair.card, from_version=repair.from_version,
                to_version=repair.to_version, conn=conn,
            )
            if result["cards_inserted"] != 1 or result["frozen_conflicts"]:
                raise ValueError(f"basket {basket_id} repaired card could not be appended")
        cur = conn.execute(
            "UPDATE reports SET basket_daily_json=?,markdown=? "
            "WHERE trade_date=? AND report_date=?",
            (json.dumps(snapshot, ensure_ascii=False), markdown, trade_date, report_date),
        )
        if cur.rowcount != 1:
            raise ValueError("target report disappeared before repair")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


__all__ = [
    "CardRepair", "apply_report_card_repairs", "atomic_write_text", "build_repair_context",
    "json_sha256", "patch_report_markdown", "patch_report_snapshot", "repair_frozen_card",
    "TARGETED_REPAIR_SYSTEM_PROMPT",
]
