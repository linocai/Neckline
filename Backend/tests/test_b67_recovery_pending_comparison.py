from datetime import timedelta
import sqlite3

from neckline.k10 import pipeline, store
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e


def test_recovery_preserves_completed_labeled_unverified_comparison(tmp_path, monkeypatch):
    from tests.test_v350_research_round import test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round

    test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round(tmp_path)
