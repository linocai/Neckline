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
    """§2.5 闭环报告侧(4E 接线):`build_report` 消费当日 `inquiry_pool`——「初审通过」
    的票强制并入当晚候选评分 universe(只扩输入,不改评分)。用 300001.SZ(创业板,报告
    日 rule v1 主板 only 会剔除)作「问询台放行但报告 mask 会排除」的代理,验证它经海选
    池被强制纳入,而未入池的 600002.SH(*ST)仍被剔除。"""

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
