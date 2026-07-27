#!/usr/bin/env python3
"""章程切换器(plan §五 v1.2-A.2 / v1.3-①-E / v1.3-⑦-E staged 步骤 2,🔴 高危区:大脑激活)。

**staged 生效铁律**:章程激活 = 用户清掉现有持仓 + 明确确认后,才由本脚本把 `is_active`
从 K1 移到目标章程行。激活前(K1 现役)所有行为按 K1 现役值执行。**默认目标 = `v1.3`**
(v1.2 合并进 v1.3 发布,对外版号跳 v1.3)。

⚠ **目标闸是白名单,不是黑名单**(2026-07-27 独立审计 🟡-2 修复):原实现只黑名单拒
`v1.2`,审计实测 `--target K2 --confirm` 在清仓后**真能把废弃研究臂激活成现役章程**
(exit=0、`is_active` 变 K2)——K2/K4 的 config 是 K1 旧值(回落 5%/2 万/5 仓),激活后
entry-suggestion / 哨兵 / 周复盘全按废弃口径跑,`reviews.strategy_version` 还会把周判归到
K2,静默且全链路生效。现改为**白名单 `_ALLOWED_TARGETS`**:名单外一律硬拒(含 K1/K2/K4 等
研究臂与过时的 v1.2),且**凡激活必做核心值核对**(原来只在 `target=="v1.3"` 时核对)。
研究臂(K 字头)永远不该经本脚本激活——它们是策略线档案,不是系统线章程。

四道闸(缺一不激活):
    1. **目标合法性(白名单)**:`--target` 必须在 `_ALLOWED_TARGETS` 内,否则硬拒 + exit 2。
    2. **前置硬校验**:`positions` 表**无 `status='open'` 行**(用户已清仓)。有 open
       持仓 → 拒绝激活 + 打印待清仓清单 + 非零退出(生效时机铁律:清仓后才切)。
    3. **打印 old→new 逐字段 diff** + **核心值核对(凡激活必做)**:现役 config 与目标 config
       全字段对照高亮改动;再按 `_CORE_EXPECTATIONS[target]` 逐项核对(防激活到错误/未改的行)。
    4. **`--confirm` 才写库**:无 `--confirm` 只 dry-run 打印 diff、不写库;带 `--confirm`
       才 `brain.activate_version(target)`。

**不做 API 端点**:策略大脑激活绝不暴露给客户端(§3.8 系统内核永不被客户端改),
只走命令行 + 用户在 ECS 权威库手动跑(能写该库的身份,即服务 `User=neckline`:
`sudo -u neckline .venv/bin/python scripts/activate_charter.py --target v1.3 --confirm`)。

用法:
    python scripts/activate_charter.py                          # dry-run:校验 + diff(默认目标 v1.3)
    python scripts/activate_charter.py --confirm                # 校验通过 + 激活 v1.3
    python scripts/activate_charter.py --target v1.3 --confirm  # 显式目标
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

_TARGET_VERSION = "v1.3"

# —— 闸 1:目标白名单(审计 🟡-2;**加版本时必须同时给它一条 `_CORE_EXPECTATIONS`**)——
# 只有系统线 v 字头**现行**章程行可被激活。名单外一律硬拒:K 字头研究臂(K1 现役是历史
# 既成事实,不经本脚本;K2/K3/K4 是已否决/参考档)、过时的 v1.2(回落 5%/hold=5,已被 v1.3
# 取代,保留不删但永不激活)、以及任何 typo/复制错的串。
_ALLOWED_TARGETS = ("v1.3",)

# —— 闸 3:核心值核对(**凡激活必做**,不再只对 v1.3 做)。{版本: {config 键: 期望值}} ——
# 目的是「防激活到错误/未改的行」:即使目标在白名单里,只要它的退出/仓位核心值不是章程
# 拍板的那几个数,就说明这行没改对(或被谁改坏了),硬拒。白名单里的每个版本都必须在此
# 有一条,否则 `_check_core_values` 直接拒绝(结构性防止「加了白名单忘了加核对」)。
_CORE_EXPECTATIONS = {
    "v1.3": {
        "take_profit_retrace": 0.08,          # 回落止盈 8%(v1.3 退出规则改革)
        "max_hold_days": 5,                   # 非浮盈单时间退出档
        "max_hold_days_profit": 15,           # 浮盈单硬上限
        "time_exit_only_if_unprofitable": True,
        "stop_pct": 0.05,                     # -5% 止损不变(§2.1 第 1 条)
        "single_cap": 40000.0,                # 三仓章程:违纪判定上限 4 万
        "max_positions": 3,                   # 三仓
    },
}
_TOL = 1e-9


def _eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= _TOL
    except (TypeError, ValueError):
        return a == b


def _check_core_values(target: str, new_cfg: dict) -> int:
    """闸 3 的核心值核对(**凡激活必做**,审计 🟡-2)。返回 0=通过、2=拒绝(并已打印原因)。"""
    expected = _CORE_EXPECTATIONS.get(target)
    if expected is None:
        # 结构性护栏:白名单加了版本却忘了加核对表 → 宁可拒绝也不放行未核对的激活。
        print(
            f"错误:目标 {target} 在白名单内但缺 `_CORE_EXPECTATIONS` 核对项——拒绝激活"
            f"(加白名单必须同时加核对表,见脚本头注释)。",
            file=sys.stderr,
        )
        return 2
    bad = [(k, new_cfg.get(k, "<缺>"), v) for k, v in expected.items() if not _eq(new_cfg.get(k, "<缺>"), v)]
    if bad:
        print(f"错误:目标 {target} 核心值核对未通过(疑似激活到错误/未改的行)——拒绝激活:", file=sys.stderr)
        for k, got, want in bad:
            print(f"    {k}: 实际 {got},期望 {want}", file=sys.stderr)
        return 2
    print(f"核心值核对通过({target}):" + "、".join(f"{k}={new_cfg.get(k)}" for k in expected))
    return 0


def activate(db_path: Path, target: str, confirm: bool) -> int:
    # ---- 闸 1:目标合法性——**白名单**(审计 🟡-2:黑名单挡不住 K2/K4 等废弃研究臂)----
    if target not in _ALLOWED_TARGETS:
        print(
            f"错误:拒绝激活 {target}——不在可激活白名单 {list(_ALLOWED_TARGETS)} 内。\n"
            f"      K 字头(K1/K2/K3/K4)是策略线研究臂/参考档,**永不经本脚本激活**;\n"
            f"      v1.2 是过时章程行(回落 5%/hold=5,已被 v1.3 取代,保留不删但永不激活)。\n"
            f"      如确要激活现行章程,用 --target v1.3。",
            file=sys.stderr,
        )
        return 2

    # ---- 闸 2:前置硬校验(无 open 持仓)----
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

    # ---- 闸 3:打印 old→new 逐字段 diff(高亮变的字段)----
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

    # ---- 闸 3b:核心值核对(**凡激活必做**,审计 🟡-2;原来只在 target=="v1.3" 时做)----
    rc = _check_core_values(target, new_cfg)
    if rc:
        return rc

    # ---- 闸 4:--confirm 才写库 ----
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
    print(f"提示:激活后请在策略线会话同步 STRATEGY_LAB §一「现役 = {target} 章程行(内核血缘 K1)」。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="章程切换器(plan v1.3-①-E / ⑦-E,staged 步骤 2)")
    ap.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")
    ap.add_argument("--target", "--version", dest="target", default=_TARGET_VERSION,
                    help=f"要激活的版本(默认 {_TARGET_VERSION};--version 为向后兼容别名)")
    ap.add_argument("--confirm", action="store_true", help="确认写库激活(不带则只 dry-run)")
    args = ap.parse_args()
    db_path = args.db or settings.db_path
    print(f"目标库:{db_path}")
    return activate(db_path, args.target, args.confirm)


if __name__ == "__main__":
    raise SystemExit(main())
