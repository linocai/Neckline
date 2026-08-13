#!/usr/bin/env python3
"""K4 advisory 的 **B3「题材持续 2-3 天」黄牌退役**(plan §五 V2-⑯-I,🔴 高危区:碰权威库)。

**改什么(只此一项,不扩)**:把 `strategy_versions` K4 行 `rule_json` 里
`k4_advisory.avoid_flag.B3_theme_persist_2_3` **这一个键删掉**。别的一个字节都不动。

**🔴 但这不违反「现役章程零改动」红线,先看清楚**:`k4_advisory` 住在 K4 行,而 **K4 行
`is_active=0`、永不激活**(§七 📌-22);现役章程是 `v1.3.3`,本脚本一个字节都不碰它,
也不碰任何 config 阈值。改的是「**建议载体**」不是「**纪律**」。跑完 K4 行仍 `is_active=0`、
`activated_at` 不动、`strategy_activation_log` 不增行 —— 三条都在收尾断言里硬查。

**为什么退役**(H11 证据,`research/k7_pre_report.md`):2-3 天(发酵态)在当前 regime 是
最优注意力段,原「被认可 = 接盘侧」的判决主要由样本内年份驱动。正向偏好由**排序侧**
承接(K7-pack 的 `industry_stage_score` 五态序),⛔ 不在 advisory 里造第三种牌。
⛔ **A2 红牌(≥4 天硬回避)不动** —— 过热态双尾最重,H11 再确认。

**为何 stdlib-only、不 import neckline**(同 `bootstrap_k4.py` 体例):目标库属
`neckline:neckline`,写库须以 `neckline` 身份跑;stdlib-only 让脚本从任意工作目录都能跑。
⚠ **绝不用 `brain.save_version`**:它会连 `changelog`/`metrics_json`/`created_at` 一起重写,
且 `INSERT OR REPLACE` 盲覆盖破坏本脚本的「内容不符则拒绝」语义。走原始 SQL 的 UPDATE。
⚠ 重写 `rule_json` 必须用 `json.dumps(obj, ensure_ascii=False)` —— 与 `brain.save_version`
逐字节同口径(已实测该 round-trip 对现库 K4 行 byte-identical),故除了被删的那 101 字节,
其余部分**逐字节不变**;两个 sha256 在 payload 里硬钉,不符即拒。

**幂等语义(三态)**:
  1. B3 仍在 `avoid_flag` 且 before-sha 吻合 → 演练打 diff;`--confirm` 才写。
  2. B3 已不在(after-sha 吻合)         → 报「无需变更」,`--confirm` 也是 **0 改动**,exit 0。
  3. 两个 sha 都不吻合(库里 K4 与预期不同) → **拒绝 + 非零退出**,除非显式
     `--allow-unknown-base`(打印实际 sha + 全量 diff 交人判断)。

**留痕**:写前 **`.backup` + `cp -p` 双备份**(默认写到库同目录,可 `--backup-dir` 改);
写前写后各 dump 一份 `rule["k4_advisory"]` 全文到 `--dump-dir`,diff 落
`archive/K4_advisory_B3退役_2026MMDD.md`(**只此一处存全文**,§九 一行 + 链接)。

用法:
    python scripts/oneoff/retire_k4_b3.py --db /path/neckline.db                 # 演练(默认,不写)
    python scripts/oneoff/retire_k4_b3.py --db /path/neckline.db --confirm       # 双备份 + 单事务写
    python scripts/oneoff/retire_k4_b3.py --db /path/neckline.db --verify-only   # 只读复核退役后状态
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_DEFAULT_PAYLOAD = Path(__file__).resolve().parent / "retire_k4_b3_payload.json"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def load_payload(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    if p.get("task") != "retire_k4_b3":
        raise ValueError(f"payload task={p.get('task')!r},不是本脚本的声明件")
    for key in ("target_version", "section", "retire_code",
                "expected_rule_json_sha256_before", "expected_rule_json_sha256_after"):
        if not p.get(key):
            raise ValueError(f"payload 缺字段 {key}")
    return p


# ══════════════════════════════════════════════════════════════════════════
# 只读探查
# ══════════════════════════════════════════════════════════════════════════

def _read_row(conn: sqlite3.Connection, version: str) -> Optional[sqlite3.Row]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(strategy_versions)")}
    sel = "version, rule_json, is_active" + (", activated_at" if "activated_at" in cols else "")
    conn.row_factory = sqlite3.Row
    return conn.execute(
        f"SELECT {sel} FROM strategy_versions WHERE version=?", (version,)
    ).fetchone()


def _activation_log_rows(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) FROM strategy_activation_log").fetchone()[0])
    except sqlite3.Error:
        return -1  # 表不存在(老库)——不作为断言依据,只如实打印


def _active_versions(conn: sqlite3.Connection) -> list:
    return [r[0] for r in conn.execute(
        "SELECT version FROM strategy_versions WHERE is_active=1 ORDER BY version")]


def _snapshot(conn: sqlite3.Connection, version: str) -> Dict[str, Any]:
    """做决定与做断言共用的一份只读快照(⛔ 别在两处各查一遍、各写一套判据)。"""
    row = _read_row(conn, version)
    if row is None:
        raise ValueError(f"目标库无 {version} 行 —— 本脚本只退役已在位的 advisory,不建行。")
    keys = row.keys()
    return {
        "rule_json": row["rule_json"],
        "sha": _sha(row["rule_json"]),
        "is_active": int(row["is_active"]),
        "activated_at": (row["activated_at"] if "activated_at" in keys else None),
        "act_log_rows": _activation_log_rows(conn),
        "active_versions": _active_versions(conn),
    }


# ══════════════════════════════════════════════════════════════════════════
# 变换 + 备份 + 写
# ══════════════════════════════════════════════════════════════════════════

def _apply_retire(rule_json: str, section: str, code: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """返回 `(新 rule_json, 被删条目)`;B3 本就不在 → `(原样, None)`。"""
    obj = json.loads(rule_json)
    adv = obj.get("k4_advisory")
    if not isinstance(adv, dict):
        raise ValueError("K4 行 rule_json 里没有 k4_advisory 节,结构与预期不符,拒绝改。")
    bucket = adv.get(section)
    if not isinstance(bucket, dict):
        raise ValueError(f"k4_advisory.{section} 不是对象,结构与预期不符,拒绝改。")
    if code not in bucket:
        return rule_json, None
    removed = bucket.pop(code)
    # ⚠ 与 `brain.save_version` 逐字节同口径(ensure_ascii=False + 默认分隔符)。
    return json.dumps(obj, ensure_ascii=False), removed


def _dump_advisory(rule_json: str, out: Path, tag: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    adv = json.loads(rule_json).get("k4_advisory", {})
    p = out / f"k4_advisory_{tag}.json"
    p.write_text(json.dumps(adv, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _double_backup(db_path: Path, backup_dir: Path) -> Tuple[Path, Path, float]:
    """`.backup`(在线一致快照)+ `cp -p`(裸拷)双备份,复刻既有部署体例。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    tag = f"b3retire-{_stamp()}"
    bak = backup_dir / f"{db_path.name}.bak-{tag}"
    cpbak = backup_dir / f"{db_path.name}.cpbak-{tag}"
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
    elapsed = time.time() - t0
    if integ != "ok":
        raise ValueError(f".backup 产物 integrity_check={integ},拒绝继续。")
    shutil.copy2(db_path, cpbak)
    return bak, cpbak, elapsed


# ══════════════════════════════════════════════════════════════════════════

def run(db_path: Path, payload_path: Path, *, confirm: bool, verify_only: bool,
        allow_unknown_base: bool, backup_dir: Optional[Path], dump_dir: Path) -> int:
    p = load_payload(payload_path)
    version, section, code = p["target_version"], p["section"], p["retire_code"]
    sha_before, sha_after = p["expected_rule_json_sha256_before"], p["expected_rule_json_sha256_after"]

    print(f"目标库 : {db_path}")
    print(f"payload: {payload_path}")
    print(f"任务   : 退役 {version}.k4_advisory.{section}.{code}(plan ⑯-I)")
    if not db_path.exists():
        print(f"错误:目标库不存在:{db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        before = _snapshot(conn, version)
        print(f"\n—— 写前只读快照 ——")
        print(f"  {version}.rule_json sha256 = {before['sha']}(len {len(before['rule_json'])})")
        print(f"  {version}.is_active       = {before['is_active']}   activated_at = {before['activated_at']!r}")
        print(f"  现役版本集合             = {before['active_versions']}")
        print(f"  strategy_activation_log  = {before['act_log_rows']} 行")

        new_rule, removed = _apply_retire(before["rule_json"], section, code)

        # —— 幂等态 2:已退役 ——
        if removed is None:
            print(f"\n✓ {code} 已不在 {section} 清单里 —— **无需变更**(幂等,0 改动)。")
            if before["sha"] != sha_after and not allow_unknown_base:
                print(f"  ⚠ 但当前 sha 与 payload 的 after-sha 不符:\n"
                      f"      当前 {before['sha']}\n      预期 {sha_after}\n"
                      f"    (B3 确已不在,但这个库的 K4 行还有别的差异;只读提示,未改任何东西。)",
                      file=sys.stderr)
            return _post_verify(conn, p, expect_sha=(sha_after if before["sha"] == sha_after else before["sha"]),
                                before=before, changed=False)

        # —— 基线核对(fail-closed)——
        if before["sha"] != sha_before:
            print(f"\n✗ 基线不符:库里 {version} 行 sha256 与 payload 的 before-sha 不同。", file=sys.stderr)
            print(f"    库里 {before['sha']}\n    预期 {sha_before}", file=sys.stderr)
            if not allow_unknown_base:
                print("    拒绝改动(fail-closed)。确认过差异无害再加 --allow-unknown-base。", file=sys.stderr)
                return 3
            print("    --allow-unknown-base:继续,但下方 after-sha 断言将按实际值放宽。", file=sys.stderr)

        # —— diff dump ——
        d_before = _dump_advisory(before["rule_json"], dump_dir, f"before-{_stamp()}")
        d_after = _dump_advisory(new_rule, dump_dir, f"after-{_stamp()}")
        print(f"\n—— 改动逐项 diff ——")
        print(f"  删除 k4_advisory.{section}[{code}] = {json.dumps(removed, ensure_ascii=False)}")
        exp_removed = p.get("expected_removed_entry")
        if exp_removed is not None and exp_removed != removed:
            print(f"  ⚠ 被删条目与 payload 声明不同(声明 {json.dumps(exp_removed, ensure_ascii=False)})",
                  file=sys.stderr)
            if not allow_unknown_base:
                return 3
        adv_new = json.loads(new_rule)["k4_advisory"]
        print(f"  {section} 剩余         = {list(adv_new.get(section, {}))}")
        print(f"  hard_cut(不动)       = {list(adv_new.get('hard_cut', {}))}")
        print(f"  intel_order(不动)     = {adv_new.get('intel_order')}")
        print(f"  rule_json len {len(before['rule_json'])} → {len(new_rule)}"
              f"(-{len(before['rule_json']) - len(new_rule)} 字节)")
        print(f"  rule_json sha {before['sha']}\n             → {_sha(new_rule)}")
        if not allow_unknown_base and _sha(new_rule) != sha_after:
            print(f"\n✗ 改后 sha 与 payload 的 after-sha 不符(预期 {sha_after}),拒绝写库。", file=sys.stderr)
            return 3
        print(f"  advisory 全文快照:{d_before}\n                    {d_after}")

        if verify_only:
            print("\n[--verify-only] 只读复核,未写库。")
            return 0
        if not confirm:
            print(f"\n[dry-run] 未带 --confirm,**未写库**。确认无误后:")
            print(f"    python {Path(__file__).name} --db {db_path} --confirm")
            return 0

        # —— 双备份 → 单事务写 ——
        bdir = backup_dir or db_path.parent
        bak, cpbak, elapsed = _double_backup(db_path, bdir)
        print(f"\n—— 双备份(写前)——")
        for f in (bak, cpbak):
            print(f"  {f}  {f.stat().st_size} bytes")
        print(f"  .backup 耗时 {elapsed:.2f}s,integrity=ok")

        with conn:  # 单事务:成功即 commit,异常自动 rollback
            cur = conn.execute(
                "UPDATE strategy_versions SET rule_json=? WHERE version=? AND is_active=0",
                (new_rule, version),
            )
            if cur.rowcount != 1:
                raise ValueError(
                    f"UPDATE 影响 {cur.rowcount} 行(期望 1)—— 可能 {version} 行 is_active 不是 0,已回滚。")
        print(f"\n✓ 已写入(单事务,1 行)。")
        return _post_verify(conn, p, expect_sha=_sha(new_rule), before=before, changed=True)
    finally:
        conn.close()


def _post_verify(conn: sqlite3.Connection, p: Dict[str, Any], *, expect_sha: str,
                 before: Dict[str, Any], changed: bool) -> int:
    """写后只读复核。**红线三条**:K4 仍 inert、activated_at 未变、激活流水未增行。"""
    version, section, code = p["target_version"], p["section"], p["retire_code"]
    after = _snapshot(conn, version)
    inv = p.get("invariants", {})
    checks = []

    checks.append((f"{version}.rule_json sha256 = {after['sha']}", after["sha"] == expect_sha))
    checks.append((f"{version}.is_active = {after['is_active']}(须 0,inert)",
                   after["is_active"] == int(inv.get("k4_is_active", 0))))
    checks.append((f"{version}.activated_at = {after['activated_at']!r}(须与写前一致)",
                   after["activated_at"] == before["activated_at"]))
    checks.append((f"现役版本集合 = {after['active_versions']}(须与写前一致)",
                   after["active_versions"] == before["active_versions"]))
    checks.append((f"strategy_activation_log = {after['act_log_rows']} 行(须与写前一致,不增行)",
                   after["act_log_rows"] == before["act_log_rows"]))

    adv = json.loads(after["rule_json"]).get("k4_advisory", {})
    checks.append((f"{section} = {list(adv.get(section, {}))}",
                   code not in adv.get(section, {})))
    want_hard = inv.get("hard_cut_untouched")
    if want_hard is not None:
        checks.append((f"hard_cut = {list(adv.get('hard_cut', {}))}(A2 红牌须仍在)",
                       list(adv.get("hard_cut", {})) == list(want_hard)))
    if inv.get("intel_order_untouched"):
        before_order = json.loads(before["rule_json"]).get("k4_advisory", {}).get("intel_order")
        checks.append((f"intel_order 未动 = {adv.get('intel_order')}",
                       adv.get("intel_order") == before_order))
    integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
    checks.append((f"PRAGMA integrity_check = {integ}", integ == "ok"))

    print(f"\n—— {'写后' if changed else '现状'}复核(只读)——")
    bad = 0
    for text, ok in checks:
        print(f"  {'✓' if ok else '✗'} {text}")
        bad += 0 if ok else 1
    if bad:
        print(f"\n✗ {bad} 条复核未通过 —— 立即人工核对(回滚绳 = 本次双备份)。", file=sys.stderr)
        return 5
    print("\n✓ 全部复核通过。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="K4 advisory B3 黄牌退役(plan V2-⑯-I,默认 dry-run)")
    ap.add_argument("--db", type=Path, required=True, help="目标 SQLite 库(生产:/opt/neckline/data/neckline.db)")
    ap.add_argument("--payload", type=Path, default=_DEFAULT_PAYLOAD, help=f"声明件(默认 {_DEFAULT_PAYLOAD.name})")
    ap.add_argument("--confirm", action="store_true", help="确认写库(不带则只演练)")
    ap.add_argument("--verify-only", action="store_true", help="只读复核,绝不写库")
    ap.add_argument("--allow-unknown-base", action="store_true",
                    help="库里 K4 行与 payload 声明的基线不符时仍继续(需人工先看过 diff)")
    ap.add_argument("--backup-dir", type=Path, default=None, help="双备份落点(默认 = 库同目录)")
    ap.add_argument("--dump-dir", type=Path, default=Path("/tmp/k4_b3_retire"),
                    help="advisory 全文 dump 落点(供 archive diff 用)")
    a = ap.parse_args()
    try:
        return run(a.db, a.payload, confirm=a.confirm, verify_only=a.verify_only,
                   allow_unknown_base=a.allow_unknown_base, backup_dir=a.backup_dir,
                   dump_dir=a.dump_dir)
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as e:
        print(f"错误(已中止):{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
