"""⛔ **DEPRECATED:被测对象已于 V2.4.0 P0 从生产链断开**(`sentinel/retreat.py`)。

本文件**一条用例都不删、断言一字未改** —— 它记录的是退役前的**行为基准**,
是回滚绳的一部分(§3.14-A / A 表处置:「保留这些文件,另加一组『生产链零调用』
守门,⛔ 不许删测试换绿」)。
⛔ 它证明不了、也不再声称该判定还在生产链上跑;那件事由
`tests/test_v240_p0_retirement_guard.py`(AST 扫 `engine.py` 调用点数 = 0 +
推送链零引用)与 `tests/test_sentinel_engine.py::TestP0RetiredIntradayJudgements`
(整拍级反向断言)负责。

以下为原文说明:
退潮哨兵单测(plan §2.4 第2条 + v1.1-H2 双级制重构)。分两层:
    · `compute_breadth_snapshot` —— 关注池代理样本的涨停/跌停/炸板统计(未变)。
    · `evaluate_retreat` —— 双级制判级纯函数,覆盖四条修法各分支:同时段对比命中/
      缺基线静默、持续性 2 拍、早盘梯度、黄升红两条路径、重启保守。
"""

from __future__ import annotations

from datetime import date, time

import pytest

from neckline.data.board import Board
from neckline.data.realtime import Quote
from neckline.sentinel.retreat import (
    COND_LIMIT_DOWN,
    COND_SECTOR_DIVE,
    COND_ZABAN,
    MarketBreadthSnapshot,
    compute_breadth_snapshot,
    evaluate_retreat,
)
from neckline.sentinel.universe import StockMeta

D = date(2026, 7, 17)  # 阶段0/2 记录的真实大跌日,借用作测试语境(非活体数据)
NORMAL = time(10, 30)  # 常规时段
EARLY = time(9, 45)    # 早盘(< 10:00)加严档


def _quote(code, price, pre_close, high=None) -> Quote:
    return Quote(
        code=code, name=code, price=price, pre_close=pre_close, open=pre_close,
        high=high if high is not None else max(price, pre_close), low=min(price, pre_close),
        volume=10000.0, amount=price * 10000.0 * 100, ts="2026-07-17 10:30:00", source="sina",
    )


def _meta(code, board=Board.MAIN, is_st=False, list_date=date(2015, 1, 1)) -> StockMeta:
    return StockMeta(ts_code=code, name=code, board=board, is_st=is_st, list_date=list_date)


def _snap(zaban=0, limit_up=0, limit_down=0, sample=None) -> MarketBreadthSnapshot:
    denom = zaban + limit_up
    rate = zaban / denom if denom else 0.0
    return MarketBreadthSnapshot(
        trade_date=D, sample_size=sample if sample is not None else max(denom + limit_down, 1),
        limit_up_count=limit_up, limit_down_count=limit_down, zaban_count=zaban, zaban_rate=rate,
    )


def _eval(snap, *, now_time=NORMAL, baseline=None, hot_avg=None, hot_n=0, prev=(), allow_red=True):
    return evaluate_retreat(
        snap, now_time=now_time, same_time_zaban_baseline=baseline,
        hot_sector_avg_chg=hot_avg, hot_sector_sample=hot_n,
        prev_tick_triggered=prev, allow_red=allow_red,
    )


# ————————————————————— compute_breadth_snapshot(未变) —————————————————————

class TestComputeBreadthSnapshot:
    def test_counts_limit_up_down_and_zaban(self):
        quotes = {
            "A": _quote("A", 11.0, 10.0),   # 主板涨停(10%)
            "B": _quote("B", 9.0, 10.0),    # 主板跌停
            "C": _quote("C", 10.5, 10.0, high=11.0),  # 摸过涨停但现价未在涨停 → 炸板
            "D": _quote("D", 10.2, 10.0),   # 平淡
        }
        meta = {c: _meta(c) for c in quotes}
        snap = compute_breadth_snapshot(D, quotes, meta)
        assert snap.limit_up_count == 1
        assert snap.limit_down_count == 1
        assert snap.zaban_count == 1
        assert snap.sample_size == 4
        assert snap.zaban_rate == pytest.approx(1 / 2)

    def test_missing_meta_is_skipped_not_crash(self):
        quotes = {"A": _quote("A", 11.0, 10.0), "UNKNOWN": _quote("UNKNOWN", 11.0, 10.0)}
        meta = {"A": _meta("A")}
        snap = compute_breadth_snapshot(D, quotes, meta)
        assert snap.sample_size == 1

    def test_new_stock_exempt_is_excluded(self):
        quotes = {"NEW": _quote("NEW", 25.0, 10.0)}
        meta = {"NEW": _meta("NEW", board=Board.STAR, list_date=D)}
        snap = compute_breadth_snapshot(D, quotes, meta)
        assert snap.sample_size == 0
        assert snap.limit_up_count == 0

    def test_empty_quotes_returns_zeroed_snapshot(self):
        snap = compute_breadth_snapshot(D, {}, {})
        assert snap.sample_size == 0
        assert snap.zaban_rate == 0.0


# ————————————————————— 修法1:飙升条件改同时段对比 —————————————————————

class TestSameTimeSpike:
    def test_spike_vs_same_time_baseline_triggers(self):
        # rate=0.45(避开 0.50 绝对线),昨日同时段 0.10 → delta 0.35 ≥ 0.20
        d = _eval(_snap(zaban=45, limit_up=55), baseline=0.10)
        assert d.tier == "yellow"  # 单条件首次成立
        assert d.triggered == [COND_ZABAN]
        assert "昨日同时段" in d.reason_text and "飙升" in d.reason_text

    def test_no_baseline_spike_silently_disabled(self):
        # 无昨日同时段基线(部署首日) → 飙升子判据静默失效,绝对线又没到 → 不触发
        d = _eval(_snap(zaban=45, limit_up=55), baseline=None)
        assert d.tier == "none"

    def test_small_spike_below_delta_does_not_trigger(self):
        # 昨日同时段 0.30,现 0.45 → delta 0.15 < 0.20
        d = _eval(_snap(zaban=45, limit_up=55), baseline=0.30)
        assert d.tier == "none"

    def test_absolute_line_wins_over_spike_and_needs_min_sample(self):
        # 绝对线优先:rate 0.60 ≥ 0.50 → 绝对触发(理由是"过高",不是"飙升")
        d = _eval(_snap(zaban=6, limit_up=4), baseline=0.10)
        assert d.triggered == [COND_ZABAN]
        assert "过高" in d.reason_text
        # 样本太小(denom<ZABAN_MIN_SAMPLE)即便 rate=1.0 也不判
        assert _eval(_snap(zaban=2, limit_up=0), baseline=0.0).tier == "none"


# ————————————————————— 修法3:早盘加严梯度 —————————————————————

class TestEarlySessionStricter:
    def test_zaban_abs_gradient(self):
        # rate 0.55:常规 0.50 触发,早盘 0.65 不触发
        snap = _snap(zaban=55, limit_up=45)
        assert _eval(snap, now_time=NORMAL).tier != "none"
        assert _eval(snap, now_time=EARLY).tier == "none"

    def test_limit_down_count_gradient(self):
        # 跌停 6 只 / 样本 100:常规(≥5,占比 6%)触发;早盘(需 ≥8,占比阈 0.20)不触发
        snap = _snap(limit_down=6, sample=100)
        assert _eval(snap, now_time=NORMAL).triggered == [COND_LIMIT_DOWN]
        assert _eval(snap, now_time=EARLY).tier == "none"

    def test_spike_delta_gradient(self):
        # rate 0.45,昨日同时段 0.20 → delta 0.25:常规(≥0.20)触发,早盘(≥0.30)不触发
        snap = _snap(zaban=45, limit_up=55)
        assert _eval(snap, now_time=NORMAL, baseline=0.20).triggered == [COND_ZABAN]
        assert _eval(snap, now_time=EARLY, baseline=0.20).tier == "none"

    def test_sector_dive_gradient(self):
        # 平均跌幅 -3.5%:常规(≤-3%)触发,早盘(≤-4%)不触发
        # ⚠ **V2-⑧-G-E 显式改动(planner 2026-08-03 裁定 `351ce56` 授权)**:本用例
        # 原本用 `hot_n=3`,⑧-G 给主线跳水一路加了最小样本量下限
        # `MIN_MAINLINE_SAMPLE`(=5,同源引用 `industry_strength._MIN_MEMBERS`),
        # n=3 起**不再准入该条件**(n=3 时横截面收益率标准误约 2pp,判 −3% 阈值接近
        # 抛硬币,而误触发的代价是整天禁开新仓)。故改 `hot_n=3` → `hot_n=5`,
        # **两个阈值(−3% / 早盘 −4%)与本用例要证的梯度语义一字未动**。
        # ⛔ 这不是"改断言迁就新代码",是**行为按裁定变了、测试跟着变**(⑧-G-E 原文
        # 明确区分这两件事);n<5 的新行为由下面 `TestSectorDiveNoDataHonesty` 补锁。
        assert _eval(_snap(), hot_avg=-0.035, hot_n=5, now_time=NORMAL).triggered == [COND_SECTOR_DIVE]
        assert _eval(_snap(), hot_avg=-0.035, hot_n=5, now_time=EARLY).tier == "none"


# ————————————————————— 修法4:双级制(黄 / 红两条路径)+ 修法2:持续性 —————————

class TestTwoTierAndPersistence:
    def _zaban_only(self):
        # 只触发炸板率一族(绝对过高),跌停/跳水都不触发
        return _snap(zaban=6, limit_up=4, limit_down=0, sample=10)

    def test_single_condition_first_occurrence_is_yellow(self):
        d = _eval(self._zaban_only(), prev=())
        assert d.tier == "yellow"
        assert d.triggered == [COND_ZABAN]
        assert d.red_via == []

    def test_single_condition_two_consecutive_ticks_is_red(self):
        # 上一拍已触发同族 → 连续 2 拍 → 红
        d = _eval(self._zaban_only(), prev=(COND_ZABAN,))
        assert d.tier == "red"
        assert d.red_via == [f"persist:{COND_ZABAN}"]

    def test_two_distinct_conditions_same_tick_is_red(self):
        # 炸板率 + 跌停 两个不同族同拍成立 → 直接红(无需连续)
        snap = _snap(zaban=6, limit_up=4, limit_down=6, sample=20)
        d = _eval(snap, prev=())
        assert d.tier == "red"
        assert set(d.triggered) == {COND_ZABAN, COND_LIMIT_DOWN}
        assert "multi_condition" in d.red_via

    def test_different_single_condition_next_tick_stays_yellow(self):
        # 上一拍是跌停族,本拍是炸板族(不同族)→ 非持续、非同拍多条 → 仍黄
        d = _eval(self._zaban_only(), prev=(COND_LIMIT_DOWN,))
        assert d.tier == "yellow"

    def test_no_condition_is_none(self):
        assert _eval(_snap(zaban=1, limit_up=9, sample=50)).tier == "none"


class TestRestartConservative:
    def test_first_tick_downgrades_persistence_red_to_yellow(self):
        snap = _snap(zaban=6, limit_up=4, limit_down=0, sample=10)
        d = _eval(snap, prev=(COND_ZABAN,), allow_red=False)
        assert d.tier == "yellow"
        # red_via 仍留痕(审计:本会红,被首拍保守降级)
        assert d.red_via == [f"persist:{COND_ZABAN}"]

    def test_first_tick_downgrades_multi_condition_red_to_yellow(self):
        snap = _snap(zaban=6, limit_up=4, limit_down=6, sample=20)
        d = _eval(snap, prev=(), allow_red=False)
        assert d.tier == "yellow"
        assert "multi_condition" in d.red_via


class TestSectorDiveNoDataHonesty:
    def test_empty_sample_is_no_data_not_triggered(self):
        assert _eval(_snap(), hot_avg=-0.10, hot_n=0).tier == "none"

    def test_none_avg_is_no_data(self):
        assert _eval(_snap(), hot_avg=None, hot_n=5).tier == "none"

    def test_sample_below_min_mainline_sample_is_not_judged(self):
        """**V2-⑧-G-E**:n < `MIN_MAINLINE_SAMPLE`(=5)一律不判该路 —— 小样本的
        估计量本身没意义,而误触发的代价是整天禁开新仓(⛔ 保守方向 = 不触发,与
        项目别处"宁可多提醒"刻意相反,理由见 `sentinel/mainline.py` 模块头第 7 条)。"""
        from neckline.sentinel.mainline import MIN_MAINLINE_SAMPLE

        for n in range(MIN_MAINLINE_SAMPLE):            # 0..4 一律不判,哪怕跌 10%
            assert _eval(_snap(), hot_avg=-0.10, hot_n=n).tier == "none"
        assert _eval(_snap(), hot_avg=-0.10, hot_n=MIN_MAINLINE_SAMPLE).triggered == [COND_SECTOR_DIVE]
