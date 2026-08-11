"""④-C/④-D **周度按引擎归因 + 修改建议四分类**(plan §五 V2.2-④;需求原件 K8 §十六/§十七)。

本模块回答两个问题,**只回答这两个**::

    ④-C 「这一周,按 骨架 × 引擎 × 版本 分层看,各项到底是什么读数?」   → 统计量
    ④-D 「哪些选股因素该保留 / 观察 / 降权 / 淘汰?」                    → 建议(见下)

════════════════════════════════════════════════════════════════════════════
🔴 **本模块最重要的一条:分界线不在这里,也不在任何代码里**
════════════════════════════════════════════════════════════════════════════

K8 §十七 给的是**纯定性**的四句话 —— 「保留:持续有效 / 观察:样本不足 / 降权:辅助
有效 / 淘汰:持续失效」,**一个数字都没有**。把它翻成「n ≥ 20 算够」「正确率低多少算
淘汰」是**定量决策**,按 CLAUDE.md「🔴 定性需求不许自行定量」(2026-08-09 立规),
**必须由用户拍板**,⛔ 工程侧不得代定、⛔ 写进 PROJECT_PLAN 也不算已定案。

故本模块的机械层**只出统计量**;分类那一步是**参数化**的:

    · 分界线住**骨架包** `config.iteration`(与 `config.regime` 同款 provenance 形状),
      也就是说它只能**经四道闸、由用户拍板**进入系统 —— 与「零自动回写」同一条路。
    · 包里**没有**这一段时(现状),`classify_factors()` 对每一行返回
      `klass=None` + `klass_status='thresholds_undecided'` + 一句说明,
      **⛔ 不猜、⛔ 不用默认值、⛔ 不静默降级成 observe**。
      这不是"功能没做完",这是「**没有**」与「**没看**」必须分开的同一条纪律:
      分界线没定,分类结论就不存在,摆一个出来才是撒谎。

⛔ **零写回**(V2.1 裁定 #3 一字不变;K8 §十七「复盘板块输出修改建议。**用户确认后**,
新规则从下一版本生效」正好同向):本模块**零写库** —— 不写 `selection_packs`、不写
任何表。守门单测 AST 断言。唯一出口 = 复盘板块的**移交件**(`review/handoff.py`),
用户带着它去策略台 → 新引擎包 → 四道闸 → `C2`/`Z2`/`Y2`。

════════════════════════════════════════════════════════════════════════════
🔴 **有效样本单位 = `D0 日期 × 篮子 × 引擎版本`**(K8.md §十七;V2.3.2-④-B 钉死)
════════════════════════════════════════════════════════════════════════════

即:**一个篮子在一个 D0 上算一个样本**,⛔ 不是一只票算一个、也⛔ 不是一次关口判定
算一个。现状**天然等于**这个定义,三条支撑事实(⛔ 别在别处重新发明一套计数):

    · `selection_clock` **一篮一行**(`basket_id UNIQUE`,`db.py` DDL 注释原文
      「一篮一次,UNIQUE 即『只结一次案』」)→ `n = len(members)` 数的就是篮子数;
    · `stratum_of()` 的四元键已含**引擎码 × 引擎版本** → 分层天然按引擎版本切开;
    · 裁定 #9 **单篮子单引擎** → 一个篮子不会同时属于两个引擎,不存在重复计。

⚠ **成员多的篮子不会被重复计**:唯一可能按成员展开的是 `gate` 维(核心关 / 位置关是
成员级判定),`_gate_factor_rows` 因此**按 `basket_id` 显式去重**。⛔ 删掉那段去重 =
成员多的篮子在 `gate` 维上被数很多次,而它看起来只是"样本变多了"。

⛔ **不为"混引擎篮子"预建代码**:裁定 #9 之下那个场景不可达,写了也验不了。
(以上四条由 `tests/test_eval_iteration.py` 的守门单测正面钉死。)

**⛔ 不新造统计口径**(plan ④-C 原文):
    · 「正确率」= `selection/verification_rules.STATE_SCORES`(既有登记项:verified=1 /
      partial=0.5 / falsified=0,unclear 不进分母)—— ⛔ 本模块不定义第二套。
    · 「显著性」= 既有 `eval/placebo.py` 的安慰剂对照臂 + `eval/metrics.verdict()`
      的样本闸(`MIN_CONCLUSION_DAYS`)—— ⛔ 本模块不写任何检验。
    · 「可交易收益」= `eval/exit_sim.py` 唯一判分源 —— 本模块**一行成交/退出逻辑都没有**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.eval.metrics import LEGACY_ENGINE
from neckline.selection.verification_rules import (
    STATE_FALSIFIED,
    STATE_SCORES,
    STATES,
    accuracy_from_counts,
)

logger = logging.getLogger(__name__)

REPORT_SPEC_VERSION = "iteration_v1"

# —— K8 §十七 的四个词(⛔ 不增不减、⛔ 不改名:归因要按码分桶)————————————
KLASS_KEEP = "keep"              # 保留:持续有效
KLASS_OBSERVE = "observe"        # 观察:样本不足
KLASS_DOWNWEIGHT = "downweight"  # 降权:辅助有效
KLASS_RETIRE = "retire"          # 淘汰:持续失效
KLASS_ORDER: Tuple[str, ...] = (KLASS_KEEP, KLASS_OBSERVE, KLASS_DOWNWEIGHT, KLASS_RETIRE)
KLASS_LABELS: Dict[str, str] = {
    KLASS_KEEP: "保留(持续有效)",
    KLASS_OBSERVE: "观察(样本不足)",
    KLASS_DOWNWEIGHT: "降权(辅助有效)",
    KLASS_RETIRE: "淘汰(持续失效)",
}

#: **不是第五类** —— 是「这一步还没法做」。分界线未由用户拍板时 `klass=None` 配它。
THRESHOLDS_UNDECIDED = "thresholds_undecided"
#: 分界线在,但这一行的读数算不出(⛔ 与"样本不足"分开:算不出 ≠ 样本少)。
STAT_UNAVAILABLE = "stat_unavailable"
KLASS_DECIDED = "decided"
#: V2.3.2-④-C:样本够、读数也够淘汰,**但失败集中在单一行情状态** → 不给 `retire`,
#: 降为 `observe`。⛔ 这不是第五类,是「这一步先别下全局结论」。
KLASS_FAILURES_CONCENTRATED = "failures_concentrated_in_single_regime"

#: V2.3.2-⑧-3(2026-08-11 策略线裁定,用户已确认,⛔ 不得重开):
#: **失败样本中至少 70% 落在同一种行情状态,视为集中在单一行情状态**。
#: · 分母 = 该因素的**全部失败样本**;
#: · 行情状态取 **D0 当时保存的**三态(`selection_clock.regime_at_d0`),⛔ 不用当前重算值;
#: · 70% 只表示失败具有明显状态集中性,**⛔ 不表示该因素无效**;
#: · 🔴 状态集中时**⛔ 不直接提出全局淘汰** —— 应优先研究该因素在对应行情状态下的
#:   降权或停用,且仍受 `retire_min_n` 门槛约束、并由用户最终确认。
#: ⚠ 这个数是**裁定给的**,不是工程侧选的(K8.md §十七 原文只有「集中在单一行情状态」
#: 这句定性描述,把它翻成 70% 是 2026-08-11 由用户拍板的)。
REGIME_CONCENTRATION_RATIO = 0.70

#: 「失败样本」= `tier_accuracy` 判为 `falsified` 的那些(⛔ 不新造统计口径:
#: 四态词表与计分的唯一源仍是 `selection/verification_rules.STATE_SCORES`,
#: 其中 `falsified` 计 0.0 —— 它就是这套词表里"失败"的那一态)。
#: ⚠ `partial`(0.5)**不算失败**、`unclear` 本就不进分母。

#: 分界线在骨架包里的落点(形状 = `config.regime` 同款 provenance 叶子)。
CONFIG_SECTION = "iteration"
THRESHOLD_KEYS: Tuple[str, ...] = ("min_n", "retire_min_n")

# —— 因素维度(K8 §十六「各因素对正确率的贡献」的"因素"就是这些切面)——————
FACTOR_REGIME = "regime"                    # D0 行情状态三态
FACTOR_ENGINE = "engine"                    # C/Z/Y × 版本
FACTOR_TIER = "tier"                        # T1 / T2
FACTOR_UNTRIGGERED = "untriggered_reason"   # 未触发原因码
FACTOR_POSITION_VERDICT = "position_verdict"  # 位置关 LLM 判定(§七 P3-49 的证据面)
FACTOR_GATE = "gate"                        # 六关各自的 verdict(吃 gate_evaluations)
FACTOR_DIMENSIONS: Tuple[str, ...] = (
    FACTOR_REGIME, FACTOR_ENGINE, FACTOR_TIER, FACTOR_UNTRIGGERED,
    FACTOR_POSITION_VERDICT, FACTOR_GATE,
)

# —— 安慰剂对照的三个取值(读既有 `PlaceboReport.vs_random`,⛔ 不自己判显著)——
EDGE_BETTER = "better"
EDGE_WORSE = "worse"
EDGE_INCONCLUSIVE = "inconclusive"      # 样本没到既有结论线(`Verdict.conclusive=False`)
EDGE_UNAVAILABLE = "unavailable"        # 这一层压根没有对照臂产物


# ══════════════════════════════════════════════════════════════════════════
# 分界线(**只从包里来**;包里没有 = 没有,⛔ 不给默认值)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class IterationThresholds:
    """四分类的两条分界线。**没有默认值是刻意的** —— 这个 dataclass 只能由
    `from_pack_config()` 从骨架包里读出来,读不到就是 `None`,不存在"缺省的线"。

    · `min_n`        —— 低于它 = K8「样本不足」→ `observe`
    · `retire_min_n` —— 判「持续失效」所需的最低样本量(**必然 ≥ `min_n`**)

    ⚠ 「显著优于 / 劣于」**不在这里配** —— 它复用既有安慰剂对照臂的结论
    (`eval/placebo.py` 的 `vs_random`,其样本闸是既有 `MIN_CONCLUSION_DAYS`),
    ⛔ 本模块不新造第二套显著性口径,也就不需要第三个数。
    """

    min_n: int
    retire_min_n: int
    provenance: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_pack_config(cls, config: Optional[Mapping[str, Any]]
                         ) -> Tuple[Optional["IterationThresholds"], List[str]]:
        """`config.iteration` → 分界线。返回 `(阈值 | None, 问题清单)`。

        **形状严格**(与 `config.regime` 同款):每个键是
        `{"value": <int>, "provenance": {...}}`。缺段 → `(None, [])`(**不是错误**,
        是"还没拍板");有段但形状不对 → `(None, [错误说明…])`,**fail loud**,
        ⛔ 不静默退回"没有"(那会把"配错了"读成"没配")。
        """
        problems: List[str] = []
        section = (config or {}).get(CONFIG_SECTION)
        if section is None:
            return None, problems
        if not isinstance(section, Mapping):
            return None, [f"config.{CONFIG_SECTION} 必须是对象"]
        values: Dict[str, int] = {}
        for key in THRESHOLD_KEYS:
            leaf = section.get(key)
            if leaf is None:
                problems.append(f"config.{CONFIG_SECTION}.{key} 缺失")
                continue
            v = leaf.get("value") if isinstance(leaf, Mapping) else leaf
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                problems.append(f"config.{CONFIG_SECTION}.{key}.value 必须是非负整数,得到 {v!r}")
                continue
            values[key] = int(v)
        if problems:
            return None, problems
        if values["retire_min_n"] < values["min_n"]:
            return None, [f"config.{CONFIG_SECTION}.retire_min_n({values['retire_min_n']}) "
                          f"不得小于 min_n({values['min_n']})"]
        prov = {k: (section.get(k) or {}).get("provenance")
                for k in THRESHOLD_KEYS if isinstance(section.get(k), Mapping)}
        return cls(min_n=values["min_n"], retire_min_n=values["retire_min_n"],
                   provenance=prov), problems


def resolve_thresholds(*, db_path: Optional[Path] = None
                       ) -> Tuple[Optional[IterationThresholds], List[str]]:
    """从**现役骨架线**(`selection_packs.line_code='V'`)读分界线。

    读不到现役骨架线 / 包里没有那一段 → `(None, [])`。**这是当前的真实状态**:
    截至 V2.2-④ 落地,`packs/K8-skeleton.json` 里**没有** `config.iteration` 段,
    因为那两个数还没有人拍板。⛔ 不要在这里"临时补一个"。
    """
    try:
        from neckline.selection.pack import get_active_skeleton

        pack = get_active_skeleton(db_path=db_path)
    except Exception as exc:  # noqa: BLE001 —— 读包失败不该掀翻整份周报
        logger.warning("[iteration] 现役骨架线读取失败,分界线按未配置处理", exc_info=True)
        return None, [f"骨架线读取失败:{type(exc).__name__}"]
    if pack is None:
        return None, []
    return IterationThresholds.from_pack_config(pack.config)


# ══════════════════════════════════════════════════════════════════════════
# 分层键:骨架 × 引擎 × 版本 × 条件集
# ══════════════════════════════════════════════════════════════════════════

# 历史 `LEGACY` 包(K4/K7 单包制)在引擎两位上的占位 —— **单一源在
# `eval/metrics.LEGACY_ENGINE`**(篮子面板与结案件面板必须落同一个串,否则同一批
# 历史样本在两份成绩单上会分到两个不同的层)。⛔ 不在这里再写一份字面量。


def stratum_of(closure: Mapping[str, Any]) -> Tuple[str, str, str, str]:
    """结案件 → 四元分层键 `(骨架版本, 引擎码, 引擎版本, 条件集版本)`。

    老样本(K8 之前)`engine_code` 为空 → 两位都落 `LEGACY`,骨架位退回它当时的
    `pack_version`(`selection_clock.build_closure` 已在写侧做过同样的退回)。
    """
    eb = closure.get("engine_breakdown") or {}
    return (
        str(closure.get("skeleton_version") or LEGACY_ENGINE),
        str(eb.get("engine_code") or LEGACY_ENGINE),
        str(eb.get("engine_version") or LEGACY_ENGINE),
        str(closure.get("verification_ruleset_version") or LEGACY_ENGINE),
    )


def _mech_item(closure: Mapping[str, Any], key: str) -> Dict[str, Any]:
    v = (closure.get("mech") or {}).get(key)
    return dict(v) if isinstance(v, Mapping) else {}


#: `regime_at_d0` 为空时的哨兵串(D0 当天 `market_regime_daily` 缺行 —— 该列可空,
#: 「如实,⛔ 不填默认态」)。⚠ 它**不是**第四种行情状态。
_REGIME_UNKNOWN = "(未登记)"


def failure_regime_counts(closures: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """**失败样本**按 D0 当时保存的行情状态分桶(V2.3.2-④-C / ⑧-3 的判据原料)。

    · 「失败样本」= `tier_accuracy == 'falsified'`(⛔ 不新造统计口径:四态词表的唯一源
      仍是 `verification_rules`;`partial` 不算失败、`unclear` 本就不进分母);
    · 状态取 **`regime_at_d0`**(D0 当时保存的那一份),⛔ 不用当前重算值(⑧-3 逐字)。"""
    out: Dict[str, int] = {}
    for c in closures:
        if _state_of(c) != STATE_FALSIFIED:
            continue
        regime = c.get("regime_at_d0")
        key = str(regime) if regime else _REGIME_UNKNOWN
        out[key] = out.get(key, 0) + 1
    return out


def _failures_concentrated(st: "FactorStat") -> bool:
    """⑧-3:该因素的失败样本是否**集中在单一行情状态**(≥ 70%)。

    🔴 **没有失败样本 → `False`(= 不拦)**:一个"零失败但正确率低于基线"的因素
    (全是 partial)谈不上"失败集中",拦它反而是拿不存在的证据挡一条结论。"""
    conc = st.regime_concentration
    return conc is not None and conc >= REGIME_CONCENTRATION_RATIO


def _state_of(closure: Mapping[str, Any]) -> Optional[str]:
    """一个结案样本的四态(⑨ `tier_accuracy` 列)。不在四态里 → `None`(不计入分母)。"""
    st = closure.get("tier_accuracy")
    return str(st) if st in STATES else None


def _counts(closures: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {s: 0 for s in STATES}
    for c in closures:
        st = _state_of(c)
        if st:
            out[st] += 1
    return out


def accuracy_of(closures: Sequence[Mapping[str, Any]]) -> Tuple[Optional[float], int]:
    """一组结案样本的「正确率」—— **换算走 `verification_rules` 唯一源**。

    ⚠ 这是「四态折成一个 0–1 的数」,**不是**「多少算好」;后者是分界线,见模块头。
    """
    return accuracy_from_counts(_counts(closures))


# ══════════════════════════════════════════════════════════════════════════
# ④-C 选股侧成绩单(K8 §十六 八项)
# ══════════════════════════════════════════════════════════════════════════

#: K8 §十六 选股侧八项的稳定码(顺序即原文顺序,⛔ 不重排)。
SELECTION_ITEMS: Tuple[str, ...] = (
    "regime_accuracy",          # 行情状态判断准确率
    "engine_by_regime",         # 各行情状态下的引擎表现
    "tier_signal_accuracy",     # T1、T2 入场信号正确率
    "engine_versions",          # C、Z、Y 各版本表现
    "driver_effectiveness",     # 主要驱动有效性
    "support_and_liftoff",      # 支撑与启动形态表现
    "core_vs_alternates",       # 核心标的与替代标的表现
    "factor_contribution",      # 各因素对正确率的贡献(← gate_evaluations)
)

#: K8 §十六 交易侧六项的稳定码。
TRADE_ITEMS: Tuple[str, ...] = (
    "thesis_accuracy",          # 原始判断正确率
    "plan_consistency",         # 入场与预案的一致性
    "exit_quality_on_thesis",   # 判断成立时的离场质量
    "exit_quality_on_decay",    # 上涨效率下降时的主动离场质量
    "stop_quality_on_failure",  # 判断失效时的止损质量
    "user_pick_vs_all",         # 用户实际选择相对全部候选的表现
)


def _bucket(closures: Sequence[Mapping[str, Any]], key_fn) -> Dict[str, Dict[str, Any]]:
    acc: Dict[str, List[Mapping[str, Any]]] = {}
    for c in closures:
        k = key_fn(c)
        if k is None:
            continue
        acc.setdefault(str(k), []).append(c)
    out: Dict[str, Dict[str, Any]] = {}
    for k in sorted(acc):
        a, denom = accuracy_of(acc[k])
        out[k] = {"n": len(acc[k]), "scored": denom, "accuracy": a,
                  "distribution": _counts(acc[k])}
    return out


def _ratio(hits: int, total: int) -> Optional[float]:
    return (hits / total) if total else None


def selection_scoreboard(closures: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """选股侧八项(K8 §十六)。**样本 = `selection_clock` 全部 T1/T2**(不是买过的那些)。

    每一项都只是**读数**:样本量 + 分布 + 正确率。⛔ 没有一项给「好 / 不好」的结论 ——
    那要么等分界线(④-D),要么就是人看着办。
    """
    n = len(closures)
    triggered = [c for c in closures
                 if (_mech_item(c, "untriggered_reason").get("triggered") is True)]
    led = [c for c in closures if _mech_item(c, "core_strength").get("led") is True]
    led_judged = [c for c in closures if _mech_item(c, "core_strength").get("led") is not None]
    with_liftoff = [c for c in closures
                    if _mech_item(c, "liftoff_signal").get("available")]

    return {
        "samples": n,
        "regime_accuracy": _bucket(closures, lambda c: c.get("regime_at_d0")),
        "engine_by_regime": _bucket(
            closures,
            lambda c: (None if not c.get("regime_at_d0") else
                       f"{(c.get('engine_breakdown') or {}).get('engine_code') or LEGACY_ENGINE}"
                       f"@{c.get('regime_at_d0')}"),
        ),
        "tier_signal_accuracy": _bucket(closures, lambda c: f"T{c.get('covered_tier')}"),
        "engine_versions": _bucket(
            closures,
            lambda c: "{}/{}".format(
                (c.get("engine_breakdown") or {}).get("engine_code") or LEGACY_ENGINE,
                (c.get("engine_breakdown") or {}).get("engine_version") or LEGACY_ENGINE),
        ),
        "driver_effectiveness": _bucket(
            closures, lambda c: _mech_item(c, "driver_persistence").get("state")),
        "support_and_liftoff": {
            "with_d1_metrics": len(with_liftoff),
            "entry_triggered": len(triggered),
            "entry_trigger_rate": _ratio(len(triggered), n),
            "by_untriggered_reason": _bucket(closures, lambda c: c.get("untriggered_reason")),
            "note": ("裁定 #11 之后机械层不产「起跳态」——本项按 D0 位置关判定 × D1 后续"
                     "表现分桶(见 by_position_verdict),这正是 §七 P3-49 唯一认的证据面"),
            "by_position_verdict": _bucket(closures, _dominant_position_verdict),
        },
        "core_vs_alternates": {
            "judged": len(led_judged),
            "led": len(led),
            "led_rate": _ratio(len(led), len(led_judged)),
            "note": "龙头认定取 D0 冻结卡,⛔ 不拿 D1 涨最多那只回头当龙头",
        },
        "factor_contribution": {
            "note": ("逐关贡献见 `factors` 段(维度 gate)—— 数据源是 ③ 的 "
                     "`gate_evaluations`,⛔ 不另建第二份关口台账"),
        },
    }


def _dominant_position_verdict(closure: Mapping[str, Any]) -> Optional[str]:
    """一篮的位置关判定代表值 = **成员判定里最"差"的那一个**(`unfit` > `weak` > `ok`)。

    取最差而不是取多数:位置关是**降级**关(裁定 #11「只降级不除名」),一篮里只要有
    成员被判 `unfit`,这一篮在归因上就该记在 `unfit` 桶里 —— 取多数会把它洗白。
    """
    verdicts = (_mech_item(closure, "liftoff_signal").get("d0_verdict") or {})
    if not isinstance(verdicts, Mapping) or not verdicts:
        return None
    order = {"unfit": 0, "weak": 1, "ok": 2}
    picks = [str((v or {}).get("position_verdict") or "") for v in verdicts.values()]
    picks = [p for p in picks if p in order]
    if not picks:
        return None
    return min(picks, key=lambda p: order[p])


def trade_scoreboard(clocks: Sequence[Mapping[str, Any]], *,
                     note_coverage: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """交易侧六项(K8 §十六)。**样本 = `trade_clock` 真实买入**。

    ⛔ **不判分**:这里既不算收益也不模拟退出(那永远只有 `eval/exit_sim.py` 一份);
    本函数出的是「这笔交易与它开仓时那份计划对不对得上」这一类**一致性读数**。
    """
    closed = [c for c in clocks if c.get("status") == "closed" and isinstance(c.get("final"), Mapping)]
    def _f(c: Mapping[str, Any], key: str) -> Dict[str, Any]:
        v = (c.get("final") or {}).get(key)
        return dict(v) if isinstance(v, Mapping) else {}

    in_zone = [c for c in closed if _f(c, "entry_price_position").get("in_entry_zone") is True]
    zone_judged = [c for c in closed
                   if _f(c, "entry_price_position").get("in_entry_zone") is not None]
    reached = [c for c in closed if _f(c, "target_zone_handling").get("reached_target") is True]
    reached_judged = [c for c in closed
                      if _f(c, "target_zone_handling").get("reached_target") is not None]
    eff = [_f(c, "upside_efficiency").get("ratio") for c in closed]
    eff = [float(x) for x in eff if isinstance(x, (int, float)) and not isinstance(x, bool)]
    reasons: Dict[str, int] = {}
    for c in closed:
        r = str(_f(c, "stop_after_invalidation").get("close_reason") or "(未登记)")
        reasons[r] = reasons.get(r, 0) + 1

    return {
        "trades": len(clocks),
        "running": sum(1 for c in clocks if c.get("status") == "running"),
        "closed": len(closed),
        "thesis_accuracy": {
            "note": ("原始判断正确率的样本源是**选股侧**的四态(同一批篮子),"
                     "交易侧只登记这笔仓来自哪一篮;⛔ 不在这里另算一套对错"),
            "with_source_basket": sum(1 for c in clocks if c.get("basket_id") is not None),
        },
        "plan_consistency": {
            "judged": len(zone_judged), "in_entry_zone": len(in_zone),
            "rate": _ratio(len(in_zone), len(zone_judged)),
            "above_max_chase": sum(
                1 for c in closed
                if _f(c, "entry_price_position").get("above_max_chase") is True),
        },
        "exit_quality_on_thesis": {
            "judged": len(reached_judged), "reached_target": len(reached),
            "rate": _ratio(len(reached), len(reached_judged)),
        },
        "exit_quality_on_decay": {
            "with_efficiency_reading": len(eff),
            "ratio_median": (sorted(eff)[len(eff) // 2] if eff else None),
            "note": ("上涨效率只出比值,⛔ 无阈值、无「该走了」结论"
                     "(K8 §十三「保留主观换股权,不设机械规则」)"),
        },
        "stop_quality_on_failure": {
            "by_close_reason": dict(sorted(reasons.items())),
            "note": "本项只作警戒记录,违纪判定归周复盘对账(V2.2-⑤ 起已降级为警戒)",
        },
        "user_pick_vs_all": {
            "note": ("「用户实际选择 vs 全部候选」的对照走既有 "
                     "`eval/metrics.selected_vs_not`(⛔ 不另建一份对照)"),
        },
        "note_coverage": dict(note_coverage or {}),
    }


# ══════════════════════════════════════════════════════════════════════════
# ④-D 因素统计量 → 四分类建议
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FactorStat:
    """一个因素取值在某个分层里的**统计量**(⛔ 不含任何结论)。"""

    stratum: Tuple[str, str, str, str]
    dimension: str
    value: str
    n: int                                # 该因素取值命中的结案样本数
    scored: int                           # 计入正确率分母的样本数(unclear 不计)
    accuracy: Optional[float]
    baseline_accuracy: Optional[float]    # 同分层全样本的正确率
    delta: Optional[float]                # accuracy − baseline_accuracy
    distribution: Dict[str, int]
    placebo_edge: str = EDGE_UNAVAILABLE
    evidence: Dict[str, Any] = field(default_factory=dict)
    # V2.3.2-④-C:**失败样本**(`tier_accuracy='falsified'`)按 **D0 当时保存的**
    # 行情状态分桶(`selection_clock.regime_at_d0`)。⛔ 不用当前重算值(⑧-3 逐字)。
    # ⚠ 键 `None` 用哨兵串 `_REGIME_UNKNOWN` 表示「D0 当天没有行情状态行」——
    # 它**照样进分母**(⑧-3:分母 = 全部失败样本),只是不归任何一个状态。
    failure_regimes: Dict[str, int] = field(default_factory=dict)

    @property
    def factor(self) -> str:
        return f"{self.dimension}={self.value}"

    @property
    def failure_samples(self) -> int:
        """全部失败样本数(⑧-3 的**分母**)。"""
        return sum(self.failure_regimes.values())

    @property
    def regime_concentration(self) -> Optional[float]:
        """失败样本里**最集中的那个行情状态**占的比例。`None` = 没有失败样本(算不出)。

        ⚠ 状态未知的失败样本**照样在分母里**(⑧-3:分母 = 全部失败样本),因此
        「一半失败样本查不到状态」会如实把集中度压下去 —— 这是诚实,不是 bug。"""
        total = self.failure_samples
        if total <= 0:
            return None
        known = {k: v for k, v in self.failure_regimes.items() if k != _REGIME_UNKNOWN}
        return (max(known.values()) / total) if known else 0.0

    @property
    def dominant_failure_regime(self) -> Optional[str]:
        """失败样本最集中的那个状态(并列取字典序小的,确定性)。"""
        known = {k: v for k, v in self.failure_regimes.items() if k != _REGIME_UNKNOWN}
        if not known:
            return None
        top = max(known.values())
        return sorted(k for k, v in known.items() if v == top)[0]

    def to_dict(self) -> Dict[str, Any]:
        sk, ec, ev, rs = self.stratum
        return {
            "skeletonVersion": sk, "engineCode": ec, "engineVersion": ev, "rulesetVersion": rs,
            "dimension": self.dimension, "value": self.value, "factor": self.factor,
            "n": self.n, "scored": self.scored,
            "accuracy": self.accuracy, "baselineAccuracy": self.baseline_accuracy,
            "delta": self.delta, "distribution": dict(self.distribution),
            "placeboEdge": self.placebo_edge, "evidence": dict(self.evidence),
            # V2.3.2-④-C:失败样本的状态分布 + 集中度(⑧-3 的判据原料)。
            # **⛔ 只出读数,结论在 `classify_factors`** —— 摊在这里是为了让移交件里
            # 「为什么这条没给淘汰」查得到底。
            "failureRegimes": dict(self.failure_regimes),
            "failureSamples": self.failure_samples,
            "regimeConcentration": self.regime_concentration,
            "dominantFailureRegime": self.dominant_failure_regime,
        }


def placebo_edges(placebo_reports: Sequence[Any]) -> Dict[str, str]:
    """既有安慰剂对照臂 → `{pack_version: edge}`。**只读既有结论,⛔ 不自己判显著**。

    `Verdict.conclusive=False`(样本没到既有结论线)→ `inconclusive`,
    ⛔ 不因为"中位数看起来更高"就说它更好。
    """
    out: Dict[str, str] = {}
    for rep in placebo_reports or ():
        pack = getattr(rep, "pack_version", None) or (
            rep.get("packVersion") if isinstance(rep, Mapping) else None)
        vs = getattr(rep, "vs_random", None)
        if vs is None and isinstance(rep, Mapping):
            vs = rep.get("vsRandom")
        if not pack:
            continue
        if not isinstance(vs, Mapping) or not vs.get("conclusive"):
            out[str(pack)] = EDGE_INCONCLUSIVE
            continue
        detail = vs.get("detail") or {}
        real, rnd = detail.get("real"), detail.get("random")
        if not isinstance(real, (int, float)) or not isinstance(rnd, (int, float)):
            out[str(pack)] = EDGE_INCONCLUSIVE
            continue
        out[str(pack)] = EDGE_BETTER if float(real) > float(rnd) else EDGE_WORSE
    return out


def _gate_factor_rows(closures: Sequence[Mapping[str, Any]], *,
                      db_path: Optional[Path] = None) -> Dict[str, List[Mapping[str, Any]]]:
    """`gate_evaluations` → `{"gate=<关>:<verdict>": [命中的结案样本…]}`。

    K8 §十六 第 8 项「各因素对正确率的贡献」的**直接数据源**(plan ③-B 原文:
    「没有这张表,那一项算不出来」)。按 `(d0, basket_key)` 关联,⛔ 不做近似匹配。
    """
    by_key: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for c in closures:
        key = ((c.get("mech") or {}).get("meta") or {}).get("basket_key")
        if key:
            by_key[(str(c.get("d0_date")), str(key))] = c
    if not by_key:
        return {}
    out: Dict[str, List[Mapping[str, Any]]] = {}
    seen: Dict[str, set] = {}
    try:
        from neckline.selection.gates import load_gate_evaluations

        for day in sorted({d for d, _k in by_key}):
            for row in load_gate_evaluations(day, db_path=db_path):
                c = by_key.get((day, str(row.get("candidate_key"))))
                if c is None:
                    continue
                label = f"{row.get('gate')}:{row.get('verdict')}"
                bid = int(c.get("basket_id") or 0)
                # 成员级关口(核心/位置)一篮多行 —— **篮子只计一次**,
                # 否则成员多的篮子会在分母里被重复计,把"票多"读成"更有效"。
                if bid in seen.setdefault(label, set()):
                    continue
                seen[label].add(bid)
                out.setdefault(label, []).append(c)
    except Exception:  # noqa: BLE001 —— 关口台账读不到只让这一维空着
        logger.warning("[iteration] gate_evaluations 读取失败,factor 维度 gate 本期为空",
                       exc_info=True)
        return {}
    return out


def collect_factor_stats(
    closures: Sequence[Mapping[str, Any]],
    *,
    edges: Optional[Mapping[str, str]] = None,
    db_path: Optional[Path] = None,
) -> List[FactorStat]:
    """按 `骨架 × 引擎 × 版本 × 条件集` 分层,逐因素出统计量。

    层序与行序都是字典序 —— **确定性,重跑逐位可比**(同 `metrics.evaluate` 的既定
    惯例;不确定的排序会让"这周和上周比"变成比较噪声)。
    """
    edges = dict(edges or {})
    strata: Dict[Tuple[str, str, str, str], List[Mapping[str, Any]]] = {}
    for c in closures:
        strata.setdefault(stratum_of(c), []).append(c)

    out: List[FactorStat] = []
    for stratum in sorted(strata):
        rows = strata[stratum]
        base_acc, _base_n = accuracy_of(rows)
        pack = ((rows[0].get("mech") or {}).get("meta") or {}).get("pack_version")
        edge = edges.get(str(pack), EDGE_UNAVAILABLE) if pack else EDGE_UNAVAILABLE

        dims: List[Tuple[str, Dict[str, List[Mapping[str, Any]]]]] = []
        for dim, key_fn in (
            (FACTOR_REGIME, lambda c: c.get("regime_at_d0")),
            (FACTOR_ENGINE, lambda c: "{}/{}".format(
                (c.get("engine_breakdown") or {}).get("engine_code") or LEGACY_ENGINE,
                (c.get("engine_breakdown") or {}).get("engine_version") or LEGACY_ENGINE)),
            (FACTOR_TIER, lambda c: f"T{c.get('covered_tier')}"),
            (FACTOR_UNTRIGGERED, lambda c: c.get("untriggered_reason")),
            (FACTOR_POSITION_VERDICT, _dominant_position_verdict),
        ):
            grouped: Dict[str, List[Mapping[str, Any]]] = {}
            for c in rows:
                k = key_fn(c)
                if k is None:
                    continue
                grouped.setdefault(str(k), []).append(c)
            dims.append((dim, grouped))
        dims.append((FACTOR_GATE, {
            k: [c for c in v if stratum_of(c) == stratum]
            for k, v in _gate_factor_rows(rows, db_path=db_path).items()
        }))

        for dim, grouped in dims:
            for value in sorted(grouped):
                members = grouped[value]
                if not members:
                    continue
                acc, scored = accuracy_of(members)
                out.append(FactorStat(
                    stratum=stratum, dimension=dim, value=value,
                    n=len(members), scored=scored, accuracy=acc,
                    baseline_accuracy=base_acc,
                    delta=(None if (acc is None or base_acc is None) else acc - base_acc),
                    distribution=_counts(members), placebo_edge=edge,
                    evidence={"packVersion": pack, "stratumSamples": len(rows)},
                    # ④-C:失败样本的 D0 状态分布 —— `classify_factors` 只看 `FactorStat`,
                    # 拿不到原始结案件,故必须在这里就把它算好带上去。
                    failure_regimes=failure_regime_counts(members),
                ))
    return out


def classify_factors(
    stats: Sequence[FactorStat],
    thresholds: Optional[IterationThresholds] = None,
) -> List[Dict[str, Any]]:
    """④-D 四分类建议。**每行 `{factor, klass, n, evidence, suggestion}`**(plan 定的形状)。

    🔴 **`thresholds is None` 时一行都不分类** —— `klass=None` +
    `klass_status='thresholds_undecided'`,`suggestion` 直接把「缺哪两个数、该怎么定」
    说给用户听。⛔ 不猜、⛔ 不套默认值、⛔ 不静默降级成 `observe`
    (那会让"还没决定"长得跟"样本不足"一模一样)。

    分界线在时的判据(**每一条都由传入的那两个数驱动,代码里零硬编数字**)::

        n < min_n                                            → observe     样本不足
        delta 算不出                                          → observe     算不出 ≠ 无效
        delta > 0 且 安慰剂对照判「优于随机」                    → keep        持续有效
        n ≥ retire_min_n 且 delta < 0 且未判「优于随机」        → retire      持续失效
        其余                                                  → downweight  辅助有效
    """
    out: List[Dict[str, Any]] = []
    for st in stats:
        row: Dict[str, Any] = dict(st.to_dict())
        if thresholds is None:
            row.update(
                klass=None,
                klassStatus=THRESHOLDS_UNDECIDED,
                klassLabel=None,
                suggestion=(
                    "统计量已备齐,**四分类尚不可给** —— K8 §十七 只给了定性描述"
                    "(保留 / 观察 / 降权 / 淘汰),没有给「多少样本算够」「差多少算失效」"
                    "这两个数。请拍板 `min_n`(低于它一律判「观察:样本不足」)与 "
                    "`retire_min_n`(判「淘汰:持续失效」所需的最低样本量),经四道闸写进"
                    "骨架包 `config.iteration` 后,本段自动开始给分类。"
                    "⛔ 系统不会替你选这两个数。"
                ),
            )
            out.append(row)
            continue

        if st.n < thresholds.min_n:
            klass, why = KLASS_OBSERVE, (
                f"样本 {st.n} < 分界线 min_n={thresholds.min_n},按 K8「观察:样本不足」处理,"
                f"⛔ 本期不给有效性结论")
            status = KLASS_DECIDED
        elif st.delta is None:
            klass, why = KLASS_OBSERVE, (
                "该因素的正确率算不出(四态样本全为 unclear / 未判定)——"
                "「算不出」不是「无效」,继续观察")
            status = STAT_UNAVAILABLE
        elif st.delta > 0 and st.placebo_edge == EDGE_BETTER:
            klass, why = KLASS_KEEP, (
                f"正确率高于本层基线 {st.delta:+.3f},且安慰剂对照判「优于随机同规模篮子」"
                f"—— 按 K8「保留:持续有效」")
            status = KLASS_DECIDED
        elif (st.n >= thresholds.retire_min_n and st.delta < 0
                and st.placebo_edge != EDGE_BETTER
                and _failures_concentrated(st)):
            # —— V2.3.2-④-C(⑧-3 拍板):**淘汰前的行情状态集中度检查** ————————
            # 🔴 样本够、读数也够淘汰,**但失败 ≥70% 落在同一种行情状态** → ⛔ 不给
            # `retire`,降为 `observe`。⑧-3 逐字:「状态集中时**不直接提出全局淘汰**,
            # 应优先研究该因素**在对应行情状态下的降权或停用**」。
            # ⚠ 两道闸**先后次序写死**:先看 n(上面那个 `and`),再看集中度。
            conc = st.regime_concentration or 0.0
            klass, why = KLASS_OBSERVE, (
                f"样本 {st.n} ≥ retire_min_n={thresholds.retire_min_n} 且正确率低于基线 "
                f"{st.delta:+.3f},**但 {st.failure_samples} 个失败样本里有 "
                f"{conc:.0%} 落在同一种行情状态**({st.dominant_failure_regime})—— "
                f"达到 {REGIME_CONCENTRATION_RATIO:.0%} 集中度线,按 K8 §十七 "
                f"**不提全局淘汰**;⛔ 这不表示该因素无效,应优先研究它在该状态下的"
                f"降权或停用(仍需你最终确认)")
            status = KLASS_FAILURES_CONCENTRATED
        elif (st.n >= thresholds.retire_min_n and st.delta < 0
                and st.placebo_edge != EDGE_BETTER):
            klass, why = KLASS_RETIRE, (
                f"样本 {st.n} ≥ retire_min_n={thresholds.retire_min_n},正确率低于本层基线 "
                f"{st.delta:+.3f},安慰剂对照未判「优于随机」—— 按 K8「淘汰:持续失效」")
            status = KLASS_DECIDED
        else:
            klass, why = KLASS_DOWNWEIGHT, (
                f"样本够但优势不成立(相对本层基线 {st.delta:+.3f},安慰剂对照 "
                f"{st.placebo_edge})—— 按 K8「降权:辅助有效」")
            status = KLASS_DECIDED

        row.update(
            klass=klass, klassStatus=status, klassLabel=KLASS_LABELS.get(klass),
            suggestion=why + "。⛔ 这只是建议:改包唯一通道是你带材料去策略台 → 新引擎版本 → 四道闸。",
        )
        out.append(row)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 一份周度迭代产物(装配;**零写库**)
# ══════════════════════════════════════════════════════════════════════════

def build_iteration_report(
    closures: Sequence[Mapping[str, Any]],
    *,
    clocks: Sequence[Mapping[str, Any]] = (),
    placebo: Sequence[Any] = (),
    note_coverage: Optional[Mapping[str, Any]] = None,
    thresholds: Optional[IterationThresholds] = None,
    threshold_problems: Sequence[str] = (),
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """④-C + ④-D 的一份产物(纯字典,给周度 unit 落盘 / 移交件排版)。

    ⛔ **零写库**:本函数(以及本模块)不写 `selection_packs`、不写任何表 ——
    守门单测 AST 断言(V2.1 裁定 #3「零自动回写」)。
    """
    stats = collect_factor_stats(closures, edges=placebo_edges(placebo), db_path=db_path)
    strata = sorted({st.stratum for st in stats})
    return {
        "specVersion": REPORT_SPEC_VERSION,
        "samples": {"selectionClock": len(closures), "tradeClock": len(clocks)},
        "strataKey": ["skeletonVersion", "engineCode", "engineVersion", "rulesetVersion"],
        "strata": [{"skeletonVersion": s[0], "engineCode": s[1],
                    "engineVersion": s[2], "rulesetVersion": s[3]} for s in strata],
        "selection": {
            "byStratum": [
                {"skeletonVersion": s[0], "engineCode": s[1], "engineVersion": s[2],
                 "rulesetVersion": s[3],
                 **selection_scoreboard([c for c in closures if stratum_of(c) == s])}
                for s in strata
            ],
            "overall": selection_scoreboard(closures),
        },
        "trade": trade_scoreboard(clocks, note_coverage=note_coverage),
        "thresholds": (
            {"available": True, "minN": thresholds.min_n,
             "retireMinN": thresholds.retire_min_n, "provenance": thresholds.provenance}
            if thresholds is not None else
            {"available": False,
             "unavailableReason": (
                 "骨架包 `config.iteration` 未配置四分类分界线 —— K8 §十七 只给定性描述,"
                 "这两个数必须由用户拍板后经四道闸进包;⛔ 系统不设默认值。"),
             "problems": list(threshold_problems)}
        ),
        "suggestions": classify_factors(stats, thresholds),
        "disclaimer": (
            "四分类是**建议**,不是动作:系统攒证据、用户拍板改包。"
            "⛔ 本模块零写回选股包(V2.1 裁定 #3 / K8 §十七「用户确认后,新规则从下一版本生效」)。"
        ),
    }


__all__ = [
    "REPORT_SPEC_VERSION",
    "KLASS_KEEP", "KLASS_OBSERVE", "KLASS_DOWNWEIGHT", "KLASS_RETIRE",
    "KLASS_ORDER", "KLASS_LABELS",
    "THRESHOLDS_UNDECIDED", "STAT_UNAVAILABLE", "KLASS_DECIDED",
    "CONFIG_SECTION", "THRESHOLD_KEYS", "IterationThresholds", "resolve_thresholds",
    "FACTOR_DIMENSIONS", "FACTOR_REGIME", "FACTOR_ENGINE", "FACTOR_TIER",
    "FACTOR_UNTRIGGERED", "FACTOR_POSITION_VERDICT", "FACTOR_GATE",
    "EDGE_BETTER", "EDGE_WORSE", "EDGE_INCONCLUSIVE", "EDGE_UNAVAILABLE",
    "LEGACY_ENGINE", "stratum_of", "accuracy_of",
    "SELECTION_ITEMS", "TRADE_ITEMS", "selection_scoreboard", "trade_scoreboard",
    "FactorStat", "placebo_edges", "collect_factor_stats", "classify_factors",
    "build_iteration_report",
]

# `STATE_SCORES` 在本模块只经 `accuracy_from_counts` 间接使用;显式引一次是为了让
# 「正确率换算的唯一源在哪」一眼可见(⛔ 别删成"未使用的 import")。
_ACCURACY_SOURCE = STATE_SCORES
