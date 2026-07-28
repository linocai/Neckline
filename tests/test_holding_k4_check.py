"""持仓 K4 每日体检 + D5 净浮盈 seam 单测(plan §五 v1.3-② 验收)。

覆盖:①K4 advisory polars 镜像分级(A1/A3/A3b 强价量、B1/B2/B4 普通、A2/B3 题材弱证据)+
镜像表达式(年线下闸分 A3b 派发 vs B1 堆积);②证据强度标注(题材类=constituent 参考);
③has_strong 门槛(强价量触发、题材弱证据不触发第六类 APNs);④net_float 扣双边费 + 两档
时间退出态;⑤DB K4 advisory 读取(evidence 来自 DB 不抄常量);⑥ma250 镜像正确;
⑦holding_store 落库/读取 + **定格判向 seam**;⑧情景树每日对照挑出(复用既有写路径,无新写端点)。

**审计 🔴-1(2026-07-27 用户拍板方案 A「D5 判一次定格」)**:本模块是唯一定格点,新增
「16:35 定格」双向锁死——①定格豁免后跌回浮亏不改判;②定格「该走」后转浮盈不被洗白;
③D15 硬上限仍按 d_count 判;④K1 单档恒不定格(行为与审计前逐位一致)。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from neckline.report import holding_k4_check as hk
from neckline.report import holding_store
from neckline.report.sectors import SectorScore, sector_hot_lookup
from neckline.sentinel.positions import Position
from neckline.sentinel.precall import (
    HARD_CAP_EXIT,
    PROFIT_EXEMPT,
    TIME_EXIT_NEXT_DAY,
    classify_time_exit,
    scan_time_exits,
)
from neckline.strategy.momentum import MomentumConfig

TD = date(2026, 7, 17)
_RULE_K1 = {"config": {"stop_pct": 0.05, "max_hold_days": 5, "take_profit_retrace": 0.05}}
_RULE_V13 = {"config": {"stop_pct": 0.05, "max_hold_days": 5, "take_profit_retrace": 0.08,
                        "time_exit_only_if_unprofitable": True, "max_hold_days_profit": 15}}


def _pos(pid=1, code="600001.SH", buy_price=10.0, qty=1000, buy_date="20260710", buy_fees=None):
    return Position(id=pid, ts_code=code, buy_price=buy_price, qty=qty, buy_date=buy_date,
                    status="open", sell_price=None, sell_date=None, note=None, buy_fees=buy_fees)


# ————————————————————————————————————————————————————————————————
# 1) polars 镜像:年线下闸分级(A3 派发涨停 / A3b 派发放量大阳 / B1 普通堆积)
# ————————————————————————————————————————————————————————————————

def _hit_row(**over):
    """构造一行已含 K4 判据特征的合成面板(绕过 250 日加载),跑 `_add_hit_columns`。"""
    base = dict(
        ts_code="600001.SH", close=9.0, ma20=8.0, ma250=10.0, ma250_slope_up=False,
        is_limit_up=False, is_limit_down=False, high=9.0, low=8.5,
        vol_above_ma20_cnt3=0, ret_1d=0.0, vol=100.0, vol_ma20=100.0, vol_ratio_5=1.0,
        turnover_rate=3.0, state4="④双空头态",
    )
    base.update(over)
    df = pl.DataFrame([base])
    return hk._add_hit_columns(df).to_dicts()[0]


def test_a1_turnover_gt_10():
    assert _hit_row(turnover_rate=12.0)["_hit_A1"] is True
    assert _hit_row(turnover_rate=9.9)["_hit_A1"] is False


def test_a3_belowyear_limitup():
    # 年线下(close<ma250 & ~slope_up)+ 涨停 → A3 派发涨停(强)
    r = _hit_row(close=9.0, ma250=10.0, ma250_slope_up=False, is_limit_up=True)
    assert r["_hit_A3"] is True
    # 年线上(close>ma250 & slope_up)+ 涨停 → 非派发(不是诱多域)
    r2 = _hit_row(close=11.0, ma250=10.0, ma250_slope_up=True, is_limit_up=True)
    assert r2["_hit_A3"] is False


def test_a3b_vs_b1_year_line_gate():
    """放量大阳:年线下=A3b 派发(强,量比≥2 实测口径),年线上=B1 堆积(普通,advisory ×1.5
    口径)——由年线闸分级(雷区地图 3-⑤:放量大阳只在年线下为负=派发)。"""
    bigred = dict(vol_above_ma20_cnt3=2, ret_1d=0.06, vol=200.0, vol_ma20=100.0, vol_ratio_5=2.5)
    below = _hit_row(close=9.0, ma250=10.0, ma250_slope_up=False, **bigred)
    assert below["_hit_A3b"] is True and below["_hit_B1"] is False
    above = _hit_row(close=11.0, ma250=10.0, ma250_slope_up=True, **bigred)
    assert above["_hit_A3b"] is False and above["_hit_B1"] is True


def test_a3b_volume_ratio_threshold_2x_not_1p5():
    """A3b 贴雷区地图 3-⑤ 实测口径:量比(vol/vol_ma5)≥2 才派发;1.6× **不**触发(证明门槛按
    2.0 走、没被 B1 的 ×1.5 兜住),2.1× 触发。强警示可信度挂在实测证据集合上,不放宽。"""
    below = dict(close=9.0, ma250=10.0, ma250_slope_up=False, ret_1d=0.06)
    assert _hit_row(vol_ratio_5=1.6, **below)["_hit_A3b"] is False
    assert _hit_row(vol_ratio_5=2.1, **below)["_hit_A3b"] is True


def test_b2_dual_golden_cross_and_b4():
    assert _hit_row(state4="①双金叉态")["_hit_B2"] is True
    assert _hit_row(state4="②仅MACD")["_hit_B2"] is False
    assert _hit_row(close=9.0, ma20=8.0, ret_1d=0.06)["_hit_B4"] is True
    assert _hit_row(close=7.0, ma20=8.0, ret_1d=0.06)["_hit_B4"] is False  # close<ma20 不算追强


def test_oneword_excluded_from_bigred():
    """一字涨停(high==low)排除放量大阳(不可交易),A3b/B1 均不命中。"""
    r = _hit_row(close=9.0, ma250=10.0, ma250_slope_up=False, is_limit_up=True,
                 high=9.0, low=9.0, vol_above_ma20_cnt3=2, ret_1d=0.06, vol=200.0,
                 vol_ma20=100.0, vol_ratio_5=2.5)
    assert r["_hit_A3b"] is False and r["_hit_B1"] is False


# ————————————————————————————————————————————————————————————————
# 2) 分级 + 证据强度(_HIT_META)+ 题材持续天数弱证据
# ————————————————————————————————————————————————————————————————

def test_hit_meta_levels_and_evidence_strength():
    meta = hk._HIT_META
    # 强价量证据(触发第六类 APNs)
    for code in ("A1_turnover_gt_10", "A3_belowyear_limitup", "A3b_belowyear_bigvol"):
        assert meta[code][1] == "strong" and meta[code][2] == "price_volume"
    # 题材≥4天:强级别但弱证据(参考)——不触发 APNs
    assert meta["A2_theme_persist_ge_4"][1] == "strong"
    assert meta["A2_theme_persist_ge_4"][2] == "constituent"
    # 普通警示
    assert meta["B1_volume_stacking"][1] == "normal" and meta["B1_volume_stacking"][2] == "price_volume"
    assert meta["B3_theme_persist_2_3"][1] == "normal" and meta["B3_theme_persist_2_3"][2] == "constituent"


def test_theme_persist_from_board_age():
    hot = sector_hot_lookup([
        SectorScore(index_code="883300.TI", name="芯片", board_age=5, ret_20d=0.1, bonus=0.0, rank=1),
        SectorScore(index_code="883301.TI", name="储能", board_age=2, ret_20d=0.05, bonus=0.0, rank=2),
    ])
    mm = {"600001.SH": ["883300.TI"], "600002.SH": ["883301.TI"], "600003.SH": ["999.TI"]}
    assert hk._theme_persist_days("600001.SH", mm, hot) == 5   # ≥4 → A2
    assert hk._theme_persist_days("600002.SH", mm, hot) == 2   # 2-3 → B3
    assert hk._theme_persist_days("600003.SH", mm, hot) == 0   # 不在热榜 → 0


def test_evaluate_hits_theme_ge4_is_strong_but_constituent():
    hits = hk._evaluate_hits(None, persist_days=4, evidence={})
    codes = {h.code for h in hits}
    assert codes == {"A2_theme_persist_ge_4"}
    a2 = hits[0]
    assert a2.level == "strong" and a2.evidence_strength == "constituent"


def test_evaluate_hits_theme_2_3_normal():
    hits = hk._evaluate_hits(None, persist_days=3, evidence={})
    assert [h.code for h in hits] == ["B3_theme_persist_2_3"]
    assert hits[0].level == "normal"


# ————————————————————————————————————————————————————————————————
# 3) build_holding_k4_check(monkeypatch 面板)+ has_strong 门槛 + net_float
# ————————————————————————————————————————————————————————————————

def _stub_panel(rows):
    """构造 `_build_holding_feature_panel` 的返回(trade_date 当日行 + _hit_* 列)。"""
    def _fn(codes, trade_date, parquet_dir):
        return pl.DataFrame(rows) if rows else pl.DataFrame()
    return _fn


def _panel_row(code, close=10.5, **hits):
    r = dict(ts_code=code, close=close, _hit_A1=False, _hit_A3=False, _hit_A3b=False,
             _hit_B1=False, _hit_B2=False, _hit_B4=False)
    r.update(hits)
    return r


def test_build_has_strong_only_price_volume(monkeypatch):
    """强价量命中(A3)→ has_strong=True(触发第六类);仅题材≥4天(弱证据)→ has_strong=False。"""
    rows = [_panel_row("600001.SH", _hit_A3=True), _panel_row("600002.SH")]
    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel(rows))
    # 600002 命中 A2(题材≥4)但弱证据:构造热榜使其 board_age≥4
    hot = [SectorScore(index_code="X.TI", name="题材", board_age=5, ret_20d=0.1, bonus=0.0, rank=1)]
    mm = {"600002.SH": ["X.TI"]}
    positions = [_pos(1, "600001.SH"), _pos(2, "600002.SH")]
    items = hk.build_holding_k4_check(TD, _RULE_K1, positions, sector_scores=hot, member_map=mm)
    by_code = {it.ts_code: it for it in items}
    assert by_code["600001.SH"].has_strong is True                 # A3 强价量
    assert "A3_belowyear_limitup" in {h.code for h in by_code["600001.SH"].hits}
    a2_item = by_code["600002.SH"]
    assert a2_item.has_strong is False                             # A2 弱证据不触发
    assert "A2_theme_persist_ge_4" in {h.code for h in a2_item.hits}
    assert a2_item.strong_price_volume_labels() == []             # 无强价量文案 → 不推 APNs


def test_build_net_float_and_two_tier_state(monkeypatch):
    """net_float 扣双边费(现价>成本 → 浮盈);两档 config 下 d≥5 且浮盈 → profit_exempt。"""
    # 现价 11 vs 成本 10,qty 1000 → 毛浮盈 1000 元,扣费后仍明显 >0
    rows = [_panel_row("600001.SH", close=11.0)]
    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel(rows))
    # buy_date 距 TD ≥5 交易日(20260710 → 20260717 跨 5 交易日)
    positions = [_pos(1, "600001.SH", buy_price=10.0, qty=1000, buy_date="20260710", buy_fees=15.0)]
    items = hk.build_holding_k4_check(TD, _RULE_V13, positions)
    it = items[0]
    assert it.net_float is not None and it.net_float > 0
    assert it.time_exit_state == PROFIT_EXEMPT and it.max_hold_effective == 15


def test_build_no_data_position_conservative(monkeypatch):
    """停牌/无 EOD 行(不在面板)→ has_data False、net_float None、无命中。"""
    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel([]))
    items = hk.build_holding_k4_check(TD, _RULE_V13, [_pos(1, "600001.SH")])
    it = items[0]
    assert it.has_data is False and it.net_float is None and it.hits == []


# —— 审计 🔴-1:16:35 是**唯一定格点**,「D5 判一次定格」双向锁死 ——————————————————

def _run_eod(monkeypatch, db, trade_date, close, positions, rule=None):
    """跑一次 16:35 EOD 体检并落库(合成面板注入 close),返回 items。"""
    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel([_panel_row("600001.SH", close=close)]))
    items = hk.build_holding_k4_check(trade_date, rule or _RULE_V13, positions, db_path=db)
    holding_store.save_holding_eod_checks(trade_date, items, db_path=db)
    return items


def test_eod_freezes_verdict_once_and_survives_later_loss(isolated_env, monkeypatch):
    """① D5 定格豁免 → D6/D7 收盘跌回浮亏,16:35 **不得**改判成时间退出(判向 + 定格三件不变)。"""
    db = isolated_env.db_path
    positions = [_pos(1, "600001.SH", buy_price=10.0, qty=1000, buy_date="20260710", buy_fees=15.0)]
    d5 = _run_eod(monkeypatch, db, date(2026, 7, 17), 11.0, positions)[0]   # d=5,收盘 11 → 浮盈
    assert d5.time_exit_state == PROFIT_EXEMPT and d5.time_exit_locked_state == PROFIT_EXEMPT
    assert d5.time_exit_locked_date == "20260717" and d5.time_exit_locked_net_float == d5.net_float
    d7 = _run_eod(monkeypatch, db, date(2026, 7, 21), 9.7, positions)[0]    # 跌成浮亏(未破 -5%)
    assert d7.net_float is not None and d7.net_float < 0                    # 当日净浮盈确实为负
    assert d7.time_exit_state == PROFIT_EXEMPT and d7.max_hold_effective == 15
    assert d7.time_exit_locked_date == "20260717"                           # 定格日不变,判向未重判


def test_eod_frozen_exit_not_laundered_by_later_profit(isolated_env, monkeypatch):
    """② D5 定格「该走」→ D6/D7 转浮盈,16:35 **不得**改口豁免(违纪不被事后合法化)。"""
    db = isolated_env.db_path
    positions = [_pos(1, "600001.SH", buy_price=10.0, qty=1000, buy_date="20260710", buy_fees=15.0)]
    d5 = _run_eod(monkeypatch, db, date(2026, 7, 17), 9.8, positions)[0]    # d=5,浮亏 → 该走
    assert d5.time_exit_state == TIME_EXIT_NEXT_DAY and d5.time_exit_locked_state == TIME_EXIT_NEXT_DAY
    d7 = _run_eod(monkeypatch, db, date(2026, 7, 21), 12.0, positions)[0]   # 大幅转浮盈
    assert d7.net_float is not None and d7.net_float > 0
    assert d7.time_exit_state == TIME_EXIT_NEXT_DAY and d7.max_hold_effective == 5
    assert d7.time_exit_locked_state == TIME_EXIT_NEXT_DAY and d7.time_exit_locked_date == "20260717"


def test_eod_hard_cap_overrides_frozen_exempt(isolated_env, monkeypatch):
    """③ D15 硬上限仍按 d_count 判(定格豁免挡不住硬上限;定格记录本身不被改写)。"""
    db = isolated_env.db_path
    positions = [_pos(1, "600001.SH", buy_price=10.0, qty=1000, buy_date="20260710", buy_fees=15.0)]
    _run_eod(monkeypatch, db, date(2026, 7, 17), 11.0, positions)           # d=5 定格豁免
    far = [_pos(1, "600001.SH", buy_price=10.0, qty=1000, buy_date="20260601", buy_fees=15.0)]
    it = _run_eod(monkeypatch, db, date(2026, 7, 21), 11.0, far)[0]         # 同 position_id,d 已 ≥15
    assert it.d_count >= 15
    assert it.time_exit_state == HARD_CAP_EXIT and it.max_hold_effective == 15
    assert it.time_exit_locked_state == PROFIT_EXEMPT                       # 定格记录原样保留(审计)


def test_eod_k1_single_tier_never_freezes(isolated_env, monkeypatch):
    """④ K1 现役(单档)行为与审计前完全一致:定格三件恒 None,状态 = classify_time_exit。"""
    db = isolated_env.db_path
    positions = [_pos(1, "600001.SH", buy_price=10.0, qty=1000, buy_date="20260710", buy_fees=15.0)]
    for close in (11.0, 9.8):
        it = _run_eod(monkeypatch, db, date(2026, 7, 17), close, positions, rule=_RULE_K1)[0]
        cfg_k1 = MomentumConfig(**_RULE_K1["config"])
        assert (it.time_exit_state, it.max_hold_effective) == classify_time_exit(it.d_count, cfg_k1)
        assert it.time_exit_state == TIME_EXIT_NEXT_DAY                     # d≥5 单档无条件退出
        assert it.time_exit_locked_state is None and it.time_exit_locked_date is None
        assert it.time_exit_locked_net_float is None
    assert holding_store.locked_time_exit_map(db_path=db) == {}


def test_eod_no_freeze_before_decision_point(isolated_env, monkeypatch):
    """d < max_hold_days(未到判定点)→ HOLDING 且不定格(定格只发生在判定点当天)。"""
    db = isolated_env.db_path
    positions = [_pos(1, "600001.SH", buy_price=10.0, qty=1000, buy_date="20260716", buy_fees=15.0)]
    it = _run_eod(monkeypatch, db, date(2026, 7, 17), 11.0, positions)[0]
    assert it.d_count < 5 and it.time_exit_state == hk._HOLDING
    assert it.time_exit_locked_state is None
    assert holding_store.locked_time_exit_map(db_path=db) == {}


def test_build_scenario_review_flag(monkeypatch):
    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel([_panel_row("600001.SH")]))
    items = hk.build_holding_k4_check(TD, _RULE_K1, [_pos(7, "600001.SH")],
                                      scenario_position_ids={7})
    assert items[0].scenario_review is True
    items2 = hk.build_holding_k4_check(TD, _RULE_K1, [_pos(8, "600001.SH")], scenario_position_ids={7})
    assert items2[0].scenario_review is False


# ————————————————————————————————————————————————————————————————
# 4) DB K4 advisory 读取(evidence 来自 DB,不抄常量)
# ————————————————————————————————————————————————————————————————

def test_evidence_read_from_db(isolated_env):
    """在隔离库落一条 K4 advisory,evidence 文字应从 DB 读出(而非模块兜底)。"""
    from neckline.strategy import brain
    db = isolated_env.db_path
    custom = "自定义证据XYZ换手异动"
    brain.save_version("K4", rule={"config": {}, "k4_advisory": {
        "hard_cut": {"A1_turnover_gt_10": {"expr": "turnover_rate > 10", "evidence": custom}},
        "avoid_flag": {},
    }}, changelog="test K4", activate=False, db_path=db)
    ev = hk._load_k4_evidence(db)
    assert ev.get("A1_turnover_gt_10") == custom
    # 命中时 hit.evidence 采用 DB 文字
    hits = hk._evaluate_hits(_panel_row("600001.SH", _hit_A1=True), persist_days=0, evidence=ev)
    a1 = next(h for h in hits if h.code == "A1_turnover_gt_10")
    assert a1.evidence == custom


def test_evidence_fallback_when_no_k4(isolated_env):
    """隔离库无 K4 行 → evidence 落模块兜底,镜像判据照跑不崩。"""
    ev = hk._load_k4_evidence(isolated_env.db_path)
    assert ev == {}
    hits = hk._evaluate_hits(_panel_row("600001.SH", _hit_A1=True), persist_days=0, evidence=ev)
    assert hits[0].evidence == hk._FALLBACK_EVIDENCE["A1_turnover_gt_10"]


# ————————————————————————————————————————————————————————————————
# 5) ma250 镜像正确性(guard 年线判据地基)
# ————————————————————————————————————————————————————————————————

def test_ma250_mirror():
    """`_add_k4_features` 的 ma250 = 后向 250 日均收盘(min_samples=250);最后一行取到值。"""
    closes = [float(i) for i in range(1, 301)]  # 1..300
    df = pl.DataFrame({
        "ts_code": ["600001.SH"] * 300,
        "trade_date": [date(2025, 1, 1)] * 300,  # 值不参与 ma250,仅排序占位(单调即可)
        "close": closes, "low": [c - 0.5 for c in closes], "high": [c + 0.5 for c in closes],
        "vol": [100.0] * 300, "vol_ma20": [90.0] * 300,
    }).with_columns(pl.int_range(0, 300).alias("_i")).with_columns(
        (pl.col("trade_date").cast(pl.Datetime) + pl.duration(days=pl.col("_i"))).cast(pl.Date).alias("trade_date")
    ).drop("_i")
    out = hk._add_k4_features(df).sort("trade_date")
    last = out.to_dicts()[-1]
    assert last["ma250"] == pytest.approx(sum(closes[-250:]) / 250)
    assert last["state4"] is not None  # warmup 后四态成形


# ————————————————————————————————————————————————————————————————
# 6) holding_store 落库/读取 + 定格判向 seam(审计 🔴-1「D5 判一次定格」)
# ————————————————————————————————————————————————————————————————

class _Item:
    """duck-typed HoldingK4Item(供 store 落库测试,不依赖真面板)。"""
    def __init__(self, pid, nf, state, eff, strong, review, hits=None,
                 lock_state=None, lock_date=None, lock_nf=None):
        self.position_id, self.net_float, self.time_exit_state = pid, nf, state
        self.max_hold_effective, self.has_strong, self.scenario_review = eff, strong, review
        self.d_count, self._hits = 5, hits or []
        self.time_exit_locked_state = lock_state
        self.time_exit_locked_date = lock_date
        self.time_exit_locked_net_float = lock_nf
    def hits_public(self):
        return self._hits


def test_holding_store_roundtrip_and_latest(isolated_env):
    db = isolated_env.db_path
    hits = [{"code": "A3_belowyear_limitup", "label": "年线下涨停", "level": "strong",
             "evidence": "e", "evidence_strength": "price_volume"}]
    holding_store.save_holding_eod_checks(date(2026, 7, 16),
        [_Item(1, -50.0, TIME_EXIT_NEXT_DAY, 5, True, True, hits)], db_path=db)
    # 次日再落一份(净浮盈翻正)——latest 取最大 trade_date
    holding_store.save_holding_eod_checks(date(2026, 7, 17),
        [_Item(1, 120.0, PROFIT_EXEMPT, 15, False, False, [])], db_path=db)
    snaps = holding_store.load_latest_checks_by_position(db_path=db)
    assert snaps[1]["net_float"] == 120.0 and snaps[1]["time_exit_state"] == PROFIT_EXEMPT
    assert snaps[1]["scenario_review"] is False
    nf_map = holding_store.latest_net_float_map(db_path=db)
    assert nf_map[1] == 120.0


def test_locked_state_provider_feeds_frozen_verdict(isolated_env):
    """seam 核心(审计 🔴-1 后):两档 config 下 provider 给出**定格判向** → profit_exempt。"""
    db = isolated_env.db_path
    holding_store.save_holding_eod_checks(date(2026, 7, 16),
        [_Item(1, 300.0, PROFIT_EXEMPT, 15, False, False, [],
               lock_state=PROFIT_EXEMPT, lock_date="20260716", lock_nf=300.0)], db_path=db)
    provider = holding_store.locked_state_provider(db_path=db)
    cfg = MomentumConfig(**_RULE_V13["config"])
    positions = [_pos(1, "600001.SH", buy_date="20260710")]  # d≥5
    exits = scan_time_exits(positions, TD, cfg, locked_state_provider=provider)
    assert len(exits) == 1 and exits[0].state == PROFIT_EXEMPT
    # 对照:无定格 → 保守判 time_exit_next_day(豁免需正向证据)
    exits_none = scan_time_exits(positions, TD, cfg, locked_state_provider=None)
    assert exits_none[0].state == TIME_EXIT_NEXT_DAY


def test_locked_map_takes_earliest_freeze_row(isolated_env):
    """定格是「判一次」:后续各日的行都把定格值带过来,`locked_time_exit_map` 取**最早**那份
    (判向源头 + 判定日 + 判定所用净浮盈都指向定格当天,不被后续行的净浮盈覆盖)。"""
    db = isolated_env.db_path
    holding_store.save_holding_eod_checks(date(2026, 7, 16),
        [_Item(1, -50.0, TIME_EXIT_NEXT_DAY, 5, False, False, [],
               lock_state=TIME_EXIT_NEXT_DAY, lock_date="20260716", lock_nf=-50.0)], db_path=db)
    holding_store.save_holding_eod_checks(date(2026, 7, 17),
        [_Item(1, 900.0, TIME_EXIT_NEXT_DAY, 5, False, False, [],
               lock_state=TIME_EXIT_NEXT_DAY, lock_date="20260716", lock_nf=-50.0)], db_path=db)
    locked = holding_store.locked_time_exit_map(db_path=db)
    assert locked[1] == {"state": TIME_EXIT_NEXT_DAY, "date": "20260716", "net_float": -50.0}


def test_locked_state_provider_missing_returns_none(isolated_env):
    """无快照(刚开仓未体检)/ 未定格 → provider 返 None(保守判非浮盈,不崩)。"""
    provider = holding_store.locked_state_provider(db_path=isolated_env.db_path)
    assert provider(_pos(99, "600009.SH")) is None


# —— v1.4-①-B 停牌 / 无当日 EOD 行的持仓票(§七 P0-2)——————————————————————————

def test_no_data_hangs_time_exit_instead_of_pushing(monkeypatch):
    """到判定点(D≥5)但当日无 EOD 行且从未定格 → 判向挂起 `suspended_hold`,
    **且这一天不写定格**(停牌当天根本没有收盘价,硬判等于凭空定一个不可回头的向)。"""
    from neckline.sentinel.precall import SUSPENDED_HOLD

    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel([]))
    items = hk.build_holding_k4_check(TD, _RULE_V13, [_pos(1, "600001.SH", buy_date="20260710")])
    it = items[0]
    assert it.d_count >= 5                       # D 计数照常累计(纪律口径是「持有交易日数」)
    assert it.time_exit_state == SUSPENDED_HOLD
    assert (it.time_exit_locked_state, it.time_exit_locked_date, it.time_exit_locked_net_float) == (None, None, None)


def test_no_data_skips_whole_checkup_including_theme_hits(monkeypatch):
    """**整份体检跳过**:当日无 EOD 行时连题材类 A2/B3(不依赖价量面板)也不产出——
    「空牌 = 体检过了没问题」与「今天压根没体检」必须能分开(§3.8)。"""
    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel([]))
    hot = [SectorScore(index_code="X.TI", name="题材", board_age=6, ret_20d=0.1, bonus=0.0, rank=1)]
    items = hk.build_holding_k4_check(
        TD, _RULE_V13, [_pos(1, "600001.SH")],
        sector_scores=hot, member_map={"600001.SH": ["X.TI"]},
    )
    it = items[0]
    assert it.has_data is False
    assert it.hits == [] and it.has_strong is False
    # 对照:同样的题材条件,有 EOD 行时 A2 是会命中的(证明上面的空不是因为条件不成立)
    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel([_panel_row("600001.SH")]))
    it2 = hk.build_holding_k4_check(
        TD, _RULE_V13, [_pos(1, "600001.SH")],
        sector_scores=hot, member_map={"600001.SH": ["X.TI"]},
    )[0]
    assert {h.code for h in it2.hits} == {"A2_theme_persist_ge_4"}


def test_existing_lock_survives_suspension(monkeypatch, isolated_env):
    """停牌**不撤回**已有定格(审计 🔴-1:判向在有真数据那天一次性做出,不得事后改口)。"""
    db = isolated_env.db_path
    positions = [_pos(1, "600001.SH", buy_price=10.0, qty=1000, buy_date="20260710")]
    # D5 当天有数据 → 浮盈豁免定格
    _run_eod(monkeypatch, db, TD, close=11.0, positions=positions)
    assert holding_store.locked_time_exit_map(db_path=db)[1]["state"] == PROFIT_EXEMPT
    # 次日停牌(面板空)→ 判向仍是定格值,不挂起
    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel([]))
    it = hk.build_holding_k4_check(date(2026, 7, 18), _RULE_V13, positions, db_path=db)[0]
    assert it.has_data is False
    assert it.time_exit_state == PROFIT_EXEMPT and it.time_exit_locked_date == TD.strftime("%Y%m%d")


def test_resume_day_locks_normally(monkeypatch, isolated_env):
    """复牌当日用**复牌当日 EOD** 正常定格(挂起只是把判向推迟,不是永久豁免)。"""
    from neckline.sentinel.precall import SUSPENDED_HOLD

    db = isolated_env.db_path
    positions = [_pos(1, "600001.SH", buy_price=10.0, qty=1000, buy_date="20260710")]
    monkeypatch.setattr(hk, "_build_holding_feature_panel", _stub_panel([]))
    it0 = hk.build_holding_k4_check(TD, _RULE_V13, positions, db_path=db)[0]
    holding_store.save_holding_eod_checks(TD, [it0], db_path=db)
    assert it0.time_exit_state == SUSPENDED_HOLD
    assert holding_store.locked_time_exit_map(db_path=db) == {}      # 挂起期不落定格

    resume = date(2026, 7, 20)
    it1 = _run_eod(monkeypatch, db, resume, close=9.0, positions=positions)[0]   # 复牌当日浮亏
    assert it1.time_exit_state == TIME_EXIT_NEXT_DAY
    assert it1.time_exit_locked_date == resume.strftime("%Y%m%d")    # 定格发生在复牌当日


def test_data_unavailable_persisted_and_legacy_row_is_null(isolated_env):
    """`data_unavailable` 落库并读回;**老行(未写该列)读回 None 而非 False**
    ——「不知道」不许冒充「体检过了」。"""
    import sqlite3

    db = isolated_env.db_path
    holding_store.save_holding_eod_checks(
        date(2026, 7, 16), [_Item(1, None, TIME_EXIT_NEXT_DAY, 5, False, False, [])], db_path=db)
    assert holding_store.load_latest_checks_by_position(db_path=db)[1]["data_unavailable"] is False

    conn = sqlite3.connect(str(db))
    try:                                     # 模拟建于本列之前的老行
        conn.execute("UPDATE holding_eod_check SET data_unavailable=NULL WHERE position_id=1")
        conn.commit()
    finally:
        conn.close()
    assert holding_store.load_latest_checks_by_position(db_path=db)[1]["data_unavailable"] is None
