from pathlib import Path

from neckline.db import init_schema, readonly_connection
from neckline.k10.metering import MeteredProvider
from neckline.llm.base import LLMResult
from neckline.llm.openai_compat import OpenAICompatProvider
from neckline.llm.usage import summary


def test_model_success_and_failure_record_actual_usage_without_materials(tmp_path: Path, monkeypatch):
    db = tmp_path / "metering.sqlite"
    init_schema(db)
    outputs = iter([
        LLMResult(ok=True, content="synthetic private response", provider="fixture", model="deepseek-v4-pro",
                  prompt_tokens=8, completion_tokens=4, total_tokens=12, usage_unavailable=False),
        LLMResult(ok=False, reason="Authorization: secret", provider="fixture", model="deepseek-v4-pro"),
    ])
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: next(outputs))
    provider = MeteredProvider(ledger_db=db, ledger_task="analysis", api_key="synthetic-key",
                              model="deepseek-v4-pro", name="fixture", api_url="https://api.deepseek.com/chat/completions")
    assert provider.chat([]).ok
    assert not provider.chat([]).ok
    with readonly_connection(db) as conn:
        rows = conn.execute("SELECT task,prompt_tokens,completion_tokens,total_tokens,failure_reason FROM llm_usage_events ORDER BY id").fetchall()
        assert rows[0] == ("analysis", 8, 4, 12, None)
        assert rows[1][4] == "provider_call_failed"
        assert "secret" not in str(rows)
    result = summary(db_path=db)
    assert result["totals"]["calls"] == 2
