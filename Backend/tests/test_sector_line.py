"""板块线：粗筛、挑候选、归并、成分股、评估、收口，以及接到晨报后的真实入口。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.evaluation.search import SearchClient, SearchOutcome
from neckline.mcp.server import _report as mcp_report
from neckline.morning import cli, db
from neckline.morning.api import create_router
from neckline.morning.replay import _summary, run_replay
from neckline.morning.report import LoadedConfig, Pusher, load_config, run_morning
from neckline.morning.schedule import CST, previous_trading_day
from neckline.morning.sector_line import (
    apply_prefilter, assign_numbers, event_id, opening_text, parse_candidate_output,
    _merge_mapping_halves, parse_evaluation_output, parse_mapping_output, run_sector_line, split_batches,
)
from neckline.morning.sector_prompt import (
    CANDIDATE_SYSTEM, EVALUATION_SYSTEM, MAPPING_CONTRACT, MAPPING_SYSTEM, candidate_line,
    evaluation_user_prompt,
)
from neckline.morning.sectors import DailyFill, trading_days_ending
from neckline.morning.stock_pool import store_rows
from neckline.screening.deepseek import ChatError, ChatResult, ChatUnknown, DeepSeekClient, canonical
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window, to_message

from tests.test_morning import FakeApns, FakeModel, _client, _config, _seed_window, _with_device, at, doc

DAY = date(2026, 10, 13)
_FROZEN_CANDIDATES = "dc7a543b7e4e059bdb23ab01d293a80b1d83f01d52365e2b65ba9758fd8ac804"
_FROZEN_MAPPING = "bbcf745a45dc90cce7f9fa8fe4184082a931ad54df8e37032a6bd1f5218f1d8e"
_FROZEN_EVALUATION = "fe272a6bd10c6f82e99a26e35d433f41330704e82405d4dc2ef387600895c6f5"
_FROZEN_USER = "86a31227d6519a384ab67c9af878a41c715d6d8a510d990e69092fe08cff6dc9"
_FROZEN_BODY = "91fb33e0a66134cdbd015a29ec9c53ec4082c6f4bb79f8e9eb5791dc9c6bf6bc"
_V6_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "report-v6-two-lines.json"


def _stable_report(payload: dict) -> dict:
    """夹具对照只看两条线的交付字段。stats 里的耗时随运行变化。"""
    content = payload["content"]
    return {
        "reportId": payload["reportId"], "tradeDate": payload["tradeDate"], "status": payload["status"],
        "gapSummary": payload["gapSummary"], "configVersion": payload["configVersion"],
        "entries": content["entries"], "sectors": content["sectors"],
    }


def _pool() -> CompanyPool:
    return CompanyPool([
        Company("300001.SZ", "甲公司", ("甲公司",)),
        Company("300002.SZ", "乙公司", ("乙公司",)),
        Company("300003.SZ", "丙公司", ("丙公司",)),
        Company("300008.SZ", "丁公司", ("丁公司",)),
    ])


def _db(tmp_path) -> object:
    path = tmp_path / "v4.db"
    db.init_schema(path)
    return path


def _rules(pool: CompanyPool, **sector_edits) -> LoadedConfig:
    base = load_config()
    rules = json.loads(json.dumps(base.rules))
    rules["sectors"]["candidates"]["concurrency"] = 1
    rules["sectors"]["evaluation"]["concurrency"] = 1
    for key, value in sector_edits.items():
        rules["sectors"][key] = value
    return LoadedConfig(rules=rules, sha256=base.sha256, pool=pool, profiles={})


def _window() -> Window:
    return Window(datetime(2026, 10, 12, 15, tzinfo=CST), datetime(2026, 10, 13, 8, tzinfo=CST),
                  datetime(2026, 10, 13, 8, 2, tzinfo=CST))


def _at(hour: int, minute: int = 0, day: int = 12) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=CST)


def _msg(doc_id: str, title: str, text: str, when: datetime, source: str = "jin10-flash") -> Message:
    label = "巨潮公告" if source == "cninfo-announcement" else "金十快讯"
    return Message(doc_id, 1, source, label, f"https://example.test/{doc_id}", title, text, when, when)


def _concept(path, codes=("300001.SZ", "300002.SZ", "300003.SZ"), name="人工智能", ts_code="885001.TI") -> None:
    with db.write(path) as conn:
        db.replace_sector_concepts(conn, [{
            "tsCode": ts_code, "name": name, "memberCount": len(codes), "members": list(codes),
        }], refreshed_at="2026-10-12T00:00:00+00:00", sha256="ab")


def _answer(codes, *, verdict="推", fresh_pass=True, weight_pass=True, position_pass=True,
           carriers_pass=True, views=None) -> dict:
    return {
        "title": "值得看的板块", "verdict": verdict, "conclusion": "资金会看一眼",
        "questions": {
            "fresh": {"pass": fresh_pass, "text": "是新消息"},
            "weight": {"pass": weight_pass, "text": "分量够"},
            "position": {"pass": position_pass, "text": "位置不高"},
            "carriers": {"pass": carriers_pass, "text": "有人接"},
        },
        "leaders": [{"code": code, "why": "消息点到它", "price": "明显高开不追"} for code in codes],
        "risk": "可能落空",
        "views": views if views is not None else [{"by": "媒体", "name": "来源", "text": "转述", "ref": "m1"}],
    }


def _http(payload, status=200) -> httpx.Response:
    return httpx.Response(status, json={
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3, "prompt_cache_hit_tokens": 1, "reasoning_tokens": 2},
    })


def _ok(payload) -> ChatResult:
    return ChatResult(json.dumps(payload, ensure_ascii=False),
                      {"prompt_tokens": 4, "completion_tokens": 3}, "digest", False, 0.1, "stop")


class Scripted:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[list[dict]] = []

    def complete(self, messages, options):
        self.calls.append(messages)
        return self.handler(messages, options)


def _leader_codes(prompt: str) -> list[str]:
    chunk = prompt.split("成分股名单", 1)[-1]
    return re.findall(r"(?m)^(\d{6}) ", chunk)


def _event(payload: dict) -> dict:
    payload.setdefault("weighty", True)
    payload.setdefault("weight", "分量够，能带动板块")
    return payload


def _ids(prompt: str) -> list[str]:
    return re.findall(r"\[(n\d+)\]", prompt)


def _script(mapping=None, *, candidate_ids=None, evaluate=None):
    """按系统提示词分发。candidate_ids 把模型返回的编号换成给定的，用来测「只认本批」。"""

    def handler(messages, _options):
        system = messages[0]["content"]
        user = messages[1]["content"]
        if "初筛员" in system:
            ids = list(candidate_ids) if candidate_ids is not None else _ids(user)
            return _ok({"events": [{"ids": ids, "event": "事"}] if ids else []})
        if "板块分析员" in system:
            if mapping is not None:
                return mapping(user)
            ids = _ids(user)
            return _ok({"events": [_event({
                "title": "行业座谈会", "ids": ids, "sector": "人工智能", "reason": "直接受益", "repeat": False,
            })], "dropped": []})
        codes = _leader_codes(user)
        if len(messages) > 2:
            shown = codes
        elif evaluate is not None:
            shown = evaluate(codes)
        else:
            shown = codes
        return _ok(_answer(shown))

    return Scripted(handler)


def _run(path, messages, client, pool=None, *, clock=None, search=None, live=False, skip=(), config=None):
    pool = pool or _pool()
    return run_sector_line(
        path, report_id="morning-2026-10-13", day=DAY, window=_window(), messages=messages,
        config=config or _rules(pool), pool=pool, skip=list(skip), client=client,
        clock=clock or (lambda: _at(8, day=13)), search_client=search, sector_live=live)


def _rows(path):
    with db.read(path) as conn:
        return db.list_sector_events(conn, "morning-2026-10-13")


# ------------------------------------------------------------------ 粗筛


def _raw(source: str, title: str, text: str) -> Message:
    return to_message({
        "documentId": "doc-" + hashlib.sha256(title.encode()).hexdigest()[:8],
        "revision": 1, "sourceKey": source, "url": "https://flash.jin10.com/detail/x",
        "publishedAt": "2026-11-01T15:19:00+08:00", "firstSeenAt": "2026-11-01T15:19:00+08:00",
        "fetchedAt": "2026-11-01T15:19:00+08:00", "text": text, "meta": {"title": title},
    })


def test_prefilter_drops_the_first_matching_rule_and_keeps_the_symposium_flashes():
    settings = load_config().rules["sectors"]["prefilter"]
    cases = [
        ("dropSources", _msg("c1", "关于召开行业座谈会", "邀请机构参加。", _at(16), "cninfo-announcement"), True),
        ("dropSources-keep", _msg("c2", "部委印发行业补贴方案", "明确了补贴范围。", _at(16)), False),
        ("otherListed", _raw("jin10-flash", "600519.SH获得大额订单", "公司公告了订单。"), True),
        ("otherListed-keep", _raw("jin10-flash", "行业龙头获得大额订单", "没有点名代码。"), False),
        ("overseas", _raw("jin10-flash", "美联储维持利率", "当地市场波动。"), True),
        ("overseas-china", _raw("jin10-flash", "美联储讨论下一步", "此举对华影响有限。"), False),
        ("opening-title", _raw("jin10-flash", "行业补贴方案落地", "美联储维持利率，当地波动。"), False),
        ("opening-body", to_message({
            "documentId": "doc-body", "revision": 1, "sourceKey": "jin10-flash",
            "url": "https://flash.jin10.com/detail/b", "publishedAt": "2026-11-01T16:00:00+08:00",
            "firstSeenAt": "2026-11-01T16:00:00+08:00", "fetchedAt": "2026-11-01T16:00:00+08:00",
            "text": "美联储维持利率，当地市场波动。", "meta": {},
        }), True),
        ("macroRelease", _raw("jin10-flash", "统计局公布CPI月率 0.2", "和上月差不多。"), True),
        ("macroRelease-keep", _raw("jin10-flash", "统计局说明统计口径", "没有发布新数字。"), False),
        ("tape", _raw("jin10-flash", "早盘两市分化", "成交较昨日放大。"), True),
        ("tape-keep", _raw("jin10-flash", "部委印发补贴方案", "覆盖三个行业。"), False),
        ("research", _raw("jin10-flash", "【中信证券】行业点评：看好设备", "报告认为需求回升。"), True),
        ("research-keep", _raw("jin10-flash", "【部委】印发行业方案", "方案列出了试点。"), False),
        ("periodic", _raw("cninfo-announcement", "2024年第三季度报告", "详见附件。"), True),
        ("periodic-keep", _msg("p2", "第三季度经营情况通报", "会议传达了要求。", _at(16)), False),
        ("roundup-jin10", _raw("jin10-flash", "金十数据整理：昨日今晨重要新闻汇总（11月1日）", "汇总。"), True),
        ("roundup-breakfast", _raw("jin10-flash", "金十数据全球财经早餐 | 2026年11月2日", "早餐。"), True),
        ("roundup-events", _raw("jin10-flash", "影响市场重大事件：中国首次载人月球探测任务乘组……", "任务。"), True),
        ("roundup-review", _raw("jin10-flash", "A股每日市场要闻回顾（2026-10-30）", "回顾。"), True),
        ("roundup-mysteel", _raw("jin10-flash", "Mysteel日报：全国生铁价格弱稳观望", "价格。"), True),
        ("symposium-1", _raw("jin10-flash", "央行、证监会将于明天召开座谈会", "邀请金融机构参加。"), False),
        ("symposium-2", to_message({
            "documentId": "doc-s2", "revision": 1, "sourceKey": "jin10-flash",
            "url": "https://flash.jin10.com/detail/s2", "publishedAt": "2026-11-02T05:17:00+08:00",
            "firstSeenAt": "2026-11-02T05:17:00+08:00", "fetchedAt": "2026-11-02T05:17:00+08:00",
            "text": "金融监管部门今日召开两个座谈会，研究相关工作。",
            "meta": {"title": "金融监管部门今日召开两个座谈会"},
        }), False),
    ]
    # 定期报告这条用了巨潮来源，会先被来源规则去掉。换一条标题相同、来源是快讯的。
    cases[14] = ("periodic", _raw("jin10-flash", "2024年第三季度报告", "详见附件。"), True)
    messages = [item[1] for item in cases]
    kept, dropped = apply_prefilter(messages, settings)
    kept_ids = {message.document_id for message in kept}
    for name, message, should_drop in cases:
        assert (message.document_id not in kept_ids) is should_drop, name
    kept_titles = {message.title for message in kept}
    assert "央行、证监会将于明天召开座谈会" in kept_titles
    assert "金融监管部门今日召开两个座谈会" in kept_titles
    assert dropped["dropSources"] == 1
    assert dropped["otherListed"] == 1
    assert dropped["overseas"] == 2  # 美联储那条，加上无标题的正文
    assert dropped["macroRelease"] == 1
    assert dropped["tape"] == 1
    assert dropped["research"] == 1
    assert dropped["periodic"] == 1
    assert dropped["roundup"] == 5
    assert opening_text(_msg("x", "", "正文开头", _at(16)), 80) == "正文开头"
    assert opening_text(_msg("y", "有标题", "正文", _at(16)), 80) == "有标题"


# ------------------------------------------------------------------ 挑候选、归并


def test_batches_keep_only_ids_from_the_same_batch_and_skip_an_empty_one(tmp_path):
    assert split_batches([], batch_chars=100, head_chars=80) == []
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    first = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))
    second = _msg("b", "监管放开行业准入", "试点扩大到三个省。", _at(17))
    numbered = assign_numbers([second, first])
    assert [number for number, _message in numbered] == ["n1", "n2"]
    line = candidate_line(numbered[0][0], numbered[0][1], 80)
    assert len(split_batches(numbered, batch_chars=len(line), head_chars=80)) == 2
    assert parse_candidate_output('{"events":[{"ids":["n1","n9"]}]}', {"n1"}) == {"n1"}

    def foreign(messages, _options):
        user = messages[1]["content"]
        own = _ids(user)
        other = ["n2"] if own == ["n1"] else ["n1"]
        return _ok({"events": [{"ids": other + ["n99"], "event": "事"}]})

    client = Scripted(foreign)
    outcome = _run(path, [first, second], client, pool)
    assert len(client.calls) == 2
    assert outcome.content["entries"] == [] and outcome.gap is None

    empty = Scripted(lambda *_a: (_ for _ in ()).throw(AssertionError("空批不该调用")))
    quiet = _run(_db(tmp_path / "empty"), [_msg("c", "公告", "正文", _at(16), "cninfo-announcement")], empty, pool)
    assert quiet.gap is None and quiet.content == {"candidates": 0, "evaluated": 0, "overflow": 0, "entries": []}
    assert empty.calls == []


def test_a_batch_cut_off_by_length_is_skipped_and_the_gap_counts_it(tmp_path):
    # 回放 10/29、11/04 各有一批推理写满上限、没有输出：其余批次照常往下走，缺口写明少了几批
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    first = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))
    second = _msg("b", "监管放开行业准入", "试点扩大到三个省。", _at(17))
    line = candidate_line("n1", first, 80)
    candidates = dict(_rules(pool).rules["sectors"]["candidates"], batchChars=len(line))
    normal = _script()

    def handler(messages, options):
        if "初筛员" in messages[0]["content"] and _ids(messages[1]["content"]) == ["n1"]:
            return ChatResult("", {"prompt_tokens": 4, "completion_tokens": 9}, "digest", False, 0.1, "length")
        return normal.handler(messages, options)

    outcome = _run(path, [first, second], Scripted(handler), pool, config=_rules(pool, candidates=candidates))
    assert outcome.stats["candidates"]["batches"] == 2 and outcome.stats["candidates"]["failedBatches"] == 1
    assert outcome.gap == "板块线有 1 批消息没挑成（共 2 批）"
    assert len(outcome.content["entries"]) == 1


def test_mapping_checks_names_duplicates_repeats_and_skips_the_call_without_candidates(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    message = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))

    def unknown(user):
        return _ok({"events": [_event({
            "title": "补贴", "ids": _ids(user), "sector": "不存在的板块", "reason": "清单里没有", "repeat": False,
        })], "dropped": []})

    client = _script(mapping=unknown)
    outcome = _run(path, [message], client, pool)
    assert len(client.calls) == 2
    row = _rows(path)[0]
    assert row["state"] == "skipped" and row["sector"] is None and row["reason"] == "清单里没有"
    assert outcome.content["entries"] == [] and outcome.stats["mapping"]["null"] == 1

    def duplicated(user):
        ids = _ids(user)
        return _ok({"events": [
            _event({"title": "第一件", "ids": ids[:2], "sector": "人工智能", "reason": "直接", "repeat": False}),
            _event({"title": "第二件", "ids": [ids[1], ids[2]], "sector": "人工智能", "reason": "另一件", "repeat": False}),
        ], "dropped": []})

    three = [
        _msg("a", "部委印发行业补贴方案", "第一件事。", _at(16)),
        _msg("b", "监管放开行业准入", "第二件事。", _at(17)),
        _msg("c", "国家级项目开工", "第三件事。", _at(18)),
    ]
    other = _db(tmp_path / "dup")
    _concept(other)
    dup = _script(mapping=duplicated)
    _run(other, three, dup, pool)
    stored = _rows(other)
    assert [row["title"] for row in stored] != [] and len(stored) == 2
    packages = {row["title"]: row["package"] for row in stored}
    assert len(packages["第一件"]["messages"]) == 2
    assert [item["title"] for item in packages["第二件"]["messages"]] == ["国家级项目开工"]

    def repeated(user):
        return _ok({"events": [_event({
            "title": "旧座谈会", "ids": _ids(user), "sector": "人工智能", "reason": "昨天推过", "repeat": True,
        })], "dropped": []})

    again = _db(tmp_path / "repeat")
    _concept(again)
    repeat = _script(mapping=repeated)
    repeated_outcome = _run(again, [message], repeat, pool)
    assert len(repeat.calls) == 2
    assert _rows(again)[0]["state"] == "skipped" and _rows(again)[0]["repeat"] is True
    assert repeated_outcome.content["entries"] == [] and repeated_outcome.stats["mapping"]["repeat"] == 1

    none = _script(candidate_ids=[])
    silent = _db(tmp_path / "none")
    _concept(silent)
    empty = _run(silent, [message], none, pool)
    assert len(none.calls) == 1 and "板块分析员" not in none.calls[0][0]["content"]
    assert empty.gap is None and empty.content["candidates"] == 0


def test_members_skip_the_main_list_and_render_one_line_each(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path, ("300008.SZ", "300001.SZ", "300002.SZ"))
    as_of = date(2026, 10, 12)
    days = trading_days_ending(as_of, 20)
    with db.write(path) as conn:
        for day in days:
            db.write_market_day(conn, day.isoformat(), [
                {"code": "300008.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
                 "pctChg": 1, "amount": 1_000_000, "limitUp": False},
                {"code": "300001.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
                 "pctChg": 1, "amount": 100_000, "limitUp": False},
                {"code": "300002.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
                 "pctChg": 10, "amount": 1_000, "limitUp": True},
            ])
        db.replace_stock_pool(conn, [
            {"code": "300001.SZ", "name": "甲公司", "summary": "做充电", "summarySource": "tushare",
             "circMv": 1_234_000, "totalMv": 2_000_000, "mvDate": "2026-10-12"},
            {"code": "300002.SZ", "name": "乙公司", "summary": "做材料", "summarySource": "tushare",
             "circMv": 1000, "totalMv": 2000, "mvDate": "2026-10-12"},
        ], refreshed_at="2026-10-12T00:00:00+00:00", sha256="ab", source="tushare")
    client = _script()
    outcome = _run(path, [_msg("a", "部委印发行业补贴方案", "正文里没有简称。", _at(16))], client, pool,
                   skip=["300008"])
    prompt = client.calls[-1][1]["content"]
    assert "成分股名单（池内、已去掉今天个股名单里的，共 2 只）：" in prompt
    assert "300001 甲公司｜主营：做充电｜流通市值 123.4 亿元" in prompt
    assert "300002" in prompt and "300008" not in prompt and "龙头" not in prompt
    package = _rows(path)[0]["package"]
    assert [item["code"] for item in package["members"]] == ["300001", "300002"]
    assert "leaderRankings" not in package and "leaders" not in package
    assert package["members"][0]["named"] is False
    assert outcome.content["entries"][0]["leaders"][0]["companyCode"] == "300001"
    assert outcome.content["entries"][0]["leaders"][0]["companyName"] == "甲公司"
    assert outcome.content["entries"][0]["leaders"][0]["why"] == "消息点到它"
    assert outcome.content["entries"][0]["leaders"][0]["price"] == "明显高开不追"


# ------------------------------------------------------------------ 评估、收口


class _Search:
    def __init__(self):
        self.queries = []

    def search(self, *, query, count, ori_question):
        self.queries.append((query, count, ori_question))
        return SearchOutcome(payload={"results": [{
            "title": "媒体标题", "url": "https://news.example/a", "date": "2026-10-12T10:00:00+08:00",
            "summary": "认为有催化", "site_name": "财联社",
        }]}, from_receipt=False, seconds=0.2, error=None)


def test_evaluation_repairs_format_vetoes_and_publishes_views(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    message = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16, 30))
    client = _script(evaluate=lambda codes: ["300099"])
    outcome = _run(path, [message], client, pool)
    assert len(client.calls) == 4  # 候选、归并、评估、修正
    row = _rows(path)[0]
    assert row["state"] == "succeeded" and row["verdict"] == "推"
    assert [item["code"] for item in row["output"]["leaders"]] == ["300001", "300002", "300003"]
    assert outcome.stats["evaluation"]["repairs"] == 1 and outcome.stats["evaluation"]["repaired"] == 1

    veto_path = _db(tmp_path / "veto")
    _concept(veto_path)
    veto = _script()
    original = veto.handler

    def deny(messages, options):
        result = original(messages, options)
        if "评估员" in messages[0]["content"] and len(messages) == 2:
            payload = json.loads(result.content)
            payload["verdict"] = "推"
            payload["questions"]["fresh"]["pass"] = False
            result = _ok(payload)
        return result

    veto.handler = deny
    vetoed = _run(veto_path, [message], veto, pool)
    assert _rows(veto_path)[0]["verdict"] == "不推" and _rows(veto_path)[0]["output"]["vetoed"] is True
    assert vetoed.content["entries"] == [] and vetoed.stats["evaluation"]["vetoed"] == 1

    shown_path = _db(tmp_path / "shown")
    _concept(shown_path)
    search = _Search()
    views = [
        {"by": "媒体", "name": "财联社", "text": "认为有催化", "ref": "s1"},
        {"by": "媒体", "name": "幽灵", "text": "没有这条", "ref": "s9"},
    ]
    shown = _script()
    base = shown.handler

    def with_views(messages, options):
        result = base(messages, options)
        if "评估员" in messages[0]["content"] and len(messages) == 2:
            payload = json.loads(result.content)
            payload["views"] = views
            payload["title"] = "补贴落地"
            result = _ok(payload)
        return result

    shown.handler = with_views
    published = _run(shown_path, [message], shown, pool, search=search)
    entry = published.content["entries"][0]
    assert entry["title"] == "补贴落地"
    assert entry["sector"] == {"name": "人工智能", "code": "885001.TI", "memberCount": 3}
    assert entry["questions"] == {
        "fresh": "是新消息", "weight": "分量够", "position": "位置不高", "carriers": "有人接",
    }
    assert entry["firstPublished"] == "2026-10-12T16:30+08:00"
    assert entry["sources"][0]["title"] == "部委印发行业补贴方案"
    assert entry["sources"][0]["sourceLabel"] == "金十快讯"
    assert entry["views"][0]["sourceTitle"] == "媒体标题" and entry["views"][0]["sourceUrl"] == "https://news.example/a"
    assert "没有这条" not in json.dumps(entry["views"], ensure_ascii=False)
    assert published.stats["evaluation"]["droppedViews"] == 1
    query, count, question = search.queries[0]
    assert query == "行业座谈会 人工智能 before:2026-10-14"
    assert count == 10 and question == "这件事对人工智能板块影响多大，媒体和券商怎么看"
    assert set(published.stats["search"]) == {
        "enabled", "calls", "fromReceipt", "failed", "seconds", "returned", "kept", "dropped", "viewsCited",
    }
    assert published.stats["search"]["calls"] == 1 and published.stats["search"]["viewsCited"] == 1


def test_cutoff_before_the_call_and_before_the_repair(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    message = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))
    stage = {"late": False}

    def clock():
        return _at(9, 6, day=13) if stage["late"] else _at(8, day=13)

    def fill(*_args, **_kwargs):
        stage["late"] = True
        return DailyFill((), 0, None)

    monkey = pytest.MonkeyPatch()
    monkey.setattr("neckline.morning.sector_line._await_market_daily", fill)
    try:
        client = _script()
        outcome = _run(path, [message], client, pool, clock=clock, live=True)
    finally:
        monkey.undo()
    assert len(client.calls) == 2
    assert _rows(path)[0]["state"] == "gap" and _rows(path)[0]["error"] == "收口时刻前没评完"
    assert outcome.gap == "板块线没有完成：收口时刻前没评完"

    repair_path = _db(tmp_path / "repair")
    _concept(repair_path)
    flipped = {"late": False}

    def later():
        return _at(9, 6, day=13) if flipped["late"] else _at(8, day=13)

    def bad(messages, _options):
        system = messages[0]["content"]
        if "初筛员" in system or "板块分析员" in system:
            return _script().handler(messages, _options)
        flipped["late"] = True
        return _ok({"title": "缺字段"})

    client = Scripted(bad)
    late = _run(repair_path, [message], client, pool, clock=later)
    assert len(client.calls) == 3
    assert _rows(repair_path)[0]["state"] == "gap"
    assert late.stats["evaluation"]["repairs"] == 0


def test_unknown_evaluation_is_not_sent_again(tmp_path, monkeypatch):
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    message = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))
    hits = []

    def handler(request):
        hits.append(1)
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        if "初筛员" in system:
            return _http({"events": [{"ids": _ids(user), "event": "事"}]})
        if "板块分析员" in system:
            return _http({"events": [_event({
                "title": "补贴", "ids": _ids(user), "sector": "人工智能", "reason": "直接", "repeat": False,
            })], "dropped": []})
        raise httpx.ReadTimeout("no answer")

    def client():
        return DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
                              receipts=db.DbReceipts(path, "morning-2026-10-13"), timeout_seconds=5, max_attempts=2,
                              transport=httpx.MockTransport(handler))

    first = _run(path, [message], client(), pool)
    assert first.stats["evaluation"]["calls"] == 1 and _rows(path)[0]["state"] == "gap"
    sent = len(hits)
    second = _run(path, [message], client(), pool)
    assert len(hits) == sent and second.stats["evaluation"]["calls"] == 0
    with db.write(path) as conn:
        conn.execute("DELETE FROM sector_events")
    third = _run(path, [message], client(), pool)
    assert len(hits) == sent
    assert "上次调用结果不明" in (_rows(path)[0]["error"] or "")


def test_delivery_keeps_the_three_earliest_and_zero_is_legal(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    messages = [_msg(f"m{index}", f"第{index}件行业补贴", "方案落地。", _at(16 + index)) for index in range(5)]

    def mapping(user):
        ids = _ids(user)
        events = []
        for number in reversed(ids):
            events.append(_event({
                "title": f"事件{number}", "ids": [number], "sector": "人工智能", "reason": "直接", "repeat": False,
            }))
        return _ok({"events": events, "dropped": []})

    def titles(messages, options):
        result = _script(mapping=mapping).handler(messages, options)
        if "评估员" in messages[0]["content"] and len(messages) == 2:
            payload = json.loads(result.content)
            payload["title"] = re.search(r"事件：(事件n\d+)", messages[1]["content"]).group(1)
            result = _ok(payload)
        return result

    outcome = _run(path, messages, Scripted(titles), pool)
    assert [entry["title"] for entry in outcome.content["entries"]] == ["事件n1", "事件n2", "事件n3"]
    assert [entry["firstPublished"] for entry in outcome.content["entries"]] == [
        "2026-10-12T16:00+08:00", "2026-10-12T17:00+08:00", "2026-10-12T18:00+08:00",
    ]
    assert outcome.content["overflow"] == 2 and outcome.stats["overflow"] == 2
    assert outcome.stats["evaluation"]["verdicts"] == {"推": 5, "不推": 0}

    none = _script(candidate_ids=[])
    zero = _db(tmp_path / "zero")
    _concept(zero)
    quiet = _run(zero, messages[:1], none, pool)
    assert quiet.gap is None and quiet.content["entries"] == [] and quiet.content["evaluated"] == 0

    # 没有概念清单时不挑候选：一次模型调用也不发，如实写缺口
    calls = _script(candidate_ids=["n1"])
    blank = _run(_db(tmp_path / "blank"), messages[:1], calls, pool)
    assert blank.gap == "板块线没有完成：概念清单是空的" and blank.stats["candidates"]["calls"] == 0


def test_same_messages_keep_the_same_event_id():
    assert event_id(["b#1", "a#1"]) == event_id(["a#1", "b#1"])
    assert len(event_id(["a#1"])) == 16


# ------------------------------------------------------------------ 晨报入口


def _seed_sector_context(path, *, with_daily=True):
    _seed_window(path)
    with db.write(path) as conn:
        db.store_documents(conn, [
            doc("jin10-flash", "forum", "央行、证监会将于明天召开座谈会，邀请金融机构参加。",
                at("2026-10-12", "17:30"), at("2026-10-12", "20:00"), title="央行、证监会将于明天召开座谈会"),
        ])
        db.replace_sector_concepts(conn, [{
            "tsCode": "885001.TI", "name": "人工智能", "memberCount": 3,
            "members": ["300953.SZ", "300683.SZ", "300082.SZ"],
        }], refreshed_at="2026-10-12T00:00:00+00:00", sha256="cd")
        if not with_daily:
            return
        as_of = previous_trading_day(DAY)
        for day in trading_days_ending(as_of, 40):
            db.write_market_day(conn, day.isoformat(), [{
                "code": "300082.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
                "pctChg": 1, "amount": 1000, "limitUp": False,
            }])


def _sector_handler(model: FakeModel, *, mapping_status: int = 200, kill: BaseException | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/tools/search" in str(request.url):
            return httpx.Response(200, json={"results": [{
                "title": "媒体标题", "url": "https://news.example/a", "date": "2026-10-12T10:00:00+08:00",
                "summary": "认为有催化", "site_name": "财联社",
            }]})
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"] if len(body["messages"]) > 1 else ""
        if "初筛员" in system:
            return _http({"events": [{"ids": _ids(user), "event": "座谈会"}]})
        if "板块分析员" in system:
            if mapping_status != 200:
                return httpx.Response(mapping_status, json={"error": "down"})
            return _http({"events": [_event({
                "title": "金融座谈会", "ids": _ids(user), "sector": "人工智能", "reason": "直接受益", "repeat": False,
            })], "dropped": []})
        if "板块消息评估员" in system:
            if kill is not None and len(body["messages"]) == 2:
                raise kill
            codes = _leader_codes(user)
            return _http(_answer(codes, views=[{"by": "媒体", "name": "财联社", "text": "认为有催化", "ref": "s1"}]))
        return model(request)

    return handler


def _install_clients(monkeypatch, handler):
    class Wrapped(DeepSeekClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    class WrappedSearch:
        def __init__(self, **kwargs):
            from neckline.evaluation.search import SearchClient
            kwargs["transport"] = httpx.MockTransport(handler)
            self._client = SearchClient(**kwargs)

        def search(self, **kwargs):
            return self._client.search(**kwargs)

    monkeypatch.setattr(cli, "DeepSeekClient", Wrapped)
    monkeypatch.setattr(cli, "SearchClient", WrappedSearch)


def test_sector_failure_does_not_block_the_main_list(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path)
    _with_device(path)
    monkeypatch.setattr("neckline.morning.sector_line._await_market_daily",
                        lambda *_args, **_kwargs: DailyFill((), 0, "没取到"))
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    model = FakeModel()
    result = run_morning(
        path, now=at("2026-10-13", "08:02"), config=_config(), clock=lambda: at("2026-10-13", "08:02"),
        collector=lambda _at: {"status": "completed"},
        client_factory=lambda rid: _client(path, _sector_handler(model), rid),
        pusher=Pusher(path, FakeApns(), lambda: at("2026-10-13", "08:02")))
    assert result["state"] == "available" and result["entries"] == 2
    assert "2 家材料不全：海特生物、震裕科技" in result["gapSummary"]
    assert "板块线没有完成：日线没取到" in result["gapSummary"]
    with db.read(path) as conn:
        stored = db.get_report(conn, "2026-10-13")
    assert [entry["companyName"] for entry in stored["content"]["entries"]] == ["海特生物", "震裕科技"]
    assert stored["content"]["sectors"]["entries"] == []

    mapping_path = path.parent / "mapping.db"
    db.init_schema(mapping_path)
    _seed_sector_context(mapping_path)
    result = run_morning(
        mapping_path, now=at("2026-10-13", "08:02"), config=_config(), clock=lambda: at("2026-10-13", "08:02"),
        collector=lambda _at: {"status": "completed"},
        client_factory=lambda rid: _client(mapping_path, _sector_handler(FakeModel(), mapping_status=500), rid, attempts=1),
        pusher=Pusher(mapping_path, FakeApns(), lambda: at("2026-10-13", "08:02")))
    assert result["state"] == "available" and result["entries"] == 2
    assert "板块线没有完成：归并没有完成" in result["gapSummary"]


def test_cli_morning_round_trip_includes_sectors_and_resume_does_not_rebill(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path)
    from neckline.morning.stock_pool import store_rows as _store
    rows = [{"code": code, "name": name, "fullname": "", "listDate": "2010-01-01", "aliases": [name],
             "summary": None, "summarySource": "none"}
            for code, name in (("300953.SZ", "震裕科技"), ("300683.SZ", "海特生物"), ("300082.SZ", "奥克股份"))]
    with db.write(path) as conn:
        _store(conn, rows, now=datetime(2026, 10, 12, 4, tzinfo=timezone.utc), source="tushare")
    _with_device(path)

    class _Clock:
        current = datetime(2026, 10, 13, 0, 2, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

        fromisoformat = staticmethod(datetime.fromisoformat)

    monkeypatch.setattr(cli, "datetime", _Clock)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", "r" * 32)
    monkeypatch.setattr(cli, "run_collection", lambda *_a, **_k: {"status": "completed"})
    monkeypatch.setattr("neckline.push.apns.send_push", FakeApns())
    hits = []

    class Killed(BaseException):
        pass

    def handler(request):
        hits.append(str(request.url))
        return _sector_handler(FakeModel(), kill=Killed())(request)

    _install_clients(monkeypatch, handler)
    with pytest.raises(Killed):
        cli.main(["--db", str(path), "morning", "--now", "2026-10-13T08:02:00+08:00"])
    with db.read(path) as conn:
        assert db.get_report(conn, "2026-10-13")["status"] == "running"
    sent = len(hits)
    assert cli.main(["--db", str(path), "morning", "--now", "2026-10-13T08:02:00+08:00"]) == 0
    assert len(hits) == sent
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=lambda: None))
    latest = TestClient(app).get("/api/v4/reports/latest").json()
    via_mcp = mcp_report("2026-10-13")
    assert via_mcp["content"] == latest["content"]
    assert "sectors" in latest["content"]
    assert [entry["companyName"] for entry in latest["content"]["entries"]] == ["海特生物", "震裕科技"]
    assert "price" in latest["content"]["entries"][0]["questions"]
    sector_questions = latest["content"]["sectors"]["entries"]
    assert sector_questions == [] or "price" not in sector_questions[0]["questions"]


def test_cli_success_publishes_the_sector_entry(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path)
    rows = [{"code": code, "name": name, "fullname": "", "listDate": "2010-01-01", "aliases": [name],
             "summary": None, "summarySource": "none"}
            for code, name in (("300953.SZ", "震裕科技"), ("300683.SZ", "海特生物"), ("300082.SZ", "奥克股份"))]
    with db.write(path) as conn:
        store_rows(conn, rows, now=datetime(2026, 10, 12, 4, tzinfo=timezone.utc), source="tushare")
    _with_device(path)

    class _Clock:
        current = datetime(2026, 10, 13, 0, 2, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

        fromisoformat = staticmethod(datetime.fromisoformat)

    monkeypatch.setattr(cli, "datetime", _Clock)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", "r" * 32)
    monkeypatch.setattr(cli, "run_collection", lambda *_a, **_k: {"status": "completed"})
    apns = FakeApns()
    monkeypatch.setattr("neckline.push.apns.send_push", apns)
    _install_clients(monkeypatch, _sector_handler(FakeModel()))
    assert cli.main(["--db", str(path), "morning", "--now", "2026-10-13T08:02:00+08:00"]) == 0
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=lambda: None))
    from neckline.morning.web import create_router as web_router
    app.include_router(web_router())
    client = TestClient(app)
    latest = client.get("/api/v4/reports/latest").json()
    assert mcp_report(None)["content"] == latest["content"]
    sectors = latest["content"]["sectors"]
    assert sectors["candidates"] >= 1 and sectors["entries"]
    entry = sectors["entries"][0]
    assert entry["sector"]["name"] == "人工智能" and entry["sector"]["code"] == "885001.TI"
    assert entry["leaders"] and entry["sources"]
    assert "price" in latest["content"]["entries"][0]["questions"]
    assert "price" not in entry["questions"]
    assert latest["content"]["stats"]["sectors"]["raw"] >= 1
    stocks = len(latest["content"]["entries"])
    expected = f"个股 {stocks} 只，板块 {len(sectors['entries'])} 个"
    if latest["gapSummary"]:
        expected += "（有缺口）"
    assert apns.sent == [("ab" * 32, "晨报已出", expected)]
    listed = client.get("/api/v4/reports", params={"limit": 5}).json()["reports"]
    assert listed[0]["counts"] == {"stocks": stocks, "sectors": len(sectors["entries"])}
    assert client.get("/api/v4/reports/2026-10-13").json()["content"]["sectors"] == sectors
    page = client.get("/read/" + "r" * 32)
    assert "<h2>板块热点</h2>" in page.text and entry["title"] in page.text
    assert f"创业板 {entry['sector']['memberCount']} 只" in page.text
    assert "① 是不是新消息" in page.text and "② 分量够不够" in page.text
    assert "③ 板块在什么位置" in page.text and "④ 谁来承接" in page.text
    assert "为什么" in page.text and "价格" in page.text
    assert client.get("/read/" + "r" * 32, params={"format": "json"}).json()["content"]["sectors"] == sectors
    blob = json.dumps(latest, ensure_ascii=False)
    assert "test-key" not in blob and "r" * 32 not in blob
    if os.environ.get("NK_WRITE_V6_FIXTURE") == "1" or not _V6_FIXTURE.is_file():
        _V6_FIXTURE.write_text(json.dumps(latest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    saved = json.loads(_V6_FIXTURE.read_text(encoding="utf-8"))
    assert _stable_report(saved) == _stable_report(latest)
    assert saved["content"]["entries"] and saved["content"]["sectors"]["entries"]


def test_replay_summary_lists_the_named_carriers(tmp_path, monkeypatch):
    snapshot = tmp_path / "snap.db"
    db.init_schema(snapshot)
    with db.write(snapshot) as conn:
        db.begin_report(conn, report_id="morning-2026-10-13", trade_date="2026-10-13",
                        window_start=datetime(2026, 10, 12, 7, tzinfo=timezone.utc),
                        window_end=datetime(2026, 10, 13, 0, tzinfo=timezone.utc),
                        config_version="screening-v6", config_sha256="0" * 64,
                        started_at=datetime(2026, 10, 13, 0, 2, tzinfo=timezone.utc))
    market = tmp_path / "market.json"
    market.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")

    def fake_run(dest, **kwargs):
        assert kwargs["sector_live"] is False
        with db.write(dest) as conn:
            db.insert_sector_event(
                conn, report_id="morning-2026-10-13", event_id="0123456789abcdef", title="金融座谈会",
                sector="人工智能", reason="直接", repeat=False, state="succeeded", verdict="推",
                package={"event": {"firstPublished": "2026-10-12T17:30+08:00"}},
                prepared_at=datetime(2026, 10, 13, 0, 2, tzinfo=timezone.utc),
                output={"title": "金融座谈会", "leaders": [
                    {"code": "300082", "why": "消息点到它", "price": "明显高开不追"},
                ]}, settled_at=datetime(2026, 10, 13, 0, 3, tzinfo=timezone.utc))
        return {"state": "available", "entries": 0, "stats": {}}

    monkeypatch.setattr("neckline.morning.replay.run_morning", fake_run)
    run_replay(snapshot=snapshot, trade_date="2026-10-13", work=tmp_path / "work", market_snapshot=market,
               receipts=tmp_path / "receipts", filings_cache=tmp_path / "filings", pool_seed_only=True)
    summary = json.loads((tmp_path / "work" / "2026-10-13" / "summary.json").read_text(encoding="utf-8"))
    assert summary["sectors"] == [{
        "title": "金融座谈会", "sector": "人工智能", "state": "succeeded", "verdict": "推",
        "leaders": [{"code": "300082", "why": "消息点到它", "price": "明显高开不追"}],
    }]
    assert _summary({"stats": {}}, None)["sectors"] == []


def test_missing_sector_settings_are_rejected(tmp_path):
    raw = json.loads((load_config().rules and _config_path()).read_text(encoding="utf-8"))
    for key in ("prefilter", "candidates", "mapping", "carriers", "evaluation", "delivery"):
        broken = json.loads(json.dumps(raw))
        broken["sectors"].pop(key)
        target = tmp_path / f"{key}.json"
        target.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ValueError, match=rf"sectors\.{key}"):
            load_config(path=target)
    loaded = load_config()
    assert loaded.rules["sectors"]["candidates"]["modelOptions"] == loaded.rules["classification"]["modelOptions"]
    assert loaded.rules["sectors"]["evaluation"]["modelOptions"] == loaded.rules["evaluation"]["modelOptions"]
    assert loaded.rules["sectors"]["delivery"]["maxEvents"] == 3
    assert "leaders" not in loaded.rules["sectors"]
    assert loaded.rules["sectors"]["carriers"]["max"] == 3
    assert loaded.rules["sectors"]["mapping"]["contract"] == MAPPING_CONTRACT
    stale = json.loads(json.dumps(raw))
    stale["sectors"]["mapping"]["contract"] = "sector-mapping-v2"
    target = tmp_path / "mapping-v2.json"
    target.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(ValueError, match=f"sectors.mapping.contract 须为 {MAPPING_CONTRACT}"):
        load_config(path=target)
    retired = json.loads(json.dumps(raw))
    retired["sectors"]["leaders"] = {"rankBy": "amount"}
    retired["sectors"]["market"]["leaderCount"] = 3
    retired["sectors"]["market"]["leaderWindow"] = 20
    target = tmp_path / "retired.json"
    target.write_text(json.dumps(retired), encoding="utf-8")
    with pytest.raises(ValueError, match="已停用"):
        load_config(path=target)


def _config_path():
    from importlib import resources
    return resources.files("neckline.config").joinpath("screening-v6.json")


def _frozen_package() -> dict:
    return {
        "report": {"tradeDate": "2026-10-13", "windowStart": "2026-10-12T15:00:00+08:00",
                   "windowEnd": "2026-10-13T08:00:00+08:00"},
        "event": {"title": "金融座谈会", "reason": "直接受益", "firstPublished": "2026-10-12T17:00+08:00"},
        "sector": {"name": "人工智能", "code": "885001.TI"},
        "original": {"documents": [{
            "id": "m1", "source": "金十快讯", "publishedAt": "2026-10-12T17:00:00+08:00",
            "title": "金融座谈会", "text": "将于明天召开。", "truncated": False, "error": None,
        }], "media": []},
        "board": {"asOf": "2026-10-12", "memberCount": 3, "windows": [], "limitWindow": 5,
                  "limitUps": 1, "limitMissing": 0, "baselineLimitUps": 2, "baselineLimitMissing": 0},
        "carriers": {"max": 3, "amountWindow": 20, "limitWindow": 20, "changeWindow": 5},
        "members": [{
            "code": "300001", "name": "特锐德", "summary": "做充电", "circMv": 1_234_000,
            "averageAmountYuan": 560_000_000, "limitUps20": 0, "change5Percent": -2.3, "named": False,
        }],
        "search": {"enabled": True, "error": None, "results": []},
        "gaps": [],
    }


def _frozen_user() -> str:
    return evaluation_user_prompt(_frozen_package())


def _frozen_body() -> dict:
    rules = load_config().rules
    client = DeepSeekClient(endpoint="https://example.invalid/chat/completions", model=rules["model"]["name"],
                            api_key="k", receipts=None, timeout_seconds=5, max_attempts=1)
    return client.request_body(
        [{"role": "system", "content": EVALUATION_SYSTEM}, {"role": "user", "content": _frozen_user()}],
        rules["sectors"]["evaluation"]["modelOptions"])


def _eval_package(members=("300001",)) -> dict:
    return {
        "carriers": {"max": 3, "amountWindow": 20, "limitWindow": 20, "changeWindow": 5},
        "members": [{"code": code, "name": code} for code in members],
        "original": {"documents": [{"id": "m1"}], "media": []},
        "search": {"results": []},
    }


def test_sector_evaluation_rejects_a_bad_carrier_list_and_vetoes():
    package = _eval_package(("300001", "300002"))
    missing = _answer(["300001"])
    del missing["questions"]["position"]
    assert parse_evaluation_output(json.dumps(missing), package) == "questions 缺少 position"
    bad_pass = _answer(["300001"])
    bad_pass["questions"]["fresh"]["pass"] = 1
    assert parse_evaluation_output(json.dumps(bad_pass), package) == "fresh.pass 不是布尔"
    assert parse_evaluation_output(json.dumps(_answer(["300099"])), package) == "leaders 不在成分股名单"
    assert parse_evaluation_output(json.dumps(_answer(["300001", "300001"])), package) == "leaders 重复"
    wide = _eval_package(("300001", "300002", "300003", "300004"))
    assert parse_evaluation_output(
        json.dumps(_answer(["300001", "300002", "300003", "300004"])), wide) == "leaders 数量不对"
    ignored = _answer(["300099"], carriers_pass=False)
    parsed = parse_evaluation_output(json.dumps(ignored), package)
    assert parsed["leaders"] == [] and parsed["verdict"] == "不推" and parsed["vetoed"] is True
    kept = parse_evaluation_output(json.dumps(_answer(["300002"])), package)
    assert kept["verdict"] == "推" and kept["leaders"] == [{"code": "300002", "why": "消息点到它", "price": "明显高开不追"}]


def test_a_light_event_is_stored_and_not_evaluated(tmp_path):
    path = _db(tmp_path)
    _concept(path)
    message = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))

    def light(user):
        return _ok({"events": [_event({
            "title": "分量不够的会", "ids": _ids(user), "sector": "人工智能", "reason": "对得上",
            "repeat": False, "weighty": False, "weight": "只是表态，带不动板块",
        })], "dropped": []})

    client = _script(mapping=light)
    outcome = _run(path, [message], client)
    assert len(client.calls) == 2
    row = _rows(path)[0]
    assert row["state"] == "skipped" and row["reason"] == "只是表态，带不动板块"
    assert outcome.content["entries"] == [] and outcome.stats["mapping"]["light"] == 1


def test_length_splits_once_and_a_failed_half_is_the_original_batch(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    first = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))
    second = _msg("b", "监管放开行业准入", "试点扩大到三个省。", _at(17))
    normal = _script()

    def both(messages, options):
        if "初筛员" in messages[0]["content"] and _ids(messages[1]["content"]) == ["n1", "n2"]:
            return ChatResult("", {"prompt_tokens": 4, "completion_tokens": 9}, "digest", False, 0.1, "length")
        return normal.handler(messages, options)

    client = Scripted(both)
    outcome = _run(path, [first, second], client, pool)
    assert outcome.gap is None and outcome.stats["candidates"]["splits"] == 1
    assert "failedBatches" not in outcome.stats["candidates"]
    assert len(outcome.content["entries"]) == 1
    candidate_calls = [call for call in client.calls if "初筛员" in call[0]["content"]]
    assert len(candidate_calls) == 3

    line = len(candidate_line("n1", first, 80)) + len(candidate_line("n2", second, 80))
    rules = _rules(pool, candidates=dict(_rules(pool).rules["sectors"]["candidates"], batchChars=line))
    failed = {"half": 0}

    def one_half(messages, options):
        system = messages[0]["content"]
        ids = _ids(messages[1]["content"]) if len(messages) > 1 else []
        if "初筛员" in system and ids == ["n1", "n2"]:
            return ChatResult("", {"prompt_tokens": 1, "completion_tokens": 1}, "digest", False, 0.1, "length")
        if "初筛员" in system:
            failed["half"] += 1
            if ids == ["n2"]:
                return ChatResult("", {"prompt_tokens": 1, "completion_tokens": 1}, "digest", False, 0.1, "length")
            return _ok({"events": [{"ids": ids, "event": "事"}]})
        return normal.handler(messages, options)

    third = _msg("c", "国家级项目开工", "第三个省也纳入。", _at(18))
    again = _db(tmp_path / "half")
    _concept(again)
    half = Scripted(one_half)
    missed = _run(again, [first, second, third], half, pool, config=rules)
    assert missed.stats["candidates"]["batches"] == 2
    assert missed.stats["candidates"]["splits"] == 1
    assert missed.stats["candidates"]["failedBatches"] == 1
    assert missed.gap == "板块线有 1 批消息没挑成（共 2 批）"
    candidate_calls = [call for call in half.calls if "初筛员" in call[0]["content"]]
    assert len(candidate_calls) == 4


def test_a_split_resume_does_not_bill_the_same_request_twice(tmp_path, monkeypatch):
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    path = _db(tmp_path)
    _concept(path)
    first = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))
    second = _msg("b", "监管放开行业准入", "试点扩大到三个省。", _at(17))
    hits = []

    def handler(request):
        hits.append(1)
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"] if len(body["messages"]) > 1 else ""
        if "初筛员" in system:
            ids = _ids(user)
            if ids == ["n1", "n2"]:
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 9},
                })
            return _http({"events": [{"ids": ids, "event": "事"}]})
        if "板块分析员" in system:
            return _http({"events": [_event({
                "title": "行业座谈会", "ids": _ids(user), "sector": "人工智能", "reason": "直接受益", "repeat": False,
            })], "dropped": []})
        return _http(_answer(_leader_codes(user)))

    def client():
        return DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
                              receipts=db.DbReceipts(path, "morning-2026-10-13"), timeout_seconds=5, max_attempts=1,
                              transport=httpx.MockTransport(handler))

    first_run = _run(path, [first, second], client())
    assert first_run.gap is None and first_run.stats["candidates"]["splits"] == 1
    sent = len(hits)
    second_run = _run(path, [first, second], client())
    assert len(hits) == sent and second_run.stats["candidates"]["calls"] == 0
    assert second_run.stats["candidates"]["fromReceipt"] >= 3


def _charged(payload, *, prompt, completion, reasoning, seconds, finish="stop"):
    content = "" if payload is None else json.dumps(payload, ensure_ascii=False)
    return ChatResult(content, {
        "prompt_tokens": prompt, "completion_tokens": completion,
        "prompt_cache_hit_tokens": 0, "reasoning_tokens": reasoning,
    }, "digest", False, seconds, finish)


def _mapping_users(client) -> list[str]:
    return [call[1]["content"] for call in client.calls if "板块分析员" in call[0]["content"]]


def _sector_messages() -> list[Message]:
    return [
        _msg("a", "部委印发行业补贴方案", "早上的方案。", _at(16)),
        _msg("b", "监管放开行业准入", "第二条。", _at(17)),
        _msg("c", "国家级项目开工", "第三条。", _at(18)),
        _msg("d", "行业龙头获得大额订单", "第四条。", _at(19)),
    ]


def _seed_previous(path, title="昨天的会", sector="人工智能") -> None:
    prev = previous_trading_day(DAY)
    start = datetime(prev.year, prev.month, prev.day, 15, tzinfo=CST)
    with db.write(path) as conn:
        db.begin_report(
            conn, report_id=f"morning-{prev.isoformat()}", trade_date=prev.isoformat(),
            window_start=start, window_end=_at(8, day=13), config_version="screening-v6",
            config_sha256="0" * 64, started_at=_at(8, day=13))
        db.complete_report(
            conn, report_id=f"morning-{prev.isoformat()}",
            content={"sectors": {"entries": [{"title": title, "sector": {"name": sector}}]}},
            stats={}, gap_summary=None, available_at=_at(8, day=13))


def _assert_mapping_added(stats, *, prompts, completions, reasonings, seconds, calls=3) -> None:
    usage = stats["mapping"]["usage"]
    assert usage["prompt_tokens"] == prompts
    assert usage["completion_tokens"] == completions
    assert usage["reasoning_tokens"] == reasonings
    assert stats["mapping"]["seconds"] == seconds
    assert stats["mapping"]["calls"] == calls
    assert stats["mapping"]["fromReceipt"] == 0


def test_mapping_length_split_keeps_light_and_repeat_events_apart(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    _seed_previous(path)
    normal = _script()

    def handler(messages, _options):
        if "板块分析员" not in messages[0]["content"]:
            return normal.handler(messages, _options)
        ids = _ids(messages[1]["content"])
        if ids == ["n1", "n2", "n3", "n4"]:
            return _charged(None, prompt=10, completion=20, reasoning=30, seconds=1, finish="length")
        if ids == ["n1", "n2"]:
            return _charged({"events": [
                _event({"title": "早间方案", "ids": ["n1"], "sector": "人工智能",
                        "reason": "早的理由", "repeat": False, "weighty": False, "weight": "早的分量"}),
                _event({"title": "说不清", "ids": ["n2"], "sector": None,
                        "reason": "分散", "repeat": False, "weighty": True, "weight": "够看"}),
            ], "dropped": [{"ids": ["n2"], "reason": "这条仍留在事件里"}]},
                prompt=1, completion=2, reasoning=3, seconds=2)
        if ids == ["n3", "n4"]:
            return _charged({"events": [
                _event({"title": "晚间方案", "ids": ["n3"], "sector": "人工智能",
                        "reason": "晚的理由", "repeat": True, "weighty": True, "weight": "晚的分量"}),
                _event({"title": "另一件", "ids": ["n4"], "sector": None,
                        "reason": "也对不上", "repeat": False, "weighty": False, "weight": "不够"}),
            ], "dropped": [{"ids": ["n9"], "reason": "汇总"}]},
                prompt=4, completion=5, reasoning=6, seconds=4)
        raise AssertionError(ids)

    client = Scripted(handler)
    outcome = _run(path, _sector_messages(), client, pool)
    assert outcome.gap is None and outcome.stats["mapping"]["splits"] == 1
    _assert_mapping_added(outcome.stats, prompts=15, completions=27, reasonings=39, seconds=7)
    users = _mapping_users(client)
    assert [_ids(user) for user in users] == [
        ["n1", "n2", "n3", "n4"], ["n1", "n2"], ["n3", "n4"],
    ]
    full_tail = users[0].split("板块清单：", 1)[1]
    for user in users[1:]:
        assert user.split("板块清单：", 1)[1] == full_tail
        assert "昨天的会｜人工智能" in user
    assert "国家级项目开工" not in users[1]
    assert "部委印发行业补贴方案" not in users[2]
    by_title = {row["title"]: row for row in _rows(path)}
    assert set(by_title) == {"早间方案", "说不清", "晚间方案", "另一件"}
    early = by_title["早间方案"]
    assert early["state"] == "skipped" and early["reason"] == "早的分量" and early["repeat"] is False
    assert early["package"]["ids"] == ["n1"]
    late = by_title["晚间方案"]
    assert late["state"] == "skipped" and late["reason"] == "晚的理由" and late["repeat"] is True
    assert late["package"]["ids"] == ["n3"]
    assert by_title["说不清"]["state"] == "skipped" and by_title["说不清"]["sector"] is None
    assert by_title["说不清"]["package"]["ids"] == ["n2"]
    assert by_title["另一件"]["state"] == "skipped" and by_title["另一件"]["sector"] is None
    assert outcome.content["entries"] == []


def test_mapping_length_keeps_the_other_half_and_records_the_gap(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    normal = _script()

    def handler(messages, _options):
        if "板块分析员" not in messages[0]["content"]:
            return normal.handler(messages, _options)
        ids = _ids(messages[1]["content"])
        if ids == ["n1", "n2", "n3", "n4"]:
            return _charged(None, prompt=10, completion=20, reasoning=30, seconds=1, finish="length")
        if ids == ["n1", "n2"]:
            return _charged(None, prompt=1, completion=2, reasoning=3, seconds=2, finish="length")
        if ids == ["n3", "n4"]:
            return _charged({"events": [_event({
                "title": "后一半", "ids": ["n3", "n4"], "sector": "人工智能",
                "reason": "后一半对得上", "repeat": False,
            })], "dropped": []}, prompt=4, completion=5, reasoning=6, seconds=4)
        raise AssertionError(ids)

    client = Scripted(handler)
    outcome = _run(path, _sector_messages(), client, pool)
    assert outcome.gap == "板块线有一半候选没归并成"
    assert outcome.stats["mapping"]["splits"] == 1
    _assert_mapping_added(outcome.stats, prompts=15, completions=27, reasonings=39, seconds=7)
    assert [_ids(user) for user in _mapping_users(client)] == [
        ["n1", "n2", "n3", "n4"], ["n1", "n2"], ["n3", "n4"],
    ]
    rows = _rows(path)
    assert [row["title"] for row in rows] == ["后一半"]
    assert [item["title"] for item in rows[0]["package"]["messages"]] == ["国家级项目开工", "行业龙头获得大额订单"]


def test_mapping_length_both_halves_fail_uses_the_original_gap(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    normal = _script()

    def handler(messages, _options):
        if "板块分析员" not in messages[0]["content"]:
            return normal.handler(messages, _options)
        ids = _ids(messages[1]["content"])
        if ids == ["n1", "n2", "n3", "n4"]:
            return _charged(None, prompt=10, completion=20, reasoning=30, seconds=1, finish="length")
        if ids == ["n1", "n2"]:
            return _charged(None, prompt=1, completion=2, reasoning=3, seconds=2, finish="length")
        if ids == ["n3", "n4"]:
            return _charged(None, prompt=4, completion=5, reasoning=6, seconds=4, finish="length")
        raise AssertionError(ids)

    client = Scripted(handler)
    outcome = _run(path, _sector_messages(), client, pool)
    assert outcome.gap == "板块线没有完成：归并没有完成"
    assert "一半" not in outcome.gap
    assert outcome.content["entries"] == [] and _rows(path) == []
    assert outcome.stats["mapping"]["splits"] == 1
    _assert_mapping_added(outcome.stats, prompts=15, completions=27, reasonings=39, seconds=7)
    assert not any("板块消息评估员" in call[0]["content"] for call in client.calls)


def test_mapping_does_not_split_on_failure_or_bad_output(tmp_path):
    pool = _pool()
    pair = [
        _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16)),
        _msg("b", "监管放开行业准入", "试点扩大到三个省。", _at(17)),
    ]
    normal = _script()

    def run(name, respond, messages=None):
        path = tmp_path / f"{name}.db"
        db.init_schema(path)
        _concept(path)

        def handler(sent, _options):
            if "板块分析员" in sent[0]["content"]:
                outcome = respond(sent)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
            return normal.handler(sent, _options)

        client = Scripted(handler)
        outcome = _run(path, messages or pair, client, pool)
        assert outcome.gap == "板块线没有完成：归并没有完成", name
        assert "一半" not in (outcome.gap or "")
        assert outcome.content["entries"] == []
        assert outcome.stats["mapping"]["splits"] == 0, name
        assert outcome.stats["mapping"]["calls"] == 1, name
        assert len(_mapping_users(client)) == 1, name

    run("json", lambda _sent: ChatResult(
        "not-json", {"prompt_tokens": 1, "completion_tokens": 1}, "d", False, 0.1, "stop"))

    def bad_weight(sent):
        return _ok({"events": [_event({
            "title": "补贴", "ids": _ids(sent[1]["content"]), "sector": "人工智能",
            "reason": "直接", "repeat": False, "weighty": "yes", "weight": "够",
        })], "dropped": []})

    run("weighty", bad_weight)
    run("http", lambda _sent: ChatError("http:500"))
    run("unknown", lambda _sent: ChatUnknown("响应不是 JSON"))
    run("single", lambda _sent: ChatResult(
        "", {"prompt_tokens": 2, "completion_tokens": 3}, "d", False, 0.1, "length"), messages=[pair[0]])


def test_mapping_split_resume_does_not_bill_the_same_request_twice(tmp_path, monkeypatch):
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    path = _db(tmp_path)
    _concept(path)
    messages = _sector_messages()
    sent = []

    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"] if len(body["messages"]) > 1 else ""
        if "初筛员" in system:
            sent.append(("candidate", tuple(_ids(user))))
            return _http({"events": [{"ids": _ids(user), "event": "事"}]})
        if "板块分析员" in system:
            ids = _ids(user)
            sent.append(("mapping", tuple(ids)))
            if ids == ["n1", "n2", "n3", "n4"]:
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 9},
                })
            title = "前一半" if ids == ["n1", "n2"] else "后一半"
            return _http({"events": [_event({
                "title": title, "ids": [ids[0]], "sector": "人工智能", "reason": "直接受益", "repeat": False,
            })], "dropped": [{"ids": [ids[1]], "reason": "并进同一件事"}]})
        sent.append(("eval", tuple(_leader_codes(user))))
        return _http(_answer(_leader_codes(user)))

    class _Halt(BaseException):
        pass

    def client(limit):
        inner = DeepSeekClient(
            endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
            receipts=db.DbReceipts(path, "morning-2026-10-13"), timeout_seconds=5, max_attempts=1,
            transport=httpx.MockTransport(handler))
        count = {"n": 0}

        class Gate:
            def complete(self, sent, options):
                if limit is not None and count["n"] >= limit:
                    raise _Halt()
                count["n"] += 1
                return inner.complete(sent, options)

        return Gate()

    with pytest.raises(_Halt):
        _run(path, messages, client(3))
    assert [item for item in sent if item[0] == "mapping"] == [
        ("mapping", ("n1", "n2", "n3", "n4")),
        ("mapping", ("n1", "n2")),
    ]
    assert _rows(path) == []
    outcome = _run(path, messages, client(None))
    assert outcome.gap is None and outcome.stats["mapping"]["splits"] == 1
    assert outcome.stats["mapping"]["calls"] == 1
    assert outcome.stats["mapping"]["fromReceipt"] == 2
    usage = outcome.stats["mapping"]["usage"]
    assert usage["prompt_tokens"] == 12 and usage["completion_tokens"] == 15
    assert usage["prompt_cache_hit_tokens"] == 2 and usage["reasoning_tokens"] == 4
    assert [item for item in sent if item[0] == "mapping"] == [
        ("mapping", ("n1", "n2", "n3", "n4")),
        ("mapping", ("n1", "n2")),
        ("mapping", ("n3", "n4")),
    ]
    done = [row for row in _rows(path) if row["state"] == "succeeded"]
    assert len(done) == 1 and done[0]["title"] == "前一半"
    assert [item["title"] for item in done[0]["package"]["messages"]] == [
        "部委印发行业补贴方案", "国家级项目开工",
    ]


def test_sector_prompts_and_one_request_body_stay_frozen():
    assert hashlib.sha256(CANDIDATE_SYSTEM.encode()).hexdigest() == _FROZEN_CANDIDATES
    assert MAPPING_CONTRACT == "sector-mapping-v3"
    assert (
        "金融监管部门针对资本市场、券商、银行、保险等金融行业的会议和工具，"
        "不算全市场的消息，要保留，对到金融行业的板块。"
    ) in MAPPING_SYSTEM
    assert hashlib.sha256(MAPPING_SYSTEM.encode()).hexdigest() == _FROZEN_MAPPING
    # 11/07 用户：第四问放宽，按身份划分的板块（参股、持股、资格）不要求主营相关
    assert "不要求主营相关" in EVALUATION_SYSTEM and "按身份划分的板块" in EVALUATION_SYSTEM
    assert hashlib.sha256(EVALUATION_SYSTEM.encode()).hexdigest() == _FROZEN_EVALUATION
    assert hashlib.sha256(_frozen_user().encode()).hexdigest() == _FROZEN_USER
    assert hashlib.sha256(canonical(_frozen_body()).encode()).hexdigest() == _FROZEN_BODY


def _mapped(title, ids, *, repeat=False, weighty=True, reason="理由", weight="分量"):
    return {"title": title, "ids": list(ids), "sector": "人工智能", "reason": reason,
            "repeat": repeat, "weighty": weighty, "weight": weight}


def test_mapping_merge_does_not_fold_repeat_or_light_into_a_valid_event(tmp_path):
    """重复、分量不够的不进合并；有效事件才合并，代表取首条消息最早的那件。"""
    ordered = ["n1", "n2", "n3", "n4"]

    def merged(left, right):
        return _merge_mapping_halves([left], [right], ordered, [], [], {"人工智能"})

    repeat_and_valid = merged(
        _mapped("旧重复", ["n1"], repeat=True, reason="旧理由", weight="旧分量"),
        _mapped("新事件", ["n3"], reason="新理由", weight="新分量"))
    by_title = {item["title"]: item for item in repeat_and_valid}
    assert set(by_title) == {"旧重复", "新事件"}
    assert by_title["旧重复"]["repeat"] is True and by_title["旧重复"]["ids"] == ["n1"]
    assert by_title["旧重复"]["weight"] == "旧分量" and by_title["旧重复"]["reason"] == "旧理由"
    assert by_title["新事件"]["ids"] == ["n3"] and by_title["新事件"]["reason"] == "新理由"
    assert by_title["新事件"]["weight"] == "新分量" and by_title["新事件"]["weighty"] is True
    assert by_title["新事件"]["repeat"] is False

    light_and_valid = merged(
        _mapped("分量不够", ["n1"], weighty=False, reason="旧理由", weight="不够的分量"),
        _mapped("有效事件", ["n3"], reason="有效理由", weight="有效分量"))
    by_title = {item["title"]: item for item in light_and_valid}
    assert by_title["分量不够"]["weighty"] is False and by_title["分量不够"]["ids"] == ["n1"]
    assert by_title["分量不够"]["weight"] == "不够的分量"
    assert by_title["有效事件"]["ids"] == ["n3"] and by_title["有效事件"]["title"] == "有效事件"
    assert by_title["有效事件"]["weight"] == "有效分量" and by_title["有效事件"]["reason"] == "有效理由"

    both_valid = merged(
        _mapped("最早", ["n1"], reason="最早的理由", weight="最早的分量"),
        _mapped("较晚", ["n3"], reason="较晚的理由", weight="较晚的分量"))
    assert len(both_valid) == 1
    assert both_valid[0]["title"] == "最早" and both_valid[0]["reason"] == "最早的理由"
    assert both_valid[0]["weight"] == "最早的分量" and both_valid[0]["ids"] == ["n1", "n3"]
    assert both_valid[0]["repeat"] is False and both_valid[0]["weighty"] is True

    def archive(root, left, right):
        path = _db(root)
        _concept(path)
        normal = _script()

        def handler(messages, _options):
            if "板块分析员" not in messages[0]["content"]:
                return normal.handler(messages, _options)
            ids = _ids(messages[1]["content"])
            if ids == ["n1", "n2", "n3", "n4"]:
                return _charged(None, prompt=1, completion=1, reasoning=1, seconds=1, finish="length")
            if ids == ["n1", "n2"]:
                return _charged({"events": [left], "dropped": []}, prompt=1, completion=1, reasoning=1, seconds=1)
            if ids == ["n3", "n4"]:
                return _charged({"events": [right], "dropped": []}, prompt=1, completion=1, reasoning=1, seconds=1)
            raise AssertionError(ids)

        outcome = _run(path, _sector_messages(), Scripted(handler), _pool())
        return outcome, {row["title"]: row for row in _rows(path)}

    _outcome, stored = archive(
        tmp_path / "repeat",
        _event(_mapped("旧重复", ["n1"], repeat=True, reason="旧理由", weight="旧分量")),
        _event(_mapped("新事件", ["n3"], reason="新理由", weight="新分量")))
    assert stored["旧重复"]["state"] == "skipped" and stored["旧重复"]["reason"] == "旧理由"
    assert stored["新事件"]["state"] == "succeeded" and stored["新事件"]["title"] == "新事件"
    assert [item["title"] for item in stored["新事件"]["package"]["messages"]] == ["国家级项目开工"]

    _outcome, stored = archive(
        tmp_path / "light",
        _event(_mapped("分量不够", ["n1"], weighty=False, reason="旧理由", weight="不够的分量")),
        _event(_mapped("有效事件", ["n3"], reason="有效理由", weight="有效分量")))
    assert stored["分量不够"]["state"] == "skipped" and stored["分量不够"]["reason"] == "不够的分量"
    assert stored["有效事件"]["state"] == "succeeded" and stored["有效事件"]["title"] == "有效事件"
    assert [item["title"] for item in stored["有效事件"]["package"]["messages"]] == ["国家级项目开工"]


class _MorningClock:
    def __init__(self, start):
        self.current = start

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current = self.current + timedelta(seconds=seconds)


def test_sector_daily_fill_stops_at_cutoff_and_the_main_list_still_publishes(tmp_path, monkeypatch):
    """09:04 开始补 40 日日线，每次外呼 9 秒：到 09:05 停板块线，主线照常交付。"""
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path, with_daily=False)
    _with_device(path)
    start = at("2026-10-13", "09:04")
    clock = _MorningClock(start)
    cutoff = at("2026-10-13", "09:05")
    deadline = at("2026-10-13", "09:15")
    started = []
    codes = ["300953.SZ", "300683.SZ", "300082.SZ"]

    def slow_post(_url, payload, _timeout):
        started.append(clock())
        clock.advance(9)
        stamp = payload["params"]["trade_date"]
        api = payload["api_name"]
        if api == "daily":
            rows = [[code, stamp, 10, 11, 9, 11, 10, 1, 1.5] for code in codes]
            return {"code": 0, "data": {
                "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "amount"],
                "items": rows, "has_more": False}}
        if api == "limit_list_d":
            return {"code": 0, "data": {"fields": ["ts_code", "trade_date"], "items": [], "has_more": False}}
        raise AssertionError(api)

    monkeypatch.setattr("neckline.morning.sectors._sender", lambda _post, _environ: ("slow-token", slow_post))
    monkeypatch.setattr("neckline.morning.sectors._pause", lambda pause, _sleep, _interval: pause.__setitem__("started", True))
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    apns = FakeApns()
    result = run_morning(
        path, now=start, config=_config(), clock=clock, collector=lambda _at: {"status": "completed"},
        client_factory=lambda rid: _client(path, _sector_handler(FakeModel()), rid),
        pusher=Pusher(path, apns, clock))
    assert result["state"] == "available" and result["entries"] == 2
    assert "板块线没有完成：收口时刻前没备齐行情" in result["gapSummary"]
    assert "前没能出报" not in (result.get("reason") or "")
    assert started and len(started) < 80 and len(started) == 7
    assert all(moment < cutoff for moment in started)
    assert cutoff <= clock() < deadline
    assert apns.sent and apns.sent[-1][1] == "晨报已出"
    with db.read(path) as conn:
        stored = db.get_report(conn, "2026-10-13")
    assert stored["status"] == "available"
    assert [entry["companyName"] for entry in stored["content"]["entries"]] == ["海特生物", "震裕科技"]


def test_chunked_keepalive_stops_at_cutoff_without_holding_the_main_list(tmp_path, monkeypatch):
    """08:55 发起板块挑候选。传输每块把业务时钟拨快 500 秒，600 秒读超时不会响。

    09:05 后不再等。主线照常交付。迟到的正文不改名单，回执停在 started 再改成 unknown，续跑不重发。
    """
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path)
    _with_device(path)
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    start = at("2026-10-13", "08:55")
    clock = _MorningClock(start)
    cutoff = at("2026-10-13", "09:05")
    deadline = at("2026-10-13", "09:15")
    release = threading.Event()
    sector_calls: list[int] = []
    model = FakeModel()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        if "初筛员" in system:
            sector_calls.append(1)
            if len(sector_calls) == 1:
                # 两块各 500 秒：08:55 → 09:03:20 → 09:11:40。读超时按块重置，这里壁钟不睡。
                clock.advance(500)
                clock.advance(500)
                if not release.wait(timeout=30):
                    raise AssertionError("板块调用没有在时限内被放行")
                clock.advance(500)
            user = body["messages"][1]["content"]
            return _http({"events": [{"ids": _ids(user), "event": "迟到的事"}]})
        return _sector_handler(model)(request)

    def factory(rid):
        return DeepSeekClient(
            endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
            receipts=db.DbReceipts(path, rid), timeout_seconds=600, max_attempts=2,
            transport=httpx.MockTransport(handler))

    def run():
        return run_morning(
            path, now=start, config=_config(), clock=clock, collector=lambda _at: {"status": "completed"},
            client_factory=factory, pusher=Pusher(path, apns, clock))

    def receipt_states() -> list[str]:
        with db.read(path) as conn:
            return [row["state"] for row in conn.execute("SELECT state FROM model_receipts ORDER BY rowid")]

    def stored_report() -> dict:
        with db.read(path) as conn:
            return db.get_report(conn, "2026-10-13")

    apns = FakeApns()
    try:
        started = time.monotonic()
        result = run()
        elapsed = time.monotonic() - started
        assert elapsed < 15
        assert cutoff <= clock() < deadline
        assert result["state"] == "available" and result["entries"] == 2
        assert "板块线没有完成：收口时刻前没评完" in result["gapSummary"]
        assert "前没能出报" not in (result.get("reason") or "")
        assert apns.sent and apns.sent[-1][1] == "晨报已出"
        first = stored_report()
        assert first["status"] == "available"
        names = [entry["companyName"] for entry in first["content"]["entries"]]
        assert names == ["海特生物", "震裕科技"]
        assert first["content"]["sectors"]["entries"] == []
        assert "started" in receipt_states()
        sent = len(sector_calls)
        with db.write(path) as conn:
            conn.execute("UPDATE reports SET status='running' WHERE trade_date='2026-10-13'")
        resumed = run()
        assert len(sector_calls) == sent
        assert resumed["state"] == "available" and resumed["entries"] == 2
        after_resume = stored_report()
        assert [entry["companyName"] for entry in after_resume["content"]["entries"]] == names
        assert after_resume["content"]["sectors"]["entries"] == []
        snapshot = json.dumps(after_resume["content"], ensure_ascii=False, sort_keys=True)
        release.set()
        limit = time.monotonic() + 5
        while time.monotonic() < limit:
            states = receipt_states()
            if "unknown" in states and "started" not in states:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"迟到回执没有收成 unknown：{receipt_states()}")
        late = stored_report()
        assert json.dumps(late["content"], ensure_ascii=False, sort_keys=True) == snapshot
        assert "迟到的事" not in snapshot
        # 回执已经是 unknown。把时钟拨回收口之后、截止之前，再续跑一次，确认不会重发。
        clock.current = at("2026-10-13", "09:11")
        with db.write(path) as conn:
            conn.execute("UPDATE reports SET status='running' WHERE trade_date='2026-10-13'")
        again = run()
        assert len(sector_calls) == sent
        assert again["state"] == "available" and again["entries"] == 2
        final = stored_report()
        assert [entry["companyName"] for entry in final["content"]["entries"]] == names
        assert final["content"]["sectors"]["entries"] == []
        assert "迟到的事" not in json.dumps(final["content"], ensure_ascii=False)
    finally:
        release.set()


def test_concept_refresh_failure_is_a_sector_gap_and_the_line_still_runs(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path)
    with db.write(path) as conn:
        db.set_sector_refresh_error(conn, "ths_index 返回异常")
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    result = run_morning(
        path, now=at("2026-10-13", "08:02"), config=_config(), clock=lambda: at("2026-10-13", "08:02"),
        collector=lambda _at: {"status": "completed"},
        client_factory=lambda rid: _client(path, _sector_handler(FakeModel()), rid),
        pusher=Pusher(path, FakeApns(), lambda: at("2026-10-13", "08:02")))
    assert result["state"] == "available" and result["entries"] == 2
    assert "板块清单没刷新成功，沿用 2026-10-12 的" in result["gapSummary"]
    assert "联网搜索没成" not in result["gapSummary"]
    with db.read(path) as conn:
        stored = db.get_report(conn, "2026-10-13")
    assert stored["content"]["sectors"]["entries"]
    assert stored["content"]["sectors"]["entries"][0]["sector"]["name"] == "人工智能"


def _stale_concepts(path) -> None:
    _concept(path)
    with db.write(path) as conn:
        db.set_sector_refresh_error(conn, "ths_index 返回异常")


def test_concept_refresh_failure_is_reported_when_no_messages_remain(tmp_path):
    path = _db(tmp_path)
    _stale_concepts(path)
    client = _script()
    outcome = _run(path, [], client)
    assert outcome.gap == "板块清单没刷新成功，沿用 2026-10-12 的"
    assert outcome.content["entries"] == []
    assert client.calls == []


def test_concept_refresh_failure_is_reported_when_prefilter_drops_every_message(tmp_path):
    path = _db(tmp_path)
    _stale_concepts(path)
    message = _msg("a", "关于召开行业座谈会", "邀请机构参加。", _at(16), "cninfo-announcement")
    client = _script()
    outcome = _run(path, [message], client)
    assert outcome.gap == "板块清单没刷新成功，沿用 2026-10-12 的"
    assert outcome.content["entries"] == []
    assert client.calls == []
    assert outcome.stats["kept"] == 0


def test_search_unknown_is_a_sector_gap_and_the_same_report_is_published(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path)
    _with_device(path)
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    hits = []

    def search_handler(_request):
        hits.append(1)
        raise httpx.RemoteProtocolError("closed")

    now = at("2026-10-13", "08:02")
    apns = FakeApns()
    model = FakeModel()

    def search_factory(rid):
        return SearchClient(
            endpoint="https://example.invalid/tools/search", api_key="k",
            receipts=db.DbReceipts(path, rid), timeout_seconds=30, max_attempts=2,
            transport=httpx.MockTransport(search_handler))

    from neckline.morning import report as morning_report
    real = morning_report.run_sector_line
    gate = {"halt": True}

    class _Halt(BaseException):
        pass

    def wrapped(*args, **kwargs):
        outcome = real(*args, **kwargs)
        if gate["halt"]:
            gate["halt"] = False
            raise _Halt()
        return outcome

    monkeypatch.setattr(morning_report, "run_sector_line", wrapped)

    def once():
        return run_morning(
            path, now=now, config=_config(), clock=lambda: now, collector=lambda _at: {"status": "completed"},
            client_factory=lambda rid: _client(path, _sector_handler(model), rid),
            pusher=Pusher(path, apns, lambda: now), search_factory=search_factory)

    with pytest.raises(_Halt):
        once()
    with db.read(path) as conn:
        assert db.get_report(conn, "2026-10-13")["status"] == "running"
    sent = len(hits)
    assert sent >= 1
    result = once()
    assert len(hits) == sent
    assert result["state"] == "available"
    assert "板块线有 1 个事件联网搜索没成" in result["gapSummary"]
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=lambda: None))
    latest = TestClient(app).get("/api/v4/reports/latest").json()
    via_mcp = mcp_report("2026-10-13")
    assert latest["gapSummary"] == via_mcp["gapSummary"] == result["gapSummary"]
    assert apns.sent[-1][1] == "晨报已出" and "有缺口" in apns.sent[-1][2]


def _never_refreshed(path) -> None:
    with db.write(path) as conn:
        conn.execute("DELETE FROM sector_members")
        conn.execute("DELETE FROM sector_concepts")
        conn.execute("DELETE FROM sector_concepts_meta")
        db.set_sector_refresh_error(conn, "ths_index 返回异常")


def test_first_refresh_failure_without_a_previous_list_when_there_are_no_messages(tmp_path):
    path = _db(tmp_path)
    _never_refreshed(path)
    client = _script()
    outcome = _run(path, [], client)
    assert outcome.gap == "板块清单还没有成功刷新过"
    assert outcome.content["entries"] == []
    assert client.calls == []


def test_first_refresh_failure_without_a_previous_list_when_prefilter_drops_everything(tmp_path):
    path = _db(tmp_path)
    _never_refreshed(path)
    message = _msg("a", "关于召开行业座谈会", "邀请机构参加。", _at(16), "cninfo-announcement")
    client = _script()
    outcome = _run(path, [message], client)
    assert outcome.gap == "板块清单还没有成功刷新过"
    assert outcome.content["entries"] == []
    assert outcome.stats["kept"] == 0
    assert client.calls == []


def test_first_refresh_failure_without_a_previous_list_still_publishes_the_main_line(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path)
    _never_refreshed(path)
    _with_device(path)
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    now = at("2026-10-13", "08:02")
    apns = FakeApns()
    result = run_morning(
        path, now=now, config=_config(), clock=lambda: now, collector=lambda _at: {"status": "completed"},
        client_factory=lambda rid: _client(path, _sector_handler(FakeModel()), rid),
        pusher=Pusher(path, apns, lambda: now))
    assert result["state"] == "available" and result["entries"] == 2
    assert "板块清单还没有成功刷新过" in result["gapSummary"]
    assert "概念清单是空的" not in result["gapSummary"]
    assert "前没能出报" not in (result.get("reason") or "")
    assert apns.sent and apns.sent[-1][1] == "晨报已出"
    with db.read(path) as conn:
        stored = db.get_report(conn, "2026-10-13")
    assert [entry["companyName"] for entry in stored["content"]["entries"]] == ["海特生物", "震裕科技"]
    assert stored["content"]["sectors"]["entries"] == []


def test_chunked_daily_request_stops_at_cutoff_without_holding_a_write(tmp_path, monkeypatch):
    """09:04 发起日线，分块把业务时钟拖到 09:20。主线在收口时交付，写事务不罩着这次外呼。

    晚到的行不写入 market_daily，已经交付的名单保持不动。
    """
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path, with_daily=False)
    _with_device(path)
    monkeypatch.setattr("neckline.morning.sector_line._WAIT_SLICE", 0.05)
    monkeypatch.setattr("neckline.morning.sectors._pause", lambda pause, _sleep, _interval: pause.__setitem__("started", True))
    start = at("2026-10-13", "09:04")
    clock = _MorningClock(start)
    cutoff = at("2026-10-13", "09:05")
    deadline = at("2026-10-13", "09:15")
    proceed = threading.Event()
    inflight = threading.Event()
    finished = threading.Event()
    started: list[str] = []
    depth = {"n": 0, "max": 0}
    real_write = db.write

    @contextmanager
    def watching(target):
        depth["n"] += 1
        try:
            with real_write(target) as conn:
                yield conn
        finally:
            depth["n"] -= 1

    monkeypatch.setattr(db, "write", watching)
    codes = ["300953.SZ", "300683.SZ", "300082.SZ"]

    def slow_post(_url, payload, _timeout):
        api = payload["api_name"]
        started.append(api)
        passed = at("2026-10-13", "09:05:01")
        while clock() < passed:
            depth["max"] = max(depth["max"], depth["n"])
            if depth["n"] != 0:
                raise AssertionError("日线请求还在飞，写事务不该开着")
            clock.advance(1)
            time.sleep(0.001)
        inflight.set()
        if not proceed.wait(timeout=15):
            raise AssertionError("主流程没有在时限内离开在途日线")
        while clock() < at("2026-10-13", "09:20"):
            clock.advance(30)
            time.sleep(0.001)
        stamp = payload["params"]["trade_date"]
        finished.set()
        if api == "daily":
            rows = [[code, stamp, 10, 11, 9, 11, 10, 1, 1.5] for code in codes]
            return {"code": 0, "data": {
                "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "amount"],
                "items": rows, "has_more": False}}
        if api == "limit_list_d":
            return {"code": 0, "data": {"fields": ["ts_code", "trade_date"], "items": [], "has_more": False}}
        raise AssertionError(api)

    monkeypatch.setattr("neckline.morning.sectors._sender", lambda _post, _environ: ("slow-token", slow_post))
    box: dict = {}

    def run():
        try:
            box["result"] = run_morning(
                path, now=start, config=_config(), clock=clock, collector=lambda _at: {"status": "completed"},
                client_factory=lambda rid: _client(path, _sector_handler(FakeModel()), rid),
                pusher=Pusher(path, apns, clock))
        except Exception as exc:
            box["error"] = exc

    apns = FakeApns()
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert inflight.wait(timeout=10)
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert "error" not in box
        when = clock()
        result = box["result"]
        assert cutoff <= when < deadline
        assert result["state"] == "available" and result["entries"] == 2
        assert "板块线没有完成：收口时刻前没备齐行情" in result["gapSummary"]
        assert "前没能出报" not in (result.get("reason") or "")
        assert apns.sent and apns.sent[-1][1] == "晨报已出"
        assert depth["max"] == 0
        with db.read(path) as conn:
            stored = db.get_report(conn, "2026-10-13")
            assert db.market_dates(conn) == []
        snapshot = json.dumps(stored["content"], ensure_ascii=False, sort_keys=True)
        assert [entry["companyName"] for entry in stored["content"]["entries"]] == ["海特生物", "震裕科技"]
        assert stored["content"]["sectors"]["entries"] == []
    finally:
        proceed.set()
    thread.join(timeout=5)
    assert finished.wait(timeout=5)
    time.sleep(0.3)
    assert started == ["daily"]
    with db.read(path) as conn:
        assert db.market_dates(conn) == []
        late = db.get_report(conn, "2026-10-13")
    assert json.dumps(late["content"], ensure_ascii=False, sort_keys=True) == snapshot


def _wait_until_unknown(path) -> list[str]:
    limit = time.monotonic() + 5

    def states() -> list[str]:
        with db.read(path) as conn:
            return [row["state"] for row in conn.execute("SELECT state FROM model_receipts ORDER BY rowid")]

    while time.monotonic() < limit:
        found = states()
        if found and "unknown" in found and "started" not in found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"回执没有停在 unknown：{states()}")


def test_model_does_not_retry_after_the_cutoff(tmp_path, monkeypatch):
    """09:04:59 发起模型调用，09:06 才返回 500。调用方已经不再等，后台不再发第二次。"""
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("neckline.morning.sector_line._WAIT_SLICE", 0.05)
    monkeypatch.setattr("neckline.morning.sector_line._tighten", lambda client, _cutoff, _clock: client)
    path = _db(tmp_path)
    _concept(path)
    clock = _MorningClock(datetime(2026, 10, 13, 9, 4, 59, tzinfo=CST))
    release = threading.Event()
    calls: list[str] = []

    def handler(_request):
        calls.append(clock().isoformat())
        clock.current = at("2026-10-13", "09:06")
        if not release.wait(timeout=10):
            raise AssertionError("模型调用没有在时限内被放行")
        return httpx.Response(500, json={"error": "down"})

    client = DeepSeekClient(
        endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
        receipts=db.DbReceipts(path, "morning-2026-10-13"), timeout_seconds=30, max_attempts=2,
        transport=httpx.MockTransport(handler))
    try:
        outcome = _run(path, [_msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))], client, clock=clock)
        assert calls == ["2026-10-13T09:04:59+08:00"]
        assert "收口时刻前没评完" in (outcome.gap or "")
    finally:
        release.set()
    assert _wait_until_unknown(path) == ["unknown"]
    time.sleep(0.2)
    assert calls == ["2026-10-13T09:04:59+08:00"]


def test_search_does_not_retry_after_the_cutoff(tmp_path, monkeypatch):
    """09:04:59 发起搜索，09:06 返回 500。调用方已经不再等，回执停在 unknown，没有第二次请求。"""
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("neckline.morning.sector_line._WAIT_SLICE", 0.05)
    monkeypatch.setattr("neckline.morning.sector_line._tighten", lambda client, _cutoff, _clock: client)
    path = _db(tmp_path)
    _concept(path)
    clock = _MorningClock(datetime(2026, 10, 13, 9, 4, 59, tzinfo=CST))
    release = threading.Event()
    calls: list[str] = []

    def handler(_request):
        calls.append(clock().isoformat())
        clock.current = at("2026-10-13", "09:06")
        if not release.wait(timeout=10):
            raise AssertionError("搜索没有在时限内被放行")
        return httpx.Response(500, json={"error": "down"})

    search = SearchClient(
        endpoint="https://example.invalid/tools/search", api_key="k",
        receipts=db.DbReceipts(path, "morning-2026-10-13"), timeout_seconds=30, max_attempts=2,
        transport=httpx.MockTransport(handler))
    try:
        outcome = _run(
            path, [_msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))], _script(),
            clock=clock, search=search)
        assert calls == ["2026-10-13T09:04:59+08:00"]
        assert "收口时刻前没评完" in (outcome.gap or "")
    finally:
        release.set()
    assert _wait_until_unknown(path) == ["unknown"]
    time.sleep(0.2)
    assert calls == ["2026-10-13T09:04:59+08:00"]
