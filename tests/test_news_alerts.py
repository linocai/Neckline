"""消息面扫描单测(plan §五 v1.3-③-C4)。锁死:① 减持(TuShare stk_holdertrade)
——只取 in_de='DE'、只取扫描对象代码内的行、TuShare 无 token/调用失败/空结果
分别的降级形状、**事件级跨日去重**(2026-07-26 必改);② 立案/暴雷/监管(LLM)
——缺 provider 整批零调用降级、逐票扫描、部分标的失败不污染其余标的、**墙钟
预算封顶 + 持仓优先于自选**(2026-07-26 必改);③ `build_news_alerts` 把两源
结果合并、持仓+自选双双为空时零 I/O;④ 「没扫到 vs 扫了没有」——`scan_statuses`
的 `scanned`/`reason`/`codesSkipped` 语义;⑤ `llm.news_scan` 与本模块的类别
字符串常量对拍(防两处漂移)。

所有测试均用 `tmp_path` 隔离 db(减持类跨日去重要查库,不能碰真实 `data/`)。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

import neckline.report.news_alerts as news_alerts_mod
from neckline.data.tushare_client import TushareResult
from neckline.llm import news_scan
from neckline.llm.providers.glm import GLMProvider
from neckline.report import news_alerts_store
from neckline.report.news_alerts import (
    NewsCategory,
    SOURCE_LLM_PREFIX,
    SOURCE_TUSHARE_HOLDERTRADE,
    build_news_alerts,
    empty_news_alerts_report,
)

D = date(2026, 7, 24)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "neckline_test.db"


def _holdertrade_df(**overrides) -> pd.DataFrame:
    row = {
        "ts_code": "600001.SH", "ann_date": "20260724", "holder_name": "张三",
        "holder_type": "G", "in_de": "DE", "change_vol": 50000.0, "change_ratio": 0.35,
        "after_share": 1200000.0, "after_ratio": 3.1, "avg_price": 12.5, "total_share": 40000000.0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


class TestCategoryConstantsAlignment:
    """`llm.news_scan` 的类别常量与 `report.news_alerts.NewsCategory` 逐字对拍
    (两处因避免循环 import 各自定义,模块头承诺"已用单测互相对拍")。"""

    def test_investigation_blowup_regulatory_match(self):
        assert news_scan.CATEGORY_INVESTIGATION == NewsCategory.INVESTIGATION
        assert news_scan.CATEGORY_BLOWUP == NewsCategory.BLOWUP
        assert news_scan.CATEGORY_REGULATORY == NewsCategory.REGULATORY


class TestReductionScan:
    def test_de_rows_within_target_codes_become_items(self, monkeypatch, db):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(_holdertrade_df()),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        reduction_items = [i for i in report.items if i.category == NewsCategory.REDUCTION]
        assert len(reduction_items) == 1
        it = reduction_items[0]
        assert it.ts_code == "600001.SH"
        assert it.source == SOURCE_TUSHARE_HOLDERTRADE
        assert "张三" in it.summary and "减持" in it.summary
        assert "50,000" in it.summary or "50000" in it.summary
        assert it.event_date == "20260724"
        assert it.event_key   # 非空

    def test_in_rows_excluded_increase_is_not_a_reduction_alert(self, monkeypatch, db):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(_holdertrade_df(in_de="IN")),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        assert report.items == []

    def test_rows_outside_target_codes_excluded(self, monkeypatch, db):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(_holdertrade_df(ts_code="900001.SH")),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        assert report.items == []

    def test_no_token_or_call_failure_degrades_scan_status_not_confirmed_clean(self, monkeypatch, db):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.fail("token 缺失"),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        status = next(s for s in report.scan_statuses if s.source == SOURCE_TUSHARE_HOLDERTRADE)
        assert status.scanned is False
        assert "token 缺失" in status.reason
        assert report.items == []   # 空列表,但读者须看 scanned=False,不能当"确认无减持"

    def test_empty_result_is_scanned_true_no_items(self, monkeypatch, db):
        """该窗口内本就没有任何减持公告是正常情况(非失败)——scanned=True。"""
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(pd.DataFrame()),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        status = next(s for s in report.scan_statuses if s.source == SOURCE_TUSHARE_HOLDERTRADE)
        assert status.scanned is True
        assert status.reason == ""
        assert report.items == []

    def test_lookback_window_passed_to_tushare(self, monkeypatch, db):
        captured = {}

        def fake(start, end):
            captured["start"], captured["end"] = start, end
            return TushareResult.success(pd.DataFrame())

        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", fake)
        build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        assert captured["end"] == "20260724"
        assert captured["start"] < captured["end"]

    def test_duplicate_tushare_rows_for_same_event_collapse_to_one_item(self, monkeypatch, db):
        """2026-07-26 端到端真实数据验证时发现:TuShare 对同一笔披露会原样返回重复行
        (实测 301358.SZ 同一 holder/ann_date/change_vol/change_ratio 出现两次)——本模块
        须按完整字段去重,不能把源头的重复原样透到报告(用户会误读成两笔独立减持)。"""
        dup = pd.concat([_holdertrade_df(), _holdertrade_df()], ignore_index=True)
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda start, end: TushareResult.success(dup))
        report = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        reduction_items = [i for i in report.items if i.category == NewsCategory.REDUCTION]
        assert len(reduction_items) == 1   # 一只票一条,不是两条

    def test_multiple_distinct_reduction_events_same_code_kept_as_separate_items(self, monkeypatch, db):
        """同一票在窗口内有多个真实不同的减持事件(不同股东/不同公告日)——
        2026-07-26 必改后改为**一事件一行**(不再合并成一条 summary),因为跨日
        去重天然要求逐事件判断"这条是否已经报过"。"""
        rows = pd.concat([
            _holdertrade_df(holder_name="张三", ann_date="20260722", change_vol=10000.0, change_ratio=0.1),
            _holdertrade_df(holder_name="李四", ann_date="20260724", change_vol=20000.0, change_ratio=0.2),
        ], ignore_index=True)
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda start, end: TushareResult.success(rows))
        report = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        reduction_items = [i for i in report.items if i.category == NewsCategory.REDUCTION]
        assert len(reduction_items) == 2
        summaries = "|".join(i.summary for i in reduction_items)
        assert "张三" in summaries and "李四" in summaries
        event_dates = {i.event_date for i in reduction_items}
        assert event_dates == {"20260722", "20260724"}


class TestReductionCrossDayDedup:
    """2026-07-26 必改 2:同一 (ts_code, event_date, event_key) 只在第一份扫到它的
    报告里出现,之后的报告(即使仍在回看窗口内重新扫到同一条 TuShare 原始行)不
    再重复生成条目——不能让用户在一周内的每份报告里看到同一句话。"""

    def test_event_already_persisted_on_earlier_day_is_not_resurfaced(self, monkeypatch, db):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(_holdertrade_df(ann_date="20260722")),
        )
        # 模拟"更早的报告日"已经扫到并落库过这个事件。
        day1 = date(2026, 7, 22)
        report1 = build_news_alerts(day1, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        assert len(report1.items) == 1
        news_alerts_store.save_news_alerts(day1, report1.items, db_path=db)

        # 今天(D=07-24)再次扫描,回看窗口仍覆盖 07-22 那笔——TuShare 还是会返回它,
        # 但因为已经在 day1 报过,不应再次出现在今天的报告里。
        report2 = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        assert report2.items == []
        # 扫描状态仍是"已扫描、确认无新事件",不是降级——不应被误判为"没扫到"。
        status = next(s for s in report2.scan_statuses if s.source == SOURCE_TUSHARE_HOLDERTRADE)
        assert status.scanned is True

    def test_new_event_for_same_code_still_surfaces_after_old_one_recorded(self, monkeypatch, db):
        """跨日去重是事件级,不是票级——同一票的新事件不会被老事件的记录挡住。"""
        day1 = date(2026, 7, 22)
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(_holdertrade_df(holder_name="张三", ann_date="20260722")),
        )
        report1 = build_news_alerts(day1, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        news_alerts_store.save_news_alerts(day1, report1.items, db_path=db)

        # 今天出现同一票的一笔新事件(不同持股人)。
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(pd.concat([
                _holdertrade_df(holder_name="张三", ann_date="20260722"),   # 老事件,应被去重
                _holdertrade_df(holder_name="李四", ann_date="20260724"),   # 新事件,应出现
            ], ignore_index=True)),
        )
        report2 = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        assert len(report2.items) == 1
        assert "李四" in report2.items[0].summary

    def test_llm_sourced_items_have_no_event_date_and_are_not_deduped_cross_day(self, monkeypatch, db):
        """LLM 侧维持现状(优先不漏报),`event_date`/`event_key` 恒空,不参与、
        也不会被跨日去重误伤——同一提示连续两天都应能各自出现。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant", "content": "有情况。\n结论-监管:交易所下发问询函。",
                }}],
            })
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda s, e: TushareResult.success(pd.DataFrame()))
        provider = GLMProvider(api_key="sk-xxx")
        day1 = date(2026, 7, 22)
        report1 = build_news_alerts(
            day1, [("600001.SH", "甲")], [], provider=provider, transport=httpx.MockTransport(handler), db_path=db,
        )
        news_alerts_store.save_news_alerts(day1, report1.items, db_path=db)
        report2 = build_news_alerts(
            D, [("600001.SH", "甲")], [], provider=provider, transport=httpx.MockTransport(handler), db_path=db,
        )
        assert len(report1.items) == 1 and report1.items[0].event_date is None
        assert len(report2.items) == 1   # 第二天同样出现,未被跨日去重误杀


class TestLLMCategoriesScan:
    def test_none_provider_skips_all_codes_without_any_call(self, monkeypatch, db):
        # 本测试聚焦 LLM 侧降级,但 build_news_alerts 会同时跑减持侧(TuShare)——
        # 显式桩掉,避免单测意外发起真实网络调用(且真实调用会把
        # tushare_client 模块级 _get_pro() 缓存污染成"已用真 token 初始化",
        # 影响同一 pytest 进程内后续依赖 isolated_env 无 token 降级路径的测试)。
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda s, e: TushareResult.success(pd.DataFrame()))
        report = build_news_alerts(
            D, [("600001.SH", "甲")], [("600002.SH", "乙")], provider=None, db_path=db,
        )
        status = next(s for s in report.scan_statuses if s.source == SOURCE_LLM_PREFIX)
        assert status.scanned is False
        assert "LLM_PROVIDER" in status.reason
        assert status.codes_total == 2
        assert not any(i.source.startswith(SOURCE_LLM_PREFIX) for i in report.items)

    def test_successful_scan_across_multiple_codes(self, monkeypatch, db):
        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode()
            if "600001" in body:
                content = "有情况。\n结论-立案:因信披违规被证监会立案调查。"
            else:
                content = "一切正常。\n结论:未发现"
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            })

        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda s, e: TushareResult.success(pd.DataFrame()))
        provider = GLMProvider(api_key="sk-xxx")
        report = build_news_alerts(
            D, [("600001.SH", "甲")], [("600002.SH", "乙")],
            provider=provider, transport=httpx.MockTransport(handler), db_path=db,
        )
        llm_items = [i for i in report.items if i.source.startswith(SOURCE_LLM_PREFIX)]
        assert len(llm_items) == 1
        assert llm_items[0].ts_code == "600001.SH"
        assert llm_items[0].category == NewsCategory.INVESTIGATION
        status = next(s for s in report.scan_statuses if s.source == SOURCE_LLM_PREFIX)
        assert status.scanned is True
        assert status.codes_failed == 0
        assert status.codes_skipped == 0

    def test_one_code_failure_does_not_drop_other_codes(self, monkeypatch, db):
        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode()
            if "600001" in body:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant", "content": "正常。\n结论-暴雷:审计意见异常。",
                }}],
            })

        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda s, e: TushareResult.success(pd.DataFrame()))
        provider = GLMProvider(api_key="sk-xxx")
        report = build_news_alerts(
            D, [("600001.SH", "甲")], [("600002.SH", "乙")],
            provider=provider, transport=httpx.MockTransport(handler), db_path=db,
        )
        llm_items = [i for i in report.items if i.source.startswith(SOURCE_LLM_PREFIX)]
        assert len(llm_items) == 1
        assert llm_items[0].ts_code == "600002.SH"
        status = next(s for s in report.scan_statuses if s.source == SOURCE_LLM_PREFIX)
        assert status.scanned is True
        assert status.codes_failed == 1
        assert "1/2" in status.reason


class TestLLMWallClockBudgetAndPriority:
    """2026-07-26 必改 1(生产风险):LLM 侧必须有耗时封顶,且优先扫持仓、预算不够
    时牺牲自选——都要有单测锁死,不能只是文档承诺。"""

    def _slow_handler(self, calls: list, delay: float = 0.05):
        def handler(request: httpx.Request) -> httpx.Response:
            import time
            calls.append(request.content.decode())
            time.sleep(delay)
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "无异常。\n结论:未发现"}}],
            })
        return handler

    def test_budget_exhausted_skips_remaining_codes_and_records_honestly(self, monkeypatch, db):
        calls: list = []
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda s, e: TushareResult.success(pd.DataFrame()))
        provider = GLMProvider(api_key="sk-xxx")
        watchlist = [(f"60000{i}.SH", f"票{i}") for i in range(5)]
        report = build_news_alerts(
            D, [], watchlist, provider=provider,
            transport=httpx.MockTransport(self._slow_handler(calls, delay=0.05)),
            db_path=db, llm_budget_seconds=0.08,   # 只够撑 1-2 次调用
        )
        status = next(s for s in report.scan_statuses if s.source == SOURCE_LLM_PREFIX)
        assert status.scanned is True
        assert status.codes_skipped > 0
        assert status.codes_skipped < 5   # 不是全部跳过(至少扫了第一只)
        assert "预算" in status.reason
        assert "不代表确认无消息" in status.reason
        assert len(calls) < 5   # 确实没有把 5 只全部发起调用

    def test_positions_scanned_before_watchlist_when_budget_tight(self, monkeypatch, db):
        """预算只够扫 1-2 只时,持仓必须先被扫、自选被跳过的那个——不是巧合的
        顺序,是写死的优先级。"""
        calls: list = []
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda s, e: TushareResult.success(pd.DataFrame()))
        provider = GLMProvider(api_key="sk-xxx")
        positions = [("900001.SH", "持仓甲")]
        watchlist = [(f"60000{i}.SH", f"自选{i}") for i in range(4)]
        report = build_news_alerts(
            D, positions, watchlist, provider=provider,
            transport=httpx.MockTransport(self._slow_handler(calls, delay=0.05)),
            db_path=db, llm_budget_seconds=0.08,
        )
        status = next(s for s in report.scan_statuses if s.source == SOURCE_LLM_PREFIX)
        assert status.codes_skipped >= 3   # 4 只自选里至少 3 只被跳过
        # 真正发起过调用的第一只必须是持仓(900001),不是任何一只自选。
        assert "900001" in calls[0]
        assert not any("900001" in c for c in calls[1:])  # 持仓只有一只,不会出现第二次

    def test_ample_budget_scans_everything_no_skips(self, monkeypatch, db):
        calls: list = []
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda s, e: TushareResult.success(pd.DataFrame()))
        provider = GLMProvider(api_key="sk-xxx")
        report = build_news_alerts(
            D, [("900001.SH", "持仓甲")], [("600001.SH", "自选乙")],
            provider=provider, transport=httpx.MockTransport(self._slow_handler(calls, delay=0.0)),
            db_path=db, llm_budget_seconds=300.0,
        )
        status = next(s for s in report.scan_statuses if s.source == SOURCE_LLM_PREFIX)
        assert status.codes_skipped == 0
        assert len(calls) == 2

    def test_default_budget_constant_is_documented_heuristic(self):
        from neckline.report.news_alerts import LLM_SCAN_BUDGET_SECONDS
        assert LLM_SCAN_BUDGET_SECONDS > 0


class TestBuildNewsAlertsEmptyCodes:
    def test_empty_position_and_watchlist_returns_scanned_true_no_network(self, monkeypatch, db):
        def boom(*a, **kw):
            raise AssertionError("空扫描对象不应发起任何 TuShare/LLM 调用")

        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", boom)
        report = build_news_alerts(D, [], [], provider=GLMProvider(api_key="sk-xxx"), db_path=db)
        assert report.items == []
        assert all(s.scanned for s in report.scan_statuses)

    def test_priority_ordering_dedupes_code_appearing_in_both_lists(self, monkeypatch, db):
        """同一票既是持仓又是自选(理论边界情形)——去重后只扫一次,保留持仓身份
        (排在前面),不因为同码在自选里又出现一次而重复调用。"""
        calls: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "无异常。\n结论:未发现"}}],
            })

        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda s, e: TushareResult.success(pd.DataFrame()))
        provider = GLMProvider(api_key="sk-xxx")
        build_news_alerts(
            D, [("600001.SH", "持仓名")], [("600001.SH", "自选名")],
            provider=provider, transport=httpx.MockTransport(handler), db_path=db,
        )
        assert len(calls) == 1   # 不是 2


class TestEmptyNewsAlertsReportFactory:
    def test_both_sources_marked_unscanned_with_reason(self):
        report = empty_news_alerts_report(D, reason="编排异常")
        assert len(report.scan_statuses) == 2
        assert all(s.scanned is False for s in report.scan_statuses)
        assert all(s.reason == "编排异常" for s in report.scan_statuses)
        assert report.items == []


class TestPublicDictSafety:
    def test_to_public_dict_is_json_safe(self, monkeypatch, db):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda s, e: TushareResult.success(_holdertrade_df()),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        d = report.to_public_dict()
        assert d["tradeDate"] == "2026-07-24"
        assert d["items"][0]["category"] == NewsCategory.REDUCTION
        assert "scanStatuses" in d
        import json
        json.dumps(d)

    def test_scan_statuses_public_excludes_items(self, monkeypatch, db):
        """`scan_statuses_public()` 只给 `store.save_report(news_alerts_scan=...)`
        用——只带扫描状态,不重复携带 items(items 已落独立 news_alerts 表)。"""
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda s, e: TushareResult.success(_holdertrade_df()),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], [], provider=None, db_path=db)
        scan_pub = report.scan_statuses_public()
        assert all("items" not in s for s in scan_pub)
        assert len(scan_pub) == 2
