"""Actual provider usage accounting; no estimates, prices, keys or prompt text."""
from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

from neckline.llm.openai_compat import OpenAICompatProvider
from neckline.llm import usage

logger = logging.getLogger(__name__)


class MeteredProvider(OpenAICompatProvider):
    def __init__(self, *, ledger_db: Path, ledger_task: str, **kwargs):
        super().__init__(**kwargs)
        self._ledger_db, self._ledger_task = ledger_db, ledger_task

    def chat(self, *args, **kwargs):
        started = monotonic()
        result = None
        try:
            result = super().chat(*args, **kwargs)
            return result
        finally:
            try:
                usage.record(
                    task=self._ledger_task, result=result,
                    report_date=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
                    duration_ms=max(0, round((monotonic() - started) * 1000)),
                    failure_reason=None if result is not None and result.ok else "provider_call_failed",
                    db_path=self._ledger_db,
                )
            except Exception as exc:
                # Completed analysis still carries the provider's actual usage;
                # a ledger fault must not cause a second billable model call.
                logger.warning("K10 usage ledger unavailable (%s)", type(exc).__name__)
