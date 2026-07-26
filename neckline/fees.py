"""A 股交易费用估算(plan §五 v1.3-①-F,唯一源)。

**用途边界(务必分清)**:
  · **实盘估算**:D5 收盘净浮盈判向(哨兵/报告的两档时间退出,§五 v1.3-①-C)、呼吸台账
    `edgeToPrice`/先手成本的实时近似——**卖出费在 D5 当天尚未发生,只能按本模块公式估**。
    诚实标注为估算;真实卖出费在清仓时由用户补录 `positions.sell_fees` 回填,**周复盘对账
    一律用真数、不用估数**。
  · **回测侧不走本模块**:回测引擎有自带的双边精确 fee 模型(`backtest/broker.py`),别把
    实盘估算混进回测(§五 v1.3-① 铁律)。

政策常量单一源住此一处——费率有变(如印花税政策调整)只改这里,不在别处抄字面量(§3.8)。

**⚠ 印花税口径需用户确认**:`STAMP_DUTY_SELL` 当前 = 万5(0.0005),是 2023-08-28 起
证券交易印花税**减半后的现行卖出单边税率**(与回测 broker `stamp_duty_rate=0.0005` 同源
口径)。planner 上级建议里出现过「千1(0.001)」——那是 2023-08-28 之前的旧税率,本模块按
现行统计口径取万5;若用户要按千1 或未来政策再变,改此一处常量即可。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# —— 政策/费率常量(单一源;app_settings fee 参数可覆盖,见 estimate_sell_fee 的 overrides)——
STAMP_DUTY_SELL = 0.0005        # 印花税:卖出单边,万5(2023-08-28 减半后现行;⚠待用户确认见模块头)
TRANSFER_FEE = 0.00001         # 过户费:双边,万0.1(沪深已统一,与 broker.transfer_fee_rate 同源)
DEFAULT_COMMISSION_RATE = 0.00025  # 佣金兜底:万2.5(无 buy_fees 可反推时用;与 broker 默认同源)
MIN_COMMISSION = 5.0           # 最低佣金地板:5 元/笔(券商普遍下限)


@dataclass
class FeeRates:
    """费率覆盖(用户在 app_settings 存一份;缺省 = 上述统计常量)。任一字段 None = 用常量。"""
    stamp_duty_sell: Optional[float] = None
    transfer_fee: Optional[float] = None
    default_commission_rate: Optional[float] = None
    min_commission: Optional[float] = None

    def stamp(self) -> float:
        return self.stamp_duty_sell if self.stamp_duty_sell is not None else STAMP_DUTY_SELL

    def transfer(self) -> float:
        return self.transfer_fee if self.transfer_fee is not None else TRANSFER_FEE

    def default_comm(self) -> float:
        return self.default_commission_rate if self.default_commission_rate is not None else DEFAULT_COMMISSION_RATE

    def min_comm(self) -> float:
        return self.min_commission if self.min_commission is not None else MIN_COMMISSION


@dataclass
class SellFeeEstimate:
    """卖出费估算结果(诚实标注:`is_estimate=True`,`commission_source` 说明佣金怎么来的)。"""
    total: float                 # 估算卖出总费(印花税 + 过户费 + 佣金)
    stamp_duty: float
    transfer_fee: float
    commission: float
    commission_rate: float       # 实际用的佣金率(反推得到 or 兜底默认)
    commission_source: str       # 'inferred_from_buy_fees' | 'default_rate' | 'min_floor'
    is_estimate: bool = True     # 恒 True——本模块只出估算;真数走 positions.sell_fees 回填


def infer_commission_rate(
    buy_fees: Optional[float], buy_amount: float, rates: Optional[FeeRates] = None
) -> tuple[float, str]:
    """从买入实付费用反推佣金率:`comm_rate = max(buy_fees − buy_amount×过户费, 0)/buy_amount`。

    ⚠ 若买入命中 5 元最低佣金地板,反推出的佣金率会偏高(实际单笔佣金被地板抬到 5 元 →
    除以 buy_amount 得到的率大于券商真实费率)→ 估算的卖出佣金偏保守(偏高)。这是**诚实
    标注的偏差方向**,不修正(修正需知道券商真实费率,而那正是我们没有的)。

    无 `buy_fees`(用户未补录开仓实付)或 `buy_amount<=0` → 返 (兜底万2.5, 'default_rate')。
    """
    r = rates or FeeRates()
    if not buy_fees or buy_amount <= 0:
        return r.default_comm(), "default_rate"
    inferred = max(buy_fees - buy_amount * r.transfer(), 0.0) / buy_amount
    if inferred <= 0:
        return r.default_comm(), "default_rate"
    return inferred, "inferred_from_buy_fees"


def estimate_sell_fee(
    sell_amount: float,
    buy_fees: Optional[float] = None,
    buy_amount: float = 0.0,
    rates: Optional[FeeRates] = None,
) -> SellFeeEstimate:
    """估算一笔卖出的双边**卖出侧**费用(印花税单边 + 过户费 + 佣金,含最低佣金地板)。

    `sell_amount` = 卖出金额(现价×股数);`buy_fees`/`buy_amount` 供反推佣金率(优先从买入
    实付反推,无则兜底默认率)。`rates` 覆盖(app_settings 存的费率;缺省 = 模块常量)。

    误差影响(写死):只用于「净浮盈 ≈0 盈亏平衡线附近」翻转 time-exit vs profit-exempt
    判向,对明显盈/亏单无影响。真实卖出后回填 `sell_fees` → 周复盘用真数。
    """
    r = rates or FeeRates()
    if sell_amount <= 0:
        return SellFeeEstimate(0.0, 0.0, 0.0, 0.0, r.default_comm(), "default_rate")
    stamp = sell_amount * r.stamp()
    transfer = sell_amount * r.transfer()
    comm_rate, comm_src = infer_commission_rate(buy_fees, buy_amount, rates=r)
    raw_comm = sell_amount * comm_rate
    if raw_comm < r.min_comm():
        commission = r.min_comm()
        comm_src = "min_floor"
    else:
        commission = raw_comm
    return SellFeeEstimate(
        total=stamp + transfer + commission,
        stamp_duty=stamp, transfer_fee=transfer, commission=commission,
        commission_rate=comm_rate, commission_source=comm_src,
    )


def estimate_net_float(
    price: float, qty: int, buy_price: float,
    buy_fees: Optional[float] = None, rates: Optional[FeeRates] = None,
) -> float:
    """D5 收盘净浮盈估算(扣双边费):`price×qty − buy_price×qty − buy_fees(实录) − 估算卖出费`。
    `buy_fees` 缺失(用户未补录)→ 按 0 计入(买入费未知只影响绝对量,不臆造)、卖出费仍按
    默认率估。这是 §五 v1.3-①-C 两档时间退出的净浮盈判据的实盘估算落点。"""
    sell_amount = price * qty
    buy_amount = buy_price * qty
    est = estimate_sell_fee(sell_amount, buy_fees=buy_fees, buy_amount=buy_amount, rates=rates)
    return sell_amount - buy_amount - (buy_fees or 0.0) - est.total


__all__ = [
    "STAMP_DUTY_SELL", "TRANSFER_FEE", "DEFAULT_COMMISSION_RATE", "MIN_COMMISSION",
    "FeeRates", "SellFeeEstimate", "infer_commission_rate", "estimate_sell_fee",
    "estimate_net_float",
]
