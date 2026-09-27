"""B91 parent-review catalogue survives concurrent discovery checkpoints."""
from __future__ import annotations

from threading import Event, current_thread

from neckline.k10 import pipeline
from tests.test_b91_morning_catalogue_resume import (
    test_same_task_resume_keeps_frozen_review_catalogue,
)
from tests.test_b91_morning_evidence import (
    test_b91_real_morning_reads_non_named_shared_original_and_preserves_all_parent_reasons,
)


def _force_discovery_to_checkpoint_a_stale_coverage(monkeypatch):
    discovery_has_stale_coverage = Event()
    allow_discovery_checkpoint = Event()
    intercepted = {"value": False}
    original_docs = pipeline._docs_for_window
    original_sources = pipeline._b90_morning_review_sources
    original_freeze = pipeline._b90_frozen_morning_review_catalogue

    def docs_barrier(*args, **kwargs):
        if (current_thread().name.startswith("k10-morning-discovery")
                and not intercepted["value"]):
            intercepted["value"] = True
            discovery_has_stale_coverage.set()
            assert allow_discovery_checkpoint.wait(10), "review never froze its catalogue"
        return original_docs(*args, **kwargs)

    def sources_after_discovery_read(*args, **kwargs):
        assert discovery_has_stale_coverage.wait(10), "discovery never reached stale-coverage barrier"
        return original_sources(*args, **kwargs)

    def freeze_then_release(*args, **kwargs):
        value = original_freeze(*args, **kwargs)
        allow_discovery_checkpoint.set()
        return value

    monkeypatch.setattr(pipeline, "_docs_for_window", docs_barrier)
    monkeypatch.setattr(pipeline, "_b90_morning_review_sources", sources_after_discovery_read)
    monkeypatch.setattr(pipeline, "_b90_frozen_morning_review_catalogue", freeze_then_release)


def test_b91_parallel_discovery_cannot_erase_frozen_catalogue(tmp_path, monkeypatch):
    """A recovery uses the original work-item hash after forced interleaving."""
    _force_discovery_to_checkpoint_a_stale_coverage(monkeypatch)
    test_same_task_resume_keeps_frozen_review_catalogue(tmp_path, monkeypatch)


def test_b91_parallel_discovery_keeps_parent_for_same_run_publication(tmp_path, monkeypatch):
    """The same parent run retains its catalogue through final publication."""
    _force_discovery_to_checkpoint_a_stale_coverage(monkeypatch)
    test_b91_real_morning_reads_non_named_shared_original_and_preserves_all_parent_reasons(tmp_path, monkeypatch)
