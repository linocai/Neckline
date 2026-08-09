"""盘前校准 tick(plan v1.1-A,§2.4 第 5 类哨兵动作)。**纯规则判定、零 LLM、零
资金面**——交易日 9:25:30(集合竞价撮合后快照即含开盘价 / 竞价量)跑一次,只判
「前晚报告的计划是否已被集合竞价作废 / 预警」,**不产新票、不推荐买入**(§2.4 铁
原则的执行层落点,不是选新票)。现有 9:35 起的 intraday 判定逻辑(`engine.run_tick`)
一字不改。

⚠ **V2-⑬-7:判定对象与判据源换血(阈值与推送一行未改)**。V1 的对象是「昨晚 20 只
候选」、判据源是候选 `entry_spec`/`invalidation_spec`;候选榜已删(⑬-1)→ 改为
**篮子竞价剧本核对**:对象 = **D0 冻结的 T1/T2 篮子成员**,判据源 = **⑦ 冻结在
`basket_cards.card_json` 里的结构化 spec**(`verification_spec.members[].ref_close`
与 `invalidation_spec.members[].close_below_stop_line`)。三点纪律:

    · **只读冻结值,盘前一律不重算**(§2.4 铁律的原意:计划是昨晚写死的)。卡里没有
      该成员的行 / 该阈值为 null → **跳过该票不判**,不拿现价现推一个阈值顶上。
    · **⑧-E 除权除息锚失效检测先于前两类判定**(判定线审计 🟡-2,2026-08-03 补):
      竞价 `pre_close ≠ 卡里冻结的 ref_close` → 冻结锚今日已失效,拿 D+1 的原始开盘价
      去跟除权前尺度的价位比是**错的比较**。⑬-7 晚于 ⑧-E 落地,把同一个锚错配在另一个
      消费方重新引入:成员恰在 D+1 除权除息(⑧-E 真实样本 603409.SH,D0 收盘 31.70 →
      D+1 除权参考价 21.07)时,`open ≤ stop_line` 必真 →「开盘即在失效位下方」假警,
      直接进 9:26 盘前汇总推送。现改为**该票前两类判定跳过 + 如实标 `member_ex_rights`**;
      检测器复用 `basket_verify.anchor_mismatch`(全项目唯一一份),⛔ 不做自动 rescale
      (与 ⑧-E 同理:盘中分不开「真除权」与「行情源故障」,确诊留给今晚 ⑧-E EOD 的
      `adj_factor` 交叉确认)。竞价量能附注不受此影响(它比的是量,不读冻结锚)。
    · **不碰 `verification_rules.py` 的条件集与 `VERIFICATION_RULESET_VERSION`**:
      竞价开盘价不是收盘价,把它塞进 ⑦-b 的 close 语义条件里会污染 ⑧ 的四态判定。
      本模块**只借冻结价位**(ref_close / stop_line 两个数),自己用既有的
      `PRECALL_GAP_UP_INVALIDATE` 阈值判偏离 —— 判定与阈值都是 V1 原样。
    · **依赖方向单向**(`precall → selection`,反向被 `test_selection_basket_card.py`
      守门锁死);本模块也**不读 ⑦-K7 的成员标注件**(那套标注禁入 `neckline/sentinel/`,
      守门单测按「标注码字面量 + 模块名」全目录扫描,别在这里写出它们的名字)。

四类判定(全从 `Quote` + 冻结 spec + 派生 stopLine 算):
    1. 成员竞价高开偏离剧本 →「今日高开已偏离冻结剧本」:`open` 相对卡里冻结的
       `ref_close` 高开超 `PRECALL_GAP_UP_INVALIDATE` → 提示今日追入位已失真。
    2. 成员竞价低开踩失效位 →「开盘即在失效位下方」:`open` ≤ 卡里冻结的
       `close_below_stop_line`(章程 `stop_pct` 算出的价位,系统算不由 LLM 给)→
       失效预警。⛔ 这是**成员级剧本核对**,不是篮子 `falsified` 定论(那归 ⑧ 的
       EOD 拍,且 ⛔ 不进推送);也不驱动任何持仓动作。
    3. 竞价量能异常标注:集合竞价量相对 `load_prev5_avg_volume`(前 5 日**日**均量)
       异常放大 / 地量 → 附注(非独立刹车)。**诚实局限**:竞价量是「开盘一撮」的量,
       与「全天日均量」量纲不同,这里用「竞价量 / 前 5 日日均量」的占比做启发式标注
       (放量占比高 = 竞价参与踊跃;地量占比低 = 无人问津),阈值均**未回测**。
    4. 持仓大幅低开 →「止损条件单今日可能触发」:持仓 `open` 逼近 / 跌破派生 stopLine
       (= buy×(1−`stop_pct`),读现役 config)→ 提示条件单今日可能触发(**系统永不代
       下单**,只提醒)。

时序 / 防重(plan A.1):`is_precall_window` 独立判定 `9:25:30 <= now < 9:30`(`is_intraday_now`
不改,它对 9:25:30 仍返 False);`run_precall_tick` 内按 `sentinel_events` 市场级
key(`(td,"precall","","tick")`)防重「当日只跑一次」——盘前收紧轮询(9:20–9:30 每
30s 一探,见 `api/app.py::_sentinel_loop`)重复调用本函数时,首次入窗执行、其余直接跳过。

推送 / 落库分离(照 `engine.run_tick` 与退潮刹车的两机制惯例):本函数只做**看板落库**
(每条判定落 `sentinel_events`,sentinel=`precall` / `d5exit`)+ 计算待推项,返回给
`_sentinel_loop`;真正的 APNs 汇总推送(9:26,category `PRECALL`)与 D5 推送(category
`D5EXIT`)由循环调 `api/notify.py` 完成(受各自开关)。D5 时间退出扫描**折进本进程**
(9:25:30 已在跑,一次唤醒同时做校准 + D5,省一次进程唤醒),且**独立于 kind=`precall`
开关**(进程无条件跑扫描,各推送查各自开关)。

**回退备选(写代码注释备查,优先走循环内分支照 §3.6「哨兵不另起进程」)**:若 always-on
循环 timing 不可靠,可另起独立 `neckline-precall.timer`(oneshot 9:25:30 → 调本函数
→ APNs)。当前不采用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from neckline.calendar import is_trading_day
from neckline.sentinel.basket_verify import anchor_mismatch
from neckline.sentinel.dedup import already_pushed, record_pushed
from neckline.sentinel.holding import STOP_APPROACH_BUFFER
from neckline.sentinel.positions import Position, d_count
from neckline.sentinel.quotes import Quote, get_quotes
from neckline.selection import verification_rules as vr
from neckline.selection.basket_store import BasketRef, load_basket_card
from neckline.sentinel.universe import (
    DEFAULT_BREADTH_CAP,
    WatchUniverse,
    load_prev5_avg_volume,
    load_stock_meta,
    load_watch_universe,
)
from neckline.strategy import brain
from neckline.strategy.momentum import MomentumConfig

logger = logging.getLogger(__name__)

# 盘前窗口:集合竞价 9:25 撮合,留 30s 让快照落地含开盘价 / 竞价量,故 9:25:30 起跑。
_PRECALL_START = time(9, 25, 30)
_PRECALL_END = time(9, 30)

# 候选高开作废阈(**未回测,启发式**):open 高于候选买点参考位超过此比例 → 今日买点
# 已变形(追高开缺口,§2.4 精神「别在情绪化的位置进出」)。
PRECALL_GAP_UP_INVALIDATE = 0.03

# 竞价量能异常阈(**未回测,启发式**;单位:竞价量 / 前 5 日**日**均量的占比):
#   · ≥ HIGH → 竞价放量(参与踊跃);  · ≤ LOW → 竞价地量(无人问津)。
# 竞价量本身只是全天量的一小截,故占比常态在 1%~5% 量级,阈值据此拍(见模块头「诚实局限」)。
PRECALL_AUCTION_VOL_HIGH_FRAC = 0.10
PRECALL_AUCTION_VOL_LOW_FRAC = 0.005

# 浮点容差(与 holding.py 同量级):由配置减法算出的阈值(如 buy×(1−stop_pct))在二进制
# 浮点下偶发落在边界误差上,纪律阈值比较统一留此容差,不写裸 >=/<=(CLAUDE.md 记的坑)。
_EPS = 1e-9

# 现役 config 缺失(异常状态)时的兜底源:直接取 MomentumConfig 的字段默认(单一源,
# 不在此另拍字面量);正常生产恒有现役版本,兜底只在未定版的异常库里触发 + 打 warning。
_FALLBACK_CFG = MomentumConfig()

# 判定类型 → sentinel_events 的 event_key(当日按 (td, "precall", code, key) 防重一次)。
EVENT_GAP_UP = "gap_up_invalidate"
EVENT_LOW_OPEN = "low_open_falsify"
EVENT_AUCTION = "auction_vol_anomaly"
EVENT_POS_LOW_OPEN = "position_low_open"
# 🟡-2:冻结锚今日失效(竞价 pre_close ≠ 卡里 ref_close)。**这是「今天没法核对」的
# 如实标注,不是一条判定** —— 不进 `summary_actionable`,不单独触发推送。⚠ 名字里的
# `ex_rights` 是**盘前的疑似**:盘前没有 `adj_factor` 交叉确认能力,分不开「真除权」与
# 「行情源故障」(⑧-E 同款局限),确诊在今晚 ⑧-E EOD 那一拍(`REASON_MEMBER_EX_RIGHTS`
# 才是确诊码)。文案里据此写「疑似」,别在别处把这个标记当成已确诊。
EVENT_MEMBER_EX_RIGHTS = "member_ex_rights"
# 市场级「当日盘前 tick 已跑」标记(ts_code 空,不进看板事件列表,见 api board 过滤)。
EVENT_TICK = "tick"
# ⚠ **V2.2-⑤-B:`EVENT_CIRCUIT_LOCKED` / `CIRCUIT_LOCKED_PRECALL_NOTE` 已随熔断整体退役
# 删除**(裁定 #8:锁定态 / 次日只减不加 / 强制复盘解锁三件机制全删)。连带:9:26 汇总
# 推送的「锁定期必发」豁免**一并取消**,`should_push_summary` 回归「有需要动作的判定才推」。
# ⛔ 别再往盘前 tick 里加任何「建议今天别开仓」的自动状态位(§五 〇b-7)。
# D5 时间退出:sentinel="d5exit",ts_code=持仓代码,event_key 固定(当日每票防重一次)。
D5EXIT_EVENT_KEY = "trigger"

# —— v1.3 两档时间退出状态码(§五 v1.3-①-C;= 客户端契约 `PositionOut.timeExitState`)——
TIME_EXIT_NEXT_DAY = "time_exit_next_day"  # 非浮盈单:d≥max_hold_days 且净浮盈 ≤0 → 次日退出(推)
PROFIT_EXEMPT = "profit_exempt"            # 浮盈单:d≥max_hold_days 且净浮盈 >0 → 续持至硬上限(不推)
HARD_CAP_EXIT = "hard_cap_exit"            # d≥max_hold_days_profit → 硬上限无条件次日退出(推)
HOLDING = "holding"                        # d<max_hold_days,未到时间退出判定点(不推)
# v1.4-①-B 第五态(§七 P0-2):**当日无 EOD 行**(停牌 / 数据缺口)且尚未定格 → 时间退出
# **判向挂起**:不定格、不推 D5、不推硬上限。`d_count` 照常按交易日累计并展示(纪律口径是
# 「持有交易日数」,不因停牌暂停计数),只是**判向**悬空;复牌当日 16:35 用复牌当日 EOD
# 正常定格。**这是对既有保守兜底的定向收窄,不是放宽** —— 现状「无定格 → 保守判
# time_exit_next_day」会催用户去卖一只**根本卖不掉**的票;其余无定格情形(EOD 管线断跑等)
# 一字不改仍保守判 time_exit_next_day(豁免需正向证据,审计 🔴-1 结论不得回退)。
SUSPENDED_HOLD = "suspended_hold"
# 需盘前汇总推送的两档(profit_exempt/holding/suspended_hold 不推 D5 执行提醒,§五 v1.3-①-D)。
_ACTIONABLE_TIME_EXIT = (TIME_EXIT_NEXT_DAY, HARD_CAP_EXIT)


# —— 时序判定 ————————————————————————————————————————————————————————

def is_precall_window(now: datetime) -> bool:
    """是否在盘前校准窗口:交易日 且 09:25:30 ≤ now.time() < 09:30。`is_intraday_now`
    不改(它对 9:25:30 仍返 False,盘中判逻辑仍从 9:30 起);两窗口不重叠。"""
    if not is_trading_day(now.date()):
        return False
    t = now.time()
    return _PRECALL_START <= t < _PRECALL_END


# —— 四类纯规则判定(可单测,不联网、不落库)——————————————————————————————

@dataclass(frozen=True)
class MemberScript:
    """一位 T1/T2 篮子成员的**冻结竞价剧本**(V2-⑬-7)。两个价位都直接取自 ⑦ 冻结在
    `basket_cards.card_json` 里的结构化 spec,**盘前不重算**;取不到就是 `None`,
    对应判定直接跳过(「没有」与「没看」分得开:None = 卡里没给,不是"不触发")。"""
    ts_code: str
    basket_key: str
    ref_close: Optional[float] = None     # verification_spec.members[].ref_close(D0 收盘锚)
    stop_line: Optional[float] = None     # invalidation_spec.members[].close_below_stop_line


def load_member_scripts(
    baskets: Sequence["BasketRef"], *, db_path: Optional[Path] = None
) -> List[MemberScript]:
    """把 D0 冻结的 T1/T2 篮子卡摊成「一位成员一份竞价剧本」。

    **有篮子无卡是合法中间态**(`load_basket_card` 返回 None)——⛔ 不许拿默认条件顶上,
    该篮子本拍直接不判(与 `basket_verify.evaluate_card` 的 `REASON_NO_CARD` 同一纪律)。
    读卡异常只 WARNING、跳过该篮,绝不掀翻整个盘前 tick(持仓那一类判定还要跑)。"""
    out: List[MemberScript] = []
    for b in baskets:
        try:
            row = load_basket_card(b.basket_id, db_path=db_path)
        except Exception:  # noqa: BLE001
            logger.warning("[precall] 读篮子 %s 的冻结卡失败,本篮不做竞价剧本核对",
                           b.basket_key, exc_info=True)
            continue
        card = (row or {}).get("card") or {}
        if not card:
            continue        # 有篮子无卡:合法,不判(不猜阈值)
        verify = card.get("verification_spec") or {}
        invalid = card.get("invalidation_spec") or {}
        ref_of = {m.get("ts_code"): m.get("ref_close")
                  for m in (verify.get("members") or []) if isinstance(m, dict)}
        stop_of = {m.get("ts_code"): m.get(vr.COND_CLOSE_BELOW_STOP_LINE)
                   for m in (invalid.get("members") or []) if isinstance(m, dict)}
        for code in b.member_codes:
            out.append(MemberScript(
                ts_code=code, basket_key=b.basket_key,
                ref_close=_positive_or_none(ref_of.get(code)),
                stop_line=_positive_or_none(stop_of.get(code)),
            ))
    return out


def _positive_or_none(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def member_anchor_stale(script: MemberScript, quote: Quote) -> bool:
    """🟡-2:这只成员的**冻结锚今日还成不成立** —— 竞价 `pre_close` 与卡里 `ref_close`
    对不上(带 `vr.EPS` 容差)即判失效。检测器**复用 ⑧-E 的 `basket_verify.
    anchor_mismatch`,不在本模块抄第二份**(抄一份 = 两处容差各自漂移,正是 ⑧-E 那场
    事故的复发路径)。

    `pre_close` 取不到(测试替身 / 老调用点)→ `getattr` 兜底 `None` → 检测器返回
    `False` = **不做锚检测**,不是"锚一定有效"(⛔「没有」不是「不匹配」)。"""
    return anchor_mismatch(script.ref_close, getattr(quote, "pre_close", None))


def judge_gap_up_invalidate(
    script: MemberScript, quote: Quote, threshold: float = PRECALL_GAP_UP_INVALIDATE
) -> Optional[str]:
    """成员集合竞价开盘高于**卡里冻结的 D0 收盘锚**超阈 →「今日高开已偏离冻结剧本」。
    锚缺失或 open≤0 → None(数据不足,不妄判)。阈值沿用 V1 的
    `PRECALL_GAP_UP_INVALIDATE`,一个字未改。

    **锚失效(疑似除权除息)→ None**(🟡-2):这一步在阈值判定**之前**,与 ⑧-E
    「检测先于任何条件判定」同序 —— 先排除错的比较,不许先判后知错。编排层
    (`run_precall_tick`)会另标 `member_ex_rights` 如实披露,不是静默丢弃。"""
    ref = script.ref_close
    if ref is None or quote.open <= 0 or member_anchor_stale(script, quote):
        return None
    gap = quote.open / ref - 1
    if gap >= threshold - _EPS:
        return (
            f"集合竞价开盘{quote.open:.2f}高于冻结锚{ref:.2f} {gap:.1%}"
            f"(超阈{threshold:.0%}),今日已偏离冻结剧本——勿追高开缺口。"
        )
    return None


def judge_low_open_falsify(script: MemberScript, quote: Quote) -> Optional[str]:
    """成员集合竞价开盘已在**卡里冻结的失效位**(`close_below_stop_line`,由现役章程
    `stop_pct` 算出)下方 →「开盘即在失效位下方」。失效位缺失或 open≤0 → None。

    ⛔ 这是**成员级剧本核对**,不是篮子 `falsified` 定论(⑦-b/⑧ 的 EOD 拍才是,且
    篮子 falsified ⛔ 不进推送),更不驱动任何持仓动作(持仓走下面第 4 类判定)。

    **锚失效(疑似除权除息)→ None**(🟡-2):这条是假警重灾区 —— 除权除息日的开盘价
    比冻结止损线低一大截是**尺度问题不是破位**,`open ≤ stop_line` 必真。分红季每 1–3 天
    就有一个被验证成员中招(⑧-E 裁定书量化过),而这条会进 9:26 锁屏推送。"""
    stop_line = script.stop_line
    if stop_line is None or quote.open <= 0 or member_anchor_stale(script, quote):
        return None
    if quote.open <= stop_line + _EPS:
        gap_txt = ""
        if quote.pre_close > 0:
            gap_txt = f"低开{(quote.open - quote.pre_close) / quote.pre_close:.1%},"
        return (
            f"集合竞价开盘{quote.open:.2f}已在冻结失效位{stop_line:.2f}下方({gap_txt}"
            f"该价位由现役章程 stop_pct 算出),开盘即失效预警——前晚剧本今日大概率不成立。"
        )
    return None


def judge_auction_volume(
    quote: Quote,
    prev5_avg_vol: float,
    *,
    high_frac: float = PRECALL_AUCTION_VOL_HIGH_FRAC,
    low_frac: float = PRECALL_AUCTION_VOL_LOW_FRAC,
) -> Optional[str]:
    """竞价量能异常标注(附注,非独立刹车)。`prev5_avg_vol`≤0(无基准)或竞价量≤0 →
    None(诚实「无数据」)。"""
    if prev5_avg_vol <= 0 or quote.volume <= 0:
        return None
    frac = quote.volume / prev5_avg_vol
    if frac >= high_frac - _EPS:
        return f"集合竞价量达前 5 日日均量的{frac:.1%}(竞价放量,参与踊跃)"
    if frac <= low_frac + _EPS:
        return f"集合竞价量仅前 5 日日均量的{frac:.2%}(竞价地量,无人问津)"
    return None


def judge_position_low_open(
    position: Position, quote: Quote, stop_pct: float, buffer_pct: float = STOP_APPROACH_BUFFER,
    *, advisory: bool = False,
) -> Optional[str]:
    """持仓集合竞价大幅低开逼近 / 跌破派生 stopLine →「止损条件单今日可能触发」。
    `stopLine = buy×(1−stop_pct)`(读现役 config,不硬编)。逼近提前量复用 holding 的
    `STOP_APPROACH_BUFFER`(单一源)。**系统永不代下单**,只提醒确认券商条件单。

    `advisory`(V2.2-⑤,口径与 `holding.check_stop_approach` 同源同参名):True = `v2.2-k8`
    的「止损警戒 + 离场决策」口吻;缺省 False → 文案**逐字不变**(条件单口径)。
    ⚠ **判定与阈值一字未动**,只换这句话在说什么。"""
    if quote.open <= 0 or position.buy_price <= 0:
        return None
    stop_line = position.buy_price * (1 - stop_pct)
    drawdown = (position.buy_price - quote.open) / position.buy_price
    warn_from = max(stop_pct - buffer_pct, 0.0)
    if drawdown < warn_from - _EPS:
        return None
    if quote.open <= stop_line + _EPS * position.buy_price:
        if advisory:
            return (
                f"止损警戒:集合竞价开盘{quote.open:.2f}已跌破止损线{stop_line:.2f}"
                f"(-{stop_pct:.0%}),离场决策在你(系统不代下单)。"
            )
        return (
            f"集合竞价开盘{quote.open:.2f}已跌破止损线{stop_line:.2f}(-{stop_pct:.0%}),"
            f"止损条件单今日很可能触发,请确认券商条件单(系统不代下单)。"
        )
    if advisory:
        return (
            f"止损警戒:集合竞价开盘{quote.open:.2f}逼近止损线{stop_line:.2f}"
            f"(-{stop_pct:.0%}),当前较买入价{drawdown:.1%},离场决策在你。"
        )
    return (
        f"集合竞价开盘{quote.open:.2f}逼近止损线{stop_line:.2f}(-{stop_pct:.0%}),"
        f"当前较买入价{drawdown:.1%},盯紧条件单。"
    )


# —— D5 时间退出扫描(plan v1.1-B.2)————————————————————————————————————

@dataclass
class D5Exit:
    position_id: int
    ts_code: str
    name: str
    d: int          # = max_hold_days(读现役 config,不硬编 5)


def scan_d5_exits(
    positions: List[Position],
    trade_date: date,
    max_hold_days: Optional[int],
    *,
    names: Optional[Dict[str, str]] = None,
) -> List[D5Exit]:
    """单档 D5 时间退出(v1.1 原语,保留供直接单测/兜底)。对每只 open 持仓算 `d_count`,
    `== max_hold_days` → D5 时间退出(改 config 到 3 则 D3 触发)。`names` 供展示名(缺 →
    回 code)。**v1.3 两档口径见 `scan_time_exits`**(禁用两档 config 时二者结果一致)。

    **V2.2-⑤:`max_hold_days is None`(章程不设时间退出)→ 恒返空表**(显式早退,不靠
    `d == None` 恒假这条"碰巧对"的路径 —— 语义要写在脸上)。"""
    if max_hold_days is None:
        return []
    names = names or {}
    out: List[D5Exit] = []
    for p in positions:
        buy = datetime.strptime(p.buy_date, "%Y%m%d").date()
        if d_count(buy, trade_date) == max_hold_days:
            out.append(D5Exit(
                position_id=p.id, ts_code=p.ts_code,
                name=names.get(p.ts_code, p.ts_code), d=max_hold_days,
            ))
    return out


# —— v1.3 两档时间退出(§五 v1.3-①-C,净浮盈感知)————————————————————————————

@dataclass
class TimeExit:
    """两档时间退出条目(权威判定,`state` 决定推不推 + 客户端徽标档)。"""
    position_id: int
    ts_code: str
    name: str
    d: int                  # d_count(买入日=D1)
    state: str              # TIME_EXIT_NEXT_DAY | PROFIT_EXEMPT | HARD_CAP_EXIT | HOLDING
    # 该档有效硬上限(非浮盈=max_hold_days;浮盈/硬上限=max_hold_days_profit)。
    # **V2.2-⑤:章程不设时间退出(`max_hold_days=None`)→ 本字段 None**(没有"有效硬上限"
    # 这回事,⛔ 不拿 0 或默认天数冒充一个上限)。
    max_hold_effective: Optional[int]
    two_tier: bool = False   # 是否 v1.3 两档档(推送文案区分:两档非浮盈标「净浮盈 ≤0」,单档不标)


def has_time_exit_clause(cfg: MomentumConfig) -> bool:
    """现役章程**有没有时间退出条款**(V2.2-⑤ 唯一判据;`max_hold_days is None` = 没有)。

    ⚠ 这是 K8 §十三「时间退出让位主观换股权」在 config 层的**全部**表达:`v2.2-k8` 把
    `max_hold_days` / `max_hold_days_profit` 一并置 `None`、`time_exit_only_if_unprofitable`
    回落 `False`(免得留一个假旋钮)。判"有没有"只看 `max_hold_days` 这一位 —— 另两位在
    `None` 下都已无意义(见 §五 ⑤ config 逐字段改动表)。"""
    return cfg.max_hold_days is not None


def is_two_tier_time_exit(cfg: MomentumConfig) -> bool:
    """是否启用 v1.3 条件时间退出档(需 config 显式开 + 设浮盈硬上限;否则退回 v1.1 单档)。
    **V2.2-⑤:压根没有时间退出条款时恒 False**(两档是"时间退出怎么判",不是"判不判")。"""
    return (
        has_time_exit_clause(cfg)
        and bool(cfg.time_exit_only_if_unprofitable)
        and cfg.max_hold_days_profit is not None
    )


def classify_time_exit(
    d: int, cfg: MomentumConfig, net_float: Optional[float] = None
) -> tuple[str, Optional[int]]:
    """**判向定格时刻**的时间退出分类(纯函数)。返回 `(state, max_hold_effective)`。

    ⚠ **调用边界(2026-07-27 审计 🔴-1 修复后收紧,别再当通用分类器用)**:两档启用时,
    本函数只允许在**判向定格的那一刻**被调用 —— 即 16:35 报告管线首次遇到某持仓
    `d ≥ max_hold_days` 且库里尚无定格记录的那一天(`report/holding_k4_check.py`)。
    **所有消费点(precall 盘前 / GET /positions / 后续每日 16:35)一律改用
    `resolve_time_exit(d, cfg, locked_state)` 读定格值**,那个函数压根没有 `net_float`
    参数,结构上就不可能用「当日最新净浮盈」重判。

    · 默认档(未启用两档,现役 K1):`d≥max_hold_days` → TIME_EXIT_NEXT_DAY,否则 HOLDING
      (与 `PositionOut.todayAction` 的 `>=` 口径一致)。**单档不涉净浮盈,无定格概念**,
      本函数与 `resolve_time_exit` 在单档下行为逐位相同。
    · v1.3 两档:`d≥max_hold_days_profit` → HARD_CAP_EXIT(硬上限无条件,不看净浮盈也不看
      定格);`d≥max_hold_days` 时按净浮盈——`>0` → PROFIT_EXEMPT(续持至硬上限)、`≤0`/未知
      → TIME_EXIT_NEXT_DAY;`d<max_hold_days` → HOLDING。**净浮盈未知(None)保守判非浮盈**
      (豁免需正向证据,与 h9 V1 停牌不豁免同理)。
    · **V2.2-⑤ 章程无时间退出条款**(`max_hold_days is None`,`v2.2-k8` 起)→ 恒
      `(HOLDING, None)`,**永不定格、永不产生 actionable 判向**。HOLDING 的既有语义
      「未到时间退出判定点」在这里刚好精确:没有判定点,就永远到不了。
    """
    if not has_time_exit_clause(cfg):
        return HOLDING, None
    if not is_two_tier_time_exit(cfg):
        return (TIME_EXIT_NEXT_DAY if d >= cfg.max_hold_days else HOLDING), cfg.max_hold_days
    if d >= cfg.max_hold_days_profit:
        return HARD_CAP_EXIT, cfg.max_hold_days_profit
    if d >= cfg.max_hold_days:
        if net_float is not None and net_float > 0:
            return PROFIT_EXEMPT, cfg.max_hold_days_profit
        return TIME_EXIT_NEXT_DAY, cfg.max_hold_days
    return HOLDING, cfg.max_hold_days


def resolve_time_exit(
    d: int, cfg: MomentumConfig, locked_state: Optional[str] = None,
    *, data_unavailable: bool = False,
) -> tuple[str, Optional[int]]:
    """**消费点**的时间退出状态解析(单一源;precall / 16:35 / `GET /positions` 三处共用)。

    语义 = 2026-07-27 用户拍板的**方案 A「D5 判一次定格」**(审计 🔴-1):D5 那天在 16:35
    定格判向并落库(`holding_eod_check.time_exit_locked_state`),此后各消费点**读定格值**,
    **不再用当日最新净浮盈重判**。理由(勿删,写进 §2.1/§五 v1.3-①):
      ① **回测验证过的规则才是能守的规则** —— 引擎 `momentum.py::_time_exit_reason` 就是
         「D5 判一次、豁免后 `_eff_max` 一次性抬到硬上限」;实盘逐日重判 = 实盘执行的规则
         从未被任何回测验证过。
      ② 定格堵死「D5 判该走 → 用户没走 → D6 转浮盈 → D7 系统改口豁免」这条**违纪被事后
         合法化**的路(审计实测的反向漏洞,比正向偏差更重)。

    **本函数刻意不接受 `net_float`** —— 结构上杜绝「消费点顺手重判一次」。定格发生在
    `classify_time_exit`(仅 16:35 首次到达判定点时调用)。

    参数 `locked_state`:库里的定格判向(`PROFIT_EXEMPT` | `TIME_EXIT_NEXT_DAY`),
    None = 尚未定格。返回 `(state, max_hold_effective)`:
      · 单档(现役 K1)→ 与 `classify_time_exit` 逐位相同,`locked_state` 被忽略(单档退出
        与浮亏浮盈无关,无定格概念)。
      · 两档 + `d≥max_hold_days_profit` → HARD_CAP_EXIT(**硬上限仍按 d_count 判,不受定格
        影响**,用户拍板明示的例外)。
      · 两档 + `d≥max_hold_days`:有定格 → 原样返回定格判向;**无定格 → 保守判
        TIME_EXIT_NEXT_DAY**(豁免需正向证据;正常生产 16:35 先于次日 9:25:30 跑,故此分支
        只在 EOD 管线当天断跑等异常下走到,诚实偏保守而非偏豁免)。
      · 两档 + `d<max_hold_days` → HOLDING。

    **v1.4-①-B 第五态 `data_unavailable`(§七 P0-2,唯一新增分支)**:该持仓票**当日无 EOD
    行**(停牌 / 数据缺口)时传 True。仅当「到判定点(`d≥max_hold_days`)且**尚无定格**」
    才改判 `SUSPENDED_HOLD`(判向挂起,不推任何提醒);其余一律走原路径:
      · `data_unavailable=False`(缺省)→ 本函数与 v1.4 之前**逐位相同**(K1/两档均是),
        `EOD 管线断跑` 这类无定格情形仍保守判 TIME_EXIT_NEXT_DAY,一字不改。
      · **已有定格 → 定格值优先,挂起不生效**:判向是在有真数据的那天一次性做出的决定
        (审计 🔴-1),停牌不能事后把它撤回 —— 否则「D5 判该走 → 用户没走 → 停牌 → 系统
        改口」又是一条违纪被事后合法化的路。
      · `d<max_hold_days` → 仍 HOLDING(压根还没到判定点,无需挂起)。

    **V2.2-⑤ 章程无时间退出条款**(`max_hold_days is None`,`v2.2-k8` 起)→ **最先短路**、
    恒 `(HOLDING, None)`:连"判定点"都不存在,`data_unavailable` 的挂起态也就无从谈起
    (挂起是"该判但今天判不了",这里是"根本不判")。⛔ 这一位必须在 `data_unavailable`
    分支**之前**,否则 `d >= None` 直接 TypeError。
    """
    if not has_time_exit_clause(cfg):
        return HOLDING, None
    if data_unavailable and locked_state is None and d >= cfg.max_hold_days:
        two_tier = is_two_tier_time_exit(cfg)
        eff = (cfg.max_hold_days_profit
               if two_tier and d >= cfg.max_hold_days_profit else cfg.max_hold_days)
        return SUSPENDED_HOLD, eff
    if not is_two_tier_time_exit(cfg):
        return (TIME_EXIT_NEXT_DAY if d >= cfg.max_hold_days else HOLDING), cfg.max_hold_days
    if d >= cfg.max_hold_days_profit:
        return HARD_CAP_EXIT, cfg.max_hold_days_profit
    if d >= cfg.max_hold_days:
        if locked_state == PROFIT_EXEMPT:
            return PROFIT_EXEMPT, cfg.max_hold_days_profit
        return TIME_EXIT_NEXT_DAY, cfg.max_hold_days
    return HOLDING, cfg.max_hold_days


def scan_time_exits(
    positions: List[Position],
    trade_date: date,
    cfg: MomentumConfig,
    locked_state_provider: Optional[Callable[[Position], Optional[str]]] = None,
    *,
    names: Optional[Dict[str, str]] = None,
    data_unavailable_provider: Optional[Callable[[Position], bool]] = None,
) -> List[TimeExit]:
    """两档时间退出扫描(§五 v1.3-①-C;9:25:30 盘前执行提醒的取数入口)。

    **本函数是纯消费点,不做判向**(2026-07-27 审计 🔴-1 修复):判向由 16:35 报告管线在
    D5 当天定格落库,这里只经 `locked_state_provider`(注入,读
    `report/holding_store.locked_state_provider`)把定格值取回来,交 `resolve_time_exit`
    解析。**净浮盈根本不进本函数**——盘前 9:25:30 当日收盘未出,历史上用「上一份 EOD 快照
    的净浮盈重判」正是审计查出的分叉根因。

    · **config 未启用两档**(`time_exit_only_if_unprofitable=False`,现役 K1)→ 退回单档
      `d==max_hold_days` = 与 v1.1 `scan_d5_exits` **完全一致**(兜底,防未激活章程时行为漂移;
      provider 在此分支根本不被触及)。
    · **两档启用**→ 每只到判定点的持仓给三态之一(TIME_EXIT_NEXT_DAY / PROFIT_EXEMPT /
      HARD_CAP_EXIT);HOLDING(未到 D5)不 emit。profit_exempt 也 emit(供看板/记录),但
      **不进 D5 执行提醒推送**(调用方按 `state in _ACTIONABLE_TIME_EXIT` 过滤,§五 v1.3-①-D)。

    · **v1.4-①-B `data_unavailable_provider`(注入,读
      `report/holding_store.data_unavailable_provider`)** —— 那只票在最近一份 16:35 体检里
      是不是「当日无 EOD 行」。**这条盘前路径是 P0-2 病根最尖锐的形态**:9:26 汇总推送会把
      「D5 该走」直接推到用户锁屏,而那只票**今天根本卖不掉**。挂起态(`SUSPENDED_HOLD`)不在
      `_ACTIONABLE_TIME_EXIT` 里,自然不推。查无快照 / 老快照没记这一位 → **按 False**
      (保守,维持既有推送行为;豁免需正向证据)。

    · **V2.2-⑤ 章程无时间退出条款**(`max_hold_days is None`)→ **恒返空表**:零判定、零
      看板事件、零推送(⑤ 验收「当晚哨兵 journal 里零时间退出判定」的落点)。
    """
    if not has_time_exit_clause(cfg):
        return []
    names = names or {}
    two_tier = is_two_tier_time_exit(cfg)
    out: List[TimeExit] = []
    for p in positions:
        buy = datetime.strptime(p.buy_date, "%Y%m%d").date()
        d = d_count(buy, trade_date)
        name = names.get(p.ts_code, p.ts_code)
        no_data = bool(data_unavailable_provider(p)) if data_unavailable_provider is not None else False
        if not two_tier:
            # 单档兜底 = v1.1 D5 行为(恰达 max_hold_days 当天,== 语义,与 scan_d5_exits 一致);
            # v1.4-①-B:该票停牌/无当日行情则同样挂起(不推),其余逐位不变。
            if d == cfg.max_hold_days and not no_data:
                out.append(TimeExit(p.id, p.ts_code, name, d, TIME_EXIT_NEXT_DAY, cfg.max_hold_days, two_tier=False))
            continue
        locked = locked_state_provider(p) if locked_state_provider is not None else None
        state, eff = resolve_time_exit(d, cfg, locked, data_unavailable=no_data)
        if state != HOLDING:
            out.append(TimeExit(p.id, p.ts_code, name, d, state, eff, two_tier=True))
    return out


# —— 单拍编排 ————————————————————————————————————————————————————————

@dataclass
class PrecallResult:
    trade_date: date
    now: datetime
    ran: bool = False                      # 本次调用是否真的执行(未入窗 / 当日已跑 → False)
    skipped_reason: str = ""               # "not_precall_window" | "already_ran" | ""
    gap_up: List[str] = field(default_factory=list)              # 买点变形候选代码
    low_open: List[str] = field(default_factory=list)            # 开盘证伪候选代码
    auction: List[str] = field(default_factory=list)             # 竞价量能异常代码(附注)
    position_low_open: List[str] = field(default_factory=list)   # 持仓止损预警代码
    # 🟡-2:冻结锚今日失效(疑似除权除息)→ 该票前两类判定**没做**。**附注,不是判定**
    # ——「没做」必须与「做了没异常」分得开,所以它有自己的位置而不是被静默省略。
    member_ex_rights: List[str] = field(default_factory=list)
    d5_exits: List["TimeExit"] = field(default_factory=list)     # 需推的两档时间退出(actionable)
    # ⚠ V2.2-⑤-B:`circuit_locked` 字段已随熔断整体退役删除(裁定 #8),⛔ 别加回来。
    watched_codes: int = 0
    quotes_fetched: int = 0

    @property
    def counts(self) -> Dict[str, int]:
        return {
            "gap_up": len(self.gap_up),
            "low_open": len(self.low_open),
            "position_low_open": len(self.position_low_open),
            "auction": len(self.auction),
            "member_ex_rights": len(self.member_ex_rights),
        }

    @property
    def summary_actionable(self) -> int:
        """盘前四类判定中「需要动作」的条数:买点变形 + 开盘证伪 + 持仓预警之和(竞价量能
        异常是附注、不计入,避免每个平静清晨都轰炸)。**注意:这是判定计数,不是推送门槛**
        ——门槛见 `should_push_summary`。

        🟡-2 的 `member_ex_rights` **同样不计入**:它是「今天核对不了」的如实标注,不是
        「你得做点什么」。分红季若把它算进来,每天都会因为几只除权票而推一条盘前提醒。"""
        return len(self.gap_up) + len(self.low_open) + len(self.position_low_open)

    @property
    def should_push_summary(self) -> bool:
        """9:26 汇总推送的**门槛**(唯一源,`_sentinel_loop` 照此判)= **有需要动作的判定**。

        ⚠ **V2.2-⑤-B:原来的第二个析取项「或熔断锁定中」已删**(裁定 #8 熔断整体退役)。
        那一项是 2026-07-27 审计 🟡-4 加的「锁定期即便零判定也必发」豁免 —— **豁免随机制
        一并取消**,如实登记在 §八 第 19 项:**以后平静的清晨就是真的没推送**。
        ⛔ 别以"少了条提醒"为由把它以别的形式加回来。"""
        return self.summary_actionable > 0


def _resolve_config(db_path: Optional[Path]) -> tuple:
    """现役 config → (stop_pct, MomentumConfig)。MomentumConfig 携带 v1.3 两档时间退出字段
    (`max_hold_days_profit`/`time_exit_only_if_unprofitable`,未激活章程时吃默认 None/False
    → `scan_time_exits` 退回单档 = v1.1 行为)。无现役版本(异常)→ MomentumConfig 字段默认兜底。"""
    cfg_dict = brain.active_config(db_path=db_path)
    if not cfg_dict:
        logger.warning("策略大脑无现役版本,盘前校准止损/持有天数退回 MomentumConfig 兜底(非正常状态)")
        cfg = _FALLBACK_CFG
    else:
        cfg = MomentumConfig(**cfg_dict)
    # 兜底判据:章程**显式** stop_pct=None(不设止损)→ 返 0.0,不悄悄换回 0.05
    # (审计 🔵-9;0.0 时 `judge_position_low_open` 的止损线退化为买入价,不臆造止损位)。
    return float(cfg.stop_pct if cfg.stop_pct is not None else 0.0), cfg


def _time_exit_body(ex: "TimeExit") -> str:
    """两档 D5 执行提醒看板文案(§五 v1.3-①-D;profit_exempt 不走此路径,不推)。
    单档(K1,无条件时间退出)不写「净浮盈 ≤0」(那是两档判据,单档退出与浮亏浮盈无关);
    两档非浮盈单才标净浮盈 ≤0。"""
    if ex.state == HARD_CAP_EXIT:
        return f"{ex.name} 今日 D{ex.d} 已达浮盈硬上限 D{ex.max_hold_effective},按计划离场。"
    if ex.two_tier:
        return f"{ex.name} 今日 D{ex.d} 时间退出日(净浮盈 ≤0),按计划离场。"
    return f"{ex.name} 今日 D{ex.d} 时间退出日,按计划离场。"


def run_precall_tick(
    now: datetime,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    breadth_cap: int = DEFAULT_BREADTH_CAP,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Quote]]] = None,
) -> PrecallResult:
    """跑一次盘前校准 tick(9:25:30,当日只跑一次)。**只落看板 + 返回待推项**,APNs
    推送由 `_sentinel_loop` 调 `notify` 完成(照退潮刹车两机制惯例)。`quotes_fn` 可覆盖
    (默认 `sentinel.quotes.get_quotes`)——合成竞价快照冒烟(`scripts/smoke_precall.py`)
    据此注入,不改一行编排。

    幂等/防重顺序刻意如此:所有 APNs 推送都在本函数**返回之后**由循环执行,而市场级
    「tick 已跑」标记在返回**之前**落库——故本函数中途异常(标记未落)会被下一拍干净
    重跑(看板落库均 INSERT OR IGNORE,幂等),不会重复推送;正常完成后标记已落,后续
    30s 轮询直接跳过,汇总/ D5 各只推一次。
    """
    trade_date = now.date()
    result = PrecallResult(trade_date=trade_date, now=now)

    if not is_precall_window(now):
        result.skipped_reason = "not_precall_window"
        return result
    if already_pushed(trade_date, "precall", "", EVENT_TICK, db_path=db_path):
        result.skipped_reason = "already_ran"
        return result

    fetch = quotes_fn or (lambda codes: get_quotes(codes))
    wu: WatchUniverse = load_watch_universe(
        trade_date, breadth_cap=breadth_cap, db_path=db_path, parquet_dir=parquet_dir
    )
    quotes = fetch(wu.codes) if wu.codes else {}
    meta = load_stock_meta(wu.codes, db_path=db_path) if wu.codes else {}
    prev5 = load_prev5_avg_volume(wu.codes, trade_date, parquet_dir=parquet_dir) if wu.codes else {}
    stop_pct, cfg = _resolve_config(db_path)
    # V2.2-⑤:现役章程的止损口径(强制条件单 / 止损警戒),只换文案口吻、不改判定。
    # 判据单一源 `brain.stop_is_advisory`;`v2.2-k8` 激活前恒 False = 逐字节不变。
    stop_advisory = brain.active_stop_is_advisory(db_path=db_path)

    result.ran = True
    result.watched_codes = len(wu.codes)
    result.quotes_fetched = len(quotes)
    names = {code: m.name for code, m in meta.items()}

    def _record(sentinel: str, code: str, event_key: str, body: str) -> None:
        record_pushed(
            trade_date, sentinel, code, event_key,
            payload={"body": body}, db_path=db_path,
        )

    # —— 篮子竞价剧本核对(V2-⑬-7)→ 高开偏离 / 低开踩失效位 / 竞价量能 ——————————
    # 对象 = D0 冻结的 T1/T2 篮子成员;判据 = ⑦ 冻结在卡里的 ref_close / stop_line。
    # 有篮子无卡 / 卡里没这只 / 阈值为 null → 该票不判(见 `load_member_scripts`)。
    scripts = load_member_scripts(wu.baskets, db_path=db_path)
    for sc in scripts:
        q = quotes.get(sc.ts_code)
        if q is None:
            continue  # 拉不到竞价快照 → 该票无意见,跳过(不是「无异常」)
        # 🟡-2:⑧-E 锚失效检测**先于**前两类判定 —— 冻结锚今日失效时,拿原始开盘价去跟
        # 除权前尺度的价位比是错的比较,判出来的「开盘即失效」是假警(而它会进 9:26 锁屏
        # 推送)。⛔ 不自动 rescale;如实标一条附注,确诊留给今晚 ⑧-E EOD 的交叉确认。
        if member_anchor_stale(sc, q):
            result.member_ex_rights.append(sc.ts_code)
            _record("precall", sc.ts_code, EVENT_MEMBER_EX_RIGHTS, (
                f"竞价昨收{q.pre_close:.2f}与卡里冻结的 D0 收盘锚{sc.ref_close:.2f}不符,"
                f"疑似除权除息(或行情源异常)——冻结锚今日失效,本票盘前剧本核对跳过"
                f"(不是「无异常」);今晚 EOD 会用复权因子交叉确认成因。"
            ))
            auc_only = judge_auction_volume(q, prev5.get(sc.ts_code, 0.0))
            if auc_only:   # 竞价量能比的是量、不读冻结锚,不受锚失效影响,照常附注
                result.auction.append(sc.ts_code)
                _record("precall", sc.ts_code, EVENT_AUCTION, auc_only)
            continue
        gap_reason = judge_gap_up_invalidate(sc, q)
        if gap_reason:
            result.gap_up.append(sc.ts_code)
            _record("precall", sc.ts_code, EVENT_GAP_UP, gap_reason)
        else:
            low_reason = judge_low_open_falsify(sc, q)  # 高开与低开互斥,只判其一
            if low_reason:
                result.low_open.append(sc.ts_code)
                _record("precall", sc.ts_code, EVENT_LOW_OPEN, low_reason)
        auc_reason = judge_auction_volume(q, prev5.get(sc.ts_code, 0.0))
        if auc_reason:
            result.auction.append(sc.ts_code)
            _record("precall", sc.ts_code, EVENT_AUCTION, auc_reason)

    # —— 持仓 → 大幅低开逼近/跌破止损线 ————————————————————————————————
    for p in wu.positions:
        q = quotes.get(p.ts_code)
        if q is None:
            continue
        pos_reason = judge_position_low_open(p, q, stop_pct, advisory=stop_advisory)
        if pos_reason:
            result.position_low_open.append(p.ts_code)
            _record("precall", p.ts_code, EVENT_POS_LOW_OPEN, pos_reason)

    # —— 时间退出扫描(两档,§五 v1.3-①-C;折进本进程,独立于 kind=`precall` 开关)——————
    # **纯执行提醒,不做判向**(2026-07-27 审计 🔴-1 修复,用户拍板方案 A):判向由 16:35
    # 报告管线在 D5 当天用 EOD 收盘净浮盈**定格**落库,盘前只经
    # `holding_store.locked_state_provider` 把定格值读回来(查无定格 → None → `resolve_time_exit`
    # 保守判 time_exit_next_day)。**此处不再读 net_float**——旧写法(读上一份 EOD 快照的净浮盈
    # 重判)会让 D5 判该走的单子在 D6/D7 转浮盈后被系统改口豁免,把违纪事后合法化。
    # **未启用两档 config(现役 K1)时 scan_time_exits 退回单档 == max_hold_days = v1.1 D5 行为
    # 完全一致**(is_two_tier_time_exit=False,provider 根本不被触及)。只对 actionable 两档
    # (time_exit_next_day / hard_cap_exit)落看板 + 推;profit_exempt 不推 D5 执行提醒
    # (客户端徽标经 PositionOut 表达,§五 v1.3-①-D)。
    from neckline.report.holding_store import (
        data_unavailable_provider as _nodata_provider,
        locked_state_provider as _locked_provider,
    )
    time_exits = scan_time_exits(
        wu.positions, trade_date, cfg,
        locked_state_provider=_locked_provider(db_path=db_path), names=names,
        # v1.4-①-B:停牌/无当日行情的持仓票判向挂起 → 不进 actionable → **不推**
        # (9:26 汇总推送把「D5 该走」推到锁屏,而那只票今天根本卖不掉,§七 P0-2)。
        data_unavailable_provider=_nodata_provider(db_path=db_path),
    )
    actionable = [ex for ex in time_exits if ex.state in _ACTIONABLE_TIME_EXIT]
    for ex in actionable:
        _record("d5exit", ex.ts_code, D5EXIT_EVENT_KEY, _time_exit_body(ex))
    result.d5_exits = actionable

    # ⚠ **V2.2-⑤-B:原「熔断锁定态 →『今日只减不加』盘前强提醒」整段已删**(裁定 #8)。
    # 盘前 tick 自此**不读任何熔断/锁定态、不落任何市场级"别开仓"事件**;
    # ⛔ 不许以任何名义在这里补一个自动状态位(§五 〇b-7「用户明确说了不要程序替他做决定」)。

    # —— 市场级「当日 tick 已跑」标记(返回前落,见函数 docstring 的幂等说明)————
    record_pushed(trade_date, "precall", "", EVENT_TICK, payload={"counts": result.counts}, db_path=db_path)
    return result


__all__ = [
    "PRECALL_GAP_UP_INVALIDATE",
    "PRECALL_AUCTION_VOL_HIGH_FRAC",
    "PRECALL_AUCTION_VOL_LOW_FRAC",
    "is_precall_window",
    "member_anchor_stale",
    "judge_gap_up_invalidate",
    "judge_low_open_falsify",
    "judge_auction_volume",
    "judge_position_low_open",
    "scan_d5_exits",
    "D5Exit",
    "TimeExit",
    "scan_time_exits",
    "classify_time_exit",
    "resolve_time_exit",
    "has_time_exit_clause",
    "is_two_tier_time_exit",
    "TIME_EXIT_NEXT_DAY",
    "PROFIT_EXEMPT",
    "HARD_CAP_EXIT",
    "HOLDING",
    "SUSPENDED_HOLD",
    "PrecallResult",
    "run_precall_tick",
    "EVENT_GAP_UP",
    "EVENT_LOW_OPEN",
    "EVENT_AUCTION",
    "EVENT_MEMBER_EX_RIGHTS",
    "EVENT_POS_LOW_OPEN",
    "EVENT_TICK",
    "D5EXIT_EVENT_KEY",
]
