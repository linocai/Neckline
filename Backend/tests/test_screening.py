"""V4 消息筛选：晨报窗口粗筛、回执、按公司分类的解析与按配置选择。全部用合成数据，不外呼。"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from neckline.morning.report import build_content, load_config
from neckline.screening.classify import (CHANNELS, CONTRACT, EVENT_TYPES, PROMPT_GROUPS, CompanyResult, Event,
                                         make_batches, parse_batch, pick_history, render_company, system_prompt)
from neckline.screening.deepseek import ChatError, DeepSeekClient, FileReceipts
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window, as_of, prefilter
from neckline.screening.select import Entry, Selection, select, validate_rules
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


def _v2() -> dict:
    path = Path(__file__).resolve().parents[1] / "neckline" / "config" / "screening-v2.json"
    return json.loads(path.read_text(encoding="utf-8"))["selection"]


def _event(company: str, ids: list[str], **fields) -> Event:
    base = dict(relation="主体", type="业绩预增", direction="利好", first_disclosed=None, fresh="新",
                importance="中", headline="h", reason="r", channel="公司公告", amount_yuan=None)
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


def test_parse_batch_lets_one_message_carry_several_events():
    """贝泰妮那种一份公告里有注册证、回购、分红三件事：同一编号可分属几个事件，但不能同时算提及。"""
    def event(kind):
        return {"ids": ["m1"], "relation": "主体", "type": kind, "direction": "利好", "channel": "公司公告",
                "amountYuan": None, "firstDisclosed": None, "fresh": "新", "importance": "中",
                "headline": kind, "reason": "r"}
    expected = {"300957.SZ": ["m1", "m2"], "300082.SZ": ["m3"]}
    content = json.dumps({"companies": [
        {"company": "300957", "events": [event("产品获批"), event("回购增持"), event("分红")], "mentions": ["m2"]},
        {"company": "300082", "events": [event("分红") | {"ids": ["m3"]}], "mentions": ["m3"]}]}, ensure_ascii=False)
    results = parse_batch(content, expected)
    good, bad = results["300957.SZ"], results["300082.SZ"]
    assert good.error is None and [e.type for e in good.events] == ["产品获批", "回购增持", "分红"]
    assert good.mentions == ["m2"]
    assert bad.error == "消息编号没有对上" and not bad.events


def test_make_batches_respects_company_and_char_limits():
    sizes = {"a": 5, "b": 5, "c": 20, "d": 1}
    assert make_batches(sizes, ["a", "b", "c", "d"], max_companies=2, max_chars=12) == [["a", "b"], ["c"], ["d"]]


def test_parse_batch_requires_every_message_accounted_once():
    expected = {"300953.SZ": ["m1", "m2"], "300082.SZ": ["m3"]}
    content = json.dumps({"companies": [
        {"company": "300953", "events": [{"ids": ["m1", "m2"], "relation": "主体", "type": "业绩预增",
                                          "direction": "利好", "channel": "公司公告", "amountYuan": 145000000,
                                          "firstDisclosed": "2026-10-12 17:05", "fresh": "新",
                                          "importance": "高", "headline": "预增", "reason": "r"}], "mentions": []},
        {"company": "300082", "events": [], "mentions": ["m3", "m3"]}]}, ensure_ascii=False)
    results = parse_batch(content, expected)
    good, bad = results["300953.SZ"], results["300082.SZ"]
    assert good.error is None and good.events[0].ids == ["m1", "m2"] and good.events[0].first_disclosed == "2026-10-12 17:05"
    assert good.events[0].channel == "公司公告" and good.events[0].amount_yuan == 145000000
    assert bad.error == "消息编号没有对上" and not bad.events
    missing = parse_batch(json.dumps({"companies": []}), expected)
    assert all(r.error == "输出里没有这家公司" for r in missing.values())
    assert all(r.error for r in parse_batch("不是 JSON", expected).values())
    invalid = json.dumps({"companies": [{"company": "300082", "events": [{"ids": ["m3"], "relation": "主体",
                          "type": "没这个", "direction": "利好", "channel": "公司公告", "amountYuan": None,
                          "fresh": "新", "importance": "低"}], "mentions": []}]})
    assert parse_batch(invalid, {"300082.SZ": ["m3"]})["300082.SZ"].error == "事件字段无效"


def _company_json(amount, channel="公司公告", drop_amount=False) -> str:
    item = {"ids": ["m1"], "relation": "主体", "type": "业绩预增", "direction": "利好", "channel": channel,
            "amountYuan": amount, "firstDisclosed": None, "fresh": "新", "importance": "高",
            "headline": "h", "reason": "r"}
    if drop_amount:
        del item["amountYuan"]
    return json.dumps({"companies": [{"company": "300953", "events": [item], "mentions": []}]}, ensure_ascii=False)


def test_parse_batch_rejects_bad_channel_and_amount():
    expected = {"300953.SZ": ["m1"]}
    assert parse_batch(_company_json(None), expected)["300953.SZ"].events[0].amount_yuan is None
    assert parse_batch(_company_json(12000000.0), expected)["300953.SZ"].events[0].amount_yuan == 12000000
    assert parse_batch(_company_json(1.5), expected)["300953.SZ"].events[0].amount_yuan == 1.5
    for payload in (_company_json("1000万"), _company_json(True), _company_json(None, channel="微博"),
                    _company_json(None, drop_amount=True)):
        result = parse_batch(payload, expected)["300953.SZ"]
        assert result.error == "事件字段无效" and not result.events
    assert set(CHANNELS) == {"公司公告", "媒体报道", "调研纪要", "互动平台", "第三方综述"}


def test_system_prompt_lists_every_event_type():
    prompt = system_prompt()
    assert CONTRACT == "screening-classify-v3"
    assert all(name in prompt for name in EVENT_TYPES)
    assert "首次" in prompt and "业绩落地" in prompt and "互动平台" in prompt and "amountYuan" in prompt
    body = prompt.split("只能从下列种类里选一个。\n", 1)[1].split("\n- firstDisclosed：", 1)[0]
    groups = body.strip().split("\n\n")
    assert len(groups) == len(PROMPT_GROUPS)
    for chunk, (_, names) in zip(groups, PROMPT_GROUPS):
        bullets = [line[4:].split("：", 1)[0] for line in chunk.splitlines() if line.startswith("  - ")]
        assert bullets == list(names)


def test_select_collapses_company_and_leaves_review_empty():
    by_id = {"m1": _message(1, 16), "m2": _message(2, 18), "m3": _message(3, 17)}
    results = {"300953.SZ": CompanyResult("300953.SZ", ["m1", "m2", "m3"], events=[
        _event("300953.SZ", ["m2"], importance="低", type="分红"),
        _event("300953.SZ", ["m1", "m3"], importance="高")], mentions=[])}
    result = select(by_id, results, _v2())
    main = result.sections["main"]
    assert len(main) == 1 and main[0].primary.importance == "高" and [o.type for o in main[0].others] == ["分红"]
    assert [m.document_id for m in main[0].messages] == ["doc_1", "doc_3"]
    assert [len(result.sections[n]) for n in ("review_types", "review_relation", "review_fresh", "held_old")] == [0, 0, 0, 0]


def test_v2_routes_examples_the_user_judged():
    specs = [
        ("300001.SZ", {}, "300001.SZ"),
        ("300002.SZ", dict(type="业绩落地"), None),
        ("300003.SZ", dict(type="临床进展"), None),
        ("300004.SZ", dict(type="产品发布"), None),
        ("300005.SZ", dict(type="小额订单"), None),
        ("300006.SZ", dict(type="中标候选"), None),
        ("300007.SZ", dict(type="出售资产", direction="利空"), "300007.SZ"),
        ("300008.SZ", dict(type="股份转让", direction="中性"), "300008.SZ"),
        ("300009.SZ", dict(channel="互动平台"), None),
        ("300010.SZ", dict(type="热点合作", channel="第三方综述"), None),
        ("300011.SZ", dict(relation="受益方"), None),
        ("300012.SZ", dict(fresh="不确定"), None),
        ("300013.SZ", dict(type="热点合作"), "300013.SZ"),
        ("300014.SZ", dict(type="控制权变更"), "300014.SZ"),
        ("300015.SZ", dict(type="订单中标", amount_yuan=7_460_000), None),
        ("300016.SZ", dict(type="订单中标"), "300016.SZ"),
        ("300018.SZ", dict(type="订单中标", amount_yuan=10_000_000), "300018.SZ"),
        ("300019.SZ", dict(fresh="旧"), None),
        ("300020.SZ", dict(type="大额采购", direction="利空"), "300020.SZ"),
        ("300021.SZ", dict(type="产品获批", channel="调研纪要"), "300021.SZ"),
    ]
    by_id = {f"m{i}": _message(i, 10) for i in range(len(specs))}
    results = {code: CompanyResult(code, [f"m{i}"], events=[_event(code, [f"m{i}"], **fields)], mentions=[])
               for i, (code, fields, _) in enumerate(specs)}
    results["300017.SZ"] = CompanyResult("300017.SZ", ["gap"], error="输出里没有这家公司")
    result = select(by_id, results, _v2())
    assert [e.company for e in result.main] == [code for code, _, keep in specs if keep]
    assert [len(result.sections[n]) for n in ("review_types", "review_relation", "review_fresh", "held_old")] == [0, 0, 0, 0]
    assert result.reasons["种类不收：业绩落地"] == 1
    assert result.reasons["种类不收：临床进展"] == 1
    assert result.reasons["种类不收：产品发布"] == 1
    assert result.reasons["种类不收：小额订单"] == 1
    assert result.reasons["种类不收：中标候选"] == 1
    assert result.reasons["渠道：互动平台"] == 1
    assert result.reasons["渠道：第三方综述无金额"] == 1
    assert result.reasons["关系：受益方"] == 1
    assert result.reasons["时效：不确定"] == 1
    assert result.reasons["时效：旧"] == 1
    assert result.reasons["金额：订单中标低于门槛"] == 1
    assert [g.company for g in result.gaps] == ["300017.SZ"]


def test_select_applies_threshold_and_cap():
    by_id = {"m1": _message(1, 16), "m2": _message(2, 17), "m3": _message(3, 18)}
    results = {code: CompanyResult(code, [mid], events=[_event(code, [mid], importance=level)], mentions=[])
               for code, mid, level in (("300001.SZ", "m1", "低"), ("300002.SZ", "m2", "中"), ("300003.SZ", "m3", "高"))}
    rules = _v2()
    rules["importanceMinimum"] = "中"
    rules["maxItems"] = 1
    capped = select(by_id, results, rules)
    assert [e.primary.importance for e in capped.main] == ["高"]


def _broken(mutate) -> dict:
    rules = _v2()
    mutate(rules)
    return rules


@pytest.mark.parametrize("rules, message", [
    (_broken(lambda r: r["kinds"]["exclude"].append("没这个")), "未知事件类型"),
    (_broken(lambda r: r["kinds"]["judge"].append(r["kinds"]["include"][0])), "多个分组"),
    (_broken(lambda r: r["kinds"]["exclude"].pop()), "遗漏"),
    (_broken(lambda r: r["freshness"].__setitem__("include", ["新", "新"])), "freshness"),
    (_broken(lambda r: r["relation"].__setitem__("review", ["受益方"])), "relation"),
    (_broken(lambda r: r["channels"].__setitem__("exclude", ["微博"])), "channels"),
    (_broken(lambda r: r["channels"]["requireAmount"].append("互动平台")), "同一渠道"),
    (_broken(lambda r: r["minimumAmountYuan"].__setitem__("没这个", 1)), "未知事件类型"),
    (_broken(lambda r: r.__delitem__("maxItems")), "maxItems"),
    (_broken(lambda r: r.__setitem__("importanceMinimum", "极高")), "importanceMinimum"),
])
def test_validate_rules_rejects_incomplete_config(rules, message):
    with pytest.raises(ValueError, match=message):
        validate_rules(rules)


def test_v2_config_groups_match_the_prompt_and_cover_every_kind():
    rules = _v2()
    validate_rules(rules)
    assert [set(names) for _, names in PROMPT_GROUPS] == [set(rules["kinds"][key]) for key in ("include", "judge", "exclude")]
    assert rules["relation"]["include"] == ["主体"] and rules["freshness"]["include"] == ["新"]
    assert rules["direction"]["include"] == ["利好"]
    assert rules["channels"]["exclude"] == ["互动平台"] and rules["channels"]["requireAmount"] == ["第三方综述"]
    assert rules["minimumAmountYuan"] == {"订单中标": 10000000}
    assert rules["importanceMinimum"] is None and rules["maxItems"] is None


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
    with pytest.raises(ChatError, match="凭据缺失"):
        offline.complete(prompt, {"max_tokens": 11})


def test_report_v2_keeps_review_keys_empty():
    config = load_config()
    assert config.rules["configVersion"] == "screening-v2"
    assert config.rules["classification"]["contract"] == CONTRACT
    pool = CompanyPool([Company("300001.SZ", "甲公司", ("甲公司",))])
    by_id = {"m1": _message(1, 16)}
    results = {"300001.SZ": CompanyResult("300001.SZ", ["m1"], events=[_event("300001.SZ", ["m1"])], mentions=[])}
    selection = select(by_id, results, config.rules["selection"])
    content = build_content(report_id="morning-2026-10-13", day=date(2026, 10, 13), window=WINDOW,
                            selection=selection, pool=pool, config_version=config.rules["configVersion"],
                            gap=None, generated_at=WINDOW.collected_by, stats={})
    assert content["configVersion"] == "screening-v2"
    assert list(content["review"]) == ["types", "relation", "fresh", "old"]
    assert all(content["review"][name] == [] for name in content["review"])
    entry = content["entries"][0]
    assert set(entry) == {"rank", "companyCode", "companyName", "type", "importance", "relation", "fresh",
                          "headline", "reason", "firstDisclosed", "sources", "others"}
    assert set(entry["sources"][0]) == {"documentId", "sourceLabel", "publishedAt", "title", "url"}
    assert entry["importance"] == "中"


def test_digest_source_title_uses_the_company_section():
    pool = CompanyPool([
        Company("300683.SZ", "海特生物", ("海特生物",)),
        Company("605189.SH", "富春染织", ("富春染织",)),
        Company("301235.SZ", "华康洁净", ("华康洁净",)),
        Company("300901.SZ", "中胤时尚", ("中胤时尚",)),
        Company("300182.SZ", "捷成股份", ("捷成股份",)),
        Company("300052.SZ", "中青宝", ("中青宝",)),
        Company("300953.SZ", "震裕科技", ("震裕科技",)),
        Company("300082.SZ", "奥克股份", ("奥克股份",)),
    ])
    digest_title = "公告精选︱富春染织：预计2026年前三季度净利润同比增加1347.69%到1519.01%"
    digest_text = "\n".join([
        "【热点】",
        "海特生物(300683.SZ)：与Visara签署HT006.2.2滴眼液项目授权许可协议",
        "海特生物公告，公司与Visara签署协议。",
        "富春染织(605189.SH)：预计前三季度净利润同比增加",
        "华康洁净(301235.SZ)：中标产线项目",
    ])
    digest = _source(1, digest_title, digest_text, {
        "300683.SZ": ["海特生物"], "605189.SH": ["富春染织"], "301235.SZ": ["华康洁净"], "300901.SZ": ["中胤时尚"]})
    flash = _source(2, "海特生物：与Visara签署独家合作与授权许可协议", "海特生物公告首付款。", {"300683.SZ": ["海特生物"]})
    plain = _source(3, "震裕科技：与奥克股份合作", "震裕科技与奥克股份签署合作协议，没有分节。",
                    {"300953.SZ": ["震裕科技"], "300082.SZ": ["奥克股份"]})
    bracket = _source(4, "专栏｜两家公司", "【捷成股份：与吉视传媒战略合作AI微短剧】\n正文\n【中青宝：3款游戏获版号】\n正文",
                      {"300182.SZ": ["捷成股份"], "300052.SZ": ["中青宝"]})
    selection = Selection(sections={
        "main": [Entry(company="300683.SZ", primary=_event("300683.SZ", ["m1"]), messages=[flash, digest]),
                 Entry(company="300901.SZ", primary=_event("300901.SZ", ["m1"]), messages=[digest]),
                 Entry(company="300953.SZ", primary=_event("300953.SZ", ["m1"]), messages=[plain]),
                 Entry(company="300182.SZ", primary=_event("300182.SZ", ["m1"]), messages=[bracket])],
        "review_types": [], "review_relation": [], "review_fresh": [], "held_old": [],
    }, reasons={}, gaps=[])
    content = build_content(report_id="morning-2026-10-14", day=date(2026, 10, 14), window=WINDOW,
                            selection=selection, pool=pool, config_version="screening-v2", gap=None,
                            generated_at=WINDOW.collected_by, stats={})
    by_name = {entry["companyName"]: entry for entry in content["entries"]}
    assert by_name["海特生物"]["sources"][0]["title"] == flash.title
    assert by_name["海特生物"]["sources"][1]["title"] == "海特生物(300683.SZ)：与Visara签署HT006.2.2滴眼液项目授权许可协议"
    assert by_name["中胤时尚"]["sources"][0]["title"] == digest_title + "（汇总稿）"
    assert by_name["震裕科技"]["sources"][0]["title"] == plain.title
    assert by_name["捷成股份"]["sources"][0]["title"] == "【捷成股份：与吉视传媒战略合作AI微短剧】"
    assert digest.title == digest_title and "（汇总稿）" not in digest.text


def test_digest_title_when_only_one_pool_company_is_named():
    """第一节是池外主板公司、池内只点名一家时，也要显示这家公司自己的那一节。"""
    pool = CompanyPool([Company("300683.SZ", "海特生物", ("海特生物",))])
    digest_title = "公告精选︱富春染织：预计2026年前三季度净利润同比增加1347.69%到1519.01%"
    digest = _source(1, digest_title, "\n".join([
        "富春染织(605189.SH)：预计前三季度净利润同比增加",
        "海特生物(300683.SZ)：与Visara签署HT006.2.2滴眼液项目授权许可协议",
        "海特生物公告，公司与Visara签署协议。",
    ]), {"300683.SZ": ["海特生物"]})
    single = _source(2, "海特生物：签署授权协议", "海特生物公告首付款。", {"300683.SZ": ["海特生物"]})
    selection = Selection(sections={
        "main": [Entry(company="300683.SZ", primary=_event("300683.SZ", ["m1"]), messages=[digest, single])],
        "review_types": [], "review_relation": [], "review_fresh": [], "held_old": [],
    }, reasons={}, gaps=[])
    content = build_content(report_id="morning-2026-10-14", day=date(2026, 10, 14), window=WINDOW,
                            selection=selection, pool=pool, config_version="screening-v2", gap=None,
                            generated_at=WINDOW.collected_by, stats={})
    titles = [source["title"] for source in content["entries"][0]["sources"]]
    assert titles == ["海特生物(300683.SZ)：与Visara签署HT006.2.2滴眼液项目授权许可协议", single.title]


def _source(i: int, title: str, text: str, companies: dict) -> Message:
    return Message(document_id=f"doc_{i}", revision=1, source_key="tushare-major-news", source_label="新浪财经",
                   url=None, title=title, text=text, published_at=WINDOW.start, first_seen_at=WINDOW.start,
                   companies=companies)


def test_compare_verdicts_script_on_fake_replay(tmp_path):
    period = tmp_path / "run-a"
    period.mkdir()
    def event(company, headline, **fields):
        raw = {"company": company, "ids": [f"m-{company}-{headline}"], "relation": "主体", "direction": "利好",
               "first_disclosed": None, "fresh": "新", "importance": "中", "headline": headline, "reason": "公告",
               "channel": "公司公告", "amount_yuan": None, "name": company,
               "messages": [{"publishedAt": "2026-10-12T10:00:00+08:00", "title": headline, "source": "金十快讯"}]}
        raw.update(fields)
        return raw
    events = [
        event("300001.SZ", "前三季度净利预增", type="业绩预增"),
        event("300002.SZ", "临床首例入组", type="临床进展"),
        event("300003.SZ", "挂牌出售子公司", type="出售资产", direction="利空"),
        event("300004.SZ", "与吉视传媒合作AI微短剧", type="热点合作", fresh="旧"),
        event("300005.SZ", "中标五千万订单", type="订单中标", amountYuan=50_000_000),
        event("300006.SZ", "回购实施完毕", type="回购进展", ids=["m-a"]),
        event("300006.SZ", "游戏获版号", type="产品获批", ids=["m-b"]),
        event("300007.SZ", "调整定增方案", type="上市融资", fresh="旧"),
    ]
    del events[4]["amount_yuan"]
    (period / "events.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    (period / "stats.json").write_text(json.dumps(
        {"window": ["2026-10-12T15:00:00+08:00", "2026-10-13T08:00:00+08:00"]}), encoding="utf-8")
    verdicts = [
        {"batch": 1, "no": 1, "window": "2026-10-13", "run": "m1013-r1", "company": "300001.SZ", "name": "甲",
         "event": "净利预增", "user": "进", "typeOnly": False},
        {"batch": 1, "no": 2, "window": "2026-10-13", "run": "m1013-r1", "company": "300002.SZ", "name": "乙",
         "event": "临床入组", "user": "不进", "typeOnly": False},
        {"batch": 1, "no": 3, "window": "2026-10-13", "run": "m1013-r1", "company": "300003.SZ", "name": "丙",
         "event": "出售子公司", "user": "进", "typeOnly": False},
        {"batch": 5, "no": 8, "window": "2026-10-13", "run": "m1013-r1", "company": "300004.SZ", "name": "丁",
         "event": "微短剧合作", "user": "进", "typeOnly": True},
        {"batch": 2, "no": 2, "window": "2026-10-13", "run": "m1013-r1", "company": "300005.SZ", "name": "戊",
         "event": "小订单", "user": "不进", "typeOnly": False},
        {"batch": 2, "no": 3, "window": "2026-10-13", "run": "m1013-r1", "company": "300006.SZ", "name": "己",
         "event": "子丑寅卯辰巳午未", "user": "进", "typeOnly": False},
        {"batch": 5, "no": 3, "window": "2026-10-13", "run": "m1013-r1", "company": "300007.SZ", "name": "庚",
         "event": "调整定增", "user": "不进", "typeOnly": True},
        {"batch": 9, "no": 1, "window": "1999-01-01", "run": "missing", "company": "300008.SZ", "name": "辛",
         "event": "没有这期", "user": "进", "typeOnly": False},
    ]
    verdict_path = tmp_path / "verdicts.json"
    verdict_path.write_text(json.dumps(verdicts, ensure_ascii=False), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / ".work" / "p1-offline" / "compare_verdicts.py"
    config = Path(__file__).resolve().parents[1] / "neckline" / "config" / "screening-v2.json"
    completed = subprocess.run([sys.executable, str(script), "--replay", str(tmp_path), "--verdicts", str(verdict_path),
                                "--config", str(config)], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert "总一致率：3/6（50.0%）" in completed.stdout
    assert "typeOnly：1/2（50.0%）" in completed.stdout
    body = completed.stdout.split("不一致：", 1)[1]
    assert "甲" not in body and "丁" not in body
    assert "用户不进｜v2 进｜种类 订单中标｜渠道 公司公告｜金额 50000000｜时效 新｜理由 公告" in body
    assert "用户不进｜v2 种类该收｜种类 上市融资｜渠道 公司公告｜金额 null｜时效 旧｜理由 公告" in body
    assert body.count("v2 未找到") == 2
