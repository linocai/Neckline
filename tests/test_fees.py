"""卖出费估算单测(§五 v1.3-①-F,neckline/fees.py 唯一源)。

锁死:①印花税万5(卖出单边)+ 过户费万0.1(双边)+ 佣金(从买入实付反推,含 5 元地板);
②无 buy_fees → 兜底默认佣金率;③买入命中地板 → 反推偏高(诚实标注 min_floor);
④盈亏平衡线附近净浮盈判向对费用敏感;⑤费率可覆盖(app_settings 参数)。
"""

from __future__ import annotations

from neckline.fees import (
    DEFAULT_COMMISSION_RATE,
    MIN_COMMISSION,
    STAMP_DUTY_SELL,
    TRANSFER_FEE,
    FeeRates,
    estimate_net_float,
    estimate_sell_fee,
    infer_commission_rate,
)


def test_constants_are_current_statutory():
    """政策常量口径:印花税万5(2023-08-28 减半后)、过户费万0.1、默认佣金万2.5、地板 5 元。"""
    assert STAMP_DUTY_SELL == 0.0005
    assert TRANSFER_FEE == 0.00001
    assert DEFAULT_COMMISSION_RATE == 0.00025
    assert MIN_COMMISSION == 5.0


def test_stamp_and_transfer_components():
    """大额单(佣金不触地板):印花税 + 过户费 + 佣金各component 对。"""
    # sell_amount=100000,buy_amount=100000,buy_fees=30(反推佣金率≈(30−1)/100000=万2.9)。
    est = estimate_sell_fee(100000.0, buy_fees=30.0, buy_amount=100000.0)
    assert abs(est.stamp_duty - 100000 * 0.0005) < 1e-9          # 50.0
    assert abs(est.transfer_fee - 100000 * 0.00001) < 1e-9        # 1.0
    # 反推佣金率 = (30 − 100000*0.00001)/100000 = (30−1)/100000 = 0.00029
    assert est.commission_source == "inferred_from_buy_fees"
    assert abs(est.commission_rate - 0.00029) < 1e-12
    assert abs(est.commission - 100000 * 0.00029) < 1e-9          # 29.0
    assert abs(est.total - (50.0 + 1.0 + 29.0)) < 1e-9


def test_min_commission_floor():
    """小额单佣金触 5 元地板 → commission=5、source=min_floor。"""
    est = estimate_sell_fee(2000.0, buy_fees=None, buy_amount=2000.0)   # 2000*0.00025=0.5 < 5
    assert est.commission == MIN_COMMISSION
    assert est.commission_source == "min_floor"
    assert abs(est.total - (2000 * 0.0005 + 2000 * 0.00001 + 5.0)) < 1e-9


def test_no_buy_fees_falls_back_default_rate():
    """无 buy_fees → 兜底默认佣金率万2.5(大额不触地板时 source=default_rate)。"""
    rate, src = infer_commission_rate(None, 100000.0)
    assert rate == DEFAULT_COMMISSION_RATE and src == "default_rate"
    est = estimate_sell_fee(100000.0, buy_fees=None, buy_amount=100000.0)
    assert est.commission_source == "default_rate"                # 100000*0.00025=25 > 5,不触地板
    assert abs(est.commission - 25.0) < 1e-9


def test_infer_rate_conservative_when_buy_hit_floor():
    """买入命中 5 元地板 → 反推佣金率偏高(诚实:估算偏保守)。buy 2万×0.00025=5元(地板),
    但 buy_fees 实录含地板 5 + 过户 0.2 = 5.2 → 反推率 (5.2−0.2)/20000=0.00025... 这里造 buy_fees
    偏高的场景验证反推>真实费率。"""
    # 假设买入 2 万,券商真实费率万1,但被 5 元地板抬到 5 元,buy_fees=5+过户0.2=5.2。
    rate, src = infer_commission_rate(5.2, 20000.0)
    # 反推 = (5.2 − 20000*0.00001)/20000 = (5.2−0.2)/20000 = 0.00025(万2.5)> 真实万1 → 偏高保守
    assert abs(rate - 0.00025) < 1e-12 and src == "inferred_from_buy_fees"


def test_net_float_breakeven_sensitivity():
    """盈亏平衡线附近:扣双边费后净浮盈判向翻转(费用口径敏感,只影响临界单)。"""
    # buy 10.0 × 1000 = 10000,buy_fees=5。卖出费 ≈ 印花5 + 过户0.1 + 佣金地板5 = 10.1(@cur≈10)。
    # 总费 ≈ buy_fees 5 + 卖出费 10.1 = 15.1 → 需毛浮盈 >15.1 才净浮盈>0 → cur > 10.0151。
    nf_below = estimate_net_float(10.01, 1000, 10.0, buy_fees=5.0)     # 毛浮盈 10 < 15.1
    nf_above = estimate_net_float(10.03, 1000, 10.0, buy_fees=5.0)     # 毛浮盈 30 > 15.1
    assert nf_below < 0
    assert nf_above > 0


def test_rates_override():
    """app_settings 费率覆盖:改印花税为千1 → total 随之变(政策值可配)。"""
    base = estimate_sell_fee(100000.0, buy_fees=30.0, buy_amount=100000.0)
    override = estimate_sell_fee(
        100000.0, buy_fees=30.0, buy_amount=100000.0,
        rates=FeeRates(stamp_duty_sell=0.001),
    )
    assert abs((override.stamp_duty - base.stamp_duty) - (100000 * (0.001 - 0.0005))) < 1e-9


def test_zero_sell_amount_no_crash():
    est = estimate_sell_fee(0.0)
    assert est.total == 0.0
