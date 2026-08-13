"""挂单未成交追踪单测(plan §五 v1.3-④,原 v1.2.1-C 全文)。直接测
`neckline.report.pending_track`(不经完整 `build_report`——管线接线的「①落库
②推进 pending→expired」端到端测试在 `tests/test_pipeline.py::TestPendingTrackWiring`)。

用 `tests.conftest.seed_synthetic_market` 铺多日合成行情(600001.SH 等,收盘价逐日
递增,直到最后一天才小幅回调)+ 自建 `decision_log` 行(`tests.conftest.
insert_decision_log_row` 裸 SQL fixture,`created_at` 显式指定,不依赖真实墙钟时间)。

**v2.0.0(⑩-C)起的行为变化**:`decision_log` 表停写留档,`track_pending_decisions`
不再把到期决策的 `status` 翻成 `expired`(`neckline.decision_log.expire_decision`
已物理删除)——追踪窗口到点后**仍停止新增追踪行**(`_already_completed` 判据,
效果等价),但 `status` 永远停在 fixture 建行时给的值,不会自己变成 `expired`。
"""

from __future__ import annotations

from typing import Optional

import pytest

from tests.conftest import insert_decision_log_row, seed_synthetic_market, set_decision_status

from neckline.decision_log import STATUS_CANCELLED, STATUS_FILLED, STATUS_PENDING, get_decision
from neckline.report.pending_track import (
    DECISION_PENDING_TRACK_DAYS,
    load_track_rows,
    track_pending_decisions,
)
from neckline.sentinel import positions as pos_store

pytestmark = pytest.mark.usefixtures("isolated_env")


def _make_decision(db_path, created_at: str, ts_code: str = "600001.SH", planned_price: Optional[float] = 10.0):
    """在指定(伪造)`created_at` 下建一条 pending 决策(裸 SQL fixture,`decision_log`
    v2.0.0 起停写留档,不再有 `create_decision` 可调)。"""
    return insert_decision_log_row(
        db_path, ts_code=ts_code, why_buy="题材热", why_entry_price="回调低吸",
        invalidation="跌破10日线", thesis_tags=["THEME"], playbook_tag="SWING_CHASE",
        planned_price=planned_price, planned_qty=1000, created_at=created_at,
    )


class TestOffsetWindow:
    """`d_offset` 语义(距 created_at 之后第几个交易日,不含创建当日)+ 窗口边界。"""

    def test_same_day_as_creation_is_not_due_yet(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00")
        written = track_pending_decisions(created_day, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert written == 0
        assert load_track_rows(d.id, db_path=isolated_env.db_path) == []
        # 仍 pending,未被误判过期
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_PENDING

    def test_offset_increments_one_per_trading_day(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00")
        for offset, td in enumerate(dates[6:6 + DECISION_PENDING_TRACK_DAYS], start=1):
            track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
            rows = load_track_rows(d.id, db_path=isolated_env.db_path)
            assert rows[-1]["dOffset"] == offset
            assert len(rows) == offset   # 每天新增恰一行,不重不漏

    def test_stops_tracking_at_day_n_status_stays_pending(self, isolated_env):
        """v2.0.0:窗口到点(第 N 个交易日)仍落最后一行,但 **不再** 把 `status`
        翻成 `expired`(该写入口已随 `decision_log` 停写一起删除)——`status` 如实
        停在 fixture 给的 `pending`,这是"这张表不再变化"的诚实反映,不是回归。"""
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00")
        track_days = dates[6:6 + DECISION_PENDING_TRACK_DAYS]
        for td in track_days[:-1]:
            track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
            assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_PENDING
        # 第 N 个交易日:落最后一行,status 仍是 pending(v2.0.0 前会同批转 expired)
        track_pending_decisions(track_days[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        rows = load_track_rows(d.id, db_path=isolated_env.db_path)
        assert len(rows) == DECISION_PENDING_TRACK_DAYS
        assert rows[-1]["dOffset"] == DECISION_PENDING_TRACK_DAYS
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_PENDING

    def test_no_further_rows_after_window_ends(self, isolated_env):
        """过窗口后不再新增追踪行(`_already_completed` 判据,v2.0.0 前靠
        `status != 'pending'` 天然排除,现在靠直接查 `decision_pending_track` 里
        已有的 `MAX(d_offset)` 排除——观察得到的效果相同)。"""
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00")
        track_days = dates[6:6 + DECISION_PENDING_TRACK_DAYS + 2]   # 多跑两天
        for td in track_days:
            track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        rows = load_track_rows(d.id, db_path=isolated_env.db_path)
        assert len(rows) == DECISION_PENDING_TRACK_DAYS   # 过窗口后两天没有新增行

    def test_overshoot_self_heals_when_a_run_is_skipped(self, isolated_env):
        """报告管线曾断跑(如直接跳到第 N+2 天才再次跑)→ 如实记录【实际】offset
        (> N)后停止追踪,而不是假装发生在第 N 天。"""
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00")
        overshoot_day = dates[6 + DECISION_PENDING_TRACK_DAYS + 1]   # 直接跳到第 N+2 个交易日
        track_pending_decisions(overshoot_day, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        rows = load_track_rows(d.id, db_path=isolated_env.db_path)
        assert len(rows) == 1
        assert rows[0]["dOffset"] == DECISION_PENDING_TRACK_DAYS + 2   # 如实记录,不假装是 N
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_PENDING


class TestRetFromPlan:
    def test_ret_from_plan_relative_to_planned_price(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", planned_price=10.0)
        td = dates[6]
        track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        row = load_track_rows(d.id, db_path=isolated_env.db_path)[0]
        expected = (row["close"] - 10.0) / 10.0
        assert row["retFromPlan"] == pytest.approx(expected)
        assert row["close"] > 10.0   # 合成行情逐日递增,验证不是摆设数字

    def test_ret_from_plan_none_without_planned_price(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", planned_price=None)
        td = dates[6]
        track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        row = load_track_rows(d.id, db_path=isolated_env.db_path)[0]
        assert row["retFromPlan"] is None
        assert row["close"] is not None   # 收盘价仍如实记录,只是没有基准价可比


class TestFilledCancelledNotTracked:
    def test_filled_decision_not_tracked(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00")
        pos_id = pos_store.open_position("600001.SH", 10.0, 1000, dates[5], db_path=isolated_env.db_path)
        set_decision_status(isolated_env.db_path, d.id, STATUS_FILLED, position_id=pos_id)
        track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert load_track_rows(d.id, db_path=isolated_env.db_path) == []

    def test_cancelled_decision_not_tracked(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00")
        set_decision_status(isolated_env.db_path, d.id, STATUS_CANCELLED)
        track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert load_track_rows(d.id, db_path=isolated_env.db_path) == []


class TestIdempotentAndMisc:
    def test_same_day_rerun_is_idempotent(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00")
        td = dates[6]
        n1 = track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        n2 = track_pending_decisions(td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert n1 == 1 and n2 == 1   # 同日重跑仍报告"写了一行"(INSERT OR REPLACE 覆盖)
        assert len(load_track_rows(d.id, db_path=isolated_env.db_path)) == 1   # 但不重复堆积

    def test_unknown_code_skips_without_crash(self, isolated_env):
        """面板查无该票(代码有误/未覆盖)→ 本次跳过,不崩、不臆造一行假数据。"""
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", ts_code="999999.SZ")
        written = track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert written == 0
        assert load_track_rows(d.id, db_path=isolated_env.db_path) == []
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_PENDING   # 未被误判过期

    def test_no_pending_decisions_is_a_noop(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        written = track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert written == 0

    def test_multiple_pending_decisions_tracked_independently(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        created_day = dates[5]
        d1 = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", ts_code="600001.SH")
        d2 = _make_decision(isolated_env.db_path, f"{created_day.isoformat()}T09:00:00+00:00", ts_code="600002.SH")
        written = track_pending_decisions(dates[6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert written == 2
        assert len(load_track_rows(d1.id, db_path=isolated_env.db_path)) == 1
        assert len(load_track_rows(d2.id, db_path=isolated_env.db_path)) == 1
