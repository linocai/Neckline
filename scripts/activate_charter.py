#!/usr/bin/env python3
"""v1.2 章程切换器(plan §五 v1.2-A.2 / A.5 staged 步骤 2,🔴 高危区:大脑激活)。

**staged 生效铁律**:v1.2 三仓制章程激活 = 用户清掉现有持仓 + 明确确认后,才由本
脚本把 `is_active` 从 K1 移到 v1.2。激活前(K1 现役)所有行为按 K1 现役值执行。

三道闸(缺一不激活):
    1. **前置硬校验**:`positions` 表**无 `status='open'` 行**(用户已清仓)。有 open
       持仓 → 拒绝激活 + 打印待清仓清单 + 非零退出(生效时机铁律:清仓后才切)。
    2. **打印 old→new 逐字段 diff**:现役 config 与 v1.2 config 全字段对照,高亮变的
       三个仓位字段。
    3. **`--confirm` 才写库**:无 `--confirm` 只 dry-run 打印 diff、不写库;带 `--confirm`
       才 `brain.activate_version("v1.2")`。

**不做 API 端点**:策略大脑激活绝不暴露给客户端(§3.8 系统内核永不被客户端改),
只走命令行 + 用户在 ECS 权威库手动跑(能写该库的身份,即服务 `User=neckline`:
`sudo -u neckline .venv/bin/python scripts/activate_charter.py --confirm`)。

用法:
    python scripts/activate_charter.py                 # dry-run:校验 + diff,不写库
    python scripts/activate_charter.py --confirm       # 校验通过 + 激活 v1.2
    python scripts/activate_charter.py --db /path.db --confirm
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.sentinel.positions import load_open_positions  # noqa: E402
from neckline.strategy import brain  # noqa: E402

_TARGET_VERSION = "v1.2"
_TOL = 1e-9


def _eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= _TOL
    except (TypeError, ValueError):
        return a == b


def activate(db_path: Path, target: str, confirm: bool) -> int:
    # ---- 闸 1:前置硬校验(无 open 持仓)----
    open_positions = load_open_positions(db_path=db_path)
    if open_positions:
        print(
            f"错误:仍有 {len(open_positions)} 笔 status='open' 持仓,拒绝激活 "
            f"{target}(生效时机铁律:清仓后才切)。待清仓清单:",
            file=sys.stderr,
        )
        for p in open_positions:
            print(f"    id={p.id}  {p.ts_code}  买入 {p.buy_date} @¥{p.buy_price} × {p.qty} 股", file=sys.stderr)
        return 1

    active = brain.get_active(db_path=db_path)
    tgt = brain.get_version(target, db_path=db_path)
    if active is None:
        print("错误:大脑无现役版本(异常状态),拒绝激活。", file=sys.stderr)
        return 1
    if tgt is None:
        print(f"错误:目标版本 {target} 不存在(先跑 scripts/charter_v1_2.py 落行)。", file=sys.stderr)
        return 1
    if active.version == target:
        print(f"提示:{target} 已是现役版本,无需激活(is_active 已在 {target})。")
        return 0

    old_cfg = dict(active.rule.get("config", {}) or {})
    new_cfg = dict(tgt.rule.get("config", {}) or {})

    # ---- 闸 2:打印 old→new 逐字段 diff(高亮变的字段)----
    print(f"现役 {active.version} → 目标 {target} 章程 config 逐字段 diff:")
    all_keys = sorted(set(old_cfg) | set(new_cfg))
    changed = []
    for k in all_keys:
        ov, nv = old_cfg.get(k, "<缺>"), new_cfg.get(k, "<缺>")
        if not _eq(ov, nv):
            changed.append(k)
            print(f"  * {k:<20} {ov} → {nv}   ← 改动")
        else:
            print(f"    {k:<20} {ov}")
    print(f"\n改动字段:{changed or '(无)'}")
    print(f"目标 {target} 内核血缘 lineage = {tgt.rule.get('lineage', '(未标注)')}")

    # ---- 闸 3:--confirm 才写库 ----
    if not confirm:
        print(f"\n[dry-run] 未带 --confirm,不写库。现役仍为 {active.version}。")
        print(f"确认无误后加 --confirm 激活:python scripts/activate_charter.py --confirm")
        return 0

    result = brain.activate_version(target, db_path=db_path)
    active_after = brain.get_active(db_path=db_path)
    print(f"\n已激活:{target}(is_active={int(result.is_active)}, activated_at={result.activated_at})")
    print(f"现役断言:is_active 现在 = {active_after.version}")
    if active_after.version != target:
        print(f"错误:激活后现役竟不是 {target}!", file=sys.stderr)
        return 3
    # 唯一现役断言
    actives = [v.version for v in brain.list_versions(db_path=db_path) if v.is_active]
    if actives != [target]:
        print(f"错误:现役版本不唯一或不对:{actives}", file=sys.stderr)
        return 3
    print("提示:激活后请在策略线会话同步 STRATEGY_LAB §一「现役 = v1.2 章程行(内核血缘 K1)」。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="v1.2 章程切换器(plan v1.2-A.2,staged 步骤 2)")
    ap.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")
    ap.add_argument("--version", default=_TARGET_VERSION, help=f"要激活的版本(默认 {_TARGET_VERSION})")
    ap.add_argument("--confirm", action="store_true", help="确认写库激活(不带则只 dry-run)")
    args = ap.parse_args()
    db_path = args.db or settings.db_path
    print(f"目标库:{db_path}")
    return activate(db_path, args.version, args.confirm)


if __name__ == "__main__":
    raise SystemExit(main())
