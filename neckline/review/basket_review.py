"""⑤-b 盘后复盘引擎(plan §五 V2-⑨-A / ⑨-B)。D+1 收盘后回答一个问题:**D0 那份
篮子判断,今天哪里对了、哪里错了**,并把依据留成可复现的数字。

**⑨-A 机械判九项**(蓝图 4.8,`mech_json` 的九个顶层键,顺序即本模块函数顺序)::

    ① auction_vs_script    竞价 vs 剧本(竞价高开幅度落在卡上哪一条分支剧本里)
    ② open_direction       开盘首方向(高/平/低开 × 收盘相对开盘的日内方向)
    ③ mfe_mae              分时 MFE / MAE(**有存拍才有时刻**,缺存拍走 EOD 近似并标注)
    ④ member_alignment     成员同向率(共振的直接证据)
    ⑤ leader_pull          龙头带动(龙头 vs 同篮其余成员)
    ⑥ buyability           可买性(一字与涨停排除,与 `fwd_buyable` 同口径)
    ⑦ verification_timing  验证与证伪时点(读 ⑧ 的 `basket_verification` 流水)
    ⑧ close_rs             收盘 RS(成员超额收益 vs 大盘)
    ⑨ tier_vs_outcome      D0 逻辑与 Tier 哪里对哪里错的**机械依据**(不下结论)

**三条铁律**

    1. **「没有」与「没看」必须分得开**:每一项都带 `available` / `source`;算不出
       就落 `null` + `unavailable_reason`,⛔ 不许 `or 0.0` 兜底,也不许把"没数据"
       写成"表现平平"。
    2. **只记录,不改策略**(蓝图 4.9):本模块产出**只进复盘表与周报**,⛔ 不进任何
       在线判据 —— 不改 Tier、不改排序、不进哨兵、不接持仓动作。改权重一律走换包。
    3. **前视零容忍**:只读 `review_date` 当日及之前的数据。跨篮子的排名类量在
       **当日**内部算(同日全部篮子),不吃未来任何一天。

**分层键**:每份复盘都带 `pack_version`(D0 那天的现役选股包)与
`verification_ruleset_version`(⑦-b 冻在卡指纹里的条件集版本)。⑨-C 评价引擎按
这两个键**分层归因** —— 没有它们,两套条件集 / 两个包的成绩会混成一锅(⑦-b 原文)。

⚠ **本模块的分档阈值全是「临时默认、零审计背书」**(同 `selection/verification_rules.py`
的登记体例):`AUCTION_STRONG_GAP` / `AUCTION_WEAK_GAP` / `FLAT_EPS` 没有任何回测或
事件研究支持,它们只是为了把「竞价算强还是算弱」变成可复现的机器判据。**升级路径**:
⑨-C 攒够样本后由策略线校准 → 走换包。在那之前 ⛔ 不许自行改数。
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.calendar import prev_trading_day
from neckline.llm.budget import (
    DEGRADE_ORDER, DROP_T2_REVIEW_DETAIL, LEDGER_REVIEW, BudgetLedger,
)
from neckline.llm.json_block import split_narrative_and_reference_json
from neckline.llm.prompt_context import date_anchor_line
from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.router import TASK_REVIEW
from neckline.selection import verification_rules as vr
from neckline.selection.basket_store import BasketRef, load_basket_card, load_baskets_for_date
from neckline.sentinel import basket_verify_store as vstore

logger = logging.getLogger(__name__)

#: `mech_json` 的形状版本(**形状变了就 bump**;条件集版本是另一回事,那个跟卡走)。
MECH_SPEC_VERSION = "basket_review_mech_v1"

DEPTH_FULL = "full"      # T1 / T2:每日必复盘(机械判 + LLM 解释)—— V2.1 起唯一写入值
# ⚠ **`DEPTH_BRIEF` 保留但已无写入方**(V2.1-② T3 全链退役):历史
# `basket_review_daily` 里 `depth='brief'` 的旧行仍要能读回渲染 —— 这是「停写留档」
# 纪律在**值**层面的同一条(⛔ 别删这个常量,删了历史复盘就读不回来了)。
DEPTH_BRIEF = "brief"    # 历史值:T3 篮子的简评深度(V2.1 起不再产生新行)

MFE_SOURCE_INTRADAY = "intraday"     # 有存拍:幅度 + **时刻**都有
MFE_SOURCE_EOD_APPROX = "eod_approx"  # 缺存拍:用当日最高/最低近似,**没有时刻**

# —— 分档阈值(⚠ 零审计背书的工程默认,见模块 docstring;改动要走换包)——————
AUCTION_STRONG_GAP = 0.02     # 竞价相对 D0 收盘 ≥ +2% → 落「strong」分支剧本
AUCTION_WEAK_GAP = -0.02      # ≤ −2% → 落「weak」分支剧本;中间 → 「flat」
FLAT_EPS = 1e-4               # 判「涨/跌/平」的死区(0.01% 以内算平,防浮点噪声)

# 浮点比较容差(工程不变量,同 `verification_rules.EPS` 先例)。
EPS = 1e-9

#: 本模块消费的 LLM 任务(② 的任务常量单一源;调用方一律从这里取,别抄字符串)。
REVIEW_TASK = TASK_REVIEW

_SCRIPT_BRANCHES = ("strong", "flat", "weak")


# ══════════════════════════════════════════════════════════════════════════
# 当日市场面(**每日装配一次**,全部篮子复用 —— 别每篮各读一次 parquet)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class DayMarket:
    """`review_date` 当日的观测面。每个字段都可能是空的,空就是空,不编。"""

    review_date: date
    d0: date
    bars: Dict[str, Dict[str, Any]] = field(default_factory=dict)      # daily 行
    limits: Dict[str, Dict[str, Any]] = field(default_factory=dict)    # limit_derived 行(稀疏)
    index_code: Optional[str] = None
    index_ret: Optional[float] = None                                   # 小数,不是百分数
    ticks: Dict[str, List[Tuple[str, float]]] = field(default_factory=dict)
    auction: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    capture_status: Dict[str, Any] = field(default_factory=dict)
    auction_capture_status: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def _index_return(review_date: date, parquet_dir: Optional[Path]) -> Tuple[Optional[str], Optional[float]]:
    """大盘当日收益(RS 的基准,与信息卡 RS 线同源 `strategy.features.SSE_INDEX`)。"""
    try:
        from neckline.data.market_data import get_index_history
        from neckline.strategy.features import SSE_INDEX

        df = get_index_history(SSE_INDEX, review_date, review_date, parquet_dir=parquet_dir)
        if df.is_empty():
            return SSE_INDEX, None
        pct = df["pct_chg"].to_list()[0]
        return SSE_INDEX, (float(pct) / 100.0 if pct is not None else None)
    except Exception:  # noqa: BLE001 —— 可选情报的保险丝(§铁律),缺大盘只是少一维
        logger.warning("[basket_review] 大盘指数当日收益读取失败,RS 一项将标为算不出", exc_info=True)
        return None, None


def _load_ticks(review_date: date, codes: Sequence[str],
                parquet_dir: Optional[Path]) -> Dict[str, List[Tuple[str, float]]]:
    """当日 `intraday_ticks` 分区 → `ts_code -> [(ts, price)]`(按 ts 升序)。

    没有分区 / 读不出 → 空 dict(调用方走 EOD 近似并**如实标注**,⛔ 不装精确)。
    """
    if not codes:
        return {}
    try:
        import polars as pl

        from neckline.data.market_data import get_market_slice

        df = get_market_slice(review_date, table="intraday_ticks", parquet_dir=parquet_dir)
        if df.is_empty():
            return {}
        df = df.filter(pl.col("ts_code").is_in(list(codes))).sort(["ts_code", "ts"])
        out: Dict[str, List[Tuple[str, float]]] = {}
        for row in df.iter_rows(named=True):
            px = row.get("price")
            if px is None:
                continue
            out.setdefault(row["ts_code"], []).append((str(row.get("ts") or ""), float(px)))
        return out
    except Exception:  # noqa: BLE001
        logger.info("[basket_review] %s 无盘中存拍分区(或读取失败),MFE/MAE 走 EOD 近似", review_date)
        return {}


def _load_auction(review_date: date, codes: Sequence[str],
                  parquet_dir: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    if not codes:
        return {}
    try:
        import polars as pl

        from neckline.data.market_data import get_market_slice

        df = get_market_slice(review_date, table="auction_snapshots", parquet_dir=parquet_dir)
        if df.is_empty():
            return {}
        df = df.filter(pl.col("ts_code").is_in(list(codes)))
        return {r["ts_code"]: dict(r) for r in df.iter_rows(named=True)}
    except Exception:  # noqa: BLE001
        logger.info("[basket_review] %s 无竞价快照分区(或读取失败)", review_date)
        return {}


def build_day_market(
    review_date: date,
    codes: Sequence[str],
    *,
    d0: Optional[date] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> DayMarket:
    """装配当日观测面。**每一段都包保险丝**:任何一段读失败都只让对应的机械判项标
    「算不出」,⛔ 不许把当日复盘整个掀翻(§铁律「核心管线对可选情报输入包保险丝」)。
    """
    from neckline.sentinel.capture import EVENT_AUCTION, EVENT_INTRADAY, load_capture_status

    want = [c for c in dict.fromkeys(codes) if c]
    dm = DayMarket(review_date=review_date, d0=d0 or prev_trading_day(review_date))

    try:
        import polars as pl

        from neckline.data.market_data import get_market_slice

        bars = get_market_slice(review_date, table="daily", parquet_dir=parquet_dir)
        if not bars.is_empty() and want:
            bars = bars.filter(pl.col("ts_code").is_in(want))
            dm.bars = {r["ts_code"]: dict(r) for r in bars.iter_rows(named=True)}
        elif bars.is_empty():
            dm.notes.append(f"{review_date} 无 daily 分区(当日行情缺失)")
    except Exception:  # noqa: BLE001
        logger.warning("[basket_review] daily 当日切片读取失败", exc_info=True)
        dm.notes.append("daily 当日切片读取失败")

    try:
        import polars as pl

        from neckline.data.market_data import get_market_slice

        lim = get_market_slice(review_date, table="limit_derived", parquet_dir=parquet_dir)
        if not lim.is_empty() and want:
            lim = lim.filter(pl.col("ts_code").is_in(want))
            dm.limits = {r["ts_code"]: dict(r) for r in lim.iter_rows(named=True)}
    except Exception:  # noqa: BLE001
        logger.warning("[basket_review] limit_derived 当日切片读取失败", exc_info=True)
        dm.notes.append("limit_derived 当日切片读取失败(可买性判据降级)")

    dm.index_code, dm.index_ret = _index_return(review_date, parquet_dir)
    dm.ticks = _load_ticks(review_date, want, parquet_dir)
    dm.auction = _load_auction(review_date, want, parquet_dir)
    try:
        dm.capture_status = load_capture_status(review_date, EVENT_INTRADAY, db_path=db_path)
        dm.auction_capture_status = load_capture_status(review_date, EVENT_AUCTION, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[basket_review] 存拍台账读取失败", exc_info=True)
        dm.capture_status = {"capture_status": "missing", "recorded": False}
        dm.auction_capture_status = {"capture_status": "missing", "recorded": False}
    return dm


# ══════════════════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════════════════

def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if (f == f and not math.isinf(f)) else None


def _median(xs: Sequence[float]) -> Optional[float]:
    s = sorted(x for x in xs if x is not None)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _sign(x: Optional[float], eps: float = FLAT_EPS) -> Optional[str]:
    if x is None:
        return None
    if x > eps:
        return "up"
    if x < -eps:
        return "down"
    return "flat"


def _card_members(card: Optional[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(card, Mapping):
        return {}
    return {str(m.get("ts_code")): m for m in (card.get("members") or [])
            if isinstance(m, Mapping) and m.get("ts_code")}


def _card_mech(member: Optional[Mapping[str, Any]], key: str) -> Any:
    """取卡上成员的**机械数据**字段(`close`/`ma20`/`limit_up`/`limit_down`/`stop_price`)。

    ⚠ 这些字段住在 `members[].mech` 子对象里(⑦ `MemberCardEntry.to_dict()` 的形状),
    **不在成员顶层** —— 施工时按顶层读过一次,真卡上一律取到 `None`,可买性整项静默
    退化成"涨停价算不出"(冒烟才照出来)。顶层保留为兜底(合成卡 / 老形状)。
    """
    if not isinstance(member, Mapping):
        return None
    mech = member.get("mech")
    if isinstance(mech, Mapping) and mech.get(key) is not None:
        return mech.get(key)
    return member.get(key)


def member_return(code: str, day: DayMarket) -> Optional[float]:
    """成员当日收益(小数)。**用 `daily.pct_chg`**,不是「收盘/卡里的 D0 收盘 − 1」。

    理由:卡里的 D0 锚是**前复权**口径(⑧ 已登记的已知局限),而 `pct_chg` 由 TuShare
    按当日 `pre_close`(已处理除权除息)算,恰好绕开「D+1 除权 → 锚与观测差一个复权
    因子」那个坑。⛔ 别"顺手"改成拿卡里的收盘价相减。
    """
    bar = day.bars.get(code)
    if not bar:
        return None
    pct = _num(bar.get("pct_chg"))
    return None if pct is None else pct / 100.0


# ══════════════════════════════════════════════════════════════════════════
# ① 竞价 vs 剧本
# ══════════════════════════════════════════════════════════════════════════

def script_branch(gap: Optional[float]) -> Optional[str]:
    """竞价高开幅度 → 卡上三条分支剧本中的哪一条(阈值见模块头登记)。"""
    if gap is None:
        return None
    if gap >= AUCTION_STRONG_GAP - EPS:
        return "strong"
    if gap <= AUCTION_WEAK_GAP + EPS:
        return "weak"
    return "flat"


def judge_auction_vs_script(codes: Sequence[str], card: Optional[Mapping[str, Any]],
                            day: DayMarket) -> Dict[str, Any]:
    """① 竞价 vs 剧本。竞价幅度**优先取存拍的竞价快照**(09:25 真竞价),没有存拍
    才退回「当日开盘价 / 昨收 − 1」——**两者不是一回事**(竞价快照是集合竞价的最终
    撮合价,开盘价则是它的成交结果),故 `source` 必须落下来给读者分辨。"""
    per: Dict[str, Any] = {}
    gaps: List[float] = []
    src = "auction_snapshot"
    for code in codes:
        gap = None
        snap = day.auction.get(code)
        if snap is not None:
            gap = _num(snap.get("gap_pct"))
        if gap is None:
            bar = day.bars.get(code)
            if bar:
                op, pre = _num(bar.get("open")), _num(bar.get("pre_close"))
                gap = (op / pre - 1.0) if (op is not None and pre) else None
            if gap is not None:
                src = "daily_open" if src != "mixed" else "mixed"
        per[code] = {"gap": gap, "branch": script_branch(gap)}
        if gap is not None:
            gaps.append(gap)
    if not day.auction:
        src = "daily_open"

    med = _median(gaps)
    branch = script_branch(med)
    scripts = (card or {}).get("scripts") if isinstance(card, Mapping) else None
    scripts = scripts if isinstance(scripts, Mapping) else {}
    text = scripts.get(branch) if branch else None
    return {
        "available": med is not None,
        "unavailable_reason": None if med is not None else "全体成员当日竞价与开盘价都取不到",
        "source": src,
        "gap_median": med,
        "branch": branch,
        "script_present": bool(text),
        "script_text": text or None,
        "scripts_branches_on_card": [b for b in _SCRIPT_BRANCHES if scripts.get(b)],
        "per_member": per,
    }


# ══════════════════════════════════════════════════════════════════════════
# ② 开盘首方向
# ══════════════════════════════════════════════════════════════════════════

def judge_open_direction(codes: Sequence[str], day: DayMarket) -> Dict[str, Any]:
    """② 开盘首方向 = 「跳空方向」×「日内方向」。

    有存拍时额外给**开盘后首个观测拍相对开盘价的方向**(`first_tick_dir`)——那才是
    严格意义的"开盘首方向";没有存拍就只有前两个,`first_tick_dir=None` 而不是编一个。
    """
    per: Dict[str, Any] = {}
    gaps, days = [], []
    for code in codes:
        bar = day.bars.get(code)
        if not bar:
            per[code] = {"gap": None, "intraday": None, "first_tick_dir": None}
            continue
        op, pre, cl = _num(bar.get("open")), _num(bar.get("pre_close")), _num(bar.get("close"))
        gap = (op / pre - 1.0) if (op is not None and pre) else None
        intra = (cl / op - 1.0) if (cl is not None and op) else None
        first_dir = None
        seq = day.ticks.get(code) or []
        if seq and op:
            first_dir = _sign(seq[0][1] / op - 1.0)
        per[code] = {"gap": gap, "intraday": intra, "gap_dir": _sign(gap),
                     "intraday_dir": _sign(intra), "first_tick_dir": first_dir}
        if gap is not None:
            gaps.append(gap)
        if intra is not None:
            days.append(intra)
    gm, dm_ = _median(gaps), _median(days)
    return {
        "available": gm is not None or dm_ is not None,
        "unavailable_reason": None if (gm is not None or dm_ is not None) else "当日无成员行情",
        "gap_median": gm, "intraday_median": dm_,
        "gap_dir": _sign(gm), "intraday_dir": _sign(dm_),
        "aligned": (None if (gm is None or dm_ is None) else _sign(gm) == _sign(dm_)),
        "has_intraday_capture": bool(day.ticks),
        "per_member": per,
    }


# ══════════════════════════════════════════════════════════════════════════
# ③ 分时 MFE / MAE
# ══════════════════════════════════════════════════════════════════════════

def judge_mfe_mae(codes: Sequence[str], day: DayMarket) -> Dict[str, Any]:
    """③ 分时 MFE / MAE(相对**当日 `pre_close`**,与成员收益同锚,不吃复权因子)。

    · **有存拍**(`intraday`):从分钟快照序列取极值,**带时刻**(`mfe_at`/`mae_at`)。
      ⚠ 快照是**采样**,可能错过两拍之间的瞬时极值 —— 这是采样的固有代价,已如实登记。
    · **缺存拍**(`eod_approx`):用当日 `high`/`low` 近似。⚠ 这两个数其实是当日**真实
      极值**(比采样更准),缺的是**时刻与路径** —— 所以 `eod_approx` 的诚实含义是
      「幅度可信、时刻未知」,**不是**「幅度是估的」。plan 要求的「不装精确」指的正是
      不许凭空给它编一个时刻。
    """
    per: Dict[str, Any] = {}
    mfes, maes = [], []
    src = MFE_SOURCE_EOD_APPROX if not day.ticks else MFE_SOURCE_INTRADAY
    mixed = False
    for code in codes:
        bar = day.bars.get(code)
        pre = _num((bar or {}).get("pre_close"))
        seq = day.ticks.get(code) or []
        row: Dict[str, Any] = {"source": None, "mfe": None, "mae": None,
                               "mfe_at": None, "mae_at": None}
        if pre:
            if seq:
                hi_ts, hi = max(seq, key=lambda x: x[1])[0], max(p for _, p in seq)
                lo_ts, lo = min(seq, key=lambda x: x[1])[0], min(p for _, p in seq)
                row.update(source=MFE_SOURCE_INTRADAY, mfe=hi / pre - 1.0, mae=lo / pre - 1.0,
                           mfe_at=hi_ts or None, mae_at=lo_ts or None)
            elif bar:
                hi, lo = _num(bar.get("high")), _num(bar.get("low"))
                if hi is not None and lo is not None:
                    row.update(source=MFE_SOURCE_EOD_APPROX, mfe=hi / pre - 1.0, mae=lo / pre - 1.0)
        if row["source"] == MFE_SOURCE_EOD_APPROX and src == MFE_SOURCE_INTRADAY:
            mixed = True
        per[code] = row
        if row["mfe"] is not None:
            mfes.append(row["mfe"])
        if row["mae"] is not None:
            maes.append(row["mae"])
    if mixed:
        src = "mixed"
    cap = dict(day.capture_status or {})
    return {
        "available": bool(mfes or maes),
        "unavailable_reason": None if (mfes or maes) else "当日既无存拍也无 EOD 行情",
        "mfe_source": src,
        "mfe_median": _median(mfes), "mae_median": _median(maes),
        "capture_status": cap.get("capture_status"),
        "capture_recorded": bool(cap.get("recorded")),
        "capture_covered_minutes": cap.get("covered_minutes"),
        "capture_expected_minutes": cap.get("expected_minutes"),
        "capture_empty_ticks": cap.get("empty_ticks"),
        "note": ("存拍缺席,MFE/MAE 用当日最高/最低近似:幅度可信、**时刻未知**"
                 if src == MFE_SOURCE_EOD_APPROX else None),
        "per_member": per,
    }


# ══════════════════════════════════════════════════════════════════════════
# ④ 成员同向率
# ══════════════════════════════════════════════════════════════════════════

def judge_member_alignment(codes: Sequence[str], day: DayMarket) -> Dict[str, Any]:
    """④ 成员同向率。`alignment` = 占多数的那个方向的占比(`max(up,down,flat)/observed`)
    —— 这是「共振」的直接机械证据,与 ⑦-b 的验证条件互相独立(那个看的是绝对水平,
    这个看的是**一致性**)。"""
    rets = {c: member_return(c, day) for c in codes}
    observed = [c for c, r in rets.items() if r is not None]
    dirs = {c: _sign(rets[c]) for c in observed}
    up = sum(1 for c in observed if dirs[c] == "up")
    down = sum(1 for c in observed if dirs[c] == "down")
    flat = len(observed) - up - down
    n = len(observed)
    return {
        "available": n > 0,
        "unavailable_reason": None if n else "全体成员当日无行情",
        "member_count": len(codes), "observed": n,
        "up": up, "down": down, "flat": flat,
        "up_ratio": (up / n) if n else None,
        "alignment": (max(up, down, flat) / n) if n else None,
        "dominant_direction": (max((("up", up), ("down", down), ("flat", flat)),
                                   key=lambda kv: (kv[1], kv[0]))[0] if n else None),
        "returns": {c: rets[c] for c in codes},
        "missing": [c for c in codes if rets[c] is None],
    }


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 龙头带动
# ══════════════════════════════════════════════════════════════════════════

def resolve_leaders(codes: Sequence[str], card: Optional[Mapping[str, Any]]) -> List[str]:
    """从**冻结卡**里认龙头(D0 的判断,不用今天的数据重认)。

    优先级:`role_mech == 'leader'` > `rs_rank == 1` > `is_primary`。⛔ 不拿当日涨幅
    最大的那只当龙头 —— 那是拿结果当原因,复盘会自证正确。
    """
    members = _card_members(card)
    for pick in (lambda m: (m.get("role_mech") or "") == "leader",
                 lambda m: m.get("rs_rank") == 1,
                 lambda m: bool(m.get("is_primary"))):
        hit = [c for c in codes if c in members and pick(members[c])]
        if hit:
            return sorted(hit)
    return []


def judge_leader_pull(codes: Sequence[str], card: Optional[Mapping[str, Any]],
                      day: DayMarket) -> Dict[str, Any]:
    """⑤ 龙头带动:龙头收益 vs 同篮其余成员收益中位数。"""
    leaders = resolve_leaders(codes, card)
    rets = {c: member_return(c, day) for c in codes}
    lead_rets = [rets[c] for c in leaders if rets.get(c) is not None]
    other_rets = [rets[c] for c in codes if c not in leaders and rets.get(c) is not None]
    lm, om = _median(lead_rets), _median(other_rets)
    return {
        "available": lm is not None,
        "unavailable_reason": (None if lm is not None else
                               ("卡上认不出龙头" if not leaders else "龙头当日无行情")),
        "leaders": leaders,
        "leader_source": ("card_role_mech_or_rank" if leaders else None),
        "leader_ret_median": lm, "others_ret_median": om,
        "spread": (None if (lm is None or om is None) else lm - om),
        "led": (None if (lm is None or om is None) else lm >= om - EPS),
        # 「没有可比的同篮其他成员」——单票篮,或成员**全部**被 D0 卡认成龙头
        # (多簇合成的篮子会这样)。名字刻意不叫 `solo_basket`:后者会被读成
        # 「这篮只有一只票」,而真实含义是**这一项没有对照组、`led` 判不了**。
        "no_peer_group": len(codes) <= 1 or not other_rets,
        "member_count": len(codes),
    }


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 可买性(一字与涨停排除)
# ══════════════════════════════════════════════════════════════════════════

BUY_OK = "buyable"
BUY_NO_BAR = "no_bar"                 # 当日无行情(停牌 / 数据缺口)
BUY_LIMIT_UP_CLOSE = "limit_up"       # 收在涨停 → 买不进(与 `fwd_buyable` 同口径)
BUY_ONE_WORD = "one_word_board"       # 一字板(开=高=低=涨停),涨停里最极端的一种


def judge_buyability(codes: Sequence[str], card: Optional[Mapping[str, Any]],
                     day: DayMarket) -> Dict[str, Any]:
    """⑥ 可买性。口径**对齐项目既有 `strategy/features.py::fwd_buyable`**(次日有成交
    且非涨停);在此之上额外把「一字板」单独标出来(它是涨停里最极端的一种,复盘要
    分得开「涨停但盘中有量能买」与「一字根本没机会」)。

    涨停价优先取**卡上冻结的次日涨停价**(`members[].limit_up`,⑧-C2 第 2 条「锚取
    D0 冻结值」的同一条纪律);卡上算不出才退回当日 `limit_derived`(稀疏表,只有命中
    行才有)。两者都没有 → 只能用 `limit_derived.is_limit_up` 判,没有该行就当没涨停。
    """
    members = _card_members(card)
    per: Dict[str, Any] = {}
    buyable_n = 0
    for code in codes:
        bar = day.bars.get(code)
        if not bar:
            per[code] = {"buyable": False, "reason": BUY_NO_BAR, "limit_up_source": None}
            continue
        lim_row = day.limits.get(code) or {}
        frozen_lu = _num(_card_mech(members.get(code), "limit_up"))
        lu = frozen_lu if frozen_lu is not None else _num(lim_row.get("limit_up_price"))
        lu_src = "card_frozen" if frozen_lu is not None else (
            "limit_derived" if lu is not None else None)
        op, hi, lo, cl = (_num(bar.get("open")), _num(bar.get("high")),
                          _num(bar.get("low")), _num(bar.get("close")))
        at_limit_close = (bool(lim_row.get("is_limit_up"))
                          or (lu is not None and cl is not None and cl >= lu - EPS))
        one_word = bool(
            lu is not None and None not in (op, hi, lo)
            and op >= lu - EPS and hi >= lu - EPS and lo >= lu - EPS
        )
        if one_word:
            reason = BUY_ONE_WORD
        elif at_limit_close:
            reason = BUY_LIMIT_UP_CLOSE
        else:
            reason = BUY_OK
        ok = reason == BUY_OK
        buyable_n += 1 if ok else 0
        per[code] = {"buyable": ok, "reason": reason, "limit_up": lu, "limit_up_source": lu_src}
    n = len(codes)
    return {
        "available": n > 0 and any(day.bars.get(c) for c in codes),
        "unavailable_reason": None if any(day.bars.get(c) for c in codes) else "当日无成员行情",
        "member_count": n, "buyable": buyable_n,
        "buyable_ratio": (buyable_n / n) if n else None,
        "one_word": sum(1 for v in per.values() if v["reason"] == BUY_ONE_WORD),
        "limit_up": sum(1 for v in per.values() if v["reason"] == BUY_LIMIT_UP_CLOSE),
        "no_bar": sum(1 for v in per.values() if v["reason"] == BUY_NO_BAR),
        "per_member": per,
    }


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 验证与证伪时点
# ══════════════════════════════════════════════════════════════════════════

def judge_verification_timing(basket_id: int, review_date: date, *,
                              db_path: Optional[Path] = None) -> Dict[str, Any]:
    """⑦ 验证与证伪时点:读 ⑧ 的 `basket_verification` 流水(**三路读法走
    `current_state()` 唯一实现,⛔ 不自己拼 SQL**)。

    「今天还没判过」与「判了是 unclear」必须分得开 —— 前者 `not_evaluated=True`,
    后者是一条真实的 `unclear` 行。⚠ EOD 定论行由 ⑭-A 的 16:35 链(或
    `scripts/basket_verify.py`)产生;那一拍没跑过的话这里只会看到盘中暂态。
    """
    rows = vstore.list_rows(basket_id, review_date, db_path=db_path)
    cur = vstore.current_state(basket_id, review_date, db_path=db_path)
    first_of = lambda st: next((r.observed_at for r in rows if r.state == st), None)  # noqa: E731
    eod = [r for r in rows if r.source == vstore.SOURCE_EOD]
    return {
        "available": bool(rows),
        "unavailable_reason": None if rows else "当日这只篮子没有任何验证记录(那一拍没跑过)",
        "state": cur.state,
        "state_label": cur.label,
        "provisional": bool(cur.provisional),
        "not_evaluated": bool(cur.not_evaluated),
        "rows": len(rows),
        "intraday_rows": sum(1 for r in rows if r.source == vstore.SOURCE_INTRADAY),
        "has_eod_verdict": bool(eod),
        "eod_state": eod[-1].state if eod else None,
        "first_verified_at": first_of(vr.STATE_VERIFIED),
        "first_falsified_at": first_of(vr.STATE_FALSIFIED),
        "first_partial_at": first_of(vr.STATE_PARTIAL),
        "latched_falsified": any(r.state == vr.STATE_FALSIFIED for r in rows),
        "trail": [{"observed_at": r.observed_at, "state": r.state, "source": r.source} for r in rows],
    }


# ══════════════════════════════════════════════════════════════════════════
# ⑧ 收盘 RS
# ══════════════════════════════════════════════════════════════════════════

def judge_close_rs(codes: Sequence[str], day: DayMarket) -> Dict[str, Any]:
    """⑧ 收盘 RS = 成员当日收益 − 大盘当日收益(超额,小数)。"""
    if day.index_ret is None:
        return {"available": False, "unavailable_reason": "当日大盘指数收益取不到",
                "index_code": day.index_code, "index_ret": None,
                "excess_median": None, "rs_positive": None, "per_member": {}}
    per = {}
    ex: List[float] = []
    for code in codes:
        r = member_return(code, day)
        e = None if r is None else r - day.index_ret
        per[code] = {"ret": r, "excess": e}
        if e is not None:
            ex.append(e)
    med = _median(ex)
    return {
        "available": med is not None,
        "unavailable_reason": None if med is not None else "全体成员当日无行情",
        "index_code": day.index_code, "index_ret": day.index_ret,
        "excess_median": med,
        "rs_positive": (None if med is None else med > EPS),
        "outperformers": sum(1 for v in per.values() if (v["excess"] or 0) > EPS),
        "per_member": per,
    }


# ══════════════════════════════════════════════════════════════════════════
# ⑨ D0 逻辑与 Tier 的机械依据
# ══════════════════════════════════════════════════════════════════════════

def judge_tier_vs_outcome(
    ref: BasketRef,
    card: Optional[Mapping[str, Any]],
    day: DayMarket,
    *,
    tier_row: Optional[Mapping[str, Any]] = None,
    day_rank: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """⑨ D0 逻辑与 Tier 哪里对哪里错的**机械依据**。

    ⚠ **只给依据、不下结论**:本项把「D0 排第几」与「当日结果排第几」并排放好,给出
    `rank_gap`(名次差),**不判"Tier 错了"** —— 单日名次差是噪声,结论只能由 ⑨-C
    在足够样本上给(蓝图 4.9「每日复盘只记录,不因单日失败改策略」)。
    """
    c = card if isinstance(card, Mapping) else {}
    breakdown = c.get("tier_breakdown") or {}
    if not breakdown and isinstance(tier_row, Mapping):
        breakdown = tier_row.get("mech_breakdown") or {}
    rets = [r for r in (member_return(x, day) for x in ref.member_codes) if r is not None]
    outcome = _median(rets)
    out: Dict[str, Any] = {
        "available": outcome is not None,
        "unavailable_reason": None if outcome is not None else "当日无成员行情,算不出篮子结果",
        "tier": ref.tier,
        "mech_score": c.get("mech_score") if c.get("mech_score") is not None else (
            (tier_row or {}).get("mech_score")),
        "rank_in_tier": c.get("rank_in_tier") if c.get("rank_in_tier") is not None else (
            (tier_row or {}).get("rank_in_tier")),
        "rank_mech": c.get("rank_mech") if c.get("rank_mech") is not None else (
            (tier_row or {}).get("rank_mech")),
        "tier_breakdown": dict(breakdown) if isinstance(breakdown, Mapping) else {},
        "basket_ret_median": outcome,
    }
    if day_rank:
        out.update({
            "day_baskets": day_rank.get("total"),
            "rank_by_tier": day_rank.get("rank_by_tier"),
            "rank_by_outcome": day_rank.get("rank_by_outcome"),
            "rank_gap": day_rank.get("rank_gap"),
            "rank_note": "名次差是当日横截面的机械依据,**单日不构成结论**",
        })
    return out


def day_rank_table(entries: Sequence[Tuple[str, int, Optional[int], Optional[float]]]) -> Dict[str, Dict[str, Any]]:
    """当日横截面名次表:`(basket_key, tier, rank_in_tier, outcome)` → 逐篮名次。

    **两处排序都先排定确定性 tie-break 再定名次**(§铁律:`rank(method="ordinal")`
    的并列由行序打散 = 不确定性):
        · D0 序:`(tier 升, rank_in_tier 升〔缺则 +inf〕, basket_key 升)`
        · 结果序:`(当日收益 降, basket_key 升)`;**算不出收益的篮子排在最后**,
          且 `rank_by_outcome=None`(⛔ 不给它编一个名次)。
    """
    d0_sorted = sorted(entries, key=lambda e: (e[1], e[2] if e[2] is not None else 10**9, e[0]))
    d0_rank = {e[0]: i + 1 for i, e in enumerate(d0_sorted)}
    scored = [e for e in entries if e[3] is not None]
    out_sorted = sorted(scored, key=lambda e: (-float(e[3]), e[0]))
    out_rank = {e[0]: i + 1 for i, e in enumerate(out_sorted)}
    total = len(entries)
    table: Dict[str, Dict[str, Any]] = {}
    for key, _tier, _rit, outcome in entries:
        rb, ro = d0_rank[key], out_rank.get(key)
        table[key] = {
            "total": total, "rank_by_tier": rb, "rank_by_outcome": ro,
            "rank_gap": (None if ro is None else ro - rb),
        }
    return table


# ══════════════════════════════════════════════════════════════════════════
# 一篮的机械判(九项装配)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class BasketReview:
    """一个篮子在 `review_date` 的复盘结果(**纯值对象**,落库由 store 负责)。"""

    basket_id: int
    basket_key: str
    name: str
    tier: int
    review_date: date
    d0: date
    depth: str
    mech: Dict[str, Any]
    llm_text: Optional[str] = None
    llm_skip_reason: Optional[str] = None
    degraded: bool = False

    @property
    def pack_version(self) -> Optional[str]:
        return (self.mech.get("meta") or {}).get("pack_version")

    @property
    def ruleset_version(self) -> Optional[str]:
        return (self.mech.get("meta") or {}).get("verification_ruleset_version")


def build_mech(
    ref: BasketRef,
    card: Optional[Mapping[str, Any]],
    day: DayMarket,
    *,
    db_path: Optional[Path] = None,
    tier_row: Optional[Mapping[str, Any]] = None,
    day_rank: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """九项机械判装配成 `mech_json`。**每一项各自包保险丝**:某一项算炸了只让那一项
    落 `error`,其余八项照出(§铁律:任何一段异常都不许让当日无复盘)。"""
    codes = list(ref.member_codes)
    fp = (card or {}).get("fingerprint") if isinstance(card, Mapping) else None
    fp = fp if isinstance(fp, Mapping) else {}
    mech: Dict[str, Any] = {
        "spec_version": MECH_SPEC_VERSION,
        "meta": {
            "basket_id": ref.basket_id, "basket_key": ref.basket_key, "name": ref.name,
            "tier": ref.tier, "d0": ref.trade_date,
            "review_date": day.review_date.strftime("%Y%m%d"),
            "member_count": len(codes), "members": codes,
            "has_card": bool(card),
            "card_version": (card or {}).get("version") if isinstance(card, Mapping) else None,
            # —— ⑨-C 归因的两个分层键(缺一就没法分层,⛔ 不许省)——
            "pack_version": fp.get("pack_version"),
            "verification_ruleset_version": fp.get("verification_ruleset_version"),
            "charter_version": fp.get("charter_version"),
            "engine_api_version": fp.get("engine_api_version"),
            "day_notes": list(day.notes),
        },
    }
    items = (
        ("auction_vs_script", lambda: judge_auction_vs_script(codes, card, day)),
        ("open_direction", lambda: judge_open_direction(codes, day)),
        ("mfe_mae", lambda: judge_mfe_mae(codes, day)),
        ("member_alignment", lambda: judge_member_alignment(codes, day)),
        ("leader_pull", lambda: judge_leader_pull(codes, card, day)),
        ("buyability", lambda: judge_buyability(codes, card, day)),
        ("verification_timing",
         lambda: judge_verification_timing(ref.basket_id, day.review_date, db_path=db_path)),
        ("close_rs", lambda: judge_close_rs(codes, day)),
        ("tier_vs_outcome",
         lambda: judge_tier_vs_outcome(ref, card, day, tier_row=tier_row, day_rank=day_rank)),
    )
    for key, fn in items:
        try:
            mech[key] = fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[basket_review] 机械判 %s 失败(basket_id=%s),该项标 error",
                           key, ref.basket_id, exc_info=True)
            mech[key] = {"available": False, "unavailable_reason": f"该项计算失败:{type(exc).__name__}",
                         "error": f"{type(exc).__name__}: {exc}"}
    return mech


MECH_ITEM_KEYS: Tuple[str, ...] = (
    "auction_vs_script", "open_direction", "mfe_mae", "member_alignment", "leader_pull",
    "buyability", "verification_timing", "close_rs", "tier_vs_outcome",
)


# ══════════════════════════════════════════════════════════════════════════
# ⑨-B LLM 解释(TASK_REVIEW → 推理 Agent;预算走**复盘账**)
# ══════════════════════════════════════════════════════════════════════════

REVIEW_SYSTEM_PROMPT = """你是「颈线」系统的盘后复盘员,读者是一位短线交易者。系统只做审计、不代客下单。

你现在拿到的是**昨天收盘后定下的一个股票篮子**,以及**今天收盘后算出来的一整套机械判定**。
你的任务只有一件:**用人话解释这份机械判定里,昨天那个判断哪里对了、哪里错了**。

信息边界(铁律,不可违反):
1. 只能依据下面提供的机械判定与数字。**本次没有联网检索工具**,不得补充资料之外的新闻、
公告、传闻,也不得编造任何数字。
2. 标注「算不出」「无存拍」「没有记录」的项,就照实当作未知说出来,**不要用推测填补**,
更不要把"没数据"讲成"表现平平"。
3. **不预测明天**。复盘就是复盘:说清楚今天发生了什么、与昨天的判断差在哪。不要给出
任何"接下来该怎么做"的操作建议、不要提目标价、不要说"可以继续持有/该走了"。
4. 单日结果有大量噪声。**不许由一天的结果宣布某个规则失效或某个档位判错**,最多说
"这一天上它没兑现",并指出机械判里哪几项是这么讲的。

风格:一段到三段连贯的中文叙述,像交易员收盘后自己复盘。禁止分点列表、禁止"技术面/
资金面/消息面"这类固定分栏模板。你的一切表述都是**参考,不是指令**。
"""

LLM_OK = "ok"
LLM_NO_PROVIDER = "no_provider"
LLM_BUDGET_EXHAUSTED = "budget_exhausted"
LLM_CALL_FAILED = "call_failed"
LLM_DROPPED = "dropped_by_degrade_order"
LLM_PARSE_EMPTY = "empty_narrative"
LLM_DISABLED = "llm_disabled"


def build_review_context(review: "BasketReview", card: Optional[Mapping[str, Any]],
                         day: DayMarket) -> str:
    """喂给 LLM 的上下文(纯文本,同 `basket_card.build_card_context` 的理由:降低模型
    把它当输出模板抄回来的概率)。**第一行是日期锚**(`prompt_context` 唯一实现,
    §铁律 —— 没有它模型没有"现在"的概念)。"""
    m = review.mech
    lines: List[str] = [date_anchor_line(day.review_date), ""]
    meta = m.get("meta") or {}
    lines.append(f"复盘对象:{review.name}(basket_key {review.basket_key},T{review.tier} 档)")
    lines.append(f"D0(定档日)= {meta.get('d0')},复盘日(D+1)= {meta.get('review_date')}")
    if isinstance(card, Mapping):
        lines.append(f"D0 的共同驱动({card.get('driver_kind') or '未知'}):{card.get('driver') or '(缺)'}")
        if card.get("why_now"):
            lines.append(f"D0 的「为什么是现在」:{card.get('why_now')}")
    lines.append(f"成员({meta.get('member_count')} 只):{'、'.join(meta.get('members') or [])}")
    lines.append("")

    def pct(x, nd=2):
        return "算不出" if x is None else f"{float(x) * 100:+.{nd}f}%"

    a = m.get("auction_vs_script") or {}
    lines.append(f"① 竞价 vs 剧本:竞价/开盘中位 {pct(a.get('gap_median'))},"
                 f"落在卡上「{a.get('branch') or '判不了'}」分支;"
                 f"{'卡上写了这条分支的剧本' if a.get('script_present') else '卡上这条分支没有剧本文字'}"
                 f"(取值来源 {a.get('source')})")
    if a.get("script_text"):
        lines.append(f"   D0 写的这条剧本原文:{a['script_text']}")

    o = m.get("open_direction") or {}
    lines.append(f"② 开盘首方向:跳空中位 {pct(o.get('gap_median'))}({o.get('gap_dir') or '?'}),"
                 f"日内(收/开)中位 {pct(o.get('intraday_median'))}({o.get('intraday_dir') or '?'});"
                 f"{'两者同向' if o.get('aligned') else '两者不同向' if o.get('aligned') is not None else '方向判不全'}")

    f3 = m.get("mfe_mae") or {}
    lines.append(f"③ 分时 MFE/MAE(相对昨收):最大有利 {pct(f3.get('mfe_median'))},"
                 f"最大不利 {pct(f3.get('mae_median'))};数据来源 {f3.get('mfe_source')}"
                 f"(存拍台账 {f3.get('capture_status')},covered {f3.get('capture_covered_minutes')}/"
                 f"{f3.get('capture_expected_minutes')} 分钟)")
    if f3.get("note"):
        lines.append(f"   ⚠ {f3['note']}")

    al = m.get("member_alignment") or {}
    align_txt = "算不出" if al.get("alignment") is None else f"{float(al['alignment']):.0%}"
    lines.append(f"④ 成员同向率:{al.get('observed')}/{al.get('member_count')} 只有行情,"
                 f"涨 {al.get('up')} / 跌 {al.get('down')} / 平 {al.get('flat')},"
                 f"同向率 {align_txt}"
                 f"(占多数的方向:{al.get('dominant_direction') or '?'})")

    lp = m.get("leader_pull") or {}
    lines.append(f"⑤ 龙头带动:D0 认定的龙头 {'、'.join(lp.get('leaders') or []) or '(卡上认不出)'};"
                 f"龙头 {pct(lp.get('leader_ret_median'))} vs 其余成员 {pct(lp.get('others_ret_median'))},"
                 f"{'龙头带住了' if lp.get('led') else '龙头没带住' if lp.get('led') is not None else '判不了'}")

    b = m.get("buyability") or {}
    lines.append(f"⑥ 可买性:{b.get('buyable')}/{b.get('member_count')} 只今天买得进"
                 f"(一字 {b.get('one_word')} 只、收在涨停 {b.get('limit_up')} 只、无行情 {b.get('no_bar')} 只)")

    v = m.get("verification_timing") or {}
    lines.append(f"⑦ 验证与证伪:当前判定「{v.get('state')}」({v.get('state_label')}),"
                 f"当日共 {v.get('rows')} 条记录;"
                 f"首次判为已验证 {v.get('first_verified_at') or '未发生'},"
                 f"首次判为证伪 {v.get('first_falsified_at') or '未发生'}")

    rs = m.get("close_rs") or {}
    lines.append(f"⑧ 收盘 RS:大盘({rs.get('index_code')}){pct(rs.get('index_ret'))},"
                 f"篮子超额中位 {pct(rs.get('excess_median'))},跑赢大盘的成员 {rs.get('outperformers')} 只")

    tv = m.get("tier_vs_outcome") or {}
    dims = (tv.get("tier_breakdown") or {}).get("dims") or {}
    dim_txt = "、".join(f"{k} {float(x):.2f}" for k, x in sorted(dims.items())) if dims else "(无)"
    lines.append(f"⑨ D0 判断 vs 今日结果:T{tv.get('tier')} 档内第 {tv.get('rank_in_tier')} 位"
                 f"(机械分 {tv.get('mech_score')});五维:{dim_txt};"
                 f"今日篮子收益中位 {pct(tv.get('basket_ret_median'))}")
    if tv.get("rank_by_outcome") is not None:
        lines.append(f"   当日 {tv.get('day_baskets')} 个篮子里,D0 序第 {tv.get('rank_by_tier')} 位、"
                     f"今日结果第 {tv.get('rank_by_outcome')} 位(名次差 {tv.get('rank_gap'):+d};"
                     f"单日名次差是噪声,不构成结论)")

    lines.append("")
    if review.depth == DEPTH_BRIEF:
        lines.append("请写**两三句话**的简评:今天这个篮子最值得记的一点是什么。")
    else:
        lines.append("请按上面的风格要求,写一段到三段的复盘叙述。")
    return "\n".join(lines)


def run_review_llm(
    context_text: str,
    *,
    provider: Optional[LLMProvider],
    ledger: BudgetLedger,
    transport: Optional[Any] = None,
    system_prompt: str = REVIEW_SYSTEM_PROMPT,
) -> Tuple[Optional[str], str]:
    """复盘解释一次调用,返回 `(叙述 or None, 段状态)`。

    · 路由走 **`TASK_REVIEW`**(② 的任务常量单一源;provider 由调用方解析后注入)。
    · **不联网**:复盘吃的是机械判定,不需要新证据,也不该再花一次检索预算。
    · 预算走**复盘账** `LEDGER_REVIEW`(② 的三本账之一,与检索/推理互不透支)。
    · **本链路没有结论标签**,不复用 `judge._parse_verdict`;万一模型硬塞了一个 JSON
      围栏块,`split_narrative_and_reference_json` 会把它剥掉只留叙述(v1.5.1 标签
      劫持案的同一条防线:先剥再用)。
    """
    if provider is None:
        return None, LLM_NO_PROVIDER
    if ledger.exhausted(LEDGER_REVIEW):
        return None, LLM_BUDGET_EXHAUSTED

    messages = [ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=context_text)]
    started = time.monotonic()
    try:
        result = provider.chat(messages, enable_search=False, transport=transport)
    except Exception as exc:  # noqa: BLE001
        ledger.spend(LEDGER_REVIEW, time.monotonic() - started)
        logger.warning("[basket_review] 复盘解释调用抛异常,本篮只留机械判", exc_info=True)
        return None, f"{LLM_CALL_FAILED}:{type(exc).__name__}"
    ledger.spend(LEDGER_REVIEW, time.monotonic() - started)

    if not getattr(result, "ok", False):
        return None, f"{LLM_CALL_FAILED}:{getattr(result, 'reason', '')}"
    narrative, _payload = split_narrative_and_reference_json(result.content or "")
    narrative = (narrative or "").strip()
    return (narrative or None), (LLM_OK if narrative else LLM_PARSE_EMPTY)


# ══════════════════════════════════════════════════════════════════════════
# 编排:一天全部篮子
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ReviewRunResult:
    review_date: date
    d0: Optional[date] = None
    baskets: int = 0
    reviews: List[BasketReview] = field(default_factory=list)
    rows_inserted: int = 0
    rows_existing: int = 0
    llm_called: int = 0
    llm_dropped: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # V2.2-④-A:选股时钟结案接线的产出(**结了就是结了**,重跑 = `clock_existing` 增长)。
    clock_closed: int = 0
    clock_existing: int = 0


def depth_for_tier(tier: int) -> str:
    """**恒 `full`**(V2.1-② T3 全链退役后只剩 T1/T2,两档都是每日必复盘)。

    ⚠ 形参保留是刻意的:签名不变,调用方(`review_day`)不必改;而且**历史 D0 被
    重放**时(那天可能有 tier=3 的篮子)也给 `full` —— 新规下 `brief` 已无写入方,
    给它更完整的复盘而不是更少,方向安全。历史库里已冻住的 `depth='brief'` 行照常
    读回(`DEPTH_BRIEF` 常量为此保留)。
    """
    return DEPTH_FULL


def plan_llm_drops(reviews: Sequence[BasketReview], ledger: BudgetLedger) -> List[str]:
    """按 ② 定死的降级次序,决定这一批要丢哪些 LLM 段。

    **次序恒为** `budget.DEGRADE_ORDER`,V2.1-② 起只剩一项 = (T2 复盘细节,)。
    预算没耗尽 → 一个都不丢;耗尽了丢 T2 细节。**T1 永远不在可丢清单里** —— 那不是
    本函数"心软",是 `DEGRADE_ORDER` 里根本没有它这一项。
    """
    if not ledger.exhausted(LEDGER_REVIEW):
        return []
    dropped: List[str] = []
    for item in DEGRADE_ORDER:
        dropped.append(item)
    return dropped


def _dropped_reason(review: BasketReview, dropped: Sequence[str]) -> Optional[str]:
    # V2.1-②:T3 简评分支随 `DROP_T3_BRIEF` 一并删除(可丢清单只剩 T2 细节)。
    if review.tier == 2 and DROP_T2_REVIEW_DETAIL in dropped:
        return f"{LLM_DROPPED}:{DROP_T2_REVIEW_DETAIL}"
    return None


def review_day(
    review_date: date,
    *,
    d0: Optional[date] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    use_llm: bool = False,
    provider: Optional[LLMProvider] = None,
    ledger: Optional[BudgetLedger] = None,
    transport: Optional[Any] = None,
    persist: bool = True,
) -> ReviewRunResult:
    """一天的盘后复盘编排(D+1 收盘后跑)。**永不抛异常**:任何一段炸了都只记 note。

    次序刻意如此:**先把全部篮子的机械判算完**(拿到当日横截面才谈得上名次),
    **再**统一跑 LLM(预算与降级次序在这一步生效),**最后**一次性落库 ——
    `basket_review_daily` 是「每日一行幂等」,一行写下去就冻住了,所以**必须等
    LLM 段有结论(成功 / 缺席 / 被丢)之后再写**,不然第一次降级跑会把"没有解释"
    永久钉在库里(⛔ 本模块刻意不提供任何 UPDATE 路径)。
    """
    from neckline.review import basket_review_store as store

    res = ReviewRunResult(review_date=review_date)
    ledger = ledger or BudgetLedger()
    try:
        res.d0 = d0 or prev_trading_day(review_date)
        refs = load_baskets_for_date(res.d0, db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[basket_review] 读取 D0 篮子失败", exc_info=True)
        res.notes.append(f"读取 D0 篮子失败:{type(exc).__name__}: {exc}")
        return res
    res.baskets = len(refs)
    if not refs:
        res.notes.append(f"D0={res.d0} 当天没有已冻结的篮子,今日无可复盘对象")
        return res

    all_codes = sorted({c for r in refs for c in r.member_codes})
    day = build_day_market(review_date, all_codes, d0=res.d0,
                           db_path=db_path, parquet_dir=parquet_dir)
    cards = {r.basket_id: (load_basket_card(r.basket_id, db_path=db_path) or {}).get("card")
             for r in refs}
    tier_rows = _load_tier_rows(res.d0, [r.basket_id for r in refs], db_path=db_path)

    entries = [
        (r.basket_key, r.tier,
         (cards.get(r.basket_id) or {}).get("rank_in_tier") if isinstance(cards.get(r.basket_id), Mapping)
         else (tier_rows.get(r.basket_id) or {}).get("rank_in_tier"),
         _median([x for x in (member_return(c, day) for c in r.member_codes) if x is not None]))
        for r in refs
    ]
    ranks = day_rank_table(entries)

    for ref in refs:
        card = cards.get(ref.basket_id)
        mech = build_mech(ref, card, day, db_path=db_path,
                          tier_row=tier_rows.get(ref.basket_id),
                          day_rank=ranks.get(ref.basket_key))
        res.reviews.append(BasketReview(
            basket_id=ref.basket_id, basket_key=ref.basket_key, name=ref.name, tier=ref.tier,
            review_date=review_date, d0=res.d0, depth=depth_for_tier(ref.tier), mech=mech,
        ))

    if use_llm:
        dropped = plan_llm_drops(res.reviews, ledger)
        res.llm_dropped = list(dropped)
        # T1 → T2:先把最该有解释的那几篮做掉,预算耗尽时后面的自然轮空,
        # 与 `DEGRADE_ORDER` 的语义方向一致(不是靠这个顺序代替次序判定)。
        for review in sorted(res.reviews, key=lambda x: (x.tier, x.basket_key)):
            skip = _dropped_reason(review, dropped)
            if skip:
                review.llm_skip_reason, review.degraded = skip, True
                continue
            try:
                text, status = run_review_llm(
                    build_review_context(review, cards.get(review.basket_id), day),
                    provider=provider, ledger=ledger, transport=transport,
                )
            except Exception as exc:  # noqa: BLE001
                text, status = None, f"{LLM_CALL_FAILED}:{type(exc).__name__}"
                logger.warning("[basket_review] 复盘解释编排异常(basket_id=%s)", review.basket_id,
                               exc_info=True)
            review.llm_text = text
            review.llm_skip_reason = None if status == LLM_OK else status
            review.degraded = status != LLM_OK
            if status == LLM_OK:
                res.llm_called += 1
    else:
        for review in res.reviews:
            review.llm_skip_reason = LLM_NO_PROVIDER if provider is None else LLM_DISABLED
            review.degraded = True

    if persist:
        try:
            stats = store.save_reviews(res.reviews, db_path=db_path)
            res.rows_inserted = stats["inserted"]
            res.rows_existing = stats["existing"]
            res.notes.extend(stats.get("conflicts") or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[basket_review] 复盘落库失败", exc_info=True)
            res.notes.append(f"复盘落库失败:{type(exc).__name__}: {exc}")

        # —— V2.2-④-A 选股时钟**结案**接线 ————————————————————————————————
        # 位置刻意在**复盘落库之后**:结案件的九项里有四项直接吃复盘的机械判,
        # 而结案是 `INSERT OR IGNORE`(结了就是结了)—— 先把复盘那一行写稳,
        # 再结案,免得把一份半成品永久冻住。
        # ⛔ **零新增 LLM 调用**:结案叙述并进上面那一次 `TASK_REVIEW`(plan 附
        # 「成本与超时算术」第 6 行),`close_day` 自己一次模型都不调。
        # 整段包保险丝:结案失败只记 note,⛔ 不许掀翻当日复盘(它已经落库了)。
        try:
            from neckline.review import selection_clock

            cres = selection_clock.close_day(
                review_date, d0=res.d0, refs=refs,
                cards=cards,
                review_mechs={r.basket_id: r.mech for r in res.reviews},
                bars=day.bars, db_path=db_path, parquet_dir=parquet_dir,
            )
            res.clock_closed, res.clock_existing = cres.inserted, cres.existing
            res.notes.extend(cres.notes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[basket_review] 选股时钟结案失败(不影响当日复盘)", exc_info=True)
            res.notes.append(f"选股时钟结案失败:{type(exc).__name__}: {exc}")
    res.notes.extend(day.notes)
    return res


def _load_tier_rows(d0: date, basket_ids: Sequence[int], *,
                    db_path: Optional[Path] = None) -> Dict[int, Dict[str, Any]]:
    """D0 的 `tier_history` 行(卡缺失时的定档留痕来源)。读失败 → 空 dict。"""
    import json

    from neckline.db import connection, init_schema

    if not basket_ids:
        return {}
    try:
        init_schema(db_path)
        with connection(db_path) as conn:
            rows = conn.execute(
                "SELECT basket_id, tier, mech_score, mech_breakdown_json, rank_in_tier, "
                "rank_mech, llm_rank_delta, llm_reason, pack_version FROM tier_history "
                "WHERE trade_date=? AND basket_id IN (%s)" % ",".join("?" * len(basket_ids)),
                (d0.strftime("%Y%m%d"), *[int(b) for b in basket_ids]),
            ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("[basket_review] tier_history 读取失败", exc_info=True)
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        try:
            breakdown = json.loads(r[3]) if r[3] else {}
        except (json.JSONDecodeError, TypeError):
            breakdown = {}
        out[int(r[0])] = {
            "tier": int(r[1]), "mech_score": r[2], "mech_breakdown": breakdown,
            "rank_in_tier": r[4], "rank_mech": r[5], "llm_rank_delta": r[6],
            "llm_reason": r[7], "pack_version": r[8],
        }
    return out


__all__ = [
    "MECH_SPEC_VERSION", "MECH_ITEM_KEYS", "REVIEW_TASK",
    "DEPTH_FULL", "DEPTH_BRIEF", "depth_for_tier",
    "MFE_SOURCE_INTRADAY", "MFE_SOURCE_EOD_APPROX",
    "AUCTION_STRONG_GAP", "AUCTION_WEAK_GAP", "FLAT_EPS", "EPS",
    "BUY_OK", "BUY_NO_BAR", "BUY_LIMIT_UP_CLOSE", "BUY_ONE_WORD",
    "DayMarket", "build_day_market", "member_return",
    "script_branch", "judge_auction_vs_script", "judge_open_direction", "judge_mfe_mae",
    "judge_member_alignment", "resolve_leaders", "judge_leader_pull", "judge_buyability",
    "judge_verification_timing", "judge_close_rs", "judge_tier_vs_outcome", "day_rank_table",
    "BasketReview", "build_mech",
    "REVIEW_SYSTEM_PROMPT", "build_review_context", "run_review_llm", "plan_llm_drops",
    "LLM_OK", "LLM_NO_PROVIDER", "LLM_BUDGET_EXHAUSTED", "LLM_CALL_FAILED", "LLM_DROPPED",
    "LLM_PARSE_EMPTY", "LLM_DISABLED",
    "ReviewRunResult", "review_day",
]
