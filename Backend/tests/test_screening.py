"""V4 消息筛选：晨报窗口粗筛、回执、按公司分类的解析与按配置选择。全部用合成数据，不外呼。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from neckline.screening.classify import (EVENT_TYPES, CompanyResult, Event, make_batches, parse_batch,
                                         pick_history, render_company, system_prompt)
from neckline.screening.deepseek import ChatError, DeepSeekClient, FileReceipts
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window, as_of, prefilter
from neckline.screening.select import select, validate_rules
from neckline.screening.text import excerpt_for, plain_text

CST = timezone(timedelta(hours=8))
WINDOW = Window(start=datetime(2026, 10, 12, 15, 0, tzinfo=CST), end=datetime(2026, 10, 13, 8, 0, tzinfo=CST),
                collected_by=datetime(2026, 10, 13, 8, 2, tzinfo=CST))
SETTINGS = {"profileChars": 20, "excerpt": {"lead": 50, "window": 30, "limit": 300, "minChars": 40, "blockMaxChars": 4000},
            "history": {"lookbackDays": 14, "maxItems": 8, "window": 20, "snippetChars": 60}}


def _pool() -> CompanyPool:
    return CompanyPool([Company("300953.SZ", "震裕科技", ("震裕科技", "宁波震裕科技股份有限公司")),
                        Company("300082.SZ", "奥克股份", ("奥克股份",))])


def _doc(i: int, text: str, *, published: str, seen: str = "2026-10-13T08:01:00+08:00", title: str | None = None,
         source: str = "jin10-flash", revision: int = 1, fetched: str | None = None) -> dict:
    return {"kind": "doc", "documentId": f"doc_{i}", "revision": revision, "sourceKey": source, "url": None,
            "firstSeenAt": seen, "fetchedAt": fetched or seen, "publishedAt": published,
            "text": text, "excerpt": None, "meta": {"title": title}}


def _rules(**overrides) -> dict:
    rules = {"relation": {"include": ["主体"], "review": ["受益方"]}, "direction": {"include": ["利好"]},
             "freshness": {"include": ["新"], "review": ["不确定"]},
             "types": {"include": ["订单合同", "业绩向好"], "review": ["回购增持"], "exclude": ["程序性事项"]},
             "importanceMinimum": None, "maxItems": None}
    rules.update(overrides)
    return rules


def _event(company: str, ids: list[str], **fields) -> Event:
    base = dict(relation="主体", type="业绩向好", direction="利好", first_disclosed=None, fresh="新",
                importance="中", headline="h", reason="r")
    base.update(fields)
    return Event(company=company, ids=ids, **base)


def _message(i: int, hour: int, companies: dict | None = None) -> Message:
    ts = datetime(2026, 10, 12, hour, 0, tzinfo=CST)
    return Message(document_id=f"doc_{i}", revision=1, source_key="jin10-flash", source_label="金十快讯",
                   url=None, title="", text="t", published_at=ts, first_seen_at=ts, companies=companies or {})


def test_plain_text_strips_markup_and_keeps_paragraphs():
    raw = "<div><script>x=1</script><p>第一段&nbsp;内容</p><p>第二段</p><iframe>广告</iframe></div>"
    assert plain_text(raw) == "第一段 内容\n第二段"
    assert plain_text(None) == ""


def test_excerpt_keeps_lead_and_context_within_limit():
    text = "导语" * 50 + "无关" * 500 + "震裕科技预增" + "尾巴" * 500
    out = excerpt_for(text, ["震裕科技"], lead=20, window=10, limit=80)
    assert out.startswith("导语") and "震裕科技" in out and len(out) <= 80 + len(" …… ")


def test_pool_matches_names_aliases_and_pool_codes_only():
    hits = _pool().match("宁波震裕科技股份有限公司公告；奥克股份(300082)；300750 不在池内")
    assert set(hits) == {"300953.SZ", "300082.SZ"}
    assert set(_pool().subset(["300082.SZ"]).match("震裕科技与奥克股份")) == {"300082.SZ"}


def test_strict_short_names_need_company_news_format():
    pool = CompanyPool([Company("300785.SZ", "值得买", ("值得买", "北京值得买科技股份有限公司")),
                        Company("300953.SZ", "震裕科技", ("震裕科技",))], strict_names=["值得买"])
    assert pool.match("哪款电视值得买（300785）") == {}
    assert set(pool.match("值得买：前三季度净利增长")) == {"300785.SZ"}
    assert set(pool.match("【值得买发布公告】")) == {"300785.SZ"}
    assert set(pool.match("北京值得买科技股份有限公司公告")) == {"300785.SZ"}
    with pytest.raises(ValueError, match="不在公司池"):
        CompanyPool([], strict_names=["值得买"])


def test_as_of_takes_latest_revision_fetched_before_cutoff():
    docs = [_doc(1, "v1", published="2026-10-12T16:00:00+08:00", fetched="2026-10-12T20:01:00+08:00"),
            _doc(1, "v2", published="2026-10-12T16:00:00+08:00", revision=2, fetched="2026-10-13T09:00:00+08:00")]
    assert [d["text"] for d in as_of(docs, WINDOW.collected_by)] == ["v1"]


def test_prefilter_uses_morning_window_and_collects_history():
    docs = [
        _doc(1, "【震裕科技预增】前三季度净利预增", published="2026-10-12T17:05:00+08:00"),
        _doc(2, "【震裕科技预增】前三季度净利预增", published="2026-10-12T17:06:00+08:00"),       # 精确重复
        _doc(3, "美股收涨", published="2026-10-12T22:00:00+08:00"),                               # 未点名
        _doc(4, "震裕科技盘中公告", published="2026-10-12T10:00:00+08:00"),                       # 窗口前：核对
        _doc(5, "奥克股份盘中公告", published="2026-10-12T11:00:00+08:00"),                       # 窗口前但不是候选
        _doc(6, "震裕科技晚到", published="2026-10-13T07:30:00+08:00", seen="2026-10-13T20:00:00+08:00"),
        _doc(7, "奥克股份开盘后", published="2026-10-13T09:00:00+08:00"),                         # 窗口后
    ]
    result = prefilter(docs, _pool(), WINDOW, history_days=14)
    assert (result.input_count, result.duplicate_count, result.unmatched_count) == (3, 1, 1)
    assert [m.document_id for m in result.messages] == ["doc_1"] and result.messages[0].duplicates == ["doc_2#1"]
    assert [m.document_id for m in result.history] == ["doc_4"]


def test_render_company_marks_history_for_checking_only():
    pool = _pool()
    m = _message(1, 17, {"300953.SZ": ["震裕科技"]})
    m.text, m.title = "震裕科技预计前三季度净利增长", "震裕科技预增"
    h = _message(2, 10, {"300953.SZ": ["震裕科技"]})
    h.text = "震裕科技此前已披露"
    block = render_company("300953.SZ", pool, "精密模具", [("m0001", m)], [("h0001", h)], SETTINGS)
    assert "[m0001] 金十快讯｜10-12 17:00｜震裕科技预增" in block and "[h0001] 10-12 10:00" in block
    assert block.index("本期消息") < block.index("窗口开始前的相关消息")


def test_long_company_block_shrinks_to_budget():
    pool = _pool()
    messages = []
    for i in range(40):
        m = _message(i, 16, {"300953.SZ": ["震裕科技"]})
        m.text = "震裕科技" + "行情" * 400
        messages.append((f"m{i:04d}", m))
    settings = {**SETTINGS, "excerpt": {**SETTINGS["excerpt"], "limit": 1500, "blockMaxChars": 6000}}
    block = render_company("300953.SZ", pool, None, messages, [], settings)
    assert len(block) <= 6000 and block.count("[m00") == 40


def test_pick_history_prefers_titles_naming_the_company():
    pool = _pool()
    items = []
    for i, (hour, title) in enumerate([(8, "震裕科技签约"), (9, "收评"), (10, "盘面"), (11, "午评")]):
        m = _message(i, hour, {"300953.SZ": ["震裕科技"]})
        m.title = title
        items.append((f"h{i}", m))
    picked = pick_history("300953.SZ", pool, items, 2)
    assert [m.title for _, m in picked] == ["震裕科技签约", "午评"]


def test_make_batches_respects_company_and_char_limits():
    sizes = {"a": 5, "b": 5, "c": 20, "d": 1}
    assert make_batches(sizes, ["a", "b", "c", "d"], max_companies=2, max_chars=12) == [["a", "b"], ["c"], ["d"]]


def test_parse_batch_requires_every_message_accounted_once():
    expected = {"300953.SZ": ["m1", "m2"], "300082.SZ": ["m3"]}
    content = json.dumps({"companies": [
        {"company": "300953", "events": [{"ids": ["m1", "m2"], "relation": "主体", "type": "业绩向好",
                                          "direction": "利好", "firstDisclosed": "2026-10-12 17:05", "fresh": "新",
                                          "importance": "高", "headline": "预增", "reason": "r"}], "mentions": []},
        {"company": "300082", "events": [], "mentions": ["m3", "m3"]}]}, ensure_ascii=False)
    results = parse_batch(content, expected)
    good, bad = results["300953.SZ"], results["300082.SZ"]
    assert good.error is None and good.events[0].ids == ["m1", "m2"] and good.events[0].first_disclosed == "2026-10-12 17:05"
    assert bad.error == "消息编号没有恰好各出现一次" and not bad.events
    missing = parse_batch(json.dumps({"companies": []}), expected)
    assert all(r.error == "输出里没有这家公司" for r in missing.values())
    assert all(r.error for r in parse_batch("不是 JSON", expected).values())
    invalid = json.dumps({"companies": [{"company": "300082", "events": [{"ids": ["m3"], "relation": "主体",
                          "type": "没这个", "direction": "利好", "fresh": "新", "importance": "低"}], "mentions": []}]})
    assert parse_batch(invalid, {"300082.SZ": ["m3"]})["300082.SZ"].error == "事件字段无效"


def test_system_prompt_lists_every_event_type():
    prompt = system_prompt()
    assert all(name in prompt for name in EVENT_TYPES)


def test_select_one_entry_per_company_and_routes_by_rules():
    by_id = {f"m{i}": _message(i, 15 + i) for i in range(1, 9)}
    results = {
        "300953.SZ": CompanyResult("300953.SZ", ["m1", "m2", "m3"], events=[
            _event("300953.SZ", ["m2"], importance="低", type="订单合同"),
            _event("300953.SZ", ["m1", "m3"], importance="高")], mentions=[]),
        "300082.SZ": CompanyResult("300082.SZ", ["m4", "m5", "m6", "m7", "m8"], events=[
            _event("300082.SZ", ["m4"], type="回购增持"),
            _event("300082.SZ", ["m5"], relation="受益方", type="订单合同"),
            _event("300082.SZ", ["m6"], fresh="旧"),
            _event("300082.SZ", ["m7"], fresh="不确定"),
            _event("300082.SZ", ["m8"], type="程序性事项")], mentions=[]),
        "300001.SZ": CompanyResult("300001.SZ", ["m9"], error="输出里没有这家公司"),
    }
    result = select(by_id, results, _rules())
    main = result.sections["main"]
    assert len(main) == 1 and main[0].primary.importance == "高" and [o.type for o in main[0].others] == ["订单合同"]
    assert [m.document_id for m in main[0].messages] == ["doc_1", "doc_3"]
    assert [len(result.sections[n]) for n in ("review_types", "review_relation", "review_fresh", "held_old")] == [1, 1, 1, 1]
    assert result.reasons["类型不收：程序性事项"] == 1 and [g.company for g in result.gaps] == ["300001.SZ"]


def test_select_applies_threshold_and_cap():
    by_id = {"m1": _message(1, 16), "m2": _message(2, 17), "m3": _message(3, 18)}
    results = {code: CompanyResult(code, [mid], events=[_event(code, [mid], importance=level)], mentions=[])
               for code, mid, level in (("300001.SZ", "m1", "低"), ("300002.SZ", "m2", "中"), ("300003.SZ", "m3", "高"))}
    capped = select(by_id, results, _rules(importanceMinimum="中", maxItems=1))
    assert [e.primary.importance for e in capped.main] == ["高"]


@pytest.mark.parametrize("rules, message", [
    (_rules(types={"include": ["没这个"], "review": [], "exclude": []}), "未知事件类型"),
    (_rules(types={"include": ["订单合同"], "review": ["订单合同"], "exclude": []}), "多个分组"),
    (_rules(freshness={"include": ["新"], "review": ["新"]}), "freshness"),
    ({k: v for k, v in _rules().items() if k != "maxItems"}, "maxItems"),
])
def test_validate_rules_rejects_incomplete_config(rules, message):
    with pytest.raises(ValueError, match=message):
        validate_rules(rules)


def test_identical_request_is_billed_once(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 3}})

    client = DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
                            receipts=FileReceipts(tmp_path), timeout_seconds=5, max_attempts=1,
                            transport=httpx.MockTransport(handler))
    prompt = [{"role": "user", "content": "x"}]
    first = client.complete(prompt, {"max_tokens": 10})
    second = client.complete(prompt, {"max_tokens": 10})
    assert len(calls) == 1 and not first.from_receipt and second.from_receipt
    assert second.request_sha256 == first.request_sha256 and second.usage == {"prompt_tokens": 3}
    offline = DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key=None,
                             receipts=FileReceipts(tmp_path), timeout_seconds=5, max_attempts=1)
    assert offline.complete(prompt, {"max_tokens": 10}).from_receipt
    with pytest.raises(ChatError, match="未提供"):
        offline.complete(prompt, {"max_tokens": 11})
