"""No test here contacts TuShare; all requests use MockTransport or a callable."""

from __future__ import annotations

import json
from datetime import date, datetime

import httpx

from neckline.k10.sources import SourceFetchRequest
from neckline.k10.tushare_news import MAJOR_NEWS_FIELDS, SATURATION_RECORDS, TUSHARE_API_URL, TuShareMajorNewsAdapter
from neckline.k10.windows import SHANGHAI, evening_window, morning_window


def _moment(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=SHANGHAI)


def _request(*, window=None) -> SourceFetchRequest:
    return SourceFetchRequest(
        window=window or morning_window(previous_trading_day=date(2026, 9, 4), observation_day=date(2026, 9, 7)),
        previous_cursor=None,
        source_success_watermark=_moment(4, 21),
    )


def _body(items):
    return {"code": 0, "msg": "", "data": {"fields": MAJOR_NEWS_FIELDS.split(","), "items": items}}


def _record(pub_time="2026-09-05 22:00:00", source="新华社", title="测试通讯", content="完整通讯正文"):
    return [pub_time, source, title, content]


def test_major_news_uses_official_endpoint_and_exact_second_parameters_with_mock_transport():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_body([_record()]))

    adapter = TuShareMajorNewsAdapter(
        token="runtime-token-only", src="新华社", transport=httpx.MockTransport(handler), clock=lambda: _moment(7, 9, 5),
    )
    result = adapter.fetch_incremental(_request())

    assert seen["url"] == TUSHARE_API_URL
    assert seen["payload"]["api_name"] == "major_news"
    assert seen["payload"]["fields"] == MAJOR_NEWS_FIELDS
    assert seen["payload"]["params"] == {
        "start_date": "2026-09-04 21:00:00", "end_date": "2026-09-07 09:00:00", "src": "新华社",
    }
    assert result.complete is True
    assert result.success_watermark == _moment(7, 9)
    document = result.documents[0]
    assert document.canonical_url is None
    assert document.original_text == "完整通讯正文"
    assert document.metadata == {"provider": "tushare", "source": "新华社", "title": "测试通讯", "rawPubTime": "2026-09-05 22:00:00"}
    assert "announcements" in " ".join(adapter.coverage.limitations)


def test_saturated_result_recursively_splits_second_intervals_without_last_timestamp_decrement():
    calls: list[tuple[str, str]] = []

    def request_callable(payload):
        params = payload["params"]
        interval = (params["start_date"], params["end_date"])
        calls.append(interval)
        if interval == ("2026-09-07 09:00:00", "2026-09-07 09:00:02"):
            # TuShare probing has returned 800 despite documentation saying 400 max;
            # both values must hit the conservative >=400 saturation path.
            return _body([_record("2026-09-07 09:00:00", title=f"full-{index}") for index in range(SATURATION_RECORDS * 2)])
        return _body([_record(interval[0], title=f"{interval[0]}-{interval[1]}")])

    window = morning_window(previous_trading_day=date(2026, 9, 4), observation_day=date(2026, 9, 7))
    # Use a focused inclusive sub-window to make every second observable in the assertion.
    window = type(window)(kind="morning", start_at=_moment(7, 9), cutoff_at=_moment(7, 9, 0, 2), start_inclusive=True, cutoff_inclusive=True)
    result = TuShareMajorNewsAdapter(token="runtime-token", request_callable=request_callable, clock=lambda: _moment(7, 10)).fetch_incremental(_request(window=window))

    assert result.complete is True
    assert calls == [
        ("2026-09-07 09:00:00", "2026-09-07 09:00:02"),
        ("2026-09-07 09:00:00", "2026-09-07 09:00:01"),
        ("2026-09-07 09:00:02", "2026-09-07 09:00:02"),
    ]
    assert any(document.published_at == _moment(7, 9, 0, 2) for document in result.documents)


def test_same_second_saturation_is_partial_and_never_advances_watermark():
    def request_callable(_payload):
        return _body([_record("2026-09-07 09:00:00", title=f"same-second-{index}") for index in range(SATURATION_RECORDS)])

    window = morning_window(previous_trading_day=date(2026, 9, 4), observation_day=date(2026, 9, 7))
    window = type(window)(kind="morning", start_at=_moment(7, 9), cutoff_at=_moment(7, 9), start_inclusive=True, cutoff_inclusive=True)
    result = TuShareMajorNewsAdapter(token="runtime-token", request_callable=request_callable, clock=lambda: _moment(7, 10)).fetch_incremental(_request(window=window))

    assert result.complete is False
    assert result.success_watermark is None
    assert result.exhausted is False
    assert result.errors == ("same_second_saturated",)
    assert len(result.documents) == SATURATION_RECORDS


def test_request_bound_and_transport_failure_are_partial_without_leaking_token_or_upstream_body():
    def saturated(_payload):
        return _body([_record(title=str(index)) for index in range(SATURATION_RECORDS)])

    bounded = TuShareMajorNewsAdapter(token="do-not-leak", request_callable=saturated, request_bound=1)
    bounded_result = bounded.fetch_incremental(_request())
    assert bounded_result.errors == ("request_bound_reached",)
    assert bounded_result.success_watermark is None

    def rejected(_payload):
        return {"code": -2001, "msg": "do-not-leak: denied", "data": None}

    failed = TuShareMajorNewsAdapter(token="do-not-leak", request_callable=rejected).fetch_incremental(_request())
    assert failed.errors == ("upstream_rejected",)
    assert "do-not-leak" not in " ".join(failed.errors)
    assert failed.documents == ()


def test_empty_result_is_a_complete_empty_communications_window_not_an_error():
    result = TuShareMajorNewsAdapter(token="runtime-token", request_callable=lambda _payload: _body([])).fetch_incremental(_request())
    assert result.complete is True
    assert result.documents == ()
    assert result.success_watermark == _moment(7, 9)


def test_content_correction_keeps_stable_identity_without_inventing_url():
    responses = iter([
        _body([_record(content="第一版")]),
        _body([_record(content="更正后的第二版")]),
    ])
    adapter = TuShareMajorNewsAdapter(token="runtime-token", request_callable=lambda _payload: next(responses), clock=lambda: _moment(7, 10))
    first = adapter.fetch_incremental(_request())
    second = adapter.fetch_incremental(_request())

    assert first.documents[0].external_id == second.documents[0].external_id
    assert first.documents[0].original_text != second.documents[0].original_text
    assert first.documents[0].canonical_url is None


def test_missing_window_start_is_not_silently_expanded_to_history():
    evening = evening_window(trading_day=date(2026, 9, 7), source_success_watermark=None)
    called = False

    def source(_payload):
        nonlocal called
        called = True
        return _body([])

    result = TuShareMajorNewsAdapter(token="runtime-token", request_callable=source).fetch_incremental(_request(window=evening))
    assert result.errors == ("window_start_missing",)
    assert result.success_watermark is None
    assert called is False
