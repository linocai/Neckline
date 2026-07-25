"""v1.2-A2 熔断纪律单测(plan §五 v1.2-A2 验收①/③/④/⑤,§2.1 第 7 条,🔴)。

覆盖:连续 3 笔止损(尾部连续 / 遇非止损断链归零 / 显式码 vs NULL 价格兜底 / 显式
非止损码不被价格二次猜)、单日净亏 ≤ −4000(净口径盈亏互抵不触发、连续止损链独立
触发)、已锁定重复触发幂等不开第二行、锁定态派生、两条解锁路径各置对 unlocked_via、
阈值 stop_pct 读现役 config 不硬编。
"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.sentinel import circuit
from neckline.sentinel.positions import (
    CLOSE_REASON_MANUAL,
    CLOSE_REASON_STOP_LOSS,
    close_position,
    open_position,
)
from neckline.review.reconcile import WeeklyReview

from .conftest import seed_active_rule_v1


def _open_close(db, *, buy, sell, sell_date, reason=None, qty=100):
    """开一笔再平掉,返回 position_id。buy/sell 是单价,qty 股数。"""
    pid = open_position("600001.SH", buy, qty, date(2026, 7, 1), db_path=db)
    close_position(pid, sell, sell_date, close_reason=reason, db_path=db)
    return pid


# ————————————————————————————————————————————————————————————————
# 1) 连续 3 笔止损
# ————————————————————————————————————————————————————————————————

def test_three_explicit_stop_losses_trigger(isolated_env):
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)   # stop_pct=0.05
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 20), reason=CLOSE_REASON_STOP_LOSS)
    _open_close(db, buy=100, sell=94, sell_date=date(2026, 7, 21), reason=CLOSE_REASON_STOP_LOSS)
    ep = _open_close_eval(db, buy=100, sell=93, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_STOP_LOSS)
    assert ep is not None
    assert ep.trigger_reason == circuit.TRIGGER_CONSECUTIVE_STOPS
    assert ep.basis_trades_count == 3
    assert "已补录成交" in ep.note        # 诚实边界文案
    assert circuit.is_locked(db_path=db)


def test_null_reason_price_fallback_counts_as_stop(isolated_env):
    """close_reason NULL → 价格近似兜底(sell ≤ buy×(1−stop_pct))判止损。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)   # stop_pct=0.05 → 阈值 95
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 20), reason=None)   # 恰在阈值(含 _EPS)
    _open_close(db, buy=100, sell=94, sell_date=date(2026, 7, 21), reason=None)
    ep = _open_close_eval(db, buy=100, sell=93, sell_date=date(2026, 7, 22), reason=None)
    assert ep is not None and ep.trigger_reason == circuit.TRIGGER_CONSECUTIVE_STOPS
    # 兜底笔数如实透出(3 笔均未标注 → approx=3)
    assert ep.basis.get("approx_count") == 3


def test_explicit_non_stop_reason_not_second_guessed(isolated_env):
    """用户显式标注非止损码(MANUAL)——即便卖出价远低于止损线,也**不**用价格二次
    猜成止损(信用户标注)。3 笔 MANUAL 深亏 → 不触发连续止损。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    _open_close(db, buy=100, sell=80, sell_date=date(2026, 7, 20), reason=CLOSE_REASON_MANUAL)
    _open_close(db, buy=100, sell=80, sell_date=date(2026, 7, 21), reason=CLOSE_REASON_MANUAL)
    ep = _open_close_eval(db, buy=100, sell=80, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_MANUAL)
    assert ep is None
    assert not circuit.is_locked(db_path=db)


def test_non_stop_at_tail_breaks_chain(isolated_env):
    """尾部一笔非止损即断链归零:两笔止损在前、最近一笔非止损 → 不触发。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    _open_close(db, buy=100, sell=94, sell_date=date(2026, 7, 20), reason=CLOSE_REASON_STOP_LOSS)
    _open_close(db, buy=100, sell=94, sell_date=date(2026, 7, 21), reason=CLOSE_REASON_STOP_LOSS)
    ep = _open_close_eval(db, buy=100, sell=110, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_MANUAL)
    assert ep is None and not circuit.is_locked(db_path=db)


def test_consecutive_chain_triggers_despite_earlier_big_win(isolated_env):
    """连续止损链独立触发,不被(更早的)大赢单遮蔽——三笔小额止损(各 −2000,单日均
    不到 −4000)仍触发,尽管更早有一笔 +5000 大赢单。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    _open_close(db, buy=100, sell=150, sell_date=date(2026, 7, 17), reason=CLOSE_REASON_MANUAL)  # +5000 早于止损链
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 20), reason=CLOSE_REASON_STOP_LOSS)  # −500
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 21), reason=CLOSE_REASON_STOP_LOSS)
    ep = _open_close_eval(db, buy=100, sell=95, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_STOP_LOSS)
    assert ep is not None and ep.trigger_reason == circuit.TRIGGER_CONSECUTIVE_STOPS


# ————————————————————————————————————————————————————————————————
# 2) 单日净亏 ≥ 4000(净口径)
# ————————————————————————————————————————————————————————————————

def test_single_day_net_loss_triggers(isolated_env):
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    # 同日两笔:各自单笔不越 −4000,合计净亏 −5000(-3000 + -2000)才越阈;MANUAL 避免
    # 走连续止损路径(专测单日净口径)。
    _open_close(db, buy=100, sell=70, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_MANUAL)   # −3000
    ep = _open_close_eval(db, buy=100, sell=80, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_MANUAL)  # −2000
    assert ep is not None
    assert ep.trigger_reason == circuit.TRIGGER_DAILY_LOSS
    assert ep.basis["daily_net_pnl"] == pytest.approx(-5000.0)
    assert ep.basis_trades_count == 2


def test_morning_loss_offset_by_afternoon_win_no_trigger(isolated_env):
    """净口径:上午亏 6000 + 下午赚 3000 = 净亏 3000 → 不触发(赢单可抵消)。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    _open_close(db, buy=100, sell=40, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_MANUAL)   # −6000
    ep = _open_close_eval(db, buy=100, sell=130, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_MANUAL)  # +3000
    assert ep is None
    assert not circuit.is_locked(db_path=db)


def test_daily_loss_exact_boundary_triggers(isolated_env):
    """恰好 −4000(边界,含 _EPS 容差)→ 触发。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    ep = _open_close_eval(db, buy=100, sell=60, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_MANUAL)  # −4000
    assert ep is not None and ep.trigger_reason == circuit.TRIGGER_DAILY_LOSS


# ————————————————————————————————————————————————————————————————
# 3) 幂等 / 锁定态派生
# ————————————————————————————————————————————————————————————————

def test_relock_is_idempotent_no_second_row(isolated_env):
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 20), reason=CLOSE_REASON_STOP_LOSS)
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 21), reason=CLOSE_REASON_STOP_LOSS)
    _open_close_eval(db, buy=100, sell=95, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_STOP_LOSS)
    assert len(circuit.list_episodes(db_path=db)) == 1
    # 再平一笔止损(仍锁定)→ 幂等,不开第二行
    _open_close_eval(db, buy=100, sell=95, sell_date=date(2026, 7, 23), reason=CLOSE_REASON_STOP_LOSS)
    assert len(circuit.list_episodes(db_path=db)) == 1


def test_no_trigger_when_below_threshold(isolated_env):
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    # 只 2 笔止损(不足 3)+ 单日不到 −4000
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 20), reason=CLOSE_REASON_STOP_LOSS)
    ep = _open_close_eval(db, buy=100, sell=95, sell_date=date(2026, 7, 21), reason=CLOSE_REASON_STOP_LOSS)
    assert ep is None and not circuit.is_locked(db_path=db)


def test_get_state_shape(isolated_env):
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    st = circuit.get_state(db_path=db)
    assert st.locked is False and st.episode is None
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 20), reason=CLOSE_REASON_STOP_LOSS)
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 21), reason=CLOSE_REASON_STOP_LOSS)
    _open_close_eval(db, buy=100, sell=95, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_STOP_LOSS)
    st = circuit.get_state(db_path=db)
    assert st.locked is True and st.episode is not None
    assert st.episode.trigger_ref_date == "20260722"


# ————————————————————————————————————————————————————————————————
# 4) stop_pct 读现役 config(不硬编 -5%)
# ————————————————————————————————————————————————————————————————

def test_stop_pct_read_from_active_config(isolated_env):
    """现役 config stop_pct=0.08 时,价格兜底阈值随之平移到 buy×0.92(不硬编 0.05)。
    sell=93(> 95,K1 下非止损)在 0.08 档(阈值 92)也非止损 → 3 笔 sell=91(≤92)才止损。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env, {"stop_pct": 0.08})
    # sell=93 在 0.08 档(阈值 92)不算止损 → 不触发
    _open_close(db, buy=100, sell=93, sell_date=date(2026, 7, 20), reason=None)
    _open_close(db, buy=100, sell=93, sell_date=date(2026, 7, 21), reason=None)
    ep = _open_close_eval(db, buy=100, sell=93, sell_date=date(2026, 7, 22), reason=None)
    assert ep is None
    # sell=91(≤92)才算止损 → 3 笔触发
    _open_close(db, buy=100, sell=91, sell_date=date(2026, 7, 23), reason=None)
    _open_close(db, buy=100, sell=91, sell_date=date(2026, 7, 24), reason=None)
    ep = _open_close_eval(db, buy=100, sell=91, sell_date=date(2026, 7, 27), reason=None)
    assert ep is not None and ep.trigger_reason == circuit.TRIGGER_CONSECUTIVE_STOPS


# ————————————————————————————————————————————————————————————————
# 5) 解锁两路径
# ————————————————————————————————————————————————————————————————

def test_unlock_review_ack(isolated_env):
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    _lock_via_three_stops(db)
    assert circuit.is_locked(db_path=db)
    assert circuit.unlock(via=circuit.UNLOCK_VIA_REVIEW_ACK, db_path=db) is True
    assert not circuit.is_locked(db_path=db)
    ep = circuit.list_episodes(db_path=db)[0]
    assert ep.unlocked_at is not None and ep.unlocked_via == circuit.UNLOCK_VIA_REVIEW_ACK
    # 再解锁(已无锁定)→ 幂等 False
    assert circuit.unlock(db_path=db) is False


def test_auto_unlock_weekly_review_forced(isolated_env):
    """周复盘覆盖触发日且该周走强制复盘口径(forced_review=True)→ 自动解锁。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    _lock_via_three_stops(db)   # trigger_ref_date=20260722(周三)
    review = WeeklyReview(
        week="2026-W30", week_start=date(2026, 7, 20), week_end=date(2026, 7, 26),
        forced_review=True,
    )
    n = circuit.auto_unlock_for_reviews([review], db_path=db)
    assert n == 1 and not circuit.is_locked(db_path=db)
    ep = circuit.list_episodes(db_path=db)[0]
    assert ep.unlocked_via == circuit.UNLOCK_VIA_WEEKLY_REVIEW


def test_auto_unlock_skips_non_forced_week(isolated_env):
    """覆盖触发日但该周**未**走强制复盘(forced_review=False)→ 不解锁。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    _lock_via_three_stops(db)
    review = WeeklyReview(
        week="2026-W30", week_start=date(2026, 7, 20), week_end=date(2026, 7, 26),
        forced_review=False,
    )
    n = circuit.auto_unlock_for_reviews([review], db_path=db)
    assert n == 0 and circuit.is_locked(db_path=db)


def test_auto_unlock_skips_week_not_covering_ref_date(isolated_env):
    """强制复盘周但不覆盖触发日 → 不解锁(触发日在别的周)。"""
    db = isolated_env.db_path
    seed_active_rule_v1(isolated_env)
    _lock_via_three_stops(db)   # 20260722
    review = WeeklyReview(
        week="2026-W29", week_start=date(2026, 7, 13), week_end=date(2026, 7, 19),
        forced_review=True,
    )
    n = circuit.auto_unlock_for_reviews([review], db_path=db)
    assert n == 0 and circuit.is_locked(db_path=db)


# —— 私有辅助:一次触发 evaluate 的开平仓封装 ——————————————————————————————————

def _open_close_eval(db, *, buy, sell, sell_date, reason=None, qty=100):
    """开一笔、平掉、再跑一次熔断评估(模拟清仓端点里的 evaluate_after_close)。"""
    _open_close(db, buy=buy, sell=sell, sell_date=sell_date, reason=reason, qty=qty)
    return circuit.evaluate_after_close(sell_date, db_path=db)


def _lock_via_three_stops(db):
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 20), reason=CLOSE_REASON_STOP_LOSS)
    _open_close(db, buy=100, sell=95, sell_date=date(2026, 7, 21), reason=CLOSE_REASON_STOP_LOSS)
    _open_close_eval(db, buy=100, sell=95, sell_date=date(2026, 7, 22), reason=CLOSE_REASON_STOP_LOSS)
