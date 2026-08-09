"""V2.2-③-C 落地起跳位置关(`neckline/scan/landing.py` + `landing_store.py` +
`landing_state_daily` + 骨架包 `config.landing`)。

覆盖(plan §五 ③「测试与守门」原文逐条):
· 四态 + `none` 各一例;五项判据逐项开关的真值表(含级联两处补全的锁定);
· 缺数不猜(`state_reason` 逐条说明;缺维不当 0 参与比较);
· **bulk 与 day-by-day 与读回三路等价**(比较前 `.drop("computed_at")`,P1-36 体例);
· 反向守门:`landing.py` 零 import `neckline.sentinel.*` 与
  `neckline.report.score_display`;零写库;
· 🔴 雷区对照四条在模块头逐字在场(防删也防"当新发现");
· 阈值白名单两处对拍(`pack.py::_LANDING_THRESHOLD_KEYS` ≡
  `landing.py::LANDING_THRESHOLD_DEFAULTS`);骨架包 landing 段全 engineering_v1;
· `platform_days` 住 `metrics_json`,⛔ 不另起一张表。
"""

from __future__ import annotations

import ast
import json
import sqlite3
from datetime import date
from pathlib import Path

import polars as pl
import pytest

import neckline.scan.landing as landing
from neckline.db import connection
from neckline.scan import landing_store
from neckline.selection.pack import (
    Pack,
    _LANDING_THRESHOLD_KEYS,
    load_pack_file,
    validate_pack_doc,
)
from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture

SKELETON_FILE = Path(__file__).resolve().parent.parent / "packs" / "K8-skeleton.json"
LANDING_SRC = Path(landing.__file__).read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# 1. decide_landing 纯函数:四态 + none 各一例 + 五项判据逐项开关真值表
# ══════════════════════════════════════════════════════════════════════════

_LIFTOFF_BASE = dict(
    low5_over_base=1.05, new_low_20d=False,
    close_over_support=1.02,
    sell_ratio=0.5, down_days_5d=2, max_drop_5d=-0.02,
    close_over_ma5=1.01, ret_1d=0.012, rs5=0.02,
    dist_from_high_60d=-0.08, lift_ret=0.05, is_limit_up=False, new_high_60d=False,
)


def _decide(**overrides):
    kw = dict(_LIFTOFF_BASE)
    kw.update(overrides)
    return landing.decide_landing(**kw)


def test_liftoff_confirmed_all_five_pass():
    state, reason = _decide()
    assert state == landing.LIFTOFF_CONFIRMED
    assert reason.startswith("c1:ok;c2:ok;c3:ok;c4:ok;c5:ok")


def test_falling_when_c1_range_fails():
    state, reason = _decide(low5_over_base=1.005)   # ≤ 1+low_tol=1.01
    assert state == landing.FALLING
    assert "c1.low5_over_base=1.0050>1.0100:fail" in reason


def test_falling_when_c1_new_low_today():
    state, reason = _decide(new_low_20d=True)
    assert state == landing.FALLING
    assert "c1.new_low_20d=yes:fail" in reason


def test_landing_pending_when_c4_not_yet_turning_up():
    """四态映射原文:1+2 成立、4 不成立 → 落地待确认(判据 3 不问,na 也不拦)。"""
    state, reason = _decide(close_over_ma5=0.98, sell_ratio=None, down_days_5d=None,
                            max_drop_5d=None)
    assert state == landing.LANDING_PENDING
    assert "c4.close_over_ma5=0.9800>1:fail" in reason
    assert "c3:na" in reason   # 缺数如实标 na,但级联走不到它,不影响判定


def test_high_extended_when_c5_fails():
    state, reason = _decide(lift_ret=0.20)
    assert state == landing.HIGH_EXTENDED
    assert "c5.lift_ret=0.2000<=0.1200:fail" in reason


def test_high_extended_when_limit_up_today():
    """K8 §九 排除项第 2 条:当日涨停 → 不算启动早期(⛔ 不进 T1)。"""
    state, _ = _decide(is_limit_up=True)
    assert state == landing.HIGH_EXTENDED


def test_high_extended_when_new_high_60d():
    state, _ = _decide(new_high_60d=True)
    assert state == landing.HIGH_EXTENDED


def test_high_extended_when_pullback_too_shallow():
    state, _ = _decide(dist_from_high_60d=-0.01)   # 距 60 日高点不足 3%
    assert state == landing.HIGH_EXTENDED


def test_completion_ruling_1_no_support_goes_to_falling():
    """级联补全①(模块头登记):判据 1 成立、判据 2 不成立 → falling(未获支撑 =
    未落地,归排除桶),⛔ 不是 landing_pending。"""
    state, reason = _decide(close_over_support=0.95)
    assert state == landing.FALLING
    assert "c2.close_over_support=0.9500>=0.9900:fail" in reason


def test_completion_ruling_2_sell_pressure_not_decayed_goes_to_pending():
    """级联补全②(模块头登记):1+2+4 成立、3 不成立 → landing_pending(转强但
    抛压未衰减 = 起跳未确认),⛔ 不是 liftoff_confirmed 也不是 high_extended。"""
    state, reason = _decide(sell_ratio=1.2)
    assert state == landing.LANDING_PENDING
    assert "c3.sell_ratio=1.2000<=0.9000:fail" in reason


def test_panic_drop_in_recent_5d_blocks_c3():
    state, reason = _decide(max_drop_5d=-0.08)
    assert state == landing.LANDING_PENDING
    assert "c3.max_drop_5d=-0.0800>=-0.0500:fail" in reason


def test_no_down_days_counts_as_sell_pressure_decayed_not_na():
    """近 5 日无下跌日 → 抛压衰减子门按成立读(no_down_days),与「数据缺失」
    (na)分开——两者原因码不同。"""
    state, reason = _decide(sell_ratio=None, down_days_5d=0)
    assert state == landing.LIFTOFF_CONFIRMED
    assert "c3.sell_ratio=no_down_days:ok" in reason


def test_none_when_c1_undecidable():
    state, reason = _decide(low5_over_base=None, new_low_20d=None)
    assert state == landing.NONE_STATE
    assert "c1.low5_over_base=na" in reason and "c1.new_low_20d=na" in reason


def test_none_when_c2_missing():
    state, reason = _decide(close_over_support=None)
    assert state == landing.NONE_STATE
    assert "c2.close_over_support=na" in reason


def test_none_when_c4_rs5_missing_and_other_subgates_pass():
    """缺数不猜:close>MA5 与 close>昨收 都成立、RS5 缺数 → 判据 4 判不动 → none;
    ⛔ 不把缺的 RS5 当 0(当 0 会把 >0 判 fail → 误判 landing_pending)。"""
    state, reason = _decide(rs5=None)
    assert state == landing.NONE_STATE
    assert "c4.rs5=na" in reason


def test_c4_decidable_fail_beats_missing_subgate():
    """三值 AND:某子门已可判 fail(close ≤ MA5)时,其它子门缺数不改变结论
    (fail 优先于 na)→ landing_pending 而不是 none。"""
    state, _ = _decide(close_over_ma5=0.97, rs5=None)
    assert state == landing.LANDING_PENDING


def test_none_when_c5_limit_up_unknown():
    state, reason = _decide(is_limit_up=None)
    assert state == landing.NONE_STATE
    assert "c5.limit_up=na" in reason


def test_all_missing_is_none_with_reason_per_criterion():
    state, reason = landing.decide_landing(
        low5_over_base=None, new_low_20d=None, close_over_support=None,
        sell_ratio=None, down_days_5d=None, max_drop_5d=None,
        close_over_ma5=None, ret_1d=None, rs5=None,
        dist_from_high_60d=None, lift_ret=None, is_limit_up=None, new_high_60d=None,
    )
    assert state == landing.NONE_STATE
    assert reason.startswith("c1:na;c2:na;c3:na;c4:na;c5:na")
    for token in ("c1.low5_over_base=na", "c2.close_over_support=na", "c3.sell_ratio=na",
                  "c4.close_over_ma5=na", "c5.dist_high_60d=na"):
        assert token in reason


def test_eps_boundary_equality_counts_as_satisfied():
    """`_EPS` 容差:low5_over_base 恰为 1+low_tol、lift 恰为 lift_max → 按满足读
    (regime.py 同款登记取舍)。"""
    state, _ = _decide(low5_over_base=1.01, lift_ret=0.12)
    assert state == landing.LIFTOFF_CONFIRMED


def test_custom_thresholds_from_pack_are_honored():
    state, _ = _decide(thresholds={"lift_max": 0.03})   # 收紧涨幅上限 → 0.05 超了
    assert state == landing.HIGH_EXTENDED


def test_extra_reason_tokens_are_appended():
    _, reason = _decide(extra_reason_tokens=("missing:skeleton_pack",))
    assert reason.endswith("missing:skeleton_pack")


# ══════════════════════════════════════════════════════════════════════════
# 2. 阈值解析 + 白名单对拍 + 骨架包 landing 段
# ══════════════════════════════════════════════════════════════════════════

def _mk_pack(config) -> Pack:
    return Pack(
        pack_version="K8-V0.5", name="t", engine_api_version=2,
        manifest={"line_code": "V"}, config=config, evidence_ref=[],
        is_active=True, created_at="", activated_at=None, line_code="V",
    )


def test_no_skeleton_pack_falls_back_to_engine_defaults_with_sentinel_version():
    th, ver, extra = landing.resolve_landing_thresholds(None)
    assert th == landing.LANDING_THRESHOLD_DEFAULTS
    assert ver == landing.SKELETON_VERSION_FALLBACK == "engine_default"
    assert extra == ("missing:skeleton_pack",)


def test_pack_thresholds_override_defaults_per_key():
    pack = _mk_pack({"landing": {"lift_max": {"value": 0.10, "provenance": {
        "source": "engineering_v1", "basis": "b", "calibration": "pending"}}}})
    th, ver, extra = landing.resolve_landing_thresholds(pack)
    assert th["lift_max"] == 0.10
    assert th["n_low"] == landing.LANDING_THRESHOLD_DEFAULTS["n_low"]   # 缺键逐键回退
    assert ver == "K8-V0.5"
    assert extra == ()


def test_malformed_leaf_falls_back_per_key_with_warning(caplog):
    pack = _mk_pack({"landing": {"lift_max": 0.10}})   # 不是 {value, provenance} 叶子
    with caplog.at_level("WARNING"):
        th, _, _ = landing.resolve_landing_thresholds(pack)
    assert th["lift_max"] == landing.LANDING_THRESHOLD_DEFAULTS["lift_max"]
    assert any("回退" in r.message for r in caplog.records)


def test_threshold_key_whitelists_are_in_lockstep():
    """`pack.py::_LANDING_THRESHOLD_KEYS`(闸 1 白名单)与
    `landing.py::LANDING_THRESHOLD_DEFAULTS`(引擎默认)逐键相等——两处漂移 =
    包里配的键闸能过但引擎读不到(静默回退),②-D 同款陷阱。"""
    assert set(_LANDING_THRESHOLD_KEYS) == set(landing.LANDING_THRESHOLD_DEFAULTS)


def test_skeleton_file_landing_values_match_engine_defaults_all_engineering_v1():
    """骨架包 config.landing 首版数值与引擎默认逐位一致;五项判据全部
    `engineering_v1` + 非空 basis + calibration=pending(plan §五 ③-C:全是
    工程首版,⛔ 不冒充审计结论)。"""
    doc = load_pack_file(SKELETON_FILE)
    section = doc["config"]["landing"]
    assert set(section) == set(landing.LANDING_THRESHOLD_DEFAULTS)
    for key, default in landing.LANDING_THRESHOLD_DEFAULTS.items():
        assert float(section[key]["value"]) == float(default)
        prov = section[key]["provenance"]
        assert prov["source"] == "engineering_v1"
        assert prov["calibration"] == "pending"
        assert prov["basis"].strip()


def test_skeleton_pack_file_passes_gate1():
    assert validate_pack_doc(load_pack_file(SKELETON_FILE)) == []


def test_unknown_landing_key_is_rejected_at_gate1_not_silently_ignored():
    doc = load_pack_file(SKELETON_FILE)
    doc["config"]["landing"]["lift_maxx"] = doc["config"]["landing"].pop("lift_max")
    errors = validate_pack_doc(doc)
    assert any("lift_maxx" in e and "白名单" in e for e in errors)


def test_landing_leaf_missing_provenance_is_rejected():
    doc = load_pack_file(SKELETON_FILE)
    doc["config"]["landing"]["lift_max"] = {"value": 0.12}
    errors = validate_pack_doc(doc)
    assert any("config.landing.lift_max" in e for e in errors)


def test_landing_leaf_non_numeric_value_is_rejected():
    doc = load_pack_file(SKELETON_FILE)
    doc["config"]["landing"]["lift_max"]["value"] = True
    errors = validate_pack_doc(doc)
    assert any("必须是数值" in e for e in errors)


def test_landing_section_is_optional_absence_is_fine():
    doc = load_pack_file(SKELETON_FILE)
    del doc["config"]["landing"]
    assert validate_pack_doc(doc) == []


def test_skeleton_existing_three_sections_untouched_by_this_block():
    """③-C 只加 `landing` 段:seeds/tier/regime 三段的键集合与批 1 上产时一致
    (⛔ 不许顺手动已激活包的既有内容)。"""
    doc = load_pack_file(SKELETON_FILE)
    assert set(doc["config"]) == {"seeds", "tier", "regime", "landing"}
    assert doc["manifest"]["pack_version"] == "K8-V0.5"


# ══════════════════════════════════════════════════════════════════════════
# 3. 守门:雷区对照逐字在场 / 反向 import / 零写库 / 展示文案红线
# ══════════════════════════════════════════════════════════════════════════

def test_minefield_notes_are_verbatim_in_module_header():
    """🔴 plan §五 ③-C「雷区对照」四条必须原样在 `landing.py` 模块头(一字不省)
    ——同时防「后人当新发现」与「后人当禁令删掉」(§七 P3-49-(a)/(e))。"""
    for phrase in (
        "K3-B2 臂③「升势回撤 + 启动确认」",
        "确认信号 = 死猫跳顶点,比直接买更差",
        "research/k3_report.md",
        "K3 系统化超跌反弹四臂全灭",
        "K2「追强势」全否决 + K7-C1 诱多做局",
        "站在案底同侧",
        "只产注意力分层",
        "不得被读成买入期望背书",
        "选股时钟",
        "P3-49",
    ):
        assert phrase in LANDING_SRC, f"雷区对照缺字:{phrase!r}"


def test_landing_module_never_imports_sentinel_or_score_display():
    """反向守门(plan §五 ③ 原文):位置态 ⛔ 不接持仓动作、不进推送、不碰展示
    标度 —— 靠「没有那条 import」结构性担保,不靠自觉。"""
    tree = ast.parse(LANDING_SRC)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not name.startswith("neckline.sentinel"), f"landing.py 禁止 import {name}"
            assert "score_display" not in name, f"landing.py 禁止 import {name}"


def test_landing_module_is_read_only_no_write_sql():
    """`landing.py` 零写库(写只发生在 `landing_store.py`)——源码里不许出现
    INSERT/UPDATE/DELETE/executemany(regime.py 同款分工)。"""
    for banned in ("INSERT ", "UPDATE ", "DELETE ", "executemany"):
        assert banned not in LANDING_SRC, f"landing.py 出现写库痕迹:{banned!r}"


def test_state_labels_carry_no_expectation_wording():
    """§七 P3-49-(b):位置关产出不得出现在任何声称「期望/胜率/会涨/买入」的文案里
    ——四态中文标签(唯一展示映射源)锁死不含这些字眼。"""
    for label in landing.STATE_LABELS.values():
        for banned in ("买入", "期望", "胜率", "会涨"):
            assert banned not in label


# ══════════════════════════════════════════════════════════════════════════
# 4. 全市场批算(isolated_env):四态实算 + 缺数 + 三路等价 + 落表读回
# ══════════════════════════════════════════════════════════════════════════

_IND = "食品加工"


_CODES = ("600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH")


def _seed_market(env):
    """70 个交易日 × 5 只票的合成盘(判定日一天打出四态 + none 各一例):
      600001.SH 平台(40 日)→ 回落(15 日)→ 企稳(10 日)→ 温和转强(5 日)
                → liftoff_confirmed;
      600002.SH 单边下行、持续创新低 → falling(判据 1);
      600003.SH 只有最后 10 日有行 → 窗口不足 → none(缺数不猜);
      600004.SH 同甲形态但企稳后近端阴跌(未转强)→ landing_pending;
      600005.SH 同甲形态但近端连续急拉、创 60 日新高 → high_extended(判据 5)。
    返回 (days, 判定日)。"""
    days = business_days(date(2026, 1, 5), 70)
    insert_trade_cal(env, days)
    insert_stock_basic(env, [
        {"ts_code": code, "name": f"股{i}", "industry": _IND}
        for i, code in enumerate(_CODES)
    ])

    def _row(code, close, prev, low, high, amount=100000.0):
        return {
            "ts_code": code, "open": prev, "high": high, "low": low,
            "close": close, "pre_close": prev, "amount": amount,
        }

    def _shaped(tail5):
        """平台 40 日 → 回落 15 日(低点 8.85)→ 企稳 10 日 → 给定的近端 5 日。"""
        closes = [10.0] * 40
        closes += [round(10.0 - (1.0 / 15) * (i + 1), 4) for i in range(15)]   # → 9.0
        closes += [9.10] * 10
        closes += list(tail5)
        lows, highs = [], []
        for i, c in enumerate(closes):
            if 40 <= i <= 54:
                lows.append(round(c - 0.15, 4))    # 回落段低点(区间最低 8.85)
            elif i >= 65:
                lows.append(round(c - 0.07, 4))    # 近端低点抬高(> 8.85×1.01)
            else:
                lows.append(round(c - 0.10, 4))
            highs.append(round(c + 0.05, 4))
        return closes, lows, highs

    a_close, a_low, a_high = _shaped([9.12, 9.09, 9.18, 9.22, 9.30])   # 温和转强(d66 小阴)
    d_close, d_low, d_high = _shaped([9.11, 9.10, 9.09, 9.08, 9.07])   # 企稳后阴跌(未转强)
    e_close, e_low, e_high = _shaped([9.30, 9.60, 9.95, 10.35, 10.80])  # 连续急拉、创新高

    prev = {c: None for c in _CODES}
    for i, d in enumerate(days):
        rows = [
            _row("600001.SH", a_close[i], prev["600001.SH"] or a_close[0],
                 a_low[i], a_high[i], amount=(50000.0 if i == 66 else 100000.0)),
            _row("600004.SH", d_close[i], prev["600004.SH"] or d_close[0],
                 d_low[i], d_high[i]),
            _row("600005.SH", e_close[i], prev["600005.SH"] or e_close[0],
                 e_low[i], e_high[i]),
        ]
        b_close = round(20.0 - 0.1 * i, 4)
        rows.append(_row("600002.SH", b_close, prev["600002.SH"] or 20.0,
                         round(b_close - 0.05, 4), round((prev["600002.SH"] or 20.0) + 0.02, 4)))
        if i >= 60:   # 600003.SH 只有最后 10 日
            rows.append(_row("600003.SH", 5.0, 5.0, 4.95, 5.05))
        prev["600001.SH"], prev["600004.SH"], prev["600005.SH"] = a_close[i], d_close[i], e_close[i]
        prev["600002.SH"] = b_close
        write_daily_fixture(env, "daily", d, rows)

    # 行业中位收益(industry_strength_daily;RS5 需要判定日前后 5 日窗口凑满)
    conn = sqlite3.connect(str(env.db_path))
    try:
        for d in days[58:]:
            conn.execute(
                "INSERT OR REPLACE INTO industry_strength_daily "
                "(trade_date, industry, median_ret, member_count, industry_rank, "
                "is_strength_day, persist_days, quantile, min_members, computed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (d.strftime("%Y%m%d"), _IND, -0.001, 10, 1, 0, 0, 0.8, 5, "t"),
            )
        conn.commit()
    finally:
        conn.close()

    # 判定日附近的 limit_derived(当日非涨停;分区在、值明确)
    for d in days[-3:]:
        write_daily_fixture(env, "limit_derived", d, [
            {"ts_code": c, "board": "MAIN", "limit_pct": 0.10,
             "limit_up_price": 99.0, "limit_down_price": 1.0,
             "is_limit_up": False, "is_limit_down": False, "is_zaban": False,
             "consec_limit_up_days": 0}
            for c in _CODES
        ])
    return days, days[-1]


class TestFullMarketCompute:
    def test_four_states_and_none_from_synthetic_market(self, isolated_env):
        env = isolated_env
        days, d0 = _seed_market(env)
        stats = landing_store.refresh_landing_states(
            [d0], db_path=env.db_path, parquet_dir=env.parquet_dir
        )
        assert stats == {"days": 1, "rows": 5, "failed": 0}

        a = landing_store.load_landing_state(d0, "600001.SH", db_path=env.db_path)
        assert a is not None, "600001.SH 判定行缺失"
        assert a["state"] == landing.LIFTOFF_CONFIRMED, a["state_reason"]
        assert a["skeleton_version"] == "engine_default"   # 隔离库无骨架线现役
        assert "missing:skeleton_pack" in a["state_reason"]
        m = a["metrics"]
        assert m["low5_over_base"] is not None and m["low5_over_base"] > 1.01
        assert m["rs5"] is not None and m["rs5"] > 0
        assert m["is_limit_up"] is False
        # platform_days 住 metrics_json,⛔ 不另起一张表(plan §五 ③-F)
        assert isinstance(m["platform_days"], int) and m["platform_days"] > 0
        assert m["platform_amplitude"] is not None

        b = landing_store.load_landing_state(d0, "600002.SH", db_path=env.db_path)
        assert b is not None and b["state"] == landing.FALLING, b and b["state_reason"]
        assert "c1" in b["state_reason"]

        c = landing_store.load_landing_state(d0, "600003.SH", db_path=env.db_path)
        assert c is not None and c["state"] == landing.NONE_STATE
        # 缺数不猜:state_reason 逐条说明缺在哪
        assert "c1.low5_over_base=na" in c["state_reason"]

        dd = landing_store.load_landing_state(d0, "600004.SH", db_path=env.db_path)
        assert dd is not None and dd["state"] == landing.LANDING_PENDING, dd and dd["state_reason"]
        assert "c4" in dd["state_reason"]

        e = landing_store.load_landing_state(d0, "600005.SH", db_path=env.db_path)
        assert e is not None and e["state"] == landing.HIGH_EXTENDED, e and e["state_reason"]
        assert "c5" in e["state_reason"]

    def test_no_daily_data_yields_zero_rows_not_error(self, isolated_env):
        env = isolated_env
        days = business_days(date(2026, 1, 5), 3)
        insert_trade_cal(env, days)
        stats = landing_store.refresh_landing_states(
            [days[-1]], db_path=env.db_path, parquet_dir=env.parquet_dir
        )
        assert stats["rows"] == 0 and stats["failed"] == 0
        assert landing_store.load_landing_states(days[-1], db_path=env.db_path).is_empty()
        assert landing_store.load_landing_state(days[-1], "600001.SH", db_path=env.db_path) is None

    def test_bulk_vs_day_by_day_vs_readback_are_identical(self, isolated_env):
        """三路等价:全量批算(一次调用 3 天)≡ 逐日循环 ≡ 落表读回,比较前
        `.drop("computed_at")`(P1-36 定案体例:审计戳跨秒边界合法不同,业务列
        仍逐位相同)。metrics 只存缩放不变量(比值/收益率),qfq 基准因子随取数
        区间尾端漂移不影响任何业务列——这正是本断言成立的前提(模块头登记)。"""
        env = isolated_env
        days, _ = _seed_market(env)
        last3 = days[-3:]

        landing_store.refresh_landing_states(last3, db_path=env.db_path, parquet_dir=env.parquet_dir)
        bulk = {d: landing_store.load_landing_states(d, db_path=env.db_path) for d in last3}

        with connection(env.db_path) as conn:
            conn.execute(f"DELETE FROM {landing_store.TABLE}")
        for d in last3:
            landing_store.refresh_landing_states([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
        daybyday = {d: landing_store.load_landing_states(d, db_path=env.db_path) for d in last3}

        for d in last3:
            assert not bulk[d].is_empty()
            assert bulk[d].drop("computed_at").equals(daybyday[d].drop("computed_at")), (
                f"{d} 批算与逐日结果不一致(业务列,已排除审计戳 computed_at)"
            )

    def test_metrics_json_is_valid_json_with_platform_days(self, isolated_env):
        env = isolated_env
        days, d0 = _seed_market(env)
        landing_store.refresh_landing_states([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        df = landing_store.load_landing_states(d0, db_path=env.db_path)
        assert df.height == 5
        for s in df["metrics_json"].to_list():
            parsed = json.loads(s)
            assert "platform_days" in parsed and "rs5" in parsed

    def test_limit_derived_missing_day_makes_c5_na_not_guessed(self, isolated_env):
        """判定日 limit_derived 分区缺失 → is_limit_up = na → 判据 5 判不动 →
        `none`(⛔ 不把「查无此行」当「非涨停」——缺数不猜)。"""
        env = isolated_env
        days, d0 = _seed_market(env)
        import os
        from neckline.data.market_data import day_file_path
        for d in days[-3:]:
            p = day_file_path("limit_derived", d, env.parquet_dir)
            if p.exists():
                os.remove(p)
        landing_store.refresh_landing_states([d0], db_path=env.db_path, parquet_dir=env.parquet_dir)
        a = landing_store.load_landing_state(d0, "600001.SH", db_path=env.db_path)
        assert a is not None
        assert a["state"] == landing.NONE_STATE
        assert "c5.limit_up=na" in a["state_reason"]


# ══════════════════════════════════════════════════════════════════════════
# 5. 表结构(plan §五 ③-C DDL 逐列)
# ══════════════════════════════════════════════════════════════════════════

def test_landing_state_daily_columns_match_plan(isolated_env):
    with connection(isolated_env.db_path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(landing_state_daily)").fetchall()]
        pk = [r[1] for r in conn.execute("PRAGMA table_info(landing_state_daily)").fetchall() if r[5]]
    assert cols == [
        "trade_date", "ts_code", "state", "state_reason",
        "metrics_json", "skeleton_version", "computed_at",
    ]
    assert set(pk) == {"trade_date", "ts_code"}
