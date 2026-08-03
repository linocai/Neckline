"""⑫-B 能力画像引擎(plan §五 V2-⑫-B,蓝图 6.3)。回答「什么真有效」——各类选择
的胜率 / 盈亏比 / 回撤(MFE·MAE)、是否跑赢同篮未选股票、哪些机会经常被错误忽略。

**判分/对照口径唯一源**:凡涉及"模拟收益"的比较(是否跑赢同篮未选股票、哪些
机会被忽略),一律走 `neckline.eval.metrics.score_tradable`(其判分内核是 ⑨-D
`neckline.eval.exit_sim.fill_and_score`,项目判分引擎唯一源),本模块**不写第二份
判分或对照实现**——同 ⑨-C2 的既有纪律,`neckline/profile/` 与 `neckline/eval/`
本就是同一条"回看审计"链路上的两段。

**只看已平仓仓位算胜率/盈亏比/回撤**:持仓中的仓位盈亏仍在变,提前算等于给一个
会变的数字贴"结论"标签——不诚实(同 ⑨-C `unfinished` 的诚实边界精神)。

**vs_peer_delta 的口径(不可与"用户真实成交收益"混用)**:同一来源篮子
(`entry_snapshots.basket_id`)内,比较"用户实际买的那只"与"同篮没买的其它成员"
在**同一套 `exit_sim` 规则**下的**模拟**收益——不是拿用户的真实成交收益(不同的
持有期/退出时点)去比同篮成员的模拟收益,那是两种方法论混着比,不诚实。样本
不足(< `MIN_SAMPLE_N` 组对照)→ 该字段如实 `None`,不因为主指标(胜率等)够格
就搭车给一个数。

**⛔ 初期不得反向影响客观 Tier**:本模块只读 `baskets`/`basket_members`/
`basket_cards`/`tier_history`(经 `eval.metrics.load_basket_panel`),零写入——
全局守门测试 `tests/test_positions_entry.py::
test_positions_side_never_writes_to_basket_tables` 扫描全仓 `neckline/*.py`,
本模块天然纳入其中。
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from neckline.db import connection
from neckline.eval import metrics as eval_metrics
from neckline.eval.exit_sim import notional_from_charter, score_kw_from_charter
from neckline.profile.common import (
    BuyContext,
    CONFIDENCE_LOW,
    DIM_ENTRY_STYLE,
    DIM_MISSED_ROLE,
    DIM_ROLE,
    DIM_TIER,
    DIM_THEME,
    MIN_SAMPLE_N,
    confidence_for,
    load_buy_contexts,
)

logger = logging.getLogger(__name__)

_EPS = 1e-9

_DIMENSIONS: Tuple[Tuple[str, Callable[[BuyContext], str]], ...] = (
    (DIM_ROLE, lambda c: c.role_value),
    (DIM_TIER, lambda c: c.tier_label),
    (DIM_THEME, lambda c: c.theme_value),
    (DIM_ENTRY_STYLE, lambda c: c.entry_style),
)


@dataclass(frozen=True)
class CapabilityRow:
    """对应 `profile_capability` 一行 + 一个**不落库**的 `verdict` 说明文本
    (DDL 无该列——"哪些偏好是优势/哪些是重复性错误"是由 `win_rate`/`vs_peer_delta`
    /`confidence` 派生的结论,同 `eval.metrics.Verdict` 的既定体例:结论本身是
    读时计算,不是一份需要单独持久化的事实,消费方可以拿持久化的四个数字随时
    重新推导同一句话)。"""

    dimension: str
    value: str
    sample_n: int
    win_rate: Optional[float]
    profit_factor: Optional[float]
    avg_mfe: Optional[float]
    avg_mae: Optional[float]
    vs_peer_delta: Optional[float]
    window_start: str
    window_end: str
    confidence: str
    verdict: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension, "value": self.value, "sampleN": self.sample_n,
            "winRate": self.win_rate, "profitFactor": self.profit_factor,
            "avgMfe": self.avg_mfe, "avgMae": self.avg_mae, "vsPeerDelta": self.vs_peer_delta,
            "windowStart": self.window_start, "windowEnd": self.window_end,
            "confidence": self.confidence, "verdict": self.verdict,
        }


def _finite(x: Optional[float]) -> Optional[float]:
    """同 `reconcile._finite`:`profit_factor` 无亏损样本时是 `inf`,标准 JSON 无
    该字面量,落 `None`(客户端展示"—"),不裸传 inf/nan。"""
    if x is None:
        return None
    if isinstance(x, float) and (math.isinf(x) or math.isnan(x)):
        return None
    return x


def _trade_stats(group: Sequence[BuyContext]) -> Tuple[Optional[float], Optional[float]]:
    """(胜率, 盈亏比)——口径同 `reconcile.compute_weekly_stats`(净盈亏含双边
    费用,盈利不冲抵亏损)。空组 → `(None, None)`。"""
    pnls = [c.net_pnl for c in group if c.net_pnl is not None]
    if not pnls:
        return None, None
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls)
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    if gross_loss > 0:
        profit_factor: Optional[float] = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else None
    return win_rate, profit_factor


def _mfe_mae(
    code: str, buy_date: str, sell_date: str, buy_price: float, *, parquet_dir: Optional[Path] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """整段持有期(`[buy_date, sell_date]`)的 EOD 近似 MFE/MAE:用区间内每日最高/
    最低价相对买入价的极值(同 ⑨-A `judge_mfe_mae` 的 `eod_approx` 精神,只是把
    "单日近似"延伸到"整段持有期近似"——两者都是"幅度可信、时刻未知"的同一类
    近似,本函数不追踪触及时刻)。用**原始价**(不前复权)——买入价本身就是
    原始成交价,与 `daily.high/low` 同口径;若持有期内恰好跨越除权除息日会有
    已知的失真(同 ⑧ 除权锚失效检测是同一类局限,本函数不处理,数据缺失时直接
    返回 `None`,不装作精确)。"""
    if buy_price <= 0:
        return None, None
    from neckline.data.market_data import get_stock_history

    hist = get_stock_history(code, buy_date, sell_date, table="daily", parquet_dir=parquet_dir)
    if hist.is_empty():
        return None, None
    highs = [h for h in hist["high"].to_list() if h is not None]
    lows = [l for l in hist["low"].to_list() if l is not None]
    if not highs or not lows:
        return None, None
    mfe = max(h / buy_price - 1.0 for h in highs)
    mae = min(l / buy_price - 1.0 for l in lows)
    return round(mfe, 6), round(mae, 6)


# ══════════════════════════════════════════════════════════════════════════
# vs_peer_delta:同一批篮子只跑一次 exit_sim,供「跑赢同篮未选」与「错失机会」共用
# ══════════════════════════════════════════════════════════════════════════

def _tradable_lookup(
    closed: Sequence[BuyContext], *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    price_maps: Optional[Any] = None,
) -> Tuple[Dict[Tuple[int, str], Dict[str, Any]], List[Dict[str, Any]]]:
    """跑一次 `exit_sim`(唯一判分源,经 `eval.metrics.score_tradable`)覆盖用户
    实际触碰过的篮子,返回 `(basket_id, ts_code) -> per_member 行` 查找表 + 完整
    `per_member` 列表(「跑赢同篮未选」与「错失机会」复用同一批数据,不重算第二遍)。

    触碰不到章程 / 篮子数据 / 判分失败 → 空表(调用方据此把 `vs_peer_delta` 如实
    标 `None`,**不是** 0——"算不出"与"算出来是 0"是两回事,同项目铁律)。

    ⚠ **窗口必须按篮子自己的 `trade_date`(D0)算,不能拿 `BuyContext.buy_date`
    (D+1,用户实际下单那天)顶替**——两者差至少一个交易日,`load_basket_panel`
    过滤的是 `baskets.trade_date`,拿 D+1 当窗口边界会让 D0 恰好落在窗口外、
    查出空面板(施工期真实踩过:worktree 里 `capability_universe` 夹具用 D0/D+1
    两个不同日期时,原实现在这里悄悄把"跑不出" 静默吞成"篮子没数据")。"""
    basket_ids = sorted({c.basket_id for c in closed if c.basket_id is not None})
    if not basket_ids:
        return {}, []
    marks = ",".join("?" * len(basket_ids))
    with connection(db_path) as conn:
        d0_rows = conn.execute(
            f"SELECT trade_date FROM baskets WHERE id IN ({marks})", basket_ids,
        ).fetchall()
    d0s = [str(r[0]) for r in d0_rows]
    if not d0s:
        return {}, []
    lo, hi = min(d0s), max(d0s)
    try:
        panel = eval_metrics.load_basket_panel(lo, hi, db_path=db_path)
    except Exception:  # noqa: BLE001 —— 面板装配失败不该掀翻整份能力画像
        logger.warning("[profile.capability] load_basket_panel 失败,vs_peer_delta 本期全体 None", exc_info=True)
        return {}, []
    touched_ids = set(basket_ids)
    touched = [r for r in panel if r.basket_id in touched_ids]
    if not touched:
        return {}, []
    try:
        kw = score_kw_from_charter(db_path=db_path)
        notional = notional_from_charter(db_path=db_path)
    except ValueError as exc:
        logger.warning("[profile.capability] 无现役章程,vs_peer_delta 本期全体 None:%s", exc)
        return {}, []
    try:
        tr = eval_metrics.score_tradable(
            touched, price_maps=price_maps, score_kw=kw, notional=notional,
            db_path=db_path, parquet_dir=parquet_dir,
        )
    except Exception:  # noqa: BLE001
        logger.warning("[profile.capability] score_tradable 失败,vs_peer_delta 本期全体 None", exc_info=True)
        return {}, []
    lookup = {
        (int(row["basket_id"]), str(row["ts_code"])): row
        for row in tr.per_member if "ts_code" in row and "basket_id" in row
    }
    return lookup, tr.per_member


def _filled_ret(row: Optional[Dict[str, Any]]) -> Optional[float]:
    if row is None or not row.get("filled") or row.get("fill_code") != "ok":
        return None
    return float(row["ret"])


def _vs_peer_for_group(
    group: Sequence[BuyContext], lookup: Dict[Tuple[int, str], Dict[str, Any]],
) -> Optional[float]:
    """组内每个有来源篮子的买入,与该篮**其它**成员的模拟收益比——挨个买入求
    "自己 vs 那一篮的其它成员均值"的差,再对组内多笔取平均(**不是**把全组的
    picked 均值减全组的 peer 均值合并成一个池子——那样同一只被多次买入的成员会
    被反复计入 peer 池,权重失真;逐笔配对再平均,每笔买入的"自己 vs 当时的
    同伴"权重相等)。样本(配对数)不足 `MIN_SAMPLE_N` → `None`。"""
    deltas: List[float] = []
    for c in group:
        if c.basket_id is None:
            continue
        own_ret = _filled_ret(lookup.get((c.basket_id, c.ts_code)))
        if own_ret is None:
            continue
        peer_rets = [
            _filled_ret(row) for (bid, code), row in lookup.items()
            if bid == c.basket_id and code != c.ts_code
        ]
        peer_rets = [r for r in peer_rets if r is not None]
        if not peer_rets:
            continue
        deltas.append(own_ret - (sum(peer_rets) / len(peer_rets)))
    if len(deltas) < MIN_SAMPLE_N:
        return None
    return round(sum(deltas) / len(deltas), 6)


def _missed_role_opportunities(
    per_member_all: Sequence[Dict[str, Any]],
    closed: Sequence[BuyContext],
    window_start: str,
    window_end: str,
) -> List[CapabilityRow]:
    """⑫-B「哪些机会经常被错误忽略」:同一批 `exit_sim` 判分里,**用户没买**的
    成员按角色分组,看这些"被跳过的角色"模拟表现如何——复用 `_tradable_lookup`
    已经算出的同一批 `per_member` 数据,不重算第二遍。

    `dimension=DIM_MISSED_ROLE` 与 `DIM_ROLE` 分开命名(两张不同性质的行:一个
    描述"用户选了什么",一个描述"用户没选的那些人表现如何")。`vs_peer_delta`
    对这类行**恒为 `None`**——它是"相对未选成员的差值",而这里的行本身就是在
    描述未选成员,没有"相对谁"的对象,强行塞一个数字会让人误以为是某种差值
    (`win_rate` 与 `verdict` 里的平均模拟收益已经把结论说清楚,不需要靠字段名
    模糊的数字撑门面)。"""
    if not per_member_all:
        return []
    picked = {(c.basket_id, c.ts_code) for c in closed if c.basket_id is not None}
    buckets: Dict[str, List[float]] = defaultdict(list)
    for row in per_member_all:
        key = (row.get("basket_id"), row.get("ts_code"))
        if key in picked:
            continue
        ret = _filled_ret(row)
        if ret is None:
            continue
        role = str(row.get("role") or "unknown")
        buckets[role].append(ret)

    rows: List[CapabilityRow] = []
    for role, rets in sorted(buckets.items()):
        n = len(rets)
        confidence = confidence_for(n)
        win_rate = sum(1 for r in rets if r > 0) / n
        avg_ret = sum(rets) / n
        if confidence == CONFIDENCE_LOW:
            verdict = f"样本不足(N={n}),暂不给结论"
        elif avg_ret > _EPS:
            verdict = f"经常被跳过的「{role}」成员平均模拟收益 {avg_ret:+.1%},可能是被错误忽略的机会"
        else:
            verdict = f"「{role}」成员未选不算错过(平均模拟收益 {avg_ret:+.1%})"
        rows.append(CapabilityRow(
            dimension=DIM_MISSED_ROLE, value=role, sample_n=n,
            win_rate=round(win_rate, 4), profit_factor=None, avg_mfe=None, avg_mae=None,
            vs_peer_delta=None, window_start=window_start, window_end=window_end,
            confidence=confidence, verdict=verdict,
        ))
    return rows


def _verdict_for(win_rate: Optional[float], vs_peer_delta: Optional[float], n: int, confidence: str) -> str:
    if confidence == CONFIDENCE_LOW:
        return f"样本不足(N={n}),暂不给结论"
    parts: List[str] = []
    if win_rate is not None:
        parts.append(f"胜率 {win_rate:.0%}")
    if vs_peer_delta is not None:
        verb = "跑赢" if vs_peer_delta > _EPS else ("跑输" if vs_peer_delta < -_EPS else "持平")
        parts.append(f"{verb}同篮未选成员 {vs_peer_delta:+.1%}")
    if win_rate is not None and vs_peer_delta is not None:
        if win_rate >= 0.5 and vs_peer_delta > _EPS:
            parts.append("优势:该选择有正向证据支持")
        elif win_rate < 0.5 and vs_peer_delta < -_EPS:
            parts.append("重复性错误:该选择证据偏负面")
        else:
            parts.append("证据不一致,暂无法归为优势或错误")
    return ";".join(parts) if parts else f"N={n},数据不足以形成判断"


def compute_capability(
    window_start: str, window_end: str, *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    price_maps: Optional[Any] = None,
) -> List[CapabilityRow]:
    """`[window_start, window_end]`(按买入日,'YYYYMMDD')区间内的能力画像。
    `price_maps`:可选注入(测试用,同 `eval.metrics.score_tradable` 的既定依赖
    注入姿势),生产侧留空走真实 parquet。

    区间内没有**已平仓**买入 → 空列表(未平仓的仓位不参与——见模块头「只看
    已平仓」的诚实边界)。"""
    contexts = load_buy_contexts(window_start, window_end, db_path=db_path)
    closed = [c for c in contexts if c.status == "closed" and c.sell_price is not None]
    if not closed:
        return []

    lookup, per_member_all = _tradable_lookup(
        closed, db_path=db_path, parquet_dir=parquet_dir, price_maps=price_maps,
    )
    ws, we = min(c.buy_date for c in closed), max(c.buy_date for c in closed)

    rows: List[CapabilityRow] = []
    for dim, key in _DIMENSIONS:
        buckets: Dict[str, List[BuyContext]] = defaultdict(list)
        for c in closed:
            buckets[key(c)].append(c)
        for value, group in sorted(buckets.items()):
            n = len(group)
            win_rate, profit_factor = _trade_stats(group)
            mfes: List[float] = []
            maes: List[float] = []
            for c in group:
                mfe, mae = _mfe_mae(
                    c.ts_code, c.buy_date, c.sell_date or c.buy_date, c.buy_price,
                    parquet_dir=parquet_dir,
                )
                if mfe is not None:
                    mfes.append(mfe)
                if mae is not None:
                    maes.append(mae)
            vs_peer = _vs_peer_for_group(group, lookup)
            confidence = confidence_for(n)
            rows.append(CapabilityRow(
                dimension=dim, value=value, sample_n=n,
                win_rate=(round(win_rate, 4) if win_rate is not None else None),
                profit_factor=_finite(profit_factor),
                avg_mfe=(round(sum(mfes) / len(mfes), 6) if mfes else None),
                avg_mae=(round(sum(maes) / len(maes), 6) if maes else None),
                vs_peer_delta=vs_peer,
                window_start=ws, window_end=we, confidence=confidence,
                verdict=_verdict_for(win_rate, vs_peer, n, confidence),
            ))

    rows += _missed_role_opportunities(per_member_all, closed, ws, we)
    return rows


__all__ = ["CapabilityRow", "compute_capability"]
