"""持仓哨兵(plan §2.4 第3条)。「持仓票放量跳水逼近止损线 / 触达回落止盈区 /
所属板块跳水预警」——三条子检查相互独立(同一持仓可以同时命中多条,各自一个
独立的防重 event_key,见 `neckline.sentinel.dedup` 模块头注释),互不抑制。

**系统永不自动下单/撤单/改止损**(§3.8 铁律)——本模块只做提前预警,真正的
-5% 止损由用户在券商侧的条件单执行;哪怕现价已经跌破止损线,本模块也只是
继续提醒"若条件单未成交请人工确认",不做任何交易动作。

三条子检查:
    · 止损逼近:回撤幅度(相对买入价)达到 `stop_pct - STOP_APPROACH_BUFFER`
      即预警(留出提前量,不是等真正跌破才吭声)。`stop_pct` 由调用方从策略
      大脑现役版本读入(§3.8「规则常量单一源」——不在本模块硬编 -5%)。
    · 回落止盈:仅当该持仓**确实浮盈过**(峰值 > 买入价,含"今日现价"这个
      candidate 峰值)才有意义——从未浮盈的下跌单纯是止损问题,不重复算作
      "回落"(避免同一价位既报"逼近止损"又报"回落止盈"这种令人费解的双重
      告警)。`take_profit_retrace` 同样来自大脑现役版本,`None`(未设回落止盈)
      时本条直接跳过。
    · 板块跳水预警:该持仓所属概念板块内、当前关注池中恰好同板块的其它个股
      平均盘中跌幅(`peer_returns`,调用方从 `universe`/`sectors` 组装)——
      **诚实局限**:免费源无法拿到 THS 概念板块指数的实时报价,只能用关注池内
      恰好同板块的个股均值做代理,样本可能很小甚至为空;为空时本条不判断
      (不是"板块没有跳水",是"没有可比样本"),调用方/推送文案应如实体现这一
      局限,不能让用户误以为"没预警=板块健康"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from neckline.sentinel.positions import Position
from neckline.sentinel.quotes import Quote

# 止损预警提前量(百分点)——回撤达到 stop_pct-此值 即预警,不等真正跌破才吭声
# (**未回测,启发式**,与 report.sentiment 的既有诚实标注风格一致)。
STOP_APPROACH_BUFFER = 0.02

# 板块跳水预警阈值:同板块可比个股平均跌幅 ≤ 此值 → 预警(**未回测,启发式**)。
SECTOR_DIVE_RET_THRESHOLD = -0.03


@dataclass
class HoldingAlert:
    position_id: int
    ts_code: str
    alerts: Dict[str, str] = field(default_factory=dict)  # event_key -> 理由文案

    @property
    def triggered(self) -> bool:
        return bool(self.alerts)


def check_stop_approach(
    position: Position, quote: Quote, stop_pct: float, buffer_pct: float = STOP_APPROACH_BUFFER
) -> Optional[str]:
    if quote.price <= 0 or position.buy_price <= 0:
        return None
    drawdown = (position.buy_price - quote.price) / position.buy_price
    warn_from = max(stop_pct - buffer_pct, 0.0)
    if drawdown < warn_from:
        return None
    stop_line = position.buy_price * (1 - stop_pct)
    if quote.price <= stop_line:
        return (
            f"现价{quote.price:.2f}已跌破止损线{stop_line:.2f}(-{stop_pct:.0%}),"
            f"若券商条件单未成交请立即人工确认(系统不代下单/撤单)"
        )
    return f"现价{quote.price:.2f}逼近止损线{stop_line:.2f}(-{stop_pct:.0%}),当前浮亏{drawdown:.1%}"


def check_take_profit(
    position: Position,
    quote: Quote,
    historical_peak_close: float,
    take_profit_retrace: Optional[float],
) -> Optional[str]:
    if take_profit_retrace is None or take_profit_retrace <= 0:
        return None
    if quote.price <= 0:
        return None
    peak = max(historical_peak_close or 0.0, quote.price)
    if peak <= position.buy_price:
        return None  # 从未浮盈过,没有"盈"可回落——止损哨兵已覆盖这种下跌
    retrace_line = peak * (1 - take_profit_retrace)
    if quote.price <= retrace_line:
        retrace_pct = (peak - quote.price) / peak
        return (
            f"现价{quote.price:.2f}较持仓峰值{peak:.2f}回落{retrace_pct:.1%},"
            f"已进入回落止盈区间(阈值{take_profit_retrace:.0%})"
        )
    return None


def check_sector_dive(
    position: Position, peer_returns: List[float], threshold: float = SECTOR_DIVE_RET_THRESHOLD
) -> Optional[str]:
    if not peer_returns:
        return None
    avg_ret = sum(peer_returns) / len(peer_returns)
    if avg_ret <= threshold:
        return f"所属板块内可比个股(关注池样本{len(peer_returns)}只)平均跌幅{avg_ret:.1%},疑似板块跳水"
    return None


def evaluate_holding(
    position: Position,
    quote: Optional[Quote],
    *,
    stop_pct: float,
    take_profit_retrace: Optional[float],
    historical_peak_close: float = 0.0,
    peer_returns: Optional[List[float]] = None,
    stop_buffer: float = STOP_APPROACH_BUFFER,
    sector_dive_threshold: float = SECTOR_DIVE_RET_THRESHOLD,
) -> HoldingAlert:
    """三条子检查合一,返回本持仓当拍命中的全部告警(可能 0~3 条同时命中)。
    `quote is None`(拉不到行情)→ 止损/止盈两条跳过,板块跳水仍可能有数据
    (peer_returns 来自其它同板块个股的行情,不依赖本票自己的 quote)。"""
    alerts: Dict[str, str] = {}
    if quote is not None:
        stop_reason = check_stop_approach(position, quote, stop_pct, stop_buffer)
        if stop_reason:
            alerts["stop_approach"] = stop_reason
        tp_reason = check_take_profit(position, quote, historical_peak_close, take_profit_retrace)
        if tp_reason:
            alerts["take_profit"] = tp_reason
    dive_reason = check_sector_dive(position, peer_returns or [], sector_dive_threshold)
    if dive_reason:
        alerts["sector_dive"] = dive_reason
    return HoldingAlert(position_id=position.id, ts_code=position.ts_code, alerts=alerts)


__all__ = [
    "HoldingAlert",
    "check_stop_approach",
    "check_take_profit",
    "check_sector_dive",
    "evaluate_holding",
    "STOP_APPROACH_BUFFER",
    "SECTOR_DIVE_RET_THRESHOLD",
]
