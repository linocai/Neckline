"""K9 策略层的**唯一编排入口**(PROJECT_PLAN §5.4,架构 §3.2)。

    冻结事实包 + 参数包
        → K9 第一层 硬边界(9 条)
        → K9 第二层 四通道召回(每通道两档)
        → 档位选择(§5.4.7 第 2 步)
        → K9 第三层 排序(行业热度 / 形态内强度 / 跨日接力)
        → 第五节 名额分配(保底 + 自由竞争)
        → 署名清单 + 全市场 disposition

🔴 **本文件是唯一同时看见四个通道产物的地方**(架构 §二 边界②)。通道之间互不 import,
也拿不到彼此的结果 —— 合并只在这里发生。

🔴 **零 LLM、零联网、取数唯一来源是事实包**(§5.4.1):`k9/**` 不 import
`neckline.llm` / `neckline.search` / `httpx` / … ,也不 import `tushare_client` /
`market_data`(守门 G2/G3)。本文件读事实层(`facts.store` / `facts.industry`)——
那**就是**事实包。

🔴 **确定性**:同一份冻结事实包 + 同一份参数包 → 逐字节相同的清单(守门 G10)。
每一处排序都带明确的 tie-break,每一处百分位都是「并列取平均名次」。

⚠ **谁在池子里、谁的统计口径是全市场**(已登记 §14):
    · K9 第一层第 7 条的流动性分位在**全市场**上取(K9 §二 原文「全市场后 20%」);
    · 四通道的横截面排名(形态 4 的资金 / 涨幅 / 量比百分位)在**硬边界之后的池内**取
      —— K9 §二 开宗明义「以下情形当日直接排除,**不进入任何形态召回**」,
      而一次排名就是召回的一部分。把当日涨停的票留在分母里,等于让被排除的票继续
      影响谁被召回。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import polars as pl

from neckline.calendar import trading_days_between
from neckline.facts import industry as facts_industry
from neckline.facts import store as facts_store
from neckline.facts import universe as facts_universe
from neckline.k9 import activity as activity_mod
from neckline.k9 import boundary as boundary_mod
from neckline.k9 import industry_heat as heat_mod
from neckline.k9 import quota as quota_mod
from neckline.k9 import ranking as ranking_mod
from neckline.k9 import store as k9_store
from neckline.k9.channels import p1_breakout, p2_rebound, p3_riser, p4_moneyflow
from neckline.k9.contract import (
    DECLARED_FIELDS,
    PATTERN_ORDER,
    STRATEGY, STRATEGY_VERSION,
    ChannelHit,
    Entry,
    PackRange,
    Pattern,
    Shortlist,
    Tier,
)
from neckline.k9.params import K9Params, ParamsUnavailable

logger = logging.getLogger(__name__)

#: 四个通道。⚠ 顺序只影响日志与落库顺序,⛔ 不影响任何判定
#: (每个通道各判各的,合并后统一排序)。
CHANNELS = (p1_breakout, p2_rebound, p3_riser, p4_moneyflow)


class PackUnavailable(RuntimeError):
    """当日没有冻结的事实包 → 报告「今天没跑成」,⛔ 不是「今天没有」。"""


def required_lookback(params: K9Params) -> int:
    """跑完全链需要多少个交易日的事实包(**含当日**)。

    P3 每个历史热门日都要向前计算有效活跃度，因此历史长度是热门窗口与活跃度
    窗口的组合，而不是单键最大值。
    """
    ch = params.channels
    needs = [
        params.volume.ma_days,
        params.boundary.activity_amount_window_days,
        params.boundary.activity_participation_window_days,
    ]
    for tier in (ch.p1.strict, ch.p1.relaxed):
        needs.append(tier.breakout_window_days + 1)
        needs.append(tier.hot_identity_exclusion.lookback_days)
    for tier in (ch.p2.strict, ch.p2.relaxed):
        needs.append(tier.window_days)
    for tier in (ch.p3.strict, ch.p3.relaxed):
        needs.append(tier.hot_lookback_days)
        needs.append(tier.conflict_lookback_days + params.volume.ma_days)
    for tier in (ch.p4.strict, ch.p4.relaxed):
        needs.append(tier.cum_days)
    return max(needs)


def _window_start(as_of: date, sessions: int) -> date:
    """`as_of` 往回数 `sessions` 个交易日的那一天(含 `as_of`)。"""
    span = max(sessions * 2 + 20, 30)
    days = trading_days_between(as_of - timedelta(days=span), as_of)
    if not days:
        return as_of
    return days[-sessions] if len(days) >= sessions else days[0]


def build_pack_range(
    trade_date: date,
    *,
    params: K9Params,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Tuple[PackRange, facts_store.FactPack]:
    """读区间事实包 → `PackRange`(策略层唯一的数据入口)。

    ⚠ **列投影是必填的**(§12 坑 1 的内存红线):这里传的就是 `DECLARED_FIELDS`
    —— 声明依赖(契约一)与列投影是同一件事的两面。
    """
    pack = facts_store.load_pack(trade_date, parquet_dir=parquet_dir, db_path=db_path)
    lookback = required_lookback(params)
    if lookback > facts_store.MAX_LOOKBACK_PACKS:
        raise ParamsUnavailable(invalid=[
            f"这份参数包要读 {lookback} 个交易日的事实包,超过 MAX_LOOKBACK_PACKS="
            f"{facts_store.MAX_LOOKBACK_PACKS}(工程容量上限,§3.2)。"
            "P3 热门窗、活跃度窗与博弈窗的组合长度也必须落在容量内。"])
    start = _window_start(trade_date, lookback)
    frame = facts_store.load_pack_range(
        start, trade_date, as_of=trade_date,
        columns=sorted(DECLARED_FIELDS),
        parquet_dir=parquet_dir, db_path=db_path,
    )
    if frame.is_empty():
        raise PackUnavailable(f"{trade_date} 区间事实包为空(start={start})")
    return PackRange(as_of=trade_date, frame=frame,
                     pack_id=pack.pack_id, pack_version=pack.pack_version), pack


def _restrict(pack: PackRange, codes: Sequence[str]) -> PackRange:
    """把 `PackRange` 收窄到硬边界之后的池子(K9 §二:被排除的票**不进入任何形态召回**)。"""
    keep = list(codes)
    return PackRange(
        as_of=pack.as_of,
        frame=pack.frame.filter(pl.col("ts_code").is_in(keep)),
        pack_id=pack.pack_id,
        pack_version=pack.pack_version,
    )


def _relay_records(
    trade_date: date, params: K9Params, db_path: Optional[Path]
) -> List[ranking_mod.RelayRecord]:
    """跨日接力分的原料。读哪张表由 `relaySource` **全映射**决定(⛔ 无默认)。"""
    lookback = params.ranking.relay_lookback_days
    start = _window_start(trade_date, lookback + 1)
    table = ranking_mod.RELAY_TABLE_OF[params.ranking.relay_source]
    records = k9_store.load_relay_records(
        start=start, end=trade_date - timedelta(days=1),
        source_table=table, db_path=db_path,
    )
    return records


def _disposition_rows(
    trade_date: date,
    verdicts: pl.DataFrame,
    hits: Sequence[ChannelHit],
    allocation: quota_mod.Allocation,
) -> List[Dict[str, object]]:
    """全市场逐票处置(§5.4.8)。**一行一只票**,覆盖**当日在市的每一只票**。

    🔴 「每一只票」按字面意思算(R3-🔴-5 修复):`verdicts` 已经是
    「事实包的全部行 + 当日无 daily 行的那些」,后者由 `boundary.apply` 按第 6 条
    后半句补成 `suspended`。⛔ 不许退回「覆盖当日事实包的全部行」—— 全天停牌的票
    正是最需要这张表回答「昨天为什么没选中它」的那一类。

    ⚠ `news_excluded` 在解释层接入(S9)之前恒 **0** —— 「没有人被消息面剔除」在
    今天是**事实**(压根还没有人查公告)。S9 起它才开始区分「查过、没问题」。
    """
    import json

    recalled: Dict[str, List[str]] = {}
    tier_of: Dict[str, str] = {}
    for h in hits:
        recalled.setdefault(h.ts_code, []).append(h.pattern.value)
        if tier_of.get(h.ts_code) != Tier.STRICT.value:
            tier_of[h.ts_code] = h.tier.value
    seats = {s.candidate.ts_code: s for s in allocation.seated}
    ranks = {s.candidate.ts_code: s for s in (*allocation.seated, *allocation.reserve)}

    rows: List[Dict[str, object]] = []
    for r in verdicts.iter_rows(named=True):
        code = r["ts_code"]
        seat = seats.get(code)
        entry = ranks.get(code)
        pats = sorted(set(recalled.get(code, [])))
        rows.append({
            "trade_date": trade_date,
            "ts_code": code,
            "excluded_by": r[boundary_mod.REASON_COLUMN],
            "recalled_patterns_json": json.dumps(pats, ensure_ascii=False),
            "tier": tier_of.get(code),
            "score": None if entry is None else entry.candidate.score,
            "rank": None if entry is None else entry.rank,
            "seated": 1 if seat is not None else 0,
            "seat_kind": None if seat is None or seat.seat_kind is None
            else seat.seat_kind.value,
            "news_excluded": 0,
        })
    return rows


@dataclass(frozen=True)
class RunResult:
    """一次策略层运行的全部产物(编排器要的东西都在这里)。"""

    shortlist: Shortlist
    hits: Tuple[ChannelHit, ...]
    boundary_counts: Mapping[str, int]
    disposition_rows: Tuple[Dict[str, object], ...]
    over_strict: bool
    relaxed_streak: int
    pool_size: int


def compute(
    trade_date: date,
    *,
    params: K9Params,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> RunResult:
    """跑一遍策略层(**纯计算 + 只读**,⛔ 不落库 —— 落库是 `persist` 的事)。"""
    pack, frozen = build_pack_range(
        trade_date, params=params, parquet_dir=parquet_dir, db_path=db_path)

    # —— K9 第一层 · 硬边界 ————————————————————————————————————————————————
    # ⚠ `universe` = 当日在市全集(`stock_basic` 口径)。事实包只装当日 `daily` 有行的
    # 票,全天停牌的一只都不在里面 —— 而 §6 S6 要 disposition **覆盖全市场每一只票**
    # (R3-🔴-5)。取数经事实层过一手,⛔ 不在 k9 里 import `market_data`(守门 G3)。
    universe = facts_universe.market_universe(trade_date, db_path=db_path)
    activity_probe = activity_mod.compute(pack, target=pack.as_of, params=params.boundary)
    if activity_probe.is_empty():
        raise ParamsUnavailable(invalid=[
            "boundary.activity 缺少满足 minimumValidDays 的成交额或换手率参与密度；"
            "missingComponentPolicy=parameters_not_configured，拒绝降级运行"
        ])
    strict_verdicts = boundary_mod.apply(
        pack, boundary=params.boundary, industry=params.industry, universe=universe,
        activity_min_percentile=params.boundary.strict_activity_min_percentile)
    relaxed_verdicts = boundary_mod.apply(
        pack, boundary=params.boundary, industry=params.industry, universe=universe,
        activity_min_percentile=params.boundary.relaxed_activity_min_percentile)
    strict_pool = _restrict(pack, boundary_mod.survivors(strict_verdicts))
    relaxed_pool = _restrict(pack, boundary_mod.survivors(relaxed_verdicts))

    # —— K9 第二层 · 四通道(每通道两档)————————————————————————————————————
    hits: List[ChannelHit] = []
    for mod in CHANNELS:
        got_strict = [hit for hit in mod.run(strict_pool, params) if hit.tier is Tier.STRICT]
        got_relaxed = [hit for hit in mod.run(relaxed_pool, params) if hit.tier is Tier.RELAXED]
        hits.extend((*got_strict, *got_relaxed))
        logger.info("[k9] %s %s 严格/放宽召回 %d/%d 只",
                    trade_date, mod.PATTERN.value, len(got_strict), len(got_relaxed))

    decision = quota_mod.choose_tier(hits, params.quota)
    verdicts = strict_verdicts if decision.tier_used is Tier.STRICT else relaxed_verdicts
    pool_codes = boundary_mod.survivors(verdicts)
    counts = boundary_mod.counts(verdicts)
    logger.info(
        "[k9] %s %s硬边界:全市场 %d(事实包 %d 行)→池内 %d;逐条 %s",
        trade_date, decision.tier_used.value, verdicts.height, pack.today.height,
        len(pool_codes), counts)

    # —— K9 第三层 · 排序 ————————————————————————————————————————————————
    industry_rows = facts_industry.load_day(trade_date, db_path=db_path)
    heat = heat_mod.compute(industry_rows, params.industry)
    today = pack.today
    l2_of: Dict[str, Optional[str]] = {
        r["ts_code"]: r["sw_l2_code"]
        for r in today.select(["ts_code", "sw_l2_code"]).iter_rows(named=True)
    }
    scored, dropped = ranking_mod.rank(
        list(decision.hits), params=params, heat=heat, l2_of=l2_of,
        relay_records=_relay_records(trade_date, params, db_path),
    )

    # —— K9 §五 · 名额 ————————————————————————————————————————————————————
    # ⚠ 缺席从 `decision.hits`(drop **之前**)算 —— 与 `channel_counts` 同一个口径。
    # 被 `heatAbsentPolicy='drop'` 丢掉的票单独在 `dropped_by_heat_absent` 里说,
    # ⛔ 不许让它们把一个**有候选**的形态说成「今日无此形态」(复审 L1)。
    allocation = quota_mod.allocate(
        scored, params.quota,
        recalled_patterns={h.pattern for h in decision.hits})
    streak = k9_store.relaxed_streak_before(trade_date, db_path=db_path)
    streak_now = streak + 1 if decision.tier_used is Tier.RELAXED else 0
    over = quota_mod.over_strict(streak_now, params.quota)

    meta = {
        r["ts_code"]: r
        for r in today.select(
            ["ts_code", "name", "sw_l2_code", "sw_l2_name"]).iter_rows(named=True)
    }

    def to_entry(seat: quota_mod.Seat) -> Entry:
        c = seat.candidate
        m = meta.get(c.ts_code, {})
        return Entry(
            ts_code=c.ts_code, name=m.get("name"),
            sw_l2_code=m.get("sw_l2_code"), sw_l2_name=m.get("sw_l2_name"),
            patterns=c.patterns, primary_pattern=c.primary_pattern, tier=c.tier,
            rank=seat.rank, seat_kind=seat.seat_kind, score=c.score,
            industry_heat_score=c.industry_heat_score,
            pattern_strength_score=c.pattern_strength_score,
            relay_score=c.relay_score,
            evidence=c.evidence, risks=c.risks,
        )

    seated_entries = tuple(to_entry(s) for s in allocation.seated)
    channel_counts = {
        p.value: {
            **decision.per_pattern[p.value],
            "seated": sum(1 for e in seated_entries if e.primary_pattern is p),
        }
        for p in PATTERN_ORDER
    }
    shortlist = Shortlist(
        strategy=STRATEGY,
        strategy_version=STRATEGY_VERSION,
        label_contract_version=params.label_contract_version,
        scoring_contract={
            "touchThresholdU": params.scoring.touch_threshold_u,
            "riskLineL": params.scoring.risk_line_l,
            "d1Reference": params.scoring.d1_reference.value,
            "matchedBaseline": params.scoring.matched_baseline.value,
        },
        params_version=params.package_version,
        pack_version=frozen.pack_version,
        pack_id=frozen.pack_id,
        trade_date=trade_date,
        entries=seated_entries,
        reserve=tuple(to_entry(s) for s in allocation.reserve),
        tier_used=decision.tier_used,
        strict_candidates=decision.strict_candidates,
        relaxed_candidates=decision.relaxed_candidates,
        channel_counts=channel_counts,
        capacity_short=allocation.capacity_short,
        absent_patterns=allocation.absent_patterns,
        dropped_by_heat_absent=tuple(dropped),
    )
    logger.info(
        "[k9] %s 清单 %d 只(档=%s,严格 %d / 并集 %d)%s%s",
        trade_date, shortlist.size, decision.tier_used.value,
        decision.strict_candidates, decision.relaxed_candidates,
        ";容量不足(如实出这么多,⛔ 不造候选)" if allocation.capacity_short else "",
        ";判据过严,建议重标" if over else "",
    )
    return RunResult(
        shortlist=shortlist,
        hits=tuple(decision.hits),
        boundary_counts=counts,
        disposition_rows=tuple(
            _disposition_rows(trade_date, verdicts, decision.hits, allocation)),
        over_strict=over,
        relaxed_streak=streak_now,
        pool_size=len(pool_codes),
    )


def persist(
    result: RunResult,
    *,
    listing_finalized_by: str = k9_store.FINALIZED_BY_K9,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> str:
    """落库:`k9_runs` + `k9_channel_hits` + `k9_listing_entries` + disposition parquet。

    `listing_finalized_by='k9'` 表示只完成机械策略段；完整晚间链会在消息面剔除与
    后备补位后重写正式清单并标记为 `'explain'`。
    """
    run_id = k9_store.new_run_id()
    sl = result.shortlist
    k9_store.save_run(
        run_id=run_id, shortlist=sl, boundary_counts=result.boundary_counts,
        over_strict=result.over_strict, relaxed_streak=result.relaxed_streak,
        listing_finalized_by=listing_finalized_by, db_path=db_path,
    )
    k9_store.save_channel_hits(
        run_id=run_id, trade_date=sl.trade_date, hits=result.hits,
        seated_codes=[e.ts_code for e in sl.entries],
        strategy_version=sl.strategy_version, db_path=db_path,
    )
    k9_store.save_listing(run_id=run_id, shortlist=sl, db_path=db_path)
    k9_store.save_disposition(
        sl.trade_date, result.disposition_rows, parquet_dir=parquet_dir)
    return run_id


def run_k9(
    trade_date: date,
    *,
    params: K9Params,
    listing_finalized_by: str = k9_store.FINALIZED_BY_K9,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Tuple[RunResult, str]:
    """算 + 落库。编排器(晚间链的 `k9` 段)调这一个。"""
    result = compute(trade_date, params=params, parquet_dir=parquet_dir, db_path=db_path)
    run_id = persist(result, listing_finalized_by=listing_finalized_by,
                     parquet_dir=parquet_dir, db_path=db_path)
    return result, run_id


__all__ = [
    "CHANNELS", "PackUnavailable", "RunResult",
    "required_lookback", "build_pack_range", "compute", "persist", "run_k9",
]
