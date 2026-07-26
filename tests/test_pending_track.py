"""挂单未成交追踪单测(plan §五 v1.3-④,原 v1.2.1-C 全文)。直接测
`neckline.report.pending_track`(不经完整 `build_report`——管线接线的「①落库
②推进 pending→expired」端到端测试在 `tests/test_pipeline.py::TestPendingTrackWiring`)。

用 `tests.conftest.seed_synthetic_market` 铺多日合成行情(600001.SH 等,收盘价逐日
递增,直到最后一天才小幅回调)+ 自建 `decision_log` 行(`_now` 打桩控制 `created_at`
落在合成市场的某个具体交易日上,不依赖真实墙钟时间——同 `test_decision_log.py::
test_filter_by_date_range` 的既定打桩姿势)。
"""

from __future__ import annotations

from typing import Optional

import pytest

from tests.conftest import seed_synthetic_market

import neckline.decision_log as dl_mod
from neckline.decision_log import (
    STATUS_EXPIRED,
    STATUS_PENDING,
    cancel_decision,
    create_decision,
    get_decision,
    link_decision,
)
from neckline.report.pending_track import (
    DECISION_PENDING_TRACK_DAYS,
    load_track_rows,
    track_pending_decisions,
)
from neckline.sentinel import positions as pos_store

pytestmark = pytest.mark.usefixtures("isolated_env")


def _make_decision(db_path, created_at: str, ts_code: str = "600001.SH", planned_price: Optional[float] = 10.0, monkeypatch=None):
    """在指定(伪造)`created_at` 下建一条 pending 决策。`monkeypatch` 用来临时接管
    `neckline.decision_log._now`(同 `test_decision_log.py` 既定姿势),调用后不复原
    (调用方各自传独立的 monkeypatch fixture,函数结束自动复原,互不串味)。"""
    assert monkeypatch is not None
    monkeypatch.setattr(dl_mod, "_now", lambda: created_at)
    return create_decision(
        ts_code=ts_code, why_buy="题材热", why_entry_price="回调低吸",
        invalidation="跌破10日线", thesis_tags=["THEME"], playbook_tag="SWING_CHASE",
        planned_price=planned_price, planned_qty=1000, db_path=db_path,
    )


class TestOffsetWindow:
    """`d_offset` 语义(距 created_at 之后第几个交易日,不含创建当日)+ 窗口边界。"""

    def test_same_day_as_creation_is_not_due_yet(self, isolated_env, monkeypatch):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", monkeypatch=monkeypatch,
        )
        written = track_pending_decisions(created_day, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert written == 0
        assert load_track_rows(d.id, db_path=isolated_env.db_path) == []
        # 仍 pending,未被误判过期
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_PENDING

    def test_offset_increments_one_per_trading_day(self, isolated_env, monkeypatch):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", monkeypatch=monkeypatch,
        )
        for offset, td in enumerate(dates[6:6 + DECISION_PENDING_TRACK_DAYS], start=1):
            track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
            rows = load_track_rows(d.id, db_path=isolated_env.db_path)
            assert rows[-1]["dOffset"] == offset
            assert len(rows) == offset   # 每天新增恰一行,不重不漏

    def test_expires_exactly_at_day_n(self, isolated_env, monkeypatch):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", monkeypatch=monkeypatch,
        )
        track_days = dates[6:6 + DECISION_PENDING_TRACK_DAYS]
        for td in track_days[:-1]:
            track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
            assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_PENDING
        # 第 N 个交易日:落最后一行 + 同批转 expired
        track_pending_decisions(track_days[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        rows = load_track_rows(d.id, db_path=isolated_env.db_path)
        assert len(rows) == DECISION_PENDING_TRACK_DAYS
        assert rows[-1]["dOffset"] == DECISION_PENDING_TRACK_DAYS
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_EXPIRED

    def test_no_further_rows_after_expiry(self, isolated_env, monkeypatch):
        """过期后不再是 `status='pending'`,下一次报告 run 自然不再追踪它(不需要
        额外的「已过期」判断分支——查询条件本身就把它排除了)。"""
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", monkeypatch=monkeypatch,
        )
        track_days = dates[6:6 + DECISION_PENDING_TRACK_DAYS + 2]   # 多跑两天
        for td in track_days:
            track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        rows = load_track_rows(d.id, db_path=isolated_env.db_path)
        assert len(rows) == DECISION_PENDING_TRACK_DAYS   # 过期后两天没有新增行

    def test_overshoot_self_heals_when_a_run_is_skipped(self, isolated_env, monkeypatch):
        """报告管线曾断跑(如直接跳到第 N+2 天才再次跑)→ 不该永久卡在 pending:
        如实记录【实际】offset(> N)后立即过期,而不是假装发生在第 N 天。"""
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", monkeypatch=monkeypatch,
        )
        overshoot_day = dates[6 + DECISION_PENDING_TRACK_DAYS + 1]   # 直接跳到第 N+2 个交易日
        track_pending_decisions(overshoot_day, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        rows = load_track_rows(d.id, db_path=isolated_env.db_path)
        assert len(rows) == 1
        assert rows[0]["dOffset"] == DECISION_PENDING_TRACK_DAYS + 2   # 如实记录,不假装是 N
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_EXPIRED


class TestRetFromPlan:
    def test_ret_from_plan_relative_to_planned_price(self, isolated_env, monkeypatch):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00",
            planned_price=10.0, monkeypatch=monkeypatch,
        )
        td = dates[6]
        track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        row = load_track_rows(d.id, db_path=isolated_env.db_path)[0]
        expected = (row["close"] - 10.0) / 10.0
        assert row["retFromPlan"] == pytest.approx(expected)
        assert row["close"] > 10.0   # 合成行情逐日递增,验证不是摆设数字

    def test_ret_from_plan_none_without_planned_price(self, isolated_env, monkeypatch):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00",
            planned_price=None, monkeypatch=monkeypatch,
        )
        td = dates[6]
        track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        row = load_track_rows(d.id, db_path=isolated_env.db_path)[0]
        assert row["retFromPlan"] is None
        assert row["close"] is not None   # 收盘价仍如实记录,只是没有基准价可比


class TestFilledCancelledNotTracked:
    def test_filled_decision_not_tracked(self, isolated_env, monkeypatch):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", monkeypatch=monkeypatch,
        )
        pos_id = pos_store.open_position("600001.SH", 10.0, 1000, dates[5], db_path=isolated_env.db_path)
        link_decision(d.id, pos_id, db_path=isolated_env.db_path)
        track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert load_track_rows(d.id, db_path=isolated_env.db_path) == []

    def test_cancelled_decision_not_tracked(self, isolated_env, monkeypatch):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", monkeypatch=monkeypatch,
        )
        cancel_decision(d.id, db_path=isolated_env.db_path)
        track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert load_track_rows(d.id, db_path=isolated_env.db_path) == []


class TestIdempotentAndMisc:
    def test_same_day_rerun_is_idempotent(self, isolated_env, monkeypatch):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", monkeypatch=monkeypatch,
        )
        td = dates[6]
        n1 = track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        n2 = track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert n1 == 1 and n2 == 1   # 同日重跑仍报告"写了一行"(INSERT OR REPLACE 覆盖)
        assert len(load_track_rows(d.id, db_path=isolated_env.db_path)) == 1   # 但不重复堆积

    def test_unknown_code_skips_without_crash(self, isolated_env, monkeypatch):
        """面板查无该票(代码有误/未覆盖)→ 本次跳过,不崩、不臆造一行假数据。"""
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00",
            ts_code="999999.SZ", monkeypatch=monkeypatch,
        )
        written = track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert written == 0
        assert load_track_rows(d.id, db_path=isolated_env.db_path) == []
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_PENDING   # 未被误判过期

    def test_no_pending_decisions_is_a_noop(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        written = track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert written == 0

    def test_multiple_pending_decisions_tracked_independently(self, isolated_env, monkeypatch):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d1 = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00",
            ts_code="600001.SH", monkeypatch=monkeypatch,
        )
        d2 = _make_decision(
            isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00",
            ts_code="600002.SH", monkeypatch=monkeypatch,
        )
        written = track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert written == 2
        assert len(load_track_rows(d1.id, db_path=isolated_env.db_path)) == 1
        assert len(load_track_rows(d2.id, db_path=isolated_env.db_path)) == 1
