"""4.0.1 B120 第三轮：来源水位、配置数值下限、空结果防线、回看边界那一秒。假实现，只用临时库。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from neckline.morning import cli, db
from neckline.morning.collect import run_collection
from neckline.morning.report import Pusher, load_config, run_morning
from neckline.news.tushare import clip_covered_to_lookback
from neckline.screening.classify import Classification

from tests.test_b120_block_a import _patch_sender
from tests.test_b120_block_b import _FakeJin10, _config as collection_config, _flash, at
from tests.test_b120_round2 import _install_rules
from tests.test_morning import FakeApns, FakeModel, _client, _config, _seed_window, _with_device

TOKENS = {"TUSHARE_TOKEN": "t", "JIN10_MCP_TOKEN": "j"}


@pytest.fixture
def path(tmp_path):
    target = tmp_path / "v4.db"
    db.init_schema(target)
    return target


class _Jin10Crash(BaseException):
    pass


def _recording_post(calls):
    def post(payload):
        params = payload["params"]
        calls.append((params["start_date"], params["end_date"]))
        return {"code": 0, "data": {"items": []}}
    return post


def _tushare(result):
    return next(source for source in result["sources"] if source["sourceKey"] == "tushare-major-news")


# ------------------------------------------------------------------ S3 来源水位

def test_complete_tushare_summary_advances_the_watermark_when_the_run_stays_running(path):
    first_now, later = at("2026-10-12", "20:00"), at("2026-10-12", "20:30")
    bootstrap = at("2026-10-12", "15:00")
    calls: list[tuple[str, str]] = []

    def crash(**_kwargs):
        raise _Jin10Crash("jin10")

    with pytest.raises(_Jin10Crash):
        run_collection(path, now=first_now, config=collection_config(max_requests=8), tokens=TOKENS,
                       bootstrap_start=bootstrap, jin10_factory=crash, tushare_post=_recording_post(calls),
                       clock=lambda: first_now)
    assert len(calls) == 5  # 15:00–20:00 按 1 小时切成 5 片，全部取完
    with db.read(path) as conn:
        row = conn.execute("SELECT status, sources_json FROM collection_runs").fetchone()
        assert row["status"] == "running"
        assert [item["complete"] for item in json.loads(row["sources_json"])] == [True]
        assert db.source_watermark(conn, "tushare-major-news") == first_now
        assert db.source_watermark(conn, "jin10-flash") is None
    calls.clear()
    result = run_collection(path, now=later, config=collection_config(max_requests=8), tokens=TOKENS,
                            bootstrap_start=bootstrap, jin10_factory=lambda **_kwargs: _FakeJin10([([_flash(bootstrap)], False)]),
                            tushare_post=_recording_post(calls), clock=lambda: later)
    # 只按回看重取：回看起点 19:00:00 之前的切片不再请求。
    assert calls == [("2026-10-12 20:00:01", "2026-10-12 20:30:00"), ("2026-10-12 19:00:00", "2026-10-12 20:00:00")]
    assert _tushare(result)["requestedStart"] == at("2026-10-12", "19:00").isoformat()


def test_complete_tushare_summary_advances_the_watermark_when_the_whole_round_failed(path):
    first_now, later = at("2026-10-12", "20:00"), at("2026-10-12", "20:30")
    bootstrap = at("2026-10-12", "15:00")
    calls: list[tuple[str, str]] = []

    def broken(**_kwargs):
        raise RuntimeError("jin10 down")

    first = run_collection(path, now=first_now, config=collection_config(max_requests=8), tokens=TOKENS,
                           bootstrap_start=bootstrap, jin10_factory=broken, tushare_post=_recording_post(calls),
                           clock=lambda: first_now)
    assert first["status"] == "failed" and _tushare(first)["complete"] is True
    with db.read(path) as conn:
        assert conn.execute("SELECT status FROM collection_runs").fetchone()[0] == "failed"
        assert db.source_watermark(conn, "tushare-major-news") == first_now
    calls.clear()
    second = run_collection(path, now=later, config=collection_config(max_requests=8), tokens=TOKENS,
                            jin10_factory=broken, tushare_post=_recording_post(calls), clock=lambda: later)
    assert calls == [("2026-10-12 20:00:01", "2026-10-12 20:30:00"), ("2026-10-12 19:00:00", "2026-10-12 20:00:00")]
    assert _tushare(second)["requestedStart"] == at("2026-10-12", "19:00").isoformat()


# ------------------------------------------------------------------ S4 配置数值下限

def _rules_with(change) -> dict:
    rules = json.loads(json.dumps(load_config().rules))
    change(rules)
    return rules


def _set(section: str, key: str, value, *, sub: str | None = None):
    def apply(rules):
        target = rules[section][sub] if sub else rules[section]
        target[key] = value
    return apply


_MUST_BE_POSITIVE = [
    ("model", None, "maxAttempts", "model.maxAttempts"),
    ("classification", None, "maxAttempts", "classification.maxAttempts"),
    ("classification", None, "concurrency", "classification.concurrency"),
    ("classification", None, "batchMaxCompanies", "classification.batchMaxCompanies"),
    ("classification", None, "batchMaxChars", "classification.batchMaxChars"),
    ("classification", None, "profileChars", "classification.profileChars"),
    ("classification", "excerpt", "limit", "classification.excerpt.limit"),
    ("classification", "excerpt", "minChars", "classification.excerpt.minChars"),
    ("classification", "excerpt", "blockMaxChars", "classification.excerpt.blockMaxChars"),
]
_MAY_BE_ZERO = [
    ("classification", "excerpt", "lead", "classification.excerpt.lead"),
    ("classification", "excerpt", "window", "classification.excerpt.window"),
    ("classification", "history", "lookbackDays", "classification.history.lookbackDays"),
    ("classification", "history", "maxItems", "classification.history.maxItems"),
    ("classification", "history", "window", "classification.history.window"),
    ("classification", "history", "snippetChars", "classification.history.snippetChars"),
]


@pytest.mark.parametrize("section,sub,key,label", _MUST_BE_POSITIVE)
@pytest.mark.parametrize("value", [0, -1])
def test_counts_and_sizes_must_be_positive(monkeypatch, section, sub, key, label, value):
    _install_rules(monkeypatch, _rules_with(_set(section, key, value, sub=sub)))
    with pytest.raises(ValueError, match=label):
        load_config()


@pytest.mark.parametrize("section,sub,key,label", _MAY_BE_ZERO)
def test_zero_is_allowed_where_it_has_a_meaning_but_negative_is_not(monkeypatch, section, sub, key, label):
    _install_rules(monkeypatch, _rules_with(_set(section, key, 0, sub=sub)))
    assert load_config().rules[section][sub][key] == 0
    _install_rules(monkeypatch, _rules_with(_set(section, key, -1, sub=sub)))
    with pytest.raises(ValueError, match=label):
        load_config()


@pytest.mark.parametrize("value", [0, -1, 0.0, float("nan"), float("inf")])
def test_model_timeout_must_be_a_positive_finite_number(monkeypatch, value):
    _install_rules(monkeypatch, _rules_with(_set("model", "timeoutSeconds", value)))
    with pytest.raises(ValueError, match="model.timeoutSeconds"):
        load_config()


def test_shipped_config_passes_the_numeric_floors():
    rules = load_config().rules
    assert rules["model"]["maxAttempts"] >= 1 and rules["classification"]["concurrency"] >= 1


def test_zero_classification_attempts_push_config_invalid_and_exit_zero(path, monkeypatch):
    _install_rules(monkeypatch, _rules_with(_set("classification", "maxAttempts", 0)))
    with pytest.raises(ValueError, match="classification.maxAttempts"):
        load_config()
    collects = []
    monkeypatch.setattr(cli, "run_collection", lambda *_args, **_kwargs: collects.append(1) or {"status": "completed"})
    apns = _patch_sender(monkeypatch)
    _with_device(path)
    assert cli.main(["--db", str(path), "morning", "--now", "2026-10-13T08:02:00+08:00"]) == 0
    assert collects == [1]
    assert [item[1:] for item in apns.sent] == [("晨报出问题了", "筛选规则配置无效，今天不出报")]
    with db.read(path) as conn:
        assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 0


# ------------------------------------------------------------------ S4 空结果防线

def test_candidates_without_any_classification_result_fail_the_report(path, monkeypatch):
    _seed_window(path)
    _with_device(path)
    monkeypatch.setattr("neckline.morning.report.classify",
                        lambda *_args, **_kwargs: Classification(by_id={}, results={}, batches=[], fatal=None))
    model, apns = FakeModel(), FakeApns()
    now = at("2026-10-13", "08:02")
    result = run_morning(path, now=now, config=_config(), clock=lambda: now, collector=lambda _: {"status": "completed"},
                         client_factory=lambda rid: _client(path, model, rid), pusher=Pusher(path, apns, lambda: now))
    assert result["state"] == "failed" and result["reason"] == "有候选消息但没有任何判断结果"
    assert [item[1:] for item in apns.sent] == [("晨报出问题了", "有候选消息但没有任何判断结果")]
    with db.read(path) as conn:
        report = db.get_report(conn, "2026-10-13")
    assert report["status"] == "failed" and report["content"] is None and model.hits == 0


def test_a_window_without_candidates_still_delivers_an_empty_list(path):
    _with_device(path)
    model, apns = FakeModel(), FakeApns()
    now = at("2026-10-13", "08:02")
    result = run_morning(path, now=now, config=_config(), clock=lambda: now, collector=lambda _: {"status": "completed"},
                         client_factory=lambda rid: _client(path, model, rid), pusher=Pusher(path, apns, lambda: now))
    assert result["state"] == "available" and result["entries"] == 0
    assert apns.sent[0][1:] == ("晨报已出", "今天没有值得推的（有缺口）")


# ------------------------------------------------------------------ S5 回看边界那一秒

def test_clipped_cover_stops_one_second_before_the_lookback_start():
    fetched = at("2026-10-12", "20:00")
    covered = [(at("2026-10-12", "15:00"), at("2026-10-12", "19:00")),
               (at("2026-10-12", "19:00"), at("2026-10-12", "20:00"))]
    kept = clip_covered_to_lookback(covered, fetched_at=fetched, lookback_seconds=3600)
    assert kept == [(at("2026-10-12", "15:00"), at("2026-10-12", "19:00") - timedelta(seconds=1))]


def test_lookback_start_second_is_requested_again_so_late_news_at_that_second_is_fetched(path):
    done, later = at("2026-10-12", "20:00"), at("2026-10-12", "20:30")
    start = at("2026-10-12", "15:00")

    def source(key, *, complete, covered=None):
        item = {"sourceKey": key, "requestedStart": start.isoformat(), "requestedEnd": done.isoformat(),
                "complete": complete, "error": None if complete else "request_bound_reached",
                "requests": 1, "documents": 0, "newVersions": 0, "rejected": 0, "oldestSeen": None}
        if covered is not None:
            item["covered"] = [[lo.isoformat(), hi.isoformat()] for lo, hi in covered]
        return item

    with db.write(path) as conn:
        # 20:00 那一轮有一段覆盖正好止于 19:00:00，回看起点那一秒（19:00:00）当时可能还没有这条新闻。
        db.begin_collection(conn, run_id="partial", slot_at=done, started_at=done)
        db.finish_collection(conn, run_id="partial", status="partial", finished_at=done, sources=[
            source("tushare-major-news", complete=False, covered=[(start, at("2026-10-12", "19:00"))]),
            source("jin10-flash", complete=True), source("jin10-news", complete=True)])
        db.begin_collection(conn, run_id="done", slot_at=done, started_at=done)
        db.finish_collection(conn, run_id="done", status="completed", finished_at=done, sources=[
            source("tushare-major-news", complete=True),
            source("jin10-flash", complete=True), source("jin10-news", complete=True)])
    calls: list[tuple[str, str]] = []

    def post(payload):
        window = (payload["params"]["start_date"], payload["params"]["end_date"])
        calls.append(window)
        if window[0] <= "2026-10-12 19:00:00" <= window[1]:
            return {"code": 0, "data": {"items": [["2026-10-12 19:00:00", "证券时报", "19:00:00的迟到新闻", "迟到正文"]]}}
        return {"code": 0, "data": {"items": []}}

    result = run_collection(path, now=later, config=collection_config(max_requests=8), tokens=TOKENS,
                            jin10_factory=lambda **_kwargs: _FakeJin10([([_flash(later)], False)]),
                            tushare_post=post, clock=lambda: later)
    assert _tushare(result)["requestedStart"] == at("2026-10-12", "19:00").isoformat()
    assert min(window[0] for window in calls) == "2026-10-12 19:00:00"
    with db.read(path) as conn:
        titles = [row[0] for row in conn.execute("SELECT title FROM document_versions")]
    assert "19:00:00的迟到新闻" in titles
