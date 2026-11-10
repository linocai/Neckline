"""板块线：粗筛、收消息、回流、归并、成分股、评估、收口，以及接到晨报后的真实入口。"""
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
    apply_intake, apply_prefilter, assign_numbers, backflow_messages, event_id, merge_sector_inputs,
    opening_text, _merge_mapping_halves, parse_evaluation_output, parse_mapping_output, run_sector_line,
)
from neckline.morning.sector_prompt import (
    EVALUATION_SYSTEM, MAPPING_CONTRACT, MAPPING_SYSTEM, evaluation_user_prompt,
)
from neckline.morning.stock_pool import store_rows
from neckline.screening.deepseek import ChatError, ChatResult, ChatUnknown, DeepSeekClient, canonical
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window, to_message

from tests.test_morning import (
    FakeApns, FakeModel, _client, _config, _seed_window, _with_device, at, doc,
)

DAY = date(2026, 10, 13)
_FROZEN_MAPPING = "e272d397f5ef39c0ff648a1c6d0ea5752abd75ddd49ec783e80edbdbca2e82ee"
_FROZEN_EVALUATION = "9e90a662cdd4489a0adb8f8f712574e5f7932ff78b7b548b65542076c90b57ee"
_FROZEN_USER = "b1f0afef8b441013d06233de28dd2d1ff3cdb190470c6da80f8893859d1e0718"
_FROZEN_BODY = "5db1cfa5d69f4fd3ed787f686cb285712756b70e73eb317cad6fe3325c27416f"


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
    concepts = [{"tsCode": ts_code, "name": name, "memberCount": len(codes), "members": list(codes)}]
    with db.write(path) as conn:
        db.replace_sector_concepts(conn, concepts, refreshed_at="2026-10-12T00:00:00+00:00", sha256="ab")
        seed_limit_context(conn, concepts)


def _boards(path, names=("人工智能", "机器人", "芯片", "光伏"),
            codes=("300001.SZ", "300002.SZ", "300003.SZ")) -> None:
    concepts = [{
        "tsCode": f"88500{index}.TI", "name": name, "memberCount": len(codes), "members": list(codes),
    } for index, name in enumerate(names, start=1)]
    with db.write(path) as conn:
        db.replace_sector_concepts(conn, concepts, refreshed_at="2026-10-12T00:00:00+00:00", sha256="ab")
        seed_limit_context(conn, concepts)


def seed_limit_context(conn, concepts, day=DAY, also=("300001", "300002", "300003", "300008",
                                                      "300082", "300683", "300953")) -> None:
    """给测试用的辨识度：窗口最后一天这些成分和两只主板股一起涨停，其余天只有一只无关股票。"""
    from neckline.morning.sector_recognition import recognition_window

    report = day if isinstance(day, date) else date.fromisoformat(str(day))
    window = recognition_window(report, 40)
    hot = window[-1].isoformat()
    hot_codes: set[str] = set()
    for concept in concepts:
        shorts: list[str] = []
        for code in list(concept["members"]) + ["600001", "600002"]:
            short = str(code).split(".")[0]
            if short not in shorts:
                shorts.append(short)
        conn.execute("DELETE FROM sector_members_all WHERE ts_code=?", (concept["tsCode"],))
        conn.executemany(
            "INSERT INTO sector_members_all(ts_code, code) VALUES (?, ?)",
            [(concept["tsCode"], short) for short in shorts])
        hot_codes.update(shorts)
    for code in also:
        hot_codes.add(str(code).split(".")[0])
    for trade in window:
        iso = trade.isoformat()
        if iso == hot:
            for short in sorted(hot_codes):
                conn.execute("INSERT OR IGNORE INTO limit_ups(trade_date, code) VALUES (?, ?)", (iso, short))
        else:
            conn.execute("INSERT OR IGNORE INTO limit_ups(trade_date, code) VALUES (?, ?)", (iso, "999999"))
        count = conn.execute("SELECT count(*) FROM limit_ups WHERE trade_date=?", (iso,)).fetchone()[0]
        conn.execute(
            "INSERT INTO limit_up_days(trade_date, count, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(trade_date) DO UPDATE SET count=excluded.count, fetched_at=excluded.fetched_at",
            (iso, count, "2026-10-12T00:00:00+00:00"))


def _answer(codes, *, verdict="推", views=None, price="明显高开不追") -> dict:
    return {
        "title": "值得看的板块", "verdict": verdict, "conclusion": "资金会看一眼",
        "leaders": [{"code": code, "why": "消息点到它", "price": price} for code in codes],
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
    chunk = prompt.split("题材辨识度名单", 1)[-1]
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
        if "板块编辑" in system:
            limit = int(re.search(r"上限：(\d+)", user).group(1))
            ids = re.findall(r"\[(e\d+)\]", user)[:limit]
            return _ok({"chosen": ids, "reason": "比较后留下"})
        codes = _leader_codes(user)
        if len(messages) > 2:
            shown = codes
        elif evaluate is not None:
            shown = evaluate(codes)
        else:
            shown = codes
        return _ok(_answer(shown))

    return Scripted(handler)


def _run(path, messages, client, pool=None, *, clock=None, search=None, live=False, skip=(), config=None,
         cover=True):
    pool = pool or _pool()
    if cover:
        from tests.conftest import cover_sector_history
        cover_sector_history(path, DAY)
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
        ("dropSources-keep", _msg("c2", "工信部印发行业补贴方案", "明确了补贴范围。", _at(16)), False),
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


# ------------------------------------------------------------------ 收消息、归并


def test_intake_classifies_each_opening_and_counts_them():
    settings = load_config().rules["sectors"]
    intake, head = settings["intake"], settings["prefilter"]["bodyHeadChars"]

    def kind(title, text="正文。"):
        kept, counts = apply_intake([_msg("x", title, text, _at(16))], intake, body_head_chars=head)
        label = next(name for name, count in counts.items() if name != "kept" and count)
        return label, bool(kept)

    assert kind("工信部印发行业补贴方案") == ("publisher", True)
    assert kind("工信部宣布降准") == ("macro", False)  # 总量政策先于发布主体
    assert kind("工信部？印发行业方案") == ("noise", False)
    assert kind("工信部与美国商务部沟通方案") == ("outside", False)  # 涉外，不算 A
    assert kind("工信部会见行业协会") == ("outside", False)  # 外交动作，不算 A
    assert kind("华为获得大额订单") == ("bigCompany", True)
    assert kind("今天多家媒体报道称啊啊啊华为获得大额订单") == ("outside", False)  # 公司名起点不在前 12 字
    assert kind("国家印发项目开工方案") == ("document", True)
    assert kind("国家印发公司开工方案") == ("outside", False)  # 公司在排除词里
    assert kind("印发行业补贴方案") == ("outside", False)  # 有文件动词，没有范围
    assert kind("行业龙头获得大额订单") == ("outside", False)
    # 问号是正则特殊字符，转义后仍按噪声去掉。
    assert kind("工信部发布行业方案?") == ("noise", False)
    messages = [
        _msg("a", "工信部印发行业补贴方案", "一", _at(16)),
        _msg("b", "华为获得大额订单", "二", _at(17)),
        _msg("c", "国家印发项目开工方案", "三", _at(18)),
        _msg("d", "央行宣布降准", "四", _at(19)),
    ]
    kept, counts = apply_intake(messages, intake, body_head_chars=head)
    assert [message.document_id for message in kept] == ["a", "b", "c"]
    assert counts == {
        "publisher": 1, "bigCompany": 1, "document": 1, "macro": 1, "noise": 0, "outside": 0, "kept": 3,
    }


def test_backflow_unions_named_companies_and_blocks_a_real_subject(tmp_path):
    from neckline.screening.classify import Classification, CompanyResult, Event

    settings = load_config().rules["sectors"]["backflow"]
    shared = _msg("a", "工信部印发行业补贴方案", "两家都受益。", _at(16))
    bearish = _msg("b", "行业消息", "利空。", _at(17))
    blocked = _msg("c", "公司自己的订单", "主体。", _at(18))
    industry = _msg("d", "证监会印发行业动态", "主体但是行业动态。", _at(19))

    def event(company, mid, relation, kind, direction):
        return Event(company, [mid], relation, kind, direction, None, "新", "标题", "理由", "渠道", None)

    classification = Classification({
        "m1": shared, "m2": bearish, "m3": blocked, "m4": industry,
    }, {
        "300001.SZ": CompanyResult("300001.SZ", ["m1"], [
            event("300001.SZ", "m1", "受益方", "订单中标", "利好")]),
        "300002.SZ": CompanyResult("300002.SZ", ["m1"], [
            event("300002.SZ", "m1", "提及", "行业动态", "利好")]),
        "300003.SZ": CompanyResult("300003.SZ", ["m2"], [
            event("300003.SZ", "m2", "受益方", "行业动态", "利空")]),
        "300008.SZ": CompanyResult("300008.SZ", ["m3", "m4"], [
            event("300008.SZ", "m3", "主体", "订单中标", "利好"),
            event("300008.SZ", "m3", "受益方", "行业动态", "利好"),
            event("300008.SZ", "m4", "主体", "行业动态", "利好"),
        ]),
    }, [])
    found = backflow_messages(classification, settings)
    assert set(found) == {shared.ref, industry.ref}
    assert found[shared.ref][1] == {"300001", "300002"}
    assert found[industry.ref][1] == {"300008"}
    merged, named, refs = merge_sector_inputs([shared], found)
    assert [message.document_id for message in merged] == ["a", "d"]
    assert named[shared.ref] == {"300001", "300002"} and named[industry.ref] == {"300008"}
    assert refs == {shared.ref, industry.ref}
    pool = _pool()
    path = _db(tmp_path)
    _concept(path, ("300001.SZ", "300002.SZ", "300008.SZ"), name="参股券商")

    def mapping(user):
        return _ok({"events": [_event({
            "title": "行业座谈会", "ids": _ids(user), "sector": "参股券商", "reason": "直接受益", "repeat": False,
        })], "dropped": []})

    client = _script(mapping=mapping)
    # 直接把回流消息交给入口：主线名单跳过 300008，点名公司仍在名单里。
    from neckline.morning.sector_line import run_sector_line as _entry
    entered = _entry(
        path, report_id="morning-2026-10-13-b", day=DAY, window=_window(), messages=[],
        config=_rules(pool), pool=pool, skip=["300008"], client=client,
        clock=lambda: _at(8, day=13), classification=classification)
    assert entered.stats["backflow"]["messages"] == 2
    assert entered.stats["backflow"]["kept"] == 2
    prompts = [call[1]["content"] for call in client.calls if "板块消息评估员" in call[0]["content"]]
    prompt = prompts[-1]
    assert "这是身份类板块（板块名带「参股」）" in prompt
    assert "300001 甲公司" in prompt and "消息点名：是" in prompt
    assert "300008" not in prompt
    for marker in ("涨跌", "成交额", "上涨家数", "前 1", "前 5", "前 20", "等权"):
        assert marker not in prompt


def test_empty_intake_lists_match_nothing_and_a_prefilter_drop_never_reaches_mapping(tmp_path):
    pool = _pool()
    empty = Scripted(lambda *_a: (_ for _ in ()).throw(AssertionError("粗筛掉的不该调用")))
    quiet = _run(_db(tmp_path), [_msg("c", "公告", "正文", _at(16), "cninfo-announcement")], empty, pool)
    assert quiet.gap is None and quiet.content == {"candidates": 0, "evaluated": 0, "overflow": 0, "entries": []}
    assert quiet.stats["intake"]["kept"] == 0 and empty.calls == []


def test_mapping_checks_names_duplicates_repeats_and_skips_the_call_without_candidates(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    message = _msg("a", "工信部印发行业补贴方案", "明确了范围。", _at(16))

    def unknown(user):
        return _ok({"events": [_event({
            "title": "补贴", "ids": _ids(user), "sector": "不存在的板块", "reason": "清单里没有", "repeat": False,
        })], "dropped": []})

    client = _script(mapping=unknown)
    outcome = _run(path, [message], client, pool)
    assert len(client.calls) == 1
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
        _msg("a", "工信部印发行业补贴方案", "第一件事。", _at(16)),
        _msg("b", "证监会放开行业准入", "第二件事。", _at(17)),
        _msg("c", "国家印发项目开工方案", "第三件事。", _at(18)),
    ]
    other = _db(tmp_path / "dup")
    _concept(other)
    dup = _script(mapping=duplicated)
    _run(other, three, dup, pool)
    stored = _rows(other)
    assert [row["title"] for row in stored] != [] and len(stored) == 2
    packages = {row["title"]: row["package"] for row in stored}
    assert len(packages["第一件"]["messages"]) == 2
    assert [item["title"] for item in packages["第二件"]["messages"]] == ["国家印发项目开工方案"]

    def repeated(user):
        return _ok({"events": [_event({
            "title": "旧座谈会", "ids": _ids(user), "sector": "人工智能", "reason": "昨天推过", "repeat": True,
        })], "dropped": []})

    again = _db(tmp_path / "repeat")
    _concept(again)
    repeat = _script(mapping=repeated)
    repeated_outcome = _run(again, [message], repeat, pool)
    assert len(repeat.calls) == 1
    assert _rows(again)[0]["state"] == "skipped" and _rows(again)[0]["repeat"] is True
    assert repeated_outcome.content["entries"] == [] and repeated_outcome.stats["mapping"]["repeat"] == 1

    none = _script()
    silent = _db(tmp_path / "none")
    _concept(silent)
    outside = _msg("z", "行业龙头获得大额订单", "正文。", _at(16))
    empty = _run(silent, [outside], none, pool)
    assert none.calls == []
    assert empty.gap is None and empty.content["candidates"] == 0 and empty.stats["intake"]["outside"] == 1


def test_members_skip_the_main_list_and_render_one_line_each(tmp_path):
    pool = _pool()
    path = _db(tmp_path)
    _concept(path, ("300008.SZ", "300001.SZ", "300002.SZ"))
    with db.write(path) as conn:
        db.replace_stock_pool(conn, [
            {"code": "300001.SZ", "name": "甲公司", "summary": "做充电", "summarySource": "tushare",
             "circMv": 1_234_000, "totalMv": 2_000_000, "mvDate": "2026-10-12"},
            {"code": "300002.SZ", "name": "乙公司", "summary": "做材料", "summarySource": "tushare",
             "circMv": 1000, "totalMv": 2000, "mvDate": "2026-10-12"},
        ], refreshed_at="2026-10-12T00:00:00+00:00", sha256="ab", source="tushare")
    client = _script()
    outcome = _run(path, [_msg("a", "工信部印发行业补贴方案", "正文里没有简称。", _at(16))], client, pool,
                   skip=["300008"])
    prompt = client.calls[-1][1]["content"]
    assert "名单（题材辨识度名单：近 40 个交易日里板块整体走热的日子涨停过的成分股，已按辨识度排好，去掉了今天个股名单里的，共 2 只）：" in prompt
    assert "300001 甲公司｜题材辨识度：板块热的日子涨停 1 次，最近一次 " in prompt
    assert "｜主营：做充电｜流通市值：123.4 亿元｜消息点名：否" in prompt
    assert "300002" in prompt and "300008" not in prompt and "龙头" not in prompt
    for marker in ("涨跌", "成交额", "上涨家数", "前 1", "前 5", "前 20", "等权"):
        assert marker not in prompt
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
    message = _msg("a", "工信部印发行业补贴方案", "明确了范围。", _at(16, 30))
    client = _script()
    original = client.handler

    def broken_price(messages, options):
        result = original(messages, options)
        if "评估员" in messages[0]["content"] and len(messages) == 2:
            payload = json.loads(result.content)
            payload["leaders"][0]["price"] = 1
            result = _ok(payload)
        return result

    client.handler = broken_price
    outcome = _run(path, [message], client, pool)
    assert len(client.calls) == 3  # 归并、评估、修正
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
            payload["leaders"] = []
            result = _ok(payload)
        return result

    veto.handler = deny
    vetoed = _run(veto_path, [message], veto, pool)
    assert _rows(veto_path)[0]["verdict"] == "不推" and _rows(veto_path)[0]["output"]["vetoed"] is True
    assert _rows(veto_path)[0]["output"]["note"] == "判推但核对后没有承接股"
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
    assert entry["sector"] == {"name": "人工智能", "code": "885001.TI", "memberCount": 3, "identity": None}
    assert "questions" not in entry
    assert entry["conclusion"] == "资金会看一眼" and entry["risk"] == "可能落空"
    assert entry["firstPublished"] == "2026-10-12T16:30+08:00"
    assert entry["sources"][0]["title"] == "工信部印发行业补贴方案"
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
    message = _msg("a", "工信部印发行业补贴方案", "明确了范围。", _at(16))
    stage = {"late": False}

    def clock():
        return _at(9, 6, day=13) if stage["late"] else _at(8, day=13)

    class Flip:
        def search(self, **_kwargs):
            stage["late"] = True
            return SearchOutcome(payload={"results": []}, from_receipt=False, seconds=0.1, error=None)

    client = _script()
    outcome = _run(path, [message], client, pool, clock=clock, search=Flip(), live=True)
    assert len(client.calls) == 1
    assert _rows(path)[0]["state"] == "gap" and _rows(path)[0]["error"] == "收口时刻前没评完"
    assert outcome.gap == "板块线没有完成：收口时刻前没评完"

    repair_path = _db(tmp_path / "repair")
    _concept(repair_path)
    flipped = {"late": False}

    def later():
        return _at(9, 6, day=13) if flipped["late"] else _at(8, day=13)

    def bad(messages, _options):
        system = messages[0]["content"]
        if "板块分析员" in system:
            return _script().handler(messages, _options)
        flipped["late"] = True
        return _ok({"title": "缺字段"})

    client = Scripted(bad)
    late = _run(repair_path, [message], client, pool, clock=later)
    assert len(client.calls) == 2
    assert _rows(repair_path)[0]["state"] == "gap"
    assert late.stats["evaluation"]["repairs"] == 0


def test_unknown_evaluation_is_not_sent_again(tmp_path, monkeypatch):
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    pool = _pool()
    path = _db(tmp_path)
    _concept(path)
    message = _msg("a", "工信部印发行业补贴方案", "明确了范围。", _at(16))
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
    board_names = ("人工智能", "机器人", "芯片", "光伏", "短剧游戏")
    _boards(path, board_names)
    titles = [
        "工信部印发行业补贴方案", "证监会印发行业补贴方案", "央行印发行业补贴方案",
        "财政部印发行业补贴方案", "国家印发项目开工方案",
    ]
    messages = [_msg(f"m{index}", title, "方案落地。", _at(16 + index)) for index, title in enumerate(titles)]

    def mapping(user):
        ids = _ids(user)
        events = []
        for index, number in enumerate(reversed(ids)):
            events.append(_event({
                "title": f"事件{number}", "ids": [number],
                "sector": board_names[index % len(board_names)], "reason": "直接", "repeat": False,
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

    none = _script()
    zero = _db(tmp_path / "zero")
    _concept(zero)
    quiet = _run(zero, [_msg("z", "行业龙头获得大额订单", "正文。", _at(16))], none, pool)
    assert quiet.gap is None and quiet.content["entries"] == [] and quiet.content["evaluated"] == 0

    # 没有概念清单时不归并：一次模型调用也不发，如实写缺口
    calls = _script()
    blank = _run(_db(tmp_path / "blank"), messages[:1], calls, pool)
    assert blank.gap == "板块线没有完成：概念清单是空的" and calls.calls == []
    assert blank.stats["mapping"]["calls"] == 0


def test_same_messages_keep_the_same_event_id():
    assert event_id(["b#1", "a#1"]) == event_id(["a#1", "b#1"])
    assert len(event_id(["a#1"])) == 16


# ------------------------------------------------------------------ 晨报入口


def _seed_sector_context(path, *, with_daily=True):
    del with_daily
    _seed_window(path)
    with db.write(path) as conn:
        db.store_documents(conn, [
            doc("jin10-flash", "forum", "央行、证监会将于明天召开座谈会，邀请金融机构参加。",
                at("2026-10-12", "17:30"), at("2026-10-12", "20:00"), title="央行、证监会将于明天召开座谈会"),
        ])
        concepts = [{
            "tsCode": "885001.TI", "name": "人工智能", "memberCount": 3,
            "members": ["300953.SZ", "300683.SZ", "300082.SZ"],
        }]
        db.replace_sector_concepts(conn, concepts, refreshed_at="2026-10-12T00:00:00+00:00", sha256="cd")
        seed_limit_context(conn, concepts)


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
        if "板块编辑" in system:
            limit = int(re.search(r"上限：(\d+)", user).group(1))
            ids = re.findall(r"\[(e\d+)\]", user)[:limit]
            return _http({"chosen": ids, "reason": "比较后留下"})
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
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    model = FakeModel()
    result = run_morning(
        path, now=at("2026-10-13", "08:02"), config=_config(), clock=lambda: at("2026-10-13", "08:02"),
        collector=lambda _at: {"status": "completed"},
        client_factory=lambda rid: _client(path, _sector_handler(model, mapping_status=500), rid, attempts=1),
        pusher=Pusher(path, FakeApns(), lambda: at("2026-10-13", "08:02")))
    assert result["state"] == "available" and result["entries"] == 2
    assert "2 家材料不全：海特生物、震裕科技" in result["gapSummary"]
    assert "板块线没有完成：归并没有完成" in result["gapSummary"]
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
    assert all("questions" not in entry for entry in latest["content"]["sectors"]["entries"])


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
    assert entry["leaders"] and entry["sources"] and entry["conclusion"] and entry["risk"] and entry["views"]
    assert "questions" not in entry
    assert "price" in latest["content"]["entries"][0]["questions"]
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
    sector_html = page.text.split("<h2>板块热点</h2>", 1)[1]
    assert "① 是不是新消息" not in sector_html and "② 分量够不够" not in sector_html
    assert "③ 板块在什么位置" not in sector_html and "④ 谁来承接" not in sector_html
    assert entry["conclusion"] in sector_html and "风险：" in sector_html
    assert "为什么" in sector_html and "价格" in sector_html
    assert client.get("/read/" + "r" * 32, params={"format": "json"}).json()["content"]["sectors"] == sectors
    blob = json.dumps(latest, ensure_ascii=False)
    assert "test-key" not in blob and "r" * 32 not in blob
    assert latest["configVersion"] == "screening-v7"
    assert latest["content"]["entries"] and sectors["entries"]


_V7_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "report-v7-two-lines.json"


def _member_flags(prompt: str) -> list[tuple[str, str]]:
    return re.findall(r"(?m)^(\d{6}) .*消息点名：(是|否)$", prompt)


def _two_line_transport(request: httpx.Request) -> httpx.Response:
    """主线只推海特；板块一边是参股券商（点名公司模型不写），一边是人工智能。"""
    if "/tools/search" in str(request.url):
        return httpx.Response(200, json={"results": [{
            "title": "媒体标题", "url": "https://news.example/a", "date": "2026-10-12T10:00:00+08:00",
            "summary": "认为有催化", "site_name": "财联社",
        }]})
    body = json.loads(request.content)
    system = body["messages"][0]["content"]
    user = body["messages"][1]["content"] if len(body["messages"]) > 1 else ""
    if "板块分析员" in system:
        events = []
        for line in user.splitlines():
            found = re.search(r"\[(n\d+)\].*｜([^｜]+)$", line)
            if found is None:
                continue
            number, title = found.group(1), found.group(2)
            if "证监会" in title:
                events.append(_event({
                    "title": "券商被点名", "ids": [number], "sector": "参股券商",
                    "reason": "政策点到券商", "repeat": False,
                }))
            elif "工信部" in title:
                events.append(_event({
                    "title": "设备补贴", "ids": [number], "sector": "人工智能",
                    "reason": "补贴落到设备", "repeat": False,
                }))
        return _http({"events": events, "dropped": []})
    if "板块消息评估员" in system:
        flags = _member_flags(user)
        if "参股券商" in user:
            shown = [code for code, named in flags if named == "否"]
        else:
            shown = [flags[0][0]] if flags else []
        return _http(_answer(shown, views=[{"by": "媒体", "name": "财联社", "text": "认为有催化", "ref": "s1"}]))
    if user.startswith("晨报交易日"):
        code = re.search(r"公司：(\d{6})", user)
        verdict = "推" if code and code.group(1) == "300683" else "不推"
        return _http({
            "title": "值得跟踪", "verdict": verdict, "moneyLogic": "一般", "conclusion": "值得跟踪",
            "questions": {
                "value": {"pass": True, "text": "利好有量"},
                "priced": {"pass": True, "text": "还没怎么涨"},
                "money": {"text": "有新增量"},
                "price": {"text": "明显高开不追"},
            },
            "risk": "可能落空",
            "views": [{"by": "媒体", "name": "来源", "text": "转述", "ref": "m1"}],
        })
    blocks = re.split(r"^### ", user, flags=re.M)[1:]
    companies = []
    for block in blocks:
        code = block.split()[0]
        body_part = block.split("窗口开始前的相关消息")[0]
        ids = re.findall(r"^\[(m\d+)\]", body_part, flags=re.M)
        if code == "300683":
            companies.append({"company": code, "events": [{
                "ids": ids, "relation": "主体", "type": "业绩预增", "direction": "利好",
                "channel": "公司公告", "amountYuan": None, "firstDisclosed": None, "fresh": "新",
                "importance": "高", "headline": "海特签协议", "reason": "公告",
            }], "mentions": []})
        elif code == "300953":
            companies.append({"company": code, "events": [{
                "ids": ids, "relation": "受益方", "type": "行业动态", "direction": "利好",
                "channel": "媒体报道", "amountYuan": None, "firstDisclosed": None, "fresh": "新",
                "importance": "高", "headline": "政策点到券商", "reason": "被点名",
            }], "mentions": []})
        else:
            companies.append({"company": code, "events": [], "mentions": ids})
    return _http({"companies": companies})


def test_two_line_report_fixture_matches_the_real_entry(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    prev = "2026-10-12"
    docs = [
        doc("jin10-flash", "h1", "【海特生物：签署授权协议】海特生物公告首付款700万美元",
            at(prev, "16:45"), at(prev, "20:00")),
        doc("jin10-flash", "broker", "震裕科技被列入试点范围。",
            at(prev, "17:10"), at(prev, "20:00"), title="证监会印发行业补贴方案"),
        doc("jin10-flash", "ai", "补贴范围写进了设备更新。",
            at(prev, "18:20"), at(prev, "20:00"), title="工信部印发行业补贴方案"),
    ]
    with db.write(path) as conn:
        db.store_documents(conn, docs)
        for run_id, slot, start in (("collect-a", at(prev, "20:00"), at(prev, "08:00")),
                                    ("collect-b", at("2026-10-13", "08:00"), at(prev, "19:00"))):
            db.begin_collection(conn, run_id=run_id, slot_at=slot, started_at=slot)
            db.finish_collection(conn, run_id=run_id, status="completed", finished_at=slot, sources=[
                {"sourceKey": key, "requestedStart": start.isoformat(), "requestedEnd": slot.isoformat(),
                 "complete": True, "error": None, "documents": 1}
                for key in ("tushare-major-news", "jin10-flash", "jin10-news")])
        store_rows(conn, [
            {"code": code, "name": name, "fullname": "", "listDate": "2010-01-01", "aliases": [name],
             "summary": None, "summarySource": "none"}
            for code, name in (("300953.SZ", "震裕科技"), ("300683.SZ", "海特生物"), ("300082.SZ", "奥克股份"))
        ], now=datetime(2026, 10, 12, 4, tzinfo=timezone.utc), source="tushare")
        concepts = [
            {"tsCode": "885801.TI", "name": "参股券商", "memberCount": 3,
             "members": ["300953.SZ", "300683.SZ", "300082.SZ"]},
            {"tsCode": "885001.TI", "name": "人工智能", "memberCount": 3,
             "members": ["300953.SZ", "300683.SZ", "300082.SZ"]},
        ]
        db.replace_sector_concepts(conn, concepts, refreshed_at="2026-10-12T00:00:00+00:00", sha256="v7")
        seed_limit_context(conn, concepts)
    from tests.conftest import cover_sector_history
    cover_sector_history(path, "2026-10-13")
    _with_device(path)

    class _Clock:
        current = datetime(2026, 10, 13, 0, 2, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

        fromisoformat = staticmethod(datetime.fromisoformat)

    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(cli, "datetime", _Clock)
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", "r" * 32)
    monkeypatch.setattr(cli, "run_collection", lambda *_a, **_k: {"status": "completed"})
    monkeypatch.setattr("neckline.push.apns.send_push", FakeApns())
    _install_clients(monkeypatch, _two_line_transport)
    assert cli.main(["--db", str(path), "morning", "--now", "2026-10-13T08:02:00+08:00"]) == 0
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=lambda: None))
    raw = TestClient(app).get("/api/v4/reports/latest").content
    latest = json.loads(raw)
    stocks = latest["content"]["entries"]
    sector_entries = latest["content"]["sectors"]["entries"]
    assert len(stocks) == 1 and stocks[0]["companyCode"] == "300683"
    assert "questions" in stocks[0]
    assert len(sector_entries) == 2
    by_name = {entry["sector"]["name"]: entry for entry in sector_entries}
    assert set(by_name) == {"参股券商", "人工智能"}
    named = by_name["参股券商"]["leaders"]
    assert [item["companyCode"] for item in named] == ["300082"]
    assert named[0]["why"] == "消息点到它"
    assert by_name["人工智能"]["leaders"][0]["why"] == "消息点到它"
    assert by_name["参股券商"]["sector"]["identity"] == "参股"
    assert by_name["人工智能"]["sector"]["identity"] is None
    assert all("questions" not in entry for entry in sector_entries)
    text = json.dumps(latest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for secret in ("test-key", "r" * 32, "ab" * 32):
        assert secret not in text
    live = json.loads(text)
    frozen = json.loads(_V7_FIXTURE.read_text(encoding="utf-8"))
    # 对外板块条目不变。stats.sectors 多了三路，夹具仍与 App 那份逐字相同，这里只比对其余字段。
    live_sectors = live["content"]["stats"].pop("sectors")
    frozen["content"]["stats"].pop("sectors")
    assert live == frozen
    assert live_sectors["intake"] == {
        "publisher": 2, "bigCompany": 0, "document": 0, "macro": 0, "noise": 0, "outside": 0, "kept": 2,
    }
    assert live_sectors["routes"] == {"A": 2, "B": 0, "C": 0, "kept": 2}
    assert live_sectors["fermenting"] == {
        "ran": False, "note": "C 路关闭", "boards": [], "messages": 0,
    }
    assert live_sectors["selection"]["called"] is False and live_sectors["selection"]["candidates"] == 2


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
    for key in ("prefilter", "intake", "identity", "backflow", "mapping", "carriers", "evaluation", "delivery",
                "overseas", "fermenting", "selection"):
        broken = json.loads(json.dumps(raw))
        broken["sectors"].pop(key)
        target = tmp_path / f"{key}.json"
        target.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ValueError, match=rf"sectors\.{key}"):
            load_config(path=target)
    loaded = load_config()
    assert "candidates" not in loaded.rules["sectors"] and "market" not in loaded.rules["sectors"]
    assert set(loaded.rules["sectors"]["carriers"]) == {"max"}
    assert loaded.rules["sectors"]["evaluation"]["modelOptions"] == loaded.rules["evaluation"]["modelOptions"]
    assert loaded.rules["sectors"]["delivery"]["maxEvents"] == 3
    assert "leaders" not in loaded.rules["sectors"]
    assert loaded.rules["sectors"]["carriers"]["max"] == 3
    assert loaded.rules["sectors"]["intake"]["bigLeadChars"] == 12
    assert loaded.rules["sectors"]["mapping"]["contract"] == MAPPING_CONTRACT
    stale = json.loads(json.dumps(raw))
    stale["sectors"]["mapping"]["contract"] = "sector-mapping-v2"
    target = tmp_path / "mapping-v2.json"
    target.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(ValueError, match=f"sectors.mapping.contract 须为 {MAPPING_CONTRACT}"):
        load_config(path=target)
    retired = json.loads(json.dumps(raw))
    retired["sectors"]["leaders"] = {"rankBy": "amount"}
    retired["sectors"]["market"] = {"leaderCount": 3, "leaderWindow": 20}
    target = tmp_path / "retired.json"
    target.write_text(json.dumps(retired), encoding="utf-8")
    with pytest.raises(ValueError, match="已停用"):
        load_config(path=target)
    leftover = json.loads(json.dumps(raw))
    leftover["sectors"]["candidates"] = {"concurrency": 1}
    target = tmp_path / "candidates.json"
    target.write_text(json.dumps(leftover), encoding="utf-8")
    with pytest.raises(ValueError, match="sectors.candidates 已停用"):
        load_config(path=target)


def _config_path():
    from importlib import resources
    return resources.files("neckline.config").joinpath("screening-v7.json")


def _frozen_package() -> dict:
    return {
        "report": {"tradeDate": "2026-10-13", "windowStart": "2026-10-12T15:00:00+08:00",
                   "windowEnd": "2026-10-13T08:00:00+08:00"},
        "event": {"title": "金融座谈会", "reason": "直接受益", "firstPublished": "2026-10-12T17:00+08:00"},
        "sector": {"name": "人工智能", "code": "885001.TI", "memberCount": 3},
        "original": {"documents": [{
            "id": "m1", "source": "金十快讯", "publishedAt": "2026-10-12T17:00:00+08:00",
            "title": "金融座谈会", "text": "将于明天召开。", "truncated": False, "error": None,
        }], "media": []},
        "carriers": {"max": 3},
        "identityKeyword": None,
        "recognition": {"lookbackDays": 40},
        "members": [{
            "code": "300001", "name": "特锐德", "summary": "做充电", "circMv": 1_234_000, "named": False,
            "recognition": {"count": 2, "last": "2026-10-10"},
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
        "carriers": {"max": 3},
        "members": [{"code": code, "name": code, "named": False} for code in members],
        "original": {"documents": [{"id": "m1"}], "media": []},
        "search": {"results": []},
    }


def test_sector_evaluation_rejects_a_bad_carrier_list_and_vetoes():
    package = _eval_package(("300001", "300002", "300003"))
    package["members"][0]["named"] = True
    package["members"][2]["named"] = True
    missing = _answer(["300002"])
    del missing["title"]
    assert parse_evaluation_output(json.dumps(missing), package) == "title 为空"
    bad_price = _answer(["300099"])
    bad_price["leaders"][0]["why"] = "  "
    assert parse_evaluation_output(json.dumps(bad_price), package) == "leaders 理由为空"
    bad_price = _answer(["300002"])
    bad_price["leaders"][0]["price"] = 1
    assert parse_evaluation_output(json.dumps(bad_price), package) == "leaders 价格不是字符串"
    parsed = parse_evaluation_output(json.dumps(_answer(["300002", "300001"])), package)
    assert [item["code"] for item in parsed["leaders"]] == ["300001", "300002"]
    by_code = {item["code"]: item for item in parsed["leaders"]}
    assert by_code["300001"]["why"] == "消息点到它" and by_code["300001"]["price"] == "明显高开不追"
    assert "300003" not in by_code
    off_list = parse_evaluation_output(json.dumps(_answer(["300099"])), package)
    assert off_list["verdict"] == "不推" and off_list["vetoed"] is True
    assert off_list["leaders"] == []
    empty = _eval_package(())
    empty["members"] = []
    vetoed = parse_evaluation_output(json.dumps(_answer([])), empty)
    assert vetoed["verdict"] == "不推" and vetoed["vetoed"] is True
    assert vetoed["note"] == "判推但核对后没有承接股" and vetoed["leaders"] == []
    held = parse_evaluation_output(json.dumps(_answer(["300002"], verdict="不推")), package)
    assert held["verdict"] == "不推" and held["leaders"] and "vetoed" not in held


def test_weight_fields_do_not_skip_an_event(tmp_path):
    path = _db(tmp_path)
    _concept(path)
    message = _msg("a", "工信部印发行业补贴方案", "明确了范围。", _at(16))

    def light(user):
        return _ok({"events": [_event({
            "title": "分量不够的会", "ids": _ids(user), "sector": "人工智能", "reason": "对得上",
            "repeat": False, "weighty": False, "weight": "只是表态，带不动板块",
        })], "dropped": []})

    client = _script(mapping=light)
    outcome = _run(path, [message], client)
    assert len(client.calls) == 2
    row = _rows(path)[0]
    assert row["state"] == "succeeded" and row["reason"] == "对得上" and row["verdict"] == "推"
    assert "light" not in outcome.stats["mapping"]
    assert outcome.content["entries"] and outcome.content["entries"][0]["title"] == "值得看的板块"


def test_intake_counts_land_in_the_sector_stats(tmp_path):
    path = _db(tmp_path)
    _concept(path)
    messages = [
        _msg("a", "工信部印发行业补贴方案", "留下。", _at(16)),
        _msg("b", "央行宣布降准", "总量。", _at(17)),
        _msg("c", "行业龙头获得大额订单", "没收到。", _at(18)),
    ]
    client = _script()
    outcome = _run(path, messages, client, _pool())
    assert outcome.stats["intake"] == {
        "publisher": 1, "bigCompany": 0, "document": 0, "macro": 1, "noise": 0, "outside": 1, "kept": 1,
    }
    assert outcome.stats["backflow"] == {"messages": 0, "kept": 0}
    assert "candidates" not in outcome.stats and "light" not in outcome.stats["mapping"]
    assert len(client.calls) == 2


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
        _msg("a", "工信部印发行业补贴方案", "早上的方案。", _at(16)),
        _msg("b", "证监会放开行业准入", "第二条。", _at(17)),
        _msg("c", "国家印发项目开工方案", "第三条。", _at(18)),
        _msg("d", "华为获得大额订单", "第四条。", _at(19)),
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
    marker = "板块清单（每行：名称｜池内成分股数）："
    full_tail = users[0].split(marker, 1)[1]
    for user in users[1:]:
        assert user.split(marker, 1)[1] == full_tail
        assert "昨天的会｜人工智能" in user
    assert "国家印发项目开工方案" not in users[1]
    assert "工信部印发行业补贴方案" not in users[2]
    by_title = {row["title"]: row for row in _rows(path)}
    assert set(by_title) == {"早间方案", "说不清", "晚间方案", "另一件"}
    early = by_title["早间方案"]
    assert early["state"] == "succeeded" and early["reason"] == "早的理由" and early["repeat"] is False
    assert early["package"]["event"]["title"] == "早间方案"
    late = by_title["晚间方案"]
    assert late["state"] == "skipped" and late["reason"] == "晚的理由" and late["repeat"] is True
    assert by_title["说不清"]["state"] == "skipped" and by_title["说不清"]["sector"] is None
    assert by_title["另一件"]["state"] == "skipped" and by_title["另一件"]["sector"] is None
    assert [entry["title"] for entry in outcome.content["entries"]] == ["值得看的板块"]


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
    assert [item["title"] for item in rows[0]["package"]["messages"]] == ["国家印发项目开工方案", "华为获得大额订单"]


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
        _msg("a", "工信部印发行业补贴方案", "明确了范围。", _at(16)),
        _msg("b", "证监会放开行业准入", "试点扩大到三个省。", _at(17)),
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
        if "板块编辑" in system:
            ids = re.findall(r"\[(e\d+)\]", user)
            return _http({"chosen": ids[:1], "reason": "同板块留一个"})
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
        _run(path, messages, client(2))
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
    done = {row["title"]: row for row in _rows(path) if row["state"] == "succeeded"}
    assert set(done) == {"前一半", "后一半"}
    assert [item["title"] for item in done["前一半"]["package"]["messages"]] == ["工信部印发行业补贴方案"]
    assert [item["title"] for item in done["后一半"]["package"]["messages"]] == ["国家印发项目开工方案"]


def test_sector_prompts_and_one_request_body_stay_frozen():
    assert MAPPING_CONTRACT == "sector-mapping-v6"
    assert "代码按三路收进来的消息" in MAPPING_SYSTEM
    assert "题材辨识度只说明市场炒这个题材时认谁" in EVALUATION_SYSTEM
    rendered = _frozen_user()
    for marker in ("涨跌", "成交额", "上涨家数", "前 1", "前 5", "前 20", "等权"):
        assert marker not in rendered
    assert "消息点名：否" in rendered and "做充电" in rendered
    assert "来自哪一路" not in rendered
    assert hashlib.sha256(rendered.encode()).hexdigest() == _FROZEN_USER


def _mapped(title, ids, *, repeat=False, reason="理由"):
    return {"title": title, "ids": list(ids), "sector": "人工智能", "reason": reason, "repeat": repeat}


def test_mapping_content_filter_splits_once_and_keeps_a_surviving_half(tmp_path):
    path = _db(tmp_path)
    _concept(path)
    messages = _sector_messages()

    def handler(sent, _options):
        if "板块分析员" not in sent[0]["content"]:
            codes = _leader_codes(sent[1]["content"])
            return _ok(_answer(codes[:1] or ["300001"]))
        ids = _ids(sent[1]["content"])
        if ids == ["n1", "n2", "n3", "n4"]:
            return ChatResult("", {"prompt_tokens": 1, "completion_tokens": 1}, "d", False, 0.1, "content_filter")
        if ids == ["n1", "n2"]:
            return _ok({"events": [_event({
                "title": "左半", "ids": ["n1"], "sector": "人工智能", "reason": "直接", "repeat": False,
            })], "dropped": []})
        raise ChatError("一半被拦")

    outcome = _run(path, messages, Scripted(handler))
    assert outcome.stats["mapping"]["filtered"] == 1
    assert outcome.stats["mapping"]["splits"] == 1
    assert outcome.stats["mapping"]["incomplete"] is True
    titles = {row["title"] for row in _rows(path) if row["state"] == "succeeded"}
    assert titles == {"左半"}


def test_mapping_merge_keeps_repeats_apart_and_folds_the_rest(tmp_path):
    """同一板块的两半事件都保留，标题各自留下。重复的也各自保留。"""
    ordered = ["n1", "n2", "n3", "n4"]

    def merged(left, right):
        return _merge_mapping_halves([left], [right], ordered, [], [], {"人工智能"})

    repeat_and_valid = merged(
        _mapped("旧重复", ["n1"], repeat=True, reason="旧理由"),
        _mapped("新事件", ["n3"], reason="新理由"))
    by_title = {item["title"]: item for item in repeat_and_valid}
    assert set(by_title) == {"旧重复", "新事件"}
    assert by_title["旧重复"]["repeat"] is True and by_title["旧重复"]["ids"] == ["n1"]
    assert by_title["旧重复"]["reason"] == "旧理由"
    assert "weight" not in by_title["旧重复"] and "weighty" not in by_title["旧重复"]
    assert by_title["新事件"]["ids"] == ["n3"] and by_title["新事件"]["reason"] == "新理由"
    assert by_title["新事件"]["repeat"] is False

    former_light = merged(
        _mapped("分量不够", ["n1"], reason="旧理由"),
        _mapped("有效事件", ["n3"], reason="有效理由"))
    assert [item["title"] for item in former_light] == ["分量不够", "有效事件"]
    assert former_light[0]["ids"] == ["n1"] and former_light[1]["ids"] == ["n3"]
    assert former_light[0]["sector"] == "人工智能" and former_light[1]["sector"] == "人工智能"
    assert "weight" not in former_light[0] and "weighty" not in former_light[0]

    both_valid = merged(
        _mapped("最早", ["n1"], reason="最早的理由"),
        _mapped("较晚", ["n3"], reason="较晚的理由"))
    assert [item["title"] for item in both_valid] == ["最早", "较晚"]
    assert both_valid[0]["reason"] == "最早的理由" and both_valid[1]["reason"] == "较晚的理由"
    assert both_valid[0]["ids"] == ["n1"] and both_valid[1]["ids"] == ["n3"]

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
        _event(_mapped("旧重复", ["n1"], repeat=True, reason="旧理由")),
        _event(_mapped("新事件", ["n3"], reason="新理由")))
    assert stored["旧重复"]["state"] == "skipped" and stored["旧重复"]["reason"] == "旧理由"
    assert stored["新事件"]["state"] == "succeeded" and stored["新事件"]["title"] == "新事件"
    assert [item["title"] for item in stored["新事件"]["package"]["messages"]] == ["国家印发项目开工方案"]

    _outcome, stored = archive(
        tmp_path / "light",
        _event(_mapped("分量不够", ["n1"], reason="旧理由")),
        _event(_mapped("有效事件", ["n3"], reason="有效理由")))
    assert set(stored) == {"分量不够", "有效事件"}
    assert stored["分量不够"]["state"] == "succeeded" and stored["分量不够"]["reason"] == "旧理由"
    assert stored["有效事件"]["state"] == "succeeded" and stored["有效事件"]["reason"] == "有效理由"
    assert [item["title"] for item in stored["分量不够"]["package"]["messages"]] == ["工信部印发行业补贴方案"]
    assert [item["title"] for item in stored["有效事件"]["package"]["messages"]] == ["国家印发项目开工方案"]


class _MorningClock:
    def __init__(self, start):
        self.current = start

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current = self.current + timedelta(seconds=seconds)


def test_sector_cutoff_before_mapping_still_publishes_the_main_list(tmp_path, monkeypatch):
    """个股评估时还没到 09:05。板块线开始时已经过点：不发归并，主线照常交付。"""
    path = tmp_path / "v4.db"
    db.init_schema(path)
    _seed_sector_context(path)
    _with_device(path)
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _seconds: None)
    clock = _MorningClock(at("2026-10-13", "08:02"))
    real_sector = run_sector_line

    def late_sector(*args, **kwargs):
        clock.current = at("2026-10-13", "09:06")
        return real_sector(*args, **kwargs)

    monkeypatch.setattr("neckline.morning.report.run_sector_line", late_sector)
    start = at("2026-10-13", "08:02")
    apns = FakeApns()
    result = run_morning(
        path, now=start, config=_config(), clock=clock, collector=lambda _at: {"status": "completed"},
        client_factory=lambda rid: _client(path, _sector_handler(FakeModel()), rid),
        pusher=Pusher(path, apns, clock))
    assert result["state"] == "available" and result["entries"] == 2
    assert "板块线没有完成：收口时刻前没评完" in result["gapSummary"]
    assert "前没能出报" not in (result.get("reason") or "")
    assert apns.sent and apns.sent[-1][1] == "晨报已出"
    with db.read(path) as conn:
        stored = db.get_report(conn, "2026-10-13")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert stored["status"] == "available"
    assert [entry["companyName"] for entry in stored["content"]["entries"]] == ["海特生物", "震裕科技"]
    assert stored["content"]["sectors"]["entries"] == []
    assert "market_daily" not in tables


def test_chunked_keepalive_stops_at_cutoff_without_holding_the_main_list(tmp_path, monkeypatch):
    """08:55 发起板块归并。传输每块把业务时钟拨快 500 秒，600 秒读超时不会响。

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
        if "板块分析员" in system:
            sector_calls.append(1)
            if len(sector_calls) == 1:
                # 两块各 500 秒：08:55 → 09:03:20 → 09:11:40。读超时按块重置，这里壁钟不睡。
                clock.advance(500)
                clock.advance(500)
                if not release.wait(timeout=30):
                    raise AssertionError("板块调用没有在时限内被放行")
                clock.advance(500)
            user = body["messages"][1]["content"]
            return _http({"events": [_event({
                "title": "迟到的事", "ids": _ids(user), "sector": "人工智能",
                "reason": "直接", "repeat": False,
            })], "dropped": []})
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


def test_a_sector_run_does_not_create_market_daily(tmp_path):
    path = _db(tmp_path)
    _concept(path)
    outcome = _run(path, [_msg("a", "工信部印发行业补贴方案", "明确了范围。", _at(16))], _script(), _pool())
    assert outcome.content["entries"]
    with db.read(path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "market_daily" not in names


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
        outcome = _run(path, [_msg("a", "工信部印发行业补贴方案", "明确了范围。", _at(16))], client, clock=clock)
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
            path, [_msg("a", "工信部印发行业补贴方案", "明确了范围。", _at(16))], _script(),
            clock=clock, search=search)
        assert calls == ["2026-10-13T09:04:59+08:00"]
        assert "收口时刻前没评完" in (outcome.gap or "")
    finally:
        release.set()
    assert _wait_until_unknown(path) == ["unknown"]
    time.sleep(0.2)
    assert calls == ["2026-10-13T09:04:59+08:00"]


def test_cutoff_between_the_attempt_check_and_begin_sends_nothing(tmp_path):
    """may_attempt 刚放行就到了收口：begin 拒绝，不发请求，也不留没有回执的请求（复查 N1）。"""
    from neckline.morning.sector_line import _CutoffReceipts, _StillOpen
    from neckline.screening.deepseek import post_json

    path = _db(tmp_path)
    inner = db.DbReceipts(path, "morning-2026-10-13")
    sent: list[int] = []

    def handler(_request):
        sent.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    late = _CutoffReceipts(inner, "09:05", lambda: at("2026-10-13", "09:05"))
    with pytest.raises(_StillOpen):
        post_json(endpoint="https://example.invalid/chat/completions", body={"a": 1}, digest="d" * 64,
                  api_key="k", receipts=late, timeout=httpx.Timeout(5), max_attempts=2,
                  transport=httpx.MockTransport(handler), may_attempt=lambda: True)
    assert sent == [] and inner.lookup("d" * 64) is None
    early = _CutoffReceipts(inner, "09:05", lambda: at("2026-10-13", "09:04"))
    payload, from_receipt, _seconds = post_json(
        endpoint="https://example.invalid/chat/completions", body={"a": 1}, digest="d" * 64, api_key="k",
        receipts=early, timeout=httpx.Timeout(5), max_attempts=2, transport=httpx.MockTransport(handler),
        may_attempt=lambda: True)
    assert sent == [1] and not from_receipt and inner.lookup("d" * 64)["state"] == "succeeded"
