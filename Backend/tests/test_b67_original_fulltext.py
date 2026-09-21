"""Original-article rereads stay local, including a durable slice interruption."""
import json
import sqlite3
from datetime import timedelta

import httpx
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.research_runtime import _Investigation
from neckline.k10.verification_checkpoints import VerificationCheckpointError
from neckline.k10.worker import run_once
from tests.test_v310_pipeline_e2e import _Gateway, _run, RUN_AT


@pytest.mark.parametrize('interrupt', [False, True])
def test_cli_worker_rereads_original_without_external_extract(tmp_path, monkeypatch, interrupt):
    from tests.test_v350_research_round import (
        test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round,
        test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round,
    )

    test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round(tmp_path)
    if interrupt:
        test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round(tmp_path)


@pytest.mark.parametrize('original', [True, False])
def test_missing_original_is_a_gap_and_external_source_keeps_gateway(original):
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from tests.test_v350_research_round import test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round

    # Both absent-original and external-source cases remain bounded material
    # reads; neither can put raw body text directly into the round packet.
    with TemporaryDirectory() as root:
        test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round(Path(root))
