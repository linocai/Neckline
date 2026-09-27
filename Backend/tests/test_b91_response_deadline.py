"""A helper-thread deadline narrows the actual HTTP request without changing frozen limits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Barrier

import httpx
import pytest

from neckline.llm import openai_compat as compat


def _request_timeouts(duration, configured, barrier=None):
    seen = []

    def wire(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"fixture": "received"})

    provider = compat.OpenAICompatProvider(
        api_key="fixture", model="fixture", api_url="https://fixture.invalid/chat/completions")
    with httpx.Client(transport=httpx.MockTransport(wire), timeout=configured) as client:
        with compat.bounded_response_wait(duration) if duration is not None else nullcontext():
            if barrier is not None:
                barrier.wait(timeout=5)
            assert provider._attempt_post(client, {"messages": []}) == ({"fixture": "received"}, None)
        # The scope must not leak its deadline into the next request.
        assert provider._attempt_post(client, {"messages": []}) == ({"fixture": "received"}, None)
    return seen


@pytest.mark.parametrize("duration, expected", [
    (5.0, {"connect": 2.0, "read": 1.0, "write": 4.0, "pool": 5.0}),
    (0.25, {"connect": 0.25, "read": 0.25, "write": 0.25, "pool": 0.25}),
    (None, {"connect": 2.0, "read": 1.0, "write": 4.0, "pool": None}),
])
def test_review_thread_preserves_each_frozen_http_phase_timeout(monkeypatch, duration, expected):
    monkeypatch.setattr(compat.time, "monotonic", lambda: 100.0)
    configured = httpx.Timeout(connect=2, read=1, write=4, pool=None)
    with ThreadPoolExecutor(max_workers=1) as executor:
        requests = executor.submit(_request_timeouts, duration, configured).result(timeout=5)
    assert requests == [expected, configured.as_dict()]


def test_parallel_company_deadlines_do_not_change_each_other(monkeypatch):
    monkeypatch.setattr(compat.time, "monotonic", lambda: 100.0)
    configured = httpx.Timeout(None)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_request_timeouts, duration, configured, barrier) for duration in (2.0, 7.0)]
        requests = [future.result(timeout=5) for future in futures]
    for duration, observed in zip((2.0, 7.0), requests):
        assert observed == [{phase: duration for phase in ("connect", "read", "write", "pool")}, configured.as_dict()]
