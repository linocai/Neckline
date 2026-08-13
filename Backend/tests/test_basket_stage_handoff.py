"""⑤ 段状态留痕表 + 判读单测(§七 P0-39,2026-08-05 生产实打后定向小修)。

锁死:① 存/读往返;② **判读三态**分得开 —— 无行(`None` = 不知道)/ 跑过 /
没跑成,且各原因码**语义不合并**;③ 同日 `INSERT OR REPLACE` 覆写、不追加历史;
④ 读侧对脏 `notes_json` 不崩;⑤ **默认段状态陷阱**:⑤ 自己那道保险丝返回
`reason_stage=no_seeds`(dataclass 默认值),光看段状态会被读成"跑了、今天真
没种子" —— 判读必须先看 `notes` 里的 `aggregate_failed:*`。

⚠ 报告层怎么用这张表在 `test_basket_daily.py::TestZeroBasketHonesty`;
编排层什么时候写在 `test_evening_chain.py`。本文件只测本表自己的存取与判读契约。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from neckline.db import connection, init_schema
from neckline.selection.basket_stage_handoff import (
    load_stage_verdict,
    save_stage_handoff,
    stage_verdict,
)

D = date(2026, 8, 4)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "neckline_test.db"


def _result(*, search="ok", reason="ok", baskets=0, notes=()):
    return SimpleNamespace(search_stage=search, reason_stage=reason,
                           baskets=tuple(range(baskets)), notes=tuple(notes))


class TestRoundtrip:
    def test_save_and_load_preserves_every_field(self, db):
        save_stage_handoff(D, _result(search="partial", reason="ok", baskets=3,
                                      notes=("seeds_truncated:12/40",)), db_path=db)
        v = load_stage_verdict(D, db_path=db)
        assert v is not None
        assert (v.search_stage, v.reason_stage, v.basket_count) == ("partial", "ok", 3)
        assert v.notes == ("seeds_truncated:12/40",)
        assert v.engine_ran is True and v.reason_code is None

    def test_same_day_rerun_overwrites_and_does_not_append_history(self, db):
        save_stage_handoff(D, _result(reason="ok", baskets=2), db_path=db)
        save_stage_handoff(D, _result(reason="no_provider"), db_path=db)
        v = load_stage_verdict(D, db_path=db)
        assert v.reason_stage == "no_provider" and v.engine_ran is False
        with connection(db) as conn:
            n = conn.execute("SELECT COUNT(*) FROM basket_stage_handoff").fetchone()[0]
        assert n == 1

    def test_rows_are_per_trade_date(self, db):
        save_stage_handoff(D, _result(reason="ok"), db_path=db)
        assert load_stage_verdict(date(2026, 8, 3), db_path=db) is None


class TestThreeStates:
    def test_no_row_means_we_do_not_know_not_zero_baskets(self, db):
        """**无行 = ⑤ 本次(迄今)没跑过**,⛔ 不许猜成「跑了、今天没有」。"""
        init_schema(db)
        assert load_stage_verdict(D, db_path=db) is None

    def test_ok_stage_means_the_engine_really_ran(self, db):
        save_stage_handoff(D, _result(reason="ok"), db_path=db)
        assert load_stage_verdict(D, db_path=db).engine_ran is True

    @pytest.mark.parametrize("reason", [
        "no_provider", "budget_exhausted", "parse_failed",
        "call_failed:ReadTimeout", "segment_failed:RuntimeError",
    ])
    def test_absent_stages_are_reported_with_their_own_code(self, db, reason):
        """五种"没跑成"**语义不合并** —— 原因码原样带出,不糊成一个 `failed`。"""
        save_stage_handoff(D, _result(reason=reason), db_path=db)
        v = load_stage_verdict(D, db_path=db)
        assert v.engine_ran is False and v.reason_code == reason


class TestDefaultStageTrap:
    def test_aggregate_failed_note_beats_the_default_no_seeds_stage(self):
        """⑤ 整段异常 → `AggregateResult` 走**默认字段值**(`no_seeds`)。只看段状态
        会把故障读成结论 —— 这正是 P0-39 那类误读的同款陷阱。"""
        v = stage_verdict(search_stage="no_seeds", reason_stage="no_seeds", basket_count=0,
                          notes=["aggregate_failed:KeyError"])
        assert v.engine_ran is False and v.reason_code == "aggregate_failed:KeyError"

    def test_no_active_pack_is_a_config_gap_not_a_market_verdict(self):
        v = stage_verdict(search_stage="no_seeds", reason_stage="no_seeds", basket_count=0,
                          notes=["no_active_pack_or_seed_set"])
        assert v.engine_ran is False and v.reason_code == "no_active_pack"

    def test_zero_seeds_with_an_active_pack_is_a_real_verdict(self):
        """④ 扫描层跑过、当日零种子 = 「今日无热点 → 今日无篮子」(既有合法输出)。"""
        v = stage_verdict(search_stage="no_seeds", reason_stage="no_seeds", basket_count=0,
                          notes=["empty_seed_set"])
        assert v.engine_ran is True and v.reason_code is None

    def test_unknown_stage_is_conservatively_not_ran(self):
        """认不出来的状态**保守判没跑成** —— 「不知道」不许当「知道没有」。"""
        v = stage_verdict(search_stage="ok", reason_stage="brand_new_code", basket_count=0,
                          notes=[])
        assert v.engine_ran is False and v.reason_code == "unknown_stage:brand_new_code"

    def test_search_stage_absent_does_not_veto_a_good_reason_stage(self):
        """检索段缺席时 ⑤ 仍会出篮子(卡上 `evidence_status` 自有披露)——
        ⛔ 不许因为 `search_stage` 不 ok 就把整段判成没跑。"""
        v = stage_verdict(search_stage="no_provider", reason_stage="ok", basket_count=2,
                          notes=[])
        assert v.engine_ran is True


class TestReadSideNeverWorseThanNoTable:
    def test_garbage_notes_json_degrades_to_empty_notes(self, db):
        init_schema(db)
        with connection(db) as conn:
            conn.execute(
                "INSERT INTO basket_stage_handoff (trade_date, search_stage, reason_stage,"
                " basket_count, notes_json, created_at) VALUES (?,?,?,?,?,?)",
                (D.strftime("%Y%m%d"), "ok", "no_provider", 0, "{不是JSON", "now"),
            )
        v = load_stage_verdict(D, db_path=db)
        assert v is not None and v.notes == () and v.engine_ran is False

    def test_non_list_notes_json_degrades_to_empty_notes(self, db):
        init_schema(db)
        with connection(db) as conn:
            conn.execute(
                "INSERT INTO basket_stage_handoff (trade_date, search_stage, reason_stage,"
                " basket_count, notes_json, created_at) VALUES (?,?,?,?,?,?)",
                (D.strftime("%Y%m%d"), "ok", "ok", 0, '{"a":1}', "now"),
            )
        assert load_stage_verdict(D, db_path=db).notes == ()

    def test_garbage_basket_count_degrades_to_zero_without_crashing(self, db):
        init_schema(db)
        with connection(db) as conn:
            conn.execute(
                "INSERT INTO basket_stage_handoff (trade_date, search_stage, reason_stage,"
                " basket_count, notes_json, created_at) VALUES (?,?,?,?,?,?)",
                (D.strftime("%Y%m%d"), "ok", "ok", "abc", "[]", "now"),
            )
        assert load_stage_verdict(D, db_path=db).basket_count == 0
