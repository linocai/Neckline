"""报告三态单测(V2.5.0 S5,PROJECT_PLAN §5.10 / §6 S5 验收第 5、6 条,裁定 5)。

🔴 **本文件锁的是一条产品承诺**:
「今天没有」= 跑通了、结果为空、**可以被信任**;
「今天没跑成」= 系统没工作。
把「参数未配置」渲染成「今天没有」,等于让一句谎话每天准时到达手机 ——
从那天起,空清单再也不可信。§6 S5 验收第 5 条逐字要求把这条锁死。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from neckline.k9 import params as P
from neckline.report.state import ReportState, headline, resolve_state
from tests.test_k9_params import _DELETE, make_raw, write

D0 = date(2024, 3, 4)
D1 = date(2024, 3, 5)


# ══════════════════════════════════════════════════════════════════════════
# ① 三态判定:empty 与 not_run ⛔ 不可互换
# ══════════════════════════════════════════════════════════════════════════

class TestResolveState:
    def test_frozen_pack_plus_valid_params_plus_a_list_is_has_list(self):
        assert resolve_state(pack_frozen=True, params_ok=True, listing_count=12) \
            is ReportState.HAS_LIST

    def test_frozen_pack_plus_valid_params_plus_zero_is_empty(self):
        """「今天没有」= 跑通了、结果为空 —— **这个空是可以被信任的**。"""
        assert resolve_state(pack_frozen=True, params_ok=True, listing_count=0) \
            is ReportState.EMPTY

    def test_missing_params_is_not_run_not_empty(self):
        """🔴 §6 S5 验收第 5 条 / 裁定 5:**参数缺失 → `not_run`,⛔ 不是 `empty`**。"""
        state = resolve_state(pack_frozen=True, params_ok=False, listing_count=0)
        assert state is ReportState.NOT_RUN
        assert state is not ReportState.EMPTY

    def test_unfrozen_pack_is_not_run(self):
        """数据未到齐 → 事实包不冻结 → 「今天没跑成」(架构 §3.5)。"""
        assert resolve_state(pack_frozen=False, params_ok=True, listing_count=5) \
            is ReportState.NOT_RUN

    def test_a_null_listing_count_is_not_run_not_empty(self):
        """链路异常、清单根本没算出来 —— ⛔ 不许当成「今天没有」。"""
        assert resolve_state(pack_frozen=True, params_ok=True, listing_count=None) \
            is ReportState.NOT_RUN

    def test_all_three_arguments_are_required_keywords(self):
        import inspect
        sig = inspect.signature(resolve_state)
        for name, p in sig.parameters.items():
            assert p.kind is inspect.Parameter.KEYWORD_ONLY, name
            assert p.default is inspect.Parameter.empty, f"{name} 有默认值 —— ⛔ 不许猜"


# ══════════════════════════════════════════════════════════════════════════
# ② 首行:全映射渲染,⛔ 无 fallback 分支
# ══════════════════════════════════════════════════════════════════════════

class TestHeadline:
    def test_the_enum_has_exactly_three_members(self):
        assert [s.value for s in ReportState] == ["has_list", "empty", "not_run"]

    def test_every_state_has_a_headline(self):
        for s in ReportState:
            assert headline(s, listing_count=1, gaps=["x"]).strip()

    def test_has_list_headline_shows_the_tier_split(self):
        """§5.10:`今天有这些 · N 只(严格 a / 放宽 b)` —— K9 §五-7 的成色标注。"""
        line = headline(ReportState.HAS_LIST, listing_count=15,
                        strict_count=15, relaxed_count=0)
        assert line == "今天有这些 · 15 只(严格 15 / 放宽 0)"

    def test_empty_headline_is_short_and_trustworthy(self):
        assert headline(ReportState.EMPTY) == "今天没有"

    def test_not_run_headline_lists_the_gaps_one_by_one(self):
        """架构 §3.5:「今天没跑成」必须**说明缺口** —— 不说清缺什么等于每天推一句「坏了」。"""
        line = headline(ReportState.NOT_RUN,
                        gaps=["参数未配置", "缺键 ranking.relaySource"])
        assert line.startswith("今天没跑成 · ")
        assert "参数未配置" in line and "ranking.relaySource" in line

    def test_not_run_without_gaps_still_says_so_instead_of_going_blank(self):
        assert "缺口未知" in headline(ReportState.NOT_RUN)

    def test_the_first_line_alone_distinguishes_all_three(self):
        lines = {
            headline(ReportState.HAS_LIST, listing_count=3),
            headline(ReportState.EMPTY),
            headline(ReportState.NOT_RUN, gaps=["参数未配置"]),
        }
        assert len(lines) == 3

    def test_the_mapping_is_total(self):
        """🔴 全映射:漏写一个状态的首行 = **import 就炸**,⛔ 不留到运行时。"""
        from neckline.report import state as st
        assert set(st._HEADLINE) == set(ReportState)


# ══════════════════════════════════════════════════════════════════════════
# ③ 端到端:参数未配置 → not_run + 缺口逐条 + 保留上一份冻结结果
# ══════════════════════════════════════════════════════════════════════════

class TestParamsGapEndToEnd:
    def test_a_params_gap_becomes_a_not_run_headline_with_the_exact_key(self, tmp_path):
        db = tmp_path / "n.db"
        from neckline.db import init_schema
        init_schema(db)
        raw = make_raw(**{"ranking.relayScoring": _DELETE})
        try:
            P.load(write(tmp_path, raw), db_path=db)
            pytest.fail("缺键的参数包居然加载成功了")
        except P.ParamsUnavailable as e:
            state = resolve_state(pack_frozen=True, params_ok=False, listing_count=None)
            line = headline(state, gaps=e.gaps())
        assert state is ReportState.NOT_RUN
        assert "今天没跑成" in line
        assert "ranking.relayScoring" in line

    def test_a_not_run_day_leaves_the_previous_frozen_pack_untouched(self, isolated_env):
        """§5.4.3 第 5 条:参数未配置 → 报告说明缺口,**保留上一份冻结结果**。

        在事实层这条承诺是**结构性**的:`fact_packs` 是 `INSERT` only,
        今天没跑成动不了昨天那一行(⛔ 不是靠谁记得别覆盖)。"""
        env = isolated_env
        from neckline.facts import store as fact_store

        _freeze_one_day(env, D0)
        before = fact_store.load_pack(D0, parquet_dir=env.parquet_dir, db_path=env.db_path)

        # 第二天参数未配置 → not_run。这一天什么都不冻结。
        state = resolve_state(pack_frozen=True, params_ok=False, listing_count=None)
        assert state is ReportState.NOT_RUN

        after = fact_store.load_pack(D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert after.content_fingerprint == before.content_fingerprint
        assert after.pack_id == before.pack_id
        latest = fact_store.latest_pack(
            on_or_before=D1, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert latest is not None and latest.trade_date == D0

    def test_the_report_still_carries_facts_on_a_params_gap_day(self, isolated_env):
        """§5.10 ⚠:**参数未配置的日子照样发报告** —— 清单段标「今天没跑成 ·
        参数未配置」,而方向背景、市场事实、覆盖率成绩线**照常呈现**。
        `not_run` 管的是**清单段**,不是整份报告。"""
        env = isolated_env
        from neckline.facts import store as fact_store
        from neckline.scorecard import coverage as cov

        _freeze_one_day(env, D0)
        pack = fact_store.load_pack(D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        # 市场事实在(来自冻结包)
        assert pack.market["limitMap"]["limitUpCount"] >= 0
        # 覆盖率照跑(它不读参数包)
        day = cov.refresh_day(trade_date=D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert day is not None and day.limit_up_count >= 0
        # 清单段是 not_run
        assert resolve_state(pack_frozen=True, params_ok=False, listing_count=None) \
            is ReportState.NOT_RUN


def _freeze_one_day(env, day: date) -> None:
    """铺一天最小可用的上游数据并冻一份事实包。"""
    from neckline.data.market_data import write_table_day
    from neckline.facts import pack as fact_pack
    from neckline.facts import store as fact_store
    from tests.conftest import (
        insert_namechange, insert_stock_basic, insert_sw_members, insert_trade_cal,
        write_daily_fixture,
    )

    codes = ["600001.SH", "600002.SH", "600003.SH"]
    insert_trade_cal(env, [D0, D1])
    insert_stock_basic(env, [
        {"ts_code": c, "name": f"示例{c[:6]}", "market": "主板", "list_date": date(2020, 1, 2)}
        for c in codes])
    insert_namechange(env, [
        {"ts_code": c, "name": f"示例{c[:6]}", "start_date": date(2020, 1, 2)} for c in codes])
    insert_sw_members(env, [
        {"ts_code": c, "l2_code": "801080.SI", "l2_name": "半导体"} for c in codes])
    write_daily_fixture(env, "daily", day, [
        {"ts_code": c, "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
         "pre_close": 10.0, "change": 0.2, "pct_chg": 2.0, "vol": 1e5, "amount": 1e6}
        for c in codes])
    write_daily_fixture(env, "daily_basic", day, [
        {"ts_code": c, "turnover_rate": 5.0, "turnover_rate_f": 6.0, "volume_ratio": 1.0,
         "circ_mv": 1e6, "total_mv": 2e6, "free_share": 1e5} for c in codes])
    write_daily_fixture(env, "adj_factor", day, [
        {"ts_code": c, "adj_factor": 1.0} for c in codes])
    write_daily_fixture(env, "moneyflow_dc", day, [
        {"ts_code": c, "net_amount": 1.0, "net_amount_rate": 0.1,
         "buy_elg_amount": 1.0, "buy_lg_amount": 1.0} for c in codes])
    write_daily_fixture(env, "limit_derived", day, [])
    write_table_day("suspend_d", day, pl.DataFrame(schema={
        "ts_code": pl.String, "trade_date": pl.Date, "suspend_type": pl.String}),
        parquet_dir=env.parquet_dir)

    built = fact_pack.build(day, parquet_dir=env.parquet_dir, db_path=env.db_path)
    assert isinstance(built, fact_pack.CompletePack), getattr(built, "missing", None)
    fact_store.freeze_pack(built, parquet_dir=env.parquet_dir, db_path=env.db_path)
