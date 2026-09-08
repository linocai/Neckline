"""Offline regressions for bounded K10 material understanding."""
import json
from datetime import datetime
from pathlib import Path

from neckline.k10.discovery import DiscoveryDocument
from neckline.k10.pipeline import DeepSeekDiscoveryModel
from neckline.k10.pipeline import _CheckpointedDiscoveryModel, PipelineError
from neckline.k10 import store
from neckline.k10.schema import initialize_schema
from neckline.llm.base import LLMResult
from tests.k10_v305_fixture import append_approved_execution_profile
import pytest


def test_empty_lightweight_events_do_not_request_full_text():
    class Provider:
        def __init__(self): self.calls = []
        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return LLMResult(ok=True, content='{"events":[],"needsFullText":false}',
                             provider="fixture", model="deepseek-v4-pro")

    provider = Provider()
    policy = json.loads((Path(__file__).parents[1] / "neckline/config/k10-execution-v1.json").read_text())["discovery"]
    policy["keyPassageMaxCharacters"] = 12
    model = DeepSeekDiscoveryModel(provider)
    model.set_execution_policy(policy)
    document = DiscoveryDocument("long-source", 1, "2026-09-07T20:00:00+08:00",
        "2026-09-07T20:01:00+08:00", "无当前新增事件的长背景资料\n" * 100, None, {})
    assert model.understand(document=document) == ()
    assert len(provider.calls) == 1
    assert model.full_text_used(document=document) is False


class FactProvider:
    def __init__(self, *, full=False):
        self.calls = []
        self.full = full

    def chat(self, messages, **kwargs):
        content = messages[-1].content
        payload = json.loads(content.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])
        self.calls.append(payload)
        return LLMResult(ok=True, content=json.dumps({"events": [], "needsFullText": self.full}),
                         provider="fixture", model="deepseek-v4-pro", prompt_tokens=7,
                         completion_tokens=3, total_tokens=10, usage_unavailable=False)


def bound_model(path, provider, *, task, cutoff, allow_full=True, tune=None):
    now = "2026-09-08T21:00:00+08:00"
    cfg, rev = append_approved_execution_profile(db_path=path, created_at=now, config_id="shared-template")
    profile = store.read_execution_config(config_id=cfg, revision=rev, db_path=path)
    profile["payload"]["discovery"]["packageLimits"]["fullTextEnabled"] = allow_full
    if tune is not None:
        tune(profile["payload"])
    rev = store.append_execution_config(config_id=cfg, payload=profile["payload"], created_at=now, db_path=path)
    store.enqueue_task(task_id=task, kind="evening_scan", idempotency_key=task, input_version="fixture",
                       input_cutoff_at=cutoff.isoformat(), payload={}, budget={"maxAttempts": 1}, created_at=now, db_path=path)
    binding = store.bind_task_execution(task_id=task, execution_config_id=cfg, execution_config_revision=rev,
        binding_kind="scheduled", bound_at=now, db_path=path)
    base = DeepSeekDiscoveryModel(provider)
    base.set_execution_policy(binding["payload"]["discovery"])
    base.set_scan_cutoff(cutoff)
    return _CheckpointedDiscoveryModel(base=base, task_id=task, execution_profile=binding,
        cutoff_at=cutoff, db_path=path, leaseguard=None)


def test_source_facts_reuse_later_cutoff_but_invalidate_changed_material(tmp_path):
    path = tmp_path / "facts.sqlite"
    initialize_schema(path)
    provider = FactProvider()
    cutoff = datetime.fromisoformat("2026-09-07T21:00:00+08:00")
    document = DiscoveryDocument("source", 1, "2026-09-07T20:00:00+08:00",
        "2026-09-07T20:01:00+08:00", "原始披露事实", None, {})
    first = bound_model(path, provider, task="first", cutoff=cutoff)
    assert first.understand(document=document) == ()
    later = bound_model(path, provider, task="second", cutoff=datetime.fromisoformat("2026-09-08T21:00:00+08:00"))
    assert later.understand(document=document) == ()
    # Only content/rule/model/prompt inputs identify the facts, never the task ID.
    assert len(provider.calls) == 1 and later.fact_cache_hits == 1
    assert "scanCutoffAt" not in provider.calls[0]["publicationContext"]
    assert "knownEvents" not in provider.calls[0]
    changed = DiscoveryDocument("source", 2, document.published_at, document.fetched_at,
                                "更正：原始披露金额发生变化", None, {})
    assert later.understand(document=changed) == ()
    assert len(provider.calls) == 2


def test_explicit_full_text_request_cannot_bypass_disabled_budget(tmp_path):
    path = tmp_path / "no-full.sqlite"
    initialize_schema(path)
    provider = FactProvider(full=True)
    model = bound_model(path, provider, task="no-full", cutoff=datetime.fromisoformat("2026-09-07T21:00:00+08:00"), allow_full=False)
    document = DiscoveryDocument("source", 1, "2026-09-07T20:00:00+08:00",
        "2026-09-07T20:01:00+08:00", "资料原始段落\n" * 500, None, {})
    with pytest.raises(PipelineError) as error:
        model.understand(document=document)
    assert error.value.code == "full_text_disabled" and len(provider.calls) == 1


def test_metered_understanding_uses_mock_http_once_then_budget_blocks_and_cache_is_free(tmp_path):
    """Exercise the concrete transport adapter, not a replacement of its chat method."""
    import httpx
    from neckline.k10.metering import MeteredProvider
    path = tmp_path / "transport.sqlite"
    initialize_schema(path)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
            "content": '{"events":[],"needsFullText":false}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})

    transport = httpx.MockTransport(respond)

    class OfflineProvider(MeteredProvider):
        def chat(self, *args, **kwargs):
            return super().chat(*args, **kwargs, transport=transport)

    provider = OfflineProvider(ledger_db=path, ledger_task="discovery", api_key="fixture",
                               name="fixture", model="deepseek-v4-pro", api_url="https://fixture.invalid/chat/completions")

    def tune(payload):
        budgets = payload["discovery"]["budgets"]
        for limit in [budgets["round"], *budgets["stages"].values(), *budgets["reservation"].values()]:
            limit.update(maxInputTokens=8000, maxOutputTokens=100, maxTotalTokens=8100, maxModelCalls=1)

    cutoff = datetime.fromisoformat("2026-09-07T21:00:00+08:00")
    model = bound_model(path, provider, task="transport", cutoff=cutoff, tune=tune)
    store.set_run_control(state="open", reason_code="offline_fixture", changed_at=cutoff.isoformat(), changed_by="test", db_path=path)
    document = DiscoveryDocument("source-a", 1, cutoff.isoformat(), cutoff.isoformat(), "原始披露事实", None, {})
    assert model.understand(document=document) == ()
    assert model.understand(document=document) == ()
    changed = DiscoveryDocument("source-b", 1, cutoff.isoformat(), cutoff.isoformat(), "另一条独立资料", None, {})
    with pytest.raises(PipelineError) as error:
        model.understand(document=changed)
    assert error.value.code == "pending_budget"
    assert len(requests) == 1 and model.fact_cache_hits == 1
    assert requests[0]["model"] == "deepseek-v4-pro" and requests[0]["max_tokens"] == 20
    budget = store.execution_budget_snapshot(task_id="transport", db_path=path)
    assert budget["reservationCount"] == 1 and budget["actual"]["calls"] == 1
    assert budget["actual"]["totalTokens"] == 10
