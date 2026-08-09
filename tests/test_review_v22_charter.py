"""周复盘在 `v2.2-k8` 治下的两处收窄(V2.2-⑤-A 连带改动,🔴 碰纪律判定)。

§五 ⑤「连带改动」两条:
  ① **`check_time_exit_discipline` 整项作废**(章程无时间退出 → 无违纪可判),改为在周报里
     **如实写一句**「本周期章程无时间退出条款」——⛔ 不产违纪,也**不静默跳过**。
  ② **`classify_stop_discipline` 由「违纪判定」降为「警戒记录」**:仍统计破线未走的**笔数与
     金额**(§1.3 第一死因的持续体检),但**不再计入违纪计数**。

🔴 **两处都锚「当时 governing 的那版章程」,不是"今天的章程"**(§2.1 前置提示 + 时间轴纪律
「不用今天的章程重判历史周」)。这条**两个方向都成立**,本文件双向各锁一次:
  · `v1.3.3` 治下的历史周 → 照判、一条不少(新章程**不许洗白**旧违纪);
  · `v2.2-k8` 治下的周 → 不判(旧章程**不许罚**新口径下的行为)。

⚠ **判据来源刻意不同,别套错**:
  · 时间退出侧 = **config 里读得出来**(`max_hold_days is None`);
  · 止损侧 = **config 里读不出来** —— `stop_pct` 两版**都是 0.05**(§五 ⑤ 明写「值与唯一源
    地位一字不动,改的是它触发什么」),差异只活在 §2.1 的条文里,故按版本号声明式登记
    (`brain.STOP_ADVISORY_CHARTERS`,体例同 `activate_charter._CORE_EXPECTATIONS`)。
"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.review.reconcile import (
    STOP_BREACHED,
    iso_week_key,
    run_weekly_review,
)
from neckline.strategy import brain
from tests.conftest import TEST_RULE_V1_CONFIG, set_activation_timeline
from tests.test_review_reconcile import _trade

pytestmark = pytest.mark.usefixtures("isolated_env")

# 与 `test_review_reconcile.py::_DueItem` 同款 duck-typed 体检行(只喂落库需要的字段)。
from tests.test_review_reconcile import _DueItem, _seed_time_exit_case  # noqa: E402

_V133_CFG = dict(TEST_RULE_V1_CONFIG, take_profit_retrace=0.08, max_hold_days=5,
                 max_hold_days_profit=15, time_exit_only_if_unprofitable=True,
                 single_cap=40000.0, max_positions=3, max_exposure_frac=1.0,
                 forbid_high_elasticity=False)
_K8_CFG = dict(_V133_CFG, take_profit_retrace=None, max_hold_days=None,
               max_hold_days_profit=None, time_exit_only_if_unprofitable=False)


def _seed_charters(env, *, governing: str) -> None:
    """落两版章程,并把激活时间线重写成「`governing` 从 2026-01-01 起治权」。

    ⚠ 必须走 `set_activation_timeline`(v1.4 review 🟡-1):判定读的是 append-only 的
    `strategy_activation_log`,只 UPDATE `activated_at` 造不出有效历史。"""
    brain.save_version("v1.3.3", {"config": dict(_V133_CFG), "lineage": "K1"},
                       "测试:v1.3.3", activate=False, db_path=env.db_path)
    brain.save_version("v2.2-k8", {"config": dict(_K8_CFG), "lineage": "K1"},
                       "测试:v2.2-k8", activate=False, db_path=env.db_path)
    set_activation_timeline(env.db_path, [(governing, "2026-01-01T02:00:00+00:00")],
                            active=governing)


# 买 100 @300 → 卖 100 @270(−10%,深破 −5% 容差带下沿)= 一笔"破线未走"的回合。
_BREACH_TRADES = [
    _trade(date(2026, 7, 22), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),
    _trade(date(2026, 7, 24), "600519.SH", "sell", 270.0, 100, name="贵州茅台"),
]


def _week(env, trades, day: date):
    reviews, _ = run_weekly_review(trades, db_path=env.db_path, parquet_dir=env.parquet_dir)
    return next(r for r in reviews if r.week == iso_week_key(day))


# ======================================================================
#  ① 时间退出:章程无该条款 → 不产违纪,但有一句如实说明
# ======================================================================

class TestTimeExitClauseRetired:
    def test_no_violation_under_k8_charter(self, isolated_env):
        """`v2.2-k8` 治下:系统历史上判过「该走」、台账没走 —— **不记违纪**(章程无这条)。"""
        _seed_charters(isolated_env, governing="v2.2-k8")
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=None)
        wk = _week(isolated_env, _BREACH_TRADES, date(2026, 7, 22))
        assert not any("时间退出纪律" in m for m in wk.discipline_violations)

    def test_but_says_so_out_loud(self, isolated_env):
        """⛔ **不静默跳过**:周报必须有一句「本周期章程无时间退出条款」(§五 ⑤ 验收)。"""
        _seed_charters(isolated_env, governing="v2.2-k8")
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=None)
        wk = _week(isolated_env, _BREACH_TRADES, date(2026, 7, 22))
        assert any("无时间退出条款" in n for n in wk.charter_notes), wk.charter_notes
        # 说明是**独立一段**,⛔ 不许混进违纪清单(那会把"没这条规矩"讲成"你犯规")
        assert not any("无时间退出条款" in m for m in wk.discipline_violations)

    def test_note_reaches_the_material_text(self, isolated_env):
        """那句说明必须真的出现在给人读的复盘材料里(不是只躺在 dataclass 里)。"""
        from neckline.review.material import build_material_text

        _seed_charters(isolated_env, governing="v2.2-k8")
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=None)
        wk = _week(isolated_env, _BREACH_TRADES, date(2026, 7, 22))
        assert "无时间退出条款" in build_material_text(wk)

    def test_note_reaches_the_wire_contract(self, isolated_env):
        """落库 / 响应形状(`weekly_review_dict`)带 `charterNotes`(新增可选键,B 类纪律)。"""
        from neckline.review.reconcile import weekly_review_dict

        _seed_charters(isolated_env, governing="v2.2-k8")
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=None)
        wk = _week(isolated_env, _BREACH_TRADES, date(2026, 7, 22))
        d = weekly_review_dict(wk)
        assert "charterNotes" in d and any("无时间退出条款" in n for n in d["charterNotes"])

    def test_history_under_v133_is_still_judged(self, isolated_env):
        """🔴 **反向**:`v1.3.3` 治下的周照判、一条不少 —— 新章程**不许洗白**旧违纪。"""
        _seed_charters(isolated_env, governing="v1.3.3")
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=None)
        wk = _week(isolated_env, _BREACH_TRADES, date(2026, 7, 22))
        assert any("时间退出纪律" in m for m in wk.discipline_violations)
        assert not any("无时间退出条款" in n for n in wk.charter_notes)


# ======================================================================
#  ② 止损:违纪判定 → 警戒记录(统计一条不少)
# ======================================================================

class TestStopDisciplineDowngrade:
    def test_breach_still_counted_but_not_a_violation_under_k8(self, isolated_env):
        """`v2.2-k8` 治下:破线未走 —— **`STOP_BREACHED` 统计照旧**,但**不进违纪清单**。

        ⛔ 别把统计也一起降级掉:§1.3 第一死因的持续体检不能停(§五 ⑤ 明写)。"""
        _seed_charters(isolated_env, governing="v2.2-k8")
        wk = _week(isolated_env, _BREACH_TRADES, date(2026, 7, 22))
        kinds = [k for _rt, k, _n in wk.stop_discipline]
        assert STOP_BREACHED in kinds, "破线未走的笔数统计被一起删了 —— 体检不能停"
        assert not any("止损" in m and "违纪" in m for m in wk.discipline_violations)

    def test_note_explains_why_it_is_not_a_violation(self, isolated_env):
        _seed_charters(isolated_env, governing="v2.2-k8")
        wk = _week(isolated_env, _BREACH_TRADES, date(2026, 7, 22))
        assert any("止损警戒" in n and "不计违纪" in n for n in wk.charter_notes), wk.charter_notes
        note = next(n for _rt, k, n in wk.stop_discipline if k == STOP_BREACHED)
        assert "警戒" in note and "违纪" in note      # 文案说清「记为警戒、不计违纪」

    def test_history_under_v133_is_still_a_violation(self, isolated_env):
        """🔴 **反向**:`v1.3.3` 治下(§2.1 第 1 条仍是强制条件单)→ 照记违纪,文案不变。"""
        _seed_charters(isolated_env, governing="v1.3.3")
        wk = _week(isolated_env, _BREACH_TRADES, date(2026, 7, 22))
        assert any("§2.1 第1条违纪" in m for m in wk.discipline_violations)
        assert not any("止损警戒" in n for n in wk.charter_notes)


# ======================================================================
#  ③ 止损口径判据本身(单一源 `brain`)
# ======================================================================

class TestStopRegimePredicate:
    def test_only_k8_is_advisory(self):
        assert brain.stop_is_advisory("v2.2-k8") is True
        for mandatory in ("v1.3.3", "v1.3", "v1.2", "K1", "", None, "v2.3-future"):
            assert brain.stop_is_advisory(mandatory) is False, mandatory

    def test_unknown_version_defaults_to_mandatory(self):
        """默认方向 = **更严**(破线未走照记违纪)。漏登记的代价是"多记一条"(吵),
        不是"少记一条"(静默漏审)—— 方向刻意选前者。"""
        assert brain.stop_is_advisory("v9.9-nobody-registered") is False

    def test_active_variant_reads_the_active_row(self, isolated_env):
        _seed_charters(isolated_env, governing="v2.2-k8")
        assert brain.active_stop_is_advisory(db_path=isolated_env.db_path) is True
        # 换现役走 `activate_version`(⛔ 不能用 `save_version(activate=False)` 覆盖现役行
        # —— brain 有硬护栏:那会造成「全库无现役版本」,审计 🔵-8)。
        brain.activate_version("v1.3.3", db_path=isolated_env.db_path)
        assert brain.active_stop_is_advisory(db_path=isolated_env.db_path) is False

    def test_no_active_version_defaults_to_mandatory(self, isolated_env):
        assert brain.active_stop_is_advisory(db_path=isolated_env.db_path) is False


# ======================================================================
#  ④ 哨兵 / 端点侧的文案(只换口吻,⛔ 判定与阈值一字未动)
# ======================================================================

class TestStopWordingIsCharterDriven:
    @staticmethod
    def _pos_and_quote():
        from neckline.sentinel.positions import Position
        from neckline.sentinel.quotes import Quote

        pos = Position(id=1, ts_code="600519.SH", buy_price=10.0, qty=100,
                       buy_date="20260720", status="open",
                       sell_price=None, sell_date=None, note=None)
        q = Quote(code="600519.SH", name="", price=9.4, pre_close=10.0, open=9.4,
                  high=9.5, low=9.4, volume=0.0, amount=0.0, ts="", source="t")
        return pos, q

    def test_default_wording_is_byte_identical(self):
        """缺省 `advisory=False`(= `v2.2-k8` 激活前)→ 文案**逐字不变**,仍指向券商条件单。
        ⚠ 这是 §2.1 前置提示「激活前本节其余全文一字有效」的落点。"""
        from neckline.sentinel.holding import check_stop_approach

        pos, q = self._pos_and_quote()
        msg = check_stop_approach(pos, q, 0.05)
        assert "已跌破止损线" in msg and "若券商条件单未成交请立即人工确认" in msg
        assert "止损警戒" not in msg

    def test_advisory_wording_says_the_decision_is_yours(self):
        from neckline.sentinel.holding import check_stop_approach

        pos, q = self._pos_and_quote()
        msg = check_stop_approach(pos, q, 0.05, advisory=True)
        assert msg.startswith("止损警戒:") and "离场决策在你" in msg
        assert "条件单未成交请立即人工确认" not in msg

    def test_threshold_and_verdict_unchanged_by_wording(self):
        """🔴 **只换口吻,不换判定**:同一价位下"触不触发"两种口径完全一致。"""
        from neckline.sentinel.holding import check_stop_approach

        pos, q = self._pos_and_quote()
        for price in (10.0, 9.81, 9.8, 9.5, 9.4, 9.0):
            q2 = q.__class__(**{**q.__dict__, "price": price})
            a = check_stop_approach(pos, q2, 0.05)
            b = check_stop_approach(pos, q2, 0.05, advisory=True)
            assert (a is None) == (b is None), price

    def test_precall_low_open_wording_follows_same_switch(self):
        from neckline.sentinel.precall import judge_position_low_open

        pos, q = self._pos_and_quote()
        assert "条件单" in judge_position_low_open(pos, q, 0.05)
        assert "离场决策在你" in judge_position_low_open(pos, q, 0.05, advisory=True)
