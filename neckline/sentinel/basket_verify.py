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

⚠ **已知口径局限(如实登记,⑧ 不擅自修)**:卡里的 D0 锚是**前复权**口径(⑤ 的
`MechContext` 面板),而观测侧是**原始价** —— 盘中免费源只给原始价(无从复权),EOD
`daily.close` 同样是原始价,两条路径**互相一致**(⑧-C2 第 1 条要的就是这个)。代价:
某成员**恰在 D+1 除权除息**时,锚与观测差一个复权因子 → 可能误判破位。一天的窗口里
这是小概率,但**不是零**;正解是让 ⑦ 在卡里额外冻一份原始价锚(⑦ 的形状变更,不在
⑧ 授权范围),已写进 ⑧ 完工记录待 planner 裁定。⛔ 别在这里"顺手复权",那会让盘中
与 EOD 两条路径用上不同的价,违反 ⑧-C2 第 1 条。

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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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
FLAG_SPEC_LEVELS_MISSING = "spec_levels_missing"   # 卡里这一侧的阈值全是 null(D0 就算不出)


@dataclass(frozen=True)
class MemberObservation:
    """一只成员在**这一拍**的观测。`price` 就是代进 spec 里「收盘」那个位置的数
    (盘中 = 现价,EOD = 真实收盘价 —— ⑧-C2 第 1 条);`low` 供「触及跌停」判。"""

    ts_code: str
    price: Optional[float] = None
    low: Optional[float] = None


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


def _judge_side(
    row: Optional[Mapping[str, Any]],
    codes: Sequence[str],
    compares: Mapping[str, Optional[str]],
    obs: MemberObservation,
    *,
    require_all: bool,
) -> Tuple[Optional[bool], Dict[str, Optional[bool]]]:
    """判一侧(验证 = `require_all`;失效 = 任一命中)。

    返回 `(命中?, 逐条结果)`。**`None` = 这一侧对该成员判不了**(阈值全 null /
    观测缺失 / 比较语义不认识),⛔ 绝不当成 `False` —— 「没有」与「没看」必须分得开。
    """
    per: Dict[str, Optional[bool]] = {}
    if row is None:
        return None, per
    judged: List[bool] = []
    for code in codes:
        r = vr.evaluate_condition(
            compares.get(code) or "", row.get(code), price=obs.price, low=obs.low
        )
        per[code] = r
        if r is not None:
            judged.append(r)
    if not judged:
        return None, per
    return (all(judged) if require_all else any(judged)), per


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
        observed += 1
        v_hit, v_per = _judge_side(v_rows.get(code), require, v_cmp, obs, require_all=True)
        i_hit, i_per = _judge_side(i_rows.get(code), any_of, i_cmp, obs, require_all=False)
        if v_hit is None and i_hit is None:
            flags.append(FLAG_SPEC_LEVELS_MISSING)
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
    (源自带),供「触及跌停」判 —— 触及即算,不要求收在跌停(⑧-C2 第 3 条)。"""
    out: Dict[str, MemberObservation] = {}
    for code in codes:
        q = quotes.get(code)
        if q is None:
            continue
        price = getattr(q, "price", None)
        low = getattr(q, "low", None)
        out[code] = MemberObservation(
            ts_code=code,
            price=float(price) if isinstance(price, (int, float)) and price else None,
            low=float(low) if isinstance(low, (int, float)) and low else None,
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
    """EOD:当日 `daily` 面板 → 观测(**真实收盘价** + 当日最低价)。当日无行 = 该成员
    今天没有行情(停牌 / 数据缺口)→ 不进 dict,调用方按 `member_data_missing` 处理。"""
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
        close, low = row.get("close"), row.get("low")
        out[row["ts_code"]] = MemberObservation(
            ts_code=row["ts_code"],
            price=float(close) if isinstance(close, (int, float)) else None,
            low=float(low) if isinstance(low, (int, float)) else None,
        )
    return out


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
    for ref in refs:
        card_row = load_basket_card(ref.basket_id, db_path=db_path)
        verdict = evaluate_card((card_row or {}).get("card"),
                                {c: obs[c] for c in ref.member_codes if c in obs})
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
    "FLAG_MEMBER_DATA_MISSING", "FLAG_SPEC_LEVELS_MISSING",
    "evaluate_specs", "evaluate_card",
    "run_intraday_verification", "run_eod_verification",
]
