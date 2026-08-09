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

**2026-08-03 新增第四个函数 `check_exit_reference_reached`(用户拍板,plan §五
V2-⑪-B 定向任务书,仅服务于 APNs `take_profit` kind)**:触达来源篮子卡经
`position_plans` 继承的离场参考区间。**与上面「回落止盈」刻意不同源、不合并**
——两者是项目明文区分的「两个不同概念」(`sentinel/positions.py`
`CLOSE_REASON_TARGET_ZONE_REACHED` 注释原话:「达到参考区间...与 TAKE_PROFIT
〔回落止盈,现役章程机械规则〕是两个不同概念,不复用」):回落止盈是回测验证过
的机械纪律,继续独立驱动 console/Bark 通道、`evaluate_holding()`/`HoldingAlert`
签名与既有三条子检查**一字不动**;本函数是 `sentinel/engine.py::run_tick` 里
一条独立的 APNs 专属旁路(数据源查询——从 `position_plans` 取最新版本的
`exit_reference`——在 engine.py 侧,不在本模块,本模块只做纯判定)。
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

# 浮点容差:`buy_price`/`stop_pct` 等实数运算(如 0.08-0.02)偶发落在二进制浮点
# 表示误差的边界上(如算出 0.05999999999999996 而非 0.06),不能让纪律判定
# 因这类噪声漏判——所有阈值比较统一留这个量级的容差。
_EPS = 1e-9


@dataclass
class HoldingAlert:
    position_id: int
    ts_code: str
    alerts: Dict[str, str] = field(default_factory=dict)  # event_key -> 理由文案

    @property
    def triggered(self) -> bool:
        return bool(self.alerts)


def check_stop_approach(
    position: Position, quote: Quote, stop_pct: float, buffer_pct: float = STOP_APPROACH_BUFFER,
    *, advisory: bool = False,
) -> Optional[str]:
    """止损逼近 / 已跌破的预警文案。

    **`advisory`(V2.2-⑤,§2.9-A「−5% 由强制条件单改为止损警戒 + 离场决策」)**:
      · `False`(缺省 = `v1.3.3` 及之前的章程)→ 文案**逐字不变**,仍指向券商条件单。
      · `True`(`v2.2-k8` 起,判据单一源 `brain.stop_is_advisory`)→ 改「止损警戒」口吻,
        点明**离场决策在用户**。⚠ **阈值与判定逻辑一字未动** —— `stop_pct=0.05` 仍是同
        一个唯一源、同一条线、同一个提前量;改的只是它触发的那句话在说什么(§五 ⑤ 工程
        细节 2)。⛔ 别把它读成"止损放松了":系统本来就不代下单,变的是纪律归属。
    """
    if quote.price <= 0 or position.buy_price <= 0:
        return None
    drawdown = (position.buy_price - quote.price) / position.buy_price
    warn_from = max(stop_pct - buffer_pct, 0.0)
    if drawdown < warn_from - _EPS:
        return None
    stop_line = position.buy_price * (1 - stop_pct)
    if quote.price <= stop_line + _EPS * position.buy_price:
        if advisory:
            return (
                f"止损警戒:现价{quote.price:.2f}已跌破止损线{stop_line:.2f}(-{stop_pct:.0%}),"
                f"离场决策在你(系统不代下单/撤单)"
            )
        return (
            f"现价{quote.price:.2f}已跌破止损线{stop_line:.2f}(-{stop_pct:.0%}),"
            f"若券商条件单未成交请立即人工确认(系统不代下单/撤单)"
        )
    if advisory:
        return (
            f"止损警戒:现价{quote.price:.2f}逼近止损线{stop_line:.2f}(-{stop_pct:.0%}),"
            f"当前浮亏{drawdown:.1%},离场决策在你"
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
    if quote.price <= retrace_line + _EPS * peak:
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


def check_exit_reference_reached(
    position: Position, quote: Quote, exit_low: float, exit_high: float,
) -> Optional[str]:
    """触达离场参考区间(2026-08-03 用户拍板;仅服务 APNs `take_profit` kind,见
    模块头说明——**不是**回落止盈,两者刻意不同源、不合并)。

    `exit_low`/`exit_high` 由调用方从 `position_plans` 最新版本的 `plan_json.
    exit_reference` 取(查无该持仓的计划 / 无来源篮子 / 卡未就绪 / 该票离场参考被
    ⑦ 夹逼拒收 → 调用方直接不判、不调用本函数,**不传 0/None 冒充"没有区间"**——
    本函数因此要求两个参数都是有效正数,不在这里做"缺失时怎么办"的决策)。

    **语义**:现价进入区间下沿即算"触达"(`quote.price >= exit_low`,不要求越过
    `exit_high`)——同 `check_take_profit`/`check_stop_approach` 单阈值触发一碰线
    就提醒的既有姿势。**文案只中性陈述"触达",不建议卖出**(离场参考是参考、回落
    止盈才是纪律;同 `basket_card.py` `CARD_SYSTEM_PROMPT`「不得使用止盈线/目标价/
    建议买入」这类措辞的同一条语义红线)。

    ⚠ **文案不是随便写的,它是 §2.8-C-3 记名豁免的前提③**(2026-08-03 ⑪-D 接线时
    补齐):原文要求「纯告知型文案,禁指令词,**必须点明「这是你计划里的参考位、
    不是止盈线,纪律仍是回落止盈」**」。四条前提**缺一即豁免失效** —— 也就是说,
    改这段话之前先回去读那一节;把「纪律仍是回落止盈」这半句删掉,这条 kind 就
    不再被允许推送了。"""
    if quote.price <= 0 or exit_low <= 0 or exit_high <= 0 or exit_low > exit_high:
        return None
    if quote.price + _EPS < exit_low:
        return None
    return (
        f"现价{quote.price:.2f}已触达来源篮子的离场参考区间"
        f"[{exit_low:.2f}, {exit_high:.2f}]——这是你计划里的参考位、不是止盈信号,"
        f"纪律仍是回落止盈;是否离场由您判断"
    )


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
    stop_advisory: bool = False,
) -> HoldingAlert:
    """三条子检查合一,返回本持仓当拍命中的全部告警(可能 0~3 条同时命中)。
    `quote is None`(拉不到行情)→ 止损/止盈两条跳过,板块跳水仍可能有数据
    (peer_returns 来自其它同板块个股的行情,不依赖本票自己的 quote)。

    `stop_advisory`(V2.2-⑤)只透传给 `check_stop_approach` 换文案口吻,**不改任何判定**;
    缺省 False = 与 V2.2 之前逐字节相同。"""
    alerts: Dict[str, str] = {}
    if quote is not None:
        stop_reason = check_stop_approach(position, quote, stop_pct, stop_buffer,
                                          advisory=stop_advisory)
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
    "check_exit_reference_reached",
    "evaluate_holding",
    "STOP_APPROACH_BUFFER",
    "SECTOR_DIVE_RET_THRESHOLD",
]
