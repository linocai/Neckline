import copy
import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from neckline.k10 import pipeline, research_runtime, store
from neckline.k10.verification import VerificationEvidenceBundle
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses


@pytest.mark.parametrize('interrupt', [False, True])
def test_real_worker_batches_admitted_requests_and_resumes_between_writes(tmp_path, monkeypatch, interrupt):
    """B78 admits the body, exposes only a requested locator, then resumes.

    A durable direct receipt is rebuilt locally after an interruption; no old
    plan/assess compound stage is re-posted.
    """
    from tests.test_v350_research_round import (
        test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round,
        test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round,
    )

    test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round(tmp_path)
    if interrupt:
        test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round(tmp_path)
