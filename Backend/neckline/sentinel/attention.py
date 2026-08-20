"""监控注意力 80/15/5 的**新增四监测 + 同篮合并敞口**(plan §五 V2-⑪-A;蓝图 5.4/6.2)。

**本模块是旁路,不是纪律**(整块最重要的一句):

    · 现役纪律分支 —— −5% 止损、回落止盈、时间退出(含 `suspended_hold` 第五态)、
      证伪四哨兵、熔断、盘前校准 —— **一行不动**。本模块**不读、不改、不参与**它们
      的任何判定;`engine.run_tick` 里它挂在独立 `try/except` 旁路上,炸了只 WARNING。
    · 它回答的是**「为什么」**:持仓在跌,是同篮子一起在跌、是板块没了承接、是它
      自己掉队,还是大盘整体塌了。这四个问题以前没人回答,用户只能自己盯盘。
    · **⛔ 不构成任何交易指令**(§3.8 / §2.8-B 语义红线):文案一律带「参考、非指令」,
      系统永不下单 / 撤单 / 改止损。

**注意力配比落地(蓝图 5.4:80% 持仓自身 / 15% 大盘 / 5% 板块)**:四个监测里三个
(①②③)**以持仓为主语**、只在持仓存在时才评估,第四个(④大盘突变)同样**只在有
持仓时评估** —— 蓝图原文是「大盘是否发生足以影响**全部持仓**的突变」,空仓时它不是
一个需要打扰用户的问题。**板块监控只服务于解释持仓,不扫描无关板块**(蓝图 5.4 原文)。

**数据只用两样**:D0 冻结件(来源篮子及其成员,来自 ⑩ 的 `entry_snapshots.basket_id`
/ `position_plans.source_basket_id`)+ 这一拍**已经拉到的实时报价**。**零额外网络**
(同 ⑧-B 存拍的纪律:旁路不许给主循环加一次请求)。

**四处诚实的局限(不是遗漏,已在完工记录登记)**:

  1. **同篮成员的行情覆盖不完整**。关注池(⑧-A)装的是**今天**的 T1/T2 篮子成员;
     而一笔 D+3 的持仓,它的来源篮子是**三天前**那份冻结件,成员未必还在今天的池子
     里。买入当天(D+1)命中率最高(那天的来源篮 == 池子里的篮),越老的持仓覆盖越
     差。本模块**不为此额外拉价**(旁路不加网络),而是如实报 `sample_n` 与
     `coverage`,样本不足 `PEER_MIN_SAMPLE` 就**不判**(「没有样本」≠「篮子健康」)。
  2. **「板块 ETF 承接」实为板块基准指数**(⑧-A 已登记的同一件事,§七 P4-35):项目
     没有 ETF 成分 / 映射数据源,硬编一份 ETF 清单等于凭空发明。文案里**明写是指数
     不是 ETF**,不让用户以为系统在看 ETF 盘口。
  3. **大盘突变依赖宽基指数恰好在关注池里**。池里的指数由 `universe._related_index_
     codes` 按持仓 / 成员**所在板块**反查(主板→上证综指 / 深证成指),因此纯创业板
     持仓的日子可能一支宽基都没有 → 本监测如实标 `no_broad_index_quote` 并**不判**。
     要让它恒定生效,得把两支宽基**无条件**塞进关注池 —— 那会挤占 `breadth_cap` 配额、
     进而改变退潮哨兵的宽度样本(属 ⑧-A 关注池组成变更),**⛔ 本块不擅自动**,已登记
     待裁定。
  4. **合并敞口按「来源篮子」归并,不按 LLM 说的题材文本归并**。篮子是冻结件、有
     `basket_id` 可对账;题材文本是自由中文,拿它当归并键迟早把两个写法不同的同义
     题材算成两份分散仓位(或反过来)。篮子的 `driver` 只进**展示文案**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from neckline.config import settings as _app_settings
from neckline.db import connection, init_schema
from neckline.notify_kinds import (
    KIND_BASKET_PEERS_WEAK,
    KIND_HOLDING_DECOUPLED,
    KIND_MARKET_SHOCK,
    KIND_SECTOR_BID_FADE,
)
from neckline.sentinel.positions import Position
from neckline.data.realtime import Quote
from neckline.sentinel.universe import (
    BOARD_BENCHMARK_INDEX,
    MAIN_BOARD_INDEX_SH,
    MAIN_BOARD_INDEX_SZ,
    StockMeta,
)

logger = logging.getLogger(__name__)

# 浮点比较容差(CLAUDE.md 体例,同 `holding.py::_EPS` / `verification_rules.EPS`)。
_EPS = 1e-9

# 两支宽基指数 = 「大盘」的口径(创业板指 / 科创50 / 北证50 是**板块**基准,不是大盘,
# 故不在此列 —— 它们归监测②)。代码取自 `universe.py` 的同一批常量,不另抄字面量。
BROAD_MARKET_INDEXES: Tuple[str, ...] = (MAIN_BOARD_INDEX_SH, MAIN_BOARD_INDEX_SZ)

# ══════════════════════════════════════════════════════════════════════════
# ⚠ 阈值白名单(**工程默认、零回测背书** —— 登记体例照 ⑦-b
#   `selection/verification_rules.py`,plan §五 V2-⑪-A 要求)
# ══════════════════════════════════════════════════════════════════════════
#
# 下面每一个数字都是为了让四个监测**能跑起来**而拟的占位值,**没有任何回测或事件
# 研究支持它们**。它们:
#     · **不进选股策略包**(`selection_packs`)—— 包是策略线交付物,这些是持仓侧
#       通知的灵敏度旋钮,两条线不混(同 ⑦-b-A 的归属裁定);
#     · **不进纪律章程**(`strategy_versions`)—— 章程只装纪律,通知灵敏度不是纪律;
#     · **不影响任何判定** —— 调大调小只改「推不推这条提醒」,不改任何一笔持仓的
#       止损 / 止盈 / 退出判断。
# **升级路径**:攒够真实盘中样本(⑨ 的复盘数据 + 用户对通知有用性的反馈)再谈校准;
# 在那之前 ⛔ 不许"凭手感"改数,改了要同步改 `ATTENTION_DEFAULTS` 与守门单测。

# ① 同篮成员集体转弱
PEER_MIN_SAMPLE = 2             # 有行情的同篮其它成员少于这个数 → 不判(样本不足)
PEER_WEAK_RET = -0.03           # 单只成员「转弱」线:盘中涨跌幅 ≤ −3%
PEER_WEAK_SHARE = 0.5           # 「集体」= 转弱成员占有效样本的比例 ≥ 一半
PEER_WEAK_MEAN_RET = -0.02      # 且样本均值 ≤ −2%(防「一只暴跌拉着少数样本过线」)

# ② 板块(基准指数)承接消失
INDEX_FADE_RET = -0.010         # 指数当前涨跌幅 ≤ −1.0%
INDEX_FADE_RETRACE = 0.008      # 且自日内高点回落 ≥ 0.8%(「冲高之后没人接」)

# ③ 持仓从跟随板块转为独立弱势
DECOUPLE_SELF_RET = -0.03       # 自己 ≤ −3%
DECOUPLE_REF_FLOOR = -0.01      # 参照(同篮均值优先,退指数)≥ −1% = 环境没坏
DECOUPLE_GAP = -0.03            # 且自己落后参照 ≥ 3 个百分点

# ④ 大盘突变
MARKET_SHOCK_RET = -0.02            # 宽基 ≤ −2%
MARKET_SHOCK_RETRACE = 0.015        # 或 自日内高点回落 ≥ 1.5%
MARKET_SHOCK_RETRACE_RET = -0.005   #   且此时已翻绿 ≤ −0.5%(单纯高位回落不算突变)

# 合并敞口:占总仓比例达到这条线时,提示语里带上具体占比(低于它只在数据里给,不
# 主动在推送正文里念 —— 通知只说三件事,别把它写成一份报表)。
MERGED_EXPOSURE_NOTE_SHARE = 0.10

ATTENTION_DEFAULTS: Dict[str, float] = {
    "peer_min_sample": PEER_MIN_SAMPLE,
    "peer_weak_ret": PEER_WEAK_RET,
    "peer_weak_share": PEER_WEAK_SHARE,
    "peer_weak_mean_ret": PEER_WEAK_MEAN_RET,
    "index_fade_ret": INDEX_FADE_RET,
    "index_fade_retrace": INDEX_FADE_RETRACE,
    "decouple_self_ret": DECOUPLE_SELF_RET,
    "decouple_ref_floor": DECOUPLE_REF_FLOOR,
    "decouple_gap": DECOUPLE_GAP,
    "market_shock_ret": MARKET_SHOCK_RET,
    "market_shock_retrace": MARKET_SHOCK_RETRACE,
    "market_shock_retrace_ret": MARKET_SHOCK_RETRACE_RET,
    "merged_exposure_note_share": MERGED_EXPOSURE_NOTE_SHARE,
}


# ══════════════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class AttentionAlert:
    """一条监测命中。**只描述观察到的事实**,不含任何动作建议。"""

    kind: str                       # `notify_kinds` 四监测之一
    scope: str                      # 防重台账的 ts_code 位(持仓票 / 指数码 / '' = 大盘级)
    event_key: str                  # 防重台账的 event_key 位
    title: str
    what_happened: str              # 「发生了什么」
    plan_touched: str = ""          # 「触碰了哪条计划」(给不出就空,⛔ 不编)
    position_id: Optional[int] = None
    merged_exposure_note: str = ""  # 同篮合并敞口提示(蓝图 6.2)
    metrics: Dict[str, Any] = field(default_factory=dict)   # 原始数字,落 payload 供审计


@dataclass(frozen=True)
class PositionSource:
    """一笔持仓的 D0 来源篮子(⑩ 冻结的关联)。查不到 → 调用方按「独立买入」处理。"""

    position_id: int
    basket_id: int
    basket_key: str
    basket_name: str
    driver: str
    tier: Optional[int]
    member_codes: Tuple[str, ...]
    link_source: str    # 'entry_snapshot' | 'position_plan'(如实标关联从哪来的)


@dataclass(frozen=True)
class MergedExposureGroup:
    """同一来源篮子下的多笔持仓 = **一份主题风险**,不是几笔分散仓位(蓝图 6.2)。"""

    basket_id: int
    basket_key: str
    basket_name: str
    driver: str
    position_ids: Tuple[int, ...]
    codes: Tuple[str, ...]              # 去重后的标的(**按首次出现序**,确定性)
    cost_amount: float                  # Σ 买入价 × 股数
    market_amount: Optional[float]      # Σ 现价 × 股数;有票缺行情 → 见 partial
    market_partial: bool                # True = 市值只覆盖了部分持仓(如实标)
    cost_share_of_total: Optional[float]  # 占 `Settings.total_capital` 的比例
    theme_concentration: bool           # ≥2 个**不同标的** = 蓝图 6.2 说的那种情形


@dataclass
class AttentionResult:
    alerts: List[AttentionAlert] = field(default_factory=list)
    merged_exposure: List[MergedExposureGroup] = field(default_factory=list)
    # 未能评估的监测 → 原因码(「没有」与「没看」必须分得开,§铁律)
    unavailable: Dict[str, str] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
# 基础量
# ══════════════════════════════════════════════════════════════════════════

def intraday_return(quote: Optional[Quote]) -> Optional[float]:
    """盘中涨跌幅 = 现价 / 昨收 − 1。缺行情 / 昨收为 0 → `None`(**不是 0.0**:
    「没数据」与「没涨跌」是两件事)。"""
    if quote is None or not quote.pre_close or quote.pre_close <= 0 or quote.price <= 0:
        return None
    return quote.price / quote.pre_close - 1.0


def retrace_from_day_high(quote: Optional[Quote]) -> Optional[float]:
    """自日内最高价的回落幅度(正数 = 已回落多少)。缺行情 / 高价非正 → `None`。"""
    if quote is None or quote.high <= 0 or quote.price <= 0:
        return None
    return (quote.high - quote.price) / quote.high


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:+.1%}"


# ══════════════════════════════════════════════════════════════════════════
# 来源篮子关联(只读 entry_snapshots / position_plans / baskets / basket_members)
# ══════════════════════════════════════════════════════════════════════════

def load_position_sources(
    positions: Sequence[Position], *, db_path: Optional[Path] = None
) -> Dict[int, PositionSource]:
    """`position_id -> PositionSource`(查不到来源的持仓**不出现在结果里**)。

    关联优先 `entry_snapshots.basket_id`(⑩ 开仓时冻结的那一份),缺行才退
    `position_plans.source_basket_id`(⑩ 之前用 CLI/旧路径开的仓,或 entry_snapshot
    那一步 best-effort 失败过)。两条来源都如实记在 `link_source` 里,不混同。

    **只读**:本函数与本模块对 `baskets`/`basket_members`/`basket_cards`/`tier_history`
    **零写入**(承 ⑩-E 的同一条守门纪律,`tests/test_sentinel_attention.py` AST 断言)。"""
    if not positions:
        return {}
    ids = [int(p.id) for p in positions]
    placeholders = ",".join("?" * len(ids))
    init_schema(db_path)
    out: Dict[int, PositionSource] = {}
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT position_id, basket_id, tier FROM entry_snapshots "
            f"WHERE position_id IN ({placeholders}) AND basket_id IS NOT NULL",
            tuple(ids),
        ).fetchall()
        link: Dict[int, Tuple[int, Optional[int], str]] = {
            int(r[0]): (int(r[1]), (int(r[2]) if r[2] is not None else None), "entry_snapshot")
            for r in rows
        }
        missing = [i for i in ids if i not in link]
        if missing:
            ph2 = ",".join("?" * len(missing))
            rows2 = conn.execute(
                f"SELECT position_id, source_basket_id FROM position_plans "
                f"WHERE position_id IN ({ph2}) AND source_basket_id IS NOT NULL "
                f"ORDER BY position_id, version",
                tuple(missing),
            ).fetchall()
            for r in rows2:
                # 同一 position 多版本计划 → 取**最早**那版的来源(v1 是从 D0 卡继承的
                # 那一份;后续版本承袭同一来源,理论上相同,取最早最保守)。
                link.setdefault(int(r[0]), (int(r[1]), None, "position_plan"))
        if not link:
            return {}
        basket_ids = sorted({b for b, _t, _s in link.values()})
        ph3 = ",".join("?" * len(basket_ids))
        brows = conn.execute(
            f"SELECT id, basket_key, name, driver, tier FROM baskets WHERE id IN ({ph3})",
            tuple(basket_ids),
        ).fetchall()
        binfo = {int(r[0]): (str(r[1]), str(r[2]), str(r[3]), int(r[4])) for r in brows}
        members: Dict[int, Tuple[str, ...]] = {}
        for bid in basket_ids:
            mrows = conn.execute(
                "SELECT ts_code FROM basket_members WHERE basket_id=? ORDER BY ts_code", (bid,)
            ).fetchall()
            members[bid] = tuple(str(m[0]) for m in mrows)
    for pid, (bid, tier, src) in link.items():
        info = binfo.get(bid)
        if info is None:
            continue    # 关联指向一个不存在的篮子 → 当作查不到来源(不臆造)
        key, name, driver, btier = info
        out[pid] = PositionSource(
            position_id=pid, basket_id=bid, basket_key=key, basket_name=name,
            driver=driver, tier=(tier if tier is not None else btier),
            member_codes=members.get(bid, ()), link_source=src,
        )
    return out


# ══════════════════════════════════════════════════════════════════════════
# 同篮合并敞口(蓝图 6.2)
# ══════════════════════════════════════════════════════════════════════════

def compute_merged_exposure(
    positions: Sequence[Position],
    quotes: Dict[str, Quote],
    sources: Dict[int, PositionSource],
    *,
    total_capital: Optional[float] = None,
) -> List[MergedExposureGroup]:
    """把持仓按**来源篮子**归并成「一份主题风险」(蓝图 6.2:同一篮子的两只票**不能**
    被视为完全分散的两笔仓位)。

    · 归并键 = `basket_id`(冻结件,可对账);题材文本只进展示,见模块头登记 4。
    · 只包含**有来源篮子**的持仓;独立买入不归并(它本来就没有主题可共享)。
    · 单只票分批建的两笔也会落在同一组,但 `theme_concentration=False` —— 蓝图讲的是
      「两只**不同**的票看起来分散、其实同题材」,同一只票分批本来就没人会误以为分散。
    · 市值口径:有行情按现价,缺行情的那几笔**不拿成本冒充市值**,而是整组标
      `market_partial=True`(「没有」与「没看」分开)。
    · 排序:按 `basket_id` 升序(**确定性**,同一批输入两次调用结果逐位相同)。"""
    cap = total_capital if total_capital is not None else float(_app_settings.total_capital)
    buckets: Dict[int, List[Position]] = {}
    for p in positions:
        src = sources.get(int(p.id))
        if src is None:
            continue
        buckets.setdefault(src.basket_id, []).append(p)
    out: List[MergedExposureGroup] = []
    for bid in sorted(buckets):
        plist = buckets[bid]
        if len(plist) < 2:
            continue
        src = sources[int(plist[0].id)]
        codes: List[str] = []
        for p in plist:
            if p.ts_code not in codes:
                codes.append(p.ts_code)
        cost = sum(float(p.buy_price) * int(p.qty) for p in plist)
        market = 0.0
        partial = False
        for p in plist:
            q = quotes.get(p.ts_code)
            if q is None or q.price <= 0:
                partial = True
                continue
            market += float(q.price) * int(p.qty)
        out.append(MergedExposureGroup(
            basket_id=bid, basket_key=src.basket_key, basket_name=src.basket_name,
            driver=src.driver,
            position_ids=tuple(int(p.id) for p in plist),
            codes=tuple(codes),
            cost_amount=round(cost, 2),
            market_amount=(round(market, 2) if not partial or market > 0 else None),
            market_partial=partial,
            cost_share_of_total=(round(cost / cap, 4) if cap > 0 else None),
            theme_concentration=len(codes) >= 2,
        ))
    return out


def _exposure_note_for(
    position_id: int, groups: Sequence[MergedExposureGroup]
) -> str:
    """这笔持仓所在的合并敞口提示句(不属于任何多仓组 / 不是主题集中 → 空串)。"""
    for g in groups:
        if position_id in g.position_ids and g.theme_concentration:
            share = g.cost_share_of_total
            tail = f",合计占总仓 {share:.1%}" if share is not None and share >= MERGED_EXPOSURE_NOTE_SHARE else ""
            return (f"同篮合并敞口:「{g.basket_name}」下你有 {len(g.codes)} 只持仓"
                    f"({'、'.join(g.codes)}){tail},按同一份主题风险看待")
    return ""


# ══════════════════════════════════════════════════════════════════════════
# 四监测(全部纯规则,输入 = D0 冻结件 + 本拍实时报价)
# ══════════════════════════════════════════════════════════════════════════

def _peer_returns(
    src: PositionSource, own_code: str, quotes: Dict[str, Quote]
) -> Tuple[List[float], int]:
    """来源篮子里**其它**成员的盘中涨跌幅 + 「其它成员」的总数(算覆盖率用)。

    ⚠ 总数按「实际排除掉本票之后还剩几个」算,**不是** `len(members) - 1`:持仓票未必
    还在成员名单里(⑤ 的主归属可能换过、成员卫生线可能剔过它),硬减一会让覆盖率
    分母少一个,报出去的「样本覆盖 2/4」就是错的。"""
    peers = [c for c in src.member_codes if c != own_code]
    rets: List[float] = []
    for code in peers:
        r = intraday_return(quotes.get(code))
        if r is not None:
            rets.append(r)
    return rets, len(peers)


def check_basket_peers_weak(
    position: Position, src: PositionSource, quotes: Dict[str, Quote]
) -> Optional[AttentionAlert]:
    """① 同篮子成员集体转弱(蓝图 5.4「龙头、中军或同篮子股票是否集体转弱」)。

    判据:有行情的同篮其它成员 ≥ `PEER_MIN_SAMPLE` 只,其中 ≤ `PEER_WEAK_RET` 的
    占比 ≥ `PEER_WEAK_SHARE`,**且**样本均值 ≤ `PEER_WEAK_MEAN_RET`。样本不足直接
    返回 `None` —— 那是「没有样本」,不是「篮子健康」。"""
    rets, peer_total = _peer_returns(src, position.ts_code, quotes)
    n = len(rets)
    if n < PEER_MIN_SAMPLE:
        return None
    weak_n = sum(1 for r in rets if r <= PEER_WEAK_RET + _EPS)
    share = weak_n / n
    mean = sum(rets) / n
    if share < PEER_WEAK_SHARE - _EPS or mean > PEER_WEAK_MEAN_RET + _EPS:
        return None
    coverage = (n / peer_total) if peer_total else None
    cov_txt = f",样本覆盖 {n}/{peer_total} 只" if peer_total else ""
    return AttentionAlert(
        kind=KIND_BASKET_PEERS_WEAK,
        scope=position.ts_code, event_key=f"basket{src.basket_id}",
        title=f"同篮成员集体转弱:{position.ts_code}",
        what_happened=(
            f"来源篮子「{src.basket_name}」里 {weak_n}/{n} 只同篮成员盘中跌幅超过 "
            f"{abs(PEER_WEAK_RET):.0%},均值 {_pct(mean)}{cov_txt}"
        ),
        plan_touched=(
            f"这笔持仓的建仓依据是该篮的共同驱动「{src.driver}」;"
            f"成员集体转弱意味着当初那条共振正在减弱"
        ),
        position_id=int(position.id),
        metrics={
            "sample_n": n, "peer_total": peer_total, "coverage": coverage,
            "weak_n": weak_n, "weak_share": round(share, 4), "mean_ret": round(mean, 6),
            "basket_id": src.basket_id, "link_source": src.link_source,
        },
    )


def check_sector_bid_fade(
    index_code: str, quote: Optional[Quote], *, holders: Sequence[str]
) -> Optional[AttentionAlert]:
    """② 板块承接消失(蓝图 5.4「板块 ETF 或指数是否突然失去承接」)。

    ⚠ **本版看的是板块基准指数,不是 ETF**(项目无 ETF 成分/映射数据源,§七 P4-35)
    —— 文案里明写,不让用户以为系统在读 ETF 盘口。

    判据:指数当前涨跌幅 ≤ `INDEX_FADE_RET` **且** 自日内高点回落 ≥
    `INDEX_FADE_RETRACE`(「冲高之后没人接」这个形态需要两条同时成立;只跌不回落更像
    低开阴跌,只回落不跌是高位整理,都不是「突然失去承接」)。"""
    r = intraday_return(quote)
    back = retrace_from_day_high(quote)
    if r is None or back is None:
        return None
    if r > INDEX_FADE_RET + _EPS or back < INDEX_FADE_RETRACE - _EPS:
        return None
    who = "、".join(holders) if holders else "持仓"
    return AttentionAlert(
        kind=KIND_SECTOR_BID_FADE,
        scope=index_code, event_key="fade",
        title=f"板块承接减弱:{index_code}",
        # 🔴 **⛔ 这句话里不许有 Markdown**(`CLAUDE.md` 两条守门的同一个病,第三个现场):
        # 2026-08-12 用户裁定 ② 之后它**真的会下发给客户端**(`/positions` 的
        # `portfolioAlerts[].verdict`),而客户端拿到的是 `String` → `Text(String)`
        # **不解析 Markdown** → `**…**` 的星号会原样印在屏幕上。要强调就用「」。
        what_happened=(
            f"{who} 所属板块的基准指数 {index_code} 现报 {_pct(r)}、自日内高点回落 "
            f"{back:.1%}(「是板块基准指数,不是板块 ETF」—— 本项目没有 ETF 成分数据源)"
        ),
        plan_touched="板块承接是这批持仓共同的环境前提,承接减弱时个股的跟随性会变差",
        metrics={"index_ret": round(r, 6), "retrace_from_high": round(back, 6),
                 "holders": list(holders)},
    )


def check_holding_decoupled(
    position: Position, quote: Optional[Quote], *,
    ref_ret: Optional[float], ref_label: str, ref_sample: Optional[int],
) -> Optional[AttentionAlert]:
    """③ 持仓从跟随板块转为独立弱势(蓝图 5.4 第三条)。

    判据三条同时成立:自己 ≤ `DECOUPLE_SELF_RET`、参照 ≥ `DECOUPLE_REF_FLOOR`
    (= 环境没坏)、自己落后参照 ≥ `|DECOUPLE_GAP|`。缺参照(无同篮样本也无指数报价)
    → `None`,**不拿 0 当参照**(那等于偷偷假设「大盘平盘」)。"""
    r = intraday_return(quote)
    if r is None or ref_ret is None:
        return None
    gap = r - ref_ret
    if r > DECOUPLE_SELF_RET + _EPS:
        return None
    if ref_ret < DECOUPLE_REF_FLOOR - _EPS:
        return None       # 环境本身就坏了 → 这是「跟着跌」,不是「独立弱势」
    if gap > DECOUPLE_GAP + _EPS:
        return None
    sample_txt = f"(样本 {ref_sample} 只)" if ref_sample else ""
    return AttentionAlert(
        kind=KIND_HOLDING_DECOUPLED,
        scope=position.ts_code, event_key="decoupled",
        title=f"持仓转独立弱势:{position.ts_code}",
        what_happened=(
            f"{position.ts_code} 现报 {_pct(r)},而{ref_label}{sample_txt} {_pct(ref_ret)}"
            f" —— 落后 {abs(gap):.1%},已从跟随转为独立走弱"
        ),
        plan_touched="当初买它是看这条共同驱动;它单独走弱说明这笔仓已经不靠那条驱动在走",
        position_id=int(position.id),
        metrics={"self_ret": round(r, 6), "ref_ret": round(ref_ret, 6),
                 "gap": round(gap, 6), "ref_label": ref_label, "ref_sample": ref_sample},
    )


def check_market_shock(
    quotes: Dict[str, Quote], *, position_count: int
) -> Tuple[Optional[AttentionAlert], Optional[str]]:
    """④ 大盘突变(蓝图 5.4「大盘是否发生足以影响全部持仓的突变」)。

    返回 `(alert_or_None, unavailable_reason_or_None)` —— 分开返回是为了让「没判」
    与「判了没事」不至于混成同一个 `None`。

    判据(任一成立):宽基 ≤ `MARKET_SHOCK_RET`;**或** 自日内高点回落 ≥
    `MARKET_SHOCK_RETRACE` **且** 已跌破 `MARKET_SHOCK_RETRACE_RET`(纯高位回落不算
    突变,那天可能只是从大涨回到小涨)。"""
    if position_count <= 0:
        return None, "no_open_position"     # 蓝图口径:突变的意义是「影响全部持仓」
    seen = [(c, quotes[c]) for c in BROAD_MARKET_INDEXES if c in quotes]
    if not seen:
        return None, "no_broad_index_quote"  # 见模块头登记 3
    worst: Optional[Tuple[str, float, float]] = None
    for code, q in seen:
        r = intraday_return(q)
        back = retrace_from_day_high(q)
        if r is None or back is None:
            continue
        hit = (r <= MARKET_SHOCK_RET + _EPS) or (
            back >= MARKET_SHOCK_RETRACE - _EPS and r <= MARKET_SHOCK_RETRACE_RET + _EPS
        )
        if hit and (worst is None or r < worst[1]):
            worst = (code, r, back)
    if worst is None:
        return None, None
    code, r, back = worst
    return AttentionAlert(
        kind=KIND_MARKET_SHOCK,
        scope="", event_key="shock",
        title="大盘突变",
        what_happened=(
            f"宽基指数 {code} 现报 {_pct(r)}、自日内高点回落 {back:.1%},"
            f"当前 {position_count} 笔持仓共同承受这个环境变化"
        ),
        plan_touched="大盘级变化会同时影响全部持仓,单票层面的验证/失效条件此时解释力下降",
        metrics={"index": code, "index_ret": round(r, 6), "retrace_from_high": round(back, 6),
                 "position_count": position_count,
                 "indexes_seen": [c for c, _q in seen]},
    ), None


# ══════════════════════════════════════════════════════════════════════════
# 编排(一拍一次;engine.run_tick 在独立 try 里调它)
# ══════════════════════════════════════════════════════════════════════════

def _board_index_for(code: str, meta: Dict[str, StockMeta]) -> Optional[str]:
    """该票所属板块的基准指数码。板块判定唯一源 `load_stock_meta`(→ `data/board.
    classify`),⛔ 本模块不自己写前缀正则。"""
    m = meta.get(code)
    if m is None:
        return None
    from neckline.data.board import Board
    if m.board == Board.MAIN:
        return MAIN_BOARD_INDEX_SH if code.upper().endswith(".SH") else MAIN_BOARD_INDEX_SZ
    return BOARD_BENCHMARK_INDEX.get(m.board)


def evaluate_attention(
    trade_date: date,
    positions: Sequence[Position],
    quotes: Dict[str, Quote],
    meta: Dict[str, StockMeta],
    *,
    sources: Optional[Dict[int, PositionSource]] = None,
    db_path: Optional[Path] = None,
    total_capital: Optional[float] = None,
) -> AttentionResult:
    """跑一遍四监测 + 合并敞口。**纯计算 + 只读查询**,不推送、不落账、不改任何
    持仓/纪律状态(推送与防重由 `engine.run_tick` 统一做,同既有四哨兵的分工)。

    `sources` 可由调用方预先算好传入(`engine.run_tick` 与 NL 提醒执行器共用同一份,
    省一次查库);不传则本函数自己查。

    `trade_date` 目前只用于日志与 payload 留痕(判据全在这一拍的行情里),保留参数是
    为了让调用方与其它哨兵签名一致、且日后要按日取冻结件时不必改签名。"""
    result = AttentionResult()
    if not positions:
        result.unavailable["all"] = "no_open_position"
        # 大盘突变同样按蓝图口径不判(它问的是「影响全部持仓」)。
        result.unavailable[KIND_MARKET_SHOCK] = "no_open_position"
        return result

    if sources is None:
        try:
            sources = load_position_sources(positions, db_path=db_path)
        except Exception:  # noqa: BLE001  —— 旁路查库失败不许掀翻整个监测
            logger.warning("[attention] 查持仓来源篮子失败,本拍按「全部独立买入」处理", exc_info=True)
            sources = {}

    result.merged_exposure = compute_merged_exposure(
        positions, quotes, sources, total_capital=total_capital
    )

    # —— ① 同篮成员集体转弱 + ③ 持仓转独立弱势(逐持仓)——————————————————
    without_source = 0
    for p in positions:
        src = sources.get(int(p.id))
        peer_rets: List[float] = []
        if src is not None:
            peer_rets, _total = _peer_returns(src, p.ts_code, quotes)
            alert = check_basket_peers_weak(p, src, quotes)
            if alert is not None:
                result.alerts.append(_with_exposure(alert, result.merged_exposure))
        else:
            without_source += 1

        # ③ 参照优先同篮均值(样本够),否则退该票所属板块的基准指数
        ref_ret: Optional[float] = None
        ref_label = ""
        ref_sample: Optional[int] = None
        if len(peer_rets) >= PEER_MIN_SAMPLE:
            ref_ret = sum(peer_rets) / len(peer_rets)
            ref_label = "同篮其它成员均值"
            ref_sample = len(peer_rets)
        else:
            idx = _board_index_for(p.ts_code, meta)
            if idx is not None:
                ref_ret = intraday_return(quotes.get(idx))
                ref_label = f"所属板块基准指数 {idx}"
        dec = check_holding_decoupled(
            p, quotes.get(p.ts_code), ref_ret=ref_ret, ref_label=ref_label, ref_sample=ref_sample
        )
        if dec is not None:
            result.alerts.append(_with_exposure(dec, result.merged_exposure))
    if without_source:
        result.unavailable[KIND_BASKET_PEERS_WEAK] = f"no_source_basket:{without_source}"

    # —— ② 板块承接消失(**只看持仓所在板块**,不扫描无关板块;蓝图 5.4)—————
    holders_by_index: Dict[str, List[str]] = {}
    for p in positions:
        idx = _board_index_for(p.ts_code, meta)
        if idx is None:
            continue
        holders_by_index.setdefault(idx, []).append(p.ts_code)
    if not holders_by_index:
        result.unavailable[KIND_SECTOR_BID_FADE] = "no_board_meta"
    for idx in sorted(holders_by_index):
        alert = check_sector_bid_fade(idx, quotes.get(idx), holders=sorted(set(holders_by_index[idx])))
        if alert is not None:
            result.alerts.append(alert)

    # —— ④ 大盘突变 ————————————————————————————————————————————————
    shock, reason = check_market_shock(quotes, position_count=len(positions))
    if shock is not None:
        result.alerts.append(shock)
    if reason:
        result.unavailable[KIND_MARKET_SHOCK] = reason
    return result


def _with_exposure(
    alert: AttentionAlert, groups: Sequence[MergedExposureGroup]
) -> AttentionAlert:
    if alert.position_id is None:
        return alert
    note = _exposure_note_for(alert.position_id, groups)
    if not note:
        return alert
    return AttentionAlert(
        kind=alert.kind, scope=alert.scope, event_key=alert.event_key, title=alert.title,
        what_happened=alert.what_happened, plan_touched=alert.plan_touched,
        position_id=alert.position_id, merged_exposure_note=note, metrics=alert.metrics,
    )


__all__ = [
    "AttentionAlert", "AttentionResult", "PositionSource", "MergedExposureGroup",
    "ATTENTION_DEFAULTS", "BROAD_MARKET_INDEXES",
    "PEER_MIN_SAMPLE", "PEER_WEAK_RET", "PEER_WEAK_SHARE", "PEER_WEAK_MEAN_RET",
    "INDEX_FADE_RET", "INDEX_FADE_RETRACE",
    "DECOUPLE_SELF_RET", "DECOUPLE_REF_FLOOR", "DECOUPLE_GAP",
    "MARKET_SHOCK_RET", "MARKET_SHOCK_RETRACE", "MARKET_SHOCK_RETRACE_RET",
    "MERGED_EXPOSURE_NOTE_SHARE",
    "intraday_return", "retrace_from_day_high",
    "load_position_sources", "compute_merged_exposure",
    "check_basket_peers_weak", "check_sector_bid_fade", "check_holding_decoupled",
    "check_market_shock", "evaluate_attention",
]
