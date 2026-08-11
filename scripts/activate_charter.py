#!/usr/bin/env python3
"""章程切换器(plan §五 v1.2-A.2 / v1.3-①-E / v1.3-⑦-E staged 步骤 2,🔴 高危区:大脑激活)。

**staged 生效铁律**:章程激活 = 用户**无 open 持仓**(切换器闸 2 硬校验)+ 明确确认后,才由
本脚本把 `is_active` 移到目标章程行。激活前所有行为按当时现役版本的值执行。**默认目标 =
`v1.3.3`**(拆墙版:v1.3 逐字段相同,只 `forbid_high_elasticity` True→False;用户
2026-07-27 拍板)。历史:K1 → `v1.3`(2026-07-27 12:01 CST 生产激活)→ `v1.3.3`。

🔴🔴 **V2.2-⑤:白名单新增 `v2.2-k8`(K8 §十三 持仓原则,用户裁定 #5)**。它改的**全是
退出侧**(回落止盈 / 时间退出两档退役;`stop_pct=0.05` 值不动、语义由「强制条件单」改为
「止损警戒 + 离场决策」)—— **正是闸 2 当初要防的那一类**,故**闸 2 的纯入场侧窄豁免对它
必然不成立**(守门单测 `tests/test_activate_charter_gates.py` 正面钉死:对
`v1.3.3 → v2.2-k8` 的 diff,`_exemption_verdict()` 必须返 `False`)。**默认目标刻意仍是
`v1.3.3`** —— 高危目标必须显式 `--target` 打出来,不许手滑默认过去。
**风险登记全文在落行脚本 `scripts/oneoff/seed_charter_v22k8.py` 的 changelog 与
PROJECT_PLAN §五 ⑤ / §八 第 19 项,⛔ 不得删、不得摘要。**

🔴🔴 **V2.3.2-⑤:白名单再加 `v2.3-k8`(K8.md §十九 退出字段语义)**。它**只加两个字段**
(`loss_warning_pct=0.05` / `loss_warning_action="review"`),`stop_pct=0.05` 值一字不动、
降为**兼容只读**(「执行器不得用其触发自动卖出」K8.md §十九 逐字)。**改的仍是退出侧语义**
→ **窄豁免对它同样必然不成立**(守门单测正面钉死 `v2.2-k8 → v2.3-k8` 的 `_exemption_verdict()`
返 `False`:`loss_warning_*` 不在入场侧白名单里,条件 (a) 就过不去)。风险登记全文在
`scripts/oneoff/seed_charter_v23k8.py` 的 changelog,⛔ 不得删、不得摘要。

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
       **窄豁免见下**。
    3. **打印 old→new 逐字段 diff** + **核心值核对(凡激活必做)**:现役 config 与目标 config
       全字段对照高亮改动;再按 `_CORE_EXPECTATIONS[target]` 逐项核对(防激活到错误/未改的行)。
    4. **`--confirm` 才写库**:无 `--confirm` 只 dry-run 打印 diff、不写库;带 `--confirm`
       才 `brain.activate_version(target)`。

⚠ **闸 2 的窄豁免:纯入场侧 diff(2026-07-27 用户授权,v1.3.3 拆墙)**
闸 2 当初的理由是「别在持仓在飞的时候换**退出**规则」——持仓按 A 章程开的,中途换成 B 章程
管退出,等于对在途仓位执行一套它从未被评估过的规则。但**若 old→new 的差异只落在入场侧**
(改的是"哪些票可以买"),在途持仓的止损/止盈/时间退出行为**逐字段不变**,该理由不成立。
故加一道**极窄**豁免:**当且仅当**
    (a) diff 的字段集合 ⊆ `_ENTRY_SIDE_EXEMPT_KEYS`(目前只有 `forbid_high_elasticity`),**且**
    (b) `_HOLD_INVARIANT_KEYS` 八项(退出四 + 仓位四)**逐字段相同**(独立正向核对,不靠 (a)
        推导——万一将来有人草率地往白名单里加字段,这一条仍死死钉住"在途仓位行为不变")
才允许带持仓激活。**豁免必留痕**:打印豁免理由 + diff 全文 + 未平持仓清单,并**追加写入
审计日志文件**(`<db 同目录>/charter_activation_audit.log`,append-only);**日志写不成 →
拒绝激活**(宁可不切,也不静默豁免)。任何不满足 (a)(b) 的情形,闸 2 行为与从前完全一致。

**不做 API 端点**:策略大脑激活绝不暴露给客户端(§3.8 系统内核永不被客户端改),
只走命令行 + 用户在 ECS 权威库手动跑(能写该库的身份,即服务 `User=neckline`:
`sudo -u neckline .venv/bin/python scripts/activate_charter.py --target v1.3.3 --confirm`)。

用法:
    python scripts/activate_charter.py                            # dry-run:校验 + diff(默认目标 v1.3.3)
    python scripts/activate_charter.py --confirm                  # 校验通过 + 激活 v1.3.3
    python scripts/activate_charter.py --target v1.3.3 --confirm  # 显式目标
    python scripts/activate_charter.py --db /path.db --confirm
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.sentinel.positions import load_open_positions  # noqa: E402
from neckline.strategy import brain  # noqa: E402

_TARGET_VERSION = "v1.3.3"

# —— 闸 1:目标白名单(审计 🟡-2;**加版本时必须同时给它一条 `_CORE_EXPECTATIONS`**)——
# 只有系统线 v 字头**现行**章程行可被激活。名单外一律硬拒:K 字头研究臂(K1 现役是历史
# 既成事实,不经本脚本;K2/K3/K4 是已否决/参考档)、过时的 v1.2(回落 5%/hold=5,已被 v1.3
# 取代,保留不删但永不激活)、以及任何 typo/复制错的串。
# **v1.3 保留在名单内**:v1.3.3 只拆了高弹墙,若拆墙后需要紧急退回"主板 only"口径,v1.3 是
# 唯一合法回退目标(其余字段两版逐字段相同)——回退也须走本切换器四道闸,不许手改 DB。
# **V2.2-⑤ 加 `v2.2-k8`**(K8 §十三 持仓原则;用户裁定 #5)。**回滚目标 = `v1.3.3`**,
# 已在名单内 —— 回滚同样走本切换器四道闸,SOP 见 `archive/交接与日志/SOP_章程回滚_20260730.md`。
# **V2.3.2-⑤ 加 `v2.3-k8`**(K8.md §十九 退出字段语义:`loss_warning_pct` /
# `loss_warning_action` 两个新字段;`stop_pct=0.05` 值一字不动)。**回滚目标 = `v2.2-k8`**,
# 已在名单内 —— 回滚同样走本切换器四道闸,SOP 同上。
_ALLOWED_TARGETS = ("v1.3", "v1.3.3", "v2.2-k8", "v2.3-k8")

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
        "forbid_high_elasticity": True,       # v1.3 = 主板 only(墙还在;v1.3.3 才拆)
    },
    # v1.3.3 = v1.3 逐字段相同,**只拆高弹墙**(用户 2026-07-27 拍板)。退出/仓位七项与 v1.3
    # 一字不差地重复在此,是刻意的:核心值核对的职责就是"防激活到未改对的行"——若 v1.3.3
    # 行被人改坏了退出或仓位字段,这里必须拦下。**`forbid_high_elasticity` 期望值恰与 v1.3
    # 相反**,是本版唯一的语义差,也是"激活到了正确那一行"的判据。
    "v1.3.3": {
        "take_profit_retrace": 0.08,
        "max_hold_days": 5,
        "max_hold_days_profit": 15,
        "time_exit_only_if_unprofitable": True,
        "stop_pct": 0.05,
        "single_cap": 40000.0,
        "max_positions": 3,
        "forbid_high_elasticity": False,      # 拆墙:创业板/科创板不再被纪律层禁买
    },
    # —— V2.2-⑤ `v2.2-k8`(K8 §十三 持仓原则;🔴🔴 退出侧四字段退役)——————————————
    # ⚠ **四个 `None` 是本版的核心判据,不是"没填"**:核心值核对的职责就是"防激活到未改
    # 对的行" —— 若这四位不是 `None`(比如某人手抄时填了 0 或留了旧值),说明落的不是
    # K8 那一行,硬拒。`_eq` 对 `None` 做严格同一判定(⛔ 不与 0/False 混,见其 docstring)。
    # ⚠ **`stop_pct=0.05` 与仓位三件在此逐字重复,是刻意的**:§五 ⑤ 明写它们「一字不动」,
    # 核对表正是把这句话变成过不去的闸 —— 谁把 stop_pct 顺手改成 None,这里当场拦下。
    "v2.2-k8": {
        "take_profit_retrace": None,          # 回落止盈 8% 退役(K8:盈利离场不设统一机械比例)
        "max_hold_days": None,                # 时间退出档退役(让位主观换股权)
        "max_hold_days_profit": None,         # 浮盈硬上限随之退役
        "time_exit_only_if_unprofitable": False,   # 无时间退出时该开关无意义 → 回落 K1 默认值
        "stop_pct": 0.05,                     # **值一字不动**(语义改:条件单 → 止损警戒)
        "single_cap": 40000.0,                # 三仓章程一字不动(K8 沉默 ≠ 废除)
        "max_positions": 3,
        "forbid_high_elasticity": False,      # 纪律域一字不动(排科创板是选股域,归块 ①)
    },
    # —— V2.3.2-⑤ `v2.3-k8`(K8.md §十九 退出字段语义)—————————————————————————
    # ⚠ **本版与 `v2.2-k8` 只差两个新增字段**,其余八项逐字重复在此,是刻意的:核心值
    # 核对的职责就是"防激活到未改对的行" —— 谁把 `stop_pct` 顺手改成 None、或把 v2.2-k8
    # 退掉的四档偷偷补回来,这里当场拦下。
    # 🔴 **两个新值是本版唯一的语义差,也是"激活到了正确那一行"的判据**:`0.05` 与
    # `"review"` 都是 K8.md §十九 逐字给的数,⛔ 工程侧一个都没发明。
    # 🔴 `loss_warning_action="review"` = **亏损警戒 + 由用户完成离场决策**,
    # ⛔ 系统在任何取值下都不得自动卖出(K8.md §十三 逐字)。
    "v2.3-k8": {
        "loss_warning_pct": 0.05,             # 亏损警戒线(K8.md §十九)
        "loss_warning_action": "review",      # ⛔ 不触发系统自动卖出(K8.md §十三)
        "take_profit_retrace": None,          # 承 v2.2-k8:回落止盈已退役
        "max_hold_days": None,                # 承 v2.2-k8:时间退出档已退役
        "max_hold_days_profit": None,         # 承 v2.2-k8:浮盈硬上限已退役
        "time_exit_only_if_unprofitable": False,
        "stop_pct": 0.05,                     # **值一字不动**(降为兼容只读字段)
        "single_cap": 40000.0,                # 三仓章程一字不动
        "max_positions": 3,
        "forbid_high_elasticity": False,      # 纪律域一字不动
    },
}
# —— 闸 2 窄豁免(2026-07-27 用户授权)——————————————————————————————————————
# (a) 允许出现在 diff 里的**入场侧**字段。入场侧 = 只影响「哪些票可以买」(entry mask /
#     买入违纪判定),对**已持有**仓位的止损/止盈/时间退出零影响。往这里加字段前先问
#     自己:「一笔已经在飞的仓位,会因为这个字段变了而改变它的退出行为吗?」——只要答案
#     不是斩钉截铁的"不会",就不该加。
_ENTRY_SIDE_EXEMPT_KEYS = frozenset({"forbid_high_elasticity"})

# (b) **在途仓位行为不变量**:退出四 + 仓位四,豁免时必须逐字段相同(独立正向核对)。
_HOLD_INVARIANT_KEYS = (
    "stop_pct", "take_profit_retrace", "max_hold_days", "max_hold_days_profit",
    "time_exit_only_if_unprofitable", "single_cap", "max_positions", "max_exposure_frac",
)

_AUDIT_LOG_NAME = "charter_activation_audit.log"

_TOL = 1e-9


def _eq(a, b) -> bool:
    """章程 config 值的相等判定。

    🔴 **`None` 必须与 `0` / `False` 严格分开**(V2.2-⑤ 起这条是硬要求):`v2.2-k8` 用
    `None` 表达「**不设**回落止盈 / 不设时间退出」,而 `0` 会是「阈值 0%」这种完全不同
    (且荒谬)的东西。原实现 `float(None)` 抛 `TypeError` → 落到 `a == b`,`None == 0`
    在 Python 里是 `False`,**结论恰好正确但纯属侥幸**;`False == 0` 却是 `True` ——
    `time_exit_only_if_unprofitable: False` 与某个 `0` 会被判等。现显式分层,不靠侥幸。"""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
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


def _diff_keys(old_cfg: dict, new_cfg: dict) -> list:
    """old→new 发生变化的 config 字段名(排序)。缺键按 `<缺>` 参与比较,故「新增/删除
    一个字段」也算改动 —— 豁免判定绝不能对"多出来的字段"视而不见。"""
    return sorted(
        k for k in (set(old_cfg) | set(new_cfg))
        if not _eq(old_cfg.get(k, "<缺>"), new_cfg.get(k, "<缺>"))
    )


def _exemption_verdict(old_cfg: dict, new_cfg: dict, changed: list) -> tuple:
    """闸 2 窄豁免判定。返回 `(exempt: bool, reasons: list[str])`——`reasons` 无论准不准
    都会被打印/存档(拒绝时说明为什么不给豁免,通过时说明凭什么给)。"""
    reasons = []
    outside = [k for k in changed if k not in _ENTRY_SIDE_EXEMPT_KEYS]
    cond_a = not outside
    if cond_a:
        reasons.append(f"(a) diff 字段集合 {changed} ⊆ 入场侧白名单 {sorted(_ENTRY_SIDE_EXEMPT_KEYS)} ✓")
    else:
        reasons.append(f"(a) diff 含入场侧白名单**之外**的字段 {outside} ✗")

    violated = [(k, old_cfg.get(k, "<缺>"), new_cfg.get(k, "<缺>")) for k in _HOLD_INVARIANT_KEYS
                if not _eq(old_cfg.get(k, "<缺>"), new_cfg.get(k, "<缺>"))]
    cond_b = not violated
    if cond_b:
        reasons.append(f"(b) 在途仓位行为不变量 {list(_HOLD_INVARIANT_KEYS)} 逐字段相同 ✓")
    else:
        reasons.append("(b) 在途仓位行为不变量被改动 ✗:" +
                       "、".join(f"{k}({o}→{n})" for k, o, n in violated))
    return (cond_a and cond_b), reasons


def _reactivation_banner(db_path: Path, target: str) -> list:
    """目标此前被激活过 → 生成「重激活/回滚」告警横幅(v1.4 review 🟡-1 的 belt-and-braces)。
    从未激活过 → 返回空列表(正常首次激活不加噪音)。

    **判定层已经不怕回滚了**(激活历史改成 append-only 事件流 `strategy_activation_log`,
    历史周判定逐位不变,见 `brain.activate_version`);本横幅是给**人**看的第二道:回滚是
    事故动作,该在终端和审计日志里都留一条,而不是悄悄切回去。"""
    history = [(inst, ver) for inst, ver in brain.activation_history(db_path=db_path) if ver == target]
    if not history:
        return []
    return [
        "=" * 78,
        f"⚠ 重激活 / 回滚:{target} 此前已现役过 {len(history)} 次",
        *[f"    第 {i} 次:{inst.isoformat()}" for i, (inst, _) in enumerate(history, 1)],
        "  历史判定**不受影响**:激活历史是 append-only 事件流,本次激活只在时间轴末尾追加",
        "  一条事件,回滚之前每一段治权的逐笔纪律判定逐位不变(v1.4 review 🟡-1 修复)。",
        "  但请照旧记账:§九 一行 + STRATEGY_LAB 现役标注 + ~/hz_info.md(若动的是生产库)。",
        "=" * 78,
    ]


def _write_audit(db_path: Path, lines: list) -> bool:
    """把豁免留痕**追加**写进 `<db 同目录>/charter_activation_audit.log`。返回是否写成。
    写不成 → 调用方拒绝激活(**不许静默豁免**:留痕是豁免成立的前提,不是锦上添花)。"""
    path = Path(db_path).resolve().parent / _AUDIT_LOG_NAME
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"[留痕] 豁免记录已追加写入:{path}")
        return True
    except OSError as e:
        print(f"错误:豁免审计日志写入失败({path}: {e})——拒绝激活。"
              f"留痕是豁免成立的前提,绝不静默豁免。", file=sys.stderr)
        return False


def activate(db_path: Path, target: str, confirm: bool) -> int:
    # ---- 闸 1:目标合法性——**白名单**(审计 🟡-2:黑名单挡不住 K2/K4 等废弃研究臂)----
    if target not in _ALLOWED_TARGETS:
        print(
            f"错误:拒绝激活 {target}——不在可激活白名单 {list(_ALLOWED_TARGETS)} 内。\n"
            f"      K 字头(K1/K2/K3/K4)是策略线研究臂/参考档,**永不经本脚本激活**;\n"
            f"      v1.2 是过时章程行(回落 5%/hold=5,已被 v1.3 取代,保留不删但永不激活)。\n"
            f"      如确要激活现行章程,用 --target {_TARGET_VERSION}(v1.3 保留作回退目标)。",
            file=sys.stderr,
        )
        return 2

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
    changed = _diff_keys(old_cfg, new_cfg)

    # ---- 闸 2:前置硬校验(无 open 持仓)+ 纯入场侧 diff 窄豁免 ----
    # ⚠ diff 必须先算好才谈得上豁免,故本闸挪到读 config 之后;**无持仓时行为与从前
    # 逐行相同**(不进任何豁免分支、不写审计日志)。
    open_positions = load_open_positions(db_path=db_path)
    exempted = False
    if open_positions:
        exempt, reasons = _exemption_verdict(old_cfg, new_cfg, changed)
        if not exempt:
            print(
                f"错误:仍有 {len(open_positions)} 笔 status='open' 持仓,拒绝激活 "
                f"{target}(生效时机铁律:清仓后才切)。窄豁免不成立:",
                file=sys.stderr,
            )
            for r in reasons:
                print(f"    {r}", file=sys.stderr)
            print("待清仓清单:", file=sys.stderr)
            for p in open_positions:
                print(f"    id={p.id}  {p.ts_code}  买入 {p.buy_date} @¥{p.buy_price} × {p.qty} 股",
                      file=sys.stderr)
            return 1
        exempted = True
        holdings = [f"    id={p.id}  {p.ts_code}  买入 {p.buy_date} @¥{p.buy_price} × {p.qty} 股"
                    for p in open_positions]
        banner = [
            "=" * 78,
            f"⚠ 闸 2 窄豁免生效:带 {len(open_positions)} 笔未平持仓激活 {active.version} → {target}",
            "  豁免依据(纯入场侧 diff —— 改的只是「哪些票可以买」,在途仓位的止损/止盈/",
            "  时间退出行为逐字段不变;闸 2 原始理由〔别在持仓在飞时换退出规则〕不适用):",
            *[f"    {r}" for r in reasons],
            f"  改动字段:{changed}",
            "  未平持仓清单(激活后其退出行为不变):",
            *holdings,
            "=" * 78,
        ]
        for line in banner:
            print(line)

    # ---- 闸 3:打印 old→new 逐字段 diff(高亮变的字段)----
    print(f"\n现役 {active.version} → 目标 {target} 章程 config 逐字段 diff:")
    for k in sorted(set(old_cfg) | set(new_cfg)):
        ov, nv = old_cfg.get(k, "<缺>"), new_cfg.get(k, "<缺>")
        if k in changed:
            print(f"  * {k:<32} {ov} → {nv}   ← 改动")
        else:
            print(f"    {k:<32} {ov}")
    print(f"\n改动字段:{changed or '(无)'}")
    print(f"目标 {target} 内核血缘 lineage = {tgt.rule.get('lineage', '(未标注)')}")

    # ---- 闸 3b:核心值核对(**凡激活必做**,审计 🟡-2;原来只在 target=="v1.3" 时做)----
    rc = _check_core_values(target, new_cfg)
    if rc:
        return rc

    # ---- 重激活 / 回滚告警(v1.4 review 🟡-1;不是闸,不拦,只是把事说清楚)----
    reactivation = _reactivation_banner(db_path, target)
    for line in reactivation:
        print(line)

    # ---- 闸 4:--confirm 才写库 ----
    if not confirm:
        print(f"\n[dry-run] 未带 --confirm,不写库。现役仍为 {active.version}。")
        if exempted:
            print("[dry-run] 上方窄豁免为**预演判定**,未写审计日志(dry-run 不留痕、也不激活)。")
        if reactivation:
            print("[dry-run] 上方重激活告警同为预演,未写审计日志。")
        print(f"确认无误后加 --confirm 激活:"
              f"python scripts/activate_charter.py --target {target} --confirm")
        return 0

    # ---- 豁免留痕(**写库之前**):写不成就不激活,绝不静默豁免 ----
    if exempted:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        audit = [
            "",
            f"[{stamp}] 闸 2 窄豁免激活:{active.version} → {target}(db={db_path})",
            *[f"  {line}" for line in banner],
        ]
        if not _write_audit(db_path, audit):
            return 4

    # ---- 重激活留痕:**写不成只告警、不拦激活**(与豁免留痕刻意不同,理由写死在这)----
    # 豁免留痕是**放宽了一道闸**的前提条件,没留痕 = 没资格豁免,故失败即拒绝;
    # 重激活**没有放宽任何闸**(四道闸逐条照跑),它的留痕是事后审计的便利,而回滚往往
    # 发生在事故现场 —— 因为日志文件写不进去就把用户挡在错误章程上,是拿纪律换事故。
    # 另有两道痕不依赖文件:终端横幅 + `brain.activate_version` 的 WARNING 日志。
    if reactivation:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not _write_audit(db_path, [
            "",
            f"[{stamp}] 重激活 / 回滚:{active.version} → {target}(db={db_path})",
            *[f"  {line}" for line in reactivation],
        ]):
            print("警告:重激活留痕写入失败 —— **不拦激活**(见脚本内注释),"
                  "但请手动把本次回滚记进 §九。", file=sys.stderr)

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
    ap = argparse.ArgumentParser(description="章程切换器(plan v1.3-①-E / ⑦-E / v1.3.3 拆墙,staged 步骤 2)")
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
