"""买点哨兵(plan §2.4 第1条)。**铁律:只执行前晚报告写死的买点计划,不产生任何
新决策**——本模块只读 `Candidate.entry_spec`(阶段2 报告生成时算好、结构化写死,
见 `neckline.report.candidates.entry_spec`),逐字实现「候选触达预设买点 **且**
确认条件成立(量能折算、站稳VWAP)才推送」这句话,不额外发明选股逻辑。

判定分两层(两层都要过,任一层不过 → 不推送):
    1. **触达预设买点**(entry_spec 已写死的触发条件,按 `entry_spec["buypoint"]`
       分支):
        · breakout:现价突破 `entry_spec["platform_high"]`(前20日收盘高点)。
        · pullback / either / none(其余情形按 pullback 处理,呼应
          `candidates.entry_plan_text` 对未知 buypoint 的同一套退化文案):
          现价站稳 `entry_spec["ma10"]`(不破位)且开盘未大幅高开(避免"追高开
          缺口",呼应 `entry_plan_text` 原文"不追高开缺口")。
        · either:pullback 与 breakout 任一满足即算触达。
    2. **确认条件成立**(§2.4 原文列的两项,通用于任何 buypoint):
        · 量能折算:`intraday.intraday_vol_ratio` 折算全天量与前5日均量之比。
          breakout 型复用报告当晚已定的 `entry_spec["breakout_vol_expand"]`
          门槛(不重新拍一个数,§3.8 规则常量单一源);pullback 型量能定义是
          "缩量整理"而非放量,只要求不是地量死水(`ENTRY_PULLBACK_MIN_VOL_RATIO`,
          **未回测的启发式常量**,诚实标注同 `report.sentiment` 的既有风格)。
        · 站稳VWAP:`intraday.vwap_of` 现价 ≥ 当日VWAP。
    开盘头 `MIN_STRUCTURAL_ELAPSED_MINUTES` 分钟(集合竞价/极早盘噪声大)一律不判断
    ——不是"不确认",是"数据还不够格做判断",与 §2.4 精神一致(宁可晚一点推送,
    不误推)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from neckline.report.candidates import Candidate
from neckline.sentinel.intraday import elapsed_trading_minutes, intraday_vol_ratio, vwap_of
from neckline.sentinel.quotes import Quote

# 开盘头几分钟集合竞价延续 + 极早盘噪声大,VWAP/价格结构判断均不可靠,先按兵不动
# (量能折算另有更严的 60min 阈,见 intraday.EARLY_MINUTES_THRESHOLD)。
MIN_STRUCTURAL_ELAPSED_MINUTES = 5

# pullback 型买点的量能确认下限(**未回测,启发式**——pullback 定义即"缩量整理",
# 不应像 breakout 那样要求放量,只要求不是无人问津的地量死水)。
ENTRY_PULLBACK_MIN_VOL_RATIO = 0.8

# 开盘涨幅超过此值视为"追高开缺口",不再算"低吸"入场时机(与
# `report.candidates.LOW_OPEN_PCT=-0.02` 同量级,方向相反:一个防高开追高,
# 一个防低开破位,呼应同一份"别在情绪化的位置进出"的纪律直觉)。
PULLBACK_MAX_GAP_OPEN = 0.02


@dataclass
class EntrySignal:
    ts_code: str
    name: str
    price: float
    vol_ratio: Optional[float]
    vwap: Optional[float]
    reason: str


def _reached_pullback(spec: dict, quote: Quote) -> Optional[str]:
    ma10 = spec.get("ma10")
    if ma10 is None or ma10 <= 0:
        return None
    if quote.price < ma10:
        return None  # 已破位,不算"站稳"(破位是证伪哨兵的地盘,不是这里判"没触达")
    if quote.pre_close and quote.pre_close > 0:
        gap_pct = (quote.open - quote.pre_close) / quote.pre_close
        if gap_pct > PULLBACK_MAX_GAP_OPEN:
            return None  # 追高开缺口,不算"低吸"时机
    return f"站稳10日线支撑{ma10:.2f}"


def _reached_breakout(spec: dict, quote: Quote) -> Optional[str]:
    platform_high = spec.get("platform_high")
    if platform_high is None or platform_high <= 0:
        return None
    if quote.price <= platform_high:
        return None
    return f"突破前期平台高点{platform_high:.2f}"


def check_entry(
    candidate: Candidate,
    quote: Optional[Quote],
    prev5_avg_vol: float,
    now: datetime,
) -> Optional[EntrySignal]:
    """候选是否触达预设买点且确认条件成立。`quote is None`(拉不到行情)→ None。"""
    if quote is None:
        return None

    elapsed_min = elapsed_trading_minutes(now)
    if elapsed_min < MIN_STRUCTURAL_ELAPSED_MINUTES:
        return None

    spec = candidate.entry_spec or {}
    buypoint = spec.get("buypoint")

    level_desc: Optional[str] = None
    vol_floor: float = ENTRY_PULLBACK_MIN_VOL_RATIO
    if buypoint == "breakout":
        level_desc = _reached_breakout(spec, quote)
        vol_floor = spec.get("breakout_vol_expand") or ENTRY_PULLBACK_MIN_VOL_RATIO
    elif buypoint == "either":
        level_desc = _reached_pullback(spec, quote)
        if level_desc is not None:
            vol_floor = ENTRY_PULLBACK_MIN_VOL_RATIO
        else:
            level_desc = _reached_breakout(spec, quote)
            vol_floor = spec.get("breakout_vol_expand") or ENTRY_PULLBACK_MIN_VOL_RATIO
    else:  # "pullback" | "none" | 其它未知值,统一按 pullback 处理(同 entry_plan_text 的退化文案)
        level_desc = _reached_pullback(spec, quote)
        vol_floor = ENTRY_PULLBACK_MIN_VOL_RATIO

    if level_desc is None:
        return None

    vol_ratio, vol_note = intraday_vol_ratio(quote.volume, prev5_avg_vol, elapsed_min)
    if vol_note in ("early", "no_base") or vol_ratio is None:
        return None  # 量能数据不足以确认,不推送(从严——买点哨兵是"许可开仓",宁可漏推)
    if vol_ratio < vol_floor:
        return None

    vwap, is_above_vwap = vwap_of(quote)
    if is_above_vwap is not True:
        return None

    return EntrySignal(
        ts_code=candidate.ts_code,
        name=candidate.name,
        price=quote.price,
        vol_ratio=vol_ratio,
        vwap=vwap,
        reason=(
            f"{level_desc};量能折算{vol_ratio:.1f}倍、现价{quote.price:.2f}站稳当日VWAP{vwap:.2f}"
            f"——前晚计划的买点确认条件成立。"
        ),
    )


__all__ = [
    "EntrySignal",
    "check_entry",
    "MIN_STRUCTURAL_ELAPSED_MINUTES",
    "ENTRY_PULLBACK_MIN_VOL_RATIO",
    "PULLBACK_MAX_GAP_OPEN",
]
