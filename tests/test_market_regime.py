"""V2.2-② 行情状态层:D0 盘后三态(`neckline/scan/regime.py` + `regime_store.py` +
`market_regime_daily` + `GET /market-regime`)。

覆盖(plan §五 ② 736 行逐条 + 施工指令追加两条):
· 三态各一例 + 默认态 + 缺维 + 缺行五态;high_divergence 的「A 或 (B 且 C)」读法正反例;
· **前视锁**:塞 D0 当天的验证行,`compute_market_regime_for_day` 输出逐位不变;
· **权限锁**:AST 断言 `regime.py` 对 `selection.pack` 零 import 写入口;
· `regime.py` 读 position_quota 但零写入(AST:全模块 SQL 只读);
· `sentinel/` 与 `strategy/` 全仓零判定引用;
· `inputs_json` 每维 `available` 双位齐;端点空态 200;
· 无骨架线 → 阈值回退引擎默认且 `skeleton_version='engine_default'`;
· 第 5 维零样本 → `available=false`(`clock_samples_insufficient`),⛔ 不当正确率 0。
"""

from __future__ import annotations

import ast
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import neckline
import neckline.scan.regime as regime
from neckline.scan import regime_store
from neckline.selection.pack import (
    Pack,
    _REGIME_THRESHOLD_KEYS,
    load_pack_file,
    validate_pack_doc,
)
from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture

SKELETON_FILE = Path(__file__).resolve().parent.parent / "packs" / "K8-skeleton.json"


# ══════════════════════════════════════════════════════════════════════════
# 1. decide_regime 纯函数(三态各一例 + 默认态 + 缺维 + 优先级 + EPS 边界)
# ══════════════════════════════════════════════════════════════════════════

def _decide(**kw):
    base = dict(
        old_mainline_ret_5d=None, new_direction_ret_5d=None,
        new_direction_strength_days_3d=None, new_direction_rank_gain=None,
        core_drop_from_avg5=None, breadth_pctile=None, limit_up_drop_ratio=None,
    )
    base.update(kw)
    return regime.decide_regime(**base)


def test_rotation_confirmed_positive_example():
    r, reason = _decide(
        old_mainline_ret_5d=-0.01, new_direction_ret_5d=0.05,
        new_direction_strength_days_3d=2, new_direction_rank_gain=12,
    )
    assert r == regime.ROTATION_CONFIRMED
    assert "rot.old_5d=-0.0100<0:ok" in reason
    assert "rot.gap=0.0600>0.0300:ok" in reason
    assert "rot.strength_3d=2>=2:ok" in reason
    assert "rot.rank_gain=12.0000>=10.0000:ok" in reason


def test_high_divergence_via_core_drop_alone():
    """A 支:核心强度较 5 日均值下降 ≥ 0.30 —— 单独成立(B/C 全缺)。"""
    r, reason = _decide(core_drop_from_avg5=0.35)
    assert r == regime.HIGH_DIVERGENCE
    assert "div.core_drop=0.3500>=0.3000:hit" in reason


def test_high_divergence_via_breadth_and_limit_drop():
    """B∧C 支:广度分位低 + 涨停家数环比大降,核心强度未降也成立。"""
    r, _ = _decide(core_drop_from_avg5=0.05, breadth_pctile=0.20, limit_up_drop_ratio=0.40)
    assert r == regime.HIGH_DIVERGENCE


def test_bracket_reading_b_without_c_is_not_divergence():
    """「A 或 (B 且 C)」读法的反例:仅 B(广度低)不足以判分歧。"""
    r, _ = _decide(core_drop_from_avg5=0.05, breadth_pctile=0.20, limit_up_drop_ratio=0.10)
    assert r == regime.TREND_CONTINUATION


def test_bracket_reading_c_without_b_is_not_divergence():
    r, _ = _decide(core_drop_from_avg5=0.05, breadth_pctile=0.80, limit_up_drop_ratio=0.90)
    assert r == regime.TREND_CONTINUATION


def test_default_is_continuation_when_everything_is_healthy():
    r, _ = _decide(
        old_mainline_ret_5d=0.02, new_direction_ret_5d=0.03,
        new_direction_strength_days_3d=1, new_direction_rank_gain=2,
        core_drop_from_avg5=0.05, breadth_pctile=0.80, limit_up_drop_ratio=0.0,
    )
    assert r == regime.TREND_CONTINUATION


def test_missing_inputs_default_to_continuation_not_a_guess():
    """全缺 → 默认态「延续」(不是「不知道」——那是缺行的形态),原因码逐门 na。"""
    r, reason = _decide()
    assert r == regime.TREND_CONTINUATION
    for token in ("rot.old_5d=na", "rot.gap=na", "rot.strength_3d=na",
                  "rot.rank_gain=na", "div.core_drop=na", "div.breadth_pctile=na",
                  "div.limit_drop=na"):
        assert token in reason


def test_missing_dim_is_not_treated_as_zero():
    """⛔ 缺维不当 0 参与比较:涨停环比大降但广度分位缺数 → B∧C 不成立;
    若把缺的广度当 0(0 ≤ 0.35)就会误判分歧——此例锁死方向。"""
    r, _ = _decide(limit_up_drop_ratio=0.90, breadth_pctile=None)
    assert r == regime.TREND_CONTINUATION


def test_rotation_needs_all_four_gates_missing_rank_gain_blocks_it():
    r, reason = _decide(
        old_mainline_ret_5d=-0.01, new_direction_ret_5d=0.05,
        new_direction_strength_days_3d=3, new_direction_rank_gain=None,
        missing_dims=(regime.DIM_MONEYFLOW,),
    )
    assert r == regime.TREND_CONTINUATION
    assert "rot.rank_gain=na" in reason
    assert f"missing:{regime.DIM_MONEYFLOW}" in reason


def test_priority_rotation_wins_over_divergence():
    """顺序即优先级:切换与分歧同时满足 → 判切换。"""
    r, _ = _decide(
        old_mainline_ret_5d=-0.01, new_direction_ret_5d=0.05,
        new_direction_strength_days_3d=2, new_direction_rank_gain=15,
        core_drop_from_avg5=0.50,
    )
    assert r == regime.ROTATION_CONFIRMED


def test_eps_tolerance_boundary_equality_counts_as_crossing():
    """`_EPS` 容差:gap 恰为 0.03、rank_gain 恰为 10 → 判满足(docstring 登记的取舍)。"""
    r, _ = _decide(
        old_mainline_ret_5d=-0.01, new_direction_ret_5d=0.02,
        new_direction_strength_days_3d=2, new_direction_rank_gain=10,
    )
    assert r == regime.ROTATION_CONFIRMED


def test_custom_thresholds_from_pack_are_honored():
    r, _ = _decide(
        old_mainline_ret_5d=-0.01, new_direction_ret_5d=0.05,
        new_direction_strength_days_3d=2, new_direction_rank_gain=12,
        thresholds={"rot_gap": 0.10},   # 抬高 gap 门槛 → 0.06 不够
    )
    assert r == regime.TREND_CONTINUATION


def test_extra_reason_tokens_are_appended():
    _, reason = _decide(extra_reason_tokens=("missing:skeleton_pack",))
    assert reason.endswith("missing:skeleton_pack")


# ══════════════════════════════════════════════════════════════════════════
# 2. 阈值解析(骨架包 → 引擎默认逐键回退;两处键名白名单对拍)
# ══════════════════════════════════════════════════════════════════════════

def _mk_pack(config) -> Pack:
    return Pack(
        pack_version="K8-V0.5", name="t", engine_api_version=2,
        manifest={"line_code": "V"}, config=config, evidence_ref=[],
        is_active=True, created_at="", activated_at=None, line_code="V",
    )


def test_no_skeleton_pack_falls_back_to_engine_defaults_with_sentinel_version():
    """🔴 批 1 硬前提:骨架线未激活(`get_active_skeleton()` 返 None)时阈值回退
    引擎默认,`skeleton_version` 落哨兵串(⛔ 不是 NULL、不是伪造版本号),
    并带 `missing:skeleton_pack` 原因码。"""
    th, ver, extra = regime.resolve_regime_thresholds(None)
    assert th == regime.REGIME_THRESHOLD_DEFAULTS
    assert ver == regime.SKELETON_VERSION_FALLBACK == "engine_default"
    assert extra == ("missing:skeleton_pack",)


def test_pack_thresholds_override_defaults():
    pack = _mk_pack({"regime": {"rot_gap": {"value": 0.05, "provenance": {
        "source": "engineering_v1", "basis": "b", "calibration": "pending"}}}})
    th, ver, extra = regime.resolve_regime_thresholds(pack)
    assert th["rot_gap"] == 0.05
    assert th["rot_rank"] == regime.REGIME_THRESHOLD_DEFAULTS["rot_rank"]  # 缺键逐键回退
    assert ver == "K8-V0.5"
    assert extra == ()


def test_malformed_leaf_falls_back_per_key_not_silently_crashes(caplog):
    pack = _mk_pack({"regime": {"rot_gap": 0.05}})   # 不是 {value, provenance} 叶子
    with caplog.at_level("WARNING"):
        th, _, _ = regime.resolve_regime_thresholds(pack)
    assert th["rot_gap"] == regime.REGIME_THRESHOLD_DEFAULTS["rot_gap"]
    assert any("回退" in r.message for r in caplog.records)


def test_threshold_key_whitelists_are_in_lockstep():
    """`pack.py::_REGIME_THRESHOLD_KEYS`(闸 1 白名单)与
    `regime.py::REGIME_THRESHOLD_DEFAULTS`(引擎默认)必须逐键相等——两处漂移 =
    包里配的键闸能过但引擎读不到(静默回退),正是 ②-D 点名的陷阱。"""
    assert set(_REGIME_THRESHOLD_KEYS) == set(regime.REGIME_THRESHOLD_DEFAULTS)


def test_skeleton_file_regime_values_match_plan_numbers():
    """`packs/K8-skeleton.json` 的 config.regime 首版数值必须与 plan §五 ② 714–718
    行的引擎默认逐位一致(工程首版 = 翻译,不是另一套数)。"""
    doc = load_pack_file(SKELETON_FILE)
    section = doc["config"]["regime"]
    assert set(section) == set(regime.REGIME_THRESHOLD_DEFAULTS)
    for key, default in regime.REGIME_THRESHOLD_DEFAULTS.items():
        assert float(section[key]["value"]) == float(default)
        prov = section[key]["provenance"]
        assert prov["source"] == "engineering_v1"
        assert prov["calibration"] == "pending"
        assert prov["basis"].strip()


# ══════════════════════════════════════════════════════════════════════════
# 3. 包 schema 校验(config.regime 段:白名单 + provenance 闸)
# ══════════════════════════════════════════════════════════════════════════

def _skeleton_doc():
    return load_pack_file(SKELETON_FILE)


def test_skeleton_pack_file_passes_gate1():
    assert validate_pack_doc(_skeleton_doc()) == []


def test_unknown_regime_key_is_rejected_at_gate1_not_silently_ignored():
    """阈值键写错(typo)必须在闸 1 当场拒 —— 否则运行期静默回退默认值,看不出来。"""
    doc = _skeleton_doc()
    doc["config"]["regime"]["rot_gapp"] = doc["config"]["regime"].pop("rot_gap")
    errors = validate_pack_doc(doc)
    assert any("rot_gapp" in e and "白名单" in e for e in errors)


def test_regime_leaf_missing_provenance_is_rejected():
    doc = _skeleton_doc()
    doc["config"]["regime"]["rot_gap"] = {"value": 0.03}
    errors = validate_pack_doc(doc)
    assert any("config.regime.rot_gap" in e for e in errors)


def test_regime_leaf_non_numeric_value_is_rejected():
    doc = _skeleton_doc()
    doc["config"]["regime"]["rot_gap"]["value"] = True
    errors = validate_pack_doc(doc)
    assert any("必须是数值" in e for e in errors)


def test_regime_section_is_optional_absence_is_fine():
    doc = _skeleton_doc()
    del doc["config"]["regime"]
    assert validate_pack_doc(doc) == []


# ══════════════════════════════════════════════════════════════════════════
# 4. 当日组装(isolated_env):冷启动缺维五态 / 前视锁 / 落表读回
# ══════════════════════════════════════════════════════════════════════════

_STRENGTH_SQL = (
    "INSERT OR REPLACE INTO industry_strength_daily "
    "(trade_date, industry, median_ret, member_count, industry_rank, is_strength_day, "
    "persist_days, quantile, min_members, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
)


def _seed_strength_rows(env, rows):
    """rows = [(day, industry, median_ret, is_strength_day)],直接落
    `industry_strength_daily`(regime 的读取不校验口径指纹,照 `stage.py::
    _strength_today_rows` 直接读的先例;quantile/min_members 仍写现行常量)。"""
    from neckline.report.industry_strength import _MIN_MEMBERS, _STRENGTH_QUANTILE

    conn = sqlite3.connect(str(env.db_path))
    try:
        for day, industry, ret, is_str in rows:
            conn.execute(_STRENGTH_SQL, (
                day.strftime("%Y%m%d"), industry, ret, 10, 1,
                1 if is_str else 0, 1, _STRENGTH_QUANTILE, _MIN_MEMBERS, "t",
            ))
        conn.commit()
    finally:
        conn.close()


def _insert_basket(env, trade_date: date, *, key="k1", tier=1) -> int:
    conn = sqlite3.connect(str(env.db_path))
    try:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier, "
            "pack_version, engine_api_version, charter_version, via, evidence_status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_date.strftime("%Y%m%d"), key, "篮", "驱动", "theme", tier,
             "K8-V0.5", 2, "v1.3.3", "auto", "ok", "t"),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _insert_verification(env, basket_id: int, trade_date: date, state: str, *, source="eod"):
    conn = sqlite3.connect(str(env.db_path))
    try:
        conn.execute(
            "INSERT INTO basket_verification (basket_id, trade_date, observed_at, state, "
            "source, evidence_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (basket_id, trade_date.strftime("%Y%m%d"), "t", state, source, "{}", "t"),
        )
        conn.commit()
    finally:
        conn.close()


class TestColdStartComputation:
    def test_empty_env_yields_continuation_with_all_dims_honestly_missing(self, isolated_env):
        env = isolated_env
        days = business_days(date(2026, 3, 2), 5)
        insert_trade_cal(env, days)
        res = regime.compute_market_regime_for_day(
            days[-1], db_path=env.db_path, parquet_dir=env.parquet_dir)
        # regime 恒非 NULL,默认态是「延续」不是「不知道」
        assert res.regime == regime.TREND_CONTINUATION
        # 无骨架线:阈值回退 + 哨兵串 + 原因码(⑦ 批 1 判据的硬前提)
        assert res.skeleton_version == "engine_default"
        assert "missing:skeleton_pack" in res.regime_reason
        # inputs_json 每维 available 双位齐(五维 + position_quota)
        assert set(regime.DIM_ORDER) <= set(res.inputs)
        for dim in regime.DIM_ORDER:
            entry = res.inputs[dim]
            assert "available" in entry and "unavailable_reason" in entry
            assert entry["available"] is False
            assert f"missing:{dim}" in res.regime_reason
        assert "position_quota" in res.inputs
        assert "available" in res.inputs["position_quota"]

    def test_dim5_zero_samples_is_unavailable_not_accuracy_zero(self, isolated_env):
        """§七 P3-51:冷启动第 5 维恒缺席 —— `available=false` +
        `clock_samples_insufficient`,⛔ 不当「正确率 0」。"""
        env = isolated_env
        days = business_days(date(2026, 3, 2), 5)
        insert_trade_cal(env, days)
        res = regime.compute_market_regime_for_day(
            days[-1], db_path=env.db_path, parquet_dir=env.parquet_dir)
        acc = res.inputs[regime.DIM_ACCURACY]
        assert acc["available"] is False
        assert acc["unavailable_reason"] == "clock_samples_insufficient"
        assert acc["accuracy"] is None          # 不是 0
        assert acc["source"] == "basket_verification_fallback"
        assert f"missing:{regime.DIM_ACCURACY}" in res.regime_reason


class TestLookaheadLock:
    """前视锁(plan §五 ② 两道锁之 1):第 5 维只吃结案日 ≤ D0−1 的样本。"""

    def _base_env(self, env):
        days = business_days(date(2026, 3, 2), 20)
        insert_trade_cal(env, days)
        d0 = days[-1]
        basket_id = _insert_basket(env, days[-3])
        _insert_verification(env, basket_id, days[-2], "verified")   # 窗口内(D0−1)
        return days, d0, basket_id

    def test_d0_dated_verification_row_does_not_change_the_output_bit_for_bit(
        self, isolated_env
    ):
        env = isolated_env
        days, d0, basket_id = self._base_env(env)
        before = regime.compute_market_regime_for_day(
            d0, db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert before.inputs[regime.DIM_ACCURACY]["samples"] == 1

        _insert_verification(env, basket_id, d0, "falsified")        # D0 当天:必须读不进
        after = regime.compute_market_regime_for_day(
            d0, db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert after == before                                       # 逐位不变

    def test_non_eod_and_t3_rows_do_not_count(self, isolated_env):
        env = isolated_env
        days, d0, basket_id = self._base_env(env)
        _insert_verification(env, basket_id, days[-2], "falsified", source="intraday")
        t3 = _insert_basket(env, days[-3], key="k3", tier=3)
        _insert_verification(env, t3, days[-2], "falsified")
        res = regime.compute_market_regime_for_day(
            d0, db_path=env.db_path, parquet_dir=env.parquet_dir)
        acc = res.inputs[regime.DIM_ACCURACY]
        assert acc["samples"] == 1 and acc["verified"] == 1 and acc["falsified"] == 0

    def test_accuracy_counts_latest_eod_row_per_basket_day(self, isolated_env):
        """append-only 流水:同 (basket, 日) 多行取 id 最大的一行(读侧既有约定)。"""
        env = isolated_env
        days, d0, basket_id = self._base_env(env)
        _insert_verification(env, basket_id, days[-2], "falsified")   # 后写覆盖前判
        res = regime.compute_market_regime_for_day(
            d0, db_path=env.db_path, parquet_dir=env.parquet_dir)
        acc = res.inputs[regime.DIM_ACCURACY]
        assert acc["samples"] == 1 and acc["falsified"] == 1 and acc["verified"] == 0


class TestEndToEndScenarios:
    def test_rotation_confirmed_end_to_end(self, isolated_env):
        """切换确认端到端:旧主线 5 日走弱、新方向近 3 日 2 个强度日 + 5 日强 +
        资金迁移排名大幅上升(12 名 ≥ 10)。"""
        env = isolated_env
        days = business_days(date(2026, 2, 2), 16)
        insert_trade_cal(env, days)
        d0 = days[-1]

        rows = []
        for i, day in enumerate(days):
            in_ret_window = day in days[-5:]
            # 旧主:days[-11:-6] 五个强度日(全部落在 10 日主线窗口内);近 5 日逐日 -0.01
            rows.append((day, "旧主", -0.01 if in_ret_window else 0.0,
                         day in days[-11:-6]))
            # 新方:D0 与 D0−1 强度日(近 3 日 2 天);近 5 日逐日 +0.02
            rows.append((day, "新方", 0.02 if in_ret_window else 0.0,
                         day in days[-2:]))
            # 填充行业(资金迁移排名需要 ≥11 名可挪),无强度日
            for j in range(13):
                rows.append((day, f"填{j:02d}", 0.0, False))
        _seed_strength_rows(env, rows)

        # 资金迁移:对比日(D0 往前第 5 个交易日)新方净额垫底,D0 冲到第 1 → 上升 14-1=13 名
        codes = {"旧主": "600001.SH", "新方": "600002.SH"}
        codes.update({f"填{j:02d}": f"6001{j:02d}.SH" for j in range(13)})
        insert_stock_basic(env, [
            {"ts_code": c, "industry": ind} for ind, c in codes.items()
        ])
        compare_day = days[-6]
        write_daily_fixture(env, "moneyflow_dc", compare_day, [
            {"ts_code": c, "net_amount": (-100.0 if ind == "新方" else 100.0 + i)}
            for i, (ind, c) in enumerate(codes.items())
        ])
        write_daily_fixture(env, "moneyflow_dc", d0, [
            {"ts_code": c, "net_amount": (9999.0 if ind == "新方" else 100.0 + i)}
            for i, (ind, c) in enumerate(codes.items())
        ])

        res = regime.compute_market_regime_for_day(
            d0, db_path=env.db_path, parquet_dir=env.parquet_dir)
        rel = res.inputs[regime.DIM_RELATIVE_STRENGTH]
        assert rel["old_mainline"] == "旧主" and rel["new_direction"] == "新方"
        assert rel["new_strength_days_3d"] == 2
        mf = res.inputs[regime.DIM_MONEYFLOW]
        assert mf["available"] is True and mf["rank_gain"] >= 10
        assert res.regime == regime.ROTATION_CONFIRMED
        # 第 5 维缺席不阻断(P3-51:少一维是证据薄,不是判不了)
        assert f"missing:{regime.DIM_ACCURACY}" in res.regime_reason

    def test_high_divergence_end_to_end_via_breadth_and_limit_drop(self, isolated_env):
        env = isolated_env
        days = business_days(date(2026, 1, 5), 25)
        insert_trade_cal(env, days)
        d0 = days[-1]
        rows = []
        for day in days:
            is_last = day == d0
            rows.append((day, "甲", 0.0, not is_last))
            rows.append((day, "乙", 0.0, not is_last))
        _seed_strength_rows(env, rows)

        def _limit_row(code, up):
            return {
                "ts_code": code, "board": "MAIN",
                "status": "limit_up" if up else None,
                "limit_pct": 0.10, "limit_up_price": 11.0, "limit_down_price": 9.0,
                "is_limit_up": up, "is_limit_down": False, "is_zaban": False,
                "consec_limit_up_days": 1 if up else 0,
            }

        write_daily_fixture(env, "limit_derived", days[-2],
                            [_limit_row(f"60000{i}.SH", True) for i in range(10)])
        write_daily_fixture(env, "limit_derived", d0,
                            [_limit_row("600000.SH", True), _limit_row("600001.SH", True),
                             _limit_row("600002.SH", False)])

        res = regime.compute_market_regime_for_day(
            d0, db_path=env.db_path, parquet_dir=env.parquet_dir)
        breadth = res.inputs[regime.DIM_BREADTH]
        assert breadth["available"] is True
        assert breadth["pctile"] is not None and breadth["pctile"] <= 0.35
        assert breadth["limit_up_drop_ratio"] == pytest.approx(0.8)
        assert res.regime == regime.HIGH_DIVERGENCE

    def test_breadth_pctile_needs_min_window_days(self, isolated_env):
        """分位窗口有数天数 < 20 → `pctile=None`(不猜),B 支 na。"""
        env = isolated_env
        days = business_days(date(2026, 3, 2), 6)
        insert_trade_cal(env, days)
        _seed_strength_rows(env, [(d, "甲", 0.0, False) for d in days])
        res = regime.compute_market_regime_for_day(
            days[-1], db_path=env.db_path, parquet_dir=env.parquet_dir)
        breadth = res.inputs[regime.DIM_BREADTH]
        assert breadth["available"] is True and breadth["pctile"] is None
        assert res.regime == regime.TREND_CONTINUATION


class TestStoreRoundtrip:
    def test_refresh_lands_a_row_and_load_reads_it_back(self, isolated_env):
        env = isolated_env
        days = business_days(date(2026, 3, 2), 5)
        insert_trade_cal(env, days)
        d0 = days[-1]
        stats = regime_store.refresh_market_regime(
            [d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert stats == {"days": 1, "rows": 1, "failed": 0}
        row = regime_store.load_market_regime(d0, db_path=env.db_path)
        assert row is not None
        assert row["regime"] == regime.TREND_CONTINUATION
        assert row["skeleton_version"] == "engine_default"
        for dim in regime.DIM_ORDER:
            assert "available" in row["inputs"][dim]
            assert "unavailable_reason" in row["inputs"][dim]
        assert regime_store.load_latest_market_regime(db_path=env.db_path)["trade_date"] == \
            d0.strftime("%Y%m%d")
        assert [r["trade_date"] for r in regime_store.load_market_regime_range(
            days[0], d0, db_path=env.db_path)] == [d0.strftime("%Y%m%d")]

    def test_missing_row_is_none_not_fabricated(self, isolated_env):
        """缺行五态之「缺行」:当日无行 → None(「不知道」),⛔ 不现算自愈。"""
        env = isolated_env
        assert regime_store.load_market_regime(date(2026, 3, 6), db_path=env.db_path) is None
        assert regime_store.load_latest_market_regime(db_path=env.db_path) is None
        assert regime_store.load_market_regime_range(
            date(2026, 3, 2), date(2026, 3, 6), db_path=env.db_path) == []

    def test_same_day_rerun_is_idempotent(self, isolated_env):
        env = isolated_env
        days = business_days(date(2026, 3, 2), 5)
        insert_trade_cal(env, days)
        d0 = days[-1]
        regime_store.refresh_market_regime([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        first = regime_store.load_market_regime(d0, db_path=env.db_path)
        regime_store.refresh_market_regime([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        second = regime_store.load_market_regime(d0, db_path=env.db_path)
        first.pop("computed_at"), second.pop("computed_at")
        assert first == second


# ══════════════════════════════════════════════════════════════════════════
# 5. 端点(只读、零现算、一律不 404、零新增 reason)
# ══════════════════════════════════════════════════════════════════════════

class TestEndpoint:
    def test_requires_auth(self, client):
        assert client.get("/api/v1/market-regime").status_code == 401

    def test_empty_state_is_200_available_false(self, client, AUTH):
        r = client.get("/api/v1/market-regime", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["unavailableReason"]
        assert body["day"] is None and body["days"] == []

    def test_missing_date_is_200_available_false_not_404(self, client, AUTH):
        r = client.get("/api/v1/market-regime", params={"date": "20260306"}, headers=AUTH)
        assert r.status_code == 200
        assert r.json()["available"] is False

    def test_invalid_date_degrades_not_4xx(self, client, AUTH):
        r = client.get("/api/v1/market-regime", params={"date": "oops"}, headers=AUTH)
        assert r.status_code == 200
        assert r.json()["available"] is False

    def test_with_a_landed_row_date_latest_and_range_all_serve_it(self, client, AUTH, api_env):
        env = api_env
        days = business_days(date(2026, 3, 2), 5)
        insert_trade_cal(env, days)
        d0 = days[-1]
        regime_store.refresh_market_regime([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)

        by_date = client.get("/api/v1/market-regime",
                             params={"date": d0.strftime("%Y%m%d")}, headers=AUTH).json()
        assert by_date["available"] is True
        day = by_date["day"]
        assert day["regime"] == regime.TREND_CONTINUATION
        assert day["regimeLabel"] == regime.REGIME_LABELS[regime.TREND_CONTINUATION]
        assert day["skeletonVersion"] == "engine_default"
        for dim in regime.DIM_ORDER:
            assert "available" in day["inputs"][dim]
            assert "unavailable_reason" in day["inputs"][dim]

        latest = client.get("/api/v1/market-regime", headers=AUTH).json()
        assert latest["available"] is True
        assert latest["day"]["tradeDate"] == d0.strftime("%Y%m%d")

        rng = client.get("/api/v1/market-regime",
                         params={"from": days[0].strftime("%Y%m%d"),
                                 "to": d0.strftime("%Y%m%d")}, headers=AUTH).json()
        assert rng["available"] is True
        assert [d["tradeDate"] for d in rng["days"]] == [d0.strftime("%Y%m%d")]

    def test_empty_range_is_200_available_false(self, client, AUTH):
        r = client.get("/api/v1/market-regime",
                       params={"from": "20260302", "to": "20260306"}, headers=AUTH)
        assert r.status_code == 200
        assert r.json()["available"] is False


# ══════════════════════════════════════════════════════════════════════════
# 6. 守门(AST):权限锁 / 零写入 / sentinel+strategy 零判定引用
# ══════════════════════════════════════════════════════════════════════════

_REGIME_PATH = Path(regime.__file__)


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_permission_lock_regime_imports_zero_pack_write_entries():
    """权限锁(plan §五 ② 两道锁之 2):`regime.py` 对 `neckline.selection.pack`
    **只许 import 读入口**(它必须能读 `get_active_skeleton` 取阈值 —— ⛔ 本守门
    不是「禁一切 pack import」);`activate_pack` 一类写入口零引用。"""
    tree = _module_tree(_REGIME_PATH)
    allowed_read_entries = {"Pack", "get_active_skeleton", "get_active_line", "get_active_pack"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "selection.pack" in node.module:
            names = {a.name for a in node.names}
            assert names <= allowed_read_entries, (
                f"regime.py 从 selection.pack import 了读入口之外的名字:{names - allowed_read_entries}"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "selection.pack" not in alias.name, "regime.py 不许整模块 import selection.pack"
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id != "activate_pack"
        if isinstance(node, ast.Attribute):
            assert node.attr != "activate_pack"


def test_regime_module_is_read_only_all_sql_is_select():
    """`regime.py` 读 position_quota / 五维输入但**零写入**:全模块任何
    `execute(...)` 的字面 SQL 必须以 SELECT 开头,且零 `executemany`(写在
    `regime_store.py`,方向 store → regime 单向)。"""
    tree = _module_tree(_REGIME_PATH)

    def _literal_sql(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
        return None

    saw_execute = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        assert attr != "executemany", "regime.py 不许 executemany(零写入)"
        if attr == "execute":
            saw_execute = True
            sql = _literal_sql(node.args[0]) if node.args else None
            assert sql is not None, "regime.py 的 execute 必须用字面 SQL(守门可审计)"
            assert sql.lstrip().upper().startswith("SELECT"), f"regime.py 出现非只读 SQL:{sql[:60]}"
    assert saw_execute, "预期 regime.py 至少有只读查询(守门自检:AST 扫描没扫到东西才是坏了)"


def test_regime_store_is_the_only_writer_and_regime_does_not_import_it():
    tree = _module_tree(_REGIME_PATH)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module and "regime_store" in node.module)
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "regime_store" not in alias.name


def test_sentinel_and_strategy_have_zero_regime_references():
    """plan §五 ② 731 行:持仓侧纯展示、⛔ 不触发任何持仓动作 —— `sentinel/` 与
    `strategy/` 全仓不许 import 判定模块(regime / regime_store)。"""
    pkg_root = Path(neckline.__file__).resolve().parent
    offenders = []
    for sub in ("sentinel", "strategy"):
        for py in sorted((pkg_root / sub).rglob("*.py")):
            tree = _module_tree(py)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if "regime" in mod or any("regime" in a.name for a in node.names):
                        offenders.append(f"{py.name}: from {mod} import …")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "regime" in alias.name:
                            offenders.append(f"{py.name}: import {alias.name}")
    assert not offenders, f"sentinel/strategy 出现行情状态判定引用:{offenders}"


# ══════════════════════════════════════════════════════════════════════════
# 7. 表 schema(新表进 CREATE TABLE IF NOT EXISTS;幂等由 test_v2_schema_guard
#    的全库指纹用例覆盖,这里只锁列集)
# ══════════════════════════════════════════════════════════════════════════

def test_market_regime_daily_columns_match_plan(isolated_env):
    conn = sqlite3.connect(str(isolated_env.db_path))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(market_regime_daily)")]
    finally:
        conn.close()
    assert cols == [
        "trade_date", "regime", "regime_reason", "inputs_json",
        "strengthening_json", "weakening_json", "skeleton_version", "computed_at",
    ]


def test_inputs_json_is_valid_json_with_double_keys_per_dim(isolated_env):
    env = isolated_env
    days = business_days(date(2026, 3, 2), 5)
    insert_trade_cal(env, days)
    regime_store.refresh_market_regime([days[-1]], db_path=env.db_path,
                                       parquet_dir=env.parquet_dir)
    conn = sqlite3.connect(str(env.db_path))
    try:
        raw = conn.execute(
            "SELECT inputs_json FROM market_regime_daily WHERE trade_date=?",
            (days[-1].strftime("%Y%m%d"),),
        ).fetchone()[0]
    finally:
        conn.close()
    inputs = json.loads(raw)
    for dim in regime.DIM_ORDER:
        assert "available" in inputs[dim] and "unavailable_reason" in inputs[dim]
