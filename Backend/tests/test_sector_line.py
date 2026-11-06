"""板块线：粗筛、挑候选、归并、龙头、评估、收口，以及接到晨报后的真实入口。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.evaluation.search import SearchOutcome
from neckline.mcp.server import _report as mcp_report
from neckline.morning import cli, db
from neckline.morning.api import create_router
from neckline.morning.replay import _summary, run_replay
from neckline.morning.report import LoadedConfig, Pusher, load_config, run_morning
from neckline.morning.schedule import CST, previous_trading_day
from neckline.morning.sector_line import (
    apply_prefilter, assign_numbers, event_id, opening_text, parse_candidate_output, parse_mapping_output,
    run_sector_line, split_batches,
)
from neckline.morning.sector_prompt import (
    CANDIDATE_SYSTEM, EVALUATION_SYSTEM, MAPPING_SYSTEM, candidate_line, evaluation_user_prompt,
)
from neckline.morning.sectors import DailyFill, trading_days_ending
from neckline.morning.stock_pool import store_rows
from neckline.screening.deepseek import ChatResult, DeepSeekClient, canonical
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window, to_message

from tests.test_morning import FakeApns, FakeModel, _client, _config, _seed_window, _with_device, at, doc

DAY = date(2026, 10, 13)
_FROZEN_CANDIDATES = "0d9e4d935685f47bcb3d877e1efef1b8230c755d6dabbb6678bb61748c15985a"
_FROZEN_MAPPING = "69444b1f553b23434ad4b99df82df354f978c58a57a2f927342f0222388179ef"
_FROZEN_EVALUATION = "cb5a4e4cc300b3bda2ff49aedc92249c69a3943ff3655e24ca8e8a79fc9b86eb"
_FROZEN_USER = "288abcf9436803775f6f38c317265487aeff08fff31633a0d108a4c443db7d32"
_FROZEN_BODY = "41d71b51510ca1c5a0260b417358d84575eb37bff34627f4e1d493c9da1337ce"
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


def _answer(codes, *, verdict="推", value_pass=True, priced_pass=True, views=None) -> dict:
    return {
        "title": "值得看的板块", "verdict": verdict, "conclusion": "资金会看一眼",
        "questions": {
            "value": {"pass": value_pass, "text": "有政策"},
            "priced": {"pass": priced_pass, "text": "还没涨透"},
            "money": {"text": "可能接力"},
        },
        "leaders": [{"code": code, "text": "明显高开不追"} for code in codes],
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
    chunk = prompt.split("龙头：", 1)[-1]
    return re.findall(r"(?m)^(\d{6}) ", chunk)


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
            return _ok({"events": [{
                "title": "行业座谈会", "ids": ids, "sector": "人工智能", "reason": "直接受益", "repeat": False,
            }], "dropped": []})
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


def test_mapping_checks_names_duplicates_repeats_and_skips_the_call_without_candidates(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    message = _msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))

    def unknown(user):
        return _ok({"events": [{
            "title": "补贴", "ids": _ids(user), "sector": "不存在的板块", "reason": "清单里没有", "repeat": False,
        }], "dropped": []})

    client = _script(mapping=unknown)
    outcome = _run(path, [message], client, pool)
    assert len(client.calls) == 2
    row = _rows(path)[0]
    assert row["state"] == "skipped" and row["sector"] is None and row["reason"] == "清单里没有"
    assert outcome.content["entries"] == [] and outcome.stats["mapping"]["null"] == 1

    def duplicated(user):
        ids = _ids(user)
        return _ok({"events": [
            {"title": "第一件", "ids": ids[:2], "sector": "人工智能", "reason": "直接", "repeat": False},
            {"title": "第二件", "ids": [ids[1], ids[2]], "sector": "人工智能", "reason": "另一件", "repeat": False},
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
        return _ok({"events": [{
            "title": "旧座谈会", "ids": _ids(user), "sector": "人工智能", "reason": "昨天推过", "repeat": True,
        }], "dropped": []})

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


def test_leaders_skip_the_main_list_and_keep_the_other_ranking_out_of_the_prompt(tmp_path):
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
    rules = json.loads(json.dumps(load_config().rules))
    rules["sectors"]["market"]["leaderCount"] = 1
    rules["sectors"]["candidates"]["concurrency"] = 1
    rules["sectors"]["evaluation"]["concurrency"] = 1
    config = LoadedConfig(rules=rules, sha256="0" * 64, pool=pool, profiles={})
    client = _script()
    outcome = _run(path, [_msg("a", "部委印发行业补贴方案", "明确了范围。", _at(16))], client, pool,
                   skip=["300008"], config=config)
    prompt = client.calls[-1][1]["content"]
    assert "300001" in prompt and "300002" not in prompt and "300008" not in prompt
    package = _rows(path)[0]["package"]
    assert package["leaderRankings"]["limitups"]["leaders"][0]["code"].startswith("300002")
    assert package["leaderRankings"]["amount"]["leaders"][0]["code"].startswith("300001")
    assert "limitups" not in prompt
    assert outcome.content["entries"][0]["leaders"][0]["companyCode"] == "300001"
    assert outcome.content["entries"][0]["leaders"][0]["companyName"] == "甲公司"


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
    client = _script(evaluate=lambda codes: list(reversed(codes)))
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
            payload["questions"]["value"]["pass"] = False
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
    assert entry["questions"] == {"value": "有政策", "priced": "还没涨透", "money": "可能接力"}
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

    def fill(_conn, **_kwargs):
        stage["late"] = True
        return DailyFill((), 0, None)

    monkey = pytest.MonkeyPatch()
    monkey.setattr("neckline.morning.sector_line.fill_market_daily", fill)
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
            return _http({"events": [{
                "title": "补贴", "ids": _ids(user), "sector": "人工智能", "reason": "直接", "repeat": False,
            }], "dropped": []})
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
            events.append({
                "title": f"事件{number}", "ids": [number], "sector": "人工智能", "reason": "直接", "repeat": False,
            })
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


def _seed_sector_context(path):
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
            return _http({"events": [{
                "title": "金融座谈会", "ids": _ids(user), "sector": "人工智能", "reason": "直接受益", "repeat": False,
            }], "dropped": []})
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
    monkeypatch.setattr("neckline.morning.sector_line.fill_market_daily",
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
    assert "①" in page.text and "②" in page.text and "③" in page.text
    assert client.get("/read/" + "r" * 32, params={"format": "json"}).json()["content"]["sectors"] == sectors
    blob = json.dumps(latest, ensure_ascii=False)
    assert "test-key" not in blob and "r" * 32 not in blob
    if os.environ.get("NK_WRITE_V6_FIXTURE") == "1" or not _V6_FIXTURE.is_file():
        _V6_FIXTURE.write_text(json.dumps(latest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    saved = json.loads(_V6_FIXTURE.read_text(encoding="utf-8"))
    assert _stable_report(saved) == _stable_report(latest)
    assert saved["content"]["entries"] and saved["content"]["sectors"]["entries"]


def test_replay_summary_lists_both_leader_rankings(tmp_path, monkeypatch):
    snapshot = tmp_path / "snap.db"
    db.init_schema(snapshot)
    with db.write(snapshot) as conn:
        db.begin_report(conn, report_id="morning-2026-10-13", trade_date="2026-10-13",
                        window_start=datetime(2026, 10, 12, 7, tzinfo=timezone.utc),
                        window_end=datetime(2026, 10, 13, 0, tzinfo=timezone.utc),
                        config_version="screening-v5", config_sha256="0" * 64,
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
                package={"event": {"firstPublished": "2026-10-12T17:30+08:00"}, "leaderRankings": {
                    "amount": {"leaders": [{"code": "300001.SZ"}]},
                    "limitups": {"leaders": [{"code": "300002.SZ"}]},
                }}, prepared_at=datetime(2026, 10, 13, 0, 2, tzinfo=timezone.utc),
                output={"title": "金融座谈会"}, settled_at=datetime(2026, 10, 13, 0, 3, tzinfo=timezone.utc))
        return {"state": "available", "entries": 0, "stats": {}}

    monkeypatch.setattr("neckline.morning.replay.run_morning", fake_run)
    run_replay(snapshot=snapshot, trade_date="2026-10-13", work=tmp_path / "work", market_snapshot=market,
               receipts=tmp_path / "receipts", filings_cache=tmp_path / "filings", pool_seed_only=True)
    summary = json.loads((tmp_path / "work" / "2026-10-13" / "summary.json").read_text(encoding="utf-8"))
    assert summary["sectors"] == [{
        "title": "金融座谈会", "sector": "人工智能", "state": "succeeded", "verdict": "推",
        "leaders": {"amount": ["300001"], "limitups": ["300002"]},
    }]
    assert _summary({"stats": {}}, None)["sectors"] == []


def test_missing_sector_settings_are_rejected(tmp_path):
    raw = json.loads((load_config().rules and _config_path()).read_text(encoding="utf-8"))
    for key in ("prefilter", "candidates", "mapping", "leaders", "evaluation", "delivery"):
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
    assert loaded.rules["sectors"]["leaders"]["rankBy"] == "amount"


def _config_path():
    from importlib import resources
    return resources.files("neckline.config").joinpath("screening-v5.json")


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
        "leaders": [{
            "code": "300001.SZ", "name": "甲公司", "changePercent": 1.5, "change20Percent": None,
            "ma5": "上方", "ma10": None, "ma20": None, "maNote": None, "limitUps20": 0,
            "averageAmountYuan": 1000,
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


def test_sector_prompts_and_one_request_body_stay_frozen():
    assert hashlib.sha256(CANDIDATE_SYSTEM.encode()).hexdigest() == _FROZEN_CANDIDATES
    assert hashlib.sha256(MAPPING_SYSTEM.encode()).hexdigest() == _FROZEN_MAPPING
    assert hashlib.sha256(EVALUATION_SYSTEM.encode()).hexdigest() == _FROZEN_EVALUATION
    assert hashlib.sha256(_frozen_user().encode()).hexdigest() == _FROZEN_USER
    assert hashlib.sha256(canonical(_frozen_body()).encode()).hexdigest() == _FROZEN_BODY
