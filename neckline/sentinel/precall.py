"""盘前校准 tick(plan v1.1-A,§2.4 第 5 类哨兵动作)。**纯规则判定、零 LLM、零
资金面**——交易日 9:25:30(集合竞价撮合后快照即含开盘价 / 竞价量)跑一次,只判
「前晚报告的计划是否已被集合竞价作废 / 预警」,**不产新票、不推荐买入**(§2.4 铁
原则的执行层落点,不是选新票)。现有 9:35 起的 intraday 判定逻辑(`engine.run_tick`)
一字不改。

四类判定(全从 `Quote` + 已落库的 `entry_spec` / `invalidation_spec` + 派生 stopLine 算):
    1. 候选高开超阈 →「买点已变形今日失效」:`open` 相对候选买点(pullback 型 ma10 /
       breakout 型 platform_high,读 `Candidate.entry_spec`)高开超 `PRECALL_GAP_UP_INVALIDATE`
       → 今日买点作废。
    2. 低开踩证伪线 →「开盘即证伪预警」:`open` 触发候选 `invalidation_spec` 的低开阈
       (`low_open_pct`,复用阶段 3 写死值,不新造)→ 证伪预警。
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
(9:25:30 已在跑,一次唤醒同时做校准 + D5,省一次进程唤醒),且**独立于 push_precall
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
from typing import Callable, Dict, List, Optional

from neckline.calendar import is_trading_day
from neckline.report.candidates import Candidate
from neckline.sentinel.dedup import already_pushed, record_pushed
from neckline.sentinel.holding import STOP_APPROACH_BUFFER
from neckline.sentinel.positions import Position, d_count
from neckline.sentinel.quotes import Quote, get_quotes
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
# 市场级「当日盘前 tick 已跑」标记(ts_code 空,不进看板事件列表,见 api board 过滤)。
EVENT_TICK = "tick"
# D5 时间退出:sentinel="d5exit",ts_code=持仓代码,event_key 固定(当日每票防重一次)。
D5EXIT_EVENT_KEY = "trigger"

# —— v1.3 两档时间退出状态码(§五 v1.3-①-C;= 客户端契约 `PositionOut.timeExitState`)——
TIME_EXIT_NEXT_DAY = "time_exit_next_day"  # 非浮盈单:d≥max_hold_days 且净浮盈 ≤0 → 次日退出(推)
PROFIT_EXEMPT = "profit_exempt"            # 浮盈单:d≥max_hold_days 且净浮盈 >0 → 续持至硬上限(不推)
HARD_CAP_EXIT = "hard_cap_exit"            # d≥max_hold_days_profit → 硬上限无条件次日退出(推)
HOLDING = "holding"                        # d<max_hold_days,未到时间退出判定点(不推)
# 需盘前汇总推送的两档(profit_exempt/holding 不推 D5 执行提醒,§五 v1.3-①-D)。
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

def _entry_ref_level(candidate: Candidate) -> Optional[float]:
    """候选买点参考位:breakout 型取 platform_high、其余(pullback/either/none)取 ma10
    (与买点哨兵 `entry.py` 的分支口径一致)。缺失 / 非正 → None。"""
    spec = candidate.entry_spec or {}
    if spec.get("buypoint") == "breakout":
        ref = spec.get("platform_high")
    else:
        ref = spec.get("ma10")
    if ref is None or ref <= 0:
        return None
    return float(ref)


def judge_gap_up_invalidate(
    candidate: Candidate, quote: Quote, threshold: float = PRECALL_GAP_UP_INVALIDATE
) -> Optional[str]:
    """候选集合竞价开盘高于买点参考位超阈 →「买点已变形今日失效」。买点参考位缺失
    或 open≤0 → None(数据不足,不妄判)。"""
    ref = _entry_ref_level(candidate)
    if ref is None or quote.open <= 0:
        return None
    gap = quote.open / ref - 1
    if gap >= threshold - _EPS:
        return (
            f"集合竞价开盘{quote.open:.2f}高于买点参考位{ref:.2f} {gap:.1%}"
            f"(超阈{threshold:.0%}),今日买点已变形失效——勿追高开缺口。"
        )
    return None


def judge_low_open_falsify(candidate: Candidate, quote: Quote) -> Optional[str]:
    """候选集合竞价开盘踩证伪线(复用 `invalidation_spec.low_open_pct`,不新造)→
    「开盘即证伪预警」。盘中证伪还要求「截至目前未翻红」,但集合竞价阶段 open 即当前价、
    无「盘中翻红」可言,故此处只以 open 低开幅度判定(哨兵是提前预警,不必等盘中)。"""
    spec = candidate.invalidation_spec or {}
    low_open_pct = spec.get("low_open_pct")
    if low_open_pct is None or quote.pre_close <= 0 or quote.open <= 0:
        return None
    gap = (quote.open - quote.pre_close) / quote.pre_close
    if gap <= low_open_pct + _EPS:
        return (
            f"集合竞价低开{gap:.1%}(证伪线{low_open_pct:.0%}),开盘即证伪预警"
            f"——前晚计划今日大概率不成立。"
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
    position: Position, quote: Quote, stop_pct: float, buffer_pct: float = STOP_APPROACH_BUFFER
) -> Optional[str]:
    """持仓集合竞价大幅低开逼近 / 跌破派生 stopLine →「止损条件单今日可能触发」。
    `stopLine = buy×(1−stop_pct)`(读现役 config,不硬编)。逼近提前量复用 holding 的
    `STOP_APPROACH_BUFFER`(单一源)。**系统永不代下单**,只提醒确认券商条件单。"""
    if quote.open <= 0 or position.buy_price <= 0:
        return None
    stop_line = position.buy_price * (1 - stop_pct)
    drawdown = (position.buy_price - quote.open) / position.buy_price
    warn_from = max(stop_pct - buffer_pct, 0.0)
    if drawdown < warn_from - _EPS:
        return None
    if quote.open <= stop_line + _EPS * position.buy_price:
        return (
            f"集合竞价开盘{quote.open:.2f}已跌破止损线{stop_line:.2f}(-{stop_pct:.0%}),"
            f"止损条件单今日很可能触发,请确认券商条件单(系统不代下单)。"
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
    max_hold_days: int,
    *,
    names: Optional[Dict[str, str]] = None,
) -> List[D5Exit]:
    """单档 D5 时间退出(v1.1 原语,保留供直接单测/兜底)。对每只 open 持仓算 `d_count`,
    `== max_hold_days` → D5 时间退出(改 config 到 3 则 D3 触发)。`names` 供展示名(缺 →
    回 code)。**v1.3 两档口径见 `scan_time_exits`**(禁用两档 config 时二者结果一致)。"""
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
    max_hold_effective: int  # 该档有效硬上限(非浮盈=max_hold_days;浮盈/硬上限=max_hold_days_profit)
    two_tier: bool = False   # 是否 v1.3 两档档(推送文案区分:两档非浮盈标「净浮盈 ≤0」,单档不标)


def is_two_tier_time_exit(cfg: MomentumConfig) -> bool:
    """是否启用 v1.3 条件时间退出档(需 config 显式开 + 设浮盈硬上限;否则退回 v1.1 单档)。"""
    return bool(cfg.time_exit_only_if_unprofitable) and cfg.max_hold_days_profit is not None


def classify_time_exit(
    d: int, cfg: MomentumConfig, net_float: Optional[float] = None
) -> tuple[str, int]:
    """单只持仓的时间退出状态分类(纯函数,供 `scan_time_exits` 与 `PositionOut` 派生共用,
    单一源)。返回 `(state, max_hold_effective)`。

    · 默认档(未启用两档):`d≥max_hold_days` → TIME_EXIT_NEXT_DAY,否则 HOLDING(与
      `PositionOut.todayAction` 的 `>=` 口径一致)。
    · v1.3 两档:`d≥max_hold_days_profit` → HARD_CAP_EXIT;`d≥max_hold_days` 时按净浮盈——
      `>0` → PROFIT_EXEMPT(续持至硬上限)、`≤0`/未知 → TIME_EXIT_NEXT_DAY;`d<max_hold_days`
      → HOLDING。**净浮盈未知(None)保守判非浮盈**(豁免需正向证据,与 h9 V1 停牌不豁免同理)。
    """
    if not is_two_tier_time_exit(cfg):
        return (TIME_EXIT_NEXT_DAY if d >= cfg.max_hold_days else HOLDING), cfg.max_hold_days
    if d >= cfg.max_hold_days_profit:
        return HARD_CAP_EXIT, cfg.max_hold_days_profit
    if d >= cfg.max_hold_days:
        if net_float is not None and net_float > 0:
            return PROFIT_EXEMPT, cfg.max_hold_days_profit
        return TIME_EXIT_NEXT_DAY, cfg.max_hold_days
    return HOLDING, cfg.max_hold_days


def scan_time_exits(
    positions: List[Position],
    trade_date: date,
    cfg: MomentumConfig,
    net_float_provider: Optional[Callable[[Position], Optional[float]]] = None,
    *,
    names: Optional[Dict[str, str]] = None,
) -> List[TimeExit]:
    """两档时间退出权威扫描(§五 v1.3-①-C)。**权威计算落 16:35 报告管线(EOD,D5 收盘
    后)**——「第 5 日收盘净浮盈」是 EOD 量,precall 9:25:30 时 D5 收盘未出,故净浮盈由
    `net_float_provider`(注入,读 EOD 面板 / 16:35 持久化计划)提供;None → 保守判非浮盈。

    · **config 未启用两档**(`time_exit_only_if_unprofitable=False`)→ 退回单档 `d==max_hold_days`
      = 与 v1.1 `scan_d5_exits` **完全一致**(兜底,防未激活章程时行为漂移)。
    · **两档启用**→ 每只到判定点的持仓给三态之一(TIME_EXIT_NEXT_DAY / PROFIT_EXEMPT /
      HARD_CAP_EXIT);HOLDING(未到 D5)不 emit。profit_exempt 也 emit(供看板/记录),但
      **不进 D5 执行提醒推送**(调用方按 `state in _ACTIONABLE_TIME_EXIT` 过滤,§五 v1.3-①-D)。
    """
    names = names or {}
    two_tier = is_two_tier_time_exit(cfg)
    out: List[TimeExit] = []
    for p in positions:
        buy = datetime.strptime(p.buy_date, "%Y%m%d").date()
        d = d_count(buy, trade_date)
        name = names.get(p.ts_code, p.ts_code)
        if not two_tier:
            # 单档兜底 = v1.1 D5 行为(恰达 max_hold_days 当天,== 语义,与 scan_d5_exits 一致)
            if d == cfg.max_hold_days:
                out.append(TimeExit(p.id, p.ts_code, name, d, TIME_EXIT_NEXT_DAY, cfg.max_hold_days, two_tier=False))
            continue
        nf = net_float_provider(p) if net_float_provider is not None else None
        state, eff = classify_time_exit(d, cfg, nf)
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
    d5_exits: List["TimeExit"] = field(default_factory=list)     # 需推的两档时间退出(actionable)
    watched_codes: int = 0
    quotes_fetched: int = 0

    @property
    def counts(self) -> Dict[str, int]:
        return {
            "gap_up": len(self.gap_up),
            "low_open": len(self.low_open),
            "position_low_open": len(self.position_low_open),
            "auction": len(self.auction),
        }

    @property
    def summary_actionable(self) -> int:
        """触发 9:26 汇总推送的门槛:买点变形 + 开盘证伪 + 持仓预警之和(竞价量能异常
        是附注、不单独触发推送,避免每个平静清晨都轰炸)。"""
        return len(self.gap_up) + len(self.low_open) + len(self.position_low_open)


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
    stop_pct = cfg.stop_pct or _FALLBACK_CFG.stop_pct
    return float(stop_pct), cfg


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

    result.ran = True
    result.watched_codes = len(wu.codes)
    result.quotes_fetched = len(quotes)
    names = {code: m.name for code, m in meta.items()}

    def _record(sentinel: str, code: str, event_key: str, body: str) -> None:
        record_pushed(
            trade_date, sentinel, code, event_key,
            payload={"body": body}, db_path=db_path,
        )

    # —— 候选四件套 → 高开变形 / 低开证伪 / 竞价量能(三类判定)——————————————
    # v1.1-C.2「自选票享候选同级待遇」:候选 ∪「昨晚体检已触发买点」的自选票
    # 一视同仁(entry_spec/invalidation_spec 均是昨晚写死,盘前只读不重算)。
    for c in wu.candidates + wu.watchlist_candidates:
        q = quotes.get(c.ts_code)
        if q is None:
            continue  # 拉不到竞价快照 → 该票无意见,跳过(不是「无异常」)
        gap_reason = judge_gap_up_invalidate(c, q)
        if gap_reason:
            result.gap_up.append(c.ts_code)
            _record("precall", c.ts_code, EVENT_GAP_UP, gap_reason)
        else:
            low_reason = judge_low_open_falsify(c, q)  # 高开与低开互斥,只判其一
            if low_reason:
                result.low_open.append(c.ts_code)
                _record("precall", c.ts_code, EVENT_LOW_OPEN, low_reason)
        auc_reason = judge_auction_volume(q, prev5.get(c.ts_code, 0.0))
        if auc_reason:
            result.auction.append(c.ts_code)
            _record("precall", c.ts_code, EVENT_AUCTION, auc_reason)

    # —— 持仓 → 大幅低开逼近/跌破止损线 ————————————————————————————————
    for p in wu.positions:
        q = quotes.get(p.ts_code)
        if q is None:
            continue
        pos_reason = judge_position_low_open(p, q, stop_pct)
        if pos_reason:
            result.position_low_open.append(p.ts_code)
            _record("precall", p.ts_code, EVENT_POS_LOW_OPEN, pos_reason)

    # —— 时间退出扫描(两档,§五 v1.3-①-C;折进本进程,独立于 push_precall 开关)——————
    # **v1.3-② seam 已接**:net_float_provider 读上一交易日 16:35 持久化的 D5 收盘净浮盈
    # (`holding_store.net_float_provider`)——precall 9:25:30 时当日 D5 收盘未出,权威净浮盈
    # 是 EOD 量,故读最近一份 EOD 快照。这修复 v1.3-① 留的「provider 恒 None → 激活后所有单子
    # 保守判非浮盈、浮盈豁免形同虚设」的地基缺口(⑦ 章程激活前置)。查无快照(刚开仓未体检)
    # → None 保守。**未启用两档 config(现役 K1)时 scan_time_exits 退回单档 == max_hold_days =
    # v1.1 D5 行为完全一致**(is_two_tier_time_exit=False,provider 根本不被触及)。只对 actionable
    # 两档(time_exit_next_day / hard_cap_exit)落看板 + 推;profit_exempt 不推 D5 执行提醒
    # (客户端徽标经 PositionOut 表达,§五 v1.3-①-D)。
    from neckline.report.holding_store import net_float_provider as _nf_provider
    time_exits = scan_time_exits(
        wu.positions, trade_date, cfg, net_float_provider=_nf_provider(db_path=db_path), names=names
    )
    actionable = [ex for ex in time_exits if ex.state in _ACTIONABLE_TIME_EXIT]
    for ex in actionable:
        _record("d5exit", ex.ts_code, D5EXIT_EVENT_KEY, _time_exit_body(ex))
    result.d5_exits = actionable

    # —— 市场级「当日 tick 已跑」标记(返回前落,见函数 docstring 的幂等说明)————
    record_pushed(trade_date, "precall", "", EVENT_TICK, payload={"counts": result.counts}, db_path=db_path)
    return result


__all__ = [
    "PRECALL_GAP_UP_INVALIDATE",
    "PRECALL_AUCTION_VOL_HIGH_FRAC",
    "PRECALL_AUCTION_VOL_LOW_FRAC",
    "is_precall_window",
    "judge_gap_up_invalidate",
    "judge_low_open_falsify",
    "judge_auction_volume",
    "judge_position_low_open",
    "scan_d5_exits",
    "D5Exit",
    "TimeExit",
    "scan_time_exits",
    "classify_time_exit",
    "is_two_tier_time_exit",
    "TIME_EXIT_NEXT_DAY",
    "PROFIT_EXEMPT",
    "HARD_CAP_EXIT",
    "HOLDING",
    "PrecallResult",
    "run_precall_tick",
    "EVENT_GAP_UP",
    "EVENT_LOW_OPEN",
    "EVENT_AUCTION",
    "EVENT_POS_LOW_OPEN",
    "EVENT_TICK",
    "D5EXIT_EVENT_KEY",
]
