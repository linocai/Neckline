"""⑨-C 评价引擎(plan §五 V2-⑨-C)。把「选股这套机器到底有没有用」拆成一组**可
复现、可分层**的数字,喂给策略线下一轮迭代(§12.5:每个包自动攒出自己的成绩单)。

**七组指标**::

    1. tier_monotonicity   Tier 单调性(T1 > T2 > T3 成不成立)
    2. resonance_rate      篮子共振率(成员同向且过 ⑦-b 那道门槛)
    3. verification_rate   验证率(D+1 四态分布,`not_evaluated` 单独计,不混进分母)
    4. leader_vs_members   龙头 vs 同篮其他成员
    5. tradable_return     **可交易收益**(走 ⑨-D `exit_sim` 唯一源;排除买不进的)
    6. selected_vs_not     用户已选 vs 未选对照(V2 初期无数据 → 指标位建好、**空值如实**)
    7. contribution        驱动类型 / 角色 / 市场环境三个切面的贡献

**全部按 `pack_version` × `verification_ruleset_version` 分层**(⑦-b 原文:没有它
两套条件集的成绩会混成一锅;§12.5:换包要看得出「换了有没有变好」)。preseed 灌入的
篮子 `pack_version='preseed'` 天然自成一层 —— 人工配的成绩不许算到包头上。

**三条纪律**

    · **判分只有一份**:可交易收益一律走 `neckline/eval/exit_sim.py`,本模块
      **不写任何退出 / 成交逻辑**(⑨-C2 验收第 ② 条,有 grep 守门单测)。
    · **样本量不足只报样本数、不报结论**(⑨-C2 诚实边界):所有"谁更好"的判断都
      经 `Verdict`,`n_days < MIN_CONCLUSION_DAYS` 时它只会给出样本数。
    · **前向窗口没走完的不混进已完成样本**,且分两种如实计数:触发了退出却没有
      T+1 可撮合(`_sim_one` 记 `reason="end"`)→ `unfinished`;整段日历都没解出
      退出(取不到价 / 窗口太短)→ `unresolved`。两者都**不进收益均值**,
      ⛔ 装作走完了是最不诚实的一种四舍五入。

**红线**:本模块产出**只进周报与策略线迭代输入**,⛔ 不进任何在线判据。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.calendar import next_trading_day, trading_days_between
from neckline.db import connection, init_schema
from neckline.eval.exit_sim import (
    PriceMaps, build_price_maps, fill_and_score, notional_from_charter, score_kw_from_charter,
)
from neckline.selection import verification_rules as vr

logger = logging.getLogger(__name__)

#: 少于这么多个交易日的样本上,**只报样本数、不报结论**(⑨-C2 诚实边界)。
#: ⚠ 零审计背书的工程默认 —— 它不是统计显著性检验,只是一道"别在 3 天上宣布胜负"
#: 的粗闸。真要做显著性,得等样本够了由策略线定方法(§七 已挂账)。
MIN_CONCLUSION_DAYS = 10

#: 前向窗口:判分需要 D+1 起 `hard_cap + 1` 个交易日才可能走完。取章程的 `hard_cap`
#: 再留 1 天撮合位;**不写死数字**,由 `score_kw_from_charter()` 供给。
FORWARD_SLACK_DAYS = 1

UNSET = "(未登记)"      # 分层键缺失时的显式占位(⛔ 不许把缺失并进某个真实版本里)


# ══════════════════════════════════════════════════════════════════════════
# 面板装配(读侧:一次把区间内的篮子 + 定档 + 卡 + 验证 + 复盘拉齐)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class BasketRecord:
    """一个篮子在一次评价里的全部素材(**只读**)。"""

    basket_id: int
    d0: str                       # YYYYMMDD
    basket_key: str
    name: str
    tier: int
    driver_kind: str
    pack_version: str
    charter_version: Optional[str] = None
    evidence_status: Optional[str] = None
    via: str = "auto"
    members: Tuple[str, ...] = ()
    roles: Dict[str, str] = field(default_factory=dict)        # ts_code -> role_mech or role_llm
    mech_score: Optional[float] = None
    rank_in_tier: Optional[int] = None
    card: Optional[Dict[str, Any]] = None
    ruleset_version: Optional[str] = None
    review_date: Optional[str] = None
    review_mech: Optional[Dict[str, Any]] = None
    review_degraded: Optional[int] = None
    verification_state: Optional[str] = None
    verification_source: Optional[str] = None
    selected: bool = False        # 用户在 D0/D+1 选过它(`user_actions.kind='select'`)

    @property
    def stratum(self) -> Tuple[str, str]:
        """分层键 =(选股包版本,验证条件集版本)。缺失显式占位,不并层。"""
        return (self.pack_version or UNSET, self.ruleset_version or UNSET)

    def mech_item(self, key: str) -> Dict[str, Any]:
        item = (self.review_mech or {}).get(key)
        return item if isinstance(item, Mapping) else {}

    @property
    def outcome(self) -> Optional[float]:
        """D+1 篮子结果 = 成员当日收益中位数(来自复盘的 ⑨ 项,不重算)。"""
        v = self.mech_item("tier_vs_outcome").get("basket_ret_median")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def max_chase_of(self, code: str) -> Optional[float]:
        for m in ((self.card or {}).get("members") or []):
            if isinstance(m, Mapping) and m.get("ts_code") == code:
                v = m.get("max_chase")
                return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        return None


def _d(x: Any) -> str:
    return x if isinstance(x, str) else x.strftime("%Y%m%d")


def load_basket_panel(
    date_from: Any,
    date_to: Any,
    *,
    db_path: Optional[Path] = None,
) -> List[BasketRecord]:
    """把 [date_from, date_to] 区间内(按 **D0**)的篮子素材拉齐。

    读五张表:`baskets` / `basket_members` / `tier_history` / `basket_cards`
    (取 `version=1` 的 D0 原判)/ `basket_review_daily` + `basket_verification`,
    外加 `user_actions` 的 `select` 行。**全部只读**,一个字都不写。

    排序 `(d0, basket_key)` —— 确定性,重跑结果逐位可比。
    """
    lo, hi = _d(date_from), _d(date_to)
    init_schema(db_path)
    out: List[BasketRecord] = []
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, trade_date, basket_key, name, tier, driver_kind, pack_version, "
            "charter_version, evidence_status, via FROM baskets "
            "WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date, basket_key",
            (lo, hi),
        ).fetchall()
        if not rows:
            return out
        by_id: Dict[int, BasketRecord] = {}
        for r in rows:
            rec = BasketRecord(
                basket_id=int(r[0]), d0=str(r[1]), basket_key=str(r[2]), name=str(r[3]),
                tier=int(r[4]), driver_kind=str(r[5]), pack_version=str(r[6]),
                charter_version=r[7], evidence_status=r[8], via=str(r[9] or "auto"),
            )
            by_id[rec.basket_id] = rec
            out.append(rec)
        ids = list(by_id)
        marks = ",".join("?" * len(ids))

        for bid, code, role_mech, role_llm in conn.execute(
            f"SELECT basket_id, ts_code, role_mech, role_llm FROM basket_members "
            f"WHERE basket_id IN ({marks}) ORDER BY basket_id, ts_code", ids
        ).fetchall():
            rec = by_id[int(bid)]
            rec.members = rec.members + (str(code),)
            rec.roles[str(code)] = str(role_mech or role_llm or "unknown")

        for bid, score, rit in conn.execute(
            f"SELECT basket_id, mech_score, rank_in_tier FROM tier_history "
            f"WHERE basket_id IN ({marks})", ids
        ).fetchall():
            rec = by_id[int(bid)]
            rec.mech_score = float(score) if score is not None else None
            rec.rank_in_tier = int(rit) if rit is not None else None

        for bid, card_json in conn.execute(
            f"SELECT basket_id, card_json FROM basket_cards "
            f"WHERE basket_id IN ({marks}) AND version=1", ids
        ).fetchall():
            rec = by_id[int(bid)]
            try:
                rec.card = json.loads(card_json)
            except (json.JSONDecodeError, TypeError):
                logger.warning("[eval] basket_id=%s 的 card_json 解不出,该篮不带卡参与评价", bid)
                continue
            fp = (rec.card or {}).get("fingerprint") or {}
            rec.ruleset_version = fp.get("verification_ruleset_version")

        for bid, rdate, mech_json, degraded in conn.execute(
            f"SELECT basket_id, review_date, mech_json, degraded FROM basket_review_daily "
            f"WHERE basket_id IN ({marks}) ORDER BY review_date", ids
        ).fetchall():
            rec = by_id[int(bid)]
            rec.review_date = str(rdate)
            rec.review_degraded = int(degraded or 0)
            try:
                rec.review_mech = json.loads(mech_json) if mech_json else None
            except (json.JSONDecodeError, TypeError):
                rec.review_mech = None

        for bid, state, source in conn.execute(
            f"SELECT basket_id, state, source FROM basket_verification "
            f"WHERE basket_id IN ({marks}) ORDER BY id", ids
        ).fetchall():
            rec = by_id[int(bid)]
            # EOD 行优先(三路读法的同一条口径:有收盘定论就以它为准)
            if source == "eod" or rec.verification_state is None:
                rec.verification_state, rec.verification_source = str(state), str(source)

        for (bid,) in conn.execute(
            f"SELECT DISTINCT basket_id FROM user_actions "
            f"WHERE kind='select' AND basket_id IN ({marks})", ids
        ).fetchall():
            if bid is not None and int(bid) in by_id:
                by_id[int(bid)].selected = True
    return out


# ══════════════════════════════════════════════════════════════════════════
# 结论闸:样本不足只报样本数
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Verdict:
    """一个"谁更好"的判断。`conclusive=False` 时 `text` 只说样本数,**不说优劣**。"""

    n_days: int
    n_samples: int
    conclusive: bool
    text: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"nDays": self.n_days, "nSamples": self.n_samples,
                "conclusive": self.conclusive, "text": self.text, "detail": dict(self.detail)}


def verdict(n_days: int, n_samples: int, conclusion: str,
            detail: Optional[Mapping[str, Any]] = None) -> Verdict:
    """样本够 → 给结论;不够 → **只给样本数**(⑨-C2 诚实边界,文案由单测锁死)。"""
    if n_days < MIN_CONCLUSION_DAYS:
        return Verdict(
            n_days=n_days, n_samples=n_samples, conclusive=False,
            text=f"N={n_days} 个交易日,尚不足以判断(需要至少 {MIN_CONCLUSION_DAYS} 个交易日)",
            detail=dict(detail or {}),
        )
    return Verdict(n_days=n_days, n_samples=n_samples, conclusive=True, text=conclusion,
                   detail=dict(detail or {}))


def _mean(xs: Sequence[float]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def _median(xs: Sequence[float]) -> Optional[float]:
    s = sorted(float(x) for x in xs if x is not None)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


# ══════════════════════════════════════════════════════════════════════════
# 1 Tier 单调性
# ══════════════════════════════════════════════════════════════════════════

def tier_monotonicity(records: Sequence[BasketRecord]) -> Dict[str, Any]:
    """T1 > T2 > T3 成不成立(用 D+1 篮子结果中位数比)。

    ⚠ **语义红线**:Tier 是**注意力优先级,不是收益预测**(§红线 5)。这条指标问的
    是「注意力有没有分配到更值得看的地方」,**不是**「T1 会不会涨得多」;单调性不
    成立也不等于机器坏了,它只是**送进策略线的一个观察**。文案不许写成"T1 应该涨最多"。
    """
    by_tier: Dict[int, List[float]] = {1: [], 2: [], 3: []}
    counts: Dict[int, int] = {1: 0, 2: 0, 3: 0}
    for r in records:
        counts[r.tier] = counts.get(r.tier, 0) + 1
        if r.outcome is not None:
            by_tier.setdefault(r.tier, []).append(r.outcome)
    med = {t: _median(v) for t, v in by_tier.items()}
    have = [t for t in (1, 2, 3) if med.get(t) is not None]
    holds = None
    if len(have) >= 2:
        seq = [med[t] for t in have]
        holds = all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
    return {
        "counts": counts,
        "observed": {t: len(by_tier.get(t) or []) for t in (1, 2, 3)},
        "median_outcome": med,
        "mean_outcome": {t: _mean(v) for t, v in by_tier.items()},
        "monotonic": holds,
        "note": "Tier = 注意力优先级,不是收益预测;单调性只是送进策略线的观察",
    }


# ══════════════════════════════════════════════════════════════════════════
# 2 篮子共振率
# ══════════════════════════════════════════════════════════════════════════

def resonance_rate(records: Sequence[BasketRecord]) -> Dict[str, Any]:
    """共振率 = 「上涨成员数 ≥ ⑦-b 的 `min_members_hit(observed)`」的篮子占比。

    **门槛直接复用 `verification_rules.min_members_hit`**,⛔ 不在这里发明第二个
    "几只算共振" —— 那正是 ⑦-b 立那个模块要防的事。
    """
    total = judged = resonant = 0
    for r in records:
        total += 1
        item = r.mech_item("member_alignment")
        obs = item.get("observed")
        up = item.get("up")
        if not isinstance(obs, int) or not isinstance(up, int) or obs <= 0:
            continue
        judged += 1
        if up >= vr.min_members_hit(obs):
            resonant += 1
    return {
        "baskets": total, "judged": judged, "resonant": resonant,
        "rate": (resonant / judged) if judged else None,
        "unjudged": total - judged,
        "threshold_rule": "verification_rules.min_members_hit(observed)（⑦-b 唯一源）",
    }


# ══════════════════════════════════════════════════════════════════════════
# 3 验证率
# ══════════════════════════════════════════════════════════════════════════

def verification_rate(records: Sequence[BasketRecord]) -> Dict[str, Any]:
    """D+1 四态分布 + 验证率。

    **`not_evaluated` 不进分母**:「今天这一拍没跑过」与「跑了判成 unclear」是两件
    事(⑧-C2 第 5 条),把前者算进分母会稀释验证率、把运维缺口伪装成策略失败。
    """
    dist = {s: 0 for s in vr.STATES}
    not_evaluated = 0
    for r in records:
        st = r.verification_state
        if st in dist:
            dist[st] += 1
        else:
            not_evaluated += 1
    judged = sum(dist.values())
    return {
        "baskets": len(records), "judged": judged, "not_evaluated": not_evaluated,
        "distribution": dist,
        "verified_rate": (dist[vr.STATE_VERIFIED] / judged) if judged else None,
        "falsified_rate": (dist[vr.STATE_FALSIFIED] / judged) if judged else None,
        "note": "not_evaluated 单独计,不进分母（运维缺口 ≠ 策略失败）",
    }


# ══════════════════════════════════════════════════════════════════════════
# 4 龙头 vs 同篮其他成员
# ══════════════════════════════════════════════════════════════════════════

def leader_vs_members(records: Sequence[BasketRecord]) -> Dict[str, Any]:
    spreads: List[float] = []
    led = judged = solo = 0
    for r in records:
        item = r.mech_item("leader_pull")
        if item.get("no_peer_group"):
            solo += 1
        s, l = item.get("spread"), item.get("led")
        if s is None or l is None:
            continue
        judged += 1
        spreads.append(float(s))
        led += 1 if l else 0
    return {
        "judged": judged, "no_peer_group": solo,
        "led": led, "led_rate": (led / judged) if judged else None,
        "spread_median": _median(spreads), "spread_mean": _mean(spreads),
    }


# ══════════════════════════════════════════════════════════════════════════
# 5 可交易收益(唯一判分源 = exit_sim)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class TradableResult:
    per_basket: Dict[int, Optional[float]] = field(default_factory=dict)
    per_member: List[Dict[str, Any]] = field(default_factory=list)
    filled: int = 0
    not_filled: int = 0
    unfinished: int = 0
    unresolved: int = 0
    fill_reasons: Dict[str, int] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        rets = [v for v in self.per_basket.values() if v is not None]
        return {
            "baskets_scored": len(rets), "baskets_total": len(self.per_basket),
            "member_fills": self.filled, "member_not_filled": self.not_filled,
            "member_unfinished": self.unfinished, "member_unresolved": self.unresolved,
            "fill_reasons": dict(sorted(self.fill_reasons.items())),
            "mean": _mean(rets), "median": _median(rets),
            "win_rate": (sum(1 for x in rets if x > 0) / len(rets)) if rets else None,
        }


def score_tradable(
    records: Sequence[BasketRecord],
    *,
    price_maps: Optional[PriceMaps] = None,
    score_kw: Optional[Dict[str, Any]] = None,
    notional: Optional[float] = None,
    respect_max_chase: bool = True,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> TradableResult:
    """**可交易收益**:每个成员 D0 选中 → D+1 竞价买入 → 按现役章程纪律退出。

    · 判分全部走 `exit_sim.fill_and_score`(⑨-D 唯一源),本函数只负责**喂参数**。
    · `respect_max_chase=True` 时用卡上冻结的 `max_chase` 作成交上限 —— 「开太高
      追不进」是真实约束,不该算成一笔亏损为 0 的交易。
    · **买不进的不进收益均值**(`filled=False` 单独计数);`reason="end"` 的
      **前向窗口没走完**,同样不进均值(`unfinished`)。
    """
    res = TradableResult()
    if not records:
        return res
    kw = dict(score_kw or score_kw_from_charter(db_path=db_path))
    cash = float(notional if notional is not None else notional_from_charter(db_path=db_path))
    if price_maps is None:
        codes = sorted({c for r in records for c in r.members})
        d0s = sorted({r.d0 for r in records})
        start = date(int(d0s[0][:4]), int(d0s[0][4:6]), int(d0s[0][6:]))
        last = date(int(d0s[-1][:4]), int(d0s[-1][4:6]), int(d0s[-1][6:]))
        span = int(kw.get("hard_cap") or kw.get("base_hold") or 1) + FORWARD_SLACK_DAYS
        # 用自然日粗放外扩再由交易日历收敛(交易日 ≈ 自然日 × 5/7,乘 2 富余)
        price_maps = build_price_maps(codes, start, last + timedelta(days=2 * span + 10),
                                      parquet_dir=parquet_dir)
    if not price_maps.ok:
        logger.warning("[eval] 判分价格图为空(%s),可交易收益整段算不出", price_maps.note)
        return res

    for rec in records:
        d0 = date(int(rec.d0[:4]), int(rec.d0[4:6]), int(rec.d0[6:]))
        rets: List[float] = []
        for code in rec.members:
            buyable = _member_buyable(rec, code)
            fs = fill_and_score(
                code, d0, buyable=buyable, pm=price_maps.pm, ld=price_maps.ld,
                cal=price_maps.cal, cal_idx=price_maps.cal_idx, score_kw=kw, notional=cash,
                ceiling_price=(rec.max_chase_of(code) if respect_max_chase else None),
            )
            row = fs.to_dict()
            row.update(basket_id=rec.basket_id, tier=rec.tier, role=rec.roles.get(code),
                       pack_version=rec.pack_version, ruleset_version=rec.ruleset_version,
                       driver_kind=rec.driver_kind)
            unfinished = fs.filled and fs.exit_reason == "end"
            row["unfinished"] = unfinished
            res.per_member.append(row)
            res.fill_reasons[fs.fill_code] = res.fill_reasons.get(fs.fill_code, 0) + 1
            if not fs.filled:
                res.not_filled += 1
                continue
            if fs.fill_code != "ok":
                # `unresolved`:退出没解出来(价缺失 / 前向窗口没走完)——⛔ 不进均值
                res.unresolved += 1
                continue
            res.filled += 1
            if unfinished:
                res.unfinished += 1
                continue
            rets.append(fs.ret)
        res.per_basket[rec.basket_id] = _mean(rets)
    return res


def _member_buyable(rec: BasketRecord, code: str) -> bool:
    """成员在 D+1 买不买得进 —— **读复盘的 ⑥ 项**(机械判已经算过,不重算第二遍)。

    复盘缺席(那天没跑复盘)→ 保守当**可买**交给 `fill_and_score` 自己按价格判:
    它的 `FILL_T1_SUSPENDED` / `FILL_ABOVE_CEILING` 仍会拦住真买不进的情形,
    唯一漏掉的是「收在涨停但不是一字」那一档。**如实登记在返回行的 `fill_code` 里**,
    ⛔ 不许因为缺复盘就把这只票整个丢掉(那是静默删样本)。
    """
    item = rec.mech_item("buyability")
    per = item.get("per_member")
    if isinstance(per, Mapping) and code in per:
        return bool((per.get(code) or {}).get("buyable"))
    return True


# ══════════════════════════════════════════════════════════════════════════
# 6 用户已选 vs 未选
# ══════════════════════════════════════════════════════════════════════════

def selected_vs_not(records: Sequence[BasketRecord],
                    tradable: Optional[TradableResult] = None) -> Dict[str, Any]:
    """用户**已选** vs **未选**篮子的结果对照。

    V2 初期 `user_actions` 里一条 `select` 都没有 —— 那时 `selected=0`,本指标
    **如实返回空值 + `reason='no_user_data'`**,⛔ 不许把"没人选过"算成"未选组表现"
    (那会让对照组吃掉全部样本,看起来像个结论)。
    """
    sel = [r for r in records if r.selected]
    unsel = [r for r in records if not r.selected]
    if not sel:
        return {"available": False, "reason": "no_user_data",
                "note": "本期没有任何 user_actions.select 记录,已选组样本为 0",
                "selected": 0, "not_selected": len(unsel),
                "selected_outcome": None, "not_selected_outcome": None}
    pick = (lambda rs: [r.outcome for r in rs if r.outcome is not None])
    out = {
        "available": True, "reason": None,
        "selected": len(sel), "not_selected": len(unsel),
        "selected_outcome": _median(pick(sel)),
        "not_selected_outcome": _median(pick(unsel)),
    }
    if tradable is not None:
        out["selected_tradable"] = _median(
            [v for r in sel if (v := tradable.per_basket.get(r.basket_id)) is not None])
        out["not_selected_tradable"] = _median(
            [v for r in unsel if (v := tradable.per_basket.get(r.basket_id)) is not None])
    return out


# ══════════════════════════════════════════════════════════════════════════
# 7 三个切面的贡献(驱动类型 / 角色 / 市场环境)
# ══════════════════════════════════════════════════════════════════════════

def contribution(records: Sequence[BasketRecord],
                 tradable: Optional[TradableResult] = None) -> Dict[str, Any]:
    """哪类驱动 / 哪种角色 / 什么市场环境贡献正收益。

    **市场环境**取的是复盘 ⑧ 项里那天的大盘涨跌(`close_rs.index_ret` 的符号),
    分 `index_up` / `index_down` 两档 —— 这是能从既有数据无成本得到的最粗分层,
    比不做强、比自造一套 regime 判据诚实(真要分 regime 得由策略线定义)。
    """
    def bucket(key_fn) -> Dict[str, Dict[str, Any]]:
        acc: Dict[str, List[float]] = {}
        for r in records:
            k = key_fn(r)
            if k is None or r.outcome is None:
                continue
            acc.setdefault(str(k), []).append(r.outcome)
        return {k: {"n": len(v), "median": _median(v), "mean": _mean(v)}
                for k, v in sorted(acc.items())}

    def regime_of(r: BasketRecord) -> Optional[str]:
        idx = r.mech_item("close_rs").get("index_ret")
        if not isinstance(idx, (int, float)) or isinstance(idx, bool):
            return None
        return "index_up" if idx > 0 else "index_down"

    by_role: Dict[str, List[float]] = {}
    if tradable is not None:
        for row in tradable.per_member:
            if row.get("filled") and not row.get("unfinished") and row.get("fill_code") == "ok":
                by_role.setdefault(str(row.get("role") or "unknown"), []).append(float(row["ret"]))

    return {
        "by_driver_kind": bucket(lambda r: r.driver_kind),
        "by_market_regime": bucket(regime_of),
        "by_evidence_status": bucket(lambda r: r.evidence_status),
        "by_role_tradable": {k: {"n": len(v), "median": _median(v), "mean": _mean(v)}
                             for k, v in sorted(by_role.items())},
        "role_note": ("角色收益按可交易收益算(唯一判分源 exit_sim);"
                      "样本少时逐角色数字噪声很大,只作观察"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 分层装配
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class StratumReport:
    pack_version: str
    ruleset_version: str
    n_baskets: int
    n_days: int
    days: List[str]
    tier: Dict[str, Any]
    resonance: Dict[str, Any]
    verification: Dict[str, Any]
    leader: Dict[str, Any]
    tradable: Dict[str, Any]
    selected: Dict[str, Any]
    contribution: Dict[str, Any]
    tier_verdict: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packVersion": self.pack_version, "rulesetVersion": self.ruleset_version,
            "nBaskets": self.n_baskets, "nDays": self.n_days, "days": list(self.days),
            "tierMonotonicity": self.tier, "resonance": self.resonance,
            "verification": self.verification, "leader": self.leader,
            "tradable": self.tradable, "selectedVsNot": self.selected,
            "contribution": self.contribution, "tierVerdict": self.tier_verdict,
        }


def evaluate(
    records: Sequence[BasketRecord],
    *,
    score_kw: Optional[Dict[str, Any]] = None,
    notional: Optional[float] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    with_tradable: bool = True,
) -> List[StratumReport]:
    """按 `(pack_version, ruleset_version)` 分层出成绩单(层内再算七组指标)。

    层序:`(pack_version, ruleset_version)` 字典序 —— 确定性,重跑逐位可比。
    """
    strata: Dict[Tuple[str, str], List[BasketRecord]] = {}
    for r in records:
        strata.setdefault(r.stratum, []).append(r)

    out: List[StratumReport] = []
    for (pack, ruleset) in sorted(strata):
        rs = strata[(pack, ruleset)]
        days = sorted({r.d0 for r in rs})
        tr = TradableResult()
        if with_tradable:
            try:
                tr = score_tradable(rs, score_kw=score_kw, notional=notional,
                                    db_path=db_path, parquet_dir=parquet_dir)
            except Exception as exc:  # noqa: BLE001 —— 判分是可选情报,炸了不该掀翻整份成绩单
                logger.warning("[eval] 可交易收益判分失败(pack=%s ruleset=%s)", pack, ruleset,
                               exc_info=True)
                tr = TradableResult()
                tr.fill_reasons["scoring_failed"] = 1
                tr.per_basket = {}
                tr.per_member = [{"error": f"{type(exc).__name__}: {exc}"}]
        tier_stats = tier_monotonicity(rs)
        mono = tier_stats.get("monotonic")
        out.append(StratumReport(
            pack_version=pack, ruleset_version=ruleset, n_baskets=len(rs),
            n_days=len(days), days=days,
            tier=tier_stats,
            resonance=resonance_rate(rs),
            verification=verification_rate(rs),
            leader=leader_vs_members(rs),
            tradable=tr.summary(),
            selected=selected_vs_not(rs, tr),
            contribution=contribution(rs, tr),
            tier_verdict=verdict(
                len(days), len(rs),
                ("Tier 单调性成立(T1 ≥ T2 ≥ T3 的结果中位数)" if mono
                 else "Tier 单调性不成立" if mono is not None
                 else "档位样本不足两档,单调性无从谈起"),
                {"medianOutcome": tier_stats.get("median_outcome")},
            ).to_dict(),
        ))
    return out


def forward_window_ready(d0: Any, *, score_kw: Optional[Mapping[str, Any]] = None,
                         as_of: Optional[date] = None, db_path: Optional[Path] = None) -> bool:
    """这一天的篮子,前向窗口走完了没有(走完才谈得上"可交易收益"这个数完整)。

    判据:`D0` 之后已经过去 `hard_cap + FORWARD_SLACK_DAYS` 个交易日。⛔ 没走完
    **不是**"算不出",而是"这个数还在变" —— 调用方据此在报告里如实标注。
    """
    kw = dict(score_kw or score_kw_from_charter(db_path=db_path))
    need = int(kw.get("hard_cap") or kw.get("base_hold") or 1) + FORWARD_SLACK_DAYS
    d = d0 if isinstance(d0, date) else date(int(d0[:4]), int(d0[4:6]), int(d0[6:]))
    end = as_of or date.today()
    if end <= d:
        return False
    return len(trading_days_between(next_trading_day(d), end)) >= need


__all__ = [
    "MIN_CONCLUSION_DAYS", "FORWARD_SLACK_DAYS", "UNSET",
    "BasketRecord", "load_basket_panel",
    "Verdict", "verdict",
    "tier_monotonicity", "resonance_rate", "verification_rate", "leader_vs_members",
    "TradableResult", "score_tradable", "selected_vs_not", "contribution",
    "StratumReport", "evaluate", "forward_window_ready",
]
