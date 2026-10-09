"""Reproduce a pre-B97 terminal title failure for paid-receipt recovery tests."""
from contextlib import contextmanager
from neckline.k10 import title_runtime


@contextmanager
def legacy_title_failure(monkeypatch):
    # Only the first execution emulates the historical parser/control boundary.
    # Recovery below the context always runs the current production handler.
    with monkeypatch.context() as old:
        old.setattr(title_runtime, "isolate_reconcile_response",
                    lambda *args, **kwargs: (title_runtime.normalize_reconcile_result(*args, **kwargs), []))
        old.setattr(title_runtime, "_local_failure_code", lambda _exc: None)
        yield
