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

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import (
    markdown_modulo_generated_at,
    seed_active_rule_v1,
    seed_synthetic_market,
)

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
            # 🔴 必须先剥掉 `generated_at`(秒精度墙钟)再进 set:三次调用的戳本来就
            # 各不相同,不剥的话 `len(set(...)) == 3` **哪怕报告是个罐头答案也恒成立**
            # —— 那正是本用例要证伪的东西,不剥就是一条假绿(§七 P1-36)。
            snapshots.append(markdown_modulo_generated_at(bundle))

        # 三个不同回放日的报告全文不应全同(否则说明日期参数没被真正使用)。
        # ⚠ 合成夹具不含 `limit_derived`/`index_daily`,情绪与情报两节恒为降级占位,
        # 故这里只能锚到「日期 + 随行情变化的强势板块节」;⑭-A 上篮子日报后应把锚
        # 换成篮子节(那才是真正的选股输出)。
        assert len(set(snapshots)) == 3

    def test_replay_of_the_same_day_twice_is_identical(self, isolated_env, monkeypatch):
        """同一天重跑两次,markdown 逐字节一致(§2.6 回放可复现)。

        ⚠ **原 docstring 写的「`generated_at` 是审计戳、不进 markdown 正文,故可直接
        比全文」是错的**(§七 P1-36):它**就印在报告头第一行**,且是秒精度墙钟 ——
        本用例因此从 `1161441` 写下那天起就一直间歇红(裸比全文时,两次调用跨过整秒
        边界即失败)。现按 `markdown_modulo_generated_at` 归一化那一个戳后再逐字节比,
        **除它以外的任何不确定性照样让本用例红**。"""
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
        assert markdown_modulo_generated_at(a) == markdown_modulo_generated_at(b)

    def test_replay_is_identical_even_across_a_second_boundary(self, isolated_env, monkeypatch):
        """§七 P1-36 的**回归判据**:把「跨整秒边界」从"碰运气"变成"必然发生"。

        上一条用例靠两次调用**恰好落在同一秒**才绿 —— 那是概率,不是判据(失败率 ≈
        两次调用间隔 ÷ 1 秒:孤立跑约 2~3%,全量跑机器忙、间隔被拉长到约 25%)。本条
        把墙钟钉死成**每次 `now()` 前进整整 1 秒**,于是两次 build **必定**拿到不同的
        `generated_at`,再断言:

        · 归一化后逐字节一致 → 报告内容本身**确实**可复现(§2.6),不确定性只此一处;
        · 裸比全文**必定不等** → 证明归一化不是空操作、`generated_at` 真的进了正文
          (否则本断言会红,提醒后人前提变了)。

        ⚠ 钉墙钟是拿 `datetime` 的**子类**替换 `pipeline_mod.datetime`,于是该模块内
        `isinstance(x, datetime)` 对真 `datetime` 实例会变假(`_jsonable` 就有这么一处)
        —— 本用例 `save=False` 走不到那条路径;**要改成 `save=True` 得先换一种钉法**。
        """
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)

        base = datetime(2026, 8, 11, 8, 35, 59, tzinfo=timezone.utc)
        ticks = itertools.count()

        class _SteppingClock(datetime):
            """`now()` 每被调用一次就前进 1 秒(其余行为原样继承 `datetime`)。"""

            @classmethod
            def now(cls, tz=None):  # noqa: D102
                return base + timedelta(seconds=next(ticks))

        monkeypatch.setattr(pipeline_mod, "datetime", _SteppingClock)

        d = dates[-1]
        a = pipeline_mod.build_report(
            d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        b = pipeline_mod.build_report(
            d, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert a.generated_at != b.generated_at, "钉死的墙钟没生效,本用例失去意义"
        assert a.markdown != b.markdown, (
            "裸比全文竟然相等 —— 说明 `generated_at` 已不在 markdown 正文里,"
            "`markdown_modulo_generated_at` 已成空操作,请同步修正它与所有调用方。"
        )
        assert markdown_modulo_generated_at(a) == markdown_modulo_generated_at(b)
