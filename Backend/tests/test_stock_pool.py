"""股票池：按规则构造、开工刷新、和晨报同一套点名。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.market.pool_query import assess
from neckline.morning import cli, db
from neckline.morning.api import create_router
from neckline.morning.replay import ReplayError, run_replay
from neckline.morning.report import Pusher, load_config, run_morning
from neckline.morning.stock_pool import PoolFetchError, build_pool_rows, content_sha256, pool_from_rows
from neckline.screening.deepseek import DeepSeekClient
from neckline.screening.pool import Company, CompanyPool
from neckline.news.model import SourceDocument

from tests.test_morning import FakeModel, at

# screening-v6 去掉 configVersion、approvedAt、note、companyPool、matching、sectors 之后的 sha256。
# 主线评估合同改成 evaluation-v3 之后，这个值不再等于 v4。
MAINLINE_SHA256 = "a654dfe5a7d71e1cd4979572b75fe0c1090d3044772ea26032fb03cf1741b2f5"
DAY = date(2026, 11, 5)
SECRET = "pool-token-do-not-print"


def _rules() -> dict:
    return load_config().rules["companyPool"]


def _basic(code: str, name: str, listed: str = "20200101", status: str = "L", fullname: str = "") -> dict:
    symbol = code.split(".", 1)[0]
    return {"ts_code": f"{symbol}.SZ", "name": name, "fullname": fullname or f"{name}全称",
            "list_date": listed, "list_status": status}


def _build(basic, *, as_of: date, today: date | None = None, company=(), namechange=(), seed=(),
           previous=None, limit: int | None = None) -> list[dict]:
    rules = _rules()
    return build_pool_rows(
        basic, company, namechange_rows=namechange, seed_rows=seed, as_of=as_of, today=today or as_of,
        prefixes=tuple(rules["prefixes"]), exclude_name_contains=rules["excludeNameContains"],
        summary_max_chars=limit if limit is not None else int(rules["summaryMaxChars"]),
        previous_summaries=previous)


def _frame(fields, rows):
    return {"code": 0, "data": {"fields": fields, "items": rows, "has_more": False}}


def _post_from(basic, company=()):
    seen = []

    def post(_url, payload, _timeout):
        seen.append(payload["api_name"])
        if payload["api_name"] == "stock_basic":
            return _frame(["ts_code", "name", "fullname", "list_date", "list_status"],
                          [[row["ts_code"], row["name"], row["fullname"], row["list_date"], row["list_status"]]
                           for row in basic])
        if payload["api_name"] == "stock_company":
            return _frame(["ts_code", "main_business"], [[row["ts_code"], row["main_business"]] for row in company])
        if payload["api_name"] == "daily_basic":
            stamp = payload["params"]["trade_date"]
            return _frame(["ts_code", "trade_date", "circ_mv", "total_mv"],
                          [[row["ts_code"], stamp, 100000, 200000] for row in basic])
        raise AssertionError(payload["api_name"])

    return post, seen


def _run(path, post, *, environ=None, now=None):
    now = now or at("2026-10-13", "08:02")
    sent = []

    def push(_token, title, body, **_kwargs):
        sent.append((title, body))
        return type("R", (), {"ok": True, "reason": "ok"})()

    result = run_morning(path, now=now, config=load_config(), clock=lambda: now,
                         collector=lambda _at: {"status": "completed"}, client_factory=lambda _rid: None,
                         pusher=Pusher(path, push, lambda: now), pool_post=post,
                         pool_environ=environ if environ is not None else {"TUSHARE_TOKEN": SECRET})
    return result, sent


@pytest.fixture
def path(tmp_path):
    target = tmp_path / "v4.db"
    db.init_schema(target)
    return target


def test_pool_rows_follow_listing_st_prefix_and_summary_rules():
    assert _build([_basic("302001", "甲公司", "20261106")], as_of=DAY) == []
    entered = _build([_basic("302001", "甲公司", "20261105", fullname="甲公司股份有限公司")], as_of=DAY)
    assert entered[0]["code"] == "302001.SZ" and entered[0]["name"] == "甲公司"
    assert _build([_basic("303001", "乙公司")], as_of=DAY) == []
    assert _build([_basic("300001", "丙公司", status="D")], as_of=DAY) == []
    assert _build([_basic("300001", "ST丙公司")], as_of=DAY) == []

    today = DAY
    basic = [_basic("300001", "丙公司")]
    capped = [{"ts_code": "300001.SZ", "name": "ST丙公司", "start_date": "20200101", "end_date": "20260630"}]
    assert _build(basic, as_of=date(2026, 6, 1), today=today, namechange=capped) == []
    restored = _build(basic, as_of=date(2026, 8, 1), today=today, namechange=capped)
    assert restored[0]["name"] == "丙公司"
    assert _build(basic, as_of=date(2026, 1, 1), today=today)[0]["name"] == "丙公司"

    seed = [{"code": "300001.SZ", "name": "丙公司", "index": {"aliases": ["丙", "丙公司", "老简称"], "summary": "种子摘要"}}]
    company = [{"ts_code": "300001.SZ", "main_business": "工商登记的主营" * 10}]
    rich = _build(basic, as_of=DAY, company=company, seed=seed)
    assert rich[0]["summary"] == "种子摘要" and rich[0]["summarySource"] == "alias-seed"
    assert rich[0]["aliases"][:2] == ["丙公司", "丙公司全称"]
    assert "老简称" in rich[0]["aliases"] and "丙" not in rich[0]["aliases"]
    assert rich[0]["aliases"].count("丙公司") == 1

    short = _build([_basic("302008", "丁公司")], as_of=DAY, company=[{"ts_code": "302008.SZ", "main_business": "一二三四五六"}],
                   limit=4)
    assert short[0]["summary"] == "一二三四" and short[0]["summarySource"] == "stock_company"
    kept = _build([_basic("302008", "丁公司")], as_of=DAY, previous={"302008": "库里的摘要"})
    assert kept[0]["summary"] == "库里的摘要"


def test_circ_mv_drops_the_largest_share_and_keeps_a_missing_value():
    basics = [_basic(f"300{index:03d}", f"公司{index}") for index in range(1, 11)]
    basics.append(_basic("300011", "刚上市"))
    mv = [{"ts_code": f"300{index:03d}.SZ", "trade_date": "20261012", "circ_mv": 1000 - index, "total_mv": 2000}
          for index in range(1, 11)]
    # 300001 与 300002 流通市值并列最大，代码小的先去掉。
    mv[0]["circ_mv"] = 5000
    mv[1]["circ_mv"] = 5000
    dropped: list[dict] = []
    rows = build_pool_rows(
        basics, [], as_of=DAY, today=DAY, prefixes=("300", "301", "302"), exclude_name_contains="ST",
        summary_max_chars=40, mv_rows=mv, exclude_top_share=0.1, mv_date=date(2026, 10, 12), excluded=dropped)
    # N = 11，k = floor(11 × 0.1) = 1。并列时去掉代码较小的 300001，没有市值的 300011 留下。
    assert [item["code"] for item in dropped] == ["300001.SZ"]
    assert "300001.SZ" not in {row["code"] for row in rows}
    assert "300011.SZ" in {row["code"] for row in rows}
    assert next(row for row in rows if row["code"] == "300011.SZ")["circMv"] is None
    assert next(row for row in rows if row["code"] == "300002.SZ")["circMv"] == 5000
    assert next(row for row in rows if row["code"] == "300002.SZ")["mvDate"] == "2026-10-12"


def test_daily_basic_failure_reuses_the_previous_pool(path):
    from neckline.morning.stock_pool import store_rows
    with db.write(path) as conn:
        store_rows(conn, [_basic_row()], now=datetime(2026, 11, 1, tzinfo=timezone.utc), source="tushare")
    seed = load_config().alias_seed[0]

    def post(_url, payload, _timeout):
        if payload["api_name"] == "daily_basic":
            raise PoolFetchError("daily_basic 请求失败")
        return _post_from([_basic(seed["code"], seed["name"])])[0](_url, payload, _timeout)

    result, _sent = _run(path, post)
    assert result["state"] == "available"
    assert "daily_basic" in result["gapSummary"] and "沿用上一份" in result["gapSummary"]
    assert result["stats"]["pool"]["reused"] is True


def test_mainline_rules_match_v4_and_company_pool_keys_are_required(tmp_path):
    rules = load_config().rules
    body = {key: value for key, value in rules.items()
            if key not in {"configVersion", "approvedAt", "note", "companyPool", "matching", "sectors"}}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == MAINLINE_SHA256
    # matching 只多了扩池新增公司的常用词简称，原来 7 个原样保留
    assert rules["matching"]["strictShortNames"] == ["值得买", "线上线下", "向日葵", "新天地", "探路者", "新城市", "杭州高新",
                                                    "机器人", "同花顺", "新产业", "指南针", "创世纪", "东方财富"]
    assert set(rules["matching"]) == {"note", "strictShortNames"}
    assert rules["configVersion"] == "screening-v6" and rules["approvedAt"] == "2026-11-05"
    assert len(load_config().alias_seed) == 1089 and len(load_config().pool) == 0

    def rejected(mutate, message):
        copied = json.loads(json.dumps(rules))
        mutate(copied)
        target = tmp_path / "rules.json"
        target.write_text(json.dumps(copied), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_config(path=target)

    rejected(lambda item: item["companyPool"].pop("prefixes"), "companyPool.prefixes")
    rejected(lambda item: item["companyPool"].__setitem__("prefixes", []), "companyPool.prefixes")
    rejected(lambda item: item["companyPool"].pop("excludeNameContains"), "companyPool.excludeNameContains")
    rejected(lambda item: item["companyPool"].__setitem__("excludeNameContains", ""), "companyPool.excludeNameContains")
    rejected(lambda item: item["companyPool"].pop("summaryMaxChars"), "companyPool.summaryMaxChars")
    rejected(lambda item: item["companyPool"].__setitem__("summaryMaxChars", True), "companyPool.summaryMaxChars")
    rejected(lambda item: item["companyPool"].pop("aliasSeed"), "companyPool.aliasSeed")
    rejected(lambda item: item["companyPool"]["aliasSeed"].pop("file"), "companyPool.aliasSeed.file")
    rejected(lambda item: item["companyPool"]["aliasSeed"].__setitem__("sha256", "0" * 64), "哈希不符")
    rejected(lambda item: item["companyPool"].pop("circMv"), "companyPool.circMv")
    rejected(lambda item: item["companyPool"]["circMv"].__setitem__("excludeTopShare", 0), "excludeTopShare")
    rejected(lambda item: item["companyPool"]["circMv"].__setitem__("excludeTopShare", 1), "excludeTopShare")
    rejected(lambda item: item["companyPool"]["circMv"].__setitem__("excludeTopShare", True), "excludeTopShare")


def test_refresh_writes_the_pool_and_calls_stock_company_only_for_a_new_code(path):
    seed = load_config().alias_seed[0]
    known = _basic(seed["code"], seed["name"])
    post, seen = _post_from([known])
    result, _sent = _run(path, post)
    assert result["state"] == "available"
    assert seen == ["stock_basic", "daily_basic"]
    assert result["stats"]["pool"]["reused"] is False
    assert result["stats"]["pool"]["count"] == 1
    with db.read(path) as conn:
        stored = db.read_stock_pool(conn)
    assert stored["count"] == 1 and stored["source"] == "tushare"
    assert stored["sha256"] == result["stats"]["pool"]["sha256"] == content_sha256(stored["rows"])
    assert SECRET not in json.dumps(result, ensure_ascii=False)

    fresh = tmp_db(path.parent / "second.db")
    newbie = _basic("302001", "甲公司")
    post, seen = _post_from([newbie], [{"ts_code": "302001.SZ", "main_business": "新公司主营"}])
    result, _sent = _run(fresh, post)
    assert seen == ["stock_basic", "stock_company", "daily_basic"]
    assert result["stats"]["pool"]["count"] == 1
    with db.read(fresh) as conn:
        row = db.read_stock_pool(conn)["rows"][0]
    assert row["code"] == "302001.SZ" and row["summary"] == "新公司主营"


def tmp_db(path):
    db.init_schema(path)
    return path


def test_refresh_failure_reuses_the_previous_pool_and_a_missing_pool_fails_honestly(path):
    from neckline.morning.stock_pool import store_rows
    rows = [_basic_row()]
    with db.write(path) as conn:
        store_rows(conn, rows, now=datetime(2026, 11, 1, tzinfo=timezone.utc), source="tushare")
    with db.read(path) as conn:
        previous = db.read_stock_pool(conn)

    def explode(_url, payload, _timeout):
        raise PoolFetchError(f"stock_basic 请求失败 {SECRET}")

    result, sent = _run(path, explode)
    assert result["state"] == "available"
    assert "沿用上一份" in result["gapSummary"] and SECRET not in result["gapSummary"]
    assert result["stats"]["pool"]["reused"] is True
    assert result["stats"]["pool"]["sha256"] == previous["sha256"]
    assert result["stats"]["pool"]["refreshedAt"] == previous["refreshedAt"]
    assert sent == []

    empty = tmp_db(path.parent / "empty.db")
    with db.write(empty) as conn:
        db.upsert_device(conn, token="ab" * 32, platform="ios", at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    result, sent = _run(empty, explode)
    assert result["state"] == "failed" and result["reason"].startswith("股票池没能刷新")
    assert "沿用" not in result["reason"] and SECRET not in result["reason"]
    assert sent == [("晨报出问题了", result["reason"])]
    with db.read(empty) as conn:
        assert db.read_stock_pool(conn) is None


def test_an_empty_stock_basic_counts_as_a_failed_refresh(path):
    from neckline.morning.stock_pool import store_rows
    with db.write(path) as conn:
        store_rows(conn, [_basic_row()], now=datetime(2026, 11, 1, tzinfo=timezone.utc), source="tushare")
    with db.read(path) as conn:
        previous = db.read_stock_pool(conn)
    post, seen = _post_from([])
    result, _sent = _run(path, post)
    assert seen == ["stock_basic"]
    assert result["state"] == "available" and "沿用上一份" in result["gapSummary"]
    assert result["stats"]["pool"]["reused"] is True
    assert result["stats"]["pool"]["sha256"] == previous["sha256"]
    with db.read(path) as conn:
        assert db.read_stock_pool(conn)["sha256"] == previous["sha256"]


def _basic_row():
    return {"code": "300001.SZ", "name": "甲公司", "fullname": "甲公司全称", "listDate": "2020-01-01",
            "aliases": ["甲公司", "甲公司全称"], "summary": "原摘要", "summarySource": "stock_company"}


def test_resume_does_not_refresh_and_checks_the_pool_hash(path, monkeypatch):
    seed = load_config().alias_seed[0]
    post, seen = _post_from([_basic(seed["code"], seed["name"])])
    monkeypatch.setattr("neckline.morning.report.classify", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        _run(path, post)
    assert seen == ["stock_basic", "daily_basic"]
    with db.read(path) as conn:
        started = db.get_report(conn, "2026-10-13")
    assert started["status"] == "running"
    digest = started["stats"]["pool"]["sha256"]

    monkeypatch.setattr("neckline.morning.report.classify", _empty_classify())
    result, _sent = _run(path, post)
    assert seen == ["stock_basic", "daily_basic"]
    assert result["state"] == "available" and result["stats"]["collection"] == "skipped_resume"
    assert result["stats"]["pool"]["sha256"] == digest

    with db.write(path) as conn:
        db.upsert_device(conn, token="ab" * 32, platform="ios", at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        conn.execute("UPDATE stock_pool_meta SET content_sha256=? WHERE id=1", ("f" * 64,))
        conn.execute("UPDATE reports SET status='running', stats_json=? WHERE trade_date='2026-10-13'",
                     (json.dumps({"pool": {"count": 1, "sha256": digest, "refreshedAt": "x", "reused": False}}),))
    again, sent = _run(path, post)
    assert again["state"] == "failed" and again["reason"] == "股票池与开工时不一致"
    assert sent == [("晨报出问题了", "股票池与开工时不一致")]
    assert seen == ["stock_basic", "daily_basic"]


def _empty_classify():
    from neckline.screening.classify import classify
    return classify


def test_code_302_matches_and_check_pool_uses_the_same_strict_rule(caplog):
    pool = CompanyPool([
        Company("302001.SZ", "甲公司", ("甲公司",)),
        Company("300785.SZ", "值得买", ("值得买", "北京值得买科技股份有限公司")),
    ], strict_names=["值得买", "不在池里的词"])
    assert set(pool.match("公告 302001")) == {"302001.SZ"}
    assert set(pool.match("甲公司中标")) == {"302001.SZ"}
    named = "值得买：三季度净利增长"
    assert set(pool.match(named)) == {"300785.SZ"}
    assert assess(named, pool)["inPool"] is True and assess(named, pool)["code"] == "300785.SZ"
    loose = "哪款电视值得买（300785）"
    assert pool.match(loose) == {} and assess(loose, pool)["inPool"] is False
    caplog.set_level(logging.INFO)
    ignored = CompanyPool([Company("302001.SZ", "甲公司", ("甲公司",))], strict_names=["值得买"])
    assert ignored.strict_names == () and "值得买" in caplog.text


def test_replay_refuses_to_fetch_and_can_use_a_snapshot_or_the_seed(tmp_path, monkeypatch):
    called = {}

    def fake_morning(*_args, **kwargs):
        called["source"] = kwargs.get("pool_source")
        return {"state": "available", "entries": 0, "stats": {}}

    monkeypatch.setattr("neckline.morning.replay.run_morning", fake_morning)
    with pytest.raises(ReplayError, match="pool-snapshot"):
        run_replay(snapshot=tmp_path / "missing.db", trade_date="2026-10-13", work=tmp_path / "work",
                   market_snapshot=tmp_path / "market.json", receipts=tmp_path / "receipts",
                   filings_cache=tmp_path / "filings")
    assert called == {}

    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    snapshot = _snapshot(tmp_path / "seed.db", "2026-10-13")
    market = tmp_path / "market.json"
    market.write_text("{}", encoding="utf-8")
    run_replay(snapshot=snapshot, trade_date="2026-10-13", work=tmp_path / "seed-work", market_snapshot=market,
               receipts=tmp_path / "receipts", filings_cache=tmp_path / "filings", pool_seed_only=True)
    assert called["source"] == "database"
    with db.read(tmp_path / "seed-work" / "2026-10-13.db") as conn:
        stored = db.read_stock_pool(conn)
    assert stored["count"] == 1089 and stored["source"] == "seed"

    payload = {
        "stock_basic": [_basic("302001", "甲公司")],
        "stock_company": [],
        "namechange": [{"ts_code": "302001.SZ", "name": "甲旧名", "start_date": "20200101", "end_date": "20261001"}],
        "daily_basic": [{"ts_code": "302001.SZ", "trade_date": "20260529", "circ_mv": 100000, "total_mv": 200000}],
    }
    pool_file = tmp_path / "pool.json"
    pool_file.write_text(json.dumps(payload), encoding="utf-8")
    snap = _snapshot(tmp_path / "snap.db", "2026-06-01")
    run_replay(snapshot=snap, trade_date="2026-06-01", work=tmp_path / "snap-work", market_snapshot=market,
               receipts=tmp_path / "receipts2", filings_cache=tmp_path / "filings2", pool_snapshot=pool_file,
               today=date(2026, 11, 5))
    with db.read(tmp_path / "snap-work" / "2026-06-01.db") as conn:
        row = db.read_stock_pool(conn)["rows"][0]
    assert row["code"] == "302001.SZ" and row["name"] == "甲旧名"
    assert row["circMv"] == 100000 and row["mvDate"] == "2026-05-29"
    other = dict(payload)
    other["daily_basic"] = [{"ts_code": "302001.SZ", "trade_date": "20260528", "circ_mv": 1, "total_mv": 1}]
    missing = tmp_path / "missing-mv.json"
    missing.write_text(json.dumps(other), encoding="utf-8")
    with pytest.raises(ReplayError, match="2026-05-29 的 daily_basic"):
        run_replay(snapshot=snap, trade_date="2026-06-01", work=tmp_path / "missing-work", market_snapshot=market,
                   receipts=tmp_path / "receipts3", filings_cache=tmp_path / "filings3", pool_snapshot=missing,
                   today=date(2026, 11, 5))


def _snapshot(path, trade_date: str):
    db.init_schema(path)
    with db.write(path) as conn:
        db.begin_report(conn, report_id=f"morning-{trade_date}", trade_date=trade_date,
                        window_start=datetime(2026, 6, 1, 7, tzinfo=timezone.utc),
                        window_end=datetime(2026, 6, 1, 0, tzinfo=timezone.utc),
                        config_version="screening-v6", config_sha256="0" * 64,
                        started_at=datetime(2026, 6, 1, 0, 2, tzinfo=timezone.utc))
    return path


def test_refresh_pool_command_prints_the_result_and_hides_the_token(path, monkeypatch, capsys):
    seed = load_config().alias_seed[0]
    post, _seen = _post_from([_basic(seed["code"], seed["name"])])
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET)
    monkeypatch.setattr("neckline.morning.stock_pool.http_post", post)
    assert cli.main(["--db", str(path), "refresh-pool", "--now", "2026-11-05T01:00:00+00:00"]) == 0
    out, err = capsys.readouterr()
    assert SECRET not in out and SECRET not in err
    printed = json.loads(out)
    assert printed["count"] == 1 and printed["source"] == "tushare" and printed["sha256"]

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert cli.main(["--db", str(path.parent / "other.db"), "refresh-pool", "--now", "2026-11-05T01:00:00+00:00"]) == 1
    _out, err = capsys.readouterr()
    assert "未配置 TUSHARE_TOKEN" in err and SECRET not in err


def test_morning_entry_uses_the_database_pool_for_a_302_company(path, monkeypatch, capsys):
    """定时入口写出的晨报、接口和 check_pool 用的是同一份库里的池子。"""
    published = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    fetched = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    text = "302001 甲公司签下订单"
    document = SourceDocument(
        source_key="jin10-flash", external_id="c302", url="https://example.test/c302", title="甲公司订单",
        original_text=text, excerpt=None, published_at=published, published_precision="exact", fetched_at=fetched)
    with db.write(path) as conn:
        db.store_documents(conn, [document])
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    post, seen = _post_from(
        [_basic("302001", "甲公司")],
        [{"ts_code": "302001.SZ", "main_business": "甲公司做设备"}])
    monkeypatch.setattr("neckline.morning.stock_pool.http_post", post)
    monkeypatch.setattr(cli, "run_collection", lambda *_a, **_k: {"status": "completed"})

    class Clock:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 10, 13, 0, 2, tzinfo=timezone.utc)

        fromisoformat = staticmethod(datetime.fromisoformat)

    monkeypatch.setattr(cli, "datetime", Clock)
    model = _Model302()

    class Wrapped(DeepSeekClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(model)
            super().__init__(**kwargs)

    monkeypatch.setattr(cli, "DeepSeekClient", Wrapped)
    assert cli.main(["--db", str(path), "morning", "--now", "2026-10-13T00:02:00+00:00"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"] == "available" and seen == ["stock_basic", "stock_company", "daily_basic"]
    pool_stats = printed["stats"]["pool"]
    assert pool_stats["count"] == 1 and pool_stats["reused"] is False and pool_stats["sha256"] and pool_stats["refreshedAt"]
    assert model.companies == ["302001"]
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=lambda: None))
    latest = TestClient(app).get("/api/v4/reports/latest").json()
    assert latest["content"]["stats"]["pool"]["sha256"] == pool_stats["sha256"]
    assert latest["content"]["candidates"] >= 1
    found = assess(text)
    assert found["inPool"] is True and found["code"] == "302001.SZ"
    with db.read(path) as conn:
        stored = db.read_stock_pool(conn)
    pool, _profiles = pool_from_rows(stored["rows"], strict_names=load_config().rules["matching"]["strictShortNames"])
    assert set(pool.match(text)) == {"302001.SZ"}


class _Model302(FakeModel):
    def __init__(self):
        super().__init__()
        self.companies = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][1]["content"]
        if prompt.startswith("晨报交易日"):
            return self._evaluation(prompt)
        blocks = re.split(r"^### ", prompt, flags=re.M)[1:]
        codes = [block.split()[0] for block in blocks]
        self.companies.extend(codes)
        self.hits += 1
        companies = []
        for block, code in zip(blocks, codes):
            ids = re.findall(r"^\[(m\d+)\]", block.split("窗口开始前的相关消息")[0], flags=re.M)
            companies.append({"company": code, "events": [{"ids": ids, "relation": "主体", "type": "业绩预增",
                              "direction": "利好", "channel": "公司公告", "amountYuan": None,
                              "firstDisclosed": None, "fresh": "新", "importance": "高",
                              "headline": "甲公司订单", "reason": "公告"}], "mentions": []})
        content = json.dumps({"companies": companies}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


def test_replay_clears_the_day_so_live_evaluations_are_not_reused(tmp_path):
    from neckline.morning.replay import _clear_day
    path = tmp_path / "snapshot.db"
    db.init_schema(path)
    when = datetime(2026, 10, 30, 0, 0, tzinfo=timezone.utc)
    with db.write(path) as conn:
        for day in ("2026-10-29", "2026-10-30"):
            db.begin_report(conn, report_id=f"morning-{day}", trade_date=day, window_start=when, window_end=when,
                            config_version="screening-v6", config_sha256="0" * 64, started_at=when)
            db.insert_prepared(conn, report_id=f"morning-{day}", company="300001", contract="evaluation-v2",
                               package={}, prepared_at=when)
        _clear_day(conn, "2026-10-30")
    with db.read(path) as conn:
        assert db.get_report(conn, "2026-10-30") is None
        assert db.list_evaluations(conn, "morning-2026-10-30") == []
        assert db.get_report(conn, "2026-10-29") is not None
        assert len(db.list_evaluations(conn, "morning-2026-10-29")) == 1
