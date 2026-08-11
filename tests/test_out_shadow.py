"""V2.3.2-③ OUT 研究影子对照的机器判据(plan §五 ③ 验收清单 + ⑧-1 / ⑧-2 拍板)。

覆盖:
    ③-A 六项读数齐、口径复用 ⑨ 日复盘、`(d0_date, ts_code)` 主键 + 全部出局记录
        另存、append-only 幂等;**结构性禁令的 AST 守门**(零 import 交易时钟 /
        持仓、零写正式结论表);
    ③-B ⑧-1「表现最强」排序(相对强弱降序 + 涨幅同分)、「三只随机」crc32 可复现、
        ⑧-2 五条 AND(⛔ 不是加权打分)、前 20% 分母 = 当日全部 OUT、
        **连续 2 次的扩大 / 恢复状态机**、🔴 **重跑周报不得推进连续计数**;
        **唯一一次 LLM 调用**(AST 数死)+ unit 配额与 `REVIEW_BUDGET_SECONDS` 的关系。
"""

from __future__ import annotations

import ast
import json
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from neckline.review import out_shadow as os_mod
from neckline.selection import basket_store as store

D0 = date(2024, 4, 8)
D0_S = "20240408"
D1 = date(2024, 4, 9)
D1_S = "20240409"
_ROOT = Path(__file__).resolve().parent.parent
_MODULE = _ROOT / "neckline" / "review" / "out_shadow.py"


def _day(bars: Dict[str, Dict[str, Any]], *, index_ret: float = 0.005,
         limits: Dict[str, Dict[str, Any]] | None = None) -> SimpleNamespace:
    return SimpleNamespace(bars=bars, limits=limits or {}, index_ret=index_ret,
                           index_code="000001.SH")


def _bar(pct: float, *, open_=10.0, high=10.5, low=9.8, close=10.2,
         pre_close=10.0) -> Dict[str, Any]:
    return {"open": open_, "high": high, "low": low, "close": close,
            "pre_close": pre_close, "pct_chg": pct, "vol": 12345.0, "amount": 6789.0}


def _seed_out(db_path: Path, entries) -> None:
    """写几条 D0 OUT 票(走 ②-B 的唯一写入口,⛔ 测试不自己拼 INSERT)。"""
    baskets = {}
    dropped = []
    for key, codes, reason, gate in entries:
        baskets[key] = SimpleNamespace(
            basket_key=key, name=key,
            members=tuple(SimpleNamespace(ts_code=c, name=c, role_llm="core") for c in codes))
        dropped.append(SimpleNamespace(basket_key=key, reason=reason, mech_score=0.5,
                                       name=key, gate=gate, gate_detail=f"{gate}:detail"))
    store.save_out_candidates(D0, dropped, baskets, db_path=db_path)


# ══════════════════════════════════════════════════════════════════════════
# ③-A D1 机械记录
# ══════════════════════════════════════════════════════════════════════════

class TestDailyRecord:
    def test_three_out_stocks_get_three_rows_with_all_six_readings(self, isolated_env):
        """plan ③ 验收:造 3 只 OUT + D1 行情 → `out_shadow_daily` 恰 3 行且六项读数齐。"""
        env = isolated_env
        _seed_out(env.db_path, [("k1", ["600001.SH", "600002.SH"], "position_unfit", "position"),
                                ("k2", ["600003.SH"], "core_unfit", "core")])
        day = _day({"600001.SH": _bar(3.0), "600002.SH": _bar(-1.0),
                    "600003.SH": _bar(6.0)})
        res = os_mod.record_day(D1, d0=D0, day=day, db_path=env.db_path)
        assert res.candidates == 3 and res.inserted == 3
        rows = os_mod.load_out_shadow(D0, db_path=env.db_path)
        assert [r["ts_code"] for r in rows] == ["600001.SH", "600002.SH", "600003.SH"]
        r0 = rows[0]
        # K8 §十四 六项:涨跌幅 / 最高 / 最低 / 收盘状态 / 相对强弱 / 支撑与失效原始数据
        assert r0["pct_chg"] == pytest.approx(0.03)          # 小数,同 member_return 口径
        assert r0["high"] == 10.5 and r0["low"] == 9.8
        assert r0["close_state"] in os_mod.CLOSE_STATES
        assert "rel_strength" in r0 and r0["d1_date"] == D1_S
        det = r0["detail"]
        assert det["bar_available"] is True and det["direction"] == "up"
        assert det["rel_strength"]["index"] == pytest.approx(0.03 - 0.005)

    def test_missing_d1_bar_is_recorded_as_unknown_not_as_zero(self, isolated_env):
        """缺数 = 不知道:D1 没有该票行情 → 读数 `None` + `no_bar`,⛔ 不补 0。"""
        env = isolated_env
        _seed_out(env.db_path, [("k1", ["600001.SH"], "mech_gate_rejected", "sector")])
        os_mod.record_day(D1, d0=D0, day=_day({}), db_path=env.db_path)
        row = os_mod.load_out_shadow(D0, db_path=env.db_path)[0]
        assert row["pct_chg"] is None and row["high"] is None and row["low"] is None
        assert row["close_state"] == "no_bar"
        assert row["rel_strength"] is None

    def test_one_stock_in_two_out_baskets_gets_one_row_and_keeps_both_records(
            self, isolated_env):
        """🔴 主键刻意是 `(d0_date, ts_code)`、⛔ 不含 `basket_key`:**D1 读数是这只票的
        属性**,存两份就是两个事实源。`out_gate`/`out_reason` 取**确定性的第一条**
        (按 `basket_key` 升序),全部出局记录另存进 `all_out_records`(⛔ 不丢)。"""
        env = isolated_env
        _seed_out(env.db_path, [("kB", ["600001.SH"], "core_unfit", "core"),
                                ("kA", ["600001.SH"], "position_unfit", "position")])
        os_mod.record_day(D1, d0=D0, day=_day({"600001.SH": _bar(2.0)}), db_path=env.db_path)
        rows = os_mod.load_out_shadow(D0, db_path=env.db_path)
        assert len(rows) == 1                                 # 一票一行
        assert rows[0]["out_gate"] == "position"              # kA < kB,确定性第一条
        recs = rows[0]["detail"]["all_out_records"]
        assert [r["basket_key"] for r in recs] == ["kA", "kB"]   # 两条都在

    def test_capacity_overflow_never_enters_the_shadow_sample(self, isolated_env):
        """🔴 「档位已满 · 未定档」**不是 OUT**(K8 §八)—— 它们关口全过了、只是装不下,
        混进错杀分析会污染样本。②-B 在写入侧就把它挡住了,这里正面钉死。"""
        env = isolated_env
        _seed_out(env.db_path, [("k-full", ["600009.SH"], "capacity_overflow", None),
                                ("k-out", ["600001.SH"], "core_unfit", "core")])
        os_mod.record_day(D1, d0=D0, day=_day({"600001.SH": _bar(1.0),
                                               "600009.SH": _bar(9.0)}),
                          db_path=env.db_path)
        rows = os_mod.load_out_shadow(D0, db_path=env.db_path)
        assert [r["ts_code"] for r in rows] == ["600001.SH"]

    def test_rerun_is_idempotent(self, isolated_env):
        env = isolated_env
        _seed_out(env.db_path, [("k1", ["600001.SH"], "core_unfit", "core")])
        day = _day({"600001.SH": _bar(1.0)})
        assert os_mod.record_day(D1, d0=D0, day=day, db_path=env.db_path).inserted == 1
        again = os_mod.record_day(D1, d0=D0, day=day, db_path=env.db_path)
        assert again.inserted == 0 and again.existing == 1
        assert len(os_mod.load_out_shadow(D0, db_path=env.db_path)) == 1

    def test_never_raises_even_on_garbage_input(self, isolated_env):
        """契约:**永不抛异常**(它是复盘段的旁路)。"""
        res = os_mod.record_day("不是日期", db_path=isolated_env.db_path)
        assert res.notes and res.inserted == 0

    def test_sector_is_the_primary_basis_and_index_the_secondary(self):
        """⑧-1:**板块为主要基准、市场指数为辅助基准,两者都要算**。
        落库列 `rel_strength` 存的是板块那一个。"""
        day = _day({"600001.SH": _bar(4.0)}, index_ret=0.01)
        rs = os_mod.relative_strength_of(
            "600001.SH", day, industry_of={"600001.SH": "半导体"},
            industry_median_ret={"半导体": 0.02})
        assert rs["sector"] == pytest.approx(0.04 - 0.02)
        assert rs["index"] == pytest.approx(0.04 - 0.01)

    def test_sector_basis_absent_degrades_honestly(self):
        """行业基准取不到 → 板块那一路 `None`(⛔ 不退化成拿指数冒充板块)。"""
        rs = os_mod.relative_strength_of(
            "600001.SH", _day({"600001.SH": _bar(4.0)}),
            industry_of={}, industry_median_ret={})
        assert rs["sector"] is None and rs["index"] is not None


# ══════════════════════════════════════════════════════════════════════════
# ③-B ⑧-1 排序 / ⑧-2 判据与状态机
# ══════════════════════════════════════════════════════════════════════════

def _row(code: str, *, rs, pct, gate="core", d0=D0_S) -> Dict[str, Any]:
    return {"ts_code": code, "d0_date": d0, "rel_strength": rs, "pct_chg": pct,
            "out_gate": gate, "detail": {}}


class TestStrongestOrdering:
    def test_relative_strength_desc_then_pct_chg(self):
        """⑧-1:**D1 相对强弱降序,D1 最高涨幅作为同分排序**。"""
        rows = [_row("A", rs=0.01, pct=0.09), _row("B", rs=0.05, pct=0.01),
                _row("C", rs=0.05, pct=0.08)]
        assert [r["ts_code"] for r in os_mod.rank_by_strength(rows)] == ["C", "B", "A"]

    def test_pure_big_gainer_weaker_than_its_sector_never_outranks(self):
        """⚠ ⑧-1 的目的说明:**单纯涨幅高、但弱于所属板块的票不该排在前面**。
        这由"主排序键是相对强弱"本身实现 —— ⛔ 不是再叠一道涨幅过滤器
        (下面那只涨 9% 但板块超额为负的票必须排在涨 1% 但跑赢板块的后面)。"""
        rows = [_row("BIG", rs=-0.02, pct=0.09), _row("REL", rs=0.03, pct=0.01)]
        assert [r["ts_code"] for r in os_mod.rank_by_strength(rows)] == ["REL", "BIG"]

    def test_unknown_relative_strength_sorts_last_not_as_zero(self):
        """「算不出」不是「持平」:⛔ 不许把 None 当 0 混进排序中段。"""
        rows = [_row("N", rs=None, pct=0.20), _row("P", rs=0.001, pct=0.0)]
        assert [r["ts_code"] for r in os_mod.rank_by_strength(rows)] == ["P", "N"]

    def test_random_pick_is_crc32_and_reproducible(self):
        """「三只随机」用 `crc32` 排序取前三 —— 同一个 `(d0, 候选集)` 重跑逐位相同。
        ⛔ 不用内置 `hash()`(带进程盐,`PYTHONHASHSEED` 一变就漂)。"""
        rows = [_row(f"{600000 + i}.SH", rs=0.001 * i, pct=0.0) for i in range(12)]
        a = os_mod.pick_review_sample(rows, top_n=5, random_n=3)
        b = os_mod.pick_review_sample(list(reversed(rows)), top_n=5, random_n=3)
        assert [r["ts_code"] for r in a[1]] == [r["ts_code"] for r in b[1]]
        assert len(a[1]) == 3
        # 随机三只**不与最强五只重叠**(重叠会让"八只"缩水)
        assert not ({r["ts_code"] for r in a[0]} & {r["ts_code"] for r in a[1]})

    def test_top_quantile_denominator_is_the_whole_day_not_the_eight(self):
        """⑧-2 第 4 条:**分母 = 当日全部 OUT**,⛔ 不是被复核的那 8 只。"""
        rows = [_row(f"{600000 + i}.SH", rs=i / 100.0, pct=0.0) for i in range(10)]
        cut = os_mod.top_rs_cutoff(rows)
        assert cut == pytest.approx(0.08)        # 10 只的前 20% = 前 2 名,门槛 = 第 2 名
        assert os_mod.top_rs_cutoff([]) is None

    def test_top_quantile_keeps_at_least_one_when_the_day_is_tiny(self):
        """当日只有 3 只 OUT 时,前 20% 向上取整留 1 只 —— ⛔ 不让这一条永假。"""
        rows = [_row("A", rs=0.05, pct=0.0), _row("B", rs=0.01, pct=0.0),
                _row("C", rs=0.0, pct=0.0)]
        assert os_mod.top_rs_cutoff(rows) == pytest.approx(0.05)


class TestMiskillCriteria:
    def test_only_core_or_position_gates_qualify(self):
        """⑧-2 第 1 条:**其余关口出局的不算**(这两关正是 ③ 要验的那两关)。"""
        for gate, expect in (("core", True), ("position", True),
                             ("market", False), ("sector", False), ("evidence", False)):
            m = os_mod.mechanical_miskill_gates(_row("A", rs=0.1, pct=0.0, gate=gate),
                                                rs_cutoff=0.05)
            assert m["gate_is_core_or_position"] is expect, gate

    def test_undetermined_quantile_is_none_not_false(self):
        """🔴 算不出相对强弱 → 第 4 条是「判不出」(`None`),⛔ 不当 False ——
        当 False 会让真错杀被静默漏掉。"""
        m = os_mod.mechanical_miskill_gates(_row("A", rs=None, pct=0.0), rs_cutoff=0.05)
        assert m["in_top_rs_quantile"] is None


class TestScopeStateMachine:
    def _hist(self, *rows):
        """`rows` = `(expanded, miskill_count)` 或 `(expanded, miskill_count, llm_stage)`。
        ⚠ 缺省 `llm_stage='ok'` = 那一周**真的跑成了** —— 非 ok 的周由
        `test_weeks_where_the_llm_never_ran_are_skipped_not_counted` 专门覆盖。"""
        out = []
        for i, row in enumerate(rows, start=1):
            e, c = row[0], row[1]
            stage = row[2] if len(row) > 2 else os_mod.LLM_STAGE_OK
            out.append({"week_anchor": f"2024040{i}", "expanded": e,
                        "obvious_miskill_count": c, "llm_stage": stage})
        return out

    def test_two_consecutive_weeks_with_two_or_more_expands(self):
        """⑧-2:**连续 2 次、每次 ≥2 只** → 扩大为 `10 + 5`。"""
        s = os_mod.resolve_scope(self._hist((0, 2), (0, 3)))
        assert (s.top_n, s.random_n, s.expanded) == (10, 5, True)

    def test_one_good_week_alone_does_not_expand(self):
        s = os_mod.resolve_scope(self._hist((0, 0), (0, 5)))
        assert (s.top_n, s.random_n, s.expanded) == (5, 3, False)

    def test_two_consecutive_quiet_weeks_restore(self):
        """扩大后**连续 2 次均 <2 只** → 恢复 `5 + 3`。"""
        s = os_mod.resolve_scope(self._hist((1, 1), (1, 0)))
        assert (s.top_n, s.random_n, s.expanded) == (5, 3, False)

    def test_mixed_history_holds_the_current_state(self):
        s = os_mod.resolve_scope(self._hist((1, 0), (1, 4)))
        assert s.expanded is True and (s.top_n, s.random_n) == (10, 5)

    def test_weeks_where_the_llm_never_ran_are_skipped_not_counted(self):
        """🔴🔴 **§七 P0-39 同款病**(2026-08-11 复审整改):`provider is None` /
        `parse_failed` / `call_failed:*` / `budget_exhausted` 四种情形下第 2/3/5 条全是
        `None` → 五条 AND 恒假 → `obvious_miskill_count=0` **照样落表**。
        原来 `resolve_scope` 只读那一列 → 处于扩大态时**连续两周 key 失效,第三周自动
        恢复 `5+3`** —— 把「这两周根本没查」讲成了「这两周查过、没发现错杀」。

        **正确行为 = 跳过**:非 ok 的周既不推进连续计数、也不打断它,状态原地不动。"""
        # 扩大态 + 连续两周 LLM 没跑成 → ⛔ 不许恢复
        s = os_mod.resolve_scope(self._hist((1, 0, "no_provider"),
                                            (1, 0, "call_failed:Timeout")))
        assert s.expanded is True and (s.top_n, s.random_n) == (10, 5)
        assert "跳过" in s.reason
        # 反向:同样两周但真的跑成了 → 恢复(证明上面那条不是"永远不恢复")
        back = os_mod.resolve_scope(self._hist((1, 0), (1, 0)))
        assert back.expanded is False and (back.top_n, back.random_n) == (5, 3)
        # 扩大方向同理:两周没跑成的 ≥2 只**不算数**(它们本来也不该有 miskill 计数)
        hold = os_mod.resolve_scope(self._hist((0, 3, "parse_failed"),
                                               (0, 3, "budget_exhausted")))
        assert hold.expanded is False and (hold.top_n, hold.random_n) == (5, 3)

    def test_a_failed_week_does_not_break_a_run_of_good_weeks(self):
        """「跳过」**不是「打断」**:好周 → 没跑成的周 → 好周,连续 2 次仍然成立。"""
        s = os_mod.resolve_scope(self._hist((0, 2), (0, 0, "no_provider"), (0, 3)))
        assert (s.top_n, s.random_n, s.expanded) == (10, 5, True)

    def test_numbers_come_from_the_ruling(self):
        """⑧-1/⑧-2 给死的数照抄,⛔ 工程侧一个都不许发明。"""
        assert (os_mod.SCOPE_TOP_DEFAULT, os_mod.SCOPE_RANDOM_DEFAULT) == (5, 3)
        assert (os_mod.SCOPE_TOP_EXPANDED, os_mod.SCOPE_RANDOM_EXPANDED) == (10, 5)
        assert os_mod.EXPANDED_TOTAL == 15
        assert os_mod.CONSECUTIVE_WEEKS == 2 and os_mod.MISKILL_TRIGGER == 2
        assert os_mod.TOP_RS_QUANTILE == 0.20


class TestWeeklyReviewPersistence:
    def _seed_week(self, env, codes):
        _seed_out(env.db_path, [("k1", codes, "core_unfit", "core")])
        os_mod.record_day(D1, d0=D0, day=_day({c: _bar(2.0 + i)
                                               for i, c in enumerate(codes)}),
                          db_path=env.db_path)

    def test_rerunning_the_same_week_never_advances_the_consecutive_counter(
            self, isolated_env):
        """🔴🔴 **⑧-2 最该有的一条**:连续计数必须落表,而且**重跑一次周报不得推进它**。
        落地 = 按 `week_anchor` 一周一行 + `INSERT OR IGNORE`,「连续几次」由读最近两行
        现算(⛔ 库里不存计数器 —— 计数器会被重跑推进一格,§六 那条教训)。"""
        env = isolated_env
        self._seed_week(env, ["600001.SH", "600002.SH"])
        first = os_mod.review_week(D0, D0, D0, provider=None, db_path=env.db_path)
        assert first.persisted is True
        again = os_mod.review_week(D0, D0, D0, provider=None, db_path=env.db_path)
        assert again.persisted is False                 # 命中已有行,什么都没改
        hist = os_mod.load_review_history(db_path=env.db_path)
        assert len(hist) == 1                           # ⛔ 重跑没有多出一行

    def test_rerunning_the_same_week_from_a_different_day_still_makes_one_row(
            self, isolated_env):
        """🔴🔴 **上一条恰好绕开的那个洞**(2026-08-11 复审整改):它两次都传**同一个**
        anchor,于是 `week_anchor` UNIQUE 当然命中。可 `scripts/weekly.py::_target_week()`
        的缺省是 `date.today() - timedelta(days=7)` —— **跑周报那天不同,anchor 就不同**,
        UNIQUE 锁的是日期不是「哪一周」→ 周六跑一次、周日再跑一次会落**两行**:
        同一份样本、同一个 `obvious_miskill_count`,凭一周的发现就把范围扩到 `10+5`。

        修法 = 落表前把 anchor 归一到 ISO 周一(`week_anchor_of`)。"""
        env = isolated_env
        self._seed_week(env, ["600001.SH", "600002.SH"])
        monday, saturday, sunday = date(2024, 4, 8), date(2024, 4, 13), date(2024, 4, 14)
        assert (os_mod.week_anchor_of(saturday) == os_mod.week_anchor_of(sunday)
                == os_mod.week_anchor_of(monday) == "20240408")
        first = os_mod.review_week(saturday, D0, D0, provider=None, db_path=env.db_path)
        again = os_mod.review_week(sunday, D0, D0, provider=None, db_path=env.db_path)
        assert first.persisted is True and again.persisted is False
        hist = os_mod.load_review_history(db_path=env.db_path)
        assert len(hist) == 1, "⛔ 同一周换个跑法不许多出一行"
        assert hist[0]["week_anchor"] == "20240408"     # 归一到 ISO 周一

    def test_no_provider_still_produces_mechanical_readings(self, isolated_env):
        """无 key → 只出机械读数(第 2/3/5 条未判),**不算失败**;
        且「判不出」的条目一律进 `undetermined`,⛔ 不算成明显错杀。"""
        env = isolated_env
        self._seed_week(env, ["600001.SH"])
        res = os_mod.review_week(D0, D0, D0, provider=None, db_path=env.db_path)
        assert res.llm_stage == "no_provider" and res.reviewed == 1
        assert res.obvious_miskill == 0
        assert res.per_stock[0]["undetermined"]

    def test_five_conditions_are_an_and_not_a_weighted_score(self, isolated_env):
        """⑧-2:五条**同时满足**才算明显错杀(⛔ 不是加权打分)——
        四条真、一条假 → 仍然不算。"""
        env = isolated_env
        self._seed_week(env, ["600001.SH"])

        class _P:  # provider 替身:只要非 None 即走 LLM 路径
            pass

        def fake(picks, **kw):
            return "ok", "叙述", [{"ts_code": "600001.SH", "engine_signal": True,
                                   "invalidation_untriggered": True,
                                   "overturns_original_reason": False}]
        os_mod._run_review_llm = fake  # type: ignore[assignment]
        try:
            res = os_mod.review_week(D0, D0, D0, provider=_P(), db_path=env.db_path)
        finally:
            import importlib

            importlib.reload(os_mod)
        assert res.obvious_miskill == 0
        assert res.per_stock[0]["conditions"]["c5_overturns_original_reason"] is False


# ══════════════════════════════════════════════════════════════════════════
# 结构性守门(AST / 静态)
# ══════════════════════════════════════════════════════════════════════════

def test_out_shadow_never_touches_the_trading_side():
    """🔴 K8 §十四 逐字:OUT 研究影子对照**不进 T1/T2、不启交易时钟、不计入正式样本、
    不增加用户手工填写**。做成结构性保证:本模块零 import 交易时钟 / 持仓侧。"""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    mods: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    banned = ("neckline.review.trade_clock", "neckline.sentinel.positions",
              "neckline.sentinel", "neckline.report.positions_entry",
              "neckline.review.selection_clock")
    for m in mods:
        assert not any(m == b or m.startswith(b + ".") for b in banned), m


def test_out_shadow_never_writes_to_any_formal_verdict_table():
    """⛔ 零写 `selection_clock` / `baskets` / `tier_history` / `basket_cards`
    (裁定 5:影子结果不得回写当时的正式选股结论),且两张自有表只增不改。"""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    sql: List[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"execute", "executemany", "executescript"}
                and node.args):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sql.append(arg.value.upper())
            elif isinstance(arg, ast.JoinedStr):
                sql.append("".join(v.value.upper() for v in arg.values
                                   if isinstance(v, ast.Constant)
                                   and isinstance(v.value, str)))
    joined = " ".join(" ".join(s.split()) for s in sql)
    for table in ("SELECTION_CLOCK", "BASKETS", "TIER_HISTORY", "BASKET_CARDS"):
        assert table not in joined, table
    for banned in ("UPDATE ", "DELETE ", "INSERT OR REPLACE", "REPLACE INTO"):
        assert banned not in joined, banned


def test_the_weekly_out_review_is_exactly_one_llm_call():
    """🔴 「一次调用管八只」(⛔ 不逐票调用):本模块里 `judge_candidate(...)` 的调用点
    恒为 **1 个**,且**零** `provider.chat(...)`(调用/解析/降级链一律复用
    `judge_candidate`,项目铁律)。AST 数,⛔ 不数字符串。"""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    judge_calls = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "judge_candidate"]
    assert len(judge_calls) == 1, [n.lineno for n in judge_calls]
    chat_calls = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "chat"]
    assert not chat_calls, [n.lineno for n in chat_calls]


def test_weekly_unit_timeout_is_strictly_above_the_llm_budget():
    """🔴 **plan ③-B 点名的那颗雷**:`neckline-weekly.service::TimeoutStartSec` 原本
    **恰等于** `REVIEW_BUDGET_SECONDS`(两个 900,含义完全不同却数值相等)——
    ③-B 加了一次周度 LLM 调用后,「预算耗尽」与「systemd SIGTERM」会落在同一秒。

    ⛔ 谁把它调回 ≤ 预算上限,这条就红。"""
    from neckline.llm.budget import REVIEW_BUDGET_SECONDS

    unit = (_ROOT / "deploy" / "neckline-weekly.service").read_text(encoding="utf-8")
    values = [int(ln.split("=", 1)[1].strip()) for ln in unit.splitlines()
              if ln.strip().startswith("TimeoutStartSec=")]
    assert len(values) == 1, values
    assert values[0] > REVIEW_BUDGET_SECONDS, (values[0], REVIEW_BUDGET_SECONDS)
