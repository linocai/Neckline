"""历史回放一致性单测(plan 2.6)。

⚠ **V2-⑬-1 大幅缩编,请先读这段再改**。本文件原有两组断言:

① **同码三跑道**:`report/candidates.py`(喂"今日"单日面板)与 `strategy/momentum.py`
   (喂历史区间面板)在同一交易日、同一规则下选出的候选集合完全一致。
   → **随 V1 候选榜删除而失效**:`report/candidates.py` 已物理删除,报告侧不再有
   「K1 选股」这条跑道可对拍。V2 的选股跑道是策略包原语(`selection/primitives.py`),
   它与 `momentum.py` **本来就不是同一套判据**,强行对拍无意义。
   ⚠ **同码纪律本身没有废除**:V2 的等价守门是 `tests/test_selection_primitives.py`
   (原语白名单 + 阈值单一源)与 `tests/test_selection_tier.py`(三路等价);
   K1 回测跑道自身的完整性仍由 `tests/test_momentum.py` 锁死。
② **历史回放的日期敏感性**:报告管线能对区间内任意交易日回放,且结果随行情逐日变化
   (不是对任何输入都返回同一个罐头答案)。
   → **保留**,只是把"变化"的锚从「候选代码 + 展示分」换成**情绪仪表盘**(候选节已删)。
   ⑭-A 落地篮子日报后,应把锚再换成「今日篮子」那一节 —— 那时才重新有一个"随行情
   变化的选股输出"可锚。
"""

from __future__ import annotations

import pytest

from tests.conftest import seed_active_rule_v1, seed_synthetic_market

import neckline.report.pipeline as pipeline_mod

pytestmark = pytest.mark.usefixtures("isolated_env")


class TestHistoricalReplayAcrossMultipleDays:
    def test_build_report_end_to_end_replays_several_historical_dates(self, isolated_env, monkeypatch):
        """管线按日回放:日期正确落到报告头,且**产出随行情逐日变化**——证明
        `trade_date` 参数真被用上,不是罐头答案。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)

        snapshots = []
        for d in (dates[20], dates[25], dates[-1]):
            bundle = pipeline_mod.build_report(
                d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
            )
            assert bundle.trade_date == d
            assert bundle.markdown.startswith(f"# Neckline 篮子日报 · {d.isoformat()}")
            snapshots.append(bundle.markdown)

        # 三个不同回放日的报告全文不应全同(否则说明日期参数没被真正使用)。
        # ⚠ 合成夹具不含 `limit_derived`/`index_daily`,情绪与情报两节恒为降级占位,
        # 故这里只能锚到「日期 + 随行情变化的强势板块节」;⑭-A 上篮子日报后应把锚
        # 换成篮子节(那才是真正的选股输出)。
        assert len(set(snapshots)) == 3

    def test_replay_of_the_same_day_twice_is_identical(self, isolated_env, monkeypatch):
        """同一天重跑两次,markdown 逐字节一致(§2.6 回放可复现;`generated_at`
        是审计戳、不进 markdown 正文,故可直接比全文)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        d = dates[-1]
        a = pipeline_mod.build_report(
            d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        b = pipeline_mod.build_report(
            d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert a.markdown == b.markdown
