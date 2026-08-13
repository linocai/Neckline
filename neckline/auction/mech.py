"""竞价层的**机械层**(V2.3.3-②,K8.md §二十「机械层与 LLM 分工」)。

K8 原文给机械层的**六条职责**,逐条落在本模块:

    1. 抓取并冻结原始数据        ← `collect.py`(本模块只吃它的产物)
    2. 检查时间、来源、覆盖率、缺失和异常   → `data_quality` 三态 + 逐票三态
    3. 计算竞价涨跌幅、量额变化和相对强弱    → `MemberReading`
    4. 汇总篮子、板块、核心和指数的竞价表现  → `sector_sync` / `rel_strength`
    5. **标记触发 D0 明确失效位置的股票**     → `hit_invalidation`
    6. 生成供 LLM 判断的短摘要             → `short_summary()`

🔴 ⛔ **本模块不出任何结论**:`confirm` / `neutral` / `veto` 一个都不许在这里产生
(K8 §二十 的分工是「机械层只出读数、判定交 LLM」)。⛔ 零 LLM ——
「明显掉队 / 有效协同 / 共同转强」全部交 LLM,这里只给读数。

🔴 **本模块只有四个数字常量,四个**全部**是 2026-08-12 用户裁定值**(§七 P3-69/P3-70),
⛔ 工程侧一个都不许改、也不许再加第五个:
    · `HISTORY_LOOKBACK_TRADING_DAYS = 20`(最近 20 个有效交易日)
    · `HISTORY_LOOKBACK_MAX_CALENDAR_DAYS = 60`(最多回溯 60 个自然日补齐)
    · `HISTORY_MIN_SAMPLE_FOR_COMPARISON = 15`(n ≥ 15 才允许形成历史比较)
    · `auction.SECTOR_PEER_MIN = 3`(板块对照股至少 3 只才取中位数)
⚠ 其中 **15 是本层第一个「用户拍板的机械判据阈值」**:「历史样本够不够」**不再交 LLM 判**,
机械侧先判好、短摘要与 prompt 逐字写明「样本不足 → 只展示原始值,⛔ 不得据此做比较结论」。

**每一项读数都必须指出「这个数从哪来」**(§五 ②-D 那张表):
高开偏离与竞价量能直接复用 `sentinel/precall.py` 的既有阈值与既有纯函数;
「触发 D0 明确失效位置」用卡上**冻结**的 `close_below_stop_line`(D0 已过闸);
`plan_fit` 五态全部来自卡上冻结的 `entry_zone` / `max_chase`;涨跌分界用 `_EPS` 容差
(`sentinel/holding.py` 的既有工程不变量,不是策略阈值)。

🔴 **`hit_invalidation` / `gap_up_deviation` 是三态**:`True` 命中 / `False` 看过了没
命中 / **`None` 没判**,且 `None` 一律**同时**带一个原因码
(`UNDETERMINED_CODES`:卡上无冻结价位 / 开盘价未发布 / 锚失效 / 有篮无卡 / 没抓到)。
⛔ **绝不许把 `None` 折成 `False`「没问题」**(V2.3.3 复审 🔴-1 逮到过一次):
`precall` 的两个 judge 在「真的没命中」「卡上没这个价位」「`open<=0`」三种情况下都返回
`None`,`is not None` 会把后两种一起讲成「核对过了、没事」—— 这是本项目一贯的
「『没有』≠『不满足』」纪律(同 `member_ex_rights` / ⑧-E `anchor_unconfirmed` /
夹逼四态的 `rejected_no_close`)。所以**前置条件判在本模块**,`judge_*` 的 `None`
只承载「真的没命中」这一种含义。

⚠ 锚失效(疑似除权除息)时两项一律 `None` + 原因码 `anchor_stale` —— 同 `precall`
的既定纪律:锚失效时那是**错的比较**(除权日开盘价比冻结止损线低一大截是尺度问题,
不是破位)。

🔴 **`rel_to_sector` 与 `rel_to_index` 是两条互不相干的路径**(用户裁定 P3-70):
前者减**板块基准**(板块指数 → 取不到 → ≥3 只同行业对照股中位数),后者减**市场指数**
(沪主板 000001.SH / 深主板 399001.SZ / 创业板 399006.SZ / 北交所 899050.BJ;
**科创板按 K8 §三 排除** → `None` + `board_excluded`,⛔ 不 fallback)。
⛔ **禁止同源同值**、⛔ **禁止用市场指数代替板块基准**、「三支指数等权平均」**正式停用**。
两个读数的 `None` 同样各配一个原因码 —— ⛔ 「取不到」不许被读成 0 或「持平」。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.auction import (
    CRIT_FROZEN_ANCHOR,
    CRIT_MARKET_BENCHMARK,
    CRIT_MEMBER_QUOTE,
    CRIT_SECTOR_BENCHMARK,
    DQ_DEGRADED,
    DQ_INSUFFICIENT,
    NOTE_INDUSTRY_MAP_UNAVAILABLE,
    PLAN_FIT_ABOVE_MAX_CHASE,
    PLAN_FIT_ABOVE_ZONE_BELOW_CHASE,
    PLAN_FIT_BELOW_ZONE,
    PLAN_FIT_IN_ZONE,
    PLAN_FIT_UNKNOWN,
    REL_UNDET_BOARD_EXCLUDED,
    REL_UNDET_DATA_INSUFFICIENT,
    REL_UNDET_INDUSTRY_MAP_UNAVAILABLE,
    REL_UNDET_NO_BOARD_META,
    REL_UNDET_NO_INDEX_QUOTE,
    REL_UNDET_NO_INDUSTRY,
    REL_UNDET_NO_MEMBER_GAP,
    RISK_ANCHOR_STALE,
    RISK_AUCTION_VOLUME,
    RISK_DATA_MISSING,
    RISK_GAP_UP_DEVIATION,
    RISK_HIT_INVALIDATION,
    RISK_INVALIDATION_UNDETERMINED,
    RISK_QUOTE_INVALID,
    RISK_SOURCE_DEGRADED,
    RISK_SOURCE_CONFLICT,
    SECTOR_BENCH_PEER_MEDIAN,
    SECTOR_BENCH_UNAVAILABLE,
    SECTOR_PEER_MIN,
    UNDET_ANCHOR_STALE,
    UNDET_NO_MEMBER_SCRIPT,
    UNDET_NO_OPEN_PRICE,
    UNDET_NO_QUOTE,
    UNDET_NO_REF_CLOSE,
    UNDET_NO_STOP_LINE,
    UNDET_QUOTE_INVALID,
)
from neckline.auction.collect import AuctionSnapshot, gap_pct_of
from neckline.auction.quality import detect_conflict, worse_of
from neckline.calendar import trading_days_between
from neckline.selection.basket_store import BasketRef, load_basket_card
from neckline.sentinel.precall import (
    MemberScript,
    judge_auction_volume,
    judge_gap_up_invalidate,
    judge_low_open_falsify,
    load_member_scripts,
    member_anchor_stale,
)

logger = logging.getLogger(__name__)

#: 浮点容差(工程不变量,照 `sentinel/holding.py` 既有体例)。`> 0` 是涨跌的**自然
#: 分界**,不是发明的阈值 —— 加容差只是不让二进制浮点噪声把"平开"读成"上涨"。
_EPS = 1e-9

# ══════════════════════════════════════════════════════════════════════════
# 🔴 「自身历史对照」的窗口与样本判据 —— **三个数全部是用户裁定值**
#    (2026-08-12,§七 **P3-69**;⛔ 工程侧不许改、不许"顺手"加第四个数)
#
#    裁定原文:「采用**最近 20 个有效交易日,最多向前回溯 60 个自然日补齐**;
#    不使用自然日直接计数;不使用季度或全部历史;当期有效样本 `n ≥ 15`,允许形成
#    历史比较;`n < 15`,标记『历史样本不足』,只展示原始值;当日竞价不进入自身历史基线。」
# ══════════════════════════════════════════════════════════════════════════

#: 🔴 **裁定值**:回看**最近 20 个有效交易日**(单位 = 交易日,走
#: `neckline.calendar` 的交易日历,⛔ 不许自己数自然日)。
HISTORY_LOOKBACK_TRADING_DAYS = 20

#: 🔴 **裁定值**:最多向前回溯 **60 个自然日**补齐 —— 这是**回溯上界**(防止长假 / 停牌
#: 把扫描区间拖长,§七 P0-23 的原教旨),**不是计数单位**。
HISTORY_LOOKBACK_MAX_CALENDAR_DAYS = 60

#: 🔴 **裁定值,且这是本层第一个「用户拍板的机械判据阈值」**:当期有效样本
#: `n ≥ 15` → 允许形成历史比较;`n < 15` → 标「历史样本不足」+ **只展示原始值**。
#: ⚠ 它**取代**了原先「够不够交 LLM 判」的设计 —— 现在由机械侧判,喂 LLM 的短摘要与
#: system prompt 都会写明「本项样本不足,⛔ 不得据此做比较结论」。
HISTORY_MIN_SAMPLE_FOR_COMPARISON = 15

#: 回看窗口的诚实披露(**文案单一源**,随 `history_json` 下发 + 进 LLM 短摘要)。
#: ⛔ 客户端不许自己写这句(同 `AUCTION_PROXY_SAMPLE_NOTE` 既有体例);
#: 🔴 ⛔ 这段字里不许有 Markdown(它作为 `String` 下发,`Text(String)` 不解析)。
HISTORY_LOOKBACK_NOTE = (
    f"这一项回看最近 {HISTORY_LOOKBACK_TRADING_DAYS} 个有效交易日的竞价快照"
    f"(最多向前回溯 {HISTORY_LOOKBACK_MAX_CALENDAR_DAYS} 个自然日补齐),不是全史;"
    f"当日竞价不进入自身历史基线。"
    # 🔴 逐票各算各的(定向复审 🔴-1):这一句不写,篮级那个数会被读成"每只票都有这么多天"。
    f"样本天数逐票各算各的,篮级显示的是其中「最少」的那一只。"
)

#: 「历史样本不足」的诚实披露(**文案单一源**,篮内**有票**不够时随 `history_json` 下发)。
#: 🔴 ⛔ 这段字里不许有 Markdown(同上)。
#: ⚠ 判据是**逐票**的(定向复审 🔴-1):篮级只是「最少的那一只」,⛔ 不是全篮并集。
HISTORY_SAMPLE_INSUFFICIENT_NOTE = (
    f"本篮至少有一只成员的当期有效样本不足 {HISTORY_MIN_SAMPLE_FOR_COMPARISON} 天,"
    f"这些成员按「历史样本不足」处理:只展示原始值,不形成历史比较结论"
    f"(样本够的成员不受影响,逐票各判各的)。"
)

#: 🔴 **上市板块对照指数**那一组的标签与说明(定向复审 🟡-2 的落点,文案单一源)。
#: ⛔ 这一组**不是**「板块基准」:它按上市板块(主板 / 创业板 / 科创板 / 北交所)取指数,
#: 主板票落到的就是市场指数本身,而裁定 ④ 明令「禁止使用市场指数代替板块基准」。
#: 🔴 ⛔ 这两段字里不许有 Markdown(随契约下发,`Text(String)` 不解析)。
_LISTING_BOARD_BENCH_LABEL = "所属上市板块对照指数(主板票即市场指数本身,不是本次的板块基准)"
LISTING_BOARD_BENCHMARK_NOTE = (
    "这一组是各成员「所属上市板块」(主板 / 创业板 / 科创板 / 北交所)的对照指数,"
    "主板票落到的就是市场指数本身。它只用于描述上市板块环境,"
    "不是本次的「板块基准」—— 板块基准走的是同行业对照股中位数,见「相对板块」那一项。"
)

#: 🔴 板块对照股取样域的诚实披露(**文案单一源**,随 `rel_strength_json` 下发)。
#: 同 `AUCTION_PROXY_SAMPLE_NOTE` 的既定纪律:代理样本必须**印在报告上**,
#: ⛔ 不许只写在代码注释里 —— 否则「对照不足」会被读成「这个板块没别的票在动」。
#: 🔴 ⛔ 这段字里不许有 Markdown。
# 🔴 **2026-08-12 用户裁定 ① 之后这段话整段改写**:取样域由「盘中关注池(上界 29 只、
# 偏向当天最强的一批票)」换成竞价层**自己的独立观察池** —— 行业层是该行业的**全部
# 成分股**(完整取样),所以旧版那条「中位数系统性偏高」的偏差警告**不再成立**,
# 一并删除。⛔ 别照旧文案改回去:留着一句已经不成立的偏差警告,和藏起一条真的偏差
# 一样有害。⚠ 「凑不满 3 只」仍会出现(该行业当天可用读数不够),但它不再是常态。
SECTOR_PEER_POOL_NOTE = (
    f"「板块对照股」取自系统当天的「竞价观察池」,按同行业(股票基础资料的行业口径)"
    f"挑出、并排除本篮成员;凑不满 {SECTOR_PEER_MIN} 只就如实标「对照不足」,"
    f"不改用市场指数顶替。"
    f"观察池的行业层是该行业的全部成分股(没有截断),因此这个中位数是该行业当天"
    f"可取得读数的中位水平,不是一批强票的中位水平。"
)


@dataclass
class MemberReading:
    """逐票读数(键表 = §五 ②-F,客户端逐票行按这些字段画)。

    🔴 **一律发枚举码,中文换算在客户端做**(CLAUDE.md 连踩三次的坑:`role · leader` /
    `pullback_leader` 都是把码直接印出来造成的)。
    """

    ts_code: str
    name: str = ""
    role: Optional[str] = None            # 卡上 members[].role_llm(枚举码,⛔ 不换中文)
    auction_price: Optional[float] = None
    pre_close: Optional[float] = None
    gap_pct: Optional[float] = None
    auction_volume: Optional[float] = None
    auction_amount: Optional[float] = None
    vol_vs_prev5_frac: Optional[float] = None
    # 🔴 **两条独立路径,⛔ 禁止同源同值**(用户裁定 P3-70,2026-08-12)。
    # `None` = **取不到**,⛔ 不是 0、⛔ 不是「持平」—— 必配一个 `*_reason` 原因码。
    rel_to_sector: Optional[float] = None
    rel_to_index: Optional[float] = None
    #: `rel_to_sector` 走的是哪条路径(`SECTOR_BENCH_*`)。⛔ 走不了 ① 就如实标 ②/③,
    #: **不许假装走了板块指数**。
    rel_to_sector_source: str = SECTOR_BENCH_UNAVAILABLE
    #: `rel_to_sector is None` 时的原因码(`REL_UNDETERMINED_CODES` 之一)。
    rel_to_sector_reason: Optional[str] = None
    #: ② 路径的**对照股清单**(读者不必猜"减的是哪一组")。
    sector_peer_codes: List[str] = field(default_factory=list)
    #: ① 路径用到的板块指数码(本版恒 `None`,理由见 `sector_benchmark_of`)。
    sector_index_code: Optional[str] = None
    #: 板块基准本身的竞价涨跌幅(中位数 / 板块指数 gap);取不到 → `None`。
    sector_benchmark_gap_pct: Optional[float] = None
    #: 该票的行业(`stock_basic.industry`)= 板块对照股的取样口径,查不到 → `None`。
    industry: Optional[str] = None
    #: `rel_to_index` 减的是**哪一支市场指数**(裁定的四条映射);科创板恒 `None`。
    index_benchmark_code: Optional[str] = None
    #: 那支市场指数本身的竞价涨跌幅;拉不到 → `None`。
    index_benchmark_gap_pct: Optional[float] = None
    #: `rel_to_index is None` 时的原因码(科创板 = `board_excluded`)。
    rel_to_index_reason: Optional[str] = None
    # 🔴 **三态**:`True` 命中 / `False` 看过了没命中 / `None` **没判**。
    # ⛔ `None` 绝不许折成 `False`「没问题」—— 那是把「一个字都没核对」讲成「核对过了」。
    # ⚠ `None` 必须**同时**带一个原因码(下面两个字段),光有 `None` 读者还是只能猜。
    hit_invalidation: Optional[bool] = None
    gap_up_deviation: Optional[bool] = None
    #: `hit_invalidation is None` 时的原因码(`UNDETERMINED_CODES` 之一);判出来了 → `None`。
    hit_invalidation_undetermined_reason: Optional[str] = None
    #: `gap_up_deviation is None` 时的原因码;判出来了 → `None`。
    gap_up_deviation_undetermined_reason: Optional[str] = None
    anchor_stale: bool = False
    plan_fit: str = PLAN_FIT_UNKNOWN
    data_quality: str = DQ_INSUFFICIENT
    #: 竞价量能附注原文(`precall.judge_auction_volume` 的既有文案,阈值一字未改)。
    volume_note: Optional[str] = None
    # ── 🔴 V2.4.0 P2.1 / P2.2:这条读数**从哪来、是不是今天的、两源打不打架** ──────
    #: 双源核验后的可用状态(`fresh` | `insufficient` | `conflict`)。
    quote_freshness: str = ""
    #: 七项校验的主因状态(`QUOTE_STATUSES` 之一)。空串 = 老快照没记这一位。
    quote_status: str = ""
    #: 选用的那一源(`sina` / `tencent`)。⛔ `None` 不是"新浪",是**两源都没读数**。
    quote_source: Optional[str] = None
    #: 源自带的时刻(归一后)。⚠ 与 `captured_at`(本机抓取时刻)是**两个不同的量**。
    quote_ts: Optional[str] = None
    #: 主源不可用、改用了备源(K8 §二十「记录来源降级」)。⛔ 不许静默换源。
    source_degraded: bool = False
    #: 结论性冲突码(`CONFLICT_*`);`None` = 没冲突**或**没有第二个读数可比。
    #: ⚠ 后者要靠 `quote_freshness` / `validation_errors` 分辨,⛔ 别把 `None` 读成"已核对"。
    source_conflict: Optional[str] = None
    #: 七项校验里**失败了哪几项**(两源并集)。空 = 全过(或老快照没记)。
    validation_errors: List[str] = field(default_factory=list)

    @property
    def has_undetermined_invalidation(self) -> bool:
        """这只票的两项失效判定里**至少有一项没判**。"""
        return self.hit_invalidation is None or self.gap_up_deviation is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_code": self.ts_code, "name": self.name, "role": self.role,
            "auction_price": self.auction_price, "pre_close": self.pre_close,
            "gap_pct": self.gap_pct, "auction_volume": self.auction_volume,
            "auction_amount": self.auction_amount,
            "vol_vs_prev5_frac": self.vol_vs_prev5_frac,
            "rel_to_sector": self.rel_to_sector, "rel_to_index": self.rel_to_index,
            "rel_to_sector_source": self.rel_to_sector_source,
            "rel_to_sector_reason": self.rel_to_sector_reason,
            "sector_peer_codes": list(self.sector_peer_codes),
            "sector_index_code": self.sector_index_code,
            "sector_benchmark_gap_pct": self.sector_benchmark_gap_pct,
            "industry": self.industry,
            "index_benchmark_code": self.index_benchmark_code,
            "index_benchmark_gap_pct": self.index_benchmark_gap_pct,
            "rel_to_index_reason": self.rel_to_index_reason,
            "hit_invalidation": self.hit_invalidation,
            "gap_up_deviation": self.gap_up_deviation,
            "hit_invalidation_undetermined_reason": self.hit_invalidation_undetermined_reason,
            "gap_up_deviation_undetermined_reason": self.gap_up_deviation_undetermined_reason,
            "anchor_stale": bool(self.anchor_stale),
            "plan_fit": self.plan_fit, "data_quality": self.data_quality,
            "volume_note": self.volume_note,
            "quote_freshness": self.quote_freshness, "quote_status": self.quote_status,
            "quote_source": self.quote_source, "quote_ts": self.quote_ts,
            "source_degraded": bool(self.source_degraded),
            "source_conflict": self.source_conflict,
            "validation_errors": list(self.validation_errors),
        }


@dataclass
class BasketMech:
    """一个篮子的全部机械读数。⛔ 这里**没有** `verdict` —— 结论是 LLM 的活。"""

    basket_id: int
    basket_key: str
    name: str
    covered_tier: int
    engine_code: Optional[str] = None
    engine_version: Optional[str] = None
    skeleton_version: str = ""
    regime_at_d0: Optional[str] = None
    #: 🔴 **V2.4.0 P2.3 起本字段 = 关键域质量**(K8 §二十「数据质量分域」;
    #: 施工图 §五 P2.3「对外字段 `data_quality` 保留,兼容含义改为『关键域质量』」)。
    #: ⚠ V2.3.3 及更早的行里它是**整体**质量 —— 老行靠 `critical_data_quality` 为
    #: NULL 分辨(客户端显示「旧版本未细分」,⛔ 不得默认成正常)。
    data_quality: str = DQ_INSUFFICIENT
    #: 关键域质量(= `data_quality`,显式命名的那一份)。**只有它夹逼结论**。
    critical_quality: str = DQ_INSUFFICIENT
    #: 上下文域质量。🔴 **降级只降置信度 + 披露缺失,⛔ 不夹逼篮子结论**
    #: —— 「一只无关指数缺失导致整篮强制中性」正是本版要修的病。
    context_quality: str = DQ_INSUFFICIENT
    #: 两域的**逐项账**(哪些码进了关键域 / 上下文域、各自缺了什么、冲突在哪)。
    #: 落 `auction_verdicts.quality_detail_json`。
    quality_detail: Dict[str, Any] = field(default_factory=dict)
    members: List[MemberReading] = field(default_factory=list)
    sector_sync: Dict[str, Any] = field(default_factory=dict)
    rel_strength: Dict[str, Any] = field(default_factory=dict)
    history: Dict[str, Any] = field(default_factory=dict)
    hit_invalidation_codes: List[str] = field(default_factory=list)
    plan_consistency: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


@dataclass
class MarketMech:
    """市场级读数(小报告第 1 / 2 / 4 块的机械半份)。"""

    source: str = "unknown"
    captured_at: str = ""
    requested_codes: int = 0
    fetched_codes: int = 0
    missing_codes: List[str] = field(default_factory=list)
    #: 🔴 V2.4.0 P2.1:抓到了、但七项校验没过的码(⛔ 与 `missing_codes` 分开)。
    invalid_codes: List[str] = field(default_factory=list)
    conflict_codes: List[str] = field(default_factory=list)
    #: ⚠ **市场级 `data_quality` 仍是「整体覆盖率读数」,刻意没有收窄成关键域**
    #: (设计判断,如实登记):它不驱动任何夹逼闸(闸 1 读的是**篮子级**的),而
    #: 「跑过了、D0 当天就没有 T1/T2 篮子」那一早市场级质量本来就该照常判 ok ——
    #: 收窄会让那种早晨凭空变成「关键域数据不足」。分域读数见下面两个字段。
    data_quality: str = DQ_INSUFFICIENT
    #: 🔴 报告级分域质量 = **各篮子取更差的那个**;`None` = **本次没有篮子**,
    #: 关键域无从谈起(⛔ 不拿 `ok` 冒充,也⛔ 不拿 `insufficient` 吓人)。
    critical_quality: Optional[str] = None
    context_quality: Optional[str] = None
    #: 逐票双源核验的完整账(落 `auction_reports.quote_quality_json`)。
    quote_quality: Dict[str, Any] = field(default_factory=dict)
    #: 🔴 **独立观察池的账 + 那句「观察范围」自述**(2026-08-12 用户裁定 ①,
    #: 落 `auction_reports.observation_json`)。⚠ 空 dict = **这一版还没有独立
    #: 观察池这个概念**(老行)或本次组池失败,⛔ 不是「观察范围正常」。
    observation: Dict[str, Any] = field(default_factory=dict)
    index_gaps: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    anchors: List[Dict[str, Any]] = field(default_factory=list)
    risks: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class AuctionMech:
    trade_date: date
    d0_date: date
    market: MarketMech
    baskets: List[BasketMech] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════
# 逐票读数
# ══════════════════════════════════════════════════════════════════════════

def plan_fit_of(
    price: Optional[float], entry_zone: Optional[Mapping[str, Any]], max_chase: Optional[float],
) -> str:
    """开盘价 vs **卡上冻结**的建仓区间与最高追价 → 五态。**零阈值**:每个边界都是
    D0 那天已经过完夹逼闸的数字,这里只做比较。

    卡上没给区间(夹逼拒收 / LLM 没给)或价拿不到 → `unknown`,⛔ 不拿 `in_zone` 冒充。
    """
    try:
        p = float(price) if price is not None else None
    except (TypeError, ValueError):
        p = None
    if not p or p <= 0 or not isinstance(entry_zone, Mapping):
        return PLAN_FIT_UNKNOWN
    low, high = entry_zone.get("low"), entry_zone.get("high")
    try:
        lo = float(low) if low is not None else None
        hi = float(high) if high is not None else None
    except (TypeError, ValueError):
        return PLAN_FIT_UNKNOWN
    if lo is None or hi is None:
        return PLAN_FIT_UNKNOWN
    if p < lo - _EPS:
        return PLAN_FIT_BELOW_ZONE
    if p <= hi + _EPS:
        return PLAN_FIT_IN_ZONE
    try:
        chase = float(max_chase) if max_chase is not None else None
    except (TypeError, ValueError):
        chase = None
    if chase is None:
        # 高于区间但卡上没给最高追价 → 「高于区间」这件事本身是知道的,但**追不追得起**
        # 判不了。⛔ 不猜一个上限:如实报 `above_zone_below_chase` 会撒谎,故报 unknown。
        return PLAN_FIT_UNKNOWN
    return PLAN_FIT_ABOVE_MAX_CHASE if p > chase + _EPS else PLAN_FIT_ABOVE_ZONE_BELOW_CHASE


def _positive(v: Any) -> bool:
    """`> 0` 且是个数(⛔ 不把 `None` / 空串当 0 用)。"""
    try:
        return float(v or 0.0) > 0
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class SectorBenchmark:
    """`rel_to_sector` 的**板块基准**:走的哪条路径、减的是什么、取不到时为什么。"""

    source: str                                  # `SECTOR_BENCH_*`
    value: Optional[float] = None                # 基准的竞价涨跌幅(⛔ 取不到就是 None,不是 0)
    index_code: Optional[str] = None             # ① 板块指数码(本版恒 None)
    peer_codes: Tuple[str, ...] = ()             # ② 对照股清单
    industry: Optional[str] = None               # 取样口径(`stock_basic.industry`)
    reason: Optional[str] = None                 # `value is None` 时的原因码


def sector_benchmark_of(
    code: str, snap: AuctionSnapshot, *, exclude_codes: Sequence[str] = (),
) -> SectorBenchmark:
    """`rel_to_sector` 的基准 —— 🔴 **一条与 `rel_to_index` 完全独立的路径**
    (用户裁定 P3-70,2026-08-12:「分开计算,禁止同源同值」)。

    裁定给的三条路径,逐条落地:

    **① 优先使用 D0 主要驱动对应的板块指数** —— 🔴 **本版取不到,故恒不走这条**:
    系统里的板块指数是同花顺板块指数(`ths_index`,代码形如 `700005.TI`),而 9:26 的
    实时行情走 `sentinel/quotes.py`(新浪 / 腾讯),`to_symbol()` 只认 `.SH/.SZ/.BJ`
    —— `.TI` 会被前缀启发式拉成 `sz700005`(**另一个标的**)。**这不是 bug,是数据现实**;
    ⛔ 但也**绝不许假装走了 ①**:`source` 如实标 `peer_median` / `unavailable`。
    (将来若出现可得的板块指数实时源,在这里补 ① 并把 `source` 标成 `sector_index`。)

    **② ≥3 只有效板块对照股竞价涨跌幅的中位数** —— 取数域 = 关注池里与该票
    **同行业**(`stock_basic.industry`,本仓钉死的行业口径、`stock_persist_days` 同源)
    且**不属于本篮**的票。排除本篮成员是因为:基准必须独立于被检验的那个假设,
    拿篮内同伴当"板块"等于自己给自己打分。
    ⚠ **取数域是盘中关注池 = 代理样本,不是该行业的全部成分股**(9:26 那一拍只有池里
    这些票有报价,拉全市场是 `DEFAULT_BREADTH_CAP` 挡着的既有取舍)—— 所以「凑不满 3 只」
    在真实早晨会**常态出现**。这句话随产物下发(`SECTOR_PEER_POOL_NOTE`),⛔ 不许只写在
    注释里:否则「对照不足」会被读成「这个行业没别的票在动」。

    **③ 对照不足 → `None` + `data_insufficient`** —— ⛔ 不是 0、⛔ 不是省略键。
    ⚠ 「连行业口径都查不到」另有**两个**码,⛔ 三者一律不折平(「『没有』≠『不满足』」):
      · `no_industry` —— 行业表读到了,**这一只票**在 `stock_basic` 里没登记行业;
      · `industry_map_unavailable` —— **整张行业表都没读到**(`load_industry_map` 抛异常),
        那是**系统缺席**、不是关于这只票的任何判断(P0-39 纪律,复审 🔵-7)。
        判据 = 报告级 note `NOTE_INDUSTRY_MAP_UNAVAILABLE`(唯一源在 `auction/__init__.py`)。

    **④ ⛔ 禁止使用市场指数代替板块基准** —— 做成**结构性保证**:本函数的取样域是
    `snap.industry_of`,它只由 `stock_basic` 派生 → **指数码根本不在里面**;再叠一层
    `snap.index_codes` 显式排除。另有守门单测正面钉死(基准码恒不是那几支指数)。
    """
    industry = (snap.industry_of or {}).get(code)
    if not industry:
        # 🔵-7:整张行业表读不到(系统缺席)与「这一只票没登记行业」是两种成因,
        # ⛔ 不折平成同一个码。
        reason = (REL_UNDET_INDUSTRY_MAP_UNAVAILABLE
                  if NOTE_INDUSTRY_MAP_UNAVAILABLE in (snap.notes or ())
                  else REL_UNDET_NO_INDUSTRY)
        return SectorBenchmark(source=SECTOR_BENCH_UNAVAILABLE, reason=reason)
    skip = set(exclude_codes) | {code} | set(snap.index_codes)
    peers: List[Tuple[str, float]] = []
    for peer, ind in (snap.industry_of or {}).items():
        if peer in skip or ind != industry:
            continue
        g = snap.gap_of(peer)
        if g is not None:                       # 「有效」= 这只对照股本次真有竞价涨跌幅
            peers.append((peer, g))
    peers.sort(key=lambda t: t[0])              # 确定性顺序(⛔ 别让字典序决定产物)
    if len(peers) < SECTOR_PEER_MIN:
        return SectorBenchmark(source=SECTOR_BENCH_UNAVAILABLE,
                               reason=REL_UNDET_DATA_INSUFFICIENT, industry=industry,
                               peer_codes=tuple(c for c, _g in peers))
    return SectorBenchmark(source=SECTOR_BENCH_PEER_MEDIAN,
                           value=_median(sorted(g for _c, g in peers)),
                           industry=industry, peer_codes=tuple(c for c, _g in peers))


# ══════════════════════════════════════════════════════════════════════════
# 🔴 三态判定的**唯一实现**(V2.4.0 P2.2 抽出:逐票读数与双源冲突判定共用一份)
#
# ⚠ 抽出来的理由不是"复用",是**语义必须完全一致**:双源冲突判的是「一源触发、
#    另一源不触发」,而 `precall` 的两个 judge 在「真没命中 / 卡上没这个价位 /
#    `open<=0` / 锚失效」四种情况下都返回 `None` —— 拿 `is not None` 去比两源,
#    会把「一边判不了」讲成「两边看法不同」(`CLAUDE.md`「三态字段」那条坑)。
#    前置条件必须判在调用方,而调用方现在有两个,所以它得有一份。
# ══════════════════════════════════════════════════════════════════════════

def hit_invalidation_tristate(
    script: Optional[MemberScript], q: Any,
) -> Tuple[Optional[bool], Optional[str]]:
    """`(命中 D0 冻结失效位?, 没判的原因码)`。

    `True` 命中 / `False` 看过了没命中 / `None` **没判**(⛔ `None` 绝不是「没问题」)。
    判据取 D0 **冻结**值,判断本身复用 `precall.judge_low_open_falsify`,⛔ 不抄第二份。
    """
    if q is None:
        return None, UNDET_NO_QUOTE
    if script is None:
        return None, UNDET_NO_MEMBER_SCRIPT
    if member_anchor_stale(script, q):
        # 锚失效(疑似除权除息)→ 一律不判:除权日开盘价比冻结止损线低一大截是
        # **尺度问题不是破位**(`precall` 的既定纪律)。
        return None, UNDET_ANCHOR_STALE
    if script.stop_line is None:
        return None, UNDET_NO_STOP_LINE
    if not _positive(getattr(q, "open", None)):
        return None, UNDET_NO_OPEN_PRICE
    return (judge_low_open_falsify(script, q) is not None), None


def gap_up_deviation_tristate(
    script: Optional[MemberScript], q: Any,
) -> Tuple[Optional[bool], Optional[str]]:
    """`(高开已偏离 D0 冻结锚?, 没判的原因码)`。三态语义同上。"""
    if q is None:
        return None, UNDET_NO_QUOTE
    if script is None:
        return None, UNDET_NO_MEMBER_SCRIPT
    if member_anchor_stale(script, q):
        return None, UNDET_ANCHOR_STALE
    if script.ref_close is None:
        return None, UNDET_NO_REF_CLOSE
    if not _positive(getattr(q, "open", None)):
        return None, UNDET_NO_OPEN_PRICE
    return (judge_gap_up_invalidate(script, q) is not None), None


def plan_entered_tristate(
    q: Any, entry_zone: Optional[Mapping[str, Any]], max_chase: Optional[float],
) -> Optional[bool]:
    """`True` 进入或突破 D0 预案区间 / `False` 还在区间下方 / `None` **判不了**
    (卡上没给区间 or 价拿不到)。K8 §二十 冲突第 ③ 类的判据,**零阈值**:
    每个边界都是 D0 那天已经过完夹逼闸的数字,这里只做比较。"""
    if q is None:
        return None
    fit = plan_fit_of(getattr(q, "price", None), entry_zone, max_chase)
    if fit == PLAN_FIT_UNKNOWN:
        return None
    return fit != PLAN_FIT_BELOW_ZONE


def _member_entry_of(card: Optional[Mapping[str, Any]], code: str) -> Dict[str, Any]:
    for m in ((card or {}).get("members") or []):
        if isinstance(m, Mapping) and m.get("ts_code") == code:
            return dict(m)
    return {}


def build_member_reading(
    code: str,
    snap: AuctionSnapshot,
    *,
    script: Optional[MemberScript] = None,
    card_entry: Optional[Mapping[str, Any]] = None,
    basket_member_codes: Sequence[str] = (),
) -> MemberReading:
    """一只票的全部机械读数。

    ⚠ **关键字段缺失 → 该票 `data_quality='insufficient'`**,逐票明细里如实标
    「中性｜数据不足」(K8 §二十 逐字)—— 展示层的中文换算在客户端,这里只发码。
    ⚠ `basket_member_codes` = 本篮全部成员,只用于把它们**排除在板块对照股之外**
    (裁定 P3-70 ②:基准必须独立于被检验的假设)。
    """
    from neckline.auction import DQ_INSUFFICIENT as _INSUF, DQ_OK as _OK

    q = snap.quotes.get(code)
    meta = snap.meta.get(code)
    entry = dict(card_entry or {})
    reading = MemberReading(
        ts_code=code,
        name=(entry.get("name") or (meta.name if meta is not None else "") or code),
        role=(entry.get("role_llm") or None),
    )
    # 🔴 V2.4.0 P2.1/P2.2:先把「这条读数从哪来、是不是今天的」记上 —— 它在
    # 「读数能不能用」之前,也在任何判定之前。
    qq = (snap.quote_quality or {}).get(code)
    if qq is not None:
        reading.quote_freshness = qq.freshness
        reading.quote_status = qq.status
        reading.quote_source = qq.chosen_source
        reading.quote_ts = qq.src_ts
        reading.source_degraded = bool(qq.source_degraded)
        reading.source_conflict = qq.conflict
        reading.validation_errors = list(qq.errors)
    usable = snap.is_usable(code)
    if q is None or not usable:
        # 🔴 **两种成因,两个原因码**(⛔ 不折平):
        #   · `no_quote`      两源都没拉到 —— 网络 / 限流问题;
        #   · `quote_invalid` 抓到了、但七项校验没过(过期 / 时间戳解不出 / 字段无效)
        #     —— ⛔ **不拿它去判失效位**:用昨天的收盘价跟 D0 冻结止损线比,必然
        #     得到一条看起来很像真的假警报,那比不判更糟。
        # ⚠ 逐票读数(价 / 量 / 额 / 涨跌幅)在这一支上**一律不填** —— 原始数字没有
        #    丢,它在 `quote_quality[code].checks` 里逐字留着(K8:两源原始读数全部留存);
        #    ⛔ 但不许让一份不合格的读数从这里"洗白"成今天的竞价读数。
        # ⚠ 判据取**逐票账里有没有读数**,⛔ 不取 `q is None`:不合格的读数根本
        #    不会进 `snap.quotes`(那是刻意的 —— 免得它被派生成"今天的"涨跌幅),
        #    所以那时 `q` 也是 `None`。两种成因靠 `checks` 有没有内容分辨。
        had_reading = bool(qq is not None and qq.checks)
        why = UNDET_QUOTE_INVALID if had_reading else UNDET_NO_QUOTE
        reading.hit_invalidation_undetermined_reason = why
        reading.gap_up_deviation_undetermined_reason = why
        return reading
    price = getattr(q, "price", None)
    pre_close = getattr(q, "pre_close", None)
    reading.auction_price = float(price) if price else None
    reading.pre_close = float(pre_close) if pre_close else None
    reading.gap_pct = gap_pct_of(price, pre_close)
    vol = getattr(q, "volume", None)
    amt = getattr(q, "amount", None)
    reading.auction_volume = float(vol) if vol is not None else None
    reading.auction_amount = float(amt) if amt is not None else None
    base = float(snap.prev5_avg_volume.get(code) or 0.0)
    if base > 0 and reading.auction_volume:
        # ⚠ 量纲不同的**诚实局限**(竞价量是"开盘一撮"的量 vs 全天日均量)已写在
        # `precall.py` 模块头,**原样继承、不重写**。
        reading.vol_vs_prev5_frac = reading.auction_volume / base
    reading.volume_note = judge_auction_volume(q, base)

    # ── 相对强弱 = **减法,零阈值**;「明显掉队」的判定交 LLM ────────────────────
    #
    # 🔴 **两条路径完全分开,⛔ 禁止同源同值**(用户裁定 P3-70,2026-08-12)。
    # 「三支指数等权平均」**正式停用**(裁定原文);单指数那一版也已被这次裁定取代
    # —— 现在 `rel_to_index` 走**市场指数**的四条映射、`rel_to_sector` 走**板块对照股**
    # 中位数,两个数来自两个不同的基准。
    # 🔴 每一个 `None` 都必配一个原因码:「取不到」⛔ 不许被读成 0 或「持平」。

    # ① 相对市场:该票 `gap_pct` − **对应市场指数** `gap_pct`(科创板按 K8 §三 排除)。
    idx_code = (snap.market_index_of or {}).get(code)
    reading.index_benchmark_code = idx_code
    if reading.gap_pct is None:
        reading.rel_to_index_reason = REL_UNDET_NO_MEMBER_GAP
    elif not idx_code:
        # 科创板 → `board_excluded`(⛔ 绝不 fallback 到别的指数);查不到板块 → `no_board_meta`。
        reading.rel_to_index_reason = (
            (snap.market_index_undetermined or {}).get(code) or REL_UNDET_NO_BOARD_META)
    else:
        ig = snap.gap_of(idx_code)
        if ig is None:
            reading.rel_to_index_reason = REL_UNDET_NO_INDEX_QUOTE
        else:
            reading.index_benchmark_gap_pct = ig
            reading.rel_to_index = reading.gap_pct - ig

    # ② 相对板块:该票 `gap_pct` − **板块基准**(板块指数 → 取不到 → 对照股中位数)。
    sb = sector_benchmark_of(code, snap, exclude_codes=basket_member_codes)
    reading.rel_to_sector_source = sb.source
    reading.sector_peer_codes = list(sb.peer_codes)
    reading.sector_index_code = sb.index_code
    reading.industry = sb.industry
    if reading.gap_pct is None:
        reading.rel_to_sector_reason = REL_UNDET_NO_MEMBER_GAP
    elif sb.value is None:
        reading.rel_to_sector_reason = sb.reason
    else:
        reading.sector_benchmark_gap_pct = sb.value
        reading.rel_to_sector = reading.gap_pct - sb.value

    # 🔴 **三态,⛔ 不许把「没判」折成 `False`「没问题」**(V2.3.3 复审 🔴-1)。
    # `precall` 的两个 judge 在**四种**情况下都返回 `None`:① 真的没命中;② 卡上没有
    # 该价位;③ `quote.open <= 0`(行情源还没发开盘价);④ 锚失效。`is not None` 会把
    # ②③④ 一起折成 `False` —— 那时小报告会对这只票明确说「未触发失效位」,而真相是
    # **一个字都没核对过**。所以「判不判得了」的前置条件搬到调用方,`judge_*` 的
    # `None` 只承载 ① —— 唯一实现 = 上面那两个 `*_tristate`(V2.4.0 P2.2 抽出,
    # 双源冲突判定吃的是**同一份**,⛔ 不许在那边另写一遍前置条件)。
    reading.anchor_stale = bool(script is not None and member_anchor_stale(script, q))
    reading.hit_invalidation, reading.hit_invalidation_undetermined_reason = (
        hit_invalidation_tristate(script, q))
    reading.gap_up_deviation, reading.gap_up_deviation_undetermined_reason = (
        gap_up_deviation_tristate(script, q))

    reading.plan_fit = plan_fit_of(reading.auction_price, entry.get("entry_zone"),
                                   entry.get("max_chase"))
    reading.data_quality = (
        _OK if (reading.auction_price is not None and reading.pre_close is not None) else _INSUF
    )
    return reading


# ══════════════════════════════════════════════════════════════════════════
# 篮子级汇总
# ══════════════════════════════════════════════════════════════════════════

def sector_sync_of(readings: Sequence[MemberReading], snap: AuctionSnapshot,
                   *, bench_codes: Sequence[str]) -> Dict[str, Any]:
    """板块协同:同向数 + 强弱分布 + **所属上市板块对照指数**读数。**纯描述,零判定。**

    `> _EPS` / `< -_EPS` 是涨跌的**自然分界**(`_EPS` 只是浮点容差),⛔ 不是阈值;
    「协同够不够」交 LLM。

    🔴 **`bench_codes` ⛔ 不是「板块基准」**(定向复审 🟡-2,2026-08-12):它来自
    `snap.benchmark_of` = `sentinel/universe.py::BOARD_BENCHMARK_INDEX`,按**上市板块**
    (主板 / 创业板 / 科创板 / 北交所)取指数 —— **主板票落到的就是市场指数本身**
    (`000001.SH` / `399001.SZ`)。而裁定 P3-70 ④ 明令「⛔ 禁止使用市场指数代替板块基准」,
    本次的板块基准是 `rel_to_sector` 那条路(同行业对照股中位数)。**计算一字未动**,
    改的是**名字**:键 `listing_board_benchmarks` + 一句 `listing_board_benchmarks_note`
    随产物下发,⛔ 谁都别再管它叫「板块基准指数」。
    ⚠ 「板块」在本仓有两个互不相干的含义 —— **上市板块**(这里)与**行业板块**
    (`rel_to_sector`);两者撞名是既有事实,靠这两个键名分开。
    """
    gaps = [(r.ts_code, r.gap_pct) for r in readings if r.gap_pct is not None]
    up = [c for c, g in gaps if g > _EPS]
    down = [c for c, g in gaps if g < -_EPS]
    flat = [c for c, g in gaps if -_EPS <= g <= _EPS]
    vals = sorted(g for _c, g in gaps)
    dist: Dict[str, Any] = {"min": vals[0] if vals else None,
                            "max": vals[-1] if vals else None,
                            "median": _median(vals),
                            "sorted": [{"ts_code": c, "gap_pct": g}
                                       for c, g in sorted(gaps, key=lambda t: (-t[1], t[0]))]}
    return {
        "up_count": len(up), "down_count": len(down), "flat_count": len(flat),
        "observed": len(gaps), "member_count": len(readings),
        "up_codes": up, "down_codes": down,
        "distribution": dist,
        # 🔴 键名如实(🟡-2):**所属上市板块**的对照指数,⛔ 不是本次的板块基准。
        "listing_board_benchmarks": {c: {"gap_pct": snap.gap_of(c)}
                                     for c in dict.fromkeys(bench_codes)},
        "listing_board_benchmarks_label": _LISTING_BOARD_BENCH_LABEL,
        "listing_board_benchmarks_note": LISTING_BOARD_BENCHMARK_NOTE,
    }


def rel_strength_of(readings: Sequence[MemberReading]) -> Dict[str, Any]:
    """候选 vs 板块 / vs 市场(逐票 + 中位)。**减法的汇总,零判定。**

    🔴 逐票必须落下「**减的是哪一支 / 哪一组**」(裁定 P3-70):`index_benchmark_code`
    (哪一支市场指数)+ `sector_benchmark_source`(走的 ① 还是 ②)+ `sector_peer_codes`
    (② 减的是哪几只)。读者不必猜,复盘时也查得到。
    ⚠ **两个中位数分别只统计各自取到的那部分**(`observed_*` 是分母)——
    ⛔ 不拿 0 补齐缺口:那会把「取不到」混进「跟基准一样」。
    """
    sec = [r.rel_to_sector for r in readings if r.rel_to_sector is not None]
    idx = [r.rel_to_index for r in readings if r.rel_to_index is not None]
    sources: Dict[str, int] = {}
    for r in readings:
        sources[r.rel_to_sector_source] = sources.get(r.rel_to_sector_source, 0) + 1
    return {
        "per_member": [{
            "ts_code": r.ts_code,
            "rel_to_sector": r.rel_to_sector,
            "rel_to_index": r.rel_to_index,
            "index_benchmark_code": r.index_benchmark_code,
            "index_benchmark_gap_pct": r.index_benchmark_gap_pct,
            "rel_to_index_reason": r.rel_to_index_reason,
            "sector_benchmark_source": r.rel_to_sector_source,
            "sector_benchmark_gap_pct": r.sector_benchmark_gap_pct,
            "sector_index_code": r.sector_index_code,
            "sector_peer_codes": list(r.sector_peer_codes),
            "industry": r.industry,
            "rel_to_sector_reason": r.rel_to_sector_reason,
        } for r in readings],
        "median_rel_to_sector": _median(sorted(sec)),
        "median_rel_to_index": _median(sorted(idx)),
        "observed_sector": len(sec), "observed_index": len(idx),
        "sector_benchmark_sources": sources,
        "sector_peer_min": SECTOR_PEER_MIN,
        "sector_peer_pool_note": SECTOR_PEER_POOL_NOTE,
    }


def plan_consistency_of(readings: Sequence[MemberReading]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for r in readings:
        counts[r.plan_fit] = counts.get(r.plan_fit, 0) + 1
    return {"counts": counts,
            "per_member": [{"ts_code": r.ts_code, "plan_fit": r.plan_fit} for r in readings]}


@dataclass(frozen=True)
class HistoryIndex:
    """一次 parquet 扫描的产物:`{code: [逐日原始值]}` + 取不到时的原因。

    ⚠ **一次扫描服务全部篮子**(🔵-12):原先逐篮各扫一次同一个区间 —— N 个篮子 =
    N 次 `scan_table_range`,而这条路径跑在**常驻 `neckline.service`** 里(P0-23 语境)。
    """

    per_member: Mapping[str, List[Dict[str, Any]]] = field(default_factory=dict)
    unavailable_reason: Optional[str] = None
    #: 本次实际取数的**交易日清单**(裁定 P3-69 的窗口;⛔ 恒不含当日)。
    window_days: Tuple[date, ...] = ()


def history_window_days(trade_date: date) -> List[date]:
    """本次「自身历史对照」的取数**交易日清单**(裁定 P3-69,2026-08-12)。

    「最近 20 个**有效交易日**,最多向前回溯 60 个自然日补齐」:
      · 交易日口径走 `neckline.calendar`(⛔ 不许自己数自然日);
      · 60 自然日是**回溯上界**,不是计数单位 —— 长假 / 停牌把窗口拖长时它封顶;
      · 🔴 **当日竞价不进入自身历史基线**:窗口取 `d < trade_date`,**显式排除当日**,
        ⛔ 不靠"今天还没落盘"这个巧合(回放 / 补跑时当日分区可能已经存在)。
    """
    end = trade_date - timedelta(days=1)
    start = trade_date - timedelta(days=HISTORY_LOOKBACK_MAX_CALENDAR_DAYS)
    days = [d for d in trading_days_between(start, end) if d < trade_date]
    return days[-HISTORY_LOOKBACK_TRADING_DAYS:]


def scan_history_index(
    codes: Sequence[str], trade_date: date, *, parquet_dir: Optional[Path] = None,
) -> HistoryIndex:
    """扫一次 `auction_snapshots`,把这批票的历史竞价读数按 code 分好。

    ⚠ 窗口 = `history_window_days()`(裁定 P3-69:最近 20 个有效交易日 / 最多回溯
    60 个自然日),**当日不进基线**。
    """
    if not codes:
        return HistoryIndex()
    window: List[date] = []
    try:
        import polars as pl

        from neckline.data.market_data import scan_table_range

        window = history_window_days(trade_date)
        if not window:
            return HistoryIndex(
                unavailable_reason="回看窗口内没有交易日(60 个自然日里一天都没有)")
        df = scan_table_range("auction_snapshots", window[0], window[-1],
                              parquet_dir=parquet_dir)
    except Exception:  # noqa: BLE001 —— 可选情报的保险丝(§铁律:一处裸奔就把"少一维"升级成"没报告")
        logger.warning("[auction] 历史竞价快照读取失败,本次无自身历史对照", exc_info=True)
        return HistoryIndex(unavailable_reason="auction_snapshots 读取失败",
                            window_days=tuple(window))
    if df.is_empty():
        return HistoryIndex(
            unavailable_reason="历史竞价快照分区为空(V2-⑧-B 2026-08-05 才开始存)",
            window_days=tuple(window))
    df = df.filter(
        pl.col("ts_code").is_in(list(dict.fromkeys(codes)))
        # 🔴 只认窗口里的那 ≤20 个交易日(裁定 P3-69);窗口按构造**不含当日**。
        & pl.col("trade_date").is_in(list(window))
        # 🔴 **当日竞价不进入自身历史基线** —— 双保险,把裁定写成一条看得见的过滤,
        #    ⛔ 不让它只依赖"窗口恰好没包含今天"这个间接事实。
        & (pl.col("trade_date") != trade_date)
    )
    per: Dict[str, List[Dict[str, Any]]] = {}
    for row in df.sort("trade_date").iter_rows(named=True):
        per.setdefault(row["ts_code"], []).append({
            "trade_date": str(row["trade_date"]),
            "auction_volume": row.get("auction_volume"),
            "auction_amount": row.get("auction_amount"),
            "gap_pct": row.get("gap_pct"),
        })
    return HistoryIndex(per_member=per, window_days=tuple(window))


def _history_stats(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """一只票窗口内历史读数的**对照三数**(min / 中位 / max),逐指标各算各的。

    🔴 这是 🟡-1 的落点(V2.3.3 定向复审):机械侧告诉模型「允许形成历史比较」,
    却一个历史数字都不给,模型只能沉默或编 —— **许可证必须配证据**。
    ⛔ 三数**不是阈值**,是同一批原始值的描述统计(`_median` 是既有实现);
    ⛔ 也**不截断**任何一天:窗口本身(裁定的 20 个交易日)就是上界。
    """
    out: Dict[str, Any] = {}
    for key in ("auction_volume", "auction_amount", "gap_pct"):
        vals = sorted(float(r[key]) for r in rows
                      if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool))
        if not vals:
            continue        # 这一项整列缺 → 如实不发这个键(⛔ 不补 0)
        out[key] = {"min": vals[0], "median": _median(vals), "max": vals[-1],
                    "observed": len(vals)}
    return out


def history_of(codes: Sequence[str], index: HistoryIndex) -> Dict[str, Any]:
    """从一次扫描的产物里切出这个篮子那一份。

    🔴 **当期有效样本 `n` 是逐票的**(V2.3.3 定向复审 🔴-1 修正):字段名叫「**自身**
    历史竞价样本」、K8 §二十 要比的也是**这一只票**的当前竞价量额 vs **它自己**的历史 ——
    原实现拿全篮日期的**并集**当 `n`,一个篮里只要有一只老面孔,整篮就"够"了,于是
    一只只有 2 天历史的票会被讲成「允许形成历史比较」。**这正是那道闸要挡的那句话。**
    现在:`history_days_per_member[]` 逐票落 `days_available` / `sample_sufficient`;
    篮级 `history_days_available` = **逐票的最小值**(`history_days_available_basis`
    自曝这一点),`history_sample_sufficient` 因而 = 「**每一只**都够」。

    🔴 **`n ≥ 15` 是用户裁定的机械判据**(P3-69,2026-08-12):够 → 允许形成历史比较;
    不够 → `sample_sufficient=False` + 一句「历史样本不足」,**只展示原始值**。
    ⛔ 除这个 15、窗口的 20 / 60 之外,本段**没有第四个数**(三数统计不是阈值)。

    🔴 **窗口必须自曝**(V2.3.3 复审 🔴-2b 起的既定纪律):`n` 被窗口封顶,不自曝就是
    一个偷偷的判据 —— 故 `history_lookback_trading_days`(20 交易日)与
    `history_lookback_days`(60 自然日回溯上界)与一句 `history_lookback_note` 一并下发。
    """
    per: Dict[str, Any] = {}
    per_days: List[Dict[str, Any]] = []
    counts: List[int] = []
    insufficient: List[str] = []
    for code in dict.fromkeys(codes):
        rows = list(index.per_member.get(code) or ())
        if rows:
            per[code] = rows
        # 🔴 **逐票各算各的**:这只票自己在窗口内有几个不同的交易日
        n_i = len({str(r["trade_date"]) for r in rows})
        ok_i = n_i >= HISTORY_MIN_SAMPLE_FOR_COMPARISON
        counts.append(n_i)
        entry: Dict[str, Any] = {"ts_code": code, "days_available": n_i,
                                 "sample_sufficient": ok_i}
        if ok_i:
            # 🟡-1:够了才给对照读数 —— 「允许比较」必须配得上比较用的数。
            entry["comparison_readings"] = _history_stats(rows)
        else:
            insufficient.append(code)
        per_days.append(entry)
    # 🔴 篮级取**逐票最小值**(⛔ 不是并集):并集 = 取最长的那一只,一只老面孔就能
    # 把整篮讲成"够"。取 min 后,`sufficient` 等价于「每一只都够」。
    n = min(counts) if counts else 0
    sufficient = bool(counts) and n >= HISTORY_MIN_SAMPLE_FOR_COMPARISON
    out: Dict[str, Any] = {
        "history_days_available": n,
        "history_days_available_basis": "min_per_member",   # 🔴 自曝:这是逐票最小值
        "history_days_per_member": per_days,
        "history_insufficient_codes": insufficient,
        # 🔴 窗口的两个裁定值 + 单位,一并自曝
        "history_lookback_trading_days": HISTORY_LOOKBACK_TRADING_DAYS,
        "history_lookback_days": HISTORY_LOOKBACK_MAX_CALENDAR_DAYS,
        "history_lookback_unit": "calendar_days",       # 描述的是上面那个 60
        "history_window_trading_days": len(index.window_days),   # 窗口里实际有几个交易日
        # 🔴 样本判据(裁定值)+ 判定结果
        "history_min_sample_for_comparison": HISTORY_MIN_SAMPLE_FOR_COMPARISON,
        "history_sample_sufficient": sufficient,
        "history_excludes_today": True,                 # 当日竞价不进入自身历史基线
        "history_lookback_note": HISTORY_LOOKBACK_NOTE,
        "per_member": per,
        "source": "auction_snapshots",
    }
    if not sufficient:
        out["history_insufficient_note"] = HISTORY_SAMPLE_INSUFFICIENT_NOTE
    if index.unavailable_reason:
        # ⚠ 「扫不到 / 分区为空」与「真的只攒到 3 天」都会落成小 `n`,但成因不同 ——
        # 这一条把成因留下,⛔ 别让两者在界面上讲成同一句话。
        out["unavailable_reason"] = index.unavailable_reason
    return out


def load_history(
    codes: Sequence[str], trade_date: date, *, parquet_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """自身历史对照(单篮入口 = 扫一次 + 切一次)。

    ⚠ **开局几乎无样本是已知的数据现实**(§七 P4-67):`auction_snapshots` 是
    V2-⑧-B(2026-08-05 上产)才开始存的 —— K8 那条「样本不足只展示原始值」的纪律
    **当天就生效**,不是将来才生效。
    """
    if not codes:
        return history_of((), HistoryIndex())
    return history_of(codes, scan_history_index(codes, trade_date, parquet_dir=parquet_dir))


def _median(sorted_vals: Sequence[float]) -> Optional[float]:
    n = len(sorted_vals)
    if n == 0:
        return None
    mid = n // 2
    return sorted_vals[mid] if n % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


# ══════════════════════════════════════════════════════════════════════════
# 编排:一次算完全部篮子 + 市场段
# ══════════════════════════════════════════════════════════════════════════

def build_mech(
    snap: AuctionSnapshot,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> AuctionMech:
    """全部机械读数。⛔ 一个结论都不产生。"""
    from neckline.auction import DQ_OK
    from neckline.auction.collect import MARKET_INDEX_CODES

    scripts_by_code: Dict[str, MemberScript] = {}
    try:
        for sc in load_member_scripts(list(snap.baskets), db_path=db_path):
            scripts_by_code.setdefault(sc.ts_code, sc)
    except Exception:  # noqa: BLE001
        logger.warning("[auction] 冻结失效位读取失败,本次不做失效位标记", exc_info=True)

    regime = _regime_at(snap.d0_date, db_path=db_path)

    # 🔵-12:**一次扫描服务全部篮子**(原先逐篮各扫一次同一个区间;这条路径跑在常驻
    # `neckline.service` 里,P0-23 语境)。切片在 `history_of()`,读数逐位不变。
    history_index = scan_history_index(
        [c for b in snap.baskets for c in b.member_codes], snap.trade_date,
        parquet_dir=parquet_dir,
    )

    baskets: List[BasketMech] = []
    for b in snap.baskets:
        baskets.append(_build_basket_mech(
            b, snap, scripts_by_code=scripts_by_code, regime_at_d0=regime,
            history_index=history_index, db_path=db_path,
        ))

    # 🔴 P2.3:报告级分域质量 = **各篮子取更差的那个**;没有篮子 → `None`
    # (关键域无从谈起,⛔ 不拿 `ok` 冒充、也⛔ 不拿 `insufficient` 吓人)。
    crit = context = None
    if baskets:
        crit = baskets[0].critical_quality
        context = baskets[0].context_quality
        for bm in baskets[1:]:
            crit = worse_of(crit, bm.critical_quality)
            context = worse_of(context, bm.context_quality)
    # 逐票冲突码汇总:`snap.conflicts`(④/① 类)∪ 成员级补判出来的(②/③ 类)。
    conflict_codes = list(dict.fromkeys(
        list(snap.conflicts)
        + [r.ts_code for bm in baskets for r in bm.members if r.source_conflict]))
    market = MarketMech(
        source=snap.source,
        captured_at=snap.captured_at.isoformat(timespec="seconds"),
        requested_codes=len(snap.requested),
        fetched_codes=len(snap.quotes),
        missing_codes=list(snap.missing),
        invalid_codes=list(snap.invalid),
        conflict_codes=conflict_codes,
        data_quality=snap.quality_of(snap.requested),
        critical_quality=crit,
        context_quality=context,
        quote_quality={c: qq.to_dict() for c, qq in (snap.quote_quality or {}).items()},
        # 🔴 裁定 ①:观察范围随产物走(⛔ 不是一句写死在代码里的常量)。
        observation=snap.observation.to_dict() if snap.observation is not None else {},
        index_gaps={c: {"ts_code": c,
                        "name": (snap.meta[c].name if c in snap.meta else c),
                        "gap_pct": snap.gap_of(c)} for c in MARKET_INDEX_CODES},
        anchors=[{"ts_code": c,
                  "name": (snap.meta[c].name if c in snap.meta else c),
                  "gap_pct": snap.gap_of(c)} for c in snap.strong_anchor_codes],
        risks=[],
    )
    market.risks = _mechanical_risks(market, baskets)
    if market.data_quality == DQ_OK and not baskets:
        # 「跑过了、D0 当天就没有 T1/T2 篮子」是**合法状态**,不是数据问题 ——
        # 市场级质量照常判 ok,篮子段为空由 `baskets_covered=0` 表达(⛔ 与 404 分开)。
        pass
    return AuctionMech(trade_date=snap.trade_date, d0_date=snap.d0_date,
                       market=market, baskets=baskets, notes=list(snap.notes))


def _build_basket_mech(
    b: BasketRef,
    snap: AuctionSnapshot,
    *,
    scripts_by_code: Mapping[str, MemberScript],
    regime_at_d0: Optional[str],
    history_index: HistoryIndex,
    db_path: Optional[Path],
) -> BasketMech:
    notes: List[str] = []
    card: Dict[str, Any] = {}
    try:
        row = load_basket_card(b.basket_id, db_path=db_path)
        card = dict((row or {}).get("card") or {})
    except Exception:  # noqa: BLE001
        logger.warning("[auction] 读篮子 %s 的冻结卡失败", b.basket_key, exc_info=True)
        notes.append("card_read_failed")
    if not card:
        # 「有篮子无卡」是**合法中间态**(同 `precall.load_member_scripts` 的既定纪律):
        # 逐票读数照出,只是失效位 / 预案一致性判不了 —— ⛔ 不拿默认条件顶上。
        notes.append("no_card")

    readings = [
        build_member_reading(code, snap, script=scripts_by_code.get(code),
                             card_entry=_member_entry_of(card, code),
                             # 裁定 P3-70 ②:板块对照股**排除本篮成员**(基准必须独立于假设)
                             basket_member_codes=b.member_codes)
        for code in b.member_codes
    ]
    bench_codes = [snap.benchmark_of[c] for c in b.member_codes if c in snap.benchmark_of]
    # 🔴 V2.4.0 P2.2:需要 D0 卡的两类冲突(② 失效位 / ③ 预案区间)在这里补判 ——
    # `collect` 那一层拿不到卡,只判了 ④ 身份 / ① 方向。
    _attach_member_conflicts(readings, snap, scripts_by_code=scripts_by_code, card=card)
    # 🔴 V2.4.0 P2.3:关键域 / 上下文域(K8 §二十「数据质量分域」)。
    critical, context, detail = basket_quality_domains(
        b, snap, readings, bench_codes=bench_codes)
    return BasketMech(
        basket_id=b.basket_id,
        basket_key=b.basket_key,
        name=b.name,
        covered_tier=int(b.tier),
        engine_code=b.engine_code,
        engine_version=b.engine_version,
        skeleton_version=b.skeleton_version or "",
        regime_at_d0=regime_at_d0,
        # 🔴 P2.3:`data_quality` 的含义**收窄为关键域**(施工图 §五 P2.3 逐字)——
        # 闸 1「数据缺失只能形成中性」自此只看关键域,一只无关指数缺失⛔ 不再夹整篮。
        data_quality=critical,
        critical_quality=critical,
        context_quality=context,
        quality_detail=detail,
        members=readings,
        sector_sync=sector_sync_of(readings, snap, bench_codes=bench_codes),
        rel_strength=rel_strength_of(readings),
        history=history_of(list(b.member_codes), history_index),
        hit_invalidation_codes=[r.ts_code for r in readings if r.hit_invalidation is True],
        plan_consistency=plan_consistency_of(readings),
        notes=notes,
    )


def _attach_member_conflicts(
    readings: Sequence[MemberReading],
    snap: AuctionSnapshot,
    *,
    scripts_by_code: Mapping[str, MemberScript],
    card: Optional[Mapping[str, Any]],
) -> None:
    """🔴 **需要 D0 卡的两类跨源冲突**(K8 §二十 ② 失效位 / ③ 预案区间),就地补在
    逐票读数上(原地改 `readings`)。

    **为什么不在 `collect` 里一起判**:那一层的身份是「组清单 → 拉一次价 → 冻结,
    ⛔ 不判定」,而且它**拿不到冻结卡**(卡在 `basket_cards` 里、按篮子读)。
    ④ 身份 / ① 方向两类不需要卡,已经在那边判完 —— 这里只补另外两类,
    ⛔ 不重复覆盖已经判出来的码(`clamped_by` 那套"只记第一个"的同一体例)。

    ⚠ **只对两源都新鲜的成员判**(K8 原文:「双源**均有效**但出现结论性冲突时」);
    只有一源 → 没有第二个读数可以打架,⛔ 那不是"已核对无冲突"。
    """
    for r in readings:
        if r.source_conflict:
            continue                     # collect 已判出 ④/①,⛔ 不覆盖
        qq = (snap.quote_quality or {}).get(r.ts_code)
        dual = (snap.dual_quotes or {}).get(r.ts_code)
        if qq is None or dual is None or dual.primary is None or dual.backup is None:
            continue
        if not all(c.ok for c in qq.checks) or len(qq.checks) < 2:
            continue
        script = scripts_by_code.get(r.ts_code)
        entry = _member_entry_of(card, r.ts_code)
        zone, chase = entry.get("entry_zone"), entry.get("max_chase")
        conflict = detect_conflict(
            dual.primary, dual.backup,
            invalidation_of=lambda q, s=script: hit_invalidation_tristate(s, q)[0],
            plan_entered_of=lambda q, z=zone, mc=chase: plan_entered_tristate(q, z, mc),
        )
        if conflict:
            r.source_conflict = conflict


def basket_quality_domains(
    b: BasketRef,
    snap: AuctionSnapshot,
    readings: Sequence[MemberReading],
    *,
    bench_codes: Sequence[str],
) -> Tuple[str, str, Dict[str, Any]]:
    """🔴 **数据质量分域**(V2.4.0 P2.3,K8 §二十 逐字)→ `(关键域, 上下文域, 逐项账)`。

    **关键域**(非 `ok` → confirm/veto 夹成 neutral):
      ① 篮子成员自身竞价数据;
      ② 每只成员**实际使用的**市场基准 —— ⚠「实际使用的」= `market_index_of` 真给了
         一支的那些。科创板按 K8 §三 排除 → 它**没有**市场基准,⛔ 那不是"缺失";
      ③ **实际用于**相对板块计算的板块基准 —— ⚠ 只有 `peer_median` 真的算出来时,
         那组对照股才算"实际用于";**对照不足**(§七 P1-78,关注池缩编后近乎必然)
         意味着**压根没用上任何板块基准** → ⛔ 不进关键域、不夹整篮;
      ④ D0 失效判断所需的**冻结锚** —— 判据取自失效位三态的原因码
         (有篮无卡 / 锚失效 / 卡上没冻结失效位 → 这一位不可用)。

    **上下文域**(降级只降置信度 + 披露缺失,⛔ 不夹逼结论):其他市场指数 ·
    **未实际用于**当前成员计算的对照股 · 所属上市板块对照指数 · 市场锚点。
    ⚠ 历史比较与前五日量能背景**不是代码**,它们的缺席由 `history` / `volume_note`
    自己如实说;这里只收得进"有代码可查"的那几类。

    🔴 **⛔ 不许用无关字段缺失掩盖已有的失效事实**:两域都不碰
    `hit_invalidation_codes` —— 那条走独立警报通道(§五 ②-G),恒定进第 4 块与推送。
    """
    from neckline.auction.collect import MARKET_INDEX_CODES

    members = list(dict.fromkeys(b.member_codes))
    used_market: List[str] = []
    used_sector: List[str] = []
    anchor_missing: List[Dict[str, str]] = []
    for r in readings:
        idx = (snap.market_index_of or {}).get(r.ts_code)
        if idx:
            used_market.append(idx)
        if r.rel_to_sector_source == SECTOR_BENCH_PEER_MEDIAN:
            used_sector.extend(r.sector_peer_codes)
        # ④ 冻结锚:判据**只取那三个"锚 / 卡"类原因码** —— 行情类原因
        # (没抓到 / 读数不合格 / 开盘价没发)属于 ① 成员自身,⛔ 不在这里重复计一次。
        if r.hit_invalidation_undetermined_reason in (
                UNDET_NO_MEMBER_SCRIPT, UNDET_ANCHOR_STALE, UNDET_NO_STOP_LINE):
            anchor_missing.append({"ts_code": r.ts_code,
                                   "reason": r.hit_invalidation_undetermined_reason})

    critical_codes = list(dict.fromkeys(members + used_market + used_sector))
    context_codes = [c for c in dict.fromkeys(
        list(MARKET_INDEX_CODES) + list(bench_codes)
        + [c for r in readings for c in r.sector_peer_codes]
        + list(snap.strong_anchor_codes)) if c not in set(critical_codes)]

    critical = snap.quality_of(critical_codes)
    # ⚠ 上下文域**为空**时 `quality_of` 按既定纪律判 `insufficient`(「没有可判的东西」
    # 与「判过了都好」必须分得开)—— 那不会夹逼任何结论,只是如实说"这一域没东西可查"。
    context = snap.quality_of(context_codes)
    if anchor_missing:
        # 冻结锚拿不到 = **失效判断做不了** → 关键域至少降级(K8 §二十 把它列进关键域)。
        critical = worse_of(critical, DQ_DEGRADED)
    member_conflicts = [{"ts_code": r.ts_code, "conflict": r.source_conflict}
                        for r in readings if r.source_conflict]
    if member_conflicts:
        # 「双源均新鲜但结论性冲突」→ ⛔ 不能高置信输出(K8 §二十)。
        critical = worse_of(critical, DQ_DEGRADED)
    detail: Dict[str, Any] = {
        "critical": {
            "quality": critical,
            "codes": critical_codes,
            "missing": [c for c in critical_codes if not snap.is_usable(c)],
            "components": {
                CRIT_MEMBER_QUOTE: members,
                CRIT_MARKET_BENCHMARK: list(dict.fromkeys(used_market)),
                CRIT_SECTOR_BENCHMARK: list(dict.fromkeys(used_sector)),
                CRIT_FROZEN_ANCHOR: anchor_missing,
            },
            "conflicts": member_conflicts,
        },
        "context": {
            "quality": context,
            "codes": context_codes,
            "missing": [c for c in context_codes if not snap.is_usable(c)],
        },
        "captured_in_window": bool(snap.captured_in_window),
    }
    return critical, context, detail


def _regime_at(d0: date, *, db_path: Optional[Path]) -> Optional[str]:
    """D0 的行情状态(周度聚合维度)。**缺行 = 不知道 → `None`**,⛔ 不回填默认态
    `trend_continuation`(那会把「系统缺席」讲成「市场是延续」,同 `selection_clock`
    的既定纪律)。"""
    try:
        from neckline.scan.regime_store import load_market_regime

        row = load_market_regime(d0, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[auction] D0 行情状态读取失败(不影响其余读数)", exc_info=True)
        return None
    return (row or {}).get("regime")


def _mechanical_risks(market: MarketMech, baskets: Sequence[BasketMech]) -> List[Dict[str, str]]:
    """小报告第 4 块「异常与风险」的**机械半份**(LLM 那半份在 `llm.py` 补)。

    🔴 **「明确失效警报」是独立通道**(§五 ②-G):`hit_invalidation` 在机械段第一次写
    就落库,恒定出现在这里与推送文案里,**不受 LLM 缺席、不受三道夹逼闸影响** ——
    这正是闸 1「数据缺失只能形成中性」可以无例外执行的原因:`veto` 被夹成 `neutral`
    时,「N 只命中 D0 失效位」这条信息**一个字都没丢**。
    """
    risks: List[Dict[str, str]] = []
    if market.missing_codes:
        risks.append({"kind": RISK_DATA_MISSING,
                      "text": f"{len(market.missing_codes)} 个标的本次没抓到竞价数据"
                              f"(覆盖 {market.fetched_codes}/{market.requested_codes})。"})
    # 🔴 V2.4.0 P2.1:「抓到了、但这份读数不能用」必须自己有一条 —— ⛔ 不许并进
    # 上面那条「没抓到」:一份**上一交易日的缓存行情**长得跟正常读数一模一样,
    # 沉默会让人以为那一格是好的。
    if market.invalid_codes:
        risks.append({"kind": RISK_QUOTE_INVALID,
                      "text": f"{len(market.invalid_codes)} 个标的抓到了读数、但没通过"
                              f"七项校验(源日期 / 源时间 / 必要字段 / 单位),本次一律"
                              f"不当作今天的竞价结果使用:{'、'.join(market.invalid_codes)}。"})
    if market.conflict_codes:
        risks.append({"kind": RISK_SOURCE_CONFLICT,
                      "text": f"{len(market.conflict_codes)} 个标的两源出现结论性冲突"
                              f"(方向 / 失效位 / 预案区间 / 身份),⛔ 不能高置信输出:"
                              f"{'、'.join(market.conflict_codes)}。"})
    degraded_src = [r.ts_code for b in baskets for r in b.members if r.source_degraded]
    if degraded_src:
        risks.append({"kind": RISK_SOURCE_DEGRADED,
                      "text": f"{len(degraded_src)} 只的主源读数不可用,本次改用备源"
                              f"(来源已降级,如实记录):{'、'.join(degraded_src)}。"})
    hit = [c for b in baskets for c in b.hit_invalidation_codes]
    if hit:
        risks.append({"kind": RISK_HIT_INVALIDATION,
                      "text": f"{len(hit)} 只命中 D0 冻结的明确失效位:{'、'.join(hit)}。"})
    dev = [r.ts_code for b in baskets for r in b.members if r.gap_up_deviation is True]
    if dev:
        risks.append({"kind": RISK_GAP_UP_DEVIATION,
                      "text": f"{len(dev)} 只高开已偏离 D0 冻结锚:{'、'.join(dev)}。"})
    stale = [r.ts_code for b in baskets for r in b.members if r.anchor_stale]
    if stale:
        # 🔴 **服务端下发给界面的文案里⛔ 不许写 Markdown**(V2.3.3 批 ⑤ 实拍逮到):
        # 客户端拿到的是一个 `String`,`Text(String)` **不解析 Markdown** —— `**没判**`
        # 会把两个星号原样印在屏幕上。要强调就用「」(全项目一贯写法)。
        # 守门:`tests/test_v233_auction_api.py::test_server_facing_text_carries_no_markdown`。
        risks.append({"kind": RISK_ANCHOR_STALE,
                      "text": f"{len(stale)} 只的冻结锚今日失效(疑似除权除息),"
                              f"本票失效位与高开偏离「没判」(不是「无异常」):{'、'.join(stale)}。"})
    # 🔴 **「没判」必须自己有一条**(V2.3.3 复审 🔴-1):`hit_invalidation` /
    # `gap_up_deviation` 是三态,`None` = 一个字都没核对过。⛔ 不许沉默 —— 沉默会让
    # 用户与 LLM 都以为「没报警 = 没问题」。
    # ⚠ 两类**刻意排除**,免得同一件事报两遍:① 锚失效(上面 `RISK_ANCHOR_STALE`
    # 那条已经逐票点名);② 这只票压根没抓到(上面 `RISK_DATA_MISSING` 已计入,
    # 且逐票行本来就标着「中性｜数据不足」)。
    undet = [r for b in baskets for r in b.members
             if r.has_undetermined_invalidation and not r.anchor_stale
             and r.hit_invalidation_undetermined_reason != UNDET_NO_QUOTE
             and r.gap_up_deviation_undetermined_reason != UNDET_NO_QUOTE]
    if undet:
        reasons = sorted({c for r in undet for c in
                          (r.hit_invalidation_undetermined_reason,
                           r.gap_up_deviation_undetermined_reason) if c})
        risks.append({"kind": RISK_INVALIDATION_UNDETERMINED,
                      "text": f"{len(undet)} 只的失效位 / 高开偏离本次「没判」"
                              f"(原因:{'、'.join(_UNDET_TEXT.get(c, c) for c in reasons)}),"
                              f"这不是「无异常」:{'、'.join(r.ts_code for r in undet)}。"})
    vol = [r.ts_code for b in baskets for r in b.members if r.volume_note]
    if vol:
        risks.append({"kind": RISK_AUCTION_VOLUME,
                      "text": f"{len(vol)} 只竞价量能异常(放量 / 地量):{'、'.join(vol)}。"})
    return risks


#: 「没判」原因码 → 人话(服务端文案单一源;客户端另有一份展示层换算,两处都不许
#: 把它讲成「没问题」)。🔴 ⛔ 这些字符串里不许有 Markdown(`Text(String)` 不解析)。
_UNDET_TEXT: Dict[str, str] = {
    UNDET_NO_QUOTE: "这只票本次没抓到报价",
    UNDET_NO_MEMBER_SCRIPT: "D0 卡上没有这只成员的冻结剧本",
    UNDET_ANCHOR_STALE: "冻结锚今日失效(疑似除权除息)",
    UNDET_NO_STOP_LINE: "卡上没冻结失效位价格",
    UNDET_NO_REF_CLOSE: "卡上没冻结 D0 收盘锚",
    UNDET_NO_OPEN_PRICE: "行情源还没发出开盘价",
    UNDET_QUOTE_INVALID: "抓到的读数没通过七项校验(源日期 / 源时间 / 必要字段 / 单位)"
                         ",本次不拿它判",
}


def _history_row_text(r: Mapping[str, Any]) -> str:
    """一天历史竞价快照的原始值一行(裁定 P3-69:样本不足时**只展示原始值**)。"""
    return (f"{r.get('trade_date')} 量 {_num(r.get('auction_volume'))}"
            f" 额 {_num(r.get('auction_amount'))} 涨跌 {_pct(r.get('gap_pct'))}")


def _history_stats_text(stats: Mapping[str, Any]) -> str:
    """够样本时给模型的**对照读数**(窗口内 最低 / 中位 / 最高,逐指标各一组)。"""
    parts: List[str] = []
    for key, label, fmt in (("auction_volume", "量", _num),
                            ("auction_amount", "额", _num),
                            ("gap_pct", "涨跌", _pct)):
        s = stats.get(key)
        if not isinstance(s, Mapping):
            continue
        parts.append(f"{label} 最低 {fmt(s.get('min'))} / 中位 {fmt(s.get('median'))}"
                     f" / 最高 {fmt(s.get('max'))}(取到 {s.get('observed')} 天)")
    return "；".join(parts) if parts else "窗口内这几项读数整列都缺"


def _history_lines(b: "BasketMech") -> List[str]:
    """短摘要里「自身历史竞价对照」那一段(🔴 **逐票**,定向复审 🔴-1 + 🟡-1)。

    改前的两个病,一起治:
      ① 天数是**全篮日期并集** → 篮里有一只老面孔,整篮就"够",于是只有 2 天历史的
         那只也被讲成「允许形成历史比较」(🔴-1);
      ② 「允许比较」的许可证发出去了,**却一个历史数字都没进 prompt** → 模型要么沉默、
         要么凭印象编一句「明显放量」(🟡-1)。**给了许可就得给证据。**

    现在逐票一行:够的给窗口内对照读数(最低 / 中位 / 最高);不够的**点名 + 逐日原始值**
    + 逐字写明「⛔ 不得据此做比较结论」(裁定 P3-69 原文:「只展示原始值」)。
    """
    h = b.history
    per_days = list(h.get("history_days_per_member") or ())
    per_rows: Mapping[str, Any] = h.get("per_member") or {}
    floor = h.get("history_days_available") or 0
    limit = h.get("history_min_sample_for_comparison")
    out: List[str] = [
        f"   自身历史竞价样本(窗口 = 最近 {h.get('history_lookback_trading_days')} 个有效"
        f"交易日,最多向前回溯 {h.get('history_lookback_days')} 个自然日补齐;"
        f"当日竞价不进入自身历史基线;🔴 **逐票各算各的**)"
        + (f"({h.get('unavailable_reason')})" if h.get("unavailable_reason") else "")
    ]
    if not per_days:
        out.append("       (本篮没有成员可算这一项。)")
        return out
    for e in per_days:
        code = e.get("ts_code")
        n_i = e.get("days_available") or 0
        rows = list(per_rows.get(code) or ())
        if e.get("sample_sufficient"):
            # 🔴 够样本 → 说「允许比较」**并同时给出可据以比较的读数**(🟡-1)。
            out.append(f"       · {code} {n_i} 天 ≥ {limit} 天 → **允许形成历史比较**;"
                       f"窗口内历史读数:{_history_stats_text(e.get('comparison_readings') or {})}")
        elif rows:
            out.append(f"       · {code} {n_i} 天 < {limit} 天 → 🔴 **本项样本不足**:"
                       f"**只展示原始值,⛔ 不得据此做比较结论**(⛔ 不许说「明显放量」"
                       f"「高于平时水平」这类话)。逐日原始值:"
                       + "；".join(_history_row_text(r) for r in rows))
        else:
            # 「一天都没有」与「有 2 天但不够」是两件事,⛔ 不许写成同一句。
            out.append(f"       · {code} 0 天 → 🔴 **本项样本不足**:窗口内**一条历史竞价"
                       f"快照都没有**(⛔ 这不是「跟平时一样」,是没有可比的东西),"
                       f"**不得据此做比较结论**。")
    # 🔴 篮级那个数是**逐票最小值**,必须自曝(⛔ 不许让它被读成"每只票都有这么多天")。
    if h.get("history_sample_sufficient"):
        out.append(f"       篮级:每只成员都有 ≥ {limit} 天(最少的那只 {floor} 天)。")
    else:
        bad = list(h.get("history_insufficient_codes") or ())
        out.append(f"       篮级:按**逐票最小值**记 {floor} 天 —— 样本不足的成员有 "
                   f"{len(bad)} 只({'、'.join(bad) if bad else '无'}),"
                   f"**只对这几只**禁止比较结论,其余成员不受影响。")
    return out


# ══════════════════════════════════════════════════════════════════════════
# 机械层第 6 条职责:供 LLM 判断的**短摘要**
# ══════════════════════════════════════════════════════════════════════════

def short_summary(mech: AuctionMech) -> str:
    """喂 LLM 的短摘要(纯文本块,不是 JSON —— 同 `basket_card.build_card_context` /
    `judge.build_context_block` 的既有理由:降低模型把它误当输出模板抄回来的概率)。

    K8 §二十:「LLM 每次只读取 **D0 篮子摘要、异常股票和冲突证据**,不扫描全市场原始
    明细,不抓取行情,不持续盯盘」—— 本函数就是那个"只读"的边界。
    """
    m = mech.market
    lines: List[str] = []
    lines.append(f"【数据状态】来源 {m.source};冻结时刻 {m.captured_at};"
                 f"覆盖 {m.fetched_codes}/{m.requested_codes};"
                 f"缺失 {len(m.missing_codes)} 个;读数不合格 {len(m.invalid_codes)} 个;"
                 f"跨源冲突 {len(m.conflict_codes)} 个;"
                 f"数据质量 {m.data_quality}(市场级整体覆盖率读数)。")
    if m.missing_codes:
        # ⚠ **不截断**(同 ⑨-A 第 5 行对竞价强势股的既定理由):截断需要一个 K8 没给
        # 的数,而「模型看到的就是系统看到的全部」更诚实。量级上限 = 抓取清单本身
        # (`DEFAULT_BREADTH_CAP`),不会失控。
        lines.append(f"   缺失代码(两源都没拉到):{'、'.join(m.missing_codes)}")
    if m.invalid_codes:
        # 🔴 V2.4.0 P2.1:「抓到了、但这份读数不能用」必须说出口 —— 沉默会让模型
        # 把那一格当成"好的"。
        lines.append(f"   读数不合格(抓到了但没通过七项校验,本次⛔ 不当今天的竞价结果用):"
                     f"{'、'.join(m.invalid_codes)}")
    if m.conflict_codes:
        lines.append(f"   跨源冲突(两源都新鲜但结论相反,⛔ 不能高置信输出):"
                     f"{'、'.join(m.conflict_codes)}")
    lines.append("【市场对照指数竞价】" + "；".join(
        f"{v.get('name') or c} {_pct(v.get('gap_pct'))}" for c, v in m.index_gaps.items()
    ))
    # 🔴 裁定 ①:观察范围必须随产物说出口(⛔ 不许只写在代码注释里)。
    scope_note = str((m.observation or {}).get("scope_note") or "")
    if scope_note:
        lines.append("【竞价观察范围】" + scope_note)
    if m.anchors:
        lines.append("【竞价强势股(观察范围内的市场锚点,**不是全市场排行、不取得交易资格**)】"
                     + "；".join(
                         f"{a.get('name') or a['ts_code']}({a['ts_code']}) {_pct(a.get('gap_pct'))}"
                         for a in m.anchors
                     ))
    else:
        lines.append("【竞价强势股】本次观察池里没有高开且不属于任何 T1/T2 篮的标的。")

    if not mech.baskets:
        lines.append("")
        lines.append("【篮子】D0 当天没有 T1/T2 篮子 —— 本次没有可验证的交易假设。")
        return "\n".join(lines)

    for b in mech.baskets:
        lines.append("")
        lines.append(f"【篮子 {b.basket_key}|{b.name}】D0 原始等级 T{b.covered_tier};"
                     f"引擎 {b.engine_code or '未记录'} {b.engine_version or ''};"
                     f"骨架 {b.skeleton_version or '未记录'};"
                     f"D0 行情状态 {b.regime_at_d0 or '当日无记录'};"
                     # 🔴 V2.4.0 P2.3:两域**分开报**,并把「哪一域才夹结论」写死在
                     # 摘要里 —— ⛔ 别让模型以为上下文域缺失也得转中性。
                     f"关键域质量 {b.critical_quality}、上下文域质量 {b.context_quality}"
                     f"(🔴 只有**关键域**非 ok 才必须转中性;上下文域降级**只降低置信度**"
                     f"并在理由或风险里披露缺失项,⛔ 不改变结论)。")
        lines.extend(_quality_domain_lines(b))
        for r in b.members:
            lines.append(
                f"   · {r.ts_code} {r.name}|角色 {r.role or '未记录'}"
                f"|竞价 {_num(r.auction_price)}(昨收 {_num(r.pre_close)},"
                f"涨跌 {_pct(r.gap_pct)})|量 {_num(r.auction_volume)}"
                f"(占前 5 日日均 {_pct(r.vol_vs_prev5_frac)})|额 {_num(r.auction_amount)}"
            )
            lines.append(
                # 🔴 两个读数**各自说清减的是什么**(裁定 P3-70);取不到就写原因,
                # ⛔ 不许留一个光秃秃的「算不出」让模型自己脑补。
                f"       相对板块 {_pct(r.rel_to_sector)}{_sector_bench_clause(r)}"
                f"、相对市场 {_pct(r.rel_to_index)}{_index_bench_clause(r)}"
                f";与 D0 预案 {r.plan_fit};数据质量 {r.data_quality}"
                f"{';⚠ 冻结锚今日失效(疑似除权除息)' if r.anchor_stale else ''}"
                f"{';🔴 已触发 D0 明确失效位' if r.hit_invalidation else ''}"
                f"{';⚠ 高开已偏离 D0 冻结锚' if r.gap_up_deviation else ''}"
                # 🔴 三态的第三态**必须说出口**:不写,模型只能把"没判"读成"没事"。
                f"{_undetermined_clause(r)}"
            )
            if r.volume_note:
                lines.append(f"       竞价量能:{r.volume_note}")
        ss = b.sector_sync
        # 🔴 **⛔ 这一组不许再叫「板块基准指数」**(定向复审 🟡-2):它是各成员**所属
        # 上市板块**(主板 / 创业板 / 科创板 / 北交所)的对照指数,主板票落到的就是市场
        # 指数本身 —— 而裁定 ④ 明令「禁止使用市场指数代替板块基准」。计算侧本来就没用它
        # 当板块基准(`rel_to_sector` 走对照股中位数),但**在唯一真正读这些字的消费者
        # (LLM)眼里,标签就是口径**:上一行管上证叫「板块基准」、下一行说两者不是一回事,
        # 那道禁令在 prompt 里当场失效。这里只改**名**,一个读数都没动。
        lines.append(f"   板块协同:同向上涨 {ss.get('up_count')} / 下跌 {ss.get('down_count')}"
                     f" / 平 {ss.get('flat_count')}(有读数 {ss.get('observed')}/"
                     f"{ss.get('member_count')});"
                     f"{ss.get('listing_board_benchmarks_label') or _LISTING_BOARD_BENCH_LABEL} "
                     + ("；".join(
                         f"{c} {_pct((v or {}).get('gap_pct'))}"
                         for c, v in (ss.get("listing_board_benchmarks") or {}).items()) or "无"))
        rs = b.rel_strength
        lines.append(f"   相对强弱中位:vs 板块 {_pct(rs.get('median_rel_to_sector'))}"
                     f"(取到 {rs.get('observed_sector')}/{len(b.members)} 只)"
                     f"、vs 市场 {_pct(rs.get('median_rel_to_index'))}"
                     f"(取到 {rs.get('observed_index')}/{len(b.members)} 只)"
                     f";两者是**两个不同的基准**,⛔ 不是同一个数。")
        lines.append(f"   {rs.get('sector_peer_pool_note') or ''}")
        lines.extend(_history_lines(b))
        if b.hit_invalidation_codes:
            lines.append(f"   🔴 命中 D0 明确失效位:{'、'.join(b.hit_invalidation_codes)}")
        if b.notes:
            lines.append(f"   机械层备注:{'、'.join(b.notes)}")
    return "\n".join(lines)


def _quality_domain_lines(b: "BasketMech") -> List[str]:
    """短摘要里「关键域 / 上下文域各缺了什么」那一段(V2.4.0 P2.3)。

    🔴 **缺了什么必须点名**:只报一个 `degraded` 三态,模型没法判断"这次缺的到底
    重不重要" —— 而这恰恰是本版把域拆开的全部意义。
    """
    d = b.quality_detail or {}
    crit = d.get("critical") or {}
    ctx = d.get("context") or {}
    out: List[str] = []
    cm = list(crit.get("missing") or ())
    anchors = list(((crit.get("components") or {}).get(CRIT_FROZEN_ANCHOR)) or ())
    conflicts = list(crit.get("conflicts") or ())
    if cm or anchors or conflicts:
        parts: List[str] = []
        if cm:
            parts.append(f"关键域没有可用读数的:{'、'.join(cm)}")
        if anchors:
            parts.append("拿不到 D0 冻结锚(失效判断做不了)的:"
                         + "、".join(f"{a.get('ts_code')}({_UNDET_TEXT.get(a.get('reason') or '', '原因未记录')})"
                                     for a in anchors))
        if conflicts:
            parts.append("两源结论性冲突的:"
                         + "、".join(f"{c.get('ts_code')}({c.get('conflict')})" for c in conflicts))
        out.append("   关键域缺口:" + ";".join(parts))
    else:
        out.append("   关键域缺口:无。")
    xm = list(ctx.get("missing") or ())
    if xm:
        out.append(f"   上下文域缺口(⛔ 只降低置信度、不改变结论,但要在理由或风险里"
                   f"披露):{'、'.join(xm)}")
    return out


def _undetermined_clause(r: MemberReading) -> str:
    """逐票短摘要里那句「这两项没判(原因)」。**判出来了就一个字都不加。**

    🔴 system prompt 里那条「标了『没判』的项照实当作未知」只有在摘要里真的写了
    「没判」时才生效 —— 这个函数就是它的落点(V2.3.3 复审 🔴-1)。
    """
    parts: List[str] = []
    if r.hit_invalidation is None:
        why = _UNDET_TEXT.get(r.hit_invalidation_undetermined_reason or "", "原因未记录")
        parts.append(f"失效位「没判」({why})")
    if r.gap_up_deviation is None:
        why = _UNDET_TEXT.get(r.gap_up_deviation_undetermined_reason or "", "原因未记录")
        parts.append(f"高开偏离「没判」({why})")
    return (";⚠ " + "、".join(parts) + ",这不是「无异常」") if parts else ""


#: 相对强弱「取不到」原因码 → 人话(**服务端文案单一源**;客户端另有一份展示层换算)。
#: 🔴 ⛔ 这些字符串里不许有 Markdown(`Text(String)` 不解析);
#: 🔴 每一条都在说「这个数**没有**」,⛔ 一条都不许被读成「持平」。
_REL_UNDET_TEXT: Dict[str, str] = {
    REL_UNDET_NO_MEMBER_GAP: "这只票自己的竞价涨跌幅就算不出",
    REL_UNDET_BOARD_EXCLUDED: "科创板按 K8 基础股票池规则排除,不设市场指数对照",
    REL_UNDET_NO_BOARD_META: "查不到这只票的板块归属",
    REL_UNDET_NO_INDEX_QUOTE: "对应市场指数本次没抓到报价",
    REL_UNDET_NO_INDUSTRY: "查不到这只票的行业口径,无从取板块对照股",
    REL_UNDET_INDUSTRY_MAP_UNAVAILABLE: "本次整张行业表都没读到(系统缺席),不是这只票没有行业",
    REL_UNDET_DATA_INSUFFICIENT: f"有效板块对照股不足 {SECTOR_PEER_MIN} 只",
}


def _sector_bench_clause(r: MemberReading) -> str:
    """逐票短摘要里「相对板块」后面那个括号:**减的是哪一组 / 为什么没有**。"""
    if r.rel_to_sector is not None:
        if r.rel_to_sector_source == SECTOR_BENCH_PEER_MEDIAN:
            return (f"(对照:同行业「{r.industry or '未记录'}」{len(r.sector_peer_codes)} 只"
                    f"对照股中位 {_pct(r.sector_benchmark_gap_pct)})")
        return (f"(对照:板块指数 {r.sector_index_code or '未记录'} "
                f"{_pct(r.sector_benchmark_gap_pct)})")
    why = _REL_UNDET_TEXT.get(r.rel_to_sector_reason or "", "原因未记录")
    return f"(没有这个读数:{why};⛔ 不是「持平」)"


def _index_bench_clause(r: MemberReading) -> str:
    """逐票短摘要里「相对市场」后面那个括号:**减的是哪一支 / 为什么没有**。"""
    if r.rel_to_index is not None:
        return (f"(对照:市场指数 {r.index_benchmark_code or '未记录'} "
                f"{_pct(r.index_benchmark_gap_pct)})")
    why = _REL_UNDET_TEXT.get(r.rel_to_index_reason or "", "原因未记录")
    return f"(没有这个读数:{why};⛔ 不是「持平」)"


def _pct(v: Any) -> str:
    if v is None:
        return "算不出"
    try:
        return f"{float(v) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "算不出"


def _num(v: Any) -> str:
    if v is None:
        return "算不出"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "算不出"


__all__ = [
    "MemberReading", "BasketMech", "MarketMech", "AuctionMech", "HistoryIndex",
    "SectorBenchmark",
    "HISTORY_LOOKBACK_NOTE", "HISTORY_SAMPLE_INSUFFICIENT_NOTE", "SECTOR_PEER_POOL_NOTE",
    "LISTING_BOARD_BENCHMARK_NOTE",
    "HISTORY_LOOKBACK_TRADING_DAYS", "HISTORY_LOOKBACK_MAX_CALENDAR_DAYS",
    "HISTORY_MIN_SAMPLE_FOR_COMPARISON",
    "plan_fit_of", "build_member_reading", "sector_benchmark_of", "sector_sync_of",
    "rel_strength_of",
    "hit_invalidation_tristate", "gap_up_deviation_tristate", "plan_entered_tristate",
    "basket_quality_domains",
    "plan_consistency_of", "history_window_days", "scan_history_index", "history_of",
    "load_history", "build_mech", "short_summary",
]
