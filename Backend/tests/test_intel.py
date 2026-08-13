"""复盘情报件单测(plan §五 v1.3-③-C1)。锁死:① 涨跌幅榜排序 + 名称解析;
② 涨停梯队按连板数分组降序;③ 跌停榜 + 总数(展示上限截断);④ 大盘量能(沪深
两市合计 + 5 日均,样本不足诚实标注);⑤ 最强题材过板块池卫生线 + 题材持续天数
标签 + 核心龙头;⑥ 市值偏好 / 涨跌停制度偏好分桶;⑦ 数据缺失逐项优雅降级(不
崩、留警告);⑧ 证据强度标注透到 `ThemeItem.evidence_strength` 字段。
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List

import polars as pl
import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture, write_flat_parquet

from neckline.report.intel import SZ_INDEX, compute_intel
from neckline.strategy.features import SSE_INDEX

pytestmark = pytest.mark.usefixtures("isolated_env")


def _daily_row(code: str, *, pct_chg: float, close: float = 10.0) -> dict:
    pre = close / (1 + pct_chg / 100) if pct_chg != -100 else close
    return {"ts_code": code, "open": pre, "high": max(pre, close), "low": min(pre, close),
            "close": close, "pre_close": pre, "pct_chg": pct_chg}


def _limit_row(code: str, *, is_up: bool, is_down: bool = False, consec: int = 1, limit_pct: float = 0.10) -> dict:
    return {
        "ts_code": code, "board": "MAIN", "status": "limit_up" if is_up else ("limit_down" if is_down else "zaban"),
        "limit_pct": limit_pct, "limit_up_price": 11.0, "limit_down_price": 9.0,
        "is_limit_up": is_up, "is_limit_down": is_down, "is_zaban": not is_up and not is_down,
        "consec_limit_up_days": consec if is_up else 0,
    }


def _index_daily_rows(sh_amount: float, sz_amount: float) -> List[dict]:
    base = {"close": 3800.0, "open": 3800.0, "high": 3800.0, "low": 3800.0, "pre_close": 3800.0, "change": 0.0, "pct_chg": 0.0, "vol": 1.0}
    return [
        {**base, "ts_code": SSE_INDEX, "amount": sh_amount},
        {**base, "ts_code": SZ_INDEX, "amount": sz_amount},
    ]


class TestGainersLosers:
    def test_ranks_by_pct_chg_and_resolves_names(self, isolated_env):
        d = date(2026, 3, 2)
        insert_trade_cal(isolated_env, [d])
        write_daily_fixture(isolated_env, "daily", d, [
            _daily_row("600001.SH", pct_chg=10.0),
            _daily_row("600002.SH", pct_chg=-10.0),
            _daily_row("600003.SH", pct_chg=3.0),
        ])
        insert_stock_basic(isolated_env, [
            {"ts_code": "600001.SH", "name": "示例甲"},
            {"ts_code": "600002.SH", "name": "示例乙"},
            {"ts_code": "600003.SH", "name": "示例丙"},
        ])
        report = compute_intel(d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert [g.ts_code for g in report.gainers] == ["600001.SH", "600003.SH", "600002.SH"]
        assert report.gainers[0].name == "示例甲"
        assert report.gainers[0].pct_chg == pytest.approx(10.0)
        assert [l.ts_code for l in report.losers] == ["600002.SH", "600003.SH", "600001.SH"]

    def test_missing_daily_degrades_to_empty_with_warning(self, isolated_env):
        insert_trade_cal(isolated_env, business_days(date(2026, 3, 2), 2))
        report = compute_intel(date(2026, 3, 3), parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert report.gainers == [] and report.losers == []
        assert any("涨跌幅榜" in w for w in report.warnings)


class TestLimitUpLadderAndLimitDown:
    def test_ladder_groups_by_consec_days_descending(self, isolated_env):
        d = date(2026, 3, 2)
        insert_trade_cal(isolated_env, [d])
        rows = (
            [_limit_row(f"U1{i}.SH", is_up=True, consec=1) for i in range(3)]
            + [_limit_row(f"U2{i}.SH", is_up=True, consec=2) for i in range(2)]
            + [_limit_row("U3.SH", is_up=True, consec=3)]
        )
        write_daily_fixture(isolated_env, "limit_derived", d, rows)
        report = compute_intel(d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert [(r.consec_days, r.count) for r in report.limit_up_ladder] == [(3, 1), (2, 2), (1, 3)]

    def test_limit_down_list_reports_true_total_count_even_when_capped(self, isolated_env):
        d = date(2026, 3, 2)
        insert_trade_cal(isolated_env, [d])
        codes = [f"D{i:03d}.SH" for i in range(120)]   # 超过展示上限 100
        rows = [_limit_row(c, is_up=False, is_down=True) for c in codes]
        write_daily_fixture(isolated_env, "limit_derived", d, rows)
        write_daily_fixture(isolated_env, "daily", d, [_daily_row(c, pct_chg=-10.0, close=9.0) for c in codes])
        report = compute_intel(d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert report.limit_down_total_count == 120
        assert len(report.limit_down) == 100   # 展示截断,总数不撒谎

    def test_no_limit_up_or_down_yields_empty_ladder_and_list(self, isolated_env):
        d = date(2026, 3, 2)
        insert_trade_cal(isolated_env, [d])
        write_daily_fixture(isolated_env, "limit_derived", d, [_limit_row("Z1.SH", is_up=False, is_down=False)])
        report = compute_intel(d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert report.limit_up_ladder == []
        assert report.limit_down == []
        assert report.limit_down_total_count == 0


class TestMarketVolume:
    def test_combines_sh_sz_and_computes_ma5(self, isolated_env):
        days = business_days(date(2026, 3, 2), 6)
        insert_trade_cal(isolated_env, days)
        for i, d in enumerate(days):
            write_daily_fixture(isolated_env, "index_daily", d, _index_daily_rows(1_000_000.0 + i * 10_000.0, 500_000.0))
        report = compute_intel(days[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        mv = report.market_volume
        assert mv is not None
        assert mv.sample_days == 5
        # 最后一天(i=5):sh=1,050,000 千元 -> 10.5 亿;sz=500,000 千元 -> 5.0 亿
        assert mv.sh_amount_yi == pytest.approx(10.5)
        assert mv.sz_amount_yi == pytest.approx(5.0)
        assert mv.total_amount_yi == pytest.approx(15.5)
        # 5 日均(第 1~5 天,i=1..5):sh 均值 = 1,000,000+30,000=1,030,000 千元 -> 10.3 亿;sz 恒 5.0 亿
        assert mv.ma5_amount_yi == pytest.approx(15.3)

    def test_insufficient_history_flags_sample_days_honestly(self, isolated_env):
        days = business_days(date(2026, 3, 2), 2)
        insert_trade_cal(isolated_env, days)
        for d in days:
            write_daily_fixture(isolated_env, "index_daily", d, _index_daily_rows(1_000_000.0, 500_000.0))
        report = compute_intel(days[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert report.market_volume.sample_days == 2
        assert any("样本仅 2" in w for w in report.warnings)

    def test_missing_index_daily_degrades_to_none(self, isolated_env):
        insert_trade_cal(isolated_env, business_days(date(2026, 3, 2), 2))
        report = compute_intel(date(2026, 3, 3), parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert report.market_volume is None
        assert any("大盘量能" in w for w in report.warnings)


def _seed_boards(settings, dates, board_defs: Dict[str, tuple]):
    """`board_defs`: index_code -> (name, closes_list)。写 ths_daily/ths_index。"""
    rows = []
    names_rows = []
    for code, (name, closes) in board_defs.items():
        for d, c in zip(dates, closes):
            rows.append({"ts_code": code, "trade_date": d, "close": c})
        names_rows.append({"ts_code": code, "name": name})
    write_flat_parquet(settings, "ths_daily.parquet", rows)
    write_flat_parquet(settings, "ths_index.parquet", names_rows)


def _trailing_up_closes(n: int, k: int) -> List[float]:
    """构造一条"最后 k 天新站上 MA20"的收盘价序列,底值恒 100(测过:board_age 在
    最后一天恰好 = k)。"""
    tail = [100.0 + i for i in range(1, k + 1)]
    return [100.0] * (n - k) + tail


class TestTopThemesHygieneAndPersistence:
    def test_hygiene_excludes_denylisted_board_even_with_higher_momentum(self, isolated_env):
        dates = business_days(date(2024, 1, 2), 28)
        insert_trade_cal(isolated_env, dates)
        n = len(dates)
        legit = _trailing_up_closes(n, 3)
        # 融资融券动量远高于合法板块,但必须被卫生线剔除
        junk = [100.0 * (1.05 ** i) for i in range(n)]
        _seed_boards(isolated_env, dates, {
            "AAA.TI": ("机器人概念", legit),
            "ZZZ.TI": ("融资融券", junk),
        })
        write_flat_parquet(isolated_env, "ths_member.parquet", [
            {"index_code": "AAA.TI", "con_code": "600001.SH", "con_name": "示例甲"},
            {"index_code": "ZZZ.TI", "con_code": "600002.SH", "con_name": "示例乙"},
        ])
        insert_stock_basic(isolated_env, [
            {"ts_code": "600001.SH", "name": "示例甲"}, {"ts_code": "600002.SH", "name": "示例乙"},
        ])
        trade_date = dates[-1]
        report = compute_intel(trade_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        names = [t.name for t in report.top_themes]
        assert "机器人概念" in names
        assert "融资融券" not in names
        assert "融资融券" in report.excluded_boards_note

    def test_persistence_labels_and_evidence_strength(self, isolated_env):
        dates = business_days(date(2024, 1, 2), 28)
        insert_trade_cal(isolated_env, dates)
        n = len(dates)
        _seed_boards(isolated_env, dates, {
            "FLAT.TI": ("恒不站上", _trailing_up_closes(n, 0)),
            "NEW.TI": ("新起板块", _trailing_up_closes(n, 1)),
            "MID.TI": ("持续板块", _trailing_up_closes(n, 3)),
            "OLD.TI": ("过热板块", _trailing_up_closes(n, 6)),
        })
        trade_date = dates[-1]
        report = compute_intel(trade_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        by_name = {t.name: t for t in report.top_themes}
        assert by_name["恒不站上"].persistence_label == "未站上MA20(非持续)"
        assert by_name["新起板块"].persistence_label == "新起(1日)"
        assert by_name["持续板块"].persistence_label == "持续中(2-3日)"
        assert by_name["过热板块"].persistence_label == "已延续(≥4日,警惕退潮)"
        # 证据强度标注必须透到字段(硬要求①),不是只在注释里
        assert all(t.evidence_strength == "constituent" for t in report.top_themes)
        dist = report.theme_persistence_distribution
        assert dist["未站上MA20(非持续)"] == 1
        assert dist["新起(1日)"] == 1
        assert dist["持续中(2-3日)"] == 1
        assert dist["已延续(≥4日,警惕退潮)"] == 1

    def test_theme_leaders_pick_top_2_by_pct_chg(self, isolated_env):
        dates = business_days(date(2024, 1, 2), 28)
        insert_trade_cal(isolated_env, dates)
        n = len(dates)
        _seed_boards(isolated_env, dates, {"AAA.TI": ("人工智能", _trailing_up_closes(n, 3))})
        write_flat_parquet(isolated_env, "ths_member.parquet", [
            {"index_code": "AAA.TI", "con_code": "600001.SH", "con_name": "甲"},
            {"index_code": "AAA.TI", "con_code": "600002.SH", "con_name": "乙"},
            {"index_code": "AAA.TI", "con_code": "600003.SH", "con_name": "丙"},
        ])
        insert_stock_basic(isolated_env, [
            {"ts_code": "600001.SH", "name": "甲"}, {"ts_code": "600002.SH", "name": "乙"}, {"ts_code": "600003.SH", "name": "丙"},
        ])
        trade_date = dates[-1]
        write_daily_fixture(isolated_env, "daily", trade_date, [
            _daily_row("600001.SH", pct_chg=2.0),
            _daily_row("600002.SH", pct_chg=9.9),
            _daily_row("600003.SH", pct_chg=5.0),
        ])
        report = compute_intel(trade_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        leaders = report.top_themes[0].leaders
        assert [l.ts_code for l in leaders] == ["600002.SH", "600003.SH"]   # 9.9% > 5.0% > 2.0%,取前二

    def test_missing_ths_daily_yields_empty_themes_not_crash(self, isolated_env):
        insert_trade_cal(isolated_env, business_days(date(2026, 3, 2), 2))
        report = compute_intel(date(2026, 3, 2), parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert report.top_themes == []
        assert report.theme_persistence_distribution == {}


class TestMvPreferenceAndLimitRegimePreference:
    def test_mv_preference_buckets_by_total_mv(self, isolated_env):
        d = date(2026, 3, 2)
        insert_trade_cal(isolated_env, [d])
        write_daily_fixture(isolated_env, "limit_derived", d, [
            _limit_row("600001.SH", is_up=True), _limit_row("600002.SH", is_up=True), _limit_row("600003.SH", is_up=True),
        ])
        write_daily_fixture(isolated_env, "daily_basic", d, [
            {"ts_code": "600001.SH", "total_mv": 300_000.0},      # <50亿
            {"ts_code": "600002.SH", "total_mv": 20_000_000.0},   # ≥1000亿
            {"ts_code": "600003.SH", "total_mv": 700_000.0},      # 50-100亿
        ])
        report = compute_intel(d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        # mv_preference 固定展示全部 5 个市值桶(即便某桶当日 0 只),保证跨日可比;
        # 涨跌停制度偏好(下一测试)则只展示当日实际出现过的幅度值——两者行为刻意
        # 不同,前者是稳定的散户市值分档taxonomy,后者是当日实际制度分布,见
        # intel.py `_mv_preference`/`_limit_regime_preference` 注释。
        by_label = {b.label: b.count for b in report.mv_preference}
        assert by_label == {"<50亿": 1, "50-100亿": 1, "100-300亿": 0, "300-1000亿": 0, "≥1000亿": 1}
        for b in report.mv_preference:
            if b.count > 0:
                assert b.pct_of_total == pytest.approx(1 / 3)

    def test_limit_regime_preference_buckets_by_limit_pct(self, isolated_env):
        d = date(2026, 3, 2)
        insert_trade_cal(isolated_env, [d])
        write_daily_fixture(isolated_env, "limit_derived", d, [
            _limit_row("A.SH", is_up=True, limit_pct=0.10),
            _limit_row("B.SZ", is_up=True, limit_pct=0.20),
            _limit_row("C.SZ", is_up=True, limit_pct=0.20),
            _limit_row("D.BJ", is_up=True, limit_pct=0.30),
        ])
        report = compute_intel(d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        by_label = {b.label: b.count for b in report.limit_regime_preference}
        assert by_label == {"10cm": 1, "20cm": 2, "30cm": 1}

    def test_missing_daily_basic_degrades_mv_preference_only(self, isolated_env):
        d = date(2026, 3, 2)
        insert_trade_cal(isolated_env, [d])
        write_daily_fixture(isolated_env, "limit_derived", d, [_limit_row("600001.SH", is_up=True)])
        report = compute_intel(d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert report.mv_preference == []
        assert report.limit_regime_preference != []   # 涨跌停制度偏好不依赖 daily_basic,不受影响


class TestOverallDegradation:
    def test_all_sources_missing_returns_empty_report_not_crash(self, isolated_env):
        insert_trade_cal(isolated_env, business_days(date(2026, 3, 2), 2))
        report = compute_intel(date(2026, 3, 3), parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert report.gainers == [] and report.losers == []
        assert report.limit_up_ladder == [] and report.limit_down == []
        assert report.market_volume is None
        assert report.top_themes == []
        assert report.mv_preference == [] and report.limit_regime_preference == []
        assert len(report.warnings) > 0   # 有留痕,不是悄无声息地空

    def test_evidence_note_always_present(self, isolated_env):
        insert_trade_cal(isolated_env, business_days(date(2026, 3, 2), 2))
        report = compute_intel(date(2026, 3, 3), parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        assert "弱证据" in report.evidence_note and "强证据" in report.evidence_note

    def test_to_public_dict_is_json_safe_camel_case(self, isolated_env):
        d = date(2026, 3, 2)
        insert_trade_cal(isolated_env, [d])
        write_daily_fixture(isolated_env, "daily", d, [_daily_row("600001.SH", pct_chg=5.0)])
        report = compute_intel(d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
        d_out = report.to_public_dict()
        assert d_out["tradeDate"] == "2026-03-02"
        assert "limitUpLadder" in d_out and "topThemes" in d_out and "mvPreference" in d_out
        import json
        json.dumps(d_out)   # 不抛异常即证明全 JSON 安全(无 date/dataclass 残留)
