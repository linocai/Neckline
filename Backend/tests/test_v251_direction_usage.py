"""V2.5.1 方向 sidecar 与去敏用量账的边界。"""

from __future__ import annotations

import inspect
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from neckline.db import init_schema
from neckline.facts import direction_llm, direction_store
from neckline.llm.base import LLMResult, SearchHit
from neckline.llm import usage


class _Provider:
    name = "deepseek"
    model = "deepseek-chat"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, *_args, **_kwargs) -> LLMResult:
        self.calls += 1
        return LLMResult(
            ok=True,
            provider=self.name,
            model=self.model,
            content=("市场成交活跃，资金聚焦于有业绩支撑的方向。\n"
                     "```json\n{\"themes\":[{\"name\":\"医药\",\"reason\":\"行业活跃\"},"
                     "{\"name\":\"科技\",\"reason\":\"成交回升\"}]}\n```"),
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            search_hits=[SearchHit(title="公开资料")],
            tavily_credits=1,
        )


class _SlowProvider(_Provider):
    """让两个 worker 同时争抢，验证 claim 在外部调用之前已经落下。"""

    def __init__(self, entered: threading.Event) -> None:
        super().__init__()
        self.entered = entered

    def chat(self, *_args, **_kwargs) -> LLMResult:
        self.calls += 1
        self.entered.set()
        time.sleep(0.08)
        return LLMResult(
            ok=True, provider=self.name, model=self.model,
            content=("市场成交活跃，资金聚焦于有业绩支撑的方向。\n"
                     "```json\n{\"themes\":[{\"name\":\"医药\",\"reason\":\"行业活跃\"},"
                     "{\"name\":\"科技\",\"reason\":\"成交回升\"}]}\n```"),
            prompt_tokens=10, completion_tokens=20, total_tokens=30,
            search_hits=[SearchHit(title="公开资料")], tavily_credits=1,
        )


def _pack() -> SimpleNamespace:
    return SimpleNamespace(
        pack_id="pack-v251-test",
        trade_date=date(2026, 8, 21),
        market={"limitMap": {"limitUpCount": 42, "limitDownCount": 3}},
    )


def test_direction_is_once_per_pack_and_stays_outside_k9(tmp_path: Path):
    db = tmp_path / "direction.db"
    init_schema(db)
    provider = _Provider()

    first = direction_llm.run_once(_pack(), provider=provider, db_path=db)
    again = direction_llm.run_once(_pack(), provider=provider, db_path=db)

    assert first["state"] == "ready"
    assert len(first["themes"]) == 2
    assert again == first
    assert provider.calls == 1
    source = inspect.getsource(direction_llm)
    assert "neckline.k9" not in source
    assert "neckline.explain" not in source
    assert "neckline.playbook" not in source


def test_direction_failure_is_terminal_and_usage_is_deidentified(tmp_path: Path):
    db = tmp_path / "usage.db"
    init_schema(db)

    out = direction_llm.run_once(_pack(), provider=None, db_path=db)
    row = direction_store.load("pack-v251-test", db_path=db)
    summary = usage.summary(days=5, db_path=db)

    assert out["state"] == "unavailable"
    assert row and row["failureReason"]
    task = summary["days"][0]["tasks"][0]
    assert task["task"] == "market_direction"
    assert task["calls"] == 1 and task["usageUnavailable"] == 1
    assert "failureReason" not in task
    assert set(task) == {"task", "calls", "failed", "usageUnavailable", "promptTokens",
                         "completionTokens", "totalTokens", "tavilyCredits", "durationMs"}


def test_direction_claim_allows_exactly_one_external_call_and_one_usage_row(tmp_path: Path):
    """并发重复晚间任务不能双打 provider/Tavily，也不能把费用记两次。"""
    db = tmp_path / "direction-race.db"
    init_schema(db)
    entered = threading.Event()
    provider = _SlowProvider(entered)
    results = []

    def worker() -> None:
        results.append(direction_llm.run_once(_pack(), provider=provider, db_path=db))

    first = threading.Thread(target=worker)
    second = threading.Thread(target=worker)
    first.start()
    assert entered.wait(timeout=1), "第一个线程未进入 provider"
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert provider.calls == 1
    assert direction_store.load("pack-v251-test", db_path=db)["state"] == "ready"
    assert {row["state"] for row in results} <= {"ready", "running"}
    task = usage.summary(days=1, db_path=db)["days"][0]["tasks"][0]
    assert task["task"] == "market_direction"
    assert task["calls"] == 1 and task["tavilyCredits"] == 1


def test_usage_keeps_real_credits_when_reasoning_failed(tmp_path: Path):
    db = tmp_path / "credits.db"
    init_schema(db)
    usage.record(
        task="news_scan", trade_date=date(2026, 8, 21), pack_id="pack-credits",
        result=LLMResult(ok=False, reason="model_failed", provider="deepseek", model="chat"),
        searched=True, tavily_credits=1, db_path=db,
    )
    task = usage.summary(days=1, db_path=db)["days"][0]["tasks"][0]
    assert task["failed"] == 1
    assert task["tavilyCredits"] == 1
    assert task["totalTokens"] is None
