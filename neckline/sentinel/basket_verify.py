"""篮子验证状态机(plan §五 V2-⑧-C / ⑧-C2,2026-08-02)。**D+1 那一天**回答一个
问题:D0 冻结的那份「共同驱动」假设,今天被**验证**了、只对了**一半**、**说不清**,
还是被**证伪**了。

**判据源唯一 = ⑦ 冻结在 `card_json` 里的 `verification_spec` / `invalidation_spec`**
(条件集与四态映射的权威在 `selection/verification_rules.py`,⑦-b 裁定)。
**⛔ 本模块不写任何阈值、不定任何门槛**,只做三件事:取观测 → 代进冻结的 spec →
把状态落 `basket_verification`。

**⑧-C2 七条(planner 裁定,逐条落在本模块 + `basket_verify_store`)**

    1. **同一份 spec 不拆子集,拆的是「代入什么价」**:盘中把**现价**代进 spec 里
       「收盘」那个位置,EOD 代入**真实收盘价**。⛔ 不许为盘中另写一套更松 / 更紧的
       条件 —— 两条路径调的是同一个 `evaluate_specs()`。
    2. **锚一律取 D0 冻结值,盘中不重算**:MA20 / 跌停价 / 章程止损线全部读卡里那份
       (盘中根本算不出「今日 MA20」;`stop_pct` 已在卡的指纹里,**不读"当前现役
       config"** —— 否则同一张卡在章程切换前后会讲两套话)。
    3. **`触及跌停` 是盘中语义**:当日 `low ≤ 跌停价` 即算,不要求收在跌停;EOD 用
       当日最低价同判,两者一致。
    4. **状态未变不落行**,变了才追加;**EOD 那一拍无论变没变必落一行**(当日定论)。
    5. **「当前状态」三路读法**(有 EOD 取 EOD / 只有 intraday 标「盘中暂态」/ 都没有
       标 `not_evaluated`)—— 落在 `basket_verify_store.current_state()`。
    6. **`falsified` 是当日终态不撤回**(承 v1.3「D5 判向定格一次」),`verified`
       **不是**终态、可以翻。
    7. **无卡不判**:如实落 `unclear` + `no_card`,⛔ 不许拿默认条件顶上。

⚠ **口径局限(⑧ 如实登记;⑧-E 已治,2026-08-02 planner 裁定)**:卡里的 D0 锚是
**前复权**口径(⑤ 的 `MechContext` 面板,D0 当天 `qfq == raw`),观测侧是**原始价**
—— 盘中免费源只给原始价(无从复权),EOD `daily.close` 同样是原始价,两条路径
**互相一致**(⑧-C2 第 1 条要的就是这个)。代价:某成员**恰在 D+1 除权除息**时,
锚与观测差一个复权因子 → 可能误判破位。

**⑧-E 除权除息锚失效检测器**(零新数据源,盘中 / EOD 同一套判据)专治这个缺口:
`pre_close ≠ 卡里冻结的 ref_close`(带 `vr.EPS` 容差)即判**锚已失效**——A 股除权
除息日交易所公布的前收盘价就是除权除息参考价,这是直接、精确的信号。命中后**复用
本模块已有的「缺数据两侧都不计」机制**(不发明新机制):该成员验证侧与失效侧都不
计命中,`evidence_json` 标 `FLAG_ANCHOR_MISMATCH`。**⛔ 绝不做自动 rescale**——
`pre_close` 对不上有两种成因(真除权 / 数据错),盘中分不开,自动改价会把该报警的
故障变成静默的错误判定,宁可当天不判也不要判错。EOD 多一份交叉确认把两种成因分开:
`adj_factor(D+1) vs adj_factor(D0)` 变了 → 真除权(`REASON_MEMBER_EX_RIGHTS`,正常
降级不报警),没变 → 真故障(`REASON_ANCHOR_MISMATCH`,打 WARNING 该被看见)。
⛔ 别在这里"顺手复权",那会让盘中与 EOD 两条路径用上不同的价,违反 ⑧-C2 第 1 条。

⚠ **两条语义红线(接线时最容易越界的地方)**

    · `verified` 证明的是「没走坏 + 共振存在」,**不是「驱动兑现」更不是「可以追」**;
    · 失效侧的止损线只判「驱动是否被证伪」,**⛔ 不接任何持仓动作、不进推送**
      —— 哨兵对真实持仓执行的 −5% 止损纪律是另一回事(同数字同单一源,两个问题)。
      本模块因此**不 import 任何推送通道**,也不产生任何 `sentinel_events` 推送行。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from neckline.calendar import prev_trading_day
from neckline.selection import verification_rules as vr
from neckline.selection.basket_store import BasketRef, load_baskets_for_date, load_basket_card
from neckline.sentinel import basket_verify_store as store

logger = logging.getLogger(__name__)

SOURCE_INTRADAY = store.SOURCE_INTRADAY
SOURCE_EOD = store.SOURCE_EOD

# 「没有卡」与「有卡但判不出来」是两回事(§3.8「没有」与「没看」必须分得开)。
REASON_NO_CARD = "no_card"
REASON_NO_SPEC = "no_spec"                    # 有卡但卡里没有结构化 spec(降级卡不该出现)
FLAG_MEMBER_DATA_MISSING = "member_data_missing"   # 该成员当日无行情(停牌 / 数据缺口)
FLAG_SPEC_LEVELS_MISSING = "spec_levels_missing"   # 卡里**两侧**的阈值全是 null(D0 全算不出)
# 判定线审计 🟡-1(2026-08-03):**部分**阈值缺失 —— 该成员至少有一条条件判得了、也至少
# 有一条判不了。以前这种情形零披露(`FLAG_SPEC_LEVELS_MISSING` 只在两侧全不可判时才打),
# 于是「两条 AND 被降格成一条」在证据里完全看不出来。它与 `spec_levels_missing` 是两件事:
# 一个是「这张卡这只票压根没锚」,一个是「锚缺了一半,本次这一侧因此不下结论」。
FLAG_SPEC_LEVELS_PARTIAL = "spec_levels_partial"

# ⑧-E(2026-08-02 planner 裁定):除权除息锚失效检测器的原因码。检测命中(盘中 / EOD
# 通用,由 `evaluate_specs` 打)一律先标 `FLAG_ANCHOR_MISMATCH`;EOD 独有的
# `adj_factor` 交叉确认再把它精确拆成「真除权」/「真故障」两种,写进 evidence 里每个
# 成员行的 `confirm` 键(盘中没有交叉确认能力,不写 `confirm`,不是漏标)。
FLAG_ANCHOR_MISMATCH = "pre_close_anchor_mismatch"
REASON_MEMBER_EX_RIGHTS = "member_ex_rights"        # adj_factor 变了 → 真除权,正常降级不报警
REASON_ANCHOR_MISMATCH = "anchor_mismatch"          # adj_factor 未变 → 真故障,需要 WARNING
REASON_ANCHOR_UNCONFIRMED = "anchor_unconfirmed"    # 两天任一缺 adj_factor 行 → confirm 不了,不猜


@dataclass(frozen=True)
class MemberObservation:
    """一只成员在**这一拍**的观测。`price` 就是代进 spec 里「收盘」那个位置的数
    (盘中 = 现价,EOD = 真实收盘价 —— ⑧-C2 第 1 条);`low` 供「触及跌停」判;
    `pre_close`(⑧-E,盘中 = `Quote.pre_close`,EOD = `daily.pre_close`)供除权除息
    锚失效检测——**不参与任何验证 / 失效条件判定本身**,只用来跟卡里的 `ref_close`
    做锚有效性校验。缺省 `None` = 没有这个数据(老调用点 / 测试替身不传时安全降级为
    "不做锚检测",不是"锚一定有效")。"""

    ts_code: str
    price: Optional[float] = None
    low: Optional[float] = None
    pre_close: Optional[float] = None


@dataclass(frozen=True)
class BasketVerdict:
    """一个篮子在这一拍的判定结果(**纯值对象**,不碰 DB)。"""

    state: str
    verify_hits: int = 0
    invalidate_hits: int = 0
    min_members_hit: int = 1
    member_count: int = 0
    observed_members: int = 0
    reason: Optional[str] = None            # `no_card` / `no_spec`;正常判定时 None
    evidence: Dict[str, Any] = field(default_factory=dict)


def _compare_map(spec: Mapping[str, Any], codes: Sequence[str]) -> Dict[str, Optional[str]]:
    """条件码 → 比较语义串。**先读卡里 `conditions[].compare`**(卡是冻结件,老卡写
    的是哪种比较就按哪种判),卡里没写才退回引擎当前的声明;两者都没有 → `None`,
    该条如实"判不了"(⛔ 不猜)。"""
    declared = {
        c.get("code"): c.get("compare")
        for c in (spec.get("conditions") or []) if isinstance(c, Mapping)
    }
    return {code: declared.get(code) or vr.compare_of(code) for code in codes}


def _member_rows(spec: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    out: Dict[str, Mapping[str, Any]] = {}
    for row in spec.get("members") or []:
        if isinstance(row, Mapping) and row.get("ts_code"):
            out[str(row["ts_code"])] = row
    return out


def _num_or_none(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _member_ref_close(
    v_row: Optional[Mapping[str, Any]], i_row: Optional[Mapping[str, Any]]
) -> Optional[float]:
    """⑧-E:两份 spec 的成员行都带 `ref_close`(⑦ 冻的 D0 收盘,前复权口径;D0 当天
    `qfq == raw`)。验证侧优先、失效侧兜底 —— 两者本应同值,任一有就够;都没有 →
    `None`(锚本身缺失,判不了,交给下面 `anchor_mismatch` 如实返回"没检测到不一致")。"""
    for row in (v_row, i_row):
        if isinstance(row, Mapping):
            rc = _num_or_none(row.get("ref_close"))
            if rc is not None:
                return rc
    return None


def anchor_mismatch(ref_close: Optional[float], pre_close: Optional[float]) -> bool:
    """⑧-E 检测器(**全项目唯一一份**,盘中 / EOD / 盘前剧本核对共用):
    `pre_close ≠ 卡里 D0 收盘`(带 `vr.EPS` 容差)即判锚已失效。**任一缺失 → 判不了,
    返回 `False`**(⛔「没有」不是「不匹配」,是另一种「没看」——留给正常判定路径 /
    `FLAG_MEMBER_DATA_MISSING` 处理,不在这里冒充结论)。`<= 0` 视为坏数据兜底(真实
    价格恒正),同样不当成"匹配"或"不匹配",只是不触发检测(不把一个数据错误当成除权
    信号,也不当成正常)。

    ⚠ **公开(判定线审计 🟡-2,2026-08-03)**:⑬-7 的盘前篮子剧本核对
    (`sentinel/precall.py`)拿同样的冻结 D0 锚跟 D+1 竞价开盘价比,漏了同一道检测 ——
    ⑧-E 治好的错配在另一个消费方被原样重新引入。它现在 import 本函数,**不许再抄一份**
    (抄一份 = 两处阈值/容差各自漂移,⑧-E 那场事故的复发路径)。"""
    rc, pc = _num_or_none(ref_close), _num_or_none(pre_close)
    if rc is None or pc is None or rc <= 0 or pc <= 0:
        return False
    return abs(pc - rc) > vr.EPS


def _judge_side(
    row: Optional[Mapping[str, Any]],
    codes: Sequence[str],
    compares: Mapping[str, Optional[str]],
    obs: MemberObservation,
    *,
    require_all: bool,
) -> Tuple[Optional[bool], Dict[str, Optional[bool]]]:
    """判一侧(验证 = `require_all` 全部满足;失效 = 任一命中)。

    返回 `(命中?, 逐条结果)`。**`None` = 这一侧对该成员判不了**(阈值 null / 观测
    缺失 / 比较语义不认识),⛔ 绝不当成 `False` —— 「没有」与「没看」必须分得开。

    **合成一侧结论的读法住 `verification_rules.combine_side()`**(判定线审计 🟡-1,
    2026-08-03,= `verify_ruleset_v2` 的全部内容):本模块只负责「代入观测、逐条求值」,
    「判不了怎么算」与「什么算命中」同属条件集,归 ⑦-b 的单一源管 —— 本模块**不写
    任何阈值、不定任何门槛**这条纪律,对这套读法同样成立。

    修的是什么:原实现先把 `None` 那几条**扔掉**,再对**剩下的子集**取 `all()`/`any()`
    —— 于是「⑦-b-B 定死的两条 AND」在某成员 MA20 缺失时被静默降格成**单条 AND**,
    只要收盘 ≥ D0 收盘就计一个验证命中,`flags` 一片空白。而失效侧的复合条件
    (`close_below_ref_and_ma20`)本来就是「任一子阈值 null 整条不判」,于是**同一个
    数据缺口在两侧一松一紧,系统性偏向 `verified`**。`verified` 虽然不触发纪律,却是
    ⑨ 评价引擎算验证率的原料 —— 拿被半判成员污染的数据去校准条件集 = 错上加错。
    """
    per: Dict[str, Optional[bool]] = {}
    if row is None:
        return None, per
    for code in codes:
        per[code] = vr.evaluate_condition(
            compares.get(code) or "", row.get(code), price=obs.price, low=obs.low
        )
    return vr.combine_side(list(per.values()), require_all=require_all), per


def evaluate_specs(
    verification_spec: Optional[Mapping[str, Any]],
    invalidation_spec: Optional[Mapping[str, Any]],
    observations: Mapping[str, MemberObservation],
) -> BasketVerdict:
    """**盘中与 EOD 共用的唯一判定函数**(⑧-C2 第 1 条:同一份 spec,只是代入的价不同)。

    聚合门槛 `min_members_hit` **取卡里冻结的那个数**(不重算 —— 卡是冻结件,重算就
    等于让"今天的成员数"影响"D0 定下的门槛");卡里没有才退回 `verification_rules`
    的当前公式。**数据缺失两侧都不计命中**并在 evidence 如实标 `member_data_missing`
    (⑦-b:「查不到」≠ 失效)。
    """
    if not isinstance(verification_spec, Mapping) or not isinstance(invalidation_spec, Mapping):
        return BasketVerdict(
            state=vr.STATE_UNCLEAR, reason=REASON_NO_SPEC,
            evidence={"reason": REASON_NO_SPEC,
                      "note": "卡里没有结构化验证 / 失效 spec,本篮不判(不拿默认条件顶上)"},
        )

    v_rows, i_rows = _member_rows(verification_spec), _member_rows(invalidation_spec)
    require = [str(c) for c in (verification_spec.get("require") or [])]
    any_of = [str(c) for c in (invalidation_spec.get("any_of") or [])]
    v_cmp, i_cmp = _compare_map(verification_spec, require), _compare_map(invalidation_spec, any_of)

    codes = list(v_rows) + [c for c in i_rows if c not in v_rows]
    member_count = int(verification_spec.get("member_count") or len(codes) or 0)
    min_hit = verification_spec.get("min_members_hit")
    min_hit = int(min_hit) if isinstance(min_hit, int) else vr.min_members_hit(member_count)

    verify_hits = invalidate_hits = observed = 0
    missing: List[str] = []
    anchor_mismatched: List[str] = []
    levels_partial: List[str] = []
    detail: List[Dict[str, Any]] = []
    for code in codes:
        obs = observations.get(code)
        flags: List[str] = []
        if obs is None or obs.price is None:
            missing.append(code)
            flags.append(FLAG_MEMBER_DATA_MISSING)
            detail.append({"ts_code": code, "price": None, "low": None,
                           "verify": None, "invalidate": None, "flags": flags})
            continue
        v_row, i_row = v_rows.get(code), i_rows.get(code)
        # ⑧-E:锚失效检测先于任何条件判定 —— pre_close 对不上卡里的 ref_close 时,
        # 拿观测价去跟(除权前尺度的)阈值比是错的比较,必须先排除,不许先判后知错。
        # ⚠ `pre_close`/`ref_close` 两个键**只在这条新分支里出现**,不许顺手加进下面
        # 「正常判定」的成员行——那会让「同一份数据两条路径判定逐位相同」这条既有
        # 不变量被"盘中 Quote 没有 pre_close 而 EOD daily 面板有"这个跟判定结果本身
        # 无关的差异打破(v1.5-⑧ 既有单测 `test_intraday_and_eod_paths_agree_bit_for_
        # bit` 施工期真踩过,回滚过一次)。
        ref_close = _member_ref_close(v_row, i_row)
        if anchor_mismatch(ref_close, obs.pre_close):
            anchor_mismatched.append(code)
            flags.append(FLAG_ANCHOR_MISMATCH)
            detail.append({
                "ts_code": code, "price": obs.price, "low": obs.low, "pre_close": obs.pre_close,
                "ref_close": ref_close, "verify": None, "invalidate": None, "flags": flags,
            })
            continue
        observed += 1
        v_hit, v_per = _judge_side(v_row, require, v_cmp, obs, require_all=True)
        i_hit, i_per = _judge_side(i_row, any_of, i_cmp, obs, require_all=False)
        # 🟡-1:两个 flag 的分界 = 「一条都判不了」vs「判得了一部分」。前者维持原语义
        # (两侧全部不可判);后者是新披露位,凡有阈值缺失就必须留下痕迹 —— 被半判的
        # 成员现在既不计验证命中、也不计失效命中,但**这件事本身要能被 ⑨ 看见**。
        all_conditions = list(v_per.values()) + list(i_per.values())
        if not any(r is not None for r in all_conditions):
            flags.append(FLAG_SPEC_LEVELS_MISSING)
        elif any(r is None for r in all_conditions):
            flags.append(FLAG_SPEC_LEVELS_PARTIAL)
            levels_partial.append(code)
        verify_hits += 1 if v_hit else 0
        invalidate_hits += 1 if i_hit else 0
        detail.append({
            "ts_code": code, "price": obs.price, "low": obs.low,
            "verify": v_hit, "invalidate": i_hit,
            "verify_conditions": v_per, "invalidate_conditions": i_per,
            "flags": flags,
        })

    state = vr.decide_state(verify_hits, invalidate_hits, min_hit)
    evidence: Dict[str, Any] = {
        "ruleset_version": verification_spec.get("ruleset_version"),
        # 🟡-1:卡上冻的版本(上一行)跟的是**这张卡的条件与阈值**;判定代码永远只有
        # 一份、跑的是**当下**这套读法。跨版本那几天(老卡 v1 × 新读法 v2)两者会不等,
        # 如实两个都记下来,⑨ 分层才不至于把「按 v2 读出来的成绩」记在 v1 头上而无从
        # 察觉。相等时也照记,不做"只在不同时才写"的花活(那会让缺键有两种含义)。
        "ruleset_version_engine": vr.VERIFICATION_RULESET_VERSION,
        "verify_spec_version": verification_spec.get("spec_version"),
        "invalidate_spec_version": invalidation_spec.get("spec_version"),
        "min_members_hit": min_hit,
        "member_count": member_count,
        "verify_hits": verify_hits,
        "invalidate_hits": invalidate_hits,
        "observed_members": observed,
        "members": detail,
    }
    if missing:
        # ⛔ 「查不到」不是「失效」:如实标出来,让 ⑨ / 报告能说「这一态里有几只没数据」。
        evidence[FLAG_MEMBER_DATA_MISSING] = missing
    if levels_partial:
        # 🟡-1:「锚缺了一半 → 该侧不下结论」的成员名单。与 member_data_missing 分开:
        # 那是"今天没看到这只票",这是"看到了但卡上的锚不全"。
        evidence[FLAG_SPEC_LEVELS_PARTIAL] = levels_partial
    if anchor_mismatched:
        # ⑧-E:「锚失效」不是「失效」也不是「查不到」——单独一个原因码,别混进上面
        # 那条,否则 ⑨ 分不清「数据缺口」与「除权除息误判」这两种截然不同的成因。
        evidence[FLAG_ANCHOR_MISMATCH] = anchor_mismatched
    return BasketVerdict(
        state=state, verify_hits=verify_hits, invalidate_hits=invalidate_hits,
        min_members_hit=min_hit, member_count=member_count, observed_members=observed,
        evidence=evidence,
    )


def evaluate_card(
    card: Optional[Mapping[str, Any]], observations: Mapping[str, MemberObservation]
) -> BasketVerdict:
    """从一张冻结卡(`load_basket_card()` 的 `card` 字段)取 spec 再判。

    **无卡 → `unclear` + `no_card`**(⑧-C2 第 7 条):没有 spec 就不判,⛔ 不许拿默认
    条件顶上 —— 那等于凭空给它编一份判据。
    """
    if not isinstance(card, Mapping):
        return BasketVerdict(
            state=vr.STATE_UNCLEAR, reason=REASON_NO_CARD,
            evidence={"reason": REASON_NO_CARD,
                      "note": "本篮尚无冻结卡(有篮子无卡是合法中间态),没有 spec 就不判"},
        )
    return evaluate_specs(card.get("verification_spec"), card.get("invalidation_spec"), observations)


# ══════════════════════════════════════════════════════════════════════════
# 两条运行期路径:盘中每拍(现价) / EOD 一拍(真实收盘价)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class VerificationRunResult:
    trade_date: date
    d0: Optional[date] = None
    source: str = SOURCE_INTRADAY
    evaluated: int = 0
    rows_written: int = 0
    skipped_unchanged: int = 0
    skipped_latched: int = 0
    skipped_not_observed: int = 0
    states: Dict[int, str] = field(default_factory=dict)      # basket_id -> 本拍状态


def _observations_from_quotes(codes: Sequence[str], quotes: Mapping[str, Any]) -> Dict[str, MemberObservation]:
    """盘中:`Quote` → 观测。**现价代进 spec 的「收盘」位**;`low` 用当日累计最低
    (源自带),供「触及跌停」判 —— 触及即算,不要求收在跌停(⑧-C2 第 3 条);
    `pre_close`(⑧-E)取 `Quote.pre_close`,供除权除息锚失效检测(测试替身 / 老调用
    点没有这个属性时 `getattr` 兜底 `None`,安全降级为"不做锚检测",不是"锚必有效")。"""
    out: Dict[str, MemberObservation] = {}
    for code in codes:
        q = quotes.get(code)
        if q is None:
            continue
        price = getattr(q, "price", None)
        low = getattr(q, "low", None)
        pre_close = getattr(q, "pre_close", None)
        out[code] = MemberObservation(
            ts_code=code,
            price=float(price) if isinstance(price, (int, float)) and price else None,
            low=float(low) if isinstance(low, (int, float)) and low else None,
            pre_close=float(pre_close) if isinstance(pre_close, (int, float)) and pre_close else None,
        )
    return out


def run_intraday_verification(
    trade_date: date,
    quotes: Mapping[str, Any],
    *,
    attempted_codes: Optional[Sequence[str]] = None,
    now: Optional[datetime] = None,
    db_path: Optional[Path] = None,
    baskets: Optional[Sequence[BasketRef]] = None,
) -> VerificationRunResult:
    """盘中一拍(由 `engine.run_tick` 在拉完价之后调,**不额外拉价**)。

    只判「**这一拍确实去拉过价**的篮子」:`attempted_codes` = 本拍关注池。某篮一个
    成员都不在关注池里(如 T3 篮不进盘中池)→ **一行都不落**,让「当前状态」读法落到
    `not_evaluated`(「还没判」与「判了是 unclear」必须分得开);成员在池里但没拉到
    行情 → 照判,落 `unclear` + `member_data_missing`(那是"看了没拿到",不是"没看")。
    """
    res = VerificationRunResult(trade_date=trade_date, source=SOURCE_INTRADAY)
    d0 = prev_trading_day(trade_date)
    res.d0 = d0
    refs = list(baskets) if baskets is not None else load_baskets_for_date(
        d0, tiers=(1, 2), db_path=db_path)
    if not refs:
        return res
    attempted = set(attempted_codes) if attempted_codes is not None else set(quotes)
    stamp = store.observed_at_now(now)
    for ref in refs:
        if not any(c in attempted for c in ref.member_codes):
            res.skipped_not_observed += 1
            continue
        card_row = load_basket_card(ref.basket_id, db_path=db_path)
        verdict = evaluate_card((card_row or {}).get("card"), _observations_from_quotes(
            ref.member_codes, quotes))
        res.evaluated += 1
        res.states[ref.basket_id] = verdict.state
        outcome = store.append_if_changed(
            ref.basket_id, trade_date, verdict, source=SOURCE_INTRADAY,
            observed_at=stamp, db_path=db_path,
        )
        if outcome == store.WROTE:
            res.rows_written += 1
        elif outcome == store.SKIPPED_LATCHED:
            res.skipped_latched += 1
        else:
            res.skipped_unchanged += 1
    return res


def _eod_observations(trade_date: date, codes: Sequence[str], parquet_dir: Optional[Path]) -> Dict[str, MemberObservation]:
    """EOD:当日 `daily` 面板 → 观测(**真实收盘价** + 当日最低价 + `pre_close`—— ⑧-E
    锚失效检测用)。当日无行 = 该成员今天没有行情(停牌 / 数据缺口)→ 不进 dict,调用
    方按 `member_data_missing` 处理。"""
    import polars as pl

    from neckline.data.market_data import get_market_slice

    if not codes:
        return {}
    df = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
    if df.is_empty():
        return {}
    df = df.filter(pl.col("ts_code").is_in(list(codes)))
    out: Dict[str, MemberObservation] = {}
    for row in df.iter_rows(named=True):
        close, low, pre_close = row.get("close"), row.get("low"), row.get("pre_close")
        out[row["ts_code"]] = MemberObservation(
            ts_code=row["ts_code"],
            price=float(close) if isinstance(close, (int, float)) else None,
            low=float(low) if isinstance(low, (int, float)) else None,
            pre_close=float(pre_close) if isinstance(pre_close, (int, float)) else None,
        )
    return out


def _eod_adj_factor_maps(
    d0: date, d1: date, codes: Sequence[str], parquet_dir: Optional[Path]
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """⑧-E EOD 交叉确认用:D0 与 D+1 两天的 `adj_factor`,**只查锚失效命中的那几只**
    (不是全市场,codes 由调用方限定为已被 `FLAG_ANCHOR_MISMATCH` 标出的成员)。某天
    某票没有行 → 对应 map 里没有那个 key(留给调用方按"查不到"处理,不许当 0 或当
    "没变"——那是编数据,不是交叉确认)。"""
    import polars as pl

    from neckline.data.market_data import get_market_slice

    def _one(day: date) -> Dict[str, float]:
        if not codes:
            return {}
        df = get_market_slice(day, table="adj_factor", parquet_dir=parquet_dir)
        if df.is_empty():
            return {}
        df = df.filter(pl.col("ts_code").is_in(list(codes)))
        out: Dict[str, float] = {}
        for row in df.iter_rows(named=True):
            v = row.get("adj_factor")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[row["ts_code"]] = float(v)
        return out

    return _one(d0), _one(d1)


def _confirm_anchor_mismatches(
    verdict: BasketVerdict, adj_d0: Mapping[str, float], adj_d1: Mapping[str, float]
) -> None:
    """⑧-E EOD 专属:对 `evaluate_specs()` 已经标出 `FLAG_ANCHOR_MISMATCH` 的成员,
    用 `adj_factor(D+1) vs adj_factor(D0)` 拆分成因(原地增补 `verdict.evidence`)。

    **不改 `state` / `verify_hits` / `invalidate_hits`**——两种成因在 `evaluate_specs`
    里已经都被排除出两侧命中计数(⛔ 绝不把「锚失效」算成失效命中,真故障也不例外:
    我们不知道它真实方向,不能因为"像是故障"就反过来算它一个失效命中);这里只决定
    每个成员的 `confirm` 原因码,以及要不要打 WARNING。"""
    codes = list(verdict.evidence.get(FLAG_ANCHOR_MISMATCH) or [])
    if not codes:
        return
    confirm_map: Dict[str, Dict[str, Any]] = {}
    for code in codes:
        d0v, d1v = adj_d0.get(code), adj_d1.get(code)
        if d0v is None or d1v is None:
            reason = REASON_ANCHOR_UNCONFIRMED
        elif abs(float(d1v) - float(d0v)) > vr.EPS:
            reason = REASON_MEMBER_EX_RIGHTS
        else:
            reason = REASON_ANCHOR_MISMATCH
            logger.warning(
                "[basket_verify] ⑧-E 锚失效交叉确认为真故障(非除权):ts_code=%s "
                "adj_factor(D0)=%s adj_factor(D+1)=%s —— pre_close 与卡里 D0 收盘不符,"
                "但复权因子未变,请核查行情源是否有误",
                code, d0v, d1v,
            )
        confirm_map[code] = {"confirm": reason, "adj_factor_d0": d0v, "adj_factor_d1": d1v}
    for row in verdict.evidence.get("members") or []:
        code = row.get("ts_code")
        if code in confirm_map:
            row.update(confirm_map[code])
    verdict.evidence["anchor_mismatch_confirm"] = confirm_map


def run_eod_verification(
    trade_date: date,
    *,
    now: Optional[datetime] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> VerificationRunResult:
    """收盘定论那一拍(D+1 盘后)。**全部 D0 篮子都判**(EOD 面板是全市场,无拉价成本,
    T3 也该有个定论),且 **无论状态变没变都落一行**(⑧-C2 第 4 条:它是当日的定论
    记录,不能因为"和盘中最后一拍一样"就省掉)。

    ⚠ 调用方:V2-⑭ 的 16:35 报告链(`report/pipeline.py`)会在 ⑨ 复盘之前调它;在 ⑭
    落地之前用 `scripts/basket_verify.py` 手动 / 定时驱动。本函数**只写
    `basket_verification`**,不推送、不碰持仓。
    """
    res = VerificationRunResult(trade_date=trade_date, source=SOURCE_EOD)
    d0 = prev_trading_day(trade_date)
    res.d0 = d0
    refs = load_baskets_for_date(d0, db_path=db_path)
    if not refs:
        return res
    all_codes = sorted({c for r in refs for c in r.member_codes})
    obs = _eod_observations(trade_date, all_codes, parquet_dir)
    stamp = store.observed_at_now(now)

    # 两遍:第一遍照常判(与盘中同一个 `evaluate_card`,⑧-E 检测器已在其中生效);
    # 第二遍只对**判出锚失效的那几只**做 `adj_factor` 交叉确认(⑧-E EOD 专属,盘中
    # 没有这个数据 / 也没这个必要)。不合并成一遍是为了不把"要不要查 adj_factor"
    # 这个 EOD 专属分支糊进盘中/EOD 共用的 `evaluate_specs`,保持两条路径**同一个
    # 检测器**这件事在代码结构上就是显然的,不必靠约定。
    verdicts: Dict[int, BasketVerdict] = {}
    mismatched_codes: Set[str] = set()
    for ref in refs:
        card_row = load_basket_card(ref.basket_id, db_path=db_path)
        verdict = evaluate_card((card_row or {}).get("card"),
                                {c: obs[c] for c in ref.member_codes if c in obs})
        verdicts[ref.basket_id] = verdict
        mismatched_codes.update(verdict.evidence.get(FLAG_ANCHOR_MISMATCH) or [])

    if mismatched_codes:
        adj_d0, adj_d1 = _eod_adj_factor_maps(d0, trade_date, sorted(mismatched_codes), parquet_dir)
        for verdict in verdicts.values():
            _confirm_anchor_mismatches(verdict, adj_d0, adj_d1)

    for ref in refs:
        verdict = verdicts[ref.basket_id]
        res.evaluated += 1
        res.states[ref.basket_id] = verdict.state
        outcome = store.append_row(
            ref.basket_id, trade_date, verdict, source=SOURCE_EOD,
            observed_at=stamp, db_path=db_path,
        )
        res.rows_written += 1
        if outcome == store.WROTE_LATCHED:
            res.skipped_latched += 1
            res.states[ref.basket_id] = vr.STATE_FALSIFIED
    return res


__all__ = [
    "MemberObservation", "BasketVerdict", "VerificationRunResult",
    "SOURCE_INTRADAY", "SOURCE_EOD",
    "REASON_NO_CARD", "REASON_NO_SPEC",
    "FLAG_MEMBER_DATA_MISSING", "FLAG_SPEC_LEVELS_MISSING", "FLAG_SPEC_LEVELS_PARTIAL",
    "FLAG_ANCHOR_MISMATCH", "REASON_MEMBER_EX_RIGHTS", "anchor_mismatch",
    "REASON_ANCHOR_MISMATCH", "REASON_ANCHOR_UNCONFIRMED",
    "evaluate_specs", "evaluate_card",
    "run_intraday_verification", "run_eod_verification",
]
