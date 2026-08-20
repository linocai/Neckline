"""成员卫生线闸 `neckline/selection/member_hygiene.py` 单测(plan §五 V2-⑤-b 验收)。

编排层接线(过滤发生在截断之前、当日只装配一次、降级 notes 是否透出)见
`tests/test_selection_aggregate.py::TestMemberHygieneWiring`;本文件只测本模块
自身的判据行为——两级保险丝各自路径、K4 两档语义、与 ③ 原语 `run()` 的交叉断言。

覆盖(与 ⑤-b 验收逐条对应):
    ① ST / 次新 / `amount_ma20` 不达标三只票都不出现在 `kept` 里。
    ② `hard_cut` 命中被剔、`avoid_flag` 命中仍在 `kept` 里且带标(两路分得开)。
    ③ 面板缺失 → 趋势线放行 + `hygiene_unavailable` 如实标,而 ST 票仍被拦
       (同一次调用内两条保险丝独立生效)。
    ④ **交叉断言**:同一票同一天,本模块的卫生线判定与 ③ 原语 `stock_hygiene.
       run()` 的结果逐位相同。
    ⑤ 被剔票留痕(`rejected`),原语标签精确。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Dict, List, Sequence

import polars as pl
import pytest

from neckline.selection import member_hygiene as mh
from neckline.selection.pack import Pack
from neckline.selection.primitives import PRIMITIVES
from tests.conftest import insert_stock_basic, insert_trade_cal, write_daily_fixture

D0 = date(2024, 4, 8)

_HYGIENE_PARAMS: Dict[str, Any] = {
    "close_min": 2.0, "amount_ma20_min": 20000.0, "require_ma20": True,
    "allowed_boards": ["MAIN", "GEM", "STAR"], "exclude_st": True,
}
_NON_NEW_PARAMS: Dict[str, Any] = {"min_days": 120}
_K4_PARAMS: Dict[str, Any] = {"hard_cut_action": "exclude", "avoid_flag_action": "tag"}


def _pack(**overrides: Dict[str, Any]) -> Pack:
    seeds = {
        "stock_hygiene": dict(_HYGIENE_PARAMS),
        "non_new_stock": dict(_NON_NEW_PARAMS),
        "k4_advisory_gate": dict(_K4_PARAMS),
    }
    seeds.update(overrides)
    return Pack(
        pack_version="test-pack", name="⑤-b 测试包", engine_api_version=1,
        manifest={}, config={"seeds": seeds, "tier": {"weights": {"x": 1.0}, "dims": ["x"]}},
        evidence_ref=[], is_active=True, created_at="2024-04-08T00:00:00+00:00", activated_at=None,
    )


def _run(env, codes: Sequence[str], *, industry_of=None, close_of=None, pack=None) -> mh.MemberHygieneResult:
    codes = list(codes)
    return mh.apply_member_hygiene(
        codes, D0, pack or _pack(),
        industry_of=industry_of or {}, close_of=close_of if close_of is not None else {c: 10.0 for c in codes},
        db_path=env.db_path, parquet_dir=env.parquet_dir,
    )


def _fake_liquidity_panel(rows: List[Dict[str, Any]]) -> pl.DataFrame:
    """`_load_liquidity_rows` 只 `select(["ts_code","ma20","amount_ma20"])`,伪造
    面板只需要这三列,免铺 20+ 交易日真实历史(`build_research_panel` 已在
    `data/panel.py` 自己的测试里覆盖,本文件不重复验证它的计算过程)。"""
    return pl.DataFrame(rows, schema={"ts_code": pl.String, "ma20": pl.Float64, "amount_ma20": pl.Float64})


# ══════════════════════════════════════════════════════════════════════════
# 两级保险丝 · 便宜的硬风险(ST / 停牌 / 次新 / 板块)—— 永远拦
# ══════════════════════════════════════════════════════════════════════════

class TestTier1AlwaysBlocks:
    def test_st_stock_excluded(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "name": "ST吉祥", "list_date": "20100101"}])
        result = _run(env, ["600001.SH"])
        assert "600001.SH" not in result.kept
        assert [r.primitive for r in result.rejected] == [mh.REJECT_STOCK_HYGIENE]

    def test_suspended_stock_excluded(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        write_daily_fixture(env, "suspend_d", D0, [{"ts_code": "600001.SH"}])
        result = _run(env, ["600001.SH"])
        assert "600001.SH" not in result.kept
        assert [r.primitive for r in result.rejected] == [mh.REJECT_SUSPENDED]

    def test_non_suspended_stock_not_blocked_by_suspend_check(self, isolated_env):
        """同一天 `suspend_d` 分区里**有其它票**,但不含本票 → 不当停牌。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        write_daily_fixture(env, "suspend_d", D0, [{"ts_code": "999999.SZ"}])
        result = _run(env, ["600001.SH"])
        assert "600001.SH" in result.kept

    def test_non_new_stock_excluded(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20240401"}])   # 上市仅 7 天
        result = _run(env, ["600001.SH"])
        assert "600001.SH" not in result.kept
        assert [r.primitive for r in result.rejected] == [mh.REJECT_NON_NEW_STOCK]

    def test_board_not_allowed_excluded(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "830001.BJ", "list_date": "20100101", "market": "北交所"}])
        result = _run(env, ["830001.BJ"])
        assert "830001.BJ" not in result.kept
        assert [r.primitive for r in result.rejected] == [mh.REJECT_STOCK_HYGIENE]

    def test_meta_missing_excluded_fail_closed(self, isolated_env):
        """`stock_basic` 完全查无此票——tier-1「算不出就是异常,不放行」。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        result = _run(env, ["999999.SZ"])
        assert "999999.SZ" not in result.kept
        assert [r.primitive for r in result.rejected] == [mh.REJECT_META_MISSING]

    def test_clean_stock_is_kept(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        result = _run(env, ["600001.SH"])
        assert result.kept == frozenset({"600001.SH"})
        assert result.rejected == ()


# ══════════════════════════════════════════════════════════════════════════
# 两级保险丝 · 贵的趋势/流动性线(ma20/amount_ma20)—— 算不出才降级
# ══════════════════════════════════════════════════════════════════════════

class TestTier2DegradesWhenExpensiveDataMissing:
    def test_liquidity_panel_missing_degrades_to_permit_and_discloses(self, isolated_env, monkeypatch):
        """面板整体读取失败(如 `build_research_panel` 抛异常)→ ma20/amount_ma20
        这一维**放行**,`hygiene_unavailable=True` 如实披露——不是静默当"都合格"
        (本测试仍能看出 flag 被设,不是无痕放过)。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])

        def _boom(*a, **kw):
            raise RuntimeError("模拟面板装配失败")

        monkeypatch.setattr(mh, "build_research_panel", _boom)
        result = _run(env, ["600001.SH"])
        assert "600001.SH" in result.kept
        assert result.hygiene_unavailable is True

    def test_st_still_blocked_even_when_liquidity_unavailable(self, isolated_env, monkeypatch):
        """**两级保险丝互相独立**:同一次调用里,ma20/amount_ma20 降级为不拦,
        丝毫不影响 ST 这条便宜的硬风险仍然被拦——不是"一降级就整体放水"。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "name": "ST吉祥", "list_date": "20100101"}])
        monkeypatch.setattr(mh, "build_research_panel", lambda *a, **kw: pl.DataFrame())
        result = _run(env, ["600001.SH"])
        assert "600001.SH" not in result.kept
        assert [r.primitive for r in result.rejected] == [mh.REJECT_STOCK_HYGIENE]
        assert result.hygiene_unavailable is True   # 降级确实发生了,只是不影响拦截结论

    def test_amount_ma20_below_threshold_with_real_panel_row_is_a_genuine_block(self, isolated_env, monkeypatch):
        """面板**真有该行**、`amount_ma20` 是一个算出来的低于阈值的实数(不是
        null)→ 正常判不通过(不进降级分支,「算不出」≠「算出来不够」)。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        panel = _fake_liquidity_panel([{"ts_code": "600001.SH", "ma20": 10.0, "amount_ma20": 500.0}])
        monkeypatch.setattr(mh, "build_research_panel", lambda *a, **kw: panel)
        result = _run(env, ["600001.SH"])
        assert "600001.SH" not in result.kept
        assert [r.primitive for r in result.rejected] == [mh.REJECT_STOCK_HYGIENE]
        assert result.hygiene_unavailable is False   # 这不是"算不出",是"算出来不够"

    def test_ma20_null_within_a_present_row_also_degrades_not_blocks(self, isolated_env, monkeypatch):
        """面板**真有该行**,但 `ma20` 字段本身是 null(如实际不满 20 个交易日)——
        **与"整行缺失"同等处理**(如实登记的设计判断,见模块头):`non_new_stock`
        的 120 自然日门槛远严于 ma20 需要的 20 个交易日,能通过 `non_new_stock`
        却 `ma20` 仍 null 现实里代表数据缺口而非"真的太新",按缺失处理才不会把
        "夹具只喂了一两天历史"误判成"真的不达标"。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        panel = _fake_liquidity_panel([{"ts_code": "600001.SH", "ma20": None, "amount_ma20": 30000.0}])
        monkeypatch.setattr(mh, "build_research_panel", lambda *a, **kw: panel)
        result = _run(env, ["600001.SH"])
        assert "600001.SH" in result.kept
        assert result.hygiene_unavailable is True

    def test_amount_ma20_above_threshold_with_real_panel_row_passes(self, isolated_env, monkeypatch):
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        panel = _fake_liquidity_panel([{"ts_code": "600001.SH", "ma20": 10.0, "amount_ma20": 30000.0}])
        monkeypatch.setattr(mh, "build_research_panel", lambda *a, **kw: panel)
        result = _run(env, ["600001.SH"])
        assert "600001.SH" in result.kept
        assert result.hygiene_unavailable is False

    def test_code_absent_from_otherwise_successful_panel_degrades_that_code_only(self, isolated_env, monkeypatch):
        """面板整体读取成功,但**这只票当天没有行**(如数据缺口)→ 只这只票的
        趋势/流动性维度降级为不拦,`hygiene_unavailable` 仍如实标 `True`。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [
            {"ts_code": "600001.SH", "list_date": "20100101"},
            {"ts_code": "600002.SH", "list_date": "20100101"},
        ])
        panel = _fake_liquidity_panel([{"ts_code": "600002.SH", "ma20": 10.0, "amount_ma20": 30000.0}])
        monkeypatch.setattr(mh, "build_research_panel", lambda *a, **kw: panel)
        result = _run(env, ["600001.SH", "600002.SH"])
        assert result.kept == frozenset({"600001.SH", "600002.SH"})
        assert result.hygiene_unavailable is True


# ══════════════════════════════════════════════════════════════════════════
# K4 安检两档语义(hard_cut → exclude;avoid_flag → tag,机器不禁)
# ══════════════════════════════════════════════════════════════════════════

def _seed_k4_version(env, *, hard_cut: Dict[str, Any] = None, avoid_flag: Dict[str, Any] = None) -> None:
    from neckline.strategy import brain

    brain.save_version(
        "K4",
        rule={"config": {}, "k4_advisory": {"hard_cut": hard_cut or {}, "avoid_flag": avoid_flag or {}}},
        changelog="⑤-b 测试", activate=False, db_path=env.db_path,
    )


class TestK4AdvisoryGateTwoTiers:
    def test_hard_cut_hit_excluded(self, isolated_env, monkeypatch):
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        _seed_k4_version(env, hard_cut={"A1_turnover_gt_10": {"expr": "turnover_rate > 10", "evidence": "x"}})
        monkeypatch.setattr(
            mh, "_load_k4_panel_rows",
            lambda codes, trade_date, parquet_dir: {"600001.SH": {"_hit_A1": True}},
        )
        result = _run(env, ["600001.SH"])
        assert "600001.SH" not in result.kept
        assert [r.primitive for r in result.rejected] == [mh.REJECT_K4_ADVISORY]
        assert "600001.SH" not in result.k4_tag_of

    def test_avoid_flag_hit_kept_and_tagged(self, isolated_env, monkeypatch):
        """**验收核心**:avoid_flag 命中不拦,仍在 `kept` 里,但带标——两路分得开,
        不是"命中就一律排除"或"命中就一律无感放行"。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        _seed_k4_version(env, avoid_flag={"B4_chase_strong_red": {"expr": "close>ma20 & ret_1d>5", "evidence": "x"}})
        monkeypatch.setattr(
            mh, "_load_k4_panel_rows",
            lambda codes, trade_date, parquet_dir: {"600001.SH": {"_hit_B4": True}},
        )
        result = _run(env, ["600001.SH"])
        assert "600001.SH" in result.kept
        assert result.k4_tag_of.get("600001.SH") == "avoid_flag"
        assert result.rejected == ()

    def test_no_k4_hit_is_not_tagged(self, isolated_env, monkeypatch):
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        monkeypatch.setattr(mh, "_load_k4_panel_rows", lambda codes, trade_date, parquet_dir: {})
        result = _run(env, ["600001.SH"])
        assert "600001.SH" in result.kept
        assert "600001.SH" not in result.k4_tag_of

    def test_k4_evaluation_failure_degrades_to_permit_and_discloses(self, isolated_env, monkeypatch):
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])

        def _boom(codes, trade_date, parquet_dir):
            raise RuntimeError("模拟 K4 面板装配失败")

        monkeypatch.setattr(mh, "_load_k4_panel_rows", _boom)
        result = _run(env, ["600001.SH"])
        assert "600001.SH" in result.kept
        assert result.k4_unavailable is True

    def test_unregistered_advisory_code_defaults_to_avoid_flag_not_hard_cut(self, isolated_env, monkeypatch):
        """K4 分区归属查无该 advisory 码(缺 DB 登记)时,**判 hard_cut 用的 `.get`
        给默认值**(保守当 avoid_flag、不拦)——同 `intel_candidates.py` 既有姿势
        (CLAUDE.md「复用与设计体例」条,两处 `.get()` 语义刻意不同)。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        insert_stock_basic(env, [{"ts_code": "600001.SH", "list_date": "20100101"}])
        _seed_k4_version(env)  # 空 K4 行:hard_cut={}, avoid_flag={}
        monkeypatch.setattr(
            mh, "_load_k4_panel_rows",
            lambda codes, trade_date, parquet_dir: {"600001.SH": {"_hit_A1": True}},   # 命中但未登记分区
        )
        result = _run(env, ["600001.SH"])
        assert "600001.SH" in result.kept          # 不当 hard_cut,不拦
        assert "600001.SH" not in result.k4_tag_of  # 但也不打 avoid_flag 标(未在 DB 明确登记)


# ══════════════════════════════════════════════════════════════════════════
# 交叉断言:本模块的判定与 ③ 原语 `run()` 逐位相同(禁两处口径漂移)
# ══════════════════════════════════════════════════════════════════════════

class TestCrossCheckAgainstPrimitivesDirectly:
    # ⚠ 不含 `ma20`/`amount_ma20`/`close` 为 `None` 的用例:那些场景本模块会走
    # 「面板缺失 → 降级为不拦」分支(见 `TestTier2DegradesWhenExpensiveDataMissing`),
    # 与裸调用原语(不知道"降级"这回事)天然不同,不是"两处口径漂移",不适合拿来
    # 做"逐位相同"的交叉断言——这条断言测的是**三个数值都算得出来**时,本模块的
    # 卫生线判定是否与原语 `run()` 完全一致(没有偷偷加宽/收紧任何阈值)。
    @pytest.mark.parametrize(
        "is_st,board,close,ma20,amount_ma20,expect_pass",
        [
            (True, "MAIN", 10.0, 10.0, 30000.0, False),     # ST → 不通过
            (False, "BSE", 10.0, 10.0, 30000.0, False),      # 板块不在白名单 → 不通过
            (False, "MAIN", 1.0, 10.0, 30000.0, False),      # 低于 close_min → 不通过
            (False, "MAIN", 10.0, 10.0, 500.0, False),       # amount_ma20 不达标 → 不通过
            (False, "MAIN", 10.0, 10.0, 30000.0, True),      # 全部合格 → 通过
        ],
    )
    def test_hygiene_row_matches_primitive_run(
        self, isolated_env, monkeypatch, is_st, board, close, ma20, amount_ma20, expect_pass,
    ):
        env = isolated_env
        insert_trade_cal(env, [D0])
        name = "ST吉祥" if is_st else "正常股"
        insert_stock_basic(env, [
            {"ts_code": "600001.SH", "name": name, "list_date": "20100101",
             "market": {"MAIN": "主板", "GEM": "创业板", "STAR": "科创板", "BSE": "北交所"}[board]},
        ])
        panel = _fake_liquidity_panel([{"ts_code": "600001.SH", "ma20": ma20, "amount_ma20": amount_ma20}])
        monkeypatch.setattr(mh, "build_research_panel", lambda *a, **kw: panel)
        result = _run(env, ["600001.SH"], close_of={"600001.SH": close})

        # 本模块的结论
        got_pass = "600001.SH" in result.kept

        # ③ 原语直接跑同一份 row,期望逐位相同
        row = {"is_st": is_st, "board": board, "close": close, "ma20": ma20, "amount_ma20": amount_ma20}
        primitive_pass = PRIMITIVES["stock_hygiene"].run(row, _HYGIENE_PARAMS)

        assert got_pass == primitive_pass == expect_pass
