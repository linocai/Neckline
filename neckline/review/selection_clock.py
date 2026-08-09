"""④-A **选股时钟**(plan §五 V2.2-④-A;需求原件 K8 §十四)。

K8 §十四 四条规则,逐条落成机器判据::

    · 覆盖 D0 的**全部** T1、T2 篮子      → `load_baskets_for_date(d0, tiers=(1, 2))`
    · D1 收盘统一验证一次                 → `close_day(d1, d0=…)` 一次跑完当天全部
    · **用户是否买入不影响样本**          → 本模块**零 import 持仓**(见下「结构性保证」)
    · D1 验证完成后**结案**               → `INSERT OR IGNORE`,`basket_id` UNIQUE

**九项验证内容**(K8 §十四 原文顺序即 `mech_json` 顶层键顺序)::

    ① regime_at_d0                D0 行情状态及增强与减弱方向
    ② driver_persistence          驱动持续性
    ③ sector_sync                 板块协同
    ④ core_strength               核心标的强度
    ⑤ entry_zone_triggered        入场区间触发情况
    ⑥ liftoff_signal              「落地起跳」信号
    ⑦ intraday_support_and_close  触发后的分时承接与收盘结果
    ⑧ untriggered_reason          未触发原因
    ⑨ tier_accuracy               T1、T2 分层准确性

**⛔ 不重写既有实现**(plan ④-A 原文):②③④⑦ 四项**直接复用** ⑨ 日复盘
(`review/basket_review.py`)已经算好的九项机械判 —— 本模块吃它的 `mech_json`,
不重新读一遍行情。真正新写的只有三项:⑤(卡上冻结的建仓观察区间 × D1 最高/最低价)、
⑥(D1 的 `landing_metrics_daily` 读数 + D0 位置关判定)、⑧(由 ⑤⑥ 派生)。

🔴 **「买没买不影响样本」做成结构性保证,不靠自觉**
    本模块**没有任何一条通往持仓的路** —— 不 import `neckline.sentinel.positions`、
    不 import `neckline.positions_entry`、SQL 里不出现持仓相关表名。守门单测
    `tests/test_selection_clock.py::test_write_path_never_imports_positions` 按 AST +
    文本双向扫描(同 `basket_cards` 冻结表「靠没有那条路担保」的既有体例)。
    ⚠ 谁要"顺手"在这里加一句「这篮有没有被买过」,先去读 §2.9-A 那一行:选股与持仓
    职责独立是**产品共识**,不是本模块的偏好。

🔴 **结案 = 只增不改**
    `save_closures` 只有 `INSERT OR IGNORE` 一条写路径;同篮二次调用 = 零新行、
    内容逐位不变。⚠ **与 `basket_review_daily` 的 `UNIQUE(basket_id, review_date)`
    覆盖式刻意不同**:那张是「每日复盘」(同日重跑可覆盖),这张是「**结案**」。
    ⛔ 别"统一"它们。

🔴 **未买入的票在结案后结束跟踪**(K8 §十四「双时钟衔接」第 4 条)—— 结案即终态,
    本模块**不产生任何后续行**。要继续跟的只有真买入那一支,那是交易时钟的事
    (`review/trade_clock.py`),两者之间**没有代码路径相连**。

⛔ **本块零新增 LLM 调用**(plan 附「成本与超时算术」第 6 行):选股时钟的结案叙述
**并进 ⑨ 复盘那一次** `TASK_REVIEW` 调用 —— 落在 `basket_review_daily.llm_text`,
本模块只在 `mech_json.meta.narrative_ref` 里指过去,**自己一次模型都不调**。
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

TABLE = "selection_clock"

#: `mech_json` 的**形状**版本(形状变了就 bump;条件集版本是另一回事,那个跟卡走)。
CLOCK_MECH_SPEC_VERSION = "selection_clock_mech_v1"

#: 九项键(顺序 = K8 §十四 原文顺序,⛔ 不许重排、不许增减)。
MECH_ITEM_KEYS: Tuple[str, ...] = (
    "regime_at_d0",
    "driver_persistence",
    "sector_sync",
    "core_strength",
    "entry_zone_triggered",
    "liftoff_signal",
    "intraday_support_and_close",
    "untriggered_reason",
    "tier_accuracy",
)

#: 覆盖域 = D0 的**全部** T1/T2(K8 §十四 第 1 条;⛔ 不许按"买没买"过滤)。
COVERED_TIERS: Tuple[int, ...] = (1, 2)

#: 分层键缺失时的显式占位。**与 `eval/metrics.UNSET` 同值**(单测对拍;这里不 import
#: 那个模块是为了不把 `exit_sim` 判分链拖进晚间复盘的 import 图)。
UNSET_VERSION = "(未登记)"

# —— ⑧ 未触发原因码(机器可读;⛔ 别塞自由中文,周度按码分桶要靠它)————————
UNTRIGGERED_NONE = None                       # 触发了 → 该列 NULL
UNTRIGGERED_NO_ENTRY_ZONE = "no_entry_zone_on_card"     # 卡上一名成员都没有建仓区间
UNTRIGGERED_NO_D1_BAR = "no_d1_bar"                      # D1 全体成员无行情(停牌/数据缺口)
UNTRIGGERED_ZONE_NOT_REACHED = "zone_not_reached"        # 有区间、有行情,价格没进区间
UNTRIGGERED_UNKNOWN = "unknown"                          # 判不了(⛔ 不猜成"没触发")

# —— ⑨ 分层准确性:**取值域 = ⑦-b 四态 + 两个"没判"码**(⛔ 不自造第五态)————
#    语义写死:本列答的是「D0 那个判断在 D1 成不成立」,取的是 ⑧ 篮子验证的 EOD 定论
#    (`verification_rules.STATES`)。**它不是 0/1 的"对/错"** —— 把四态压成二元
#    需要一条"多少算对"的线,而那是**定量决策、⛔ 不由工程侧代定**(CLAUDE.md
#    「定性需求不许自行定量」)。周度侧要算「T1/T2 正确率」时,用的是
#    `verification_rules.STATE_SCORES` 那份**已登记的**既有换算,不在这里再造一份。
TIER_ACCURACY_NOT_EVALUATED = "not_evaluated"   # 当日那一拍没跑过(运维缺口 ≠ 策略失败)
TIER_ACCURACY_UNKNOWN = "unknown"               # 连"有没有跑过"都读不出


def _d(x: Any) -> str:
    return x if isinstance(x, str) else x.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if (f == f and not math.isinf(f)) else None


def _item(mech: Optional[Mapping[str, Any]], key: str) -> Dict[str, Any]:
    """取日复盘 `mech_json` 的某一项;缺 → 空 dict(调用方据此标 available=False)。"""
    v = (mech or {}).get(key)
    return dict(v) if isinstance(v, Mapping) else {}


def _missing(reason: str, **extra: Any) -> Dict[str, Any]:
    """统一的「这一项没取到」形状。⛔ 不许用 0 / 空串冒充读数(§3.8)。"""
    out: Dict[str, Any] = {"available": False, "source": None, "unavailable_reason": reason}
    out.update(extra)
    return out


# ══════════════════════════════════════════════════════════════════════════
# ① D0 行情状态及增强 / 减弱方向
# ══════════════════════════════════════════════════════════════════════════

def judge_regime_at_d0(d0: Any, *, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """① 读 `market_regime_daily` 的 D0 行。

    **缺行 = 不知道**(与 ② 的既定纪律同款):`available=False`,`regime=None` ——
    ⛔ 不许回填默认态 `trend_continuation`,那会把「系统缺席」讲成「市场是延续」。
    """
    from neckline.scan.regime_store import load_market_regime

    day = d0 if isinstance(d0, date) else datetime.strptime(str(d0), "%Y%m%d").date()
    try:
        row = load_market_regime(day, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 —— 可选情报的保险丝(§铁律)
        logger.warning("[selection_clock] D0 行情状态读取失败", exc_info=True)
        return _missing(f"行情状态表读取失败:{type(exc).__name__}", regime=None)
    if row is None:
        return _missing("D0 当日 market_regime_daily 缺行(状态层没跑过 / 非交易日)",
                        regime=None)
    return {
        "available": True,
        "source": "market_regime_daily",
        "unavailable_reason": None,
        "regime": row.get("regime"),
        "regime_reason": row.get("regime_reason"),
        "strengthening": row.get("strengthening"),
        "weakening": row.get("weakening"),
        "skeleton_version": row.get("skeleton_version"),
        "inputs": row.get("inputs"),
    }


# ══════════════════════════════════════════════════════════════════════════
# ② 驱动持续性 / ③ 板块协同 / ④ 核心标的强度 / ⑦ 分时承接与收盘
#    —— 四项**全部复用** ⑨ 日复盘已算好的机械判(plan ④-A「⛔ 不重写」)
# ══════════════════════════════════════════════════════════════════════════

def judge_driver_persistence(review_mech: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """② 驱动持续性 = ⑧ 篮子验证四态的 D1 定论(复用 ⑨ 的 `verification_timing`)。

    ⚠ **`verified` 只证明「没走坏 + 共振存在」**,⛔ 不是「驱动兑现」、更不是「可以追」
    (`verification_rules` 模块头语义红线,本项原样继承,渲染层不许改写这句)。
    """
    v = _item(review_mech, "verification_timing")
    if not v:
        return _missing("日复盘缺 verification_timing 项(那天没跑复盘)", state=None)
    return {
        "available": bool(v.get("available")),
        "source": "basket_review.verification_timing",
        "unavailable_reason": v.get("unavailable_reason"),
        "state": v.get("state"),
        "state_label": v.get("state_label"),
        "eod_state": v.get("eod_state"),
        "has_eod_verdict": v.get("has_eod_verdict"),
        "not_evaluated": v.get("not_evaluated"),
        "first_verified_at": v.get("first_verified_at"),
        "first_falsified_at": v.get("first_falsified_at"),
        "latched_falsified": v.get("latched_falsified"),
    }


def judge_sector_sync(review_mech: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """③ 板块协同 = 成员同向率(共振的直接证据)+ 收盘 RS(相对大盘的方向)。

    两个量刻意并排:同向率答「篮子内部齐不齐」,RS 答「相对市场强不强」——
    ⛔ 不合成一个分数(合成需要一组权重,那是定量决策)。
    """
    al = _item(review_mech, "member_alignment")
    rs = _item(review_mech, "close_rs")
    if not al and not rs:
        return _missing("日复盘缺 member_alignment / close_rs 两项")
    return {
        "available": bool(al.get("available")) or bool(rs.get("available")),
        "source": "basket_review.member_alignment+close_rs",
        "unavailable_reason": (None if (al.get("available") or rs.get("available"))
                               else (al.get("unavailable_reason") or rs.get("unavailable_reason"))),
        "observed": al.get("observed"),
        "member_count": al.get("member_count"),
        "up": al.get("up"), "down": al.get("down"), "flat": al.get("flat"),
        "alignment": al.get("alignment"),
        "dominant_direction": al.get("dominant_direction"),
        "index_code": rs.get("index_code"),
        "index_ret": rs.get("index_ret"),
        "excess_median": rs.get("excess_median"),
        "outperformers": rs.get("outperformers"),
    }


def judge_core_strength(review_mech: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """④ 核心标的强度 = D0 卡上认定的龙头 vs 同篮其余(复用 ⑨ 的 `leader_pull`)。

    ⚠ 龙头认定取的是 **D0 冻结卡**上的判断,⛔ 不拿 D1 涨得最多那只回头当龙头
    (那是拿结果当原因,复盘会自证正确 —— `basket_review.resolve_leaders` 已写死)。
    """
    lp = _item(review_mech, "leader_pull")
    if not lp:
        return _missing("日复盘缺 leader_pull 项")
    return {
        "available": bool(lp.get("available")),
        "source": "basket_review.leader_pull",
        "unavailable_reason": lp.get("unavailable_reason"),
        "leaders": lp.get("leaders"),
        "leader_ret_median": lp.get("leader_ret_median"),
        "others_ret_median": lp.get("others_ret_median"),
        "spread": lp.get("spread"),
        "led": lp.get("led"),
        "no_peer_group": lp.get("no_peer_group"),
    }


def judge_intraday_support_and_close(review_mech: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """⑦ 触发后的分时承接与收盘结果 = MFE/MAE + 开盘首方向 + 收盘结果。

    ⚠ `mfe_source='eod_approx'` 的诚实含义是「**幅度可信、时刻未知**」(缺盘中存拍),
    ⛔ 不是「幅度是估的」—— 原样透传 ⑨ 的口径,别在这里改写这句注解。
    """
    f3 = _item(review_mech, "mfe_mae")
    od = _item(review_mech, "open_direction")
    tv = _item(review_mech, "tier_vs_outcome")
    if not f3 and not od:
        return _missing("日复盘缺 mfe_mae / open_direction 两项")
    return {
        "available": bool(f3.get("available")) or bool(od.get("available")),
        "source": "basket_review.mfe_mae+open_direction+tier_vs_outcome",
        "unavailable_reason": (None if (f3.get("available") or od.get("available"))
                               else (f3.get("unavailable_reason") or od.get("unavailable_reason"))),
        "mfe_median": f3.get("mfe_median"),
        "mae_median": f3.get("mae_median"),
        "mfe_source": f3.get("mfe_source"),
        "capture_status": f3.get("capture_status"),
        "gap_median": od.get("gap_median"),
        "gap_dir": od.get("gap_dir"),
        "intraday_median": od.get("intraday_median"),
        "intraday_dir": od.get("intraday_dir"),
        "aligned": od.get("aligned"),
        "basket_ret_median": tv.get("basket_ret_median"),
    }


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 入场区间触发情况(**本块新写**:卡上冻结的建仓观察区间 × D1 最高/最低价)
# ══════════════════════════════════════════════════════════════════════════

def _entry_zone_of(member: Optional[Mapping[str, Any]]) -> Optional[Tuple[float, float]]:
    """成员卡上冻结的建仓观察区间 `(low, high)`;没有 / 被夹逼丢弃 → `None`。

    ⚠ 取的是 `members[].entry_zone`(⑦ 已过夹逼闸的那份),**⛔ 不重算、不放宽** ——
    区间是 D0 冻结件,复盘拿今天的价去重推一个区间就等于事后改考题。
    """
    if not isinstance(member, Mapping):
        return None
    zone = member.get("entry_zone")
    if not isinstance(zone, Mapping):
        return None
    lo, hi = _num(zone.get("low")), _num(zone.get("high"))
    if lo is None or hi is None:
        return None
    return (lo, hi) if lo <= hi else (hi, lo)


def judge_entry_zone_triggered(
    codes: Sequence[str],
    card: Optional[Mapping[str, Any]],
    bars: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """⑤ 入场区间触发情况:D1 的 `[low, high]` 与卡上冻结区间**有没有交集**。

    判据(**定义,不是阈值**):`bar.low ≤ zone.high 且 bar.high ≥ zone.low`
    —— 「当天的价格走进过这个区间」这句话的字面翻译,不含任何可调参数。
    ⚠ 它只答「**有没有机会**」,⛔ 不答「买了会怎样」:成交与判分永远只有
    `eval/exit_sim.py` 一份(⑨-D 唯一源,本模块一行成交逻辑都不写)。
    """
    members = {str(m.get("ts_code")): m
               for m in ((card or {}).get("members") or [])
               if isinstance(m, Mapping) and m.get("ts_code")}
    per: Dict[str, Any] = {}
    with_zone = triggered = with_bar = 0
    for code in codes:
        zone = _entry_zone_of(members.get(code))
        bar = bars.get(code) or {}
        lo, hi = _num(bar.get("low")), _num(bar.get("high"))
        row: Dict[str, Any] = {
            "zone": ({"low": zone[0], "high": zone[1]} if zone else None),
            "d1_low": lo, "d1_high": hi, "triggered": None, "reason": None,
        }
        if zone is None:
            row["reason"] = "no_entry_zone"
        elif lo is None or hi is None:
            with_zone += 1
            row["reason"] = "no_d1_bar"
        else:
            with_zone += 1
            with_bar += 1
            hit = lo <= zone[1] and hi >= zone[0]
            row["triggered"] = hit
            row["reason"] = "in_zone" if hit else "zone_not_reached"
            triggered += 1 if hit else 0
        per[code] = row
    return {
        "available": with_bar > 0,
        "source": "card.members[].entry_zone × d1_daily",
        "unavailable_reason": (
            None if with_bar else
            ("卡上没有任何成员带建仓观察区间" if with_zone == 0
             else "全体带区间的成员 D1 都没有行情")
        ),
        "member_count": len(codes),
        "members_with_zone": with_zone,
        "members_judged": with_bar,
        "triggered": triggered,
        "triggered_ratio": (triggered / with_bar) if with_bar else None,
        "any_triggered": (bool(triggered) if with_bar else None),
        "per_member": per,
    }


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 「落地起跳」信号(**本块新写**)
# ══════════════════════════════════════════════════════════════════════════

def judge_liftoff_signal(
    codes: Sequence[str],
    d1: Any,
    *,
    candidate_key: Optional[str] = None,
    d0: Any = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """⑥ 「落地起跳」信号 —— **只出 D1 的原始读数,⛔ 不下结论**。

    🔴 **这一项与 plan ④-A 的字面有一处出入,如实登记**:plan 写「`liftoff_signal`
    读 D1 的 `landing_state`」,而**裁定 #11 之后系统里已经没有 `landing_state`
    这个东西** —— 位置关退出机械闸,`landing_state_daily` 被重构成
    `landing_metrics_daily`(**没有 `state` 列**,只有十四项事实读数),判定改由 LLM
    在 D0 给出并落 `gate_evaluations.position` 的 `verdict`。故本项落成两半:

        · `d1_metrics`  —— D1 当天的原始读数(事实,逐票)
        · `d0_verdict`  —— D0 那一刻 LLM 位置关给的判定(`ok`/`weak`/`unfit`)与理由

    ⛔ **不在这里补一条「D1 算不算起跳」的及格线** —— 那正是裁定 #11 推翻掉的东西
    (CLAUDE.md「定性需求不许自行定量」;要那条线得先由用户拍板)。周度侧因此能出
    「**D0 位置关判定 × D1 后续表现**」的分层对照,这也正是 §七 **P3-49** 前向证伪
    义务唯一认的那份证据。
    """
    from neckline.scan.landing_store import load_landing_metric

    day1 = d1 if isinstance(d1, date) else datetime.strptime(str(d1), "%Y%m%d").date()
    metrics: Dict[str, Any] = {}
    got = 0
    for code in codes:
        try:
            row = load_landing_metric(day1, code, db_path=db_path)
        except Exception:  # noqa: BLE001 —— 可选情报保险丝
            logger.warning("[selection_clock] D1 落地读数读取失败(%s)", code, exc_info=True)
            row = None
        if row is None:
            metrics[code] = {"available": False,
                             "unavailable_reason": "D1 landing_metrics_daily 缺该票行"}
            continue
        got += 1
        metrics[code] = {"available": True, "metrics": row.get("metrics"),
                         "metrics_missing": row.get("metrics_missing")}

    verdicts: Dict[str, Any] = {}
    verdict_note: Optional[str] = None
    if candidate_key and d0 is not None:
        try:
            from neckline.selection.gates import GATE_POSITION, load_gate_evaluations

            for row in load_gate_evaluations(_d(d0), candidate_key=candidate_key,
                                             db_path=db_path):
                if row.get("gate") != GATE_POSITION or not row.get("ts_code"):
                    continue
                ev = row.get("evidence") or {}
                verdicts[str(row["ts_code"])] = {
                    "verdict": row.get("verdict"),
                    "position_verdict": ev.get("position_verdict"),
                    "reason": ev.get("position_reason") or ev.get("reason"),
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("[selection_clock] D0 位置关判定读取失败", exc_info=True)
            verdict_note = f"D0 位置关判定读取失败:{type(exc).__name__}"

    return {
        "available": got > 0,
        "source": "landing_metrics_daily(d1) + gate_evaluations.position(d0)",
        "unavailable_reason": (None if got else
                               "D1 全市场落地读数缺该篮成员行(批算没跑 / 当日停牌)"),
        "note": ("裁定 #11 之后机械层不产「起跳态」,本项只出读数与 D0 的模型判定;"
                 "⛔ 不得据此宣称任何买入期望(§3.8-(b),前向证伪义务见 §七 P3-49)"),
        "members_with_metrics": got,
        "member_count": len(codes),
        "d1_metrics": metrics,
        "d0_verdict": verdicts or None,
        "d0_verdict_note": verdict_note,
    }


# ══════════════════════════════════════════════════════════════════════════
# ⑧ 未触发原因(由 ⑤⑥ 派生)/ ⑨ 分层准确性
# ══════════════════════════════════════════════════════════════════════════

def derive_untriggered_reason(entry_item: Mapping[str, Any]) -> Optional[str]:
    """⑧ 未触发原因(**触发了就是 `None`**,⛔ 不拿空串冒充)。

    只有四个码 + 一个 `unknown`,全部由 ⑤ 的可观测事实派生 —— ⛔ 不揣测"为什么没
    走到"(那需要意图,机器没有)。
    """
    if entry_item.get("any_triggered") is True:
        return UNTRIGGERED_NONE
    if entry_item.get("any_triggered") is False:
        return UNTRIGGERED_ZONE_NOT_REACHED
    if not entry_item.get("members_with_zone"):
        return UNTRIGGERED_NO_ENTRY_ZONE
    if not entry_item.get("members_judged"):
        return UNTRIGGERED_NO_D1_BAR
    return UNTRIGGERED_UNKNOWN


def derive_tier_accuracy(driver_item: Mapping[str, Any]) -> Optional[str]:
    """⑨ T1/T2 分层准确性 —— 取 ⑧ 篮子验证的 **D1 定论四态**,⛔ 不压成 0/1。

    「多少算对」是一条**定量的线**,K8 §十七 只给了定性描述(「持续有效 / 样本不足 /
    辅助有效 / 持续失效」),故本列**保留四态原样**,把"折成正确率"这一步留在周度侧
    用 `verification_rules.STATE_SCORES`(那份换算是既有登记项,不是本块新造的数)。
    """
    if not driver_item:
        return TIER_ACCURACY_UNKNOWN
    if driver_item.get("not_evaluated"):
        return TIER_ACCURACY_NOT_EVALUATED
    state = driver_item.get("eod_state") or driver_item.get("state")
    if not state:
        return TIER_ACCURACY_UNKNOWN
    return str(state)


# ══════════════════════════════════════════════════════════════════════════
# 一篮的结案件
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ClockClosure:
    """一个篮子的选股时钟结案件(**纯值对象**,落库由 `save_closures` 负责)。"""

    basket_id: int
    basket_key: str
    name: str
    d0: str
    d1: str
    covered_tier: int
    mech: Dict[str, Any]
    engine_breakdown: Dict[str, Any]
    regime_at_d0: Optional[str] = None
    tier_accuracy: Optional[str] = None
    untriggered_reason: Optional[str] = None
    skeleton_version: str = UNSET_VERSION
    verification_ruleset_version: str = UNSET_VERSION


def build_closure(
    ref: Any,
    card: Optional[Mapping[str, Any]],
    review_mech: Optional[Mapping[str, Any]],
    *,
    d1: Any,
    bars: Optional[Mapping[str, Mapping[str, Any]]] = None,
    db_path: Optional[Path] = None,
) -> ClockClosure:
    """装配一篮的结案件。**每一项各自包保险丝**:某一项算炸了只让那一项落 `error`,
    其余八项照出(§铁律:任何一段异常都不许让当日整批结不了案)。

    `ref` 是 duck-typed 的 `selection.basket_store.BasketRef`(只用到
    `basket_id/basket_key/name/tier/trade_date/member_codes/engine_*`)——
    **刻意不 import 它的类型**,免得给 `review` 包再添一条无谓的依赖边。
    """
    codes = list(getattr(ref, "member_codes", ()) or ())
    d0 = _d(getattr(ref, "trade_date", "") or "")
    fp = (card or {}).get("fingerprint") if isinstance(card, Mapping) else None
    fp = fp if isinstance(fp, Mapping) else {}

    skeleton = (getattr(ref, "skeleton_version", None)
                or fp.get("skeleton_version")
                # 老篮子(K8 之前)没有骨架版本概念 —— 退回它当时的单包版本,
                # 让历史样本**留在自己那一层**(⛔ 不许并进 K8 新层,承 V2.1-②
                # 「历史样本不许消失」同一条纪律)。
                or fp.get("pack_version")
                or UNSET_VERSION)
    ruleset = fp.get("verification_ruleset_version") or UNSET_VERSION

    mech: Dict[str, Any] = {
        "spec_version": CLOCK_MECH_SPEC_VERSION,
        "meta": {
            "basket_id": getattr(ref, "basket_id", None),
            "basket_key": getattr(ref, "basket_key", None),
            "name": getattr(ref, "name", None),
            "tier": getattr(ref, "tier", None),
            "d0": d0, "d1": _d(d1),
            "member_count": len(codes), "members": codes,
            "has_card": bool(card),
            "has_review": bool(review_mech),
            "skeleton_version": skeleton,
            "engine_code": getattr(ref, "engine_code", None),
            "engine_version": getattr(ref, "engine_version", None),
            "verification_ruleset_version": ruleset,
            "pack_version": fp.get("pack_version"),
            # ⛔ 零新增 LLM 调用:结案叙述并进 ⑨ 复盘那一次(plan 附录第 6 行),
            # 落在 `basket_review_daily.llm_text`,这里只留一个指针。
            "narrative_ref": {
                "table": "basket_review_daily",
                "key": {"basket_id": getattr(ref, "basket_id", None), "review_date": _d(d1)},
                "note": "选股时钟不单独调用 LLM,结案叙述并进当日复盘那一次",
            },
        },
    }

    items = (
        ("regime_at_d0", lambda: judge_regime_at_d0(d0, db_path=db_path)),
        ("driver_persistence", lambda: judge_driver_persistence(review_mech)),
        ("sector_sync", lambda: judge_sector_sync(review_mech)),
        ("core_strength", lambda: judge_core_strength(review_mech)),
        ("entry_zone_triggered",
         lambda: judge_entry_zone_triggered(codes, card, bars or {})),
        ("liftoff_signal",
         lambda: judge_liftoff_signal(codes, d1,
                                      candidate_key=getattr(ref, "basket_key", None),
                                      d0=d0, db_path=db_path)),
        ("intraday_support_and_close",
         lambda: judge_intraday_support_and_close(review_mech)),
    )
    for key, fn in items:
        try:
            mech[key] = fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[selection_clock] 验证项 %s 失败(basket_id=%s),该项标 error",
                           key, getattr(ref, "basket_id", None), exc_info=True)
            mech[key] = _missing(f"该项计算失败:{type(exc).__name__}",
                                 error=f"{type(exc).__name__}: {exc}")

    untriggered = derive_untriggered_reason(mech.get("entry_zone_triggered") or {})
    tier_acc = derive_tier_accuracy(mech.get("driver_persistence") or {})
    mech["untriggered_reason"] = {
        "available": untriggered is not None or mech["entry_zone_triggered"].get("any_triggered") is True,
        "source": "derived(entry_zone_triggered)",
        "unavailable_reason": None,
        "code": untriggered,
        "triggered": mech["entry_zone_triggered"].get("any_triggered"),
    }
    mech["tier_accuracy"] = {
        "available": tier_acc not in (TIER_ACCURACY_UNKNOWN, TIER_ACCURACY_NOT_EVALUATED),
        "source": "derived(driver_persistence.eod_state)",
        "unavailable_reason": (None if tier_acc not in (TIER_ACCURACY_UNKNOWN,
                                                        TIER_ACCURACY_NOT_EVALUATED)
                               else "D1 那一拍没跑过篮子验证 / 定论读不出"),
        "tier": getattr(ref, "tier", None),
        "state": tier_acc,
        "note": "四态原样保留;折成「正确率」是周度侧的事,⛔ 本列不压成 0/1",
    }

    regime_item = mech.get("regime_at_d0") or {}
    return ClockClosure(
        basket_id=int(getattr(ref, "basket_id", 0) or 0),
        basket_key=str(getattr(ref, "basket_key", "") or ""),
        name=str(getattr(ref, "name", "") or ""),
        d0=d0, d1=_d(d1),
        covered_tier=int(getattr(ref, "tier", 0) or 0),
        mech=mech,
        engine_breakdown={
            # 裁定 #9(单篮子单引擎):两键即可。**列名保留不改** —— 将来开混引擎
            # 时它能原地扩成逐成员映射,零 schema 变更。
            "engine_code": getattr(ref, "engine_code", None),
            "engine_version": getattr(ref, "engine_version", None),
        },
        regime_at_d0=regime_item.get("regime"),
        tier_accuracy=tier_acc,
        untriggered_reason=untriggered,
        skeleton_version=str(skeleton),
        verification_ruleset_version=str(ruleset),
    )


# ══════════════════════════════════════════════════════════════════════════
# 落库(**只有 INSERT OR IGNORE 一条写路径**)
# ══════════════════════════════════════════════════════════════════════════

_INSERT_SQL = (
    "INSERT OR IGNORE INTO selection_clock "
    "(basket_id, d0_date, d1_date, covered_tier, regime_at_d0, mech_json, "
    " engine_breakdown_json, tier_accuracy, untriggered_reason, closed_at, "
    " skeleton_version, verification_ruleset_version) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


def save_closures(closures: Sequence[ClockClosure], *,
                  db_path: Optional[Path] = None) -> Dict[str, int]:
    """结案落库。返回 `{"inserted": n, "existing": m}`。

    🔴 `INSERT OR IGNORE` + `basket_id` UNIQUE = **结了就是结了**:同篮二次调用零新行、
    既有内容逐位不变(单测锁死)。⛔ 本模块**不提供**任何改写既有结案行的路径。
    """
    from neckline.db import connection, init_schema

    stats = {"inserted": 0, "existing": 0}
    if not closures:
        return stats
    now = _now()
    init_schema(db_path)
    with connection(db_path) as conn:
        for c in closures:
            cur = conn.execute(_INSERT_SQL, (
                int(c.basket_id), c.d0, c.d1, int(c.covered_tier), c.regime_at_d0,
                json.dumps(c.mech, ensure_ascii=False, sort_keys=True),
                json.dumps(c.engine_breakdown, ensure_ascii=False, sort_keys=True),
                c.tier_accuracy, c.untriggered_reason, now,
                c.skeleton_version, c.verification_ruleset_version,
            ))
            if cur.rowcount:
                stats["inserted"] += 1
            else:
                stats["existing"] += 1
    return stats


_SELECT_COLUMNS = (
    "basket_id, d0_date, d1_date, covered_tier, regime_at_d0, mech_json, "
    "engine_breakdown_json, tier_accuracy, untriggered_reason, closed_at, "
    "skeleton_version, verification_ruleset_version"
)


def _row_to_dict(row: Sequence[Any]) -> Dict[str, Any]:
    def _loads(blob: Any) -> Dict[str, Any]:
        try:
            v = json.loads(blob) if blob else {}
        except (json.JSONDecodeError, TypeError):
            return {}
        return v if isinstance(v, dict) else {}

    return {
        "basket_id": int(row[0]), "d0_date": str(row[1]), "d1_date": str(row[2]),
        "covered_tier": int(row[3]), "regime_at_d0": row[4],
        "mech": _loads(row[5]), "engine_breakdown": _loads(row[6]),
        "tier_accuracy": row[7], "untriggered_reason": row[8], "closed_at": str(row[9]),
        "skeleton_version": str(row[10]), "verification_ruleset_version": str(row[11]),
    }


def load_closure(basket_id: int, *, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读一篮的结案件。`None` = **还没结案**(合法态,⛔ 别读成"这篮不存在")。"""
    from neckline.db import connection, init_schema

    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM selection_clock WHERE basket_id=?",
            (int(basket_id),),
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def list_closures(
    date_from: Any = None, date_to: Any = None, *,
    tiers: Optional[Sequence[int]] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """按 **D0** 区间读已结案样本(排序 `(d0_date, basket_id)`,确定性可重放)。

    区间两端都可缺省(缺省 = 不设该端);⛔ 不做任何"最近 N 天"的隐式默认 ——
    窗口是调用方的语义,库读侧不替它决定。
    """
    from neckline.db import connection, init_schema

    sql = f"SELECT {_SELECT_COLUMNS} FROM selection_clock"
    where: List[str] = []
    args: List[Any] = []
    if date_from is not None:
        where.append("d0_date >= ?")
        args.append(_d(date_from))
    if date_to is not None:
        where.append("d0_date <= ?")
        args.append(_d(date_to))
    if tiers:
        where.append("covered_tier IN (" + ",".join("?" * len(tiers)) + ")")
        args.extend(int(t) for t in tiers)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY d0_date, basket_id"
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [_row_to_dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
# 编排:D1 收盘统一验证一次(覆盖 D0 全部 T1/T2)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ClockRunResult:
    d1: str
    d0: Optional[str] = None
    baskets: int = 0
    closures: List[ClockClosure] = field(default_factory=list)
    inserted: int = 0
    existing: int = 0
    notes: List[str] = field(default_factory=list)


def _load_review_mechs(basket_ids: Sequence[int], d1: str, *,
                       db_path: Optional[Path] = None) -> Dict[int, Dict[str, Any]]:
    """从 `basket_review_daily` 读当日九项机械判(⑨ 的产物)。读不到 → 缺键。"""
    from neckline.db import connection, init_schema

    if not basket_ids:
        return {}
    init_schema(db_path)
    out: Dict[int, Dict[str, Any]] = {}
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT basket_id, mech_json FROM basket_review_daily "
            "WHERE review_date=? AND basket_id IN (%s)" % ",".join("?" * len(basket_ids)),
            (d1, *[int(b) for b in basket_ids]),
        ).fetchall()
    for bid, blob in rows:
        try:
            v = json.loads(blob) if blob else None
        except (json.JSONDecodeError, TypeError):
            v = None
        if isinstance(v, dict):
            out[int(bid)] = v
    return out


def _load_d1_bars(d1: date, codes: Sequence[str],
                  parquet_dir: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    """D1 当日 `daily` 行(只为 ⑤ 取 high/low)。读不到 → 空 dict,⑤ 自会标不可得。"""
    if not codes:
        return {}
    try:
        import polars as pl

        from neckline.data.market_data import get_market_slice

        df = get_market_slice(d1, table="daily", parquet_dir=parquet_dir)
        if df.is_empty():
            return {}
        df = df.filter(pl.col("ts_code").is_in(list(codes)))
        return {r["ts_code"]: dict(r) for r in df.iter_rows(named=True)}
    except Exception:  # noqa: BLE001
        logger.warning("[selection_clock] D1 daily 切片读取失败,⑤ 入场区间项将标不可得",
                       exc_info=True)
        return {}


def close_day(
    d1: Any,
    *,
    d0: Any = None,
    refs: Optional[Sequence[Any]] = None,
    cards: Optional[Mapping[int, Any]] = None,
    review_mechs: Optional[Mapping[int, Mapping[str, Any]]] = None,
    bars: Optional[Mapping[str, Mapping[str, Any]]] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    persist: bool = True,
) -> ClockRunResult:
    """D1 收盘后**一次**给 D0 全部 T1/T2 结案。**永不抛异常**:任何一段炸了只记 note。

    调用方两处,给的东西不同、走的是同一条路:
      · `review/basket_review.py::review_day` —— 已经算好 refs/cards/mech/bars,直接注入
        (**不重读一遍 parquet**);
      · 独立回放 / CLI —— 什么都不给,本函数自己按 `d0` 从库里凑齐。
    """
    # ⚠ 日期解析也要在 try 里:`close_day` 的契约是**永不抛异常**,一个畸形入参就
    # 把晚间链掀翻,那正是这条契约要防的事(⑨ `review_day` 同款姿势)。
    res = ClockRunResult(d1=str(d1))
    try:
        day1 = d1 if isinstance(d1, date) else datetime.strptime(str(d1), "%Y%m%d").date()
        res.d1 = _d(day1)

        from neckline.calendar import prev_trading_day
        from neckline.selection.basket_store import load_basket_card, load_baskets_for_date

        day0 = d0 if d0 is not None else prev_trading_day(day1)
        res.d0 = _d(day0)
        if refs is None:
            refs = load_baskets_for_date(res.d0, tiers=COVERED_TIERS, db_path=db_path)
        else:
            # 注入路径同样只收 T1/T2 —— 覆盖域是 K8 §十四 定死的,⛔ 不随调用方漂。
            refs = [r for r in refs if int(getattr(r, "tier", 0) or 0) in COVERED_TIERS]
        res.baskets = len(refs)
        if not refs:
            res.notes.append(f"D0={res.d0} 当天没有 T1/T2 篮子,今日无可结案对象")
            return res
        if cards is None:
            cards = {r.basket_id: (load_basket_card(r.basket_id, db_path=db_path) or {}).get("card")
                     for r in refs}
        if review_mechs is None:
            review_mechs = _load_review_mechs([r.basket_id for r in refs], res.d1,
                                              db_path=db_path)
        if bars is None:
            all_codes = sorted({c for r in refs for c in (r.member_codes or ())})
            bars = _load_d1_bars(day1, all_codes, parquet_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[selection_clock] 结案素材装配失败", exc_info=True)
        res.notes.append(f"结案素材装配失败:{type(exc).__name__}: {exc}")
        return res

    for ref in refs:
        try:
            res.closures.append(build_closure(
                ref, (cards or {}).get(ref.basket_id),
                (review_mechs or {}).get(ref.basket_id),
                d1=res.d1, bars=bars, db_path=db_path,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[selection_clock] 结案件装配失败(basket_id=%s)",
                           getattr(ref, "basket_id", None), exc_info=True)
            res.notes.append(f"basket_id={getattr(ref, 'basket_id', None)} 结案件装配失败:"
                             f"{type(exc).__name__}")

    if persist and res.closures:
        try:
            stats = save_closures(res.closures, db_path=db_path)
            res.inserted, res.existing = stats["inserted"], stats["existing"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[selection_clock] 结案落库失败", exc_info=True)
            res.notes.append(f"结案落库失败:{type(exc).__name__}: {exc}")
    return res


__all__ = [
    "TABLE", "CLOCK_MECH_SPEC_VERSION", "MECH_ITEM_KEYS", "COVERED_TIERS", "UNSET_VERSION",
    "UNTRIGGERED_NO_ENTRY_ZONE", "UNTRIGGERED_NO_D1_BAR", "UNTRIGGERED_ZONE_NOT_REACHED",
    "UNTRIGGERED_UNKNOWN",
    "TIER_ACCURACY_NOT_EVALUATED", "TIER_ACCURACY_UNKNOWN",
    "judge_regime_at_d0", "judge_driver_persistence", "judge_sector_sync",
    "judge_core_strength", "judge_entry_zone_triggered", "judge_liftoff_signal",
    "judge_intraday_support_and_close",
    "derive_untriggered_reason", "derive_tier_accuracy",
    "ClockClosure", "build_closure", "save_closures", "load_closure", "list_closures",
    "ClockRunResult", "close_day",
]
