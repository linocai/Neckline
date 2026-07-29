"""报告管线编排单测(plan 2.5)。用 `tests.conftest.seed_synthetic_market`(合成
多票多日行情 + ST/创业板剔除熔断线)跑通完整 I/O 接线:大脑读取 -> 候选评分 ->
LLM 审判(强制走无 provider / 强制走 mock provider 两条路径,均不依赖真实 `.env`
状态)-> 落库 -> markdown 渲染。"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date

import httpx
import pytest

from tests.conftest import (
    business_days,
    insert_stock_basic,
    insert_trade_cal,
    seed_active_rule_v1,
    seed_synthetic_market,
)

import neckline.report.news_alerts as news_alerts_mod
import neckline.report.pipeline as pipeline_mod
from neckline import watchlist as watchlist_store
from neckline.data.tushare_client import TushareResult
from neckline.llm.judge import VERDICT_INACTIVE, VERDICT_PASS, VERDICT_VETO
from neckline.llm.providers.glm import GLMProvider
from neckline.report import reference_plan_store
from neckline.report import store
from neckline.report.candidates import Candidate
from neckline.report.reference_plan import STATUS_OK, STATUS_VETOED
from neckline.sentinel import positions as pos_store

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


class TestDispatchAlertsWiring:
    """v1.5-④-A1 自选票 K4 派发警示接入 `build_report`(领域逻辑单测见
    `test_watchlist_check.py::TestAttachDispatchAlerts`;本类只测「接线」——是否真的
    被调用、携带正确参数、异常时不阻断主报告(保险丝惯例同 `TestExecHintWiring`)。"""

    def test_attach_dispatch_alerts_called_with_db_path(self, isolated_env, monkeypatch):
        """spy 断言 `build_report` 确实调用了 `attach_dispatch_alerts`(不是被 import
        遗漏/静默跳过)且携带正确的 `db_path`/`parquet_dir`。"""
        from neckline.watchlist import add_watchlist

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        captured = {}
        real = pipeline_mod.attach_dispatch_alerts

        def spy(items, trade_date, **kw):
            captured["called"] = True
            captured["db_path"] = kw.get("db_path")
            captured["parquet_dir"] = kw.get("parquet_dir")
            captured["n_items"] = len(items)
            return real(items, trade_date, **kw)

        monkeypatch.setattr(pipeline_mod, "attach_dispatch_alerts", spy)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        add_watchlist("600001.SH", db_path=isolated_env.db_path)
        report_date = dates[-1]

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert captured["called"] is True
        assert captured["db_path"] == isolated_env.db_path
        assert captured["parquet_dir"] == isolated_env.parquet_dir
        assert captured["n_items"] == 1

    def test_dispatch_alerts_exception_does_not_block_main_report(self, isolated_env, monkeypatch):
        """`attach_dispatch_alerts` 整体异常(保险丝范围外的意外)时,`build_report`
        仍必须成功产出报告——自选体检其余字段照出,只是这批票当次没有派发警示
        (维持构造时的默认空列表)。"""
        from neckline.watchlist import add_watchlist

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        monkeypatch.setattr(
            pipeline_mod, "attach_dispatch_alerts",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        add_watchlist("600001.SH", db_path=isolated_env.db_path)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        w = next(w for w in bundle.watchlist_check if w.ts_code == "600001.SH")
        assert w.dispatch_alerts == []   # 保险丝触发,维持默认空(不是半份脏数据)
        assert w.green_light is not None  # 体检其余字段正常产出(不是"整体没跑")


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


class TestNewsAlertsWiring:
    """v1.3-③-C4(消息面扫描)接入 `build_report`(硬要求④:整段异常不阻断主报告)。
    减持/立案/暴雷/监管的评分单测在 `test_news_alerts.py`;本类只测「接线」+
    「扫描对象=持仓∪自选」+「不阻断」+「落库」。"""

    def test_bundle_carries_news_alerts_degrading_gracefully_without_token_or_key(self, isolated_env, monkeypatch):
        """隔离环境默认无 tushare_token / LLM key → 两源均应降级为「未扫描」,但
        报告整体必须正常生成(硬要求④常态验证)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        pos_store.open_position("600001.SH", 10.0, 1000, report_date, db_path=isolated_env.db_path)

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        assert bundle.news_alerts is not None
        assert bundle.news_alerts.items == []
        assert len(bundle.news_alerts.scan_statuses) == 2
        assert all(s.scanned is False for s in bundle.news_alerts.scan_statuses)   # 没扫到,不是扫了没有

    def test_news_alerts_exception_does_not_block_main_report(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        monkeypatch.setattr(
            pipeline_mod, "build_news_alerts", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        assert "600001.SH" in [c.ts_code for c in bundle.candidates]   # 主报告未受影响
        assert bundle.news_alerts is not None
        assert all(not s.scanned for s in bundle.news_alerts.scan_statuses)

    def test_markdown_includes_news_alerts_section_header(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        bundle = pipeline_mod.build_report(
            dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert "## 消息面" in bundle.markdown

    def test_scan_targets_are_positions_and_watchlist_passed_separately(self, isolated_env, monkeypatch):
        """§硬要求「扫描对象=持仓+自选,不是全市场」——用 spy 替身直接断言
        `build_news_alerts` 收到的 `position_codes`/`watchlist_codes` 两个参数,
        不依赖 TuShare/LLM 真调用。2026-07-26 必改后签名从"揉成一个列表"改为
        "分开传入"(LLM 侧墙钟预算持仓优先于自选,靠调用方把两者分开才能保证)——
        本测试同时锁死这一点:不能退化回合并成一个列表再传。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        captured = {}

        def spy(trade_date, position_codes, watchlist_codes, **kw):
            captured["position_codes"] = list(position_codes)
            captured["watchlist_codes"] = list(watchlist_codes)
            return news_alerts_mod.empty_news_alerts_report(trade_date, reason="test-spy")

        monkeypatch.setattr(pipeline_mod, "build_news_alerts", spy)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        pos_store.open_position("600001.SH", 10.0, 1000, report_date, db_path=isolated_env.db_path)
        watchlist_store.add_watchlist("600002.SH", name="*ST示例乙", db_path=isolated_env.db_path)

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        position_only = {c for c, _name in captured["position_codes"]}
        watchlist_only = {c for c, _name in captured["watchlist_codes"]}
        assert position_only == {"600001.SH"}
        assert watchlist_only == {"600002.SH"}
        assert "300001.SZ" not in position_only | watchlist_only   # 不是持仓也不是自选,不应被扫描

    def test_db_path_threaded_through_for_cross_day_dedup(self, isolated_env, monkeypatch):
        """减持类跨日去重要查库(2026-07-26 必改 2)——`pipeline.py` 必须把
        `db_path` 传给 `build_news_alerts`,不能让它退回默认(可能撞到真实生产库)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        captured = {}

        def spy(trade_date, position_codes, watchlist_codes, **kw):
            captured["db_path"] = kw.get("db_path")
            return news_alerts_mod.empty_news_alerts_report(trade_date, reason="test-spy")

        monkeypatch.setattr(pipeline_mod, "build_news_alerts", spy)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        pipeline_mod.build_report(
            dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert captured["db_path"] == isolated_env.db_path

    def test_save_persists_hit_items_and_scan_status_json(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)

        def fake_holdertrade(start, end):
            import pandas as pd
            return TushareResult.success(pd.DataFrame([{
                "ts_code": "600001.SH", "ann_date": end, "holder_name": "张三", "holder_type": "G",
                "in_de": "DE", "change_vol": 10000.0, "change_ratio": 0.2,
            }]))

        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", fake_holdertrade)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        pos_store.open_position("600001.SH", 10.0, 1000, report_date, db_path=isolated_env.db_path)

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )

        from neckline.report.news_alerts_store import load_news_alerts
        rows = load_news_alerts(report_date, db_path=isolated_env.db_path)
        assert len(rows) == 1
        assert rows[0]["ts_code"] == "600001.SH"
        assert rows[0]["category"] == "REDUCTION"

        loaded_report = store.load_report(report_date, db_path=isolated_env.db_path)
        assert len(loaded_report["news_alerts_scan"]) == 2
        tushare_status = next(s for s in loaded_report["news_alerts_scan"] if s["source"] == "tushare_holdertrade")
        assert tushare_status["scanned"] is True


class TestHoldingCheckWiring:
    """v1.5-③-C 持仓体检节接入 `build_report`(需求 9「今日计划拆两块:持仓股 /
    候选列表」的 markdown 落地)。判定逻辑本身(D 计数/时间退出态/K4 命中)覆盖在
    `test_holding_k4_check.py`;渲染文案的各态覆盖在 `test_render.py::
    TestHoldingCheckSection`;本类只测「接线到 `build_report`」+「排在候选之前」+
    「无持仓时节仍在」这三件 `build_report` 专属的事。"""

    def test_markdown_includes_holding_check_section_before_candidates(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        pos_store.open_position("600001.SH", 10.0, 1000, report_date, db_path=isolated_env.db_path)

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert "## 持仓体检" in bundle.markdown
        assert bundle.markdown.index("## 持仓体检") < bundle.markdown.index("## 候选")
        assert "600001.SH" in bundle.markdown.split("## 持仓体检")[1].split("## 候选")[0]

    def test_markdown_holding_section_present_when_no_positions(self, isolated_env, monkeypatch):
        """空持仓仍要有这一节(节在 = 体检跑过了),不是省略。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert "## 持仓体检" in bundle.markdown
        assert "今日无持仓" in bundle.markdown
        assert bundle.holding_k4_check == []


class TestInfoCardSummaryWiring:
    """v1.4-④-B 信息卡摘要接入 `build_report`(硬要求④:整段异常不阻断主报告)。
    摘要本身的计算正确性在 `test_info_card.py` 逐项覆盖;本类只测「接线」+
    「复用已算好的 news_alerts.items/top_list,不二次现拉」+「不阻断」+「落库」。"""

    def test_candidates_carry_info_card_summary_after_build_report(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        summary = cand.info_card_summary
        assert set(summary.keys()) == {"snapshot", "mildBand", "news", "topList"}
        # seed_synthetic_market 铺的 turnover_rate 恒 5.0,零额外读取(直接吃 candidate.raw)。
        assert summary["snapshot"]["turnoverRate"] == pytest.approx(5.0)
        # 不在持仓/自选域(本测试未开仓/未加自选)→ 消息面如实标"不在扫描域"。
        assert summary["news"]["scanned"] is False

        loaded = store.load_report(report_date, db_path=isolated_env.db_path)
        loaded_cand = next(c for c in loaded["candidates"] if c["ts_code"] == "600001.SH")
        assert loaded_cand["info_card_summary"]["snapshot"]["turnoverRate"] == pytest.approx(5.0)

    def test_info_card_summary_exception_does_not_block_main_report(self, isolated_env, monkeypatch):
        """`attach_info_card_summaries` 整体异常(保险丝范围外的意外)时,`build_report`
        仍必须成功产出报告——候选照出,只是这批候选当次没有信息卡摘要(维持构造时的
        默认空 dict)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        monkeypatch.setattr(
            pipeline_mod, "attach_info_card_summaries",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.info_card_summary == {}   # 保险丝触发,维持默认空(不是半份脏数据)

    def test_reuses_already_computed_news_and_top_list_no_second_fetch(self, isolated_env, monkeypatch):
        """`build_report` 第 192 行已算过一次 `top_list_lookup`、本次消息面扫描已产出
        `news_alerts.items`——`attach_info_card_summaries` 必须原样复用这两份,
        用 spy 断言传入的 `news_items`/`top_list` 非 None(不是让被调函数自己现拉)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        captured = {}
        real = pipeline_mod.attach_info_card_summaries

        def spy(candidates, trade_date, **kw):
            captured["news_items"] = kw.get("news_items")
            captured["news_domain_codes"] = kw.get("news_domain_codes")
            captured["top_list"] = kw.get("top_list")
            return real(candidates, trade_date, **kw)

        monkeypatch.setattr(pipeline_mod, "attach_info_card_summaries", spy)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        pos_store.open_position("600001.SH", 10.0, 1000, report_date, db_path=isolated_env.db_path)

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert captured["news_items"] is not None    # 内存态列表原样传入,不是 None(让对方现读 DB)
        assert captured["news_domain_codes"] == {"600001.SH"}
        assert captured["top_list"] is not None


class TestExecHintWiring:
    """v1.4-⑤-A 执行提示接入 `build_report`(硬要求④:整段异常不阻断主报告)。触发
    条件正确性在 `test_exec_hint.py` 逐项覆盖(纯函数直调,不依赖完整管线);本类只
    测「接线」+「不阻断」+「落库」。"""

    def test_candidates_carry_exec_hints_field_after_build_report(self, isolated_env, monkeypatch):
        """默认合成行情(600001.SH 报告日小幅回调 ret_1d≈-1%)不触发任何 exec_hint
        码——本测试断言的是"字段存在且正确落库为空列表",不是某条具体命中(命中
        正确性归 test_exec_hint.py)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.exec_hints == []

        loaded = store.load_report(report_date, db_path=isolated_env.db_path)
        loaded_cand = next(c for c in loaded["candidates"] if c["ts_code"] == "600001.SH")
        assert loaded_cand["exec_hints"] == []

    def test_exec_hint_exception_does_not_block_main_report(self, isolated_env, monkeypatch):
        """`attach_exec_hints` 整体异常(保险丝范围外的意外)时,`build_report` 仍必须
        成功产出报告——候选照出,只是这批候选当次没有执行提示(维持构造时的默认空
        列表)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        monkeypatch.setattr(
            pipeline_mod, "attach_exec_hints",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.exec_hints == []   # 保险丝触发,维持默认空(不是半份脏数据)

    def test_attach_exec_hints_called_with_db_path(self, isolated_env, monkeypatch):
        """spy 断言 `build_report` 确实调用了 `attach_exec_hints`(而不是被 import
        遗漏/静默跳过)且携带正确的 `db_path`(C3 需要按 `db_path` 查 decision_log)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        captured = {}
        real = pipeline_mod.attach_exec_hints

        def spy(candidates, trade_date, **kw):
            captured["called"] = True
            captured["db_path"] = kw.get("db_path")
            captured["n_candidates"] = len(candidates)
            return real(candidates, trade_date, **kw)

        monkeypatch.setattr(pipeline_mod, "attach_exec_hints", spy)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert captured.get("called") is True
        assert captured.get("db_path") == isolated_env.db_path
        assert captured.get("n_candidates", 0) > 0


class TestPendingTrackWiring:
    """v1.3-④ 挂单未成交追踪接入 `build_report`(原 v1.2.1-C 全文,归 v1.3)。偏移量 /
    到期 / ret_from_plan 等字段单测在 `test_pending_track.py`;本类只测「接线」
    (`save=True` 才推进状态机、`save=False` 零副作用)+ 端到端(连跑 N 个交易日 →
    track 表 N 行 + 决策转 expired,plan §五 v1.3-④ 验收②)。"""

    def test_save_true_invokes_track_pending_decisions_with_correct_args(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        captured = {}

        def spy(trade_date, **kw):
            captured["trade_date"] = trade_date
            captured["parquet_dir"] = kw.get("parquet_dir")
            captured["db_path"] = kw.get("db_path")
            return 0

        monkeypatch.setattr(pipeline_mod, "track_pending_decisions", spy)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        assert captured["trade_date"] == report_date
        assert captured["parquet_dir"] == isolated_env.parquet_dir
        assert captured["db_path"] == isolated_env.db_path

    def test_save_false_does_not_invoke_track_pending_decisions(self, isolated_env, monkeypatch):
        """`save=False`(预览/单测)绝不应推进 pending→expired 状态机或落追踪行——
        与既有 `mark_inquiry_pool_consumed`/`holding_store`/`news_alerts_store` 的
        「只在 `if save:` 块内触发」惯例一致。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        called = {"n": 0}

        def spy(*a, **kw):
            called["n"] += 1
            return 0

        monkeypatch.setattr(pipeline_mod, "track_pending_decisions", spy)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        pipeline_mod.build_report(
            dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert called["n"] == 0

    def test_end_to_end_pending_decision_tracked_across_n_days_then_expires(self, isolated_env, monkeypatch):
        """端到端(隔离库,plan §五 v1.3-④ 验收②):造一只 pending 决策 → 连跑 N 个
        交易日 `build_report` → `decision_pending_track` 表 N 行 + 决策转 expired;
        复用报告已建的 EOD 面板访问层,不新拉数据源(硬要求③)。"""
        import neckline.decision_log as dl_mod
        from neckline.decision_log import STATUS_EXPIRED, create_decision, get_decision
        from neckline.report.pending_track import DECISION_PENDING_TRACK_DAYS, load_track_rows

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)

        created_day = dates[5]
        monkeypatch.setattr(dl_mod, "_now", lambda: f"{created_day.isoformat()}T09:00:00+00:00")
        d = create_decision(
            ts_code="600001.SH", why_buy="题材热", why_entry_price="回调低吸",
            invalidation="跌破10日线", thesis_tags=["THEME"], playbook_tag="SWING_CHASE",
            planned_price=10.0, planned_qty=1000, db_path=isolated_env.db_path,
        )

        track_days = dates[6:6 + DECISION_PENDING_TRACK_DAYS]
        for td in track_days:
            pipeline_mod.build_report(
                td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
            )

        rows = load_track_rows(d.id, db_path=isolated_env.db_path)
        assert len(rows) == DECISION_PENDING_TRACK_DAYS
        assert [r["dOffset"] for r in rows] == list(range(1, DECISION_PENDING_TRACK_DAYS + 1))
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_EXPIRED


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


# —— v1.4-①-C 板块数据过期告警端到端(§七 P0-3;「造一个陈旧 ths_daily 验」)——————

class TestSectorFreshnessInReport:
    """`seed_synthetic_market` 只铺 `ths_index`/`ths_member`、**不铺 `ths_daily`**,
    正好等价于生产 P0-3 的现状(板块表没有日更路径)——那份报告此前长这样:板块节空、
    情报题材节空、**而报告上一个字都不提数据是旧的**。这组断言就是把那件事变得说得出口。"""

    def _seed_ths_daily(self, settings, dates):
        import polars as pl

        from neckline.data.concept_data import upsert_ths_daily

        upsert_ths_daily(pl.DataFrame({
            "ts_code": ["885921.TI"] * len(dates),
            "trade_date": [d.strftime("%Y%m%d") for d in dates],
            "close": [100.0 + i for i in range(len(dates))],
        }), settings.parquet_dir)

    def test_stale_board_data_raises_banner_and_marks_theme_untrustworthy(self, isolated_env, monkeypatch):
        from neckline.report.sectors import SECTOR_DATA_STALE_MAX_LAG_DAYS

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env, n_days=30)
        seed_active_rule_v1(isolated_env)
        # 造陈旧:板块数据只到报告日往前第 (容忍上限+1) 个交易日
        lag = SECTOR_DATA_STALE_MAX_LAG_DAYS + 1
        self._seed_ths_daily(isolated_env, dates[: len(dates) - lag])
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )
        assert bundle.sector_freshness.stale is True
        assert bundle.sector_freshness.lag_days == lag
        assert "板块数据过期告警" in bundle.markdown          # 报告**顶部**醒目告警
        assert "本小节不可信" in bundle.markdown              # 最强题材小节被点名
        # 落库快照可读回(随报告冻住,不在读时重算)
        loaded = store.load_report(report_date, db_path=isolated_env.db_path)
        # v1.4-⑩-F:同一个 `dataFreshness` 里并列**两件独立故障** —— 板块数据过期(本例
        # 造的)与行业强度未就绪(本例已由 fixture 日更喂上,故 stale=False)。
        # **既有三键语义一个字不改**(`stale` 仍只表板块),不许合并成一个 bool。
        assert loaded["data_freshness"] == {
            "sectorDataDate": dates[len(dates) - lag - 1].strftime("%Y%m%d"),
            "sectorLagDays": lag, "stale": True,
            "industryStrengthDate": report_date.strftime("%Y%m%d"),
            "industryStrengthLagDays": 0, "industryStrengthStale": False,
        }

    def test_fresh_board_data_has_no_banner(self, isolated_env, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env, n_days=30)
        seed_active_rule_v1(isolated_env)
        self._seed_ths_daily(isolated_env, dates)
        bundle = pipeline_mod.build_report(
            dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert bundle.sector_freshness.stale is False
        assert "板块数据过期告警" not in bundle.markdown
        assert "本小节不可信" not in bundle.markdown

    def test_missing_ths_daily_is_reported_as_unavailable_not_silent(self, isolated_env, monkeypatch):
        """**P0-3 的原始现场**:`ths_daily` 压根没有 → 此前板块节只说「今日无概念板块
        数据」,读者无从判断是没行情还是没更新;现在必须明说「完全缺失 + 不可信」。"""
        from neckline.report.sectors import SECTOR_LAG_UNKNOWN

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env, n_days=30)
        seed_active_rule_v1(isolated_env)
        bundle = pipeline_mod.build_report(
            dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert bundle.sector_freshness.lag_days == SECTOR_LAG_UNKNOWN
        assert bundle.sector_freshness.stale is True
        assert "板块数据过期告警" in bundle.markdown and "完全缺失" in bundle.markdown


def _limits_from_request(request: httpx.Request) -> tuple:
    """从参考件富上下文里抠出「明日涨跌停参考价」两个数字(见
    `reference_plan._threshold_block`),供 mock handler 构造必然落在区间内的买入
    参考区间——不手算 `seed_synthetic_market` 的收盘价路径,直接读上下文里系统已经
    算好的数字,断言也顺带验证了富上下文确实装配了这一行。"""
    body = json.loads(request.content)
    user_msg = next(m["content"] for m in body["messages"] if m["role"] == "user")
    m = re.search(r"涨停 ([\d.]+) / 跌停 ([\d.]+)", user_msg)
    assert m, "参考件上下文里未找到涨跌停参考价一行,说明富上下文没有正确装配"
    return float(m.group(1)), float(m.group(2))


class TestReferencePlanWiring:
    """v1.5-① 参考件三件套接入 `build_report`(需求 9,§2.0 第〇原则)。夹逼/状态
    判定/json 解析的正确性在 `test_reference_plan.py` 逐项覆盖;本类只测「接线」+
    「不阻断主报告」+「否决不移除候选(机器不禁、人可复核)」+「落库/不落库」。"""

    def test_candidate_carries_ok_reference_plan_and_persists_row(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            up, down = _limits_from_request(request)
            captured["buy_low"] = round(down + 0.05, 2)
            captured["buy_high"] = round(down + 0.20, 2)
            content = (
                "分析正文,催化站得住。\n\n结论:通过\n\n```json\n"
                + json.dumps({
                    "buy": {"low": captured["buy_low"], "high": captured["buy_high"], "why": "贴近支撑"},
                    "exit": {"low": up + 1.0, "high": up + 2.0, "why": "前高压力位"},
                    "script": "若低开则观望,符合预期则按区间执行", "veto_reason": None,
                }, ensure_ascii=False)
                + "\n```"
            )
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        bundle = pipeline_mod.build_report(
            report_date, llm_provider=provider, llm_transport=httpx.MockTransport(handler),
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.reference_plan is not None
        assert cand.reference_plan["status"] == STATUS_OK
        assert cand.reference_plan["buy"]["low"] == captured["buy_low"]
        assert cand.reference_plan["buy"]["high"] == captured["buy_high"]
        assert cand.reference_plan["disclaimer"]
        # LLM 审判叙述已清掉三件套 json 围栏(§2.7,用户看到的评语不该带原始 JSON)。
        jr = bundle.judged["600001.SH"]
        assert "```" not in jr.narrative and '"buy"' not in jr.narrative
        assert "催化站得住" in jr.narrative

        # 落库可读回,与内存态一致。
        row = reference_plan_store.load_reference_plan(report_date, "600001.SH", db_path=isolated_env.db_path)
        assert row is not None
        assert row["status"] == "ok"
        assert row["buy_low"] == pytest.approx(captured["buy_low"])

    def test_veto_verdict_keeps_candidate_in_list_with_empty_reference_items(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        def handler(request: httpx.Request) -> httpx.Response:
            content = (
                "该股近期有一则减持公告,催化站不住。\n\n结论:否决\n\n```json\n"
                + json.dumps({"buy": None, "exit": None, "script": None, "veto_reason": "股东大幅减持"})
                + "\n```"
            )
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        bundle = pipeline_mod.build_report(
            report_date, llm_provider=provider, llm_transport=httpx.MockTransport(handler),
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        codes = [c.ts_code for c in bundle.candidates]
        assert "600001.SH" in codes    # ②验收:否决不移除候选,票仍在候选列表里
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.reference_plan["status"] == STATUS_VETOED
        assert cand.reference_plan["buy"] is None
        assert cand.reference_plan["exit"] is None
        assert cand.reference_plan["script"] is None
        assert cand.reference_plan["vetoReason"] == "股东大幅减持"
        # 审判结论本身也照常展示(机器不禁、人可复核,§2.0 第三条)。
        jr = bundle.judged["600001.SH"]
        assert jr.verdict == VERDICT_VETO
        assert "减持公告" in jr.narrative

    def test_no_provider_candidate_gets_unavailable_reference_plan_not_silence(self, isolated_env, monkeypatch):
        """LLM 未激活(本项目当前常态)时,参考件不是"什么都没有"——而是一条如实
        标注"没看"的 `status=unavailable` 记录(①-D「这是没看,绝不能显示成没有」)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.reference_plan is not None
        assert cand.reference_plan["status"] == "unavailable"
        assert cand.reference_plan["unavailableReason"]
        assert cand.reference_plan["buy"] is None and cand.reference_plan["exit"] is None

    def test_reference_plan_exception_does_not_block_main_report(self, isolated_env, monkeypatch):
        """参考件三件套(v1.5-①)整体异常时,`build_report` 仍必须成功产出报告——
        候选照出、审判结论照留,只是这只候选当次没有参考件(维持默认 `None`)。"""
        monkeypatch.setattr(
            pipeline_mod, "judge_and_build_reference_plan",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.reference_plan is None   # 保险丝触发,维持默认(不是半份脏数据)
        jr = bundle.judged.get("600001.SH")
        assert jr is not None                # 退回的普通审判仍然发生,judged 不因参考件异常缺失
        assert reference_plan_store.load_reference_plans(report_date, db_path=isolated_env.db_path) == []

    def test_save_false_does_not_write_reference_plans_table(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]

        def handler(request: httpx.Request) -> httpx.Response:
            up, down = _limits_from_request(request)
            content = (
                "分析。\n\n结论:通过\n\n```json\n"
                + json.dumps({
                    "buy": {"low": round(down + 0.1, 2), "high": round(down + 0.3, 2), "why": "w"},
                    "exit": {"low": up + 1.0, "high": up + 2.0, "why": "w2"},
                    "script": "s", "veto_reason": None,
                })
                + "\n```"
            )
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        bundle = pipeline_mod.build_report(
            report_date, llm_provider=provider, llm_transport=httpx.MockTransport(handler),
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.reference_plan is not None     # 不落库不等于不产出(内存态 bundle 仍完整)
        assert reference_plan_store.load_reference_plan(
            report_date, "600001.SH", db_path=isolated_env.db_path
        ) is None
        assert reference_plan_store.load_reference_plans(report_date, db_path=isolated_env.db_path) == []


# ══════════════════════════════════════════════════════════════════════════
#  v1.5-② LLM 覆盖面与预算重排(需求 9「20只全覆盖」)
# ══════════════════════════════════════════════════════════════════════════

def _budget_candidates(n: int) -> list:
    """手搓 n 只候选(rank 1..n 升序,同 `build_report` 传给判官循环的既有排序
    姿势)——不经 `build_intel_candidates` 情报漏斗,专注测
    `_judge_candidates_with_budget` 本身的预算/跳过机制(夹逼/JSON 解析细节已在
    `test_reference_plan.py` 逐项覆盖,不在本节重复)。"""
    return [
        Candidate(
            ts_code=f"{600100 + i:06d}.SH", name=f"候选{i}", close=10.0 + i, score=90.0 - i,
            rank=i + 1, board="MAIN", pattern_tags=[], hot_sectors=[], sector_names=[],
            entry_plan="", stop_loss="", target="", invalidation_text="", invalidation_spec={},
        )
        for i in range(n)
    ]


def _seed_budget_env(settings, n: int):
    """最小环境(同 `test_reference_plan.py::_seed_env` 姿势):trade_cal + 每只候选
    一行 stock_basic(非ST主板老股,供涨跌停/元数据解析走通)+ 现役规则 v1(供
    `stop_pct` 解析)。不铺任何 parquet 行情——本节不测夹逼数值正确性,只测预算/
    跳过机制本身。"""
    dates = business_days(date(2024, 6, 3), n + 5)
    insert_trade_cal(settings, dates)
    insert_stock_basic(settings, [
        {"ts_code": f"{600100 + i:06d}.SH", "name": f"候选{i}", "market": "主板", "list_date": date(2020, 1, 1)}
        for i in range(n)
    ])
    seed_active_rule_v1(settings)
    return dates


def _pass_json(**overrides) -> dict:
    base = {"buy": None, "exit": {"low": 1.0, "high": 2.0, "why": "压力位"},
            "script": "若低开则观望,符合预期则按区间执行", "veto_reason": None}
    base.update(overrides)
    return base


def _veto_json(reason: str = "消息面有硬伤") -> dict:
    return {"buy": None, "exit": None, "script": None, "veto_reason": reason}


def _judge_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "model": "glm-5.2",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
    })


def _pass_content(json_obj: dict) -> str:
    return "分析正文,催化站得住。\n\n结论:通过\n\n```json\n" + json.dumps(json_obj, ensure_ascii=False) + "\n```"


def _veto_content(json_obj: dict) -> str:
    return "分析正文,催化站不住。\n\n结论:否决\n\n```json\n" + json.dumps(json_obj, ensure_ascii=False) + "\n```"


class TestJudgeCandidatesWithBudget:
    """v1.5-②-B/C:`pipeline_mod._judge_candidates_with_budget`(判官循环预算/跳过
    机制本身)。端到端接入 `build_report` 的验证见下方
    `TestCandidateJudgeBudgetWiring`。"""

    def test_ample_budget_covers_all_20_none_left_empty_handed(self, isolated_env):
        """② 验收原话:「20 只全部有结论 + 每只或有三件套或有不买理由,断言无一只
        两头空」。"""
        dates = _seed_budget_env(isolated_env, 20)
        report_date = dates[-1]
        cands = _budget_candidates(20)
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] % 2 == 0:
                return _judge_response(_veto_content(_veto_json()))
            return _judge_response(_pass_content(_pass_json()))

        provider = GLMProvider(api_key="sk-xxx")
        judged = pipeline_mod._judge_candidates_with_budget(
            cands, report_date, provider=provider, top_list={},
            industry_scores=None, industry_map=None, transport=httpx.MockTransport(handler),
            budget_seconds=pipeline_mod.CANDIDATE_JUDGE_BUDGET_SECONDS, save=False,
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )
        assert call_count["n"] == 20
        assert len(judged) == 20
        for c in cands:
            assert c.judge_skipped is False
            assert c.reference_plan is not None
            rp = c.reference_plan
            has_content = bool(rp.get("buy") or rp.get("exit") or rp.get("script") or rp.get("vetoReason"))
            assert has_content, f"{c.ts_code} 两头空:{rp}"

    def test_budget_exhausted_after_first_call_skips_rest_matches_plan_example(self, isolated_env):
        """② 验收原话:「把预算调到 1s → 除第 1 只外全 judgeSkipped,报告照出、不崩」
        ——本测用等价的更小数值(budget < 单次调用耗时)复现同一场景,证明**恰好
        第 1 只完成、其余全跳过**,不是"随便跳几只"。"""
        dates = _seed_budget_env(isolated_env, 20)
        report_date = dates[-1]
        cands = _budget_candidates(20)
        calls: list = []

        def slow_handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            time.sleep(0.08)   # > budget_seconds,确保第1只调用完成后预算必然已耗尽
            return _judge_response(_pass_content(_pass_json()))

        provider = GLMProvider(api_key="sk-xxx")
        judged = pipeline_mod._judge_candidates_with_budget(
            cands, report_date, provider=provider, top_list={},
            industry_scores=None, industry_map=None, transport=httpx.MockTransport(slow_handler),
            budget_seconds=0.05, save=False,
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )
        assert len(calls) == 1                       # 只发起了这一次调用
        assert len(judged) == 1
        assert cands[0].ts_code in judged
        assert cands[0].judge_skipped is False
        for c in cands[1:]:
            assert c.judge_skipped is True
            assert c.ts_code not in judged            # 跳过的不进 judged 字典

    def test_skips_are_always_the_ranked_tail_not_arbitrary(self, isolated_env):
        """降级优先级(plan 定死,不折中):预算耗尽后牺牲**排名靠后**的候选,已审的
        排名必然全部小于被跳过的排名(连续尾段,不是随机丢弃)。"""
        dates = _seed_budget_env(isolated_env, 10)
        report_date = dates[-1]
        cands = _budget_candidates(10)   # rank 1..10,升序传入(同 build_report 姿势)

        def slow_handler(request: httpx.Request) -> httpx.Response:
            time.sleep(0.02)
            return _judge_response(_pass_content(_pass_json()))

        provider = GLMProvider(api_key="sk-xxx")
        pipeline_mod._judge_candidates_with_budget(
            cands, report_date, provider=provider, top_list={},
            industry_scores=None, industry_map=None, transport=httpx.MockTransport(slow_handler),
            budget_seconds=0.05, save=False,
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )
        judged_ranks = sorted(c.rank for c in cands if not c.judge_skipped)
        skipped_ranks = sorted(c.rank for c in cands if c.judge_skipped)
        assert judged_ranks and skipped_ranks, "本测需要同时出现「已审」与「被跳过」两类才有意义"
        assert max(judged_ranks) < min(skipped_ranks)

    def test_judge_skipped_and_degraded_are_distinct_not_merged(self, isolated_env):
        """② 验收原话:「judgeSkipped 与 degraded 分开,断言两个计数各自正确」——
        `degraded`=发起了但未激活/失败(承 news_alerts `codes_failed`);
        `judge_skipped`=预算耗尽压根没发起(承 `codes_skipped`)。两者互不覆盖。"""
        dates = _seed_budget_env(isolated_env, 6)
        report_date = dates[-1]
        cands = _budget_candidates(6)

        # 前3只:provider=None(LLM 未激活)→ 全部"发起了但未激活",与预算无关,
        # 预算给到再大也不受影响(provider=None 时 `judge_candidate` 本就不发网络
        # 调用,只是状态仍是"发起过"而非"跳过")。
        judged_inactive = pipeline_mod._judge_candidates_with_budget(
            cands[:3], report_date, provider=None, top_list={},
            industry_scores=None, industry_map=None, transport=None,
            budget_seconds=1200.0, save=False,
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )
        assert len(judged_inactive) == 3
        assert all(jr.degraded for jr in judged_inactive.values())
        assert all(c.judge_skipped is False for c in cands[:3])

        # 后3只:预算=0 → 连第一只都不发起(elapsed>=0 恒真),transport 若被调用则
        # 直接断言失败,确保"零调用"不是碰巧。
        def must_not_be_called(request: httpx.Request) -> httpx.Response:
            raise AssertionError("预算=0 时不应发起任何调用")

        judged_skipped = pipeline_mod._judge_candidates_with_budget(
            cands[3:], report_date, provider=GLMProvider(api_key="sk-xxx"), top_list={},
            industry_scores=None, industry_map=None, transport=httpx.MockTransport(must_not_be_called),
            budget_seconds=0.0, save=False,
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )
        assert len(judged_skipped) == 0
        assert all(c.judge_skipped for c in cands[3:])

        degraded_count = sum(1 for jr in judged_inactive.values() if jr.degraded)
        skipped_count = sum(1 for c in cands[3:] if c.judge_skipped)
        assert degraded_count == 3 and skipped_count == 3   # 两个计数各自独立、互不覆盖

    def test_save_true_persists_llm_judgments_only_for_attempted_candidates(self, isolated_env):
        """跳过的候选不应留下任何 `llm_judgments`/`reference_plans` 落库痕迹
        (它们压根没被"审"过,不是"审了但空")。"""
        dates = _seed_budget_env(isolated_env, 4)
        report_date = dates[-1]
        cands = _budget_candidates(4)

        def slow_handler(request: httpx.Request) -> httpx.Response:
            time.sleep(0.06)
            return _judge_response(_pass_content(_pass_json()))

        provider = GLMProvider(api_key="sk-xxx")
        pipeline_mod._judge_candidates_with_budget(
            cands, report_date, provider=provider, top_list={},
            industry_scores=None, industry_map=None, transport=httpx.MockTransport(slow_handler),
            budget_seconds=0.05, save=True,
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
        )
        judgments = store.load_llm_judgments(report_date, db_path=isolated_env.db_path)
        judged_codes = {j["ts_code"] for j in judgments}
        skipped_codes = {c.ts_code for c in cands if c.judge_skipped}
        assert judged_codes & skipped_codes == set()   # 落库的判词与被跳过的票零交集
        assert judged_codes, "至少应有第1只被真正审判并落库"


class TestCandidateJudgeBudgetWiring:
    """v1.5-②:预算/跳过机制接入 `build_report` 的端到端验证(机制本身的详细分支
    见上方 `TestJudgeCandidatesWithBudget`,本类只测"接线正确"+"不阻断主报告")。"""

    def test_top_n_judged_default_now_equals_top_n_total_20(self):
        """②-A:「后10只不耗LLM」旧分档退役,`TOP_N_JUDGED` 现与 `TOP_N_TOTAL` 相等。"""
        assert pipeline_mod.TOP_N_JUDGED == pipeline_mod.TOP_N_TOTAL == 20

    def test_exhausted_budget_marks_the_one_real_candidate_skipped_report_still_builds(self, isolated_env):
        """`seed_synthetic_market` 只产 1 只真实候选(600001.SH),用预算=0 逼它
        必然被跳过——验证「报告照出、不崩」这条落到 `build_report` 整条管线上仍
        成立(不只是抽出来的辅助函数层面)。"""
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        provider = GLMProvider(api_key="sk-xxx")

        def must_not_be_called(request: httpx.Request) -> httpx.Response:
            raise AssertionError("预算已耗尽,不该发起任何调用")

        bundle = pipeline_mod.build_report(
            report_date, llm_provider=provider, llm_transport=httpx.MockTransport(must_not_be_called),
            candidate_judge_budget_seconds=0.0,
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.judge_skipped is True
        assert "600001.SH" not in bundle.judged
        assert cand.reference_plan is None      # 没发起调用,自然没有参考件(不是异常)
        assert "预算耗尽未发起" in bundle.markdown
        assert "未执行" not in bundle.markdown   # 不应误报成"异常状态"(与真异常分支区分)

        # 落库/读回同样干净:候选仍在报告里,只是没有判词行。
        row = store.load_report(report_date, db_path=isolated_env.db_path)
        saved_cand = next(c for c in row["candidates"] if c["ts_code"] == "600001.SH")
        assert saved_cand["judge_skipped"] is True

    def test_ample_budget_default_still_judges_the_one_candidate(self, isolated_env):
        """反向对照:预算充裕(默认值)时,唯一候选应正常被审判、不被跳过——防止
        「exhausted 测试之所以过,是因为代码总是标 skipped」这种退化实现蒙混过关。"""
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        provider = GLMProvider(api_key="sk-xxx")

        def handler(request: httpx.Request) -> httpx.Response:
            return _judge_response(_pass_content(_pass_json()))

        bundle = pipeline_mod.build_report(
            report_date, llm_provider=provider, llm_transport=httpx.MockTransport(handler),
            parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
        )
        cand = next(c for c in bundle.candidates if c.ts_code == "600001.SH")
        assert cand.judge_skipped is False
        assert "600001.SH" in bundle.judged
