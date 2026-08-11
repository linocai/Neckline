#!/usr/bin/env python3
"""`v2.3-k8` 章程落库脚本(K8.md §十九 退出字段语义;🔴🔴 高危区:碰纪律章程 + 大脑版本表)。

**只落行,不激活**(`activate=False`)—— 把 `v2.3-k8` 作为一个新 `strategy_versions` 行
写入,`is_active` 仍留在落行前那一版(2026-08-11 生产 = `v2.2-k8`),**生产行为零变化**。
真正生效走切换器 `scripts/activate_charter.py --target v2.3-k8 --confirm`(**staged**:
用户清空全部 open 持仓 + 明确确认后才跑;闸 2 硬校验「无 open 持仓」,带不带 `--confirm`
都过不去)。

**默认演练**:不带 `--confirm` 只打印 diff 与核对结果、**不写库**;带 `--confirm` 才落行,
且落行**之前**先做双备份(`<db>.bak-<戳>` + `<db>.bak-<戳>.integrity`)。

**单一事实源铁律(§3.8)**:`v2.3-k8` 的 config **从 DB 读 `v2.2-k8` 行的 `rule["config"]`
复制一份,只加下列两个字段**,其余逐字段原样 —— **绝不在本脚本手抄一份 config**(防漂移)。

    · loss_warning_pct:      <缺> → 0.05       (K8.md §十九 逐字给的数)
    · loss_warning_action:   <缺> → "review"   (K8.md §十九 逐字给的值)

🔴 **`stop_pct` 刻意不在改动清单里,值仍是 `0.05`**:K8.md §十九 原文「兼容字段
`stop_pct` 只保留历史读取能力,执行器不得用其触发自动卖出」—— 改的是**对外语义**,
⛔ 不是把 0.05 搬家、更不是在别处抄一份。它仍是止损价 / 篮子失效条件 / 连续止损链 /
判分链(`eval/exit_sim.py`)的唯一算料;⛔ **回测与判分口径一行都不动**。

🔴 **本版是「只加两个键」,⛔ 不删任何键**(客户端两步淘汰第一步:先发一版客户端把
`stopPct` 改成可选,**下一版**服务端才可删键 —— CLAUDE.md 铁律)。

⚠ **仓位三件(single_cap / max_positions / max_exposure_frac)、forbid_high_elasticity、
以及 `v2.2-k8` 退掉的退出侧四个 `None` 一字不动**:K8.md §十九 只讲退出字段语义,
⛔ 不许顺手动别的。

version = `v2.3-k8`(**K8 退出字段语义落进系统线章程,绝不占 K 字头选股线命名空间** ——
三条版本线见 CLAUDE.md);rule 携 `lineage="K1"`(内核血缘留痕,策略内核未改一字)。
`v2.2-k8` / `v1.3.3` / `v1.3` / K1 / K2 / K4 / v1.2 等既有行原样保留、不覆盖、不激活。

用法:
    python scripts/oneoff/seed_charter_v23k8.py                    # 演练:核对 + diff,不写库
    python scripts/oneoff/seed_charter_v23k8.py --confirm          # 双备份 + 落行(仍不激活)
    python scripts/oneoff/seed_charter_v23k8.py --db /path.db --confirm

前置安全护栏(任一不满足即拒绝落行 + 非零退出):
    · `v2.2-k8` 行必须存在,且其 config 八项核心值 = v2.2-k8 章程拍板值(防从被改坏的行复制);
    · 两个待加字段在来源里**必须还不存在**(已存在 = 来源行已被人动过 / 脚本被跑过两版);
    · 除这两个字段外,新旧 config 一个键都不许有差异(结构性自检)。
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

_SOURCE_VERSION = "v2.2-k8"
_TARGET_VERSION = "v2.3-k8"

# 待加字段:(字段, v2.3-k8 新值)。**两个数都是 K8.md §十九 逐字给的**,⛔ 工程侧一个都没发明
# (§五 〇b 红线 1:裁定与 K8.md 给了的数照抄,没给的一个都不许发明)。绝不手抄整份 config。
_NEW_FIELDS = [
    ("loss_warning_pct", 0.05),
    ("loss_warning_action", "review"),
]

# 来源核对护栏:`v2.2-k8` 行必须是章程拍板的那八个值(与 activate_charter._CORE_EXPECTATIONS
# 的 v2.2-k8 条目同源同值——那里是"激活前核对",这里是"复制前核对",两道方向相反的闸)。
_SOURCE_EXPECTATIONS = {
    "take_profit_retrace": None,
    "max_hold_days": None,
    "max_hold_days_profit": None,
    "time_exit_only_if_unprofitable": False,
    "stop_pct": 0.05,
    "single_cap": 40000.0,
    "max_positions": 3,
    "forbid_high_elasticity": False,
}

# 🔴🔴 **风险登记(照 v1.3 / v2.2-k8 先例全文入 charter changelog,⛔ 不得删、不得摘要、
# 不得软化)**。本版改的是**对外语义**,风险与 `v2.2-k8` 那次量级不同,但仍必须写清
# 「退掉了什么、谁来接住」——⛔ 别因为"只加两个字段"就把风险登记省成一句话。
_CHANGELOG = (
    "承 v2.2-k8 全部字段,**只新增两个对外语义字段**(loss_warning_pct=0.05 / "
    "loss_warning_action=review);stop_pct=0.05 **值与唯一源地位一字不动**(K8.md §十九:"
    "「兼容字段 stop_pct 只保留历史读取能力,执行器不得用其触发自动卖出」);退出侧四个 None"
    "(take_profit_retrace / max_hold_days / max_hold_days_profit / time_exit_only_if_unprofitable)"
    "与仓位三仓制(single_cap 4 万 / max_positions 3 / max_exposure_frac 1.0)、"
    "forbid_high_elasticity 一律承 v2.2-k8 不动。内核血缘=K1 未改一字;系统 v 字头章程修订,"
    "不占 K 命名空间。"
    "① **依据**:K8.md(V0.6)§十九「退出字段语义」逐字 —— 「5% 对外语义使用 "
    "`loss_warning_pct = 0.05` 和 `loss_warning_action = review`」;§十三「持仓原则/判断失效」"
    "逐字 —— 「单只股票亏损达到 5% 时,系统发出亏损警戒,由用户完成离场决策;亏损警戒不触发"
    "系统自动卖出」。两个数(0.05 / review)均由需求原件给定,⛔ 工程侧未发明任何数。"
    "② **🔴 风险登记之一(不得删):−5% 到线之后的执行责任 100% 在用户。** 系统只发亏损警戒 + "
    "算出那条线的价位,**永不代下单、永不自动卖出**(全仓零自动卖出路径由守门单测钉死)。"
    "§1.3 交割单归因里 13 笔破线未止损多亏约 1.38 万、占已实现亏损 85% —— 那条防线在 v2.2-k8 "
    "已由「强制条件单」降为「止损警戒 + 离场决策」,本版只是把这件事**写进 config 让系统说得清**,"
    "⛔ 并未再退一步,也⛔ 未把它补回来。"
    "③ **🔴 风险登记之二(不得删):`stop_pct` 从此是「兼容只读」字段,但它仍在四条链上当算料** "
    "—— 判分(eval/exit_sim.py)· 展示派生(api/app.py::_stop_line)· 篮子失效条件"
    "(selection/basket_card.py)· 哨兵警戒(sentinel/holding.py)。「兼容只读」指的是"
    "**执行器不得据它自动卖出**,⛔ 不是「这个字段可以删了」;真要删是两步淘汰第二步的事"
    "(先发客户端把 stopPct 改可选,下一版服务端才可删键)。"
    "④ **🔴 风险登记之三(不得删):本版语义未被回测验证,也无法被现有回测引擎验证** —— "
    "「警戒后由用户决定离场」依赖盘中人判层,EOD 数据够不着。故本版章程**不是**「过了进化门禁"
    "的章程」,是**用户行使决策权的越线采纳**(同 v1.3 / v2.2-k8 先例,风险已当面告知)。"
    "⑤ **🔴 风险登记之四(不得删):冻结基线全部不适用。** v1.3.3 起入场域已扩到从未被回测过的"
    "范围(N=1288 / −20.53% / 95361.4988 早已不适用),v2.2-k8 又改了退出侧 → **与任何回测基线"
    "都无可比性**,⛔ 不许拿旧基线给新章程背书。"
    "⑥ **staged 生效**:用户清空全部 open 持仓(切换器闸 2 硬校验)+ 明确确认后,才由 "
    "`activate_charter.py --target v2.3-k8 --confirm` 激活;**激活前一律仍按 v2.2-k8 执行**。"
    "回滚目标 = v2.2-k8(已在切换器白名单),SOP 见 `archive/交接与日志/SOP_章程回滚_20260730.md`。"
    "证据链:PROJECT_PLAN §2.1 / §五 V2.3.2 ⑤ / §五 V2.3.2 ⑨;需求原件 "
    "`~/Lino/whynotme/K8.md`(V0.6)§十三 与 §十九。"
)

_TOL = 1e-9
_MISSING = "<缺>"


def _eq(a, b) -> bool:
    """None 与 0 / False 必须区分得开(v2.2-k8 的核心判据就是四个 None),故 None 只与
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
    (承一次性脚本体例:默认演练、--confirm 才写、双备份)。"""
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
        print(f"错误:来源版本 {_SOURCE_VERSION} 不存在"
              f"(先跑 scripts/oneoff/seed_charter_v22k8.py 落行)。", file=sys.stderr)
        return 1
    src_cfg = dict(src.rule.get("config", {}) or {})
    if not src_cfg:
        print(f"错误:{_SOURCE_VERSION} 行无 rule['config'],无法复制。", file=sys.stderr)
        return 1

    active_before = brain.get_active(db_path=db_path)
    print(f"复制来源版本 = {_SOURCE_VERSION}(is_active={int(src.is_active)});"
          f"当前现役 = {active_before.version if active_before else '(无)'}")

    # ---- 护栏 1:来源八项核心值必须是 v2.2-k8 拍板值(防从被改坏/错误的行复制)----
    bad = [(k, src_cfg.get(k, _MISSING), v) for k, v in _SOURCE_EXPECTATIONS.items()
           if not _eq(src_cfg.get(k, _MISSING), v)]
    if bad:
        print(f"错误:来源 {_SOURCE_VERSION} 核心值核对未通过,拒绝落行(疑似来源行被改坏):",
              file=sys.stderr)
        for k, got, want in bad:
            print(f"    {k}: 实际 {got},期望 {want}", file=sys.stderr)
        return 2
    print("来源核心值核对通过(" + "、".join(f"{k}={src_cfg.get(k)}" for k in _SOURCE_EXPECTATIONS) + ")")

    # ---- 护栏 2:两个待加字段在来源里必须**还不存在** ----
    # 已存在 = 来源行被人动过,或本脚本已按某个别的口径跑过 —— 两种情况都不该静默覆盖。
    already = [(f, src_cfg.get(f)) for f, _new in _NEW_FIELDS if f in src_cfg]
    if already:
        print(f"错误:来源 {_SOURCE_VERSION} 里已经有本版要新增的字段,拒绝落行"
              f"(来源行可能已被人动过;⛔ 本脚本只加不覆盖):", file=sys.stderr)
        for f, cur in already:
            print(f"    {f}: 实际 {cur}", file=sys.stderr)
        return 2

    # ---- 从 v2.2-k8 config 复制,只加那两个字段(其余逐字段原样,不手抄)----
    new_cfg = dict(src_cfg)
    for f, new in _NEW_FIELDS:
        new_cfg[f] = new

    changed_keys = {f for f, _n in _NEW_FIELDS}
    untouched_mismatch = [k for k in set(src_cfg) | set(new_cfg)
                          if k not in changed_keys and not _eq(src_cfg.get(k, _MISSING),
                                                               new_cfg.get(k, _MISSING))]
    if untouched_mismatch:   # 结构性自检:除待加字段外,一个键都不许动
        print(f"错误:除 {sorted(changed_keys)} 外还有字段发生变化:{untouched_mismatch}", file=sys.stderr)
        return 3

    # ---- diff 打印(演练与落行都打)----
    print(f"\n{_TARGET_VERSION} 章程 old({_SOURCE_VERSION})→ new 逐字段 diff:")
    for f, new in _NEW_FIELDS:
        print(f"  + {f:<32} <缺> → {new!r}   ← 新增(K8.md §十九 退出字段语义)")
    print(f"    {'stop_pct':<32} {new_cfg.get('stop_pct')}   "
          f"← **值一字不动**(降为兼容只读:执行器不得据它自动卖出)")
    print(f"\n其余字段 = {_SOURCE_VERSION} 逐字段相同(靠复制保证):")
    for k in sorted(new_cfg):
        if k not in changed_keys:
            print(f"    {k:<32} {new_cfg[k]}")

    if not confirm:
        print(f"\n[演练] 未带 --confirm,**不写库**。确认无误后:"
              f"python scripts/oneoff/seed_charter_v23k8.py --confirm")
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
    assert _eq(cfg.get("loss_warning_pct"), 0.05), "亏损警戒线不是 0.05(K8.md §十九 给的数)!"
    assert cfg.get("loss_warning_action") == "review", "警戒动作不是 review(⛔ 系统永不自动卖出)!"
    assert _eq(cfg.get("stop_pct"), 0.05), "stop_pct 被改了 —— 它必须一字不动!"
    assert cfg.get("take_profit_retrace") is None and cfg.get("max_hold_days") is None, \
        "v2.2-k8 退掉的退出侧字段被改回来了!"
    assert cfg.get("max_hold_days_profit") is None, "浮盈硬上限被改回来了!"
    assert cfg.get("time_exit_only_if_unprofitable") is False, "时间退出开关被改!"
    assert _eq(cfg.get("single_cap"), 40000.0) and _eq(cfg.get("max_positions"), 3), "三仓章程被改!"
    assert _eq(cfg.get("max_exposure_frac"), src_cfg.get("max_exposure_frac")), "敞口档被改!"
    assert cfg.get("forbid_high_elasticity") is False, "高弹墙被改(纪律域应一字不动)!"
    assert tgt.rule.get("lineage") == "K1", "内核血缘留痕丢失!"

    # 来源行 v2.2-k8 必须逐字节未变(本脚本只写新行,绝不碰既有行)
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
          f"`activate_charter.py --target {_TARGET_VERSION} --confirm`(§五 V2.3.2 ⑩-1)。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=f"落 {_TARGET_VERSION} 章程行(K8.md §十九 退出字段语义;activate=False)")
    ap.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")
    ap.add_argument("--confirm", action="store_true", help="确认写库落行(不带则只演练打印)")
    args = ap.parse_args()
    db_path = args.db or settings.db_path
    print(f"目标库:{db_path}")
    return land_charter(db_path, args.confirm)


if __name__ == "__main__":
    raise SystemExit(main())
