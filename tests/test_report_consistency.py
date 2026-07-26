"""历史回放 + 同码一致性单测(plan 2.6)。核心断言:

① `report/candidates.py`(喂"今日"单日面板,`build_research_panel(d,d,...)`)与
   `strategy/momentum.py`(喂历史区间面板,`MomentumStrategy` 内部候选池)在**同一
   交易日、同一规则**下,选出的候选集合【完全一致】——证明报告管线与回测策略是
   真同码(§2.6/§3.8),不是两份看起来相似的独立实现;且不止验证最后一天,区间
   中段某天也要一致。
② 报告管线能对历史区间内**任意**交易日回放,并随行情逐日变化给出【日期相关】的
   正确结果(不是对任何输入都返回同一个罐头答案——用"早期未回调=排除、末日回调
   =入选"的真实价格路径差异证明这一点)。
"""

from __future__ import annotations

import pytest

from tests.conftest import seed_active_rule_v1, seed_synthetic_market

import neckline.report.pipeline as pipeline_mod
from neckline.report.candidates import build_candidates
from neckline.strategy import brain
from neckline.strategy.features import build_research_panel
from neckline.strategy.momentum import MomentumConfig, MomentumStrategy

pytestmark = pytest.mark.usefixtures("isolated_env")


def _report_codes(trade_date, rule, isolated_env):
    cands = build_candidates(trade_date, rule, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    return {c.ts_code for c in cands}


def _backtest_codes(trade_date, cfg, start, isolated_env):
    full_panel = build_research_panel(start, trade_date, with_forward=False, parquet_dir=isolated_env.parquet_dir)
    strat = MomentumStrategy(full_panel, cfg, initial_cash=120000.0)
    day_slice = strat._by_date.get(trade_date)
    return set(day_slice["ts_code"].to_list()) if day_slice is not None else set()


class TestSameCodeConsistency:
    def test_report_matches_backtest_on_final_day(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        active = brain.get_active(db_path=isolated_env.db_path)
        cfg = MomentumConfig(**active.rule["config"])

        report_codes = _report_codes(report_date, active.rule, isolated_env)
        backtest_codes = _backtest_codes(report_date, cfg, dates[0], isolated_env)

        assert report_codes == backtest_codes
        # 熔断线:确实筛出了预期的那一只(浅回调、主板、非ST),不是"两边恰好都是
        # 空集"这种弱重合。
        assert report_codes == {"600001.SH"}

    def test_report_matches_backtest_on_an_earlier_mid_range_day(self, isolated_env):
        """不止最后一天——区间中段某天(600001.SH 仍在单调上涨、尚未触发 pullback)
        也要同码一致,证明不是靠"永远选中"蒙混过关。"""
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        mid_date = dates[24]
        active = brain.get_active(db_path=isolated_env.db_path)
        cfg = MomentumConfig(**active.rule["config"])

        report_codes = _report_codes(mid_date, active.rule, isolated_env)
        backtest_codes = _backtest_codes(mid_date, cfg, dates[0], isolated_env)

        assert report_codes == backtest_codes
        assert "600001.SH" not in report_codes  # 仍在上涨,未回调,两条跑道都应正确排除

    def test_st_and_gem_excluded_identically_on_both_tracks(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        active = brain.get_active(db_path=isolated_env.db_path)
        cfg = MomentumConfig(**active.rule["config"])

        report_codes = _report_codes(report_date, active.rule, isolated_env)
        backtest_codes = _backtest_codes(report_date, cfg, dates[0], isolated_env)

        for excluded in ("600002.SH", "300001.SZ"):
            assert excluded not in report_codes
            assert excluded not in backtest_codes


class TestHistoricalReplayAcrossMultipleDays:
    def test_replay_is_date_sensitive_not_a_canned_answer(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        active = brain.get_active(db_path=isolated_env.db_path)

        early_codes = _report_codes(dates[24], active.rule, isolated_env)
        final_codes = _report_codes(dates[-1], active.rule, isolated_env)

        assert early_codes != final_codes
        assert "600001.SH" not in early_codes
        assert "600001.SH" in final_codes

    def test_build_report_end_to_end_replays_several_historical_dates(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)

        # v1.3-③-C3:候选生成改情报筛选管线后,候选**集合**由 step① 板块成员驱动
        # (较 K1 pullback 更稳定,不再逐日增减);候选**选择**的日期敏感性由本文件
        # `test_replay_is_date_sensitive_not_a_canned_answer`(build_candidates K1 直测)
        # 覆盖。此处改验:管线按日回放、日期正确落到报告头、候选产出且其**展示分**随
        # 行情逐日变化(600001.SH 的贴前高度=展示分 dist_from_high_20d 逐日不同)——
        # 证明日期参数真被用,不是罐头答案。
        seen_score_snapshots = []
        for d in (dates[20], dates[25], dates[-1]):
            bundle = pipeline_mod.build_report(d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
            assert bundle.trade_date == d
            assert bundle.markdown.startswith(f"# Neckline 盘后报告 · {d.isoformat()}")
            assert "600001.SH" in [c.ts_code for c in bundle.candidates]   # 储能成分过安检,逐日入选
            seen_score_snapshots.append(
                tuple(sorted((c.ts_code, round(c.score, 1)) for c in bundle.candidates))
            )

        # 三个不同回放日的候选(代码+展示分)快照不应全同(否则说明日期参数没被真正使用)
        assert len(set(seen_score_snapshots)) > 1
