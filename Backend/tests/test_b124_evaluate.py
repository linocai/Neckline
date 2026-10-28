"""B124：三级评估。模型走假 transport，不联网。"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest

from neckline.evaluation.evaluate import Evaluation, evaluate_candidates, parse_output
from neckline.evaluation.prompt import CONTRACT, system_prompt, user_prompt
from neckline.morning import db
from neckline.morning.report import Pusher, load_config, run_morning
from neckline.morning.schedule import CST
from neckline.screening.deepseek import DeepSeekClient, canonical
from tests.test_morning import FakeApns, FakeModel, _client, _config, _seed_window, _with_device, at

REPORT = "morning-2026-10-13"
WHEN = datetime(2026, 10, 13, 8, 2, tzinfo=CST)
OPTIONS = {
    "max_tokens": 16384,
    "thinking": {"type": "enabled"},
    "reasoning_effort": "high",
    "response_format": {"type": "json_object"},
}


def _settings(**overrides: object) -> dict:
    base = {
        "contract": CONTRACT, "cutoff": "09:05", "concurrency": 2, "modelOptions": OPTIONS, "search": None,
    }
    base.update(overrides)
    return base


def _package(code: str = "300001", name: str = "甲公司", summary: str = "做测试") -> dict:
    return {
        "report": {
            "tradeDate": "2026-10-13",
            "windowStart": "2026-10-12T15:00:00+08:00",
            "windowEnd": "2026-10-13T08:00:00+08:00",
        },
        "company": {"code": code, "name": name, "summary": summary},
        "events": {
            "primary": {
                "type": "订单中标", "direction": "利好", "headline": "签了订单", "reason": "金额大",
                "firstDisclosed": None, "amountYuan": None, "messages": ["m1"],
            },
            "others": [{
                "type": "收购投资", "direction": "不明", "headline": "拟收购", "reason": "还在谈",
                "firstDisclosed": "2026-10-12", "amountYuan": 10000000, "messages": ["m2"],
            }],
        },
        "messages": [
            {"id": "m1"},
            {"id": "m2"},
        ],
        "original": {
            "documents": [{
                "id": "a1", "source": "巨潮公告", "publishedAt": "2026-10-12T16:30:00+08:00",
                "title": "关于订单的公告", "truncated": True, "chars": 20000, "pages": 12, "text": None,
                "headings": ["一、概述", "二、订单"], "opening": "公司签署订单。", "error": None,
            }],
            "media": [{
                "message": "m2", "source": "金十快讯", "publishedAt": "2026-10-12T17:00:00+08:00",
                "title": "拟收购", "text": "还在谈。", "chars": 4, "truncated": False,
            }],
        },
        "financials": {
            "reportPeriod": "2026-06-30", "annDate": "2026-08-20", "revenue": 100.5, "netProfit": 20,
            "deducted": None, "totalMv": 125000, "peTtm": 18.5, "marketDate": "2026-10-12",
            "missing": ["扣非净利"],
        },
        "price": {
            "tradeDate": "2026-10-21", "changePercent": 1.2,
            "previous20": {
                "changePercent": 8, "averageAmountYuan": 1000000, "days": 20, "missingDays": 0, "note": None,
            },
            "ma5": "上方", "ma10": "持平", "ma20": "下方", "maNote": None,
        },
        "earnings": {
            "kind": "periodic", "period": "2026年三季报", "message": "m1",
            "yoy": {
                "revenue": {"value": 12.5, "source": "pdf", "missing": None},
                "netProfit": {"value": None, "source": None, "missing": "未能读出同比"},
                "deducted": {"value": 3, "source": "pdf", "missing": None},
            },
            "netMinusDeducted": {"value": 1, "source": "计算", "missing": None, "note": "归母减扣非"},
        },
        "gaps": ["行情 income：credential_missing"],
        "search": {"enabled": False, "results": [{"title": "不应出现"}]},
    }


def _candidate(code: str) -> SimpleNamespace:
    return SimpleNamespace(company=f"{code}.SZ")


def _answer(verdict: str = "推", *, value_pass: bool = True, priced_pass: bool = True,
            views: list | None = None, title: str = "签下订单") -> dict:
    return {
        "title": title, "verdict": verdict,
        "conclusion": "值得跟踪" if verdict == "推" else "先不推",
        "questions": {
            "value": {"pass": value_pass, "text": "订单占营收不低"},
            "priced": {"pass": priced_pass, "text": "还没怎么涨"},
            "money": {"text": "有新增量"},
            "price": {"text": "明显高开不追"},
        },
        "risk": "订单可能推迟",
        "views": [{"by": "媒体", "name": "金十", "text": "公司签了订单", "ref": "m1"}] if views is None else views,
    }


def _http(content: object, *, finish: str = "stop", usage: dict | None = None, status: int = 200) -> httpx.Response:
    if status != 200:
        return httpx.Response(status, json={"error": "no"})
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return httpx.Response(200, json={
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": usage or {
            "prompt_tokens": 10, "completion_tokens": 4, "prompt_cache_hit_tokens": 1, "reasoning_tokens": 2,
        },
    })


def _code_of(user: str) -> str:
    found = re.search(r"公司：(\d{6})", user)
    if found is None:
        raise AssertionError(user[:120])
    return found.group(1)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    target = tmp_path / "v4.db"
    db.init_schema(target)
    return target


def _eval_client(path: Path, handler, report_id: str = REPORT) -> DeepSeekClient:
    return DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="deepseek-flash", api_key="k",
                          receipts=db.DbReceipts(path, report_id), timeout_seconds=5, max_attempts=2,
                          transport=httpx.MockTransport(handler))


def _run(path: Path, handler, candidates: list, *, settings: dict | None = None,
         clock=None, prepare=None, report_id: str = REPORT, client=None):
    def default_prepare(candidate: SimpleNamespace) -> dict:
        return _package(candidate.company.split(".")[0])

    return evaluate_candidates(
        candidates, report_id=report_id, db_path=path, prepare=prepare or default_prepare,
        client=client or _eval_client(path, handler, report_id), settings=settings or _settings(),
        clock=clock or (lambda: WHEN),
    )


def _rows(path: Path, report_id: str = REPORT) -> list[dict]:
    with db.read(path) as conn:
        return db.list_evaluations(conn, report_id)


def test_prompt_renders_the_package_deterministically() -> None:
    package = _package()
    text = user_prompt(package)
    again = user_prompt(json.loads(json.dumps(package, ensure_ascii=False, sort_keys=True)))
    assert text == again == user_prompt(package)
    labels = ["晨报交易日", "公司：", "主事件：", "其他事件：", "原文：", "同一事件的媒体报道",
              "公司快照", "价格背景", "业绩另算", "备料缺口"]
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions)
    assert "晨报交易日：2026-10-13" in text
    assert "窗口：2026-10-12T15:00:00+08:00 至 2026-10-13T08:00:00+08:00（北京时间）" in text
    assert "公司：300001 甲公司" in text and "主营：做测试" in text
    assert "种类 订单中标" in text and "金额 未披露" in text and "金额 10000000 元" in text
    assert "原文共 20000 字、12 页，以下为目录与开头" in text
    assert "[m2] 金十快讯｜2026-10-12T17:00:00+08:00｜拟收购" in text
    assert "价格背景（截至 2026-10-21 收盘）" in text
    assert "来源 pdf" in text and "缺失（未能读出同比）" in text
    assert "备料缺口：" in text and "行情 income：credential_missing" in text
    assert "联网" not in text and "搜索" not in text and "不应出现" not in text
    assert system_prompt().startswith("你是 A 股创业板的消息评估员")
    assert "①不过关或②透支，一票否决" in system_prompt()
    assert "联网" not in system_prompt()
    assert CONTRACT == "evaluation-v1"


def test_parse_accepts_push_and_skip_and_rejects_contradictions() -> None:
    package = _package()
    pushed = parse_output(json.dumps(_answer("推")), package)
    skipped = parse_output(json.dumps(_answer("不推", value_pass=False)), package)
    assert isinstance(pushed, Evaluation) and pushed.verdict == "推" and pushed.dropped_views == 0
    assert isinstance(skipped, Evaluation) and skipped.verdict == "不推"
    for contradiction in (_answer("推", value_pass=False), _answer("推", priced_pass=False)):
        vetoed = parse_output(json.dumps(contradiction), package)
        assert isinstance(vetoed, Evaluation) and vetoed.verdict == "不推" and vetoed.vetoed
        assert vetoed.as_dict()["vetoed"] is True and vetoed.as_dict()["verdict"] == "不推"
    assert "vetoed" not in pushed.as_dict()
    assert parse_output(json.dumps(_answer(title="字" * 40)), package).title == "字" * 40
    assert parse_output(json.dumps(_answer(title="字" * 41)), package) == "title 无效"
    assert parse_output(json.dumps(_answer(title="  ")), package) == "title 无效"
    assert parse_output("[]", package) == "输出不是 JSON 对象"
    assert parse_output("不是 json", package) == "输出不是 JSON 对象"
    missing = _answer()
    del missing["risk"]
    assert parse_output(json.dumps(missing), package) == "risk 为空"
    missing = _answer()
    missing["questions"]["money"] = {"text": ""}
    assert parse_output(json.dumps(missing), package) == "money.text 为空"
    missing = _answer()
    missing["questions"]["value"]["pass"] = 1
    assert parse_output(json.dumps(missing), package) == "value.pass 不是布尔"


def test_bad_views_are_dropped_and_counted() -> None:
    package = _package()
    views = [
        {"by": "媒体", "name": "金十", "text": "公司签了订单", "ref": "m1"},
        {"by": "公司", "name": "公司公告", "text": "公告确认订单", "ref": "a1"},
        {"by": "散户", "name": "某人", "text": "看好", "ref": "m1"},
        {"by": "券商", "name": "某券商", "text": "上调", "ref": "m9"},
        {"by": "媒体", "name": "", "text": "空名字", "ref": "m2"},
        "不是对象",
    ]
    parsed = parse_output(json.dumps(_answer(views=views)), package)
    assert isinstance(parsed, Evaluation)
    assert parsed.dropped_views == 4
    assert [(item["by"], item["ref"]) for item in parsed.views] == [("媒体", "m1"), ("公司", "a1")]
    assert parse_output(json.dumps(_answer(views="无")), package) == "views 不是列表"


def test_two_verdicts_stats_and_dropped_views(path: Path) -> None:
    calls = []
    usages = {
        "300001": {"prompt_tokens": 10, "completion_tokens": 4, "prompt_cache_hit_tokens": 3, "reasoning_tokens": 2},
        "300002": {
            "prompt_tokens": 7, "completion_tokens": 5, "prompt_cache_hit_tokens": 1,
            "completion_tokens_details": {"reasoning_tokens": 4},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        user = json.loads(request.content)["messages"][1]["content"]
        code = _code_of(user)
        if code == "300001":
            views = [
                {"by": "媒体", "name": "金十", "text": "公司签了订单", "ref": "m1"},
                {"by": "散户", "name": "某人", "text": "看好", "ref": "m1"},
                {"by": "券商", "name": "某券商", "text": "上调", "ref": "s1"},
            ]
            content = _answer("推", views=views)
        else:
            content = _answer("不推")
        return _http(content, usage=usages[code])

    result = _run(path, handler, [_candidate("300001"), _candidate("300002")])
    assert [item.company for item in result.items] == ["300001", "300002"]
    assert [item.verdict for item in result.items] == ["推", "不推"]
    assert result.fatal is None
    assert result.stats["candidates"] == 2
    assert result.stats["calls"] == 2 and result.stats["fromReceipt"] == 0
    assert result.stats["verdicts"] == {"推": 1, "不推": 1}
    assert result.stats["invalid"] == 0 and result.stats["gap"] == 0
    assert result.stats["droppedViews"] == 2
    assert result.stats["usage"] == {
        "prompt_tokens": 17, "completion_tokens": 9, "prompt_cache_hit_tokens": 4, "reasoning_tokens": 6,
    }
    assert result.stats["seconds"] >= 0 and result.prepare_seconds >= 0
    assert len(calls) == 2
    rows = _rows(path)
    assert [(row["company"], row["state"], row["verdict"], row["contract"]) for row in rows] == [
        ("300001", "succeeded", "推", CONTRACT), ("300002", "succeeded", "不推", CONTRACT),
    ]
    assert rows[0]["output"]["views"] == [{"by": "媒体", "name": "金十", "text": "公司签了订单", "ref": "m1"}]
    assert rows[0]["package"]["company"]["code"] == "300001"


def test_contradiction_is_vetoed_non_json_and_length_are_invalid(path: Path) -> None:
    raw = {
        "300001": json.dumps(_answer("推", value_pass=False), ensure_ascii=False),
        "300002": "这不是 JSON",
        "300003": json.dumps(_answer("不推"), ensure_ascii=False),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        code = _code_of(json.loads(request.content)["messages"][1]["content"])
        finish = "length" if code == "300003" else "stop"
        return _http(raw[code], finish=finish)

    result = _run(path, handler, [_candidate(code) for code in raw])
    assert [item.state for item in result.items] == ["succeeded", "invalid", "invalid"]
    assert result.items[0].verdict == "不推" and result.items[0].evaluation.vetoed
    assert result.items[1].error == "输出不是 JSON 对象"
    assert result.items[2].error == "finish:length"
    assert result.stats["invalid"] == 2 and result.stats["verdicts"] == {"推": 0, "不推": 1}
    assert result.stats["vetoed"] == 1
    with db.read(path) as conn:
        stored = {
            row["company"]: row
            for row in conn.execute("SELECT company, verdict, output_json, error FROM evaluations ORDER BY company")
        }
    vetoed = json.loads(stored["300001"]["output_json"])
    assert stored["300001"]["verdict"] == "不推" and vetoed["verdict"] == "不推" and vetoed["vetoed"] is True
    assert stored["300002"]["output_json"] == "这不是 JSON"
    assert stored["300003"]["output_json"] == raw["300003"] and stored["300003"]["error"] == "finish:length"


def test_cutoff_records_a_gap_without_calling(path: Path) -> None:
    calls = []
    prepared: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _http(_answer("推"))

    def prepare(candidate: SimpleNamespace) -> dict:
        prepared.append(candidate.company)
        return _package(candidate.company.split(".")[0])

    early = _run(path, handler, [_candidate("300001")], report_id="morning-2026-10-13", prepare=prepare,
                 clock=lambda: datetime(2026, 10, 13, 1, 4, 59, tzinfo=timezone.utc))
    assert early.items[0].state == "succeeded" and calls == [1]
    assert prepared == ["300001.SZ"]
    late = _run(path, handler, [_candidate("300001")], report_id="morning-2026-10-14", prepare=prepare,
                clock=lambda: datetime(2026, 10, 13, 1, 5, tzinfo=timezone.utc))
    assert late.items[0].state == "gap" and late.items[0].error == "收口时刻前没评完"
    assert late.stats["gap"] == 1 and late.stats["calls"] == 0 and calls == [1]
    assert prepared == ["300001.SZ"]
    assert _rows(path, "morning-2026-10-14")[0]["state"] == "gap"
    assert _rows(path, "morning-2026-10-14")[0]["error"] == "收口时刻前没评完"


def test_call_still_in_flight_at_cutoff_is_a_gap_and_the_late_result_does_not_stick(path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    now = {"t": datetime(2026, 10, 13, 9, 4, tzinfo=CST)}

    def clock() -> datetime:
        return now["t"]

    def handler(request: httpx.Request) -> httpx.Response:
        now["t"] = datetime(2026, 10, 13, 9, 6, tzinfo=CST)
        entered.set()
        release.wait(timeout=20)
        return _http(_answer("推"))

    started = time.monotonic()
    try:
        result = _run(path, handler, [_candidate("300001")], clock=clock, settings=_settings(concurrency=1))
        elapsed = time.monotonic() - started
        assert entered.is_set()
        assert elapsed < 10
        assert result.items[0].state == "gap" and result.items[0].error == "收口时刻前没评完"
        assert result.stats["gap"] == 1 and result.stats["calls"] == 0
        assert result.stats["verdicts"] == {"推": 0, "不推": 0}
        assert _rows(path)[0]["state"] == "gap"
    finally:
        release.set()
    deadline = time.monotonic() + 5
    receipt = None
    while time.monotonic() < deadline:
        with db.read(path) as conn:
            receipt = conn.execute("SELECT state FROM model_receipts").fetchone()
            state = db.get_evaluation(conn, REPORT, "300001")["state"]
        assert state == "gap"
        if receipt is not None and receipt["state"] == "succeeded":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("迟到的回执没有落库")
    time.sleep(0.3)
    assert _rows(path)[0]["state"] == "gap"
    assert receipt is not None and receipt["state"] == "succeeded"


def test_slow_prepare_does_not_hold_up_another_candidate(path: Path) -> None:
    release = threading.Event()
    fast_called = threading.Event()
    seen = {"fast_during_slow": False}

    def prepare(candidate: SimpleNamespace) -> dict:
        code = candidate.company.split(".")[0]
        if code == "300001":
            release.wait(timeout=10)
        return _package(code)

    def handler(request: httpx.Request) -> httpx.Response:
        code = _code_of(json.loads(request.content)["messages"][1]["content"])
        if code == "300002":
            seen["fast_during_slow"] = not release.is_set()
            fast_called.set()
        return _http(_answer("推"))

    def unblock() -> None:
        fast_called.wait(timeout=5)
        release.set()

    threading.Thread(target=unblock, daemon=True).start()
    result = _run(path, handler, [_candidate("300001"), _candidate("300002")],
                  settings=_settings(concurrency=2), prepare=prepare)
    assert seen["fast_during_slow"]
    assert [item.state for item in result.items] == ["succeeded", "succeeded"]
    assert [item.verdict for item in result.items] == ["推", "推"]


def test_unknown_becomes_gap_and_is_not_sent_again(path: Path) -> None:
    calls = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    first = _run(path, handler, [_candidate("300001")])
    assert first.items[0].state == "gap" and "不重发" in first.items[0].error
    assert first.stats["calls"] == 1 and calls == [1]
    second = _run(path, handler, [_candidate("300001")])
    assert second.stats["calls"] == 0 and second.stats["gap"] == 1 and calls == [1]
    assert "上次调用结果不明" not in (second.items[0].error or "")
    with db.write(path) as conn:
        conn.execute("DELETE FROM evaluations")
    third = _run(path, handler, [_candidate("300001")])
    assert third.stats["calls"] == 0 and calls == [1]
    assert third.items[0].state == "gap" and "上次调用结果不明" in third.items[0].error


def test_resume_reuses_the_stored_package_and_does_not_rebill(path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        code = _code_of(json.loads(request.content)["messages"][1]["content"])
        return _http(_answer("推" if code == "300001" else "不推"))

    flag = {"text": "第一次"}
    prepared: list[str] = []

    def prepare(candidate: SimpleNamespace) -> dict:
        prepared.append(flag["text"])
        return _package(candidate.company.split(".")[0], summary=flag["text"])

    client = _eval_client(path, handler)
    settings = _settings(concurrency=1)

    class Killed(BaseException):
        """模拟进程被杀：普通异常会被收成单条缺口，只有这一类会冒出去。"""

    class Boom:
        def __init__(self, inner: DeepSeekClient) -> None:
            self.inner = inner
            self.n = 0

        def complete(self, messages, options):
            result = self.inner.complete(messages, options)
            self.n += 1
            if self.n >= 2:
                raise Killed("crash after receipt")
            return result

    with pytest.raises(Killed, match="crash after receipt"):
        _run(path, handler, [_candidate("300001"), _candidate("300002")], settings=settings,
             prepare=prepare, client=Boom(client))
    with db.read(path) as conn:
        states = {row["company"]: row["state"] for row in db.list_evaluations(conn, REPORT)}
        receipts = conn.execute("SELECT count(*) FROM model_receipts").fetchone()[0]
    assert states == {"300001": "succeeded", "300002": "prepared"} and receipts == 2
    flag["text"] = "第二次"
    before = len(calls)
    again = _run(path, handler, [_candidate("300001"), _candidate("300002")], settings=settings,
                 prepare=prepare, client=client)
    assert prepared == ["第一次", "第一次"]
    assert len(calls) == before and again.stats["calls"] == 0 and again.stats["fromReceipt"] == 1
    assert [item.verdict for item in again.items] == ["推", "不推"]
    stored = _rows(path)
    assert stored[1]["package"]["company"]["summary"] == "第一次"
    body = client.request_body(
        [{"role": "system", "content": system_prompt()},
         {"role": "user", "content": user_prompt(stored[1]["package"])}],
        settings["modelOptions"])
    digest = hashlib.sha256(canonical(body).encode()).hexdigest()
    with db.read(path) as conn:
        saved = conn.execute("SELECT state FROM model_receipts WHERE request_sha256=?", (digest,)).fetchone()
        assert conn.execute("SELECT count(*) FROM model_receipts").fetchone()[0] == 2
    assert saved["state"] == "succeeded"


def test_rejected_call_fails_the_run(path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _http("", status=401)

    result = _run(path, handler, [_candidate("300001")], settings=_settings(concurrency=1))
    assert result.fatal == "DeepSeek 拒绝了请求（http:401）"
    assert _rows(path)[0]["state"] == "prepared"


def test_insert_does_not_overwrite_and_settle_only_moves_prepared(path: Path) -> None:
    with db.write(path) as conn:
        assert db.insert_prepared(conn, report_id=REPORT, company="300001", contract=CONTRACT,
                                  package={"marker": 1}, prepared_at=WHEN)
        assert not db.insert_prepared(conn, report_id=REPORT, company="300001", contract=CONTRACT,
                                      package={"marker": 2}, prepared_at=WHEN)
        assert db.get_evaluation(conn, REPORT, "300001")["package"] == {"marker": 1}
        assert db.settle_evaluation(conn, report_id=REPORT, company="300001", state="succeeded", settled_at=WHEN,
                                    verdict="推", output={"title": "甲"}, usage={"prompt_tokens": 1}, seconds=0.2) == 1
        assert db.settle_evaluation(conn, report_id=REPORT, company="300001", state="gap", settled_at=WHEN,
                                    error="不该覆盖") == 0
        stored = db.get_evaluation(conn, REPORT, "300001")
    assert stored["state"] == "succeeded" and stored["verdict"] == "推" and stored["error"] is None


def test_config_requires_cutoff_concurrency_and_model_options(tmp_path: Path) -> None:
    loaded = load_config()
    evaluation = loaded.rules["evaluation"]
    assert evaluation["contract"] == CONTRACT
    assert evaluation["cutoff"] == "09:05" and evaluation["concurrency"] == 6
    assert evaluation["modelOptions"]["thinking"] == {"type": "enabled"}
    assert evaluation["modelOptions"]["max_tokens"] == 16384
    assert evaluation["search"] is None
    assert loaded.rules["delivery"] == {"maxItems": 10}

    def rejected(mutate, message: str) -> None:
        rules = json.loads(json.dumps(loaded.rules))
        mutate(rules)
        target = tmp_path / "rules.json"
        target.write_text(json.dumps(rules), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_config(path=target)

    rejected(lambda rules: rules["evaluation"].__setitem__("cutoff", "09:15"), "早于")
    rejected(lambda rules: rules["evaluation"].__setitem__("cutoff", "09:16"), "早于")
    rejected(lambda rules: rules["evaluation"].__setitem__("cutoff", "9:05"), "HH:MM")
    rejected(lambda rules: rules["evaluation"].pop("cutoff"), "evaluation.cutoff")
    rejected(lambda rules: rules["evaluation"].__setitem__("concurrency", 0), "concurrency")
    rejected(lambda rules: rules["evaluation"].__setitem__("concurrency", True), "concurrency")
    rejected(lambda rules: rules["evaluation"].__setitem__("modelOptions", []), "modelOptions")
    rejected(lambda rules: rules["evaluation"].__setitem__("contract", "evaluation-v2"), "evaluation-v1")
    rejected(lambda rules: rules.pop("delivery"), "delivery")
    rejected(lambda rules: rules["delivery"].__setitem__("maxItems", 0), "maxItems")
    rejected(lambda rules: rules["delivery"].__setitem__("maxItems", True), "maxItems")
    rejected(lambda rules: rules["delivery"].__setitem__("maxItems", 1.5), "maxItems")


def _morning(path: Path, handler, apns: FakeApns):
    now = at("2026-10-13", "08:02")
    return run_morning(path, now=now, config=_config(), clock=lambda: now, collector=lambda _: {"status": "completed"},
                       client_factory=lambda rid: _client(path, handler, rid), pusher=Pusher(path, apns, lambda: now))


def test_morning_run_stores_one_push_and_one_skip(path: Path) -> None:
    _seed_window(path)
    _with_device(path)
    model = FakeModel()

    def handler(request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][1]["content"]
        if not user.startswith("晨报交易日"):
            return model(request)
        verdict = "推" if "公司：300683" in user else "不推"
        usage = {"prompt_tokens": 11 if verdict == "推" else 13, "completion_tokens": 6 if verdict == "推" else 8,
                 "prompt_cache_hit_tokens": 2 if verdict == "推" else 1,
                 "reasoning_tokens": 3 if verdict == "推" else 4}
        return _http(_answer(verdict, title="海特签协议" if verdict == "推" else "震裕先不推"), usage=usage)

    apns = FakeApns()
    result = _morning(path, handler, apns)
    assert result["state"] == "available" and result["entries"] == 1
    assert apns.sent == [("ab" * 32, "晨报已出", "1 家公司（有缺口）")]
    assert "calls" not in result["stats"] and "fromReceipt" not in result["stats"]
    assert result["stats"]["classification"]["calls"] == 1
    evaluation = result["stats"]["evaluation"]
    assert evaluation["candidates"] == 2 and evaluation["calls"] == 2 and evaluation["fromReceipt"] == 0
    assert evaluation["verdicts"] == {"推": 1, "不推": 1}
    assert evaluation["usage"] == {
        "prompt_tokens": 24, "completion_tokens": 14, "prompt_cache_hit_tokens": 3, "reasoning_tokens": 7,
    }
    assert result["stats"]["prepare"]["seconds"] >= 0 and "prepareSeconds" not in result["stats"]
    assert result["stats"]["pushed"] == 1 and result["stats"]["overflow"] == 0
    with db.read(path) as conn:
        stored = db.get_report(conn, "2026-10-13")
        rows = db.list_evaluations(conn, "morning-2026-10-13")
    entry = stored["content"]["entries"][0]
    assert entry["companyCode"] == "300683" and entry["title"] == "海特签协议"
    assert set(entry) == {"companyCode", "companyName", "type", "title", "conclusion", "questions", "risk",
                          "views", "firstDisclosed", "sources"}
    assert set(entry["questions"]) == {"value", "priced", "money", "price"}
    assert all(isinstance(text, str) for text in entry["questions"].values())
    assert "rank" not in entry and "importance" not in entry and "headline" not in entry
    assert stored["content"]["candidates"] == 2 and stored["content"]["evaluated"] == 2
    assert [(row["company"], row["state"], row["verdict"]) for row in rows] == [
        ("300683", "succeeded", "推"), ("300953", "succeeded", "不推"),
    ]
    assert rows[0]["output"]["title"] == "海特签协议" and rows[0]["package"]["company"]["name"] == "海特生物"
    assert rows[1]["output"]["conclusion"] == "先不推"


def test_morning_rejection_fails_the_report(path: Path) -> None:
    _seed_window(path)
    _with_device(path)
    model = FakeModel()

    def handler(request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][1]["content"]
        if user.startswith("晨报交易日"):
            return _http("", status=402)
        return model(request)

    apns = FakeApns()
    result = _morning(path, handler, apns)
    assert result["state"] == "failed" and result["reason"] == "DeepSeek 余额不足（http:402）"
    assert apns.sent[-1][1:] == ("晨报出问题了", "DeepSeek 余额不足（http:402）")
    with db.read(path) as conn:
        assert db.get_report(conn, "2026-10-13")["status"] == "failed"


def test_risk_written_inside_questions_is_accepted_as_is() -> None:
    package = _package()
    answer = _answer("不推", value_pass=False)
    risk = answer.pop("risk")
    answer["questions"]["risk"] = risk
    parsed = parse_output(json.dumps(answer, ensure_ascii=False), package)
    assert isinstance(parsed, Evaluation) and parsed.risk == risk
    answer["questions"]["risk"] = {"text": risk}
    assert parse_output(json.dumps(answer, ensure_ascii=False), package).risk == risk
    del answer["questions"]["risk"]
    assert parse_output(json.dumps(answer, ensure_ascii=False), package) == "risk 为空"


def test_worker_error_becomes_a_gap_and_others_still_finish(path: Path) -> None:
    """单条在工作线程里出错（不是模型调用的错），只把这一条记为缺口，其他条目照常评完。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return _http(_answer("不推", value_pass=False))

    def prepare(candidate: SimpleNamespace) -> dict:
        code = candidate.company.split(".")[0]
        if code == "300001":
            raise ValueError("材料里混进了坏数据")
        return _package(code)

    result = _run(path, handler, [_candidate("300001"), _candidate("300002")], prepare=prepare)
    by_company = {item.company: item for item in result.items}
    assert by_company["300001"].state == "gap" and by_company["300001"].error == "评估出错：ValueError"
    assert by_company["300002"].state == "succeeded" and by_company["300002"].verdict == "不推"
    assert result.stats["gap"] == 1 and result.fatal is None
    with db.read(path) as conn:
        stored = conn.execute("SELECT state, error FROM evaluations WHERE company='300001'").fetchone()
    assert stored["state"] == "gap" and stored["error"] == "评估出错：ValueError"


def test_result_returning_after_cutoff_is_a_gap(path: Path) -> None:
    """调用在收口前发出、收口后才回来：结果不交付，记为缺口，回执照常落库。"""
    now = {"t": datetime(2026, 10, 13, 9, 4, 50, tzinfo=CST)}

    def handler(request: httpx.Request) -> httpx.Response:
        now["t"] = datetime(2026, 10, 13, 9, 5, 1, tzinfo=CST)  # 模型回来时已过 09:05
        return _http(_answer("推"))

    result = _run(path, handler, [_candidate("300001")], clock=lambda: now["t"])
    (item,) = result.items
    assert item.state == "gap" and item.verdict is None and result.stats["verdicts"]["推"] == 0
    with db.read(path) as conn:
        row = conn.execute("SELECT state, error FROM evaluations").fetchone()
        receipts = conn.execute("SELECT state FROM model_receipts").fetchall()
    assert row["state"] == "gap" and row["error"] == "收口时刻前没评完"
    assert [r["state"] for r in receipts] == ["succeeded"]
