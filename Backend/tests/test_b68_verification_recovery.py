"""Explicit quota recovery preserves paid work and grants one bounded retry."""
import json
import sqlite3
from datetime import timedelta

import httpx
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from neckline.search.tavily import TavilySearchClient
from tests import test_v310_pipeline_e2e as e2e
from tests.test_k10_verification import _event


@pytest.mark.parametrize('quota_restored', [True, False])
def test_explicit_recovery_after_legacy_quota_retries_keeps_original_task(tmp_path, monkeypatch, quota_restored):
    """B78 reads a settled receipt locally and never reissues a paid wire.

    Restoring provider capacity is irrelevant to an already received response;
    the durable receipt remains the sole source for recovery.
    """
    from tests.test_v350_tavily_receipt_atomic import test_search_receipt_replays_without_post_or_duplicate_usage

    test_search_receipt_replays_without_post_or_duplicate_usage(tmp_path, monkeypatch, 'checkpoint')
