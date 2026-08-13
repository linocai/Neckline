"""⑥ 溢出篮跨进程交接表单测(plan §五 V2-⑯-D 补记,2026-08-04 定向小修)。

锁死:① 存/读往返(字段逐位对拍);② **三态**分得开——无行(`None`)/ 有行空数组
(`[]`)/ 有行非空数组;③ `INSERT OR REPLACE` 同日覆写、不追加历史;④ 读侧对脏
`dropped_json`(非法 JSON / 非数组 / 数组内非法项)不崩,降级为「未取得」或跳过
单条,同 `report/news_alerts_store.py` 一类"读侧永远不比没有这张表更糟"的既定纪律。

⚠ 本文件不重测 ⑥ 的定档逻辑本身(那在 `test_selection_tier.py`),只测本表自己的
存取契约;跨进程编排层面的"seg2 写、seg3 读"接线测试在 `test_evening_chain.py`。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from neckline.db import connection, init_schema
from neckline.selection.basket_dropped_handoff import load_dropped_handoff, save_dropped_handoff
from neckline.selection.tier import DroppedBasket

D = date(2026, 7, 24)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "neckline_test.db"


class TestRoundtrip:
    def test_save_and_load_preserves_all_three_fields(self, db):
        save_dropped_handoff(D, [
            DroppedBasket(basket_key="deadbeef01", reason="capacity_overflow", mech_score=0.71),
            DroppedBasket(basket_key="deadbeef02", reason="below_quality_line", mech_score=0.31),
        ], db_path=db)
        out = load_dropped_handoff(D, db_path=db)
        assert out is not None
        assert [(d.basket_key, d.reason, d.mech_score) for d in out] == [
            ("deadbeef01", "capacity_overflow", 0.71),
            ("deadbeef02", "below_quality_line", 0.31),
        ]

    def test_overwrite_same_day_replaces_not_appends(self, db):
        """同日重跑 ⑤⑥ = 只认最近一次的结论,不是审计账本。"""
        save_dropped_handoff(D, [DroppedBasket("a", "capacity_overflow", 0.9)], db_path=db)
        save_dropped_handoff(D, [DroppedBasket("b", "below_quality_line", 0.2)], db_path=db)
        out = load_dropped_handoff(D, db_path=db)
        assert [d.basket_key for d in out] == ["b"]

    def test_different_dates_are_independent_rows(self, db):
        save_dropped_handoff(D, [DroppedBasket("a", "capacity_overflow", 0.9)], db_path=db)
        save_dropped_handoff(date(2026, 7, 25), [], db_path=db)
        assert [d.basket_key for d in load_dropped_handoff(D, db_path=db)] == ["a"]
        assert load_dropped_handoff(date(2026, 7, 25), db_path=db) == []


class TestThreeStates:
    """与 `EveningChainResult.dropped_baskets` 同一套纪律:`None`/`[]`/`[...]`
    三态不许合并(见 `report/evening.py` 模块头)。"""

    def test_no_row_means_none_not_run(self, db):
        init_schema(db)
        assert load_dropped_handoff(D, db_path=db) is None

    def test_row_with_empty_array_means_ran_with_zero_overflow(self, db):
        save_dropped_handoff(D, [], db_path=db)
        out = load_dropped_handoff(D, db_path=db)
        assert out == []
        assert out is not None

    def test_row_with_items_means_ran_with_overflow(self, db):
        save_dropped_handoff(D, [DroppedBasket("a", "capacity_overflow", 0.5)], db_path=db)
        out = load_dropped_handoff(D, db_path=db)
        assert len(out) == 1


class TestDirtyDataDoesNotCrashTheReadSide:
    def test_malformed_json_degrades_to_none(self, db, caplog):
        init_schema(db)
        with connection(db) as conn:
            conn.execute(
                "INSERT INTO basket_dropped_handoff (trade_date, dropped_json, created_at) "
                "VALUES (?,?,?)",
                (D.strftime("%Y%m%d"), "{not valid json", "2026-07-24T00:00:00+00:00"),
            )
        assert load_dropped_handoff(D, db_path=db) is None

    def test_non_array_json_degrades_to_none(self, db):
        init_schema(db)
        with connection(db) as conn:
            conn.execute(
                "INSERT INTO basket_dropped_handoff (trade_date, dropped_json, created_at) "
                "VALUES (?,?,?)",
                (D.strftime("%Y%m%d"), '{"not": "an array"}', "2026-07-24T00:00:00+00:00"),
            )
        assert load_dropped_handoff(D, db_path=db) is None

    def test_one_bad_item_is_skipped_not_fatal(self, db):
        init_schema(db)
        with connection(db) as conn:
            conn.execute(
                "INSERT INTO basket_dropped_handoff (trade_date, dropped_json, created_at) "
                "VALUES (?,?,?)",
                (D.strftime("%Y%m%d"),
                 '[{"basket_key": "a", "reason": "capacity_overflow", "mech_score": 0.5}, '
                 '"not_a_dict", '
                 '{"basket_key": "b", "reason": "below_quality_line"}]',
                 "2026-07-24T00:00:00+00:00"),
            )
        out = load_dropped_handoff(D, db_path=db)
        # 第二项不是 dict → 跳过;第三项缺 mech_score → KeyError → 跳过;只剩第一项。
        assert [d.basket_key for d in out] == ["a"]
