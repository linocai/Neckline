#!/usr/bin/env python3
"""`v2.2-k8` 章程落库脚本(K8 §十三 持仓原则;🔴🔴 高危区:碰纪律章程 + 大脑版本表)。

**只落行,不激活**(`activate=False`)—— 把 `v2.2-k8` 作为一个新 `strategy_versions` 行
写入,`is_active` 仍留在落行前那一版(生产 = `v1.3.3`),**生产行为零变化**。真正生效走
切换器 `scripts/activate_charter.py --target v2.2-k8 --confirm`(**staged**:用户清空全部
open 持仓 + 明确确认后才跑;闸 2 硬校验「无 open 持仓」,带不带 `--confirm` 都过不去)。

**默认演练**:不带 `--confirm` 只打印 diff 与核对结果、**不写库**;带 `--confirm` 才落行,
且落行**之前**先做双备份(`<db>.bak-<戳>` + `<db>.bak-<戳>.integrity`)。

**单一事实源铁律(§3.8)**:`v2.2-k8` 的 config **从 DB 读 `v1.3.3` 行的 `rule["config"]`
复制一份,只改下列五个字段**,其余逐字段原样 —— **绝不在本脚本手抄一份 config**(防漂移)。

    · take_profit_retrace:            0.08  → None    (回落止盈 8% 退役)
    · max_hold_days:                  5     → None    (时间退出档退役)
    · max_hold_days_profit:           15    → None    (浮盈硬上限随之退役)
    · time_exit_only_if_unprofitable: True  → False   (无时间退出时该开关无意义,回落 K1 默认
                                                       值以免留一个假旋钮)
    · stop_pct:                       0.05  → 0.05    (**值一字不动**,见下)

⚠ **`stop_pct` 刻意不在改动清单里**:§五 ⑤ 明写「**值与唯一源地位一字不动**,改的是它
**触发什么**」——① 篮子失效条件、② 连续止损链判据、③ 卡上止损价 `close×(1−stop_pct)`
三处仍读同一个 0.05。语义(强制条件单 → 止损警戒 + 离场决策)**不住 config**,住 §2.1
条文 + `brain.STOP_ADVISORY_CHARTERS` 的版本声明。

⚠ **仓位三件(single_cap / max_positions / max_exposure_frac)与 forbid_high_elasticity
一字不动**:K8 对仓位**沉默**,而**沉默 ≠ 废除**;排科创板是**选股域**(块 ①),不是纪律域。

version = `v2.2-k8`(**K8 持仓原则落进系统线章程,绝不占 K 字头选股线命名空间** —— 三条
版本线见 CLAUDE.md「三条版本线」);rule 携 `lineage="K1"`(内核血缘留痕,策略内核未改一字)。
`v1.3.3` / `v1.3` / K1 / K2 / K4 / v1.2 等既有行原样保留、不覆盖、不激活。

用法:
    python scripts/oneoff/seed_charter_v22k8.py                    # 演练:核对 + diff,不写库
    python scripts/oneoff/seed_charter_v22k8.py --confirm          # 双备份 + 落行(仍不激活)
    python scripts/oneoff/seed_charter_v22k8.py --db /path.db --confirm

前置安全护栏(任一不满足即拒绝落行 + 非零退出):
    · `v1.3.3` 行必须存在,且其 config 八项核心值 = v1.3.3 章程拍板值(防从被改坏的行复制);
    · 五个待改字段在来源里必须仍是预期旧值(否则说明来源行已被人动过);
    · 除这五个字段外,新旧 config 一个键都不许有差异(结构性自检)。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.strategy import brain  # noqa: E402

_SOURCE_VERSION = "v1.3.3"
_TARGET_VERSION = "v2.2-k8"

# 待改字段:(字段, 源 v1.3.3 期望旧值, v2.2-k8 新值)。绝不手抄整份 config。
_CHARTER_FIELDS = [
    ("take_profit_retrace", 0.08, None),
    ("max_hold_days", 5, None),
    ("max_hold_days_profit", 15, None),
    ("time_exit_only_if_unprofitable", True, False),
]

# 来源核对护栏:`v1.3.3` 行必须是章程拍板的那八个数(与 activate_charter._CORE_EXPECTATIONS
# 的 v1.3.3 条目同源同值——那里是"激活前核对",这里是"复制前核对",两道方向相反的闸)。
_SOURCE_EXPECTATIONS = {
    "take_profit_retrace": 0.08,
    "max_hold_days": 5,
    "max_hold_days_profit": 15,
    "time_exit_only_if_unprofitable": True,
    "stop_pct": 0.05,
    "single_cap": 40000.0,
    "max_positions": 3,
    "forbid_high_elasticity": False,
}

# 🔴🔴 **风险登记(照 v1.3 先例全文入 charter changelog,⛔ 不得删、不得摘要、不得软化)**
# —— 用户 2026-08-09 裁定 #5,与 §五 ⑤「🔴 风险登记」五条逐条对应、§八 第 19 项已当面告知。
_CHANGELOG = (
    "承 v1.3.3 全部字段,**只改退出侧四个字段**(take_profit_retrace 0.08→None / "
    "max_hold_days 5→None / max_hold_days_profit 15→None / time_exit_only_if_unprofitable "
    "True→False);stop_pct=0.05 **值与唯一源地位一字不动**,改的是它触发什么(强制条件单 → "
    "「止损警戒 + 离场决策」,K8 §十三);仓位三仓制(single_cap 4 万 / max_positions 3 / "
    "max_exposure_frac 1.0)与 forbid_high_elasticity 一字不动(K8 对仓位沉默,沉默 ≠ 废除;"
    "排科创板是选股域不是纪律域)。内核血缘=K1 未改一字;系统 v 字头章程修订,不占 K 命名空间。"
    "① **依据**:用户 2026-08-09 裁定 #5「按 K8 持仓原则修订」——盈利离场不设统一机械比例"
    "(改由进入目标离场区间后按当时走势决定)、时间退出让位「上涨效率下降 → 保留主观换股权」、"
    "−5% 由强制条件单改为止损警戒 + 离场决策。用户原话:K8 没有讨论空间。"
    "② **🔴 风险登记之一(不得删):本版退掉的是 §1.3 交割单归因里仅有的两条被真金证明过的"
    "防线。** (a) **−5% 强制止损** —— 历史上 13 笔破线未止损多亏约 1.38 万,**占已实现亏损 "
    "85%**;(b) **时间退出** —— 持有 4–7 自然日是唯一打平的持有桶。**退役后这两条的责任 100% "
    "转移到用户盘中执行**,系统只提供警戒与事后归因。"
    "③ **🔴 风险登记之二(不得删):回落止盈 8% 是 H9 六格网格中唯一同时改良全期与 2026 的格**"
    "(证据链 `档案/research/h9_exit_reform.md`,路径口径见 PROJECT_PLAN §1.6),退役 = 放弃一个"
    "有网格证据的改良项。"
    "④ **🔴 风险登记之三(不得删):新退出规则(按预案 + 目标区间 + 主观换股)从未被回测验证,"
    "且无法被现有回测引擎验证** —— 它依赖盘中人判层,EOD 数据够不着。故本版章程**不是**「过了"
    "进化门禁的章程」,是**用户行使决策权的越线采纳**(同 v1.3 先例,风险已当面告知)。"
    "⑤ **🔴 风险登记之四(不得删):历史样本上,退掉时间退出是负向的。** "
    "`档案/research/winners_anatomy.md` 实证 —— **封顶赢家的正是 hold=5 日历退出**(大赢家 "
    "88.6% 由它了结,彼时均值 +17.6%),而回落止盈卖飞率仅 11.1%。K8 用「主观换股权」替代它,"
    "该替代的期望**无任何样本背书**。"
    "⑥ **🔴 风险登记之五(不得删):冻结基线全部不适用。** v1.3.3 起入场域已扩到从未被回测过的"
    "范围(N=1288 / −20.53% / 95361.4988 早已不适用),本版再改退出侧 → **与任何回测基线都无"
    "可比性**,⛔ 不许拿旧基线给新章程背书。"
    "⑦ **staged 生效**:用户清空全部 open 持仓(切换器闸 2 硬校验)+ 明确确认后,才由 "
    "`activate_charter.py --target v2.2-k8 --confirm` 激活;**激活前一律仍按 v1.3.3 执行**。"
    "回滚目标 = v1.3.3(已在切换器白名单),SOP 见 `archive/交接与日志/SOP_章程回滚_20260730.md`。"
    "证据链:PROJECT_PLAN §1.3 / §2.1 / §2.9-A / §五 ⑤ / §八 第 19 项;需求原件 K8_STRATEGY_ARCH.md §十三。"
)

_TOL = 1e-9


def _eq(a, b) -> bool:
    """None 与 0 / False 必须区分得开(本次改动的核心就是把值设成 None),故 None 只与
    None 相等,⛔ 不走 float() 那条会把 None 变成异常再落到 `a == b` 的模糊路径。"""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    try:
        return abs(float(a) - float(b)) <= _TOL
    except (TypeError, ValueError):
        return a == b


def _backup(db_path: Path) -> bool:
    """落行前双备份(`.bak-<戳>` 拷贝 + `.integrity` 自检结果)。写不成 → 拒绝落行
    (承 §五 ⑦「一次性脚本体例:默认演练、--confirm 才写、双备份」)。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    try:
        shutil.copy2(db_path, bak)
        with sqlite3.connect(bak) as conn:
            verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
        bak.with_suffix(bak.suffix + ".integrity").write_text(
            f"{stamp} integrity_check={verdict}\n", encoding="utf-8")
    except (OSError, sqlite3.Error) as e:
        print(f"错误:落行前备份失败({bak}: {e})——拒绝写库。", file=sys.stderr)
        return False
    if verdict != "ok":
        print(f"错误:备份库 integrity_check={verdict}(非 ok)——拒绝写库。", file=sys.stderr)
        return False
    print(f"[备份] {bak}(integrity_check={verdict})")
    return True


def land_charter(db_path: Path, confirm: bool) -> int:
    src = brain.get_version(_SOURCE_VERSION, db_path=db_path)
    if src is None:
        print(f"错误:来源版本 {_SOURCE_VERSION} 不存在(先跑 scripts/oneoff/charter_v1_3_3.py 落行)。",
              file=sys.stderr)
        return 1
    src_cfg = dict(src.rule.get("config", {}) or {})
    if not src_cfg:
        print(f"错误:{_SOURCE_VERSION} 行无 rule['config'],无法复制。", file=sys.stderr)
        return 1

    active_before = brain.get_active(db_path=db_path)
    print(f"复制来源版本 = {_SOURCE_VERSION}(is_active={int(src.is_active)});"
          f"当前现役 = {active_before.version if active_before else '(无)'}")

    # ---- 护栏 1:来源八项核心值必须是 v1.3.3 拍板值(防从被改坏/错误的行复制)----
    bad = [(k, src_cfg.get(k, "<缺>"), v) for k, v in _SOURCE_EXPECTATIONS.items()
           if not _eq(src_cfg.get(k, "<缺>"), v)]
    if bad:
        print(f"错误:来源 {_SOURCE_VERSION} 核心值核对未通过,拒绝落行(疑似来源行被改坏):",
              file=sys.stderr)
        for k, got, want in bad:
            print(f"    {k}: 实际 {got},期望 {want}", file=sys.stderr)
        return 2
    print("来源核心值核对通过(" + "、".join(f"{k}={src_cfg.get(k)}" for k in _SOURCE_EXPECTATIONS) + ")")

    # ---- 护栏 2:五个待改字段在来源里必须还是旧值 ----
    mismatched = [(f, old, src_cfg.get(f, "<缺>")) for f, old, _new in _CHARTER_FIELDS
                  if not _eq(src_cfg.get(f, "<缺>"), old)]
    if mismatched:
        print(f"错误:来源 {_SOURCE_VERSION} 的待改字段不是预期旧值,拒绝落行"
              f"(来源行可能已被人动过):", file=sys.stderr)
        for f, old, cur in mismatched:
            print(f"    {f}: 期望 {old},实际 {cur}", file=sys.stderr)
        return 2

    # ---- 从 v1.3.3 config 复制,只改那四个字段(其余逐字段原样,不手抄)----
    new_cfg = dict(src_cfg)
    for f, _old, new in _CHARTER_FIELDS:
        new_cfg[f] = new

    changed_keys = {f for f, _o, _n in _CHARTER_FIELDS}
    untouched_mismatch = [k for k in set(src_cfg) | set(new_cfg)
                          if k not in changed_keys and not _eq(src_cfg.get(k, "<缺>"), new_cfg.get(k, "<缺>"))]
    if untouched_mismatch:   # 结构性自检:除待改字段外,一个键都不许动
        print(f"错误:除 {sorted(changed_keys)} 外还有字段发生变化:{untouched_mismatch}", file=sys.stderr)
        return 3

    # ---- diff 打印(演练与落行都打)----
    print(f"\n{_TARGET_VERSION} 章程 old({_SOURCE_VERSION})→ new 逐字段 diff:")
    for f, old, new in _CHARTER_FIELDS:
        print(f"  * {f:<32} {old} → {new}   ← 改动(K8 §十三 退出侧)")
    print(f"    {'stop_pct':<32} {new_cfg.get('stop_pct')}   ← **值一字不动**(语义改:条件单 → 止损警戒)")
    print(f"\n其余字段 = {_SOURCE_VERSION} 逐字段相同(靠复制保证):")
    for k in sorted(new_cfg):
        if k not in changed_keys:
            print(f"    {k:<32} {new_cfg[k]}")

    if not confirm:
        print(f"\n[演练] 未带 --confirm,**不写库**。确认无误后:"
              f"python scripts/oneoff/seed_charter_v22k8.py --confirm")
        print("[演练] 落行之后仍需 staged 激活(用户清仓 + 确认):"
              f"sudo -u neckline .venv/bin/python scripts/activate_charter.py "
              f"--target {_TARGET_VERSION} --confirm")
        return 0

    if not _backup(db_path):
        return 4

    brain.save_version(
        _TARGET_VERSION,
        rule={"config": new_cfg, "lineage": "K1"},
        changelog=_CHANGELOG,
        activate=False,
        db_path=db_path,
    )

    # ---- 落行后断言 ----
    tgt = brain.get_version(_TARGET_VERSION, db_path=db_path)
    cfg = dict(tgt.rule.get("config", {}))
    assert cfg.get("take_profit_retrace") is None, "回落止盈未退役(应为 None)!"
    assert cfg.get("max_hold_days") is None, "时间退出档未退役(应为 None)!"
    assert cfg.get("max_hold_days_profit") is None, "浮盈硬上限未退役(应为 None)!"
    assert cfg.get("time_exit_only_if_unprofitable") is False, "时间退出开关未回落 False!"
    assert _eq(cfg.get("stop_pct"), 0.05), "stop_pct 被改了 —— 它必须一字不动!"
    assert _eq(cfg.get("single_cap"), 40000.0) and _eq(cfg.get("max_positions"), 3), "三仓章程被改!"
    assert _eq(cfg.get("max_exposure_frac"), src_cfg.get("max_exposure_frac")), "敞口档被改!"
    assert cfg.get("forbid_high_elasticity") is False, "高弹墙被改(纪律域应一字不动)!"
    assert tgt.rule.get("lineage") == "K1", "内核血缘留痕丢失!"

    # 来源行 v1.3.3 必须逐字节未变(本脚本只写新行,绝不碰既有行)
    src_after = brain.get_version(_SOURCE_VERSION, db_path=db_path)
    if src_after.rule != src.rule or src_after.changelog != src.changelog:
        print(f"错误:{_SOURCE_VERSION} 行竟被改动 —— 本脚本只许写新行!", file=sys.stderr)
        return 3

    active_after = brain.get_active(db_path=db_path)
    print(f"\n落库完成:{_TARGET_VERSION} 已写入(is_active={int(tgt.is_active)}, "
          f"activated_at={tgt.activated_at})")
    print(f"现役断言:is_active 仍在 "
          f"{active_after.version if active_after else '(无)'}(应与落行前相同,未激活 {_TARGET_VERSION})")
    if tgt.is_active or (active_before is not None and active_after.version != active_before.version):
        print(f"错误:{_TARGET_VERSION} 竟成了现役 / 现役被改 —— 违反 activate=False 约束!",
              file=sys.stderr)
        return 3
    print(f"提示:staged 激活须用户清空全部 open 持仓 + 明确确认后再跑 "
          f"`activate_charter.py --target {_TARGET_VERSION} --confirm`(§八 第 19 项)。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=f"落 {_TARGET_VERSION} 章程行(K8 §十三;activate=False)")
    ap.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")
    ap.add_argument("--confirm", action="store_true", help="确认写库落行(不带则只演练打印)")
    args = ap.parse_args()
    db_path = args.db or settings.db_path
    print(f"目标库:{db_path}")
    return land_charter(db_path, args.confirm)


if __name__ == "__main__":
    raise SystemExit(main())
