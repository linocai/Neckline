#!/usr/bin/env python3
"""Tier 预启动填充(plan §五 V2-⑯-F,裁定 #4;🔴 高危区:往权威库灌业务行)。

**为什么要它**:评价引擎(⑨/⑨-C)要"有历史才评得动",而系统真启动那天库里一条篮子
都没有 —— 冷启动期 Tier 分层没有任何过往成绩可参照。⑯-F 的解法是**在外部把近期若干
交易日的篮子/Tier/卡配好,过校验后灌进来**,之后系统自转。

**灌进来的行与自转的行同表、同格式**,评价引擎一视同仁打分;分层靠两个字段:
  · `baskets.via = 'preseed'`      —— 这一行是灌的,不是引擎当天算的
  · `baskets.pack_version = 'preseed'` —— ⚠ **不许填 `K7-pack-v1`**(planner 定案):
    preseed 篮子是**人工 + LLM 在外部配的**,不是引擎按 K7 包算出来的;填成 K7-pack-v1
    会污染 §12.5 的按包归因(⑨-C 会把人工配的成绩算到 K7 头上)。**字段不许撒谎** ——
    两处都标。⑨-C 的按包归因把 `'preseed'` 当**独立一档**统计。

**四道校验闸(缺一不写)**,与 ⑤/⑦ 的机械闸同源、⛔ 不另写一份判据:
  1. **JSON Schema**:结构 / 必填 / 取值域(tier ∈ {1,2,3}、role ∈ leader|core|elastic、
     driver_kind 白名单、trade_date 是交易日、成员非空)。
  2. **成员白名单闸**:每个成员过 `selection.member_hygiene.apply_member_hygiene`
     —— ST / 停牌 / 次新 / 板块不在 `allowed_boards` / K4 hard_cut 一律拒收。
     ⚠ 判据参数读**当前现役包**(卫生线问的是"这只票干不干净",该用今天的规矩),
     但落库 `pack_version` 仍是 `'preseed'` —— 两者不是一回事,别对齐。
  3. **角色对拍**:输入给的 `role_llm` 与 `leader_structure_daily.role_mech` 比对,
     不一致 → `role_conflict=1` **原样入库**(分歧并存,⛔ 不静默采信任一方、
     ⛔ 不拿机械侧覆盖输入)。
  4. **夹逼**:卡里的建仓观察区间 / 最高追价必须落在**次日**涨跌停闭区间内,复用
     `selection.basket_card.clamp_entry_zone` / `clamp_max_chase` 同一实现;被拦的数字
     **置空 + 记原因**,⛔ 不是"改成边界值"(那是替 LLM 圆谎)。

**写入纪律**:默认演练;`--confirm` 才写;写前 `.backup` + `cp -p` **双备份**;单事务;
**同一交易日已有篮子行 → 拒绝**(冻结/不回写三律:preseed 只填空日子,绝不覆盖引擎
自转出来的历史)。

**输入文件**:外部准备的 V2 格式 JSON,`--example` 可打印一份带注释的模板。
⚠ **本脚本不生成数据**:篮子/驱动/角色/剧本是人 + LLM 在外部配的产物,脚本只负责
"校验 + 落库"。没有输入文件就是没有 —— ⛔ 别编。

用法:
    python scripts/oneoff/preseed_baskets.py --example > preseed.json   # 打模板
    python scripts/oneoff/preseed_baskets.py --file preseed.json --db /path.db            # 演练
    python scripts/oneoff/preseed_baskets.py --file preseed.json --db /path.db --confirm  # 双备份 + 写
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
import zlib
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

VIA_PRESEED = "preseed"
PACK_VERSION_PRESEED = "preseed"      # ⛔ 不许改成 K7-pack-v1,理由见模块头
_ROLES = ("leader", "core", "elastic")
_DRIVER_KINDS = ("theme", "policy", "event", "commodity", "overseas", "rotation", "limit_cluster")

_EXAMPLE = {
    "_comment": "V2-⑯-F Tier 预启动填充输入件。每个交易日一个条目;篮子/驱动/角色/剧本由人+LLM 在外部配好,本文件只是搬运格式。",
    "days": [
        {
            "trade_date": "20260731",
            "baskets": [
                {
                    "name": "示例:固态电池中试放量",
                    "driver": "示例:三家龙头同周公告中试线投产,产业链排产上修",
                    "driver_kind": "theme",
                    "tier": 1,
                    "evidence_status": "ok",
                    "members": [
                        {"ts_code": "600000.SH", "role_llm": "leader",
                         "reason": "示例:率先公告且量能最实,同题材里唯一站上年线"},
                        {"ts_code": "000001.SZ", "role_llm": "core",
                         "reason": "示例:配套环节,跟随度高但弹性弱于龙头"}
                    ],
                    "card": {
                        "why_now": "示例:为什么是现在(一句话)",
                        "evidence": [{"claim": "示例证据", "source": "示例来源", "date": "2026-07-31", "url": ""}],
                        "tier_reason": "示例:为什么给这一档",
                        "entry_zone": {"low": 10.10, "high": 10.60},
                        "max_chase": 10.90,
                        "invalidation": "示例:失效条件(触发即整篮作废)"
                    }
                }
            ]
        }
    ],
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _basket_key(trade_date: str, name: str) -> str:
    """与引擎同口径:`crc32(trade_date|driver_slug)` 十六进制,**跨进程可复现**
    (⛔ 不用内置 `hash()` —— 带进程盐,`PYTHONHASHSEED` 一变就漂,历史不可复现)。"""
    return format(zlib.crc32(f"{trade_date}|{name}".encode("utf-8")) & 0xFFFFFFFF, "08x")


# ══════════════════════════════════════════════════════════════════════════
# 闸 1:JSON Schema(结构 / 必填 / 取值域)
# ══════════════════════════════════════════════════════════════════════════

def validate_schema(doc: Any) -> List[str]:
    errs: List[str] = []
    if not isinstance(doc, dict) or not isinstance(doc.get("days"), list) or not doc["days"]:
        return ["顶层必须是 {\"days\": [ … ]} 且至少一天"]
    seen_days = set()
    for i, day in enumerate(doc["days"]):
        p = f"days[{i}]"
        if not isinstance(day, dict):
            errs.append(f"{p} 不是对象"); continue
        td = day.get("trade_date")
        if not isinstance(td, str) or len(td) != 8 or not td.isdigit():
            errs.append(f"{p}.trade_date 必须是 YYYYMMDD 字符串")
        elif td in seen_days:
            errs.append(f"{p}.trade_date={td} 重复")
        else:
            seen_days.add(td)
        bl = day.get("baskets")
        if not isinstance(bl, list) or not bl:
            errs.append(f"{p}.baskets 必须是非空数组"); continue
        names = set()
        for j, b in enumerate(bl):
            q = f"{p}.baskets[{j}]"
            if not isinstance(b, dict):
                errs.append(f"{q} 不是对象"); continue
            for k in ("name", "driver"):
                if not isinstance(b.get(k), str) or not b[k].strip():
                    errs.append(f"{q}.{k} 必填且非空")
            if b.get("name") in names:
                errs.append(f"{q}.name 在同一天内重复(basket_key 会撞)")
            names.add(b.get("name"))
            if b.get("driver_kind") not in _DRIVER_KINDS:
                errs.append(f"{q}.driver_kind 必须是 {list(_DRIVER_KINDS)} 之一")
            if b.get("tier") not in (1, 2, 3):
                errs.append(f"{q}.tier 必须是 1/2/3")
            if b.get("evidence_status", "ok") not in ("ok", "search_unavailable", "partial"):
                errs.append(f"{q}.evidence_status 取值非法")
            ms = b.get("members")
            if not isinstance(ms, list) or not ms:
                errs.append(f"{q}.members 必须是非空数组"); continue
            codes = set()
            for k2, m in enumerate(ms):
                r = f"{q}.members[{k2}]"
                if not isinstance(m, dict):
                    errs.append(f"{r} 不是对象"); continue
                code = m.get("ts_code")
                if not isinstance(code, str) or "." not in code:
                    errs.append(f"{r}.ts_code 必须是带后缀的代码(如 600000.SH)")
                elif code in codes:
                    errs.append(f"{r}.ts_code={code} 在同篮内重复")
                else:
                    codes.add(code)
                if m.get("role_llm") not in _ROLES:
                    errs.append(f"{r}.role_llm 必须是 {list(_ROLES)} 之一")
                if not isinstance(m.get("reason"), str) or not m["reason"].strip():
                    errs.append(f"{r}.reason 必填(为何是这只而不是同题材其他票)")
            if sum(1 for m in ms if isinstance(m, dict) and m.get("role_llm") == "leader") > 1:
                errs.append(f"{q} 有多于一个 leader")
    return errs


# ══════════════════════════════════════════════════════════════════════════
# 闸 2/3/4:白名单 / 角色对拍 / 夹逼(一律复用引擎既有实现)
# ══════════════════════════════════════════════════════════════════════════

def _parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def check_members(day: Dict[str, Any], db_path: Path, parquet_dir: Optional[Path]) -> Tuple[Dict[str, str], List[str], bool]:
    """闸 2 成员白名单。返回 `(被拒码 -> 原因, 提示, 是否整体降级)`。
    ⚠ **降级(算不出)在 preseed 里按 fail-closed 处理** —— 与在线路径「降级=不拦」
    刻意相反:在线是"今天少一维也得出报告",而 preseed 是**往权威库灌历史**,宁可
    不灌也不能灌进没验过的成员。"""
    from neckline.selection.member_hygiene import apply_member_hygiene
    from neckline.selection.pack import get_active_pack

    td = _parse_day(day["trade_date"])
    codes = sorted({m["ts_code"] for b in day["baskets"] for m in b["members"]})
    pack = get_active_pack(db_path=db_path)
    if pack is None:
        return {c: "库里无现役策略包,卫生线判据无从取参数" for c in codes}, [], True

    industry_of, close_of = _load_industry_and_close(codes, td, db_path, parquet_dir)
    res = apply_member_hygiene(codes, td, pack, industry_of=industry_of, close_of=close_of,
                               db_path=db_path, parquet_dir=parquet_dir)
    rejected = {r.ts_code: f"{r.primitive}:{r.detail}" for r in res.rejected}
    notes = []
    degraded = bool(res.hygiene_unavailable or res.k4_unavailable)
    if res.hygiene_unavailable:
        notes.append("⚠ 趋势/流动性线本次算不出(hygiene_unavailable)——preseed 按 fail-closed 拒绝写入")
    if res.k4_unavailable:
        notes.append("⚠ K4 安检本次算不出(k4_unavailable)——preseed 按 fail-closed 拒绝写入")
    return rejected, notes, degraded


def _load_industry_and_close(codes, td: date, db_path: Path, parquet_dir: Optional[Path]):
    from neckline.data.market_data import get_market_slice
    from neckline.sentinel.universe import load_stock_meta

    industry_of: Dict[str, str] = {}
    try:
        for code, meta in load_stock_meta(list(codes), db_path).items():
            ind = getattr(meta, "industry", None)
            if ind:
                industry_of[code] = ind
    except Exception:  # noqa: BLE001
        pass
    close_of: Dict[str, float] = {}
    try:
        df = get_market_slice(td, table="daily", parquet_dir=parquet_dir)
        if not df.is_empty():
            want = set(codes)
            for r in df.select(["ts_code", "close"]).iter_rows(named=True):
                if r["ts_code"] in want and r["close"] is not None:
                    close_of[r["ts_code"]] = float(r["close"])
    except Exception:  # noqa: BLE001
        pass
    return industry_of, close_of


def role_crosscheck(day: Dict[str, Any], db_path: Path) -> Dict[str, Optional[str]]:
    """闸 3 角色对拍:`ts_code -> role_mech`(`None` = 机械侧无判定)。**只做对拍不做
    覆盖** —— 不一致时两说并存(`role_conflict=1`),⛔ 不拿任一方压另一方。"""
    from neckline.scan.leader import load_leader_structure

    out: Dict[str, Optional[str]] = {}
    try:
        df = load_leader_structure(_parse_day(day["trade_date"]), db_path=db_path)
    except Exception:  # noqa: BLE001
        return out
    if df is None or df.is_empty() or "role_mech" not in df.columns:
        return out
    for r in df.select(["ts_code", "role_mech"]).iter_rows(named=True):
        rm = r["role_mech"]
        out.setdefault(r["ts_code"], None if (not rm or rm == "unknown") else rm)
    return out


def clamp_card_numbers(basket: Dict[str, Any], limits: Dict[str, Tuple[Optional[float], Optional[float]]]
                       ) -> Tuple[Dict[str, Any], List[str]]:
    """闸 4 夹逼。`limits[ts_code] = (次日 limit_up, 次日 limit_down)`;**篮子级的
    区间/追价按龙头(没有龙头则第一个成员)那只的涨跌停夹**。被拦 → 置空 + 记原因,
    ⛔ 不改成边界值。"""
    from neckline.selection.basket_card import CLAMP_OK, clamp_entry_zone, clamp_max_chase

    card = dict(basket.get("card") or {})
    notes: List[str] = []
    anchor = next((m["ts_code"] for m in basket["members"] if m.get("role_llm") == "leader"),
                  basket["members"][0]["ts_code"])
    up, down = limits.get(anchor, (None, None))
    low, high, c1 = clamp_entry_zone(card.get("entry_zone"), up, down)
    if c1 != CLAMP_OK:
        notes.append(f"{basket['name']}: 建仓区间未过夹逼({c1},锚 {anchor}),已置空")
        card["entry_zone"] = None
    else:
        card["entry_zone"] = {"low": low, "high": high}
    card["entry_zone_clamp"] = c1
    chase, c2 = clamp_max_chase(card.get("max_chase"), up, down, zone_high=high)
    if c2 != CLAMP_OK:
        notes.append(f"{basket['name']}: 最高追价未过夹逼({c2},锚 {anchor}),已置空")
    card["max_chase"] = chase
    card["max_chase_clamp"] = c2
    return card, notes


def _next_day_limits(day: Dict[str, Any], db_path: Path, parquet_dir: Optional[Path]
                     ) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """次日涨跌停闭区间(夹逼的锚)。复用 `limit_derived` 唯一源,⛔ 不自己算幅度。"""
    from neckline.calendar import next_trading_day
    from neckline.data.market_data import get_market_slice

    out: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    try:
        nxt = next_trading_day(_parse_day(day["trade_date"]))
        df = get_market_slice(nxt, table="limit_derived", parquet_dir=parquet_dir)
        if df is None or df.is_empty():
            return out
        cols = set(df.columns)
        up_c = "limit_up_price" if "limit_up_price" in cols else None
        dn_c = "limit_down_price" if "limit_down_price" in cols else None
        if not (up_c and dn_c):
            return out
        for r in df.select(["ts_code", up_c, dn_c]).iter_rows(named=True):
            out[r["ts_code"]] = (r[up_c], r[dn_c])
    except Exception:  # noqa: BLE001
        pass
    return out


# ══════════════════════════════════════════════════════════════════════════

def _double_backup(db_path: Path) -> Tuple[Path, Path, float]:
    tag = f"preseed-{_stamp()}"
    bak = db_path.parent / f"{db_path.name}.bak-{tag}"
    cpbak = db_path.parent / f"{db_path.name}.cpbak-{tag}"
    t0 = time.time()
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(bak))
        try:
            src.backup(dst)
            integ = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if integ != "ok":
        raise ValueError(f".backup 产物 integrity_check={integ},拒绝继续")
    shutil.copy2(db_path, cpbak)
    return bak, cpbak, time.time() - t0


def run(file: Optional[Path], db_path: Path, parquet_dir: Optional[Path], confirm: bool) -> int:
    from neckline.calendar import is_trading_day
    from neckline.selection.engine_api import ENGINE_API_VERSION

    doc = json.loads(file.read_text(encoding="utf-8"))
    print(f"输入件 : {file}\n目标库 : {db_path}")

    errs = validate_schema(doc)
    if errs:
        print("\n✗ 闸 1(JSON Schema)未通过:", file=sys.stderr)
        for e in errs:
            print(f"    {e}", file=sys.stderr)
        return 2
    print(f"闸 1 通过:{len(doc['days'])} 个交易日、"
          f"{sum(len(d['baskets']) for d in doc['days'])} 个篮子,结构与取值域合法。")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=15000")
    charter = (conn.execute(
        "SELECT version FROM strategy_versions WHERE is_active=1").fetchone() or ["<无现役章程>"])[0]
    blocked = False
    plans: List[Dict[str, Any]] = []
    for day in doc["days"]:
        td = day["trade_date"]
        print(f"\n—— {td} ——")
        if not is_trading_day(_parse_day(td)):
            print(f"  ✗ {td} 不是交易日,拒绝", file=sys.stderr); blocked = True; continue
        n = conn.execute("SELECT COUNT(*) FROM baskets WHERE trade_date=?",
                         (_parse_day(td).isoformat(),)).fetchone()[0]
        if n:
            print(f"  ✗ 该交易日已有 {n} 个篮子行 —— preseed 只填空日子,⛔ 绝不覆盖既有历史",
                  file=sys.stderr)
            blocked = True; continue

        rejected, notes, degraded = check_members(day, db_path, parquet_dir)
        for t in notes:
            print(f"  {t}")
        if degraded:
            blocked = True
        roles = role_crosscheck(day, db_path)
        limits = _next_day_limits(day, db_path, parquet_dir)
        if not limits:
            print("  ⚠ 次日 limit_derived 分区读不到 —— 夹逼将全体判 rejected_no_limit(数字会被置空)")

        for b in day["baskets"]:
            bad = [m["ts_code"] for m in b["members"] if m["ts_code"] in rejected]
            if bad:
                print(f"  ✗ 篮「{b['name']}」成员未过白名单闸:"
                      + "; ".join(f"{c}({rejected[c]})" for c in bad), file=sys.stderr)
                blocked = True
            card, cnotes = clamp_card_numbers(b, limits)
            for t in cnotes:
                print(f"  ! {t}")
            conflicts = [m["ts_code"] for m in b["members"]
                         if roles.get(m["ts_code"]) and roles[m["ts_code"]] != m["role_llm"]]
            if conflicts:
                print(f"  ! 篮「{b['name']}」角色对拍分歧(两说并存,原样入库):{conflicts}")
            plans.append({"trade_date": td, "basket": b, "card": card,
                          "roles": roles, "conflicts": set(conflicts)})
            print(f"  · 篮「{b['name']}」 tier={b['tier']} 成员 {len(b['members'])} 只"
                  f" key={_basket_key(_parse_day(td).isoformat(), b['name'])}")
    if blocked:
        print("\n✗ 有闸未通过 —— **一个字节都不写**(preseed 是往权威库灌历史,"
              "宁可不灌也不灌没验过的)。", file=sys.stderr)
        conn.close(); return 3

    print(f"\n所有闸通过。将写入 via='{VIA_PRESEED}' / pack_version='{PACK_VERSION_PRESEED}' / "
          f"charter_version='{charter}' / engine_api_version={ENGINE_API_VERSION}")
    if not confirm:
        print("\n[dry-run] 未带 --confirm,**未写库**。")
        conn.close(); return 0

    bak, cpbak, el = _double_backup(db_path)
    print(f"\n—— 双备份(写前)——\n  {bak} ({bak.stat().st_size} B)\n  {cpbak} ({cpbak.stat().st_size} B)"
          f"\n  .backup 耗时 {el:.2f}s,integrity=ok")

    now = _now()
    written = {"baskets": 0, "members": 0, "cards": 0}
    with conn:
        for pl in plans:
            td_iso = _parse_day(pl["trade_date"]).isoformat()
            b, card = pl["basket"], pl["card"]
            key = _basket_key(td_iso, b["name"])
            cur = conn.execute(
                "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
                " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (td_iso, key, b["name"], b["driver"], b["driver_kind"], int(b["tier"]),
                 PACK_VERSION_PRESEED, int(ENGINE_API_VERSION), charter, VIA_PRESEED,
                 b.get("evidence_status", "ok"), now))
            bid = cur.lastrowid
            written["baskets"] += 1
            for m in b["members"]:
                conn.execute(
                    "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                    " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (bid, m["ts_code"], m["role_llm"], pl["roles"].get(m["ts_code"]),
                     1 if m["ts_code"] in pl["conflicts"] else 0, m["reason"], 1, now))
                written["members"] += 1
            cj = dict(card)
            cj.update({"basket_key": key, "trade_date": td_iso, "tier": int(b["tier"]),
                       "name": b["name"], "driver": b["driver"], "driver_kind": b["driver_kind"],
                       "version": 1, "via": VIA_PRESEED,
                       "members": [{"ts_code": m["ts_code"], "role_llm": m["role_llm"],
                                    "role_mech": pl["roles"].get(m["ts_code"]),
                                    "role_conflict": 1 if m["ts_code"] in pl["conflicts"] else 0,
                                    "reason": m["reason"]} for m in b["members"]]})
            conn.execute(
                "INSERT INTO basket_cards (basket_id, version, card_json, charter_version,"
                " pack_version, engine_api_version, created_at) VALUES (?,1,?,?,?,?,?)",
                (bid, json.dumps(cj, ensure_ascii=False), charter, PACK_VERSION_PRESEED,
                 int(ENGINE_API_VERSION), now))
            written["cards"] += 1
    print(f"\n✓ 已写入(单事务):{written}")
    rows = conn.execute(
        "SELECT via, pack_version, COUNT(*) FROM baskets GROUP BY via, pack_version").fetchall()
    print("  baskets 分层核对:" + "; ".join(f"via={r[0]} pack={r[1]} → {r[2]} 行" for r in rows))
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Tier 预启动填充(plan V2-⑯-F,默认 dry-run)")
    ap.add_argument("--file", type=Path, help="外部准备好的 V2 格式输入件")
    ap.add_argument("--db", type=Path, help="目标库(默认 settings.db_path)")
    ap.add_argument("--parquet-dir", type=Path, default=None)
    ap.add_argument("--confirm", action="store_true", help="确认写库(不带则只演练)")
    ap.add_argument("--example", action="store_true", help="打印输入件模板后退出")
    a = ap.parse_args()
    if a.example:
        print(json.dumps(_EXAMPLE, ensure_ascii=False, indent=2))
        return 0
    if not a.file:
        print("错误:缺 --file(外部准备好的输入件)。本脚本不生成数据,⛔ 别编 —— "
              "先用 --example 拿模板,由策略线/用户在外部配好再来。", file=sys.stderr)
        return 2
    from neckline.config import settings
    db_path = a.db or settings.db_path
    try:
        return run(a.file, db_path, a.parquet_dir, a.confirm)
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as e:
        print(f"错误(已中止):{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
