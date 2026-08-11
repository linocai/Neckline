"""⑨-C2 安慰剂对照臂(plan §五 V2-⑨-C2,K7 需求 6;用户「尽量都做到」→ 正式验收项)。

「Tier 有没有用」这个长期命题,光看自己的数字答不了 —— 得回答**「是否稳定优于随机
与不作为」**。两条臂::

    · **臂 A · 随机同规模篮子** —— 同一交易日,从**当日过卫生线与安检的合法域**里
      随机抽出与真实篮子**同数量、同成员数**的对照篮子,走**同一套判分**
      (`exit_sim`,⑨-D 唯一源)。种子由 `zlib.crc32` 派生(⛔ 禁内置 `hash()`:
      带进程盐,`PYTHONHASHSEED` 一变分组就漂,历史报告不可复现)。
      **抽 N 次取分布**,报中位数 + 分位 —— 单次抽样的对照没有统计意义。
    · **臂 B · 满仓持有基准** —— 同期「不作为」:按大盘指数(`SSE_INDEX`,与 RS 线
      同源)同期持有的收益,回答「折腾一圈有没有跑赢躺着不动」。

**四条硬边界(接线时最容易越界的地方)**

    1. **只进周报与策略线迭代输入**,⛔ 不进任何在线判据(不改 Tier、不改排序、
       不进哨兵)。
    2. **样本量不足只报样本数、不报结论**(走 `metrics.verdict`,文案由单测锁死)。
    3. **对照口径必须对齐**:随机篮子**没有卡**,因而没有「最高追价」这个约束;
       所以与臂 A 比较时,真实臂也走 **`respect_max_chase=False`** 的那份数字
       (报告里显式标注)。拿"带追价上限的真实臂"去比"无上限的随机臂"是偷跑。
    4. **两臂同样按 `pack_version` 分层**,这样「换包有没有变好」与「比随机好多少」
       能放在同一张表里读(⑨-C2 归因维度对齐)。

⚠ **判分不在这里实现**:本模块一行退出逻辑都没有,一律调 `exit_sim.fill_and_score`
(⑨-C2 验收第 ② 条有 grep 守门单测)。
"""

from __future__ import annotations

import logging
import random
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from neckline.calendar import next_trading_day, trading_days_between
from neckline.eval.exit_sim import (
    PriceMaps, build_price_maps, fill_and_score, forward_span_days, notional_from_charter,
    score_kw_from_charter,
)
from neckline.eval.metrics import BasketRecord, _mean, _median, verdict

logger = logging.getLogger(__name__)

ARM_RANDOM = "random_basket"       # 臂 A
ARM_BUY_AND_HOLD = "buy_and_hold"  # 臂 B

#: 臂 A 每个交易日抽多少次(**引擎常量,不进包** —— 它是统计精度旋钮,不是策略参数)。
#: 200 次足以给出稳定的中位数与 10/90 分位,同时把单日判分成本控制在秒级。
PLACEBO_DRAWS = 200

#: 报告给哪几个分位(与中位数一起看,才知道真实篮子落在随机分布的什么位置)。
PLACEBO_QUANTILES: Tuple[float, ...] = (0.10, 0.25, 0.50, 0.75, 0.90)


def derive_seed(trade_date: Any, pack_version: str, arm: str) -> int:
    """`zlib.crc32(f"{trade_date}|{pack_version}|{arm}")`(plan 逐字定死)。

    ⛔ **禁内置 `hash()`** —— 它带进程盐,`PYTHONHASHSEED` 一变分组就漂,历史报告
    重跑对不上(项目 CLAUDE.md 明文)。crc32 是纯函数,跨进程跨天逐位可复现。
    """
    day = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    return zlib.crc32(f"{day}|{pack_version}|{arm}".encode("utf-8"))


def _quantile(xs: Sequence[float], q: float) -> Optional[float]:
    """确定性分位(线性插值,不依赖 numpy;空样本 → None,⛔ 不返 0)。"""
    s = sorted(float(x) for x in xs if x is not None)
    if not s:
        return None
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


# ══════════════════════════════════════════════════════════════════════════
# 合法域(臂 A 的抽样池)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LegalDomain:
    """某个 D0 的「过卫生线与安检的合法域」。`ok=False` 时臂 A 当日**不出数**
    (⛔ 不许退回"全市场随机"顶上 —— 那是另一个实验,不是这个对照)。"""

    trade_date: str
    codes: Tuple[str, ...] = ()
    source: str = "leader_structure_daily+member_hygiene"
    ok: bool = True
    note: Optional[str] = None
    hygiene_degraded: bool = False


#: 域解析器签名:`(trade_date, db_path, parquet_dir) -> LegalDomain`。做成可注入是
#: 为了让单测不必造一整套 parquet,也为了日后换域定义时不动本模块。
DomainResolver = Callable[[date, Optional[Path], Optional[Path]], LegalDomain]


def default_legal_domain(trade_date: date, db_path: Optional[Path] = None,
                         parquet_dir: Optional[Path] = None) -> LegalDomain:
    """默认合法域 = **当日 `leader_structure_daily` 里出现过的代码**,再过一遍
    ⑤-b 的成员卫生线闸(`apply_member_hygiene`,读现役包参数)。

    **为什么是这个池子**:它正是 ④ 扫描层机械算出来、⑤ 聚合层从中挑成员的那个域
    —— 随机臂要问的是「在同一个池子里瞎抽会怎样」,池子选错了对照就没有意义。
    它是**已预计算落表**的(P0-23 在线只读),不触发任何全市场历史扫描。

    读不到(当日无簇 / 表空)→ `ok=False` + 原因,当日臂 A 缺席并如实披露。
    """
    from neckline.scan.leader import load_leader_structure

    day = trade_date.strftime("%Y%m%d")
    try:
        df = load_leader_structure(trade_date, db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[placebo] leader_structure_daily 读取失败", exc_info=True)
        return LegalDomain(day, ok=False, note=f"合法域读取失败:{type(exc).__name__}: {exc}")
    if df.is_empty():
        return LegalDomain(day, ok=False, note="当日 leader_structure_daily 无行(无涨停共振簇)")
    codes = sorted({str(c) for c in df["ts_code"].to_list() if c})

    try:
        from neckline.data.market_data import get_market_slice, load_stock_basic
        from neckline.selection.member_hygiene import apply_member_hygiene
        from neckline.selection.pack import get_active_pack

        pack = get_active_pack(db_path=db_path)
        if pack is None:
            return LegalDomain(day, tuple(codes), source="leader_structure_daily(未过卫生线)",
                               ok=True, note="无现役策略包,卫生线闸跳过", hygiene_degraded=True)
        basic = load_stock_basic(db_path=db_path)
        industry_of = ({r["ts_code"]: (r.get("industry") or "") for r in basic.iter_rows(named=True)}
                       if not basic.is_empty() else {})
        bars = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
        close_of = ({r["ts_code"]: r["close"] for r in bars.iter_rows(named=True)
                     if r.get("close") is not None} if not bars.is_empty() else {})
        res = apply_member_hygiene(codes, trade_date, pack, industry_of=industry_of,
                                   close_of=close_of, db_path=db_path, parquet_dir=parquet_dir)
        kept = tuple(sorted(res.kept))
        degraded = bool(getattr(res, "hygiene_unavailable", False)
                        or getattr(res, "k4_unavailable", False))
        if not kept:
            return LegalDomain(day, ok=False, note="卫生线过后合法域为空")
        return LegalDomain(day, kept, ok=True, hygiene_degraded=degraded,
                           note=("卫生线部分维度降级为不拦(已如实披露)" if degraded else None))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[placebo] 成员卫生线闸失败,合法域退回未过闸的簇成员并如实标注", exc_info=True)
        return LegalDomain(day, tuple(codes), source="leader_structure_daily(卫生线失败)",
                           ok=True, hygiene_degraded=True,
                           note=f"卫生线闸失败:{type(exc).__name__}(合法域未过闸,对照偏松)")


# ══════════════════════════════════════════════════════════════════════════
# 臂 A · 随机同规模篮子
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ArmResult:
    arm: str
    trade_date: str
    pack_version: str
    available: bool = False
    unavailable_reason: Optional[str] = None
    seed: Optional[int] = None
    draws: int = 0
    domain_size: int = 0
    domain_source: Optional[str] = None
    real_shape: List[int] = field(default_factory=list)   # 真实篮子的成员数序列
    values: List[float] = field(default_factory=list)     # 每次抽样的组合收益
    quantiles: Dict[str, Optional[float]] = field(default_factory=dict)
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "arm": self.arm, "tradeDate": self.trade_date, "packVersion": self.pack_version,
            "available": self.available, "unavailableReason": self.unavailable_reason,
            "seed": self.seed, "draws": self.draws,
            "domainSize": self.domain_size, "domainSource": self.domain_source,
            "realShape": list(self.real_shape),
            "median": _median(self.values), "mean": _mean(self.values),
            "quantiles": dict(self.quantiles), "note": self.note,
        }


def _score_codes(
    codes: Sequence[str],
    d0: date,
    *,
    price_maps: PriceMaps,
    score_kw: Dict[str, Any],
    notional: float,
    buyable_of: Optional[Dict[str, bool]] = None,
) -> List[float]:
    """一组代码在 D0 的可交易收益(**判分唯一源**,本模块不写任何退出逻辑)。

    未成交 / 前向没走完的一律**不进列表**(与 `metrics.score_tradable` 同一条纪律:
    买不进的 0 不是收益)。
    """
    out: List[float] = []
    for code in codes:
        fs = fill_and_score(
            code, d0, buyable=(True if buyable_of is None else buyable_of.get(code, True)),
            pm=price_maps.pm, ld=price_maps.ld, cal=price_maps.cal, cal_idx=price_maps.cal_idx,
            score_kw=score_kw, notional=notional, ceiling_price=None,
        )
        if fs.filled and fs.fill_code == "ok" and fs.exit_reason != "end":
            out.append(fs.ret)
    return out


def random_arm(
    d0: date,
    real_shape: Sequence[int],
    domain: LegalDomain,
    pack_version: str,
    *,
    price_maps: PriceMaps,
    score_kw: Dict[str, Any],
    notional: float,
    draws: int = PLACEBO_DRAWS,
    buyable_of: Optional[Dict[str, bool]] = None,
) -> ArmResult:
    """臂 A:抽 `draws` 次「与真实篮子同数量、同成员数」的随机篮子,取分布。

    每次抽样的组合收益 = 该次抽出的**全部成员**的可交易收益均值(等权),与真实臂
    `score_tradable` 的口径一致(那边也是篮内等权、跨篮再取中位)。

    ⚠ **不放回抽样**:同一次抽样里一只票不会出现两遍(真实篮子也不会);跨次之间
    独立重抽。合法域小于所需总成员数 → 当日臂 A 缺席并如实说明,⛔ 不许放宽成
    有放回(那会造出真实系统不可能产生的篮子)。
    """
    day = d0.strftime("%Y%m%d")
    need = int(sum(real_shape))
    res = ArmResult(arm=ARM_RANDOM, trade_date=day, pack_version=pack_version,
                    real_shape=list(real_shape), domain_size=len(domain.codes),
                    domain_source=domain.source, note=domain.note)
    if not domain.ok:
        res.unavailable_reason = domain.note or "当日合法域取不到"
        return res
    if need <= 0:
        res.unavailable_reason = "当日没有真实篮子,谈不上同规模对照"
        return res
    if len(domain.codes) < need:
        res.unavailable_reason = (
            f"合法域只有 {len(domain.codes)} 只,凑不出同规模对照(需要 {need} 只)")
        return res
    if not price_maps.ok:
        res.unavailable_reason = f"判分价格图不可用:{price_maps.note}"
        return res

    seed = derive_seed(day, pack_version, ARM_RANDOM)
    rng = random.Random(seed)
    res.seed, res.draws = seed, int(draws)
    pool = list(domain.codes)                     # 已排序 → 抽样可复现
    for _ in range(int(draws)):
        picked = rng.sample(pool, need)
        rets = _score_codes(picked, d0, price_maps=price_maps, score_kw=score_kw,
                            notional=notional, buyable_of=buyable_of)
        m = _mean(rets)
        if m is not None:
            res.values.append(m)
    res.available = bool(res.values)
    if not res.available:
        res.unavailable_reason = "所有抽样篮子都没有可成交且走完窗口的成员"
        return res
    res.quantiles = {f"p{int(q * 100)}": _quantile(res.values, q) for q in PLACEBO_QUANTILES}
    return res


# ══════════════════════════════════════════════════════════════════════════
# 臂 B · 满仓持有基准(不作为)
# ══════════════════════════════════════════════════════════════════════════

def buy_and_hold_arm(
    d0: date,
    pack_version: str,
    *,
    score_kw: Dict[str, Any],
    parquet_dir: Optional[Path] = None,
) -> ArmResult:
    """臂 B:同期「躺着不动」。**定义定死**(照抄纪律的基础持有窗口,不发明新数)::

        D+1 开盘买入 `SSE_INDEX` → 持有 `base_hold` 个交易日 → 最后一日**收盘**卖出

    · 用**开盘买、收盘卖**是为了与真实臂的入场时点(T+1 开盘)对齐;卖点取收盘而
      非再下一日开盘,是因为「不作为」没有决策日与撮合日之分。
    · **不套用止损 / 回落止盈** —— 那是纪律,而这条臂问的恰恰是「什么都不做会怎样」;
      给基准加纪律就不是基准了。
    · 指数无法真正"买不进",故没有成交层。
    """
    day = d0.strftime("%Y%m%d")
    res = ArmResult(arm=ARM_BUY_AND_HOLD, trade_date=day, pack_version=pack_version)
    hold = int(score_kw.get("base_hold") or 1)
    try:
        from neckline.data.market_data import get_index_history
        from neckline.strategy.features import SSE_INDEX

        t1 = next_trading_day(d0)
        window = trading_days_between(t1, t1 + timedelta(days=hold * 4 + 20))[:hold]
        if len(window) < hold:
            res.unavailable_reason = f"D0 之后交易日不足 {hold} 天,持有窗口没走完"
            return res
        df = get_index_history(SSE_INDEX, window[0], window[-1], parquet_dir=parquet_dir)
        if df.is_empty():
            res.unavailable_reason = f"{SSE_INDEX} 在 [{window[0]},{window[-1]}] 无数据"
            return res
        rows = {r["trade_date"]: r for r in df.iter_rows(named=True)}
        first, last = rows.get(window[0]), rows.get(window[-1])
        if not first or not last or not first.get("open") or not last.get("close"):
            res.unavailable_reason = "指数窗口首尾行情缺失"
            return res
        ret = float(last["close"]) / float(first["open"]) - 1.0
        res.available = True
        res.values = [ret]
        res.domain_source = SSE_INDEX
        res.note = (f"{SSE_INDEX} 自 {window[0]} 开盘持有至 {window[-1]} 收盘"
                    f"({hold} 个交易日,不套用任何纪律)")
        res.quantiles = {"p50": ret}
        return res
    except Exception as exc:  # noqa: BLE001
        logger.warning("[placebo] 满仓持有基准计算失败", exc_info=True)
        res.unavailable_reason = f"计算失败:{type(exc).__name__}: {exc}"
        return res


# ══════════════════════════════════════════════════════════════════════════
# 两臂总装(按 `pack_version` 分层,逐 D0 跑一遍)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class PlaceboReport:
    pack_version: str
    days: List[str] = field(default_factory=list)
    real_values: List[float] = field(default_factory=list)     # 逐日真实臂(无追价上限口径)
    random_values: List[float] = field(default_factory=list)   # 逐日随机臂中位数
    hold_values: List[float] = field(default_factory=list)     # 逐日满仓持有
    per_day: List[Dict[str, Any]] = field(default_factory=list)
    draws: int = PLACEBO_DRAWS
    vs_random: Optional[Dict[str, Any]] = None
    vs_hold: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packVersion": self.pack_version, "nDays": len(self.days), "days": list(self.days),
            "draws": self.draws,
            "real": {"median": _median(self.real_values), "mean": _mean(self.real_values),
                     "n": len(self.real_values)},
            "randomArm": {"median": _median(self.random_values), "mean": _mean(self.random_values),
                          "n": len(self.random_values)},
            "buyAndHoldArm": {"median": _median(self.hold_values), "mean": _mean(self.hold_values),
                              "n": len(self.hold_values)},
            "vsRandom": self.vs_random, "vsBuyAndHold": self.vs_hold,
            "perDay": list(self.per_day),
            "note": ("对照口径:两臂都**不设最高追价上限**(随机篮没有卡),"
                     "故真实臂在此用同样的无上限口径;带上限的真实收益见主指标表"),
        }


def run_placebo(
    records: Sequence[BasketRecord],
    *,
    score_kw: Optional[Dict[str, Any]] = None,
    notional: Optional[float] = None,
    draws: int = PLACEBO_DRAWS,
    domain_resolver: Optional[DomainResolver] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> List[PlaceboReport]:
    """跑两条对照臂,按 `pack_version` 分层(⑨-C2 归因维度对齐)。

    **可复现性保证**:同一交易日 + 同一包版本跑两次,`derive_seed` 给出同一个种子,
    `random.Random(seed).sample` 在同一个**已排序**的池子上给出同一批抽样 →
    `values` 逐位相同(单测锁死)。
    """
    kw = dict(score_kw or score_kw_from_charter(db_path=db_path))
    cash = float(notional if notional is not None else notional_from_charter(db_path=db_path))
    resolve = domain_resolver or default_legal_domain

    by_pack: Dict[str, List[BasketRecord]] = {}
    for r in records:
        by_pack.setdefault(r.pack_version or "(未登记)", []).append(r)

    out: List[PlaceboReport] = []
    for pack in sorted(by_pack):
        rows = by_pack[pack]
        rep = PlaceboReport(pack_version=pack, draws=int(draws))
        by_day: Dict[str, List[BasketRecord]] = {}
        for r in rows:
            by_day.setdefault(r.d0, []).append(r)
        for day in sorted(by_day):
            d0 = date(int(day[:4]), int(day[4:6]), int(day[6:]))
            todays = sorted(by_day[day], key=lambda r: r.basket_key)
            shape = [len(r.members) for r in todays]
            domain = resolve(d0, db_path, parquet_dir)
            span = forward_span_days(kw) + 2
            codes = sorted(set(domain.codes) | {c for r in todays for c in r.members})
            pmaps = build_price_maps(codes, d0, d0 + timedelta(days=2 * span + 20),
                                     parquet_dir=parquet_dir)
            real_rets = _score_codes([c for r in todays for c in r.members], d0,
                                     price_maps=pmaps, score_kw=kw, notional=cash)
            real = _mean(real_rets)
            arm_a = random_arm(d0, shape, domain, pack, price_maps=pmaps, score_kw=kw,
                               notional=cash, draws=draws)
            arm_b = buy_and_hold_arm(d0, pack, score_kw=kw, parquet_dir=parquet_dir)
            rep.days.append(day)
            if real is not None:
                rep.real_values.append(real)
            a_med = _median(arm_a.values) if arm_a.available else None
            if a_med is not None:
                rep.random_values.append(a_med)
            if arm_b.available:
                rep.hold_values.append(arm_b.values[0])
            rep.per_day.append({
                "tradeDate": day, "realBaskets": len(todays), "realShape": shape,
                "real": real, "randomArm": arm_a.to_dict(), "buyAndHoldArm": arm_b.to_dict(),
                "realPercentileInRandom": (
                    None if (real is None or not arm_a.values) else
                    round(100.0 * sum(1 for v in arm_a.values if v < real) / len(arm_a.values), 1)),
            })

        n_days = len(rep.days)
        rm, am = _median(rep.real_values), _median(rep.random_values)
        hm = _median(rep.hold_values)
        rep.vs_random = verdict(
            n_days, len(rep.real_values),
            ("真实篮子中位收益优于随机同规模篮子" if (rm is not None and am is not None and rm > am)
             else "真实篮子中位收益**未**优于随机同规模篮子" if (rm is not None and am is not None)
             else "两臂都有交易日算不出,无从比较"),
            {"real": rm, "random": am},
        ).to_dict()
        rep.vs_hold = verdict(
            n_days, len(rep.real_values),
            ("真实篮子中位收益优于同期满仓持有" if (rm is not None and hm is not None and rm > hm)
             else "真实篮子中位收益**未**优于同期满仓持有" if (rm is not None and hm is not None)
             else "两臂都有交易日算不出,无从比较"),
            {"real": rm, "buyAndHold": hm},
        ).to_dict()
        out.append(rep)
    return out


__all__ = [
    "ARM_RANDOM", "ARM_BUY_AND_HOLD", "PLACEBO_DRAWS", "PLACEBO_QUANTILES",
    "derive_seed", "LegalDomain", "DomainResolver", "default_legal_domain",
    "ArmResult", "random_arm", "buy_and_hold_arm", "PlaceboReport", "run_placebo",
]
