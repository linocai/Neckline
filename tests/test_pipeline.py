"""报告管线编排单测(plan 2.5)。用 `tests.conftest.seed_synthetic_market`(合成多票
多日行情)跑通完整 I/O 接线:大脑读取 → 持仓体检 / 情报件 / 板块资金流 / 消息面 →
落库 → markdown 渲染。

⚠ **V2-⑬ 起本文件大幅缩编**:候选评分 / 20 只 LLM 审判 / 参考件三件套 / 执行提示 /
信息卡摘要五条接线随 §五 V2-⑬-1/2/3/4 删除,对应的 7 个测试类
(`TestBuildReportWithMockLLMProvider`/`TestInfoCardSummaryWiring`/`TestExecHintWiring`/
`TestTopNSplit`/`TestReferencePlanWiring`/`TestJudgeCandidatesWithBudget`/
`TestCandidateJudgeBudgetWiring`)一并删除 —— 它们测的编排代码已不存在。
⑭-A 上篮子日报后,应在这里补回「扫描 → 聚合 → Tier → 卡冻结」的分段接线测试与
**每段保险丝**的不阻断断言(那是 ⑭ 的验收条款)。"""

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
from neckline.data.tushare_client import TushareResult
from neckline.llm.providers.glm import GLMProvider
from neckline.report import store
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

        assert bundle.strategy_version == "v1"
        assert bundle.sentiment.position_quota in ("满额", "半额", "休息")
        assert bundle.markdown.startswith("# Neckline 盘后报告")

        # 落库可读回
        loaded_report = store.load_report(report_date, db_path=isolated_env.db_path)
        assert loaded_report is not None
        assert loaded_report["strategy_version"] == "v1"
        # ⑬-1:候选榜已删 → `candidates_json` 恒为空数组(⑭-A 换成篮子日报)
        assert loaded_report["candidates"] == []
        # ⑬-2:`llm_judgments` 停写留档 —— 跑完整管线后该表**零新增行**(验收条款)
        assert store.load_llm_judgments(report_date, db_path=isolated_env.db_path) == []


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


class TestInquiryPoolChannelRetired:
    """**V2-⑬-10**:问询台 forced 海选池强制并入通道整条删除。原 `TestInquiryPoolConsumption`
    (6 例)+ `TestPendingConsumptionWindowStoreLevel`(3 例)测的是「入池 → 当晚强制进候选
    → 落库后标记消费」这个闭环,通道没了、候选榜也没了(⑬-1),两个类随之删除。
    留下这一条守门锁死「不许悄悄接回来」。"""

    def test_build_report_never_touches_the_pool(self, isolated_env, monkeypatch):
        """`build_report` 的源码里不许再出现海选池消费符号(通道已删,不是"暂时不用")。"""
        import inspect

        src = inspect.getsource(pipeline_mod)
        for gone in ("load_pending_inquiry_codes", "mark_inquiry_pool_consumed", "forced_codes="):
            assert gone not in src, f"{gone} 应已随 ⑬-10 从报告管线删除"


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
        assert bundle.markdown.startswith("# Neckline 盘后报告")   # 主报告未受影响
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
        assert bundle.markdown.startswith("# Neckline 盘后报告")   # 主报告未受影响
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
        assert bundle.markdown.startswith("# Neckline 盘后报告")   # 主报告未受影响
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

    def test_scan_targets_are_positions_only_secondary_domain_empty(self, isolated_env, monkeypatch):
        """§硬要求「扫描对象=有限域,不是全市场」——用 spy 替身直接断言
        `build_news_alerts` 收到的 `position_codes`/`secondary_codes` 两个参数,
        不依赖 TuShare/LLM 真调用。**签名"分开传入"不许退化回合并成一个列表**
        (LLM 侧墙钟预算持仓优先,靠调用方把两者分开才能保证)。

        **V2-⑬-11 起次级域恒空**:自选池整链删除(裁定 #9-a)→ 次级域暂无来源;
        ⑭-A 把篮子成员接进来时,本测试要改成断言「篮子成员进次级域」。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        captured = {}

        def spy(trade_date, position_codes, secondary_codes, **kw):
            captured["position_codes"] = list(position_codes)
            captured["secondary_codes"] = list(secondary_codes)
            return news_alerts_mod.empty_news_alerts_report(trade_date, reason="test-spy")

        monkeypatch.setattr(pipeline_mod, "build_news_alerts", spy)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        pos_store.open_position("600001.SH", 10.0, 1000, report_date, db_path=isolated_env.db_path)

        pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        position_only = {c for c, _name in captured["position_codes"]}
        assert position_only == {"600001.SH"}
        assert captured["secondary_codes"] == []          # ⑬-11:自选池已删,次级域暂空
        assert "300001.SZ" not in position_only           # 不是持仓,不应被扫描

    def test_db_path_threaded_through_for_cross_day_dedup(self, isolated_env, monkeypatch):
        """减持类跨日去重要查库(2026-07-26 必改 2)——`pipeline.py` 必须把
        `db_path` 传给 `build_news_alerts`,不能让它退回默认(可能撞到真实生产库)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        captured = {}

        def spy(trade_date, position_codes, secondary_codes, **kw):
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

    def test_markdown_includes_holding_check_section_first(self, isolated_env, monkeypatch):
        """「持仓管理优先于选新票」的顺序纪律不变(⑬-1 删了候选节 → 锚改到紧随其后的
        情报节;⑭-A 重排篮子日报时锚要换成「今日篮子」那一节)。"""
        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)
        report_date = dates[-1]
        pos_store.open_position("600001.SH", 10.0, 1000, report_date, db_path=isolated_env.db_path)

        bundle = pipeline_mod.build_report(
            report_date, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
        )
        assert "## 持仓体检" in bundle.markdown
        assert bundle.markdown.index("## 持仓体检") < bundle.markdown.index("## 情报")
        assert "600001.SH" in bundle.markdown.split("## 持仓体检")[1].split("## 情报")[0]

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

    def test_end_to_end_pending_decision_tracked_across_n_days_then_stops(self, isolated_env, monkeypatch):
        """端到端(隔离库,plan §五 v1.3-④ 验收②):造一只 pending 决策 → 连跑 N 个
        交易日 `build_report` → `decision_pending_track` 表 N 行,窗口到点后停止
        新增追踪行;复用报告已建的 EOD 面板访问层,不新拉数据源(硬要求③)。

        **v2.0.0(⑩-C)**:`decision_log` 表停写留档,fixture 改走
        `tests.conftest.insert_decision_log_row`(裸 SQL,不再有 `create_decision`/
        `_now()` 可 monkeypatch);到期后不再断言 `status == STATUS_EXPIRED`——该
        写入口已删除,`status` 如实停在 fixture 给的 `pending`(见
        `tests/test_pending_track.py` 同款变更)。"""
        from neckline.decision_log import STATUS_PENDING, get_decision
        from neckline.report.pending_track import DECISION_PENDING_TRACK_DAYS, load_track_rows
        from tests.conftest import insert_decision_log_row

        monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
        dates = seed_synthetic_market(isolated_env)
        seed_active_rule_v1(isolated_env)

        created_day = dates[5]
        d = insert_decision_log_row(
            isolated_env.db_path, ts_code="600001.SH", why_buy="题材热", why_entry_price="回调低吸",
            invalidation="跌破10日线", thesis_tags=["THEME"], playbook_tag="SWING_CHASE",
            planned_price=10.0, planned_qty=1000,
            created_at=f"{created_day.isoformat()}T09:00:00+00:00",
        )

        track_days = dates[6:6 + DECISION_PENDING_TRACK_DAYS]
        for td in track_days:
            pipeline_mod.build_report(
                td, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=True,
            )

        rows = load_track_rows(d.id, db_path=isolated_env.db_path)
        assert len(rows) == DECISION_PENDING_TRACK_DAYS
        assert [r["dOffset"] for r in rows] == list(range(1, DECISION_PENDING_TRACK_DAYS + 1))
        assert get_decision(d.id, db_path=isolated_env.db_path).status == STATUS_PENDING


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


