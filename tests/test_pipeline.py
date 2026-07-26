"""报告管线编排单测(plan 2.5)。用 `tests.conftest.seed_synthetic_market`(合成
多票多日行情 + ST/创业板剔除熔断线)跑通完整 I/O 接线:大脑读取 -> 候选评分 ->
LLM 审判(强制走无 provider / 强制走 mock provider 两条路径,均不依赖真实 `.env`
状态)-> 落库 -> markdown 渲染。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import httpx
import pytest

from tests.conftest import seed_active_rule_v1, seed_synthetic_market

import neckline.report.pipeline as pipeline_mod
from neckline.llm.judge import VERDICT_INACTIVE, VERDICT_PASS
from neckline.llm.providers.glm import GLMProvider
from neckline.report import store

pytestmark = pytest.mark.usefixtures("isolated_env")


class TestNoActiveBrainVersion:
    def test_raises_clear_error_when_brain_empty(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        with pytest.raises(RuntimeError, match="策略大脑无现役版本"):
            pipeline_mod.build_report(
                dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
            )


class TestBuildReportDegradesWithoutLLM:
    def test_end_to_end_without_llm_provider(self, isolated_env, monkeypatch):
        # 强制"无 provider"路径,不依赖真实 .env 是否配了 key(确定性)
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )

        # 熔断线:600001.SH(主板,回调)入池;600002.SH(ST)/300001.SZ(创业板)被剔除
        codes = [c.ts_code for c in bundle.candidates]
        assert "600001.SH" in codes
        assert "600002.SH" not in codes
        assert "300001.SZ" not in codes

        assert bundle.strategy_version == "v1"
        for c in bundle.candidates:
            jr = bundle.judged.get(c.ts_code)
            assert jr is not None
            assert jr.verdict == VERDICT_INACTIVE
            assert jr.degraded is True
        assert "未激活" in bundle.markdown
        assert bundle.sentiment.position_quota in ("满额", "半额", "休息")

        # 落库可读回
        loaded_report = store.load_report(report_date, db_path=isolated_env.db_path)
        assert loaded_report is not None
        assert loaded_report["strategy_version"] == "v1"
        assert any(c["ts_code"] == "600001.SH" for c in loaded_report["candidates"])
        assert "raw" not in loaded_report["candidates"][0]  # public_dict 已剔除内部特征行

        loaded_judgments = store.load_llm_judgments(report_date, db_path=isolated_env.db_path)
        assert len(loaded_judgments) == len(bundle.candidates)
        assert all(j["degraded"] for j in loaded_judgments)


class TestBuildReportWithMockLLMProvider:
    def test_explicit_provider_bypasses_env_and_gets_judged(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant",
                    "content": "搜索到该股票近期有产业催化消息,逻辑站得住。\n结论:通过",
                }}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        bundle = pipeline_mod.build_report(
            report_date, llm_provider=provider, llm_transport=httpx.MockTransport(handler),
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )

        assert len(bundle.candidates) >= 1
        jr = bundle.judged["600001.SH"]
        assert jr.verdict == VERDICT_PASS
        assert jr.degraded is False
        assert jr.provider == "glm"
        assert "✅ 通过" in bundle.markdown


class TestSaveFalseDoesNotWriteStore:
    def test_no_persistence_when_save_false(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert store.load_report(report_date, db_path=isolated_env.db_path) is None
        assert store.load_llm_judgments(report_date, db_path=isolated_env.db_path) == []


class TestInquiryPoolConsumption:
    """§2.5 闭环报告侧(4E 接线,v1.1-D 修复消费窗口):`build_report` 消费
    `inquiry_pool`——「初审通过」的票强制并入当晚候选评分 universe(只扩输入,不改
    评分)。用 300001.SZ(创业板,报告日 rule v1 主板 only 会剔除)作「问询台放行但
    报告 mask 会排除」的代理,验证它经海选池被强制纳入,而未入池的 600002.SH
    (*ST)仍被剔除。

    v1.1-D 起消费判据从「入池当日(trade_date)== 报告日」改为「待消费
    (consumed_report_date IS NULL)∪ 已被本报告日消费过(幂等补跑)」——下面
    `TestPendingConsumptionWindow` 覆盖任务原文点名的四个场景:①16:35 后入池票
    次日报告纳入(跨日边界);②同一票不被两份报告重复计入;③报告补跑(同日重算)
    幂等不丢不重;④空池 noop 零回归(本类 `test_no_inquiry_pool_is_unchanged`)。"""

    def test_inquiry_pool_ticket_forced_into_candidates(self, isolated_env, monkeypatch):
        from neckline.api.stores import add_to_inquiry_pool

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        # 基线:未入池时 300001.SZ 不在候选(被主板 only mask 剔除)
        base = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert "300001.SZ" not in [c.ts_code for c in base.candidates]

        # 入海选池后重跑:300001.SZ 被强制纳入,600002.SH(*ST,未入池)仍被剔除
        add_to_inquiry_pool(report_date, "300001.SZ", name="示例丙",
                            reason="问询台初审通过", db_path=isolated_env.db_path)
        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        codes = [c.ts_code for c in bundle.candidates]
        assert "300001.SZ" in codes          # 海选池票强制纳入
        assert "600001.SH" in codes          # 原本就通过的票不受影响
        assert "600002.SH" not in codes      # 未入池的 *ST 仍剔除

    def test_no_inquiry_pool_is_unchanged(self, isolated_env, monkeypatch):
        # 空池 → 与阶段2 行为一致(零回归)
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        codes = [c.ts_code for c in bundle.candidates]
        assert "600001.SH" in codes
        assert "300001.SZ" not in codes and "600002.SH" not in codes

    def test_pending_entry_consumed_regardless_of_stored_trade_date(self, isolated_env, monkeypatch):
        """核心根因验证(§v1.1-D.1):入池当日(`trade_date` 列)与该消费它的报告日
        不再需要相等——只要 `consumed_report_date` 仍是 NULL(待消费),不论
        `trade_date` 存的是哪天,下一份生成的报告就必然收进来(旧写法要求两者
        相等,是"16:35 报告已生成后问询通过的票永久掉缝"这个生产真洞的根因)。"""
        from neckline.api.stores import add_to_inquiry_pool

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        yesterday = dates[-2]   # 故意用一个与 report_date 不同的 trade_date 入池
        add_to_inquiry_pool(yesterday, "300001.SZ", name="示例丙", db_path=isolated_env.db_path)

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        assert "300001.SZ" in [c.ts_code for c in bundle.candidates]

    def test_report_save_marks_pending_entries_consumed(self, isolated_env, monkeypatch):
        """报告落库成功后,待消费的 `inquiry_pool` 行被标记为「已被本报告日消费」
        (`consumed_report_date` 从 NULL 变成 report_date)。"""
        import sqlite3

        from neckline.api.stores import add_to_inquiry_pool

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        add_to_inquiry_pool(report_date, "300001.SZ", db_path=isolated_env.db_path)

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        conn = sqlite3.connect(str(isolated_env.db_path))
        row = conn.execute(
            "SELECT consumed_report_date FROM inquiry_pool WHERE ts_code=?", ("300001.SZ",)
        ).fetchone()
        conn.close()
        assert row[0] == report_date.strftime("%Y%m%d")

    def test_save_false_does_not_mark_consumed(self, isolated_env, monkeypatch):
        """`save=False`(预览/单测)绝不应有"标记消费"这个副作用——报告根本没有
        落库,不该悄悄消耗掉海选池的票。"""
        from neckline.api.stores import add_to_inquiry_pool, load_pending_inquiry_codes

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        add_to_inquiry_pool(report_date, "300001.SZ", db_path=isolated_env.db_path)

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        pending = load_pending_inquiry_codes(report_date, db_path=isolated_env.db_path)
        assert any(p["ts_code"] == "300001.SZ" for p in pending)   # 仍待消费

    def test_rerun_same_day_report_idempotently_reincludes(self, isolated_env, monkeypatch):
        """报告补跑(同日重算)幂等:同一天的报告重新生成时,已被这天消费过的票
        仍应被重新纳入(不能"生成过一次就再也拿不到"——ECS 补跑同日报告的真实
        场景,4E 已踩过)。"""
        from neckline.api.stores import add_to_inquiry_pool

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        add_to_inquiry_pool(report_date, "300001.SZ", db_path=isolated_env.db_path)

        bundle1 = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        assert "300001.SZ" in [c.ts_code for c in bundle1.candidates]

        # 补跑(同一天再生成一次,不重新添加入池)
        bundle2 = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        assert "300001.SZ" in [c.ts_code for c in bundle2.candidates]   # 幂等重纳,不丢


class TestPendingConsumptionWindowStoreLevel:
    """`api.stores.load_pending_inquiry_codes`/`mark_inquiry_pool_consumed` 的直接
    单测(不依赖完整合成市场,纯 DB 层验证跨日边界 + 消费隔离)。"""

    def test_cross_day_boundary_consumed_entry_not_reused_by_earlier_day(self, isolated_env):
        """`consumed_report_date` 一旦被某天标记,查询【更早】的报告日不应再把它
        当"待消费"(同一票不被两份报告重复计入 forced)。"""
        from neckline.api.stores import add_to_inquiry_pool, load_pending_inquiry_codes, mark_inquiry_pool_consumed

        day1, day2 = date(2026, 7, 20), date(2026, 7, 21)
        add_to_inquiry_pool(day1, "300001.SZ", db_path=isolated_env.db_path)
        # day2 的报告消费了它(跨日边界:16:35 后入池票次日报告纳入)
        assert any(p["ts_code"] == "300001.SZ" for p in load_pending_inquiry_codes(day2, db_path=isolated_env.db_path))
        mark_inquiry_pool_consumed(day2, db_path=isolated_env.db_path)
        # day1(更早一天)不应再把它当待消费(已被 day2 消费,不重复计入)
        assert not any(p["ts_code"] == "300001.SZ" for p in load_pending_inquiry_codes(day1, db_path=isolated_env.db_path))
        # day2 自己(幂等补跑)仍能拿到
        assert any(p["ts_code"] == "300001.SZ" for p in load_pending_inquiry_codes(day2, db_path=isolated_env.db_path))

    def test_new_entry_after_consumption_unaffected_by_already_consumed_ones(self, isolated_env):
        """消费后新入池的票不受已消费票影响——各自独立按 NULL/日期匹配,互不干扰。"""
        from neckline.api.stores import add_to_inquiry_pool, load_pending_inquiry_codes, mark_inquiry_pool_consumed

        day1, day2 = date(2026, 7, 20), date(2026, 7, 21)
        add_to_inquiry_pool(day1, "AAAA.SZ", db_path=isolated_env.db_path)
        mark_inquiry_pool_consumed(day1, db_path=isolated_env.db_path)   # AAAA 已被 day1 消费

        add_to_inquiry_pool(day2, "BBBB.SZ", db_path=isolated_env.db_path)   # day2 新入池一票
        pending_day2 = {p["ts_code"] for p in load_pending_inquiry_codes(day2, db_path=isolated_env.db_path)}
        assert "BBBB.SZ" in pending_day2
        assert "AAAA.SZ" not in pending_day2   # 已消费的不干扰新票

    def test_never_consumed_entry_stays_pending_across_many_days(self, isolated_env):
        """一票迟迟没被任何报告消费(如报告脚本连续几天没跑)→ 无论查询哪一天,
        只要还没消费过,一直待消费,不会因为"查询过"就被误判消费。"""
        from neckline.api.stores import add_to_inquiry_pool, load_pending_inquiry_codes

        add_to_inquiry_pool(date(2026, 7, 1), "CCCC.SZ", db_path=isolated_env.db_path)
        for probe_day in (date(2026, 7, 2), date(2026, 7, 5), date(2026, 7, 10)):
            pending = {p["ts_code"] for p in load_pending_inquiry_codes(probe_day, db_path=isolated_env.db_path)}
            assert "CCCC.SZ" in pending


class TestWatchlistCheckWiring:
    """v1.1-C.3 自选体检接入 `build_report`(独立一节,不改候选评分/不进候选榜,
    见 `neckline.report.watchlist_check` 的评分单测本身在 `test_watchlist_check.py`;
    本类只测「接线」——是否真的被 `build_report` 调用、落库、回放)。"""

    def test_watchlist_check_appears_in_bundle_and_report_independent_of_candidates(self, isolated_env, monkeypatch):
        from neckline.watchlist import add_watchlist

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        # 300001.SZ 是创业板,rule v1 主板 only → 报告候选会剔除它;自选体检不受
        # mask 约束,仍应给出评估——证明"独立一节,不进候选榜"。
        add_watchlist("300001.SZ", db_path=isolated_env.db_path)

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        codes = {w.ts_code for w in bundle.watchlist_check}
        assert codes == {"300001.SZ"}
        assert "300001.SZ" not in [c.ts_code for c in bundle.candidates]

        loaded = store.load_report(report_date, db_path=isolated_env.db_path)
        assert loaded["watchlist"][0]["ts_code"] == "300001.SZ"

    def test_empty_watchlist_yields_empty_check_list(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        bundle = pipeline_mod.build_report(
            dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert bundle.watchlist_check == []

    def test_status_change_diff_across_two_reports(self, isolated_env, monkeypatch):
        """跨两份报告的真实 diff:600001.SH 在 `dates[-2]`(合成行情当日未回调,
        pullback 不触发)与 `dates[-1]`(report_date,合成行情当日小幅回调,
        pullback 触发)之间买点触发状态翻转 → 第二份报告应判定 `statusChanged=True`
        (`load_watchlist_snapshot_before` 正确取到"上一份"而非本次自己)。"""
        from neckline.watchlist import add_watchlist

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        add_watchlist("600001.SH", db_path=isolated_env.db_path)

        day1, day2 = dates[-2], dates[-1]
        b1 = pipeline_mod.build_report(
            day1, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        w1 = {w.ts_code: w for w in b1.watchlist_check}["600001.SH"]
        assert w1.buy_point_triggered is False   # day1 未回调,pullback 不触发

        b2 = pipeline_mod.build_report(
            day2, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        w2 = {w.ts_code: w for w in b2.watchlist_check}["600001.SH"]
        assert w2.buy_point_triggered is True    # day2(report_date)回调,触发
        assert w2.status_changed is True         # 与上一份报告(day1)相比翻转


class TestIntelAndSectorMoneyflowWiring:
    """v1.3-③ C1(情报件)/C2(板块资金流)接入 `build_report`(硬要求④:任一子项
    /整段异常都不阻断主报告)。字段本身的评分单测在 `test_intel.py`/
    `test_sector_moneyflow.py`;本类只测「接线」+「不阻断」。"""

    def test_bundle_carries_intel_and_sector_moneyflow_degrading_gracefully(self, isolated_env, monkeypatch):
        """合成市场夹具(`seed_synthetic_market`)不含 limit_derived/index_daily/
        moneyflow_dc/ths_* 数据源 → 情报节各子项应优雅降级为空 + 记警告,但报告
        整体必须正常生成(不阻断,硬要求④的常态验证——不需要特意造异常)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        assert bundle.intel is not None
        assert bundle.intel.gainers == []          # 无 limit_derived/daily 覆盖该情报所需列,优雅降级
        assert len(bundle.intel.warnings) > 0        # 降级留痕,不是悄无声息
        assert bundle.sector_moneyflow is not None
        assert bundle.sector_moneyflow.available is False   # 无 moneyflow_dc 数据

        loaded = store.load_report(report_date, db_path=isolated_env.db_path)
        assert "warnings" in loaded["intel"]
        assert loaded["sector_moneyflow"]["available"] is False

    def test_intel_exception_does_not_block_main_report(self, isolated_env, monkeypatch):
        """情报节(C1)编排逻辑本身抛异常(非子项级降级,是 `compute_intel` 整体炸)
        时,`build_report` 仍必须成功产出报告(硬要求④外层保险丝,见
        `pipeline.py::build_report` 的 try/except + `empty_intel_report` 兜底)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        monkeypatch.setattr(pipeline_mod, "compute_intel", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        assert "600001.SH" in [c.ts_code for c in bundle.candidates]   # 主报告未受影响
        assert bundle.intel is not None
        assert "计算异常" in bundle.intel.warnings[0]

    def test_sector_moneyflow_exception_does_not_block_main_report(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        monkeypatch.setattr(
            pipeline_mod, "compute_sector_moneyflow",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        assert "600001.SH" in [c.ts_code for c in bundle.candidates]   # 主报告未受影响
        assert bundle.sector_moneyflow is not None
        assert bundle.sector_moneyflow.available is False
        assert "计算异常" in bundle.sector_moneyflow.unavailable_reason

    def test_markdown_includes_intel_section_headers(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        bundle = pipeline_mod.build_report(
            dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert "## 情报 · 复盘情报件(C1)" in bundle.markdown
        assert "## 情报 · 板块资金流(C2,拥挤参考,非选股信号)" in bundle.markdown


class TestTopNSplit:
    def test_only_top_n_judged_candidates_get_llm_called(self, isolated_env, monkeypatch):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "分析。\n结论:通过"}}],
            })

        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        provider = GLMProvider(api_key="sk-xxx")

        # 本合成行情只有 1 只票能通过 rule v1(600001.SH),top_n_judged=0 应确保
        # 一次 LLM 调用都不发生(验证"后N只不耗LLM"的边界:0 是最严格的边界值)。
        pipeline_mod.build_report(
            report_date, llm_provider=provider, llm_transport=httpx.MockTransport(handler),
            top_n_judged=0, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )
        assert calls["n"] == 0


class TestMissedEntryHint:
    """漏录兜底(plan v1.1-B.4):当日买点哨兵触发过但台账无补录 → 提示;否则空串。
    **不改评分**,纯只读旁路;GET /report 与 build_report 共用同一函数(单一源)。"""

    def _record_entry(self, db, trade_date, code="600001.SH"):
        from neckline.sentinel.dedup import record_pushed
        record_pushed(trade_date, "entry", code, "trigger", payload={"body": "买点确认"}, db_path=db)

    def test_hint_when_entry_fired_but_no_open(self, isolated_env):
        from neckline.report.pipeline import compute_missed_entry_hint
        td = date(2026, 7, 21)
        db = isolated_env.db_path
        self._record_entry(db, td)
        hint = compute_missed_entry_hint(td, db_path=db)
        assert hint and "1 只候选触达买点" in hint

    def test_no_hint_when_position_recorded(self, isolated_env):
        from neckline.report.pipeline import compute_missed_entry_hint
        from neckline.sentinel.positions import open_position
        td = date(2026, 7, 21)
        db = isolated_env.db_path
        self._record_entry(db, td)
        open_position("600001.SH", 10.0, 100, td, db_path=db)   # 当日已补录
        assert compute_missed_entry_hint(td, db_path=db) == ""

    def test_no_hint_when_no_entry_events(self, isolated_env):
        from neckline.report.pipeline import compute_missed_entry_hint
        td = date(2026, 7, 21)
        assert compute_missed_entry_hint(td, db_path=isolated_env.db_path) == ""

    def test_build_report_bundle_carries_hint(self, isolated_env):
        from neckline.report.pipeline import build_report
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        # 当日买点事件触发,但无持仓补录 → bundle.missed_entry_hint 非空
        self._record_entry(isolated_env.db_path, report_date, code="600001.SH")
        bundle = build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert bundle.missed_entry_hint and "候选触达买点" in bundle.missed_entry_hint
