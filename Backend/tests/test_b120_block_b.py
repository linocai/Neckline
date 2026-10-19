"""B120 块 B：OAuth 临时注册、TuShare 断档续采。假实现，只用临时库。"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams, RegistrationError
from mcp.shared.auth import OAuthClientInformationFull

from neckline.mcp.auth import REGISTRATION_LIMIT, REGISTRATION_TTL_SECONDS, SCOPE, OwnerAuth
from neckline.mcp.settings import McpSettings
from neckline.mcp.state import Store
from neckline.morning import db
from neckline.morning.collect import run_collection
from neckline.morning.report import coverage_gaps
from neckline.news import jin10

REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
CHALLENGE = "a" * 43
CST = timezone(timedelta(hours=8))


def at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+08:00")


class _Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


def _settings(tmp_path) -> McpSettings:
    return McpSettings("https://nk.linotsai.top", "ab" * 16, "cd" * 32, tmp_path / "oauth.db")


def _client(client_id: str) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id, client_secret="secret", redirect_uris=[REDIRECT],
        grant_types=["authorization_code", "refresh_token"], response_types=["code"],
        token_endpoint_auth_method="client_secret_post", scope=SCOPE,
    )


def _expiry(settings: McpSettings, client_id: str) -> float:
    with sqlite3.connect(settings.auth_db) as conn:
        row = conn.execute("SELECT expiry FROM records WHERE kind='client' AND key=?",
                           (Store.key(client_id),)).fetchone()
    assert row is not None
    return row[0]


def test_provisional_client_expires_and_exchange_makes_it_permanent(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr("neckline.mcp.auth.time.time", clock)
    monkeypatch.setattr("neckline.mcp.state.time.time", clock)
    settings = _settings(tmp_path)
    auth = OwnerAuth(settings)

    async def scenario():
        kept, dropped = _client("kept"), _client("dropped")
        await auth.register_client(kept)
        await auth.register_client(dropped)
        assert _expiry(settings, "kept") == clock.now + REGISTRATION_TTL_SECONDS
        stored = {
            "code": "", "client_id": kept.client_id, "scopes": [SCOPE], "expires_at": clock.now + 120,
            "code_challenge": CHALLENGE, "redirect_uri": REDIRECT, "redirect_uri_provided_explicitly": True,
            "resource": settings.resource, "subject": "owner",
        }
        auth.store.put("code", "abc", stored, clock.now + 120)
        token = await auth.exchange_authorization_code(
            kept, AuthorizationCode.model_validate({**stored, "code": "abc"}))
        assert token.access_token
        assert _expiry(settings, "kept") == 0
        clock.now += REGISTRATION_TTL_SECONDS + 1
        assert (await auth.get_client("kept")) is not None
        assert await auth.get_client("dropped") is None

    asyncio.run(scenario())


def test_registration_limit_counts_only_live_provisional_clients(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="neckline.mcp")
    settings = _settings(tmp_path)
    auth = OwnerAuth(settings)

    async def scenario():
        for index in range(11):
            client = _client(f"prod-{index}")
            auth.store.put("client", client.client_id, client.model_dump(mode="json"), 0)
        for index in range(REGISTRATION_LIMIT):
            await auth.register_client(_client(f"temp-{index}"))
        with pytest.raises(RegistrationError, match="limit"):
            await auth.register_client(_client("overflow"))
        for index in range(11):
            loaded = await auth.get_client(f"prod-{index}")
            assert loaded is not None and loaded.client_id == f"prod-{index}"
        params = AuthorizationParams(
            state="s", scopes=[SCOPE], code_challenge=CHALLENGE, redirect_uri=REDIRECT,
            redirect_uri_provided_explicitly=True, resource=settings.resource,
        )
        url = await auth.authorize(await auth.get_client("prod-0"), params)
        assert url.startswith(settings.public_base + "/login?")

    asyncio.run(scenario())
    assert any("registration limit reached" in record.message for record in caplog.records)


class _FakeJin10:
    def __init__(self, pages, **_kwargs):
        self.pages = pages
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def call_tool(self, _name, _arguments):
        items, more = self.pages[self.calls]
        self.calls += 1
        return {"data": {"items": items, "has_more": more}}

    def pagination_argument(self, _name):
        return "cursor"


def _flash(when: datetime) -> dict:
    return {"id": "f1", "url": "https://flash.jin10.com/detail/f1", "content": "快讯", "time": when.isoformat()}


def _config(*, max_requests: int, slice_seconds: int = 3600) -> dict:
    return {"jin10": {"endpoint": jin10.ENDPOINT, "protocolVersion": jin10.PROTOCOL_VERSION},
            "sources": {"tushare-major-news": {"lookbackSeconds": 3600, "sliceSeconds": slice_seconds,
                                               "maxRequests": max_requests, "timeoutSeconds": 5},
                        "jin10-flash": {"lookbackSeconds": 3600, "maxPages": 4, "timeoutSeconds": 5},
                        "jin10-news": {"lookbackSeconds": 3600, "maxPages": 4, "timeoutSeconds": 5}}}


def test_tushare_budget_gap_catches_up_without_repeating_slices_outside_lookback(tmp_path):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    now, bootstrap = at("2026-10-12", "20:00"), at("2026-10-12", "15:00")
    calls: list[tuple[str, str]] = []

    def post(payload):
        params = payload["params"]
        calls.append((params["start_date"], params["end_date"]))
        return {"code": 0, "data": {"items": []}}

    def factory(**_kwargs):
        return _FakeJin10([([_flash(bootstrap)], False)])

    tokens = {"TUSHARE_TOKEN": "t", "JIN10_MCP_TOKEN": "j"}
    rounds = []
    mark = None
    for _round in range(8):
        before = len(calls)
        result = run_collection(path, now=now, config=_config(max_requests=2), tokens=tokens,
                                bootstrap_start=bootstrap, jin10_factory=factory, tushare_post=post,
                                clock=lambda: now)
        made = calls[before:]
        rounds.append((result, made))
        assert len(made) <= 2
        with db.read(path) as conn:
            mark = db.source_watermark(conn, "tushare-major-news")
        if mark == now:
            break
        assert mark is None
    else:
        raise AssertionError("断档没有在回看截断之后补齐")
    assert rounds[0][1] == [
        ("2026-10-12 19:00:04", "2026-10-12 20:00:00"),
        ("2026-10-12 18:00:03", "2026-10-12 19:00:03"),
    ]
    first = next(source for source in rounds[0][0]["sources"] if source["sourceKey"] == "tushare-major-news")
    assert first["complete"] is False and first["error"] == "request_bound_reached"
    assert first["requestedStart"] == bootstrap.isoformat()
    # 回看窗（19:00 之后）下一轮还要再取；已经过了回看的那一段不重复。
    assert any(start >= "2026-10-12 19:00:00" for start, _end in rounds[1][1])
    assert ("2026-10-12 18:00:03", "2026-10-12 19:00:03") not in rounds[1][1]
    outside = [item for _result, made in rounds for item in made if item[1] <= "2026-10-12 19:00:00"]
    assert outside and len(outside) == len(set(outside))
    assert rounds[-1][0]["sources"]
    last = next(source for source in rounds[-1][0]["sources"] if source["sourceKey"] == "tushare-major-news")
    assert last["complete"] is True and mark == now


def test_quota_records_covered_slices_and_refetches_inside_the_lookback(tmp_path):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    now, bootstrap = at("2026-10-12", "22:00"), at("2026-10-12", "15:00")
    calls: list[tuple[str, str]] = []

    def post(payload):
        params = payload["params"]
        calls.append((params["start_date"], params["end_date"]))
        if len(calls) == 2:
            return {"code": 40203, "msg": "频率超限"}
        return {"code": 0, "data": {"items": []}}

    def factory(**_kwargs):
        return _FakeJin10([([_flash(bootstrap)], False)])

    tokens = {"TUSHARE_TOKEN": "t", "JIN10_MCP_TOKEN": "j"}
    config = _config(max_requests=8, slice_seconds=10800)
    first = run_collection(path, now=now, config=config, tokens=tokens, bootstrap_start=bootstrap,
                           jin10_factory=factory, tushare_post=post, clock=lambda: now)
    tushare_first = next(source for source in first["sources"] if source["sourceKey"] == "tushare-major-news")
    assert tushare_first["error"] == "quota_exceeded" and tushare_first["complete"] is False
    assert tushare_first["covered"] == [["2026-10-12T21:00:02+08:00", "2026-10-12T22:00:00+08:00"]]
    with db.read(path) as conn:
        assert db.source_watermark(conn, "tushare-major-news") is None
    before = len(calls)
    second = run_collection(path, now=now, config=config, tokens=tokens, jin10_factory=factory,
                            tushare_post=post, clock=lambda: now)
    made = calls[before:]
    # 21:00–22:00 取到时还在 1 小时回看里，不能当成已覆盖跳过。
    assert made == [
        ("2026-10-12 21:00:02", "2026-10-12 22:00:00"),
        ("2026-10-12 18:00:01", "2026-10-12 21:00:01"),
        ("2026-10-12 15:00:00", "2026-10-12 18:00:00"),
    ]
    tushare_second = next(source for source in second["sources"] if source["sourceKey"] == "tushare-major-news")
    assert tushare_second["complete"] is True and tushare_second["requestedStart"] == bootstrap.isoformat()
    with db.read(path) as conn:
        assert db.source_watermark(conn, "tushare-major-news") == now


def test_collection_without_covered_still_requests_the_full_range(tmp_path):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    now, bootstrap = at("2026-10-12", "20:00"), at("2026-10-12", "15:00")
    sources = []
    for key in ("tushare-major-news", "jin10-flash", "jin10-news"):
        sources.append({
            "sourceKey": key, "requestedStart": bootstrap.isoformat(), "requestedEnd": now.isoformat(),
            "complete": key != "tushare-major-news",
            "error": None if key != "tushare-major-news" else "request_bound_reached",
            "requests": 1, "documents": 0, "newVersions": 0, "rejected": 0, "oldestSeen": None,
        })
    assert "covered" not in sources[0]
    with db.write(path) as conn:
        db.begin_collection(conn, run_id="collect-old", slot_at=now, started_at=now)
        db.finish_collection(conn, run_id="collect-old", status="partial", sources=sources, finished_at=now)
    calls = []

    def post(payload):
        params = payload["params"]
        calls.append((params["start_date"], params["end_date"]))
        return {"code": 0, "data": {"items": []}}

    result = run_collection(
        path, now=now, config=_config(max_requests=8), tokens={"TUSHARE_TOKEN": "t", "JIN10_MCP_TOKEN": "j"},
        jin10_factory=lambda **_kwargs: _FakeJin10([([_flash(bootstrap)], False)]), tushare_post=post,
        clock=lambda: now)
    tushare = next(source for source in result["sources"] if source["sourceKey"] == "tushare-major-news")
    assert tushare["requestedStart"] == bootstrap.isoformat() and tushare["complete"] is True
    assert ("2026-10-12 19:00:04", "2026-10-12 20:00:00") in calls
    assert ("2026-10-12 15:00:00", "2026-10-12 16:00:00") in calls
    assert len(calls) == len(set(calls)) == 5


def test_coverage_gaps_counts_covered_slices_and_keeps_records_without_them():
    start, end = at("2026-10-12", "15:00"), at("2026-10-12", "20:00")
    slices = [
        (datetime.fromisoformat("2026-10-12T15:00:00+08:00"), datetime.fromisoformat("2026-10-12T16:00:00+08:00")),
        (datetime.fromisoformat("2026-10-12T16:00:01+08:00"), datetime.fromisoformat("2026-10-12T17:00:01+08:00")),
        (datetime.fromisoformat("2026-10-12T17:00:02+08:00"), datetime.fromisoformat("2026-10-12T18:00:02+08:00")),
        (datetime.fromisoformat("2026-10-12T18:00:03+08:00"), datetime.fromisoformat("2026-10-12T19:00:03+08:00")),
        (datetime.fromisoformat("2026-10-12T19:00:04+08:00"), datetime.fromisoformat("2026-10-12T20:00:00+08:00")),
    ]

    def source(key, *, complete, error=None, covered=None):
        item = {"sourceKey": key, "requestedStart": start.isoformat(), "requestedEnd": end.isoformat(),
                "complete": complete, "error": error}
        if covered is not None:
            item["covered"] = [[lo.isoformat(), hi.isoformat()] for lo, hi in covered]
        return item

    jin10_done = [source("jin10-flash", complete=True), source("jin10-news", complete=True)]
    old = {"sources": [source("tushare-major-news", complete=False, error="request_bound_reached"), *jin10_done]}
    assert "covered" not in old["sources"][0]
    assert coverage_gaps([old], start, end) == ["TuShare 新闻请求数到上限"]
    earlier = {"sources": [source("tushare-major-news", complete=False, error="request_bound_reached",
                                  covered=slices[:2]), *jin10_done]}
    later = {"sources": [source("tushare-major-news", complete=False, error="request_bound_reached",
                                covered=slices[2:]), *jin10_done]}
    assert any(gap.startswith("TuShare") for gap in coverage_gaps([earlier], start, end))
    assert coverage_gaps([earlier, later], start, end) == []
