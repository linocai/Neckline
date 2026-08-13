#!/usr/bin/env python3
"""v1.3.3 章程落库脚本(拆墙:创业板/科创板不再禁买;🔴 高危区:碰纪律章程 + 大脑版本表)。

**只落行,不激活**(`activate=False`)——把 v1.3.3 章程作为一个新 `strategy_versions` 行
写入,`is_active` 仍留在落行前那一版(生产=v1.3),生产行为零变化。真正生效走切换器
`scripts/activate_charter.py --target v1.3.3 --confirm`(用户确认 + 无 open 持仓后跑)。

**单一事实源铁律(§3.8)**:v1.3.3 的 config **从 DB 读 `v1.3` 行的 `rule["config"]` 复制
一份,只改一个字段**,其余逐字段原样——**绝不在本脚本手抄一份 config**(防漂移)。
唯一修订:

    · forbid_high_elasticity:  True → False   (创业板/科创板「不许碰」墙下掉)

**来源刻意按版本名 `v1.3` 读,不读 `get_active()`**:本地权威库现役仍是 K1(v1.3 行 inert)、
生产现役已是 v1.3——按版本名读,两边拿到的是同一份 config,脚本行为与"哪一行恰好现役"解耦
(若改读 get_active(),本地会从 K1 复制出一份缺退出三字段的错行)。

version = `v1.3.3`(**系统 v 字头章程修订,绝不碰 K 字头**);rule 携 `lineage="K1"`(内核
血缘留痕,策略内核未改一字)。v1.3 / K1 / K4 / v1.2 等既有行原样保留、不覆盖、不激活。

用法:
    python scripts/charter_v1_3_3.py            # 落 v1.3.3 行(activate=False),打印 diff
    python scripts/charter_v1_3_3.py --db /path/to/neckline.db   # 指定库(默认 settings.db_path)

前置安全护栏(任一不满足即拒绝落行 + 非零退出):
    · `v1.3` 行必须存在,且其 config 七项核心值 = v1.3 章程拍板值(回落 8% / 两档时间退出 /
      -5% 止损 / 三仓 / 4 万 / 敞口 1.0)——防从被改坏或错误的行复制;
    · `v1.3` 的 `forbid_high_elasticity` 必须仍是 `True`——若已是 False,说明来源行已被人动过
      (本脚本要表达的"拆墙"就无从落笔),硬拒。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.strategy import brain  # noqa: E402

_SOURCE_VERSION = "v1.3"
_TARGET_VERSION = "v1.3.3"

# 唯一修订字段:(字段, 源 v1.3 期望旧值, v1.3.3 新值)。绝不手抄整份 config。
_CHARTER_FIELDS = [
    ("forbid_high_elasticity", True, False),
]

# 来源核对护栏:`v1.3` 行必须是 v1.3 章程拍板的那七个数(与 activate_charter._CORE_EXPECTATIONS
# 的 v1.3 条目同源同值——那里是"激活前核对",这里是"复制前核对",两道方向相反的闸)。
_SOURCE_EXPECTATIONS = {
    "take_profit_retrace": 0.08,
    "max_hold_days": 5,
    "max_hold_days_profit": 15,
    "time_exit_only_if_unprofitable": True,
    "stop_pct": 0.05,
    "single_cap": 40000.0,
    "max_positions": 3,
    "max_exposure_frac": 1.0,
}

# **风险登记(不得删、不许精简,原样入 charter changelog)**——用户 2026-07-27 拍板拆墙。
_CHANGELOG = (
    "承 v1.3 全部字段,**只改一个字段**:forbid_high_elasticity True → False——创业板/科创板"
    "(高弹题材)的「不许碰」墙下掉。内核血缘=K1 未改一字;系统 v 字头章程修订,不占 K 命名空间。"
    "① **用户 2026-07-27 拍板拆墙**:用户实操域就是高弹板块(五常驻里创新药/机器人/储能全是"
    "创业板/科创板大户),而 v1.3 起的候选情报管线(需求 5)已刻意含全板块 MAIN/GEM/STAR——但"
    "`forbid_high_elasticity=True` 仍在**四处**生效(周复盘违纪判定 review/reconcile.py、问询台"
    "api/inquiry.py、自选体检 report/watchlist_check.py、回测 entry mask strategy/momentum.py),"
    "造成同一系统两套口径:用户每买一笔创业板都会被周复盘误标违纪。本行消除该分裂。"
    "② **风险登记(不得删):本 config 与任何回测基线都不同。** K1 六年回测里高弹是被**剔掉**的"
    "(P6 禁高弹 = 主板 only),当年作为**风控**采纳(§1.3 第二死因:高弹票 20% 涨跌幅与 -5% 止损"
    "不匹配、易一字跌停买卖不进出);把它关掉 = 入场域扩到**从未被该 config 回测过**的范围,"
    "**冻结基线 N=1288 / total_return −20.53% / final_equity 95361.4988 只对 K1 那套 config 成立,"
    "不适用于本行**。用户知情采纳(2026-07-27),风险已当面告知。"
    "③ **拆墙后高弹的风险改由 -5% 止损 + 三仓纪律(max_positions=3 / single_cap=4 万违纪上限 /"
    " 回落止盈 8% / 敞口 1.0)+ K4 安检(hard_cut 拦、avoid_flag 标)承担,不再由「不许碰」承担。**"
    "④ 这是用户 2026-07-25「候选生成域刻意含高弹、止损频率代价已在策略线审计定价」"
    "(§2.3 候选语义变更拍板)的自然延伸——那次只把**候选生成域**解耦出 K1 主板 only,本次把"
    "**纪律判定域**一并对齐,两边自此同一口径。"
    "证据链:§1.3 第二死因 / §2.3 候选语义变更拍板 / research/stage1_report.md(P6 当年采纳理由)。"
)

_TOL = 1e-9


def _eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= _TOL
    except (TypeError, ValueError):
        return a == b


def land_charter(db_path: Path) -> int:
    src = brain.get_version(_SOURCE_VERSION, db_path=db_path)
    if src is None:
        print(f"错误:来源版本 {_SOURCE_VERSION} 不存在(先跑 scripts/charter_v1_3.py 落行)。", file=sys.stderr)
        return 1
    src_cfg = dict(src.rule.get("config", {}) or {})
    if not src_cfg:
        print(f"错误:{_SOURCE_VERSION} 行无 rule['config'],无法复制。", file=sys.stderr)
        return 1

    active_before = brain.get_active(db_path=db_path)
    print(f"复制来源版本 = {_SOURCE_VERSION}(is_active={int(src.is_active)});"
          f"当前现役 = {active_before.version if active_before else '(无)'}")

    # ---- 护栏 1:来源七项核心值必须是 v1.3 拍板值(防从被改坏/错误的行复制)----
    bad = [(k, src_cfg.get(k, "<缺>"), v) for k, v in _SOURCE_EXPECTATIONS.items()
           if not _eq(src_cfg.get(k, "<缺>"), v)]
    if bad:
        print(f"错误:来源 {_SOURCE_VERSION} 核心值核对未通过,拒绝落行(疑似来源行被改坏):", file=sys.stderr)
        for k, got, want in bad:
            print(f"    {k}: 实际 {got},期望 {want}", file=sys.stderr)
        return 2
    print("来源核心值核对通过(" + "、".join(f"{k}={src_cfg.get(k)}" for k in _SOURCE_EXPECTATIONS) + ")")

    # ---- 护栏 2:被改的那个字段在来源里必须还是旧值(墙还在,才谈得上拆)----
    mismatched = [(f, old, src_cfg.get(f, "<缺>")) for f, old, _new in _CHARTER_FIELDS
                  if not _eq(src_cfg.get(f, "<缺>"), old)]
    if mismatched:
        print(f"错误:来源 {_SOURCE_VERSION} 的待改字段不是预期旧值,拒绝落行"
              f"(来源行可能已被人动过):", file=sys.stderr)
        for f, old, cur in mismatched:
            print(f"    {f}: 期望 {old},实际 {cur}", file=sys.stderr)
        return 2

    # ---- 从 v1.3 config 复制,只改一个字段(其余逐字段原样,不手抄)----
    new_cfg = dict(src_cfg)
    for f, _old, new in _CHARTER_FIELDS:
        new_cfg[f] = new

    changed_keys = {f for f, _o, _n in _CHARTER_FIELDS}
    untouched_mismatch = [k for k in set(src_cfg) | set(new_cfg)
                          if k not in changed_keys and src_cfg.get(k, "<缺>") != new_cfg.get(k, "<缺>")]
    if untouched_mismatch:   # 结构性自检:除唯一修订字段外,一个键都不许动
        print(f"错误:除 {sorted(changed_keys)} 外还有字段发生变化:{untouched_mismatch}", file=sys.stderr)
        return 3

    brain.save_version(
        _TARGET_VERSION,
        rule={"config": new_cfg, "lineage": "K1"},
        changelog=_CHANGELOG,
        activate=False,
        db_path=db_path,
    )

    # ---- diff + 断言 ----
    print(f"\n{_TARGET_VERSION} 章程 old({_SOURCE_VERSION})→ new 逐字段 diff(仅一个字段改动):")
    for f, old, new in _CHARTER_FIELDS:
        print(f"  * {f:<32} {old} → {new}   ← 改动(拆墙)")
    print(f"\n其余字段 = {_SOURCE_VERSION} 逐字段相同(靠复制保证):")
    for k in sorted(new_cfg):
        if k not in changed_keys:
            print(f"    {k:<32} {new_cfg[k]}")

    v133 = brain.get_version(_TARGET_VERSION, db_path=db_path)
    cfg133 = dict(v133.rule.get("config", {}))
    assert cfg133.get("forbid_high_elasticity") is False, "拆墙字段未落成 False!"
    assert _eq(cfg133.get("stop_pct"), 0.05), "-5% 止损被改!"
    assert _eq(cfg133.get("take_profit_retrace"), 0.08), "回落止盈被改!"
    assert _eq(cfg133.get("max_positions"), 3) and _eq(cfg133.get("single_cap"), 40000.0), "三仓章程被改!"
    assert v133.rule.get("lineage") == "K1", "内核血缘留痕丢失!"

    # 来源行 v1.3 必须逐字节未变(本脚本只写新行,绝不碰既有行)
    src_after = brain.get_version(_SOURCE_VERSION, db_path=db_path)
    if src_after.rule != src.rule or src_after.changelog != src.changelog:
        print(f"错误:{_SOURCE_VERSION} 行竟被改动 —— 本脚本只许写新行!", file=sys.stderr)
        return 3

    active_after = brain.get_active(db_path=db_path)
    print(f"\n落库完成:{_TARGET_VERSION} 已写入(is_active={int(v133.is_active)}, "
          f"activated_at={v133.activated_at})")
    print(f"  拆墙核对:forbid_high_elasticity={cfg133.get('forbid_high_elasticity')};"
          f"未动核对:stop_pct={cfg133.get('stop_pct')} / "
          f"take_profit_retrace={cfg133.get('take_profit_retrace')} / "
          f"max_positions={cfg133.get('max_positions')} / single_cap={cfg133.get('single_cap')}")
    print(f"现役断言:is_active 仍在 "
          f"{active_after.version if active_after else '(无)'}(应与落行前相同,未激活 {_TARGET_VERSION})")
    if v133.is_active or (active_before is not None and active_after.version != active_before.version):
        print(f"错误:{_TARGET_VERSION} 竟成了现役 / 现役被改 —— 违反 activate=False 约束!", file=sys.stderr)
        return 3
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=f"落 {_TARGET_VERSION} 拆墙章程行(activate=False)")
    ap.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")
    args = ap.parse_args()
    db_path = args.db or settings.db_path
    print(f"目标库:{db_path}")
    return land_charter(db_path)


if __name__ == "__main__":
    raise SystemExit(main())
