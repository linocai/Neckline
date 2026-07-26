"""消息面扫描单测(plan §五 v1.3-③-C4)。锁死:① 减持(TuShare stk_holdertrade)
——只取 in_de='DE'、只取扫描对象代码内的行、TuShare 无 token/调用失败/空结果
分别的降级形状;② 立案/暴雷/监管(LLM)——缺 provider 整批零调用降级、逐票扫描、
部分标的失败不污染其余标的;③ `build_news_alerts` 把两源结果合并、`codes` 为空
时零 I/O;④ 「没扫到 vs 扫了没有」——`scan_statuses` 的 `scanned`/`reason` 语义;
⑤ `llm.news_scan` 与本模块的类别字符串常量对拍(防两处漂移)。"""

from __future__ import annotations

from datetime import date

import httpx
import pandas as pd
import pytest

import neckline.report.news_alerts as news_alerts_mod
from neckline.data.tushare_client import TushareResult
from neckline.llm import news_scan
from neckline.llm.base import LLMResult
from neckline.llm.providers.glm import GLMProvider
from neckline.report.news_alerts import (
    NewsCategory,
    SOURCE_LLM_PREFIX,
    SOURCE_TUSHARE_HOLDERTRADE,
    build_news_alerts,
    empty_news_alerts_report,
)

D = date(2026, 7, 24)


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
    def test_de_rows_within_target_codes_become_items(self, monkeypatch):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(_holdertrade_df()),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        reduction_items = [i for i in report.items if i.category == NewsCategory.REDUCTION]
        assert len(reduction_items) == 1
        it = reduction_items[0]
        assert it.ts_code == "600001.SH"
        assert it.source == SOURCE_TUSHARE_HOLDERTRADE
        assert "张三" in it.summary and "减持" in it.summary
        assert "50,000" in it.summary or "50000" in it.summary

    def test_duplicate_tushare_rows_for_same_event_collapse_to_one_item(self, monkeypatch):
        """2026-07-26 端到端真实数据验证时发现:TuShare 对同一笔披露会原样返回重复行
        (实测 301358.SZ 同一 holder/ann_date/change_vol/change_ratio 出现两次)——本模块
        须按完整字段去重,不能把源头的重复原样透到报告(用户会误读成两笔独立减持)。"""
        dup = pd.concat([_holdertrade_df(), _holdertrade_df()], ignore_index=True)
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda start, end: TushareResult.success(dup))
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        reduction_items = [i for i in report.items if i.category == NewsCategory.REDUCTION]
        assert len(reduction_items) == 1   # 一只票一条,不是两条

    def test_multiple_distinct_reduction_events_same_code_consolidated_into_one_item(self, monkeypatch):
        """同一票在窗口内有多个真实不同的减持事件(不同股东/不同公告日)——UNIQUE
        (ts_code,trade_date,category) 只留一行,须合并进一条 summary,不能任由后写覆盖
        丢掉前面的事件。"""
        rows = pd.concat([
            _holdertrade_df(holder_name="张三", ann_date="20260722", change_vol=10000.0, change_ratio=0.1),
            _holdertrade_df(holder_name="李四", ann_date="20260724", change_vol=20000.0, change_ratio=0.2),
        ], ignore_index=True)
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda start, end: TushareResult.success(rows))
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        reduction_items = [i for i in report.items if i.category == NewsCategory.REDUCTION]
        assert len(reduction_items) == 1
        assert "张三" in reduction_items[0].summary
        assert "李四" in reduction_items[0].summary

    def test_events_beyond_cap_are_truncated_with_a_note(self, monkeypatch):
        from neckline.report.news_alerts import _MAX_REDUCTION_EVENTS_IN_SUMMARY

        rows = pd.concat([
            _holdertrade_df(holder_name=f"股东{i}", ann_date="20260724", change_vol=float(i), change_ratio=0.01)
            for i in range(_MAX_REDUCTION_EVENTS_IN_SUMMARY + 2)
        ], ignore_index=True)
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda start, end: TushareResult.success(rows))
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        reduction_items = [i for i in report.items if i.category == NewsCategory.REDUCTION]
        assert len(reduction_items) == 1
        assert "另有 2 笔未展示" in reduction_items[0].summary

    def test_in_rows_excluded_increase_is_not_a_reduction_alert(self, monkeypatch):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(_holdertrade_df(in_de="IN")),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        assert report.items == []

    def test_rows_outside_target_codes_excluded(self, monkeypatch):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(_holdertrade_df(ts_code="900001.SH")),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        assert report.items == []

    def test_no_token_or_call_failure_degrades_scan_status_not_confirmed_clean(self, monkeypatch):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.fail("token 缺失"),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        status = next(s for s in report.scan_statuses if s.source == SOURCE_TUSHARE_HOLDERTRADE)
        assert status.scanned is False
        assert "token 缺失" in status.reason
        assert report.items == []   # 空列表,但读者须看 scanned=False,不能当"确认无减持"

    def test_empty_result_is_scanned_true_no_items(self, monkeypatch):
        """该窗口内本就没有任何减持公告是正常情况(非失败)——scanned=True。"""
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda start, end: TushareResult.success(pd.DataFrame()),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        status = next(s for s in report.scan_statuses if s.source == SOURCE_TUSHARE_HOLDERTRADE)
        assert status.scanned is True
        assert status.reason == ""
        assert report.items == []

    def test_lookback_window_passed_to_tushare(self, monkeypatch):
        captured = {}

        def fake(start, end):
            captured["start"], captured["end"] = start, end
            return TushareResult.success(pd.DataFrame())

        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", fake)
        build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        assert captured["end"] == "20260724"
        assert captured["start"] < captured["end"]


class TestLLMCategoriesScan:
    def test_none_provider_skips_all_codes_without_any_call(self, monkeypatch):
        # 本测试聚焦 LLM 侧降级,但 build_news_alerts 会同时跑减持侧(TuShare)——
        # 显式桩掉,避免单测意外发起真实网络调用(且真实调用会把
        # tushare_client 模块级 _get_pro() 缓存污染成"已用真 token 初始化",
        # 影响同一 pytest 进程内后续依赖 isolated_env 无 token 降级路径的测试)。
        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", lambda s, e: TushareResult.success(pd.DataFrame()))
        report = build_news_alerts(
            D, [("600001.SH", "甲"), ("600002.SH", "乙")], provider=None,
        )
        status = next(s for s in report.scan_statuses if s.source == SOURCE_LLM_PREFIX)
        assert status.scanned is False
        assert "LLM_PROVIDER" in status.reason
        assert status.codes_total == 2
        assert not any(i.source.startswith(SOURCE_LLM_PREFIX) for i in report.items)

    def test_successful_scan_across_multiple_codes(self, monkeypatch):
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
            D, [("600001.SH", "甲"), ("600002.SH", "乙")],
            provider=provider, transport=httpx.MockTransport(handler),
        )
        llm_items = [i for i in report.items if i.source.startswith(SOURCE_LLM_PREFIX)]
        assert len(llm_items) == 1
        assert llm_items[0].ts_code == "600001.SH"
        assert llm_items[0].category == NewsCategory.INVESTIGATION
        status = next(s for s in report.scan_statuses if s.source == SOURCE_LLM_PREFIX)
        assert status.scanned is True
        assert status.codes_failed == 0

    def test_one_code_failure_does_not_drop_other_codes(self, monkeypatch):
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
            D, [("600001.SH", "甲"), ("600002.SH", "乙")],
            provider=provider, transport=httpx.MockTransport(handler),
        )
        llm_items = [i for i in report.items if i.source.startswith(SOURCE_LLM_PREFIX)]
        assert len(llm_items) == 1
        assert llm_items[0].ts_code == "600002.SH"
        status = next(s for s in report.scan_statuses if s.source == SOURCE_LLM_PREFIX)
        assert status.scanned is True
        assert status.codes_failed == 1
        assert "1/2" in status.reason


class TestBuildNewsAlertsEmptyCodes:
    def test_empty_codes_returns_scanned_true_no_network(self, monkeypatch):
        def boom(*a, **kw):
            raise AssertionError("空扫描对象不应发起任何 TuShare/LLM 调用")

        monkeypatch.setattr(news_alerts_mod, "ts_stk_holdertrade", boom)
        report = build_news_alerts(D, [], provider=GLMProvider(api_key="sk-xxx"))
        assert report.items == []
        assert all(s.scanned for s in report.scan_statuses)


class TestEmptyNewsAlertsReportFactory:
    def test_both_sources_marked_unscanned_with_reason(self):
        report = empty_news_alerts_report(D, reason="编排异常")
        assert len(report.scan_statuses) == 2
        assert all(s.scanned is False for s in report.scan_statuses)
        assert all(s.reason == "编排异常" for s in report.scan_statuses)
        assert report.items == []


class TestPublicDictSafety:
    def test_to_public_dict_is_json_safe(self, monkeypatch):
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda s, e: TushareResult.success(_holdertrade_df()),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        d = report.to_public_dict()
        assert d["tradeDate"] == "2026-07-24"
        assert d["items"][0]["category"] == NewsCategory.REDUCTION
        assert "scanStatuses" in d
        import json
        json.dumps(d)

    def test_scan_statuses_public_excludes_items(self, monkeypatch):
        """`scan_statuses_public()` 只给 `store.save_report(news_alerts_scan=...)`
        用——只带扫描状态,不重复携带 items(items 已落独立 news_alerts 表)。"""
        monkeypatch.setattr(
            news_alerts_mod, "ts_stk_holdertrade",
            lambda s, e: TushareResult.success(_holdertrade_df()),
        )
        report = build_news_alerts(D, [("600001.SH", "示例甲")], provider=None)
        scan_pub = report.scan_statuses_public()
        assert all("items" not in s for s in scan_pub)
        assert len(scan_pub) == 2
