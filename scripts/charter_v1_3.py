#!/usr/bin/env python3
"""v1.3 退出规则章程落库脚本(plan §五 v1.3-①-E,🔴 高危区:碰纪律章程 + 大脑版本表)。

**只落行,不激活**(`activate=False`)——把 v1.3 章程(三仓制 + 退出规则改革)作为一个新
`strategy_versions` 行写入,`is_active` 仍留在现役 K1,生产行为零变化。真正生效走 staged
步骤 2 的切换器 `scripts/activate_charter.py --target v1.3 --confirm`(用户清仓 + 确认后跑)。

**单一事实源铁律(§3.8)**:v1.3 的 config **从 DB 读现役 K1 的 `rule["config"]` 复制一份**,
只改六个字段,其余逐字段原样——**绝不在本脚本手抄一份 config**(防漂移)。六字段修订:
    仓位三字段(承 v1.2 三仓制,§2.1 第 3 条):
    · max_positions:      5     → 3      (注意力约束「只做 3 仓」)
    · single_cap:         20000 → 40000  (语义:违纪判定上限,非推荐值)
    · max_exposure_frac:  0.60  → 1.0    (满仓档;12 万 × 1.0 = 3×4 万)
    退出三字段(v1.3 新,§2.1 第 2 条;`max_hold_days` 保持 5=非浮盈时间退出档、`stop_pct`
    保持 0.05=−5% 止损不变):
    · take_profit_retrace:          0.05  → 0.08  (回落止盈 5%→8%)
    · time_exit_only_if_unprofitable: 缺省 → True  (时间退出仅对非浮盈单)
    · max_hold_days_profit:          缺省 → 15    (浮盈豁免时间退出硬上限)

version = `v1.3`(**系统 v 字头章程修订,绝不碰 K 字头**);rule 携 `lineage="K1"`(内核
血缘留痕,策略内核未改一字)。K1 行原样保留、不覆盖;⚠ **库里过时的 `v1.2` 章程行(回落
5%/hold=5)保留不删不激活**(append-only 归因链,删行破坏审计;v1.3 changelog 注明取代它)。

用法:
    python scripts/charter_v1_3.py            # 落 v1.3 行(activate=False),打印 diff
    python scripts/charter_v1_3.py --db /path/to/neckline.db   # 指定库(默认 settings.db_path)

前置安全护栏:若现役版本的 config 不是 K1 基线(single_cap=20000 / max_positions=5 /
max_exposure_frac=0.6,且退出两新字段未设=K1 从未设),脚本**拒绝落行 + 非零退出**——防
在「章程已激活」或「现役非 K1」的库上误从错误来源复制。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.strategy import brain  # noqa: E402

# v1.3 章程六字段修订:(字段, K1 基线旧值/缺省, v1.3 新值)。旧值仅作**来源核对护栏**;
# 退出两新字段 K1 config 里根本没有(缺省 = None/False),故护栏用 src.get(f, old) 容忍缺省。
# 绝不手抄整份 config(其余字段靠「复制 K1 config」保证)。
_CHARTER_FIELDS = [
    ("max_positions", 5, 3),
    ("single_cap", 20000.0, 40000.0),
    ("max_exposure_frac", 0.60, 1.0),
    ("take_profit_retrace", 0.05, 0.08),
    ("time_exit_only_if_unprofitable", False, True),
    ("max_hold_days_profit", None, 15),
]

# **风险登记(不得删、不许精简,原样入 charter changelog + §2.1)**——见 plan §五 v1.3-①-E。
_CHANGELOG = (
    "内核血缘=K1 未改一字;本行=v1.2 三仓制 + v1.3 退出规则改革(回落止盈 5%→8% + 浮盈单"
    "豁免时间退出、硬上限 15 个交易日 + 非浮盈单 D5 收盘扣双边费净浮盈≤0 次日退出;-5% 止损"
    "不变、max_hold_days=5 保持=非浮盈时间退出档);系统 v 字头章程修订,不占 K 命名空间。"
    "**取代 v1.2 章程行,勿激活 v1.2**(库里那行过时=回落 5%/hold=5,保留不删作 append-only "
    "归因链留痕,永不激活)。"
    "风险登记:② 回落 8% 系 H9 V0 网格观察免测采纳(六格网格唯一同时改良全期与 2026 的格);"
    "③④ 浮盈豁免组合版(0.08×浮盈豁免)未整体回测(最接近的 H9-V3 差 724 元未过 2026 生存"
    "门禁);用户知情行使决策权越线采纳(2026-07-25),风险已当面告知——组合退出规则的真实"
    "期望未经样本外验证,误差主要体现在「刚好在盈亏平衡线附近」的单子判向。证据链:"
    "research/h9_exit_reform.md + research/winners_anatomy.md。"
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

    # 来源核对护栏:现役 config 六字段必须是 K1 基线旧值/缺省,否则拒绝(防章程已激活/现役非 K1)。
    # 退出两新字段 K1 从未设,src.get(f, old) 缺省即取 old → 通过;若已被设成别的值 → 拒绝。
    mismatched = [
        (f, old, src_cfg.get(f, old))
        for f, old, _new in _CHARTER_FIELDS
        if not _eq(src_cfg.get(f, old), old)
    ]
    if mismatched:
        print("错误:现役 config 不是 K1 基线,拒绝落行(可能章程已激活或现役非 K1):", file=sys.stderr)
        for f, old, cur in mismatched:
            print(f"    {f}: 期望 K1 基线 {old},实际现役 {cur}", file=sys.stderr)
        return 2

    # 从 K1 config 复制,只改六个字段(其余逐字段原样,不手抄)。
    new_cfg = dict(src_cfg)
    for f, _old, new in _CHARTER_FIELDS:
        new_cfg[f] = new

    # 落行(activate=False):is_active 仍在 K1。version=v1.3(系统 v 字头,绝不碰 K)。
    brain.save_version(
        "v1.3",
        rule={"config": new_cfg, "lineage": "K1"},
        changelog=_CHANGELOG,
        activate=False,
        db_path=db_path,
    )

    # ---- diff + 现役断言 ----
    print("\nv1.3 章程 old(K1)→ new 逐字段 diff(仅六个字段改动):")
    for f, old, new in _CHARTER_FIELDS:
        print(f"    {f:<32} {old} → {new}")
    print("\n其余字段 = K1 逐字段相同(靠复制保证):")
    for k in sorted(new_cfg):
        if k not in {f for f, _o, _n in _CHARTER_FIELDS}:
            print(f"    {k:<32} {new_cfg[k]}")

    active_after = brain.get_active(db_path=db_path)
    v13 = brain.get_version("v1.3", db_path=db_path)
    # 关键校验:退出规则核心值落对(回落 8% + 浮盈硬上限 15 + 条件时间退出开)。
    cfg13 = dict(v13.rule.get("config", {}))
    assert _eq(cfg13.get("take_profit_retrace"), 0.08), "回落止盈未落成 0.08!"
    assert cfg13.get("time_exit_only_if_unprofitable") is True, "条件时间退出未开!"
    assert cfg13.get("max_hold_days_profit") == 15, "浮盈硬上限未落成 15!"
    assert _eq(cfg13.get("max_hold_days"), 5) and _eq(cfg13.get("stop_pct"), 0.05), "非浮盈档/止损被改!"
    print(f"\n落库完成:v1.3 已写入(is_active={int(v13.is_active)}, activated_at={v13.activated_at})")
    print(f"  退出核对:take_profit_retrace={cfg13.get('take_profit_retrace')} / "
          f"time_exit_only_if_unprofitable={cfg13.get('time_exit_only_if_unprofitable')} / "
          f"max_hold_days_profit={cfg13.get('max_hold_days_profit')} / "
          f"max_hold_days={cfg13.get('max_hold_days')}(非浮盈档)/ stop_pct={cfg13.get('stop_pct')}")
    print(f"现役断言:is_active 仍在 {active_after.version}(应为 K1,未激活 v1.3)")
    if active_after.version != "K1" or v13.is_active:
        print("错误:v1.3 竟成了现役 —— 违反 activate=False 约束!", file=sys.stderr)
        return 3
    # v1.2 行留痕断言(若存在):保留、仍不激活。
    v12 = brain.get_version("v1.2", db_path=db_path)
    if v12 is not None and v12.is_active:
        print("错误:过时的 v1.2 章程行竟处于激活态 —— 必须保留不激活!", file=sys.stderr)
        return 3
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="落 v1.3 退出规则章程行(activate=False,plan v1.3-①-E)")
    ap.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")
    args = ap.parse_args()
    db_path = args.db or settings.db_path
    print(f"目标库:{db_path}")
    return land_charter(db_path)


if __name__ == "__main__":
    raise SystemExit(main())
