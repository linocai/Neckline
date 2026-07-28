#!/usr/bin/env python3
"""v1.2 仓位章程落库脚本(plan §五 v1.2-A.1,🔴 高危区:碰纪律章程 + 大脑版本表)。

**只落行,不激活**(`activate=False`)——把 v1.2 三仓制章程作为一个新的
`strategy_versions` 行写入,`is_active` 仍留在现役 K1,生产行为零变化。真正生效走
staged 步骤 2 的切换器 `scripts/activate_charter.py --confirm`(用户清仓 + 确认后跑)。

**单一事实源铁律(§3.8)**:v1.2 的 config **从 DB 读现役 K1 的 `rule["config"]`
复制一份**,只改三个仓位字段,其余逐字段原样——**绝不在本脚本手抄一份 config**(防
漂移)。三字段修订(§2.1 三仓制):
    · max_positions:      5     → 3     (注意力约束「只做 3 仓」)
    · single_cap:         20000 → 40000 (语义:违纪判定上限,非推荐值)
    · max_exposure_frac:  0.60  → 1.0   (满仓档;12 万 × 1.0 = 3×4 万,第三笔满档在边界)

version = `v1.2`(**系统 v 字头章程修订,绝不碰 K 字头**);rule 携 `lineage="K1"`
(内核血缘留痕,策略内核未改一字)。K1 行原样保留、不覆盖。幂等:重跑覆盖 v1.2 行同内容。

用法:
    python scripts/charter_v1_2.py            # 落 v1.2 行(activate=False),打印 diff
    python scripts/charter_v1_2.py --db /path/to/neckline.db   # 指定库(默认 settings.db_path)

前置安全护栏:若现役版本的 config 不是 K1 基线(single_cap=20000 / max_positions=5 /
max_exposure_frac=0.6),脚本**拒绝落行 + 非零退出**——防止在「章程已激活」或「现役非
K1」的库上误从错误来源复制。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.strategy import brain  # noqa: E402

# v1.2 章程三仓制修订:(字段, K1 基线旧值, v1.2 新值)。旧值仅作**来源核对护栏**,
# 新值是唯一要落的目标;绝不手抄整份 config(其余字段靠「复制 K1 config」保证)。
_CHARTER_FIELDS = [
    ("max_positions", 5, 3),
    ("single_cap", 20000.0, 40000.0),
    ("max_exposure_frac", 0.60, 1.0),
]

_CHANGELOG = (
    "策略内核血缘 = K1 未改一字,本行仅章程仓位字段修订(三仓制:max_positions 5→3 / "
    "single_cap 2万→4万〔违纪判定上限,非推荐值〕/ max_exposure_frac 0.6→1.0);"
    "系统 v 字头章程修订,不占 K 命名空间。"
)

_TOL = 1e-9


def _eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= _TOL
    except (TypeError, ValueError):
        return a == b


def land_charter(db_path: Path) -> int:
    active = brain.get_active(db_path=db_path)
    if active is None:
        print("错误:大脑无现役版本,无法读取 K1 基线 config 复制。", file=sys.stderr)
        return 1
    src_cfg = dict(active.rule.get("config", {}) or {})
    if not src_cfg:
        print(f"错误:现役版本 {active.version} 无 rule['config'],无法复制。", file=sys.stderr)
        return 1

    print(f"现役(复制来源)版本 = {active.version}(is_active=1)")

    # 来源核对护栏:现役 config 三字段必须是 K1 基线旧值,否则拒绝(防章程已激活/现役非 K1)。
    mismatched = [
        (f, old, src_cfg.get(f))
        for f, old, _new in _CHARTER_FIELDS
        if not _eq(src_cfg.get(f), old)
    ]
    if mismatched:
        print("错误:现役 config 不是 K1 基线,拒绝落行(可能章程已激活或现役非 K1):", file=sys.stderr)
        for f, old, cur in mismatched:
            print(f"    {f}: 期望 K1 基线 {old},实际现役 {cur}", file=sys.stderr)
        return 2

    # 从 K1 config 复制,只改三个仓位字段(其余逐字段原样,不手抄)。
    new_cfg = dict(src_cfg)
    for f, _old, new in _CHARTER_FIELDS:
        new_cfg[f] = new

    # 落行(activate=False):is_active 仍在 K1。version=v1.2(系统 v 字头,绝不碰 K)。
    brain.save_version(
        "v1.2",
        rule={"config": new_cfg, "lineage": "K1"},
        changelog=_CHANGELOG,
        activate=False,
        db_path=db_path,
    )

    # ---- diff + 现役断言 ----
    print("\nv1.2 章程 old(K1)→ new 逐字段 diff(仅三个仓位字段改动):")
    for f, old, new in _CHARTER_FIELDS:
        print(f"    {f:<20} {old} → {new}")
    print("\n其余字段 = K1 逐字段相同(靠复制保证):")
    for k in sorted(new_cfg):
        if k not in {f for f, _o, _n in _CHARTER_FIELDS}:
            print(f"    {k:<20} {new_cfg[k]}")

    active_after = brain.get_active(db_path=db_path)
    v12 = brain.get_version("v1.2", db_path=db_path)
    print(f"\n落库完成:v1.2 已写入(is_active={int(v12.is_active)}, activated_at={v12.activated_at})")
    print(f"现役断言:is_active 仍在 {active_after.version}(应为 K1,未激活 v1.2)")
    if active_after.version == "v1.2" or v12.is_active:
        print("错误:v1.2 竟成了现役 —— 违反 activate=False 约束!", file=sys.stderr)
        return 3
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="落 v1.2 三仓制章程行(activate=False,plan v1.2-A.1)")
    ap.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")
    args = ap.parse_args()
    db_path = args.db or settings.db_path
    print(f"目标库:{db_path}")
    return land_charter(db_path)


if __name__ == "__main__":
    raise SystemExit(main())
