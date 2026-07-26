#!/usr/bin/env python3
"""K4 机械半身行 bootstrap 到策略版本表(strategy_versions),🔴 高危区:碰权威库。

**背景**:K4「纯避坑 advisory」行是策略线 2026-07-25 的产物,只落在本地权威库
(`is_active=0`)。生产库此前只有 K1 → v1.3 候选管线 `intel_candidates.load_k4_sections`
读不到 K4 的 `k4_advisory.hard_cut` 分区 → 全体命中降级为 `avoid_flag`(打标不拦),
K4 安检在生产上空转。本脚本把 K4 行**逐字节原样**搬进目标库,让 `hard_cut` 真正生效。

**本脚本只做搬运,不改一个字**(§3.8 单一事实源 / 本次任务边界):
  · rule_json / changelog / metrics_json / created_at 从 `bootstrap_k4_payload.json`
    读入,**sha256 在脚本内硬校验**(`_EXPECTED_SHA256`)——payload 被改一字节即拒绝运行。
  · `is_active` 恒写 0(inert 行);**绝不触碰任何其它行的 is_active**(现役仍唯一在 K1)。
  · **绝不用 `brain.save_version`**:它 `json.dumps` 会重排/重序列化 rule_json 破坏字节
    一致,且 `INSERT OR REPLACE` 盲覆盖破坏本脚本要求的「内容不同则拒绝」幂等语义。
    故本脚本走 stdlib `sqlite3` 原始 SQL,携带 payload 原文本入库。

**幂等语义(三态)**:
  1. 目标库无 K4 行            → INSERT(`--commit` 才真写;缺 `--commit` 为 dry-run)。
  2. 目标库 K4 行已在且逐字节同 → no-op(报告「已在位且一致」,exit 0)。
  3. 目标库 K4 行在但内容不同   → **打印逐字段差异 + 拒绝覆盖 + 非零退出**(交人判断)。

**为何 stdlib-only、不 import neckline**:目标库文件属 `neckline:neckline`,写库须以
`neckline` 身份跑(`sudo -u neckline .venv/bin/python scripts/bootstrap_k4.py --db
/opt/neckline/data/neckline.db --commit`);stdlib-only 让脚本从任意工作目录都能跑、不
依赖包路径。功能验证(load_k4_sections 是否不再降级)是另一步只读检查,不在本脚本内。

用法:
    python scripts/bootstrap_k4.py --db /path/neckline.db            # dry-run(默认,不写)
    python scripts/bootstrap_k4.py --db /path/neckline.db --commit   # 校验通过则真写
    python scripts/bootstrap_k4.py --db /path/neckline.db --verify-only  # 只读复核已在位的 K4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

# —— 逐字节校验闸(源自本地权威库 K4 行的 utf-8 sha256,byte-exact,无尾换行)——————————
# payload 文件的三大字段被改一字节,sha256 即变 → 脚本拒绝运行(禁自造/精简/重排)。
_EXPECTED_SHA256 = {
    "rule_json":    "b4b1f4984b119f2a137a97006dbb357aaa7a770c86fac7a6ecb87d0a7c151a93",
    "changelog":    "7dd3035fb29c0b04c9f137606cb46aa18b11c64e259037d87a2f2b5109d9fed9",
    "metrics_json": "c5e8231c0f339410410eb63df572befd06f09f09da10d072438b4a228d48ac7c",
}
_TARGET_VERSION = "K4"
_DEFAULT_PAYLOAD = Path(__file__).resolve().parent / "bootstrap_k4_payload.json"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_payload(path: Path) -> Dict[str, object]:
    """读 payload JSON 并对三大字段做 sha256 硬校验(fail-closed)。任一不符 → 抛异常。"""
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    if p.get("version") != _TARGET_VERSION:
        raise ValueError(f"payload version={p.get('version')!r},应为 {_TARGET_VERSION!r}")
    if int(p.get("is_active", -1)) != 0:
        raise ValueError(f"payload is_active={p.get('is_active')!r},K4 必须为 inert 行(0)")
    for field, expected in _EXPECTED_SHA256.items():
        got = _sha(str(p[field]))
        if got != expected:
            raise ValueError(
                f"payload 字段 {field} sha256 校验失败(payload 被改动?):\n"
                f"    期望 {expected}\n    实际 {got}"
            )
    return p


def _read_k4(conn: sqlite3.Connection) -> Optional[Tuple]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(strategy_versions)")}
    has_act = "activated_at" in cols
    sel = "version, created_at, rule_json, changelog, metrics_json, is_active" + (
        ", activated_at" if has_act else ""
    )
    return conn.execute(
        f"SELECT {sel} FROM strategy_versions WHERE version=?", (_TARGET_VERSION,)
    ).fetchone()


def _diff_existing(p: Dict[str, object], row: Tuple) -> list:
    """逐字段比对已存在的 K4 行与 payload;返回差异描述列表(空=完全一致)。"""
    ver, created_at, rule_json, changelog, metrics_json, is_active = row[:6]
    diffs = []

    def cmp(name, cur, want):
        if cur != want:
            if isinstance(cur, str) and isinstance(want, str):
                diffs.append(
                    f"  * {name}: 现库 sha256={_sha(cur)} (len {len(cur)}) "
                    f"≠ payload sha256={_sha(want)} (len {len(want)})"
                )
            else:
                diffs.append(f"  * {name}: 现库={cur!r} ≠ payload={want!r}")

    cmp("created_at", created_at, p["created_at"])
    cmp("rule_json", rule_json, p["rule_json"])
    cmp("changelog", changelog, p["changelog"])
    cmp("metrics_json", metrics_json, p["metrics_json"])
    if int(is_active) != 0:
        diffs.append(f"  * is_active: 现库={is_active} ≠ 期望 0(K4 必须 inert)")
    return diffs


def _post_verify(conn: sqlite3.Connection, p: Dict[str, object]) -> int:
    """搬运后只读复核:K4 在位且 rule_json sha256 一致、is_active=0、ACTIVE 唯一 K1、
    integrity_check ok。返回 0=全过,非 0=有异常。"""
    print("\n—— 搬运后复核(只读)——")
    row = _read_k4(conn)
    if row is None:
        print("错误:复核时 K4 行竟不在位!", file=sys.stderr)
        return 4
    rule_json, is_active = row[2], row[5]
    rj_sha = _sha(rule_json)
    ok_sha = rj_sha == _EXPECTED_SHA256["rule_json"]
    print(f"  K4.rule_json sha256 = {rj_sha}  {'✓ 与本地逐字节一致' if ok_sha else '✗ 不一致!'}")
    print(f"  K4.is_active        = {is_active}  {'✓' if int(is_active) == 0 else '✗ 应为 0!'}")

    actives = [r[0] for r in conn.execute(
        "SELECT version FROM strategy_versions WHERE is_active=1 ORDER BY version"
    )]
    ok_active = actives == ["K1"]
    print(f"  ACTIVE 版本集合     = {actives}  {'✓ 唯一 K1' if ok_active else '✗ 现役非唯一 K1!'}")

    integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
    ok_integ = integ == "ok"
    print(f"  PRAGMA integrity    = {integ}  {'✓' if ok_integ else '✗'}")

    print("\n  strategy_versions 全表:")
    for r in conn.execute(
        "SELECT version, is_active, created_at, length(rule_json) "
        "FROM strategy_versions ORDER BY created_at"
    ):
        flag = " <== ACTIVE" if r[1] else ""
        print(f"    {r[0]:<5} is_active={r[1]} created={r[2]} rule_len={r[3]}{flag}")

    return 0 if (ok_sha and int(is_active) == 0 and ok_active and ok_integ) else 5


def run(db_path: Path, payload_path: Path, commit: bool, verify_only: bool) -> int:
    print(f"目标库 : {db_path}")
    print(f"payload: {payload_path}")
    p = load_payload(payload_path)
    print(f"payload sha256 三字段校验通过(rule_json/changelog/metrics_json);"
          f"version={p['version']} created_at={p['created_at']} is_active={p['is_active']}")

    if not db_path.exists():
        print(f"错误:目标库不存在:{db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=15000")  # 服务在跑,若瞬时占写锁则等待,不硬失败
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "strategy_versions" not in tables:
            print("错误:目标库无 strategy_versions 表(本脚本不建表,请先正常初始化)。", file=sys.stderr)
            return 1

        if verify_only:
            return _post_verify(conn, p)

        existing = _read_k4(conn)
        if existing is not None:
            diffs = _diff_existing(p, existing)
            if not diffs:
                print(f"\n✓ 目标库已有 K4 行且逐字节与 payload 一致 —— no-op(幂等)。")
                return _post_verify(conn, p)
            print("\n✗ 目标库已有 K4 行但内容与 payload 不同,拒绝覆盖(交人判断):", file=sys.stderr)
            for d in diffs:
                print(d, file=sys.stderr)
            print("  (本脚本绝不覆盖已存在的不同内容;如确需替换请人工核对后处理。)", file=sys.stderr)
            return 3

        # —— K4 不在位:INSERT(is_active=0, activated_at=payload 值=NULL)——
        if not commit:
            print("\n[dry-run] 目标库无 K4 行。带 --commit 将 INSERT 如下 inert 行:")
            print(f"    version=K4 is_active=0 activated_at={p['activated_at']} "
                  f"created_at={p['created_at']} rule_len={len(str(p['rule_json']))}")
            print("    未带 --commit,不写库。")
            return 0

        has_act = "activated_at" in {r[1] for r in conn.execute("PRAGMA table_info(strategy_versions)")}
        if has_act:
            conn.execute(
                "INSERT INTO strategy_versions "
                "(version, created_at, rule_json, changelog, metrics_json, is_active, activated_at) "
                "VALUES (?,?,?,?,?,0,?)",
                (p["version"], p["created_at"], p["rule_json"], p["changelog"],
                 p["metrics_json"], p["activated_at"]),
            )
        else:
            conn.execute(
                "INSERT INTO strategy_versions "
                "(version, created_at, rule_json, changelog, metrics_json, is_active) "
                "VALUES (?,?,?,?,?,0)",
                (p["version"], p["created_at"], p["rule_json"], p["changelog"], p["metrics_json"]),
            )
        conn.commit()
        print("\n✓ 已 INSERT K4 行(is_active=0)。")
        return _post_verify(conn, p)
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="K4 机械半身行 bootstrap 到 strategy_versions(默认 dry-run)")
    ap.add_argument("--db", type=Path, required=True, help="目标 SQLite 库路径(生产:/opt/neckline/data/neckline.db)")
    ap.add_argument("--payload", type=Path, default=_DEFAULT_PAYLOAD, help="K4 payload JSON(默认脚本同目录 bootstrap_k4_payload.json)")
    ap.add_argument("--commit", action="store_true", help="确认写库(不带则仅 dry-run 报告将做什么)")
    ap.add_argument("--verify-only", action="store_true", help="只读复核已在位的 K4 行,不写库")
    args = ap.parse_args()
    try:
        return run(args.db, args.payload, args.commit, args.verify_only)
    except (ValueError, OSError, sqlite3.Error) as e:  # fail-closed:任何异常先停,不连环补救
        print(f"错误(已中止,未写库):{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
