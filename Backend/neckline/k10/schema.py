"""K10 SQLite schema 的显式、可回滚迁移入口。"""

from __future__ import annotations

import sqlite3
from hashlib import sha256
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 8


class K10SchemaError(RuntimeError):
    pass


class SchemaUnavailable(K10SchemaError):
    """读取了尚未经 K10 受控迁移的数据库。"""


_V1 = """
CREATE TABLE k10_run_config_revisions (
  config_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  payload_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (config_id, revision),
  UNIQUE (config_id, content_sha256)
);

CREATE TABLE k10_source_documents (
  document_id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL,
  external_id TEXT NOT NULL,
  canonical_url TEXT,
  first_seen_at TEXT NOT NULL,
  UNIQUE(source_key, external_id)
);
CREATE TABLE k10_source_document_versions (
  document_id TEXT NOT NULL REFERENCES k10_source_documents(document_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  content_sha256 TEXT NOT NULL,
  published_at TEXT,
  published_precision TEXT NOT NULL CHECK(published_precision IN ('exact','date','unknown')),
  fetched_at TEXT NOT NULL,
  original_text TEXT,
  excerpt TEXT,
  fetch_version TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(document_id, revision),
  UNIQUE(document_id, content_sha256)
);
CREATE INDEX idx_k10_document_versions_fetched ON k10_source_document_versions(fetched_at);

CREATE TABLE k10_events (
  event_id TEXT PRIMARY KEY,
  stable_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
CREATE TABLE k10_event_revisions (
  event_id TEXT NOT NULL REFERENCES k10_events(event_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  headline TEXT NOT NULL,
  event_kind TEXT NOT NULL,
  facts_json TEXT NOT NULL,
  source_refs_json TEXT NOT NULL,
  supersedes_revision INTEGER,
  created_at TEXT NOT NULL,
  PRIMARY KEY(event_id, revision)
);
CREATE TABLE k10_company_mappings (
  mapping_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  event_revision INTEGER NOT NULL,
  company_code TEXT NOT NULL,
  affected_stage TEXT NOT NULL,
  relation_evidence_json TEXT NOT NULL,
  inference_json TEXT NOT NULL,
  uncertainty TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(event_id, event_revision) REFERENCES k10_event_revisions(event_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_mappings_event ON k10_company_mappings(event_id, event_revision);

CREATE TABLE k10_scans (
  scan_id TEXT PRIMARY KEY,
  window_kind TEXT NOT NULL CHECK(window_kind IN ('evening','morning')),
  cutoff_at TEXT NOT NULL,
  config_id TEXT,
  config_revision INTEGER,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','partial','failed','not_configured')),
  coverage_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(config_id, config_revision) REFERENCES k10_run_config_revisions(config_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_scans_window ON k10_scans(window_kind, cutoff_at DESC);
CREATE TABLE k10_source_watermark_updates (
  watermark_id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL,
  cursor_value TEXT,
  success_cutoff_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  scan_id TEXT REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_watermark_source ON k10_source_watermark_updates(source_key, created_at DESC);

CREATE TABLE k10_candidates (
  candidate_id TEXT PRIMARY KEY,
  scan_id TEXT NOT NULL REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  event_id TEXT NOT NULL,
  event_revision INTEGER NOT NULL,
  company_code TEXT NOT NULL,
  comparison_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, event_id, event_revision, company_code),
  FOREIGN KEY(event_id, event_revision) REFERENCES k10_event_revisions(event_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_candidates_scan ON k10_candidates(scan_id, created_at);
CREATE TABLE k10_candidate_actions (
  action_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  action TEXT NOT NULL CHECK(action IN ('observe','skip','restore','withdraw')),
  idempotency_key TEXT NOT NULL UNIQUE,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_actions_candidate ON k10_candidate_actions(candidate_id, created_at);
CREATE TABLE k10_observations (
  observation_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  created_from_action_id TEXT NOT NULL UNIQUE REFERENCES k10_candidate_actions(action_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_observations_candidate ON k10_observations(candidate_id, created_at);

CREATE TABLE k10_analysis_revisions (
  analysis_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  analysis_kind TEXT NOT NULL CHECK(analysis_kind IN ('pro','con','morning')),
  input_cutoff_at TEXT NOT NULL,
  input_lineage_json TEXT NOT NULL,
  content_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','completed','partial','failed','not_configured')),
  created_at TEXT NOT NULL,
  UNIQUE(observation_id, revision, analysis_kind)
);
CREATE TABLE k10_plan_revisions (
  plan_revision_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  intent TEXT NOT NULL CHECK(intent IN ('draft','confirm','amend','abandon')),
  plan_json TEXT NOT NULL,
  data_cutoff_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(observation_id, revision)
);
CREATE TABLE k10_plan_commands (
  command_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE,
  request_json TEXT NOT NULL,
  plan_revision_id TEXT NOT NULL UNIQUE REFERENCES k10_plan_revisions(plan_revision_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);
CREATE TABLE k10_morning_updates (
  update_id TEXT PRIMARY KEY,
  observation_id TEXT REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  candidate_id TEXT REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  cutoff_at TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK(observation_id IS NOT NULL OR candidate_id IS NOT NULL)
);
CREATE TABLE k10_evaluation_records (
  evaluation_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  policy_version TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE k10_tasks (
  task_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  input_version TEXT NOT NULL,
  input_cutoff_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','not_configured','cancelled')),
  stage TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  error_text TEXT,
  budget_json TEXT NOT NULL,
  checkpoint_json TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_k10_tasks_claim ON k10_tasks(status, lease_until, created_at);
CREATE TABLE k10_task_outbox (
  outbox_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL UNIQUE REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL,
  dispatched_at TEXT
);
"""


# V1.4 opportunity lifecycle.  These records are intentionally separate from a scan
# candidate: candidates are discovery snapshots; an opportunity starts only when a
# complete publication batch becomes visible.
_V2 = r"""
DROP TABLE IF EXISTS k10_plan_commands;
DROP TABLE IF EXISTS k10_plan_revisions;
DROP TABLE IF EXISTS k10_evaluation_records;

CREATE TABLE k10_publication_batches (
  batch_id TEXT PRIMARY KEY,
  scan_id TEXT NOT NULL REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  publication_kind TEXT NOT NULL CHECK(publication_kind IN ('evening','morning')),
  available_at TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, publication_kind)
);
CREATE INDEX idx_k10_batches_available ON k10_publication_batches(available_at, batch_id);

CREATE TABLE k10_company_windows (
  company_window_id TEXT PRIMARY KEY,
  company_code TEXT NOT NULL,
  first_batch_id TEXT NOT NULL REFERENCES k10_publication_batches(batch_id) ON DELETE RESTRICT,
  d0_trade_date TEXT NOT NULL,
  d1_trade_date TEXT NOT NULL,
  d2_trade_date TEXT NOT NULL,
  d1_selection_at TEXT NOT NULL,
  d2_close_at TEXT NOT NULL,
  sample_class TEXT NOT NULL CHECK(sample_class IN ('primary','overlap')),
  overlaps_window_id TEXT REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL,
  UNIQUE(company_code, first_batch_id),
  CHECK((sample_class='primary' AND overlaps_window_id IS NULL) OR
        (sample_class='overlap' AND overlaps_window_id IS NOT NULL))
);
CREATE INDEX idx_k10_windows_company_d1 ON k10_company_windows(company_code, d1_trade_date, d2_trade_date);

CREATE TABLE k10_opportunities (
  opportunity_id TEXT PRIMARY KEY,
  opportunity_key TEXT NOT NULL UNIQUE,
  company_code TEXT NOT NULL,
  catalyst_event_id TEXT NOT NULL,
  catalyst_event_revision INTEGER NOT NULL,
  catalyst_stage TEXT NOT NULL,
  related_opportunity_id TEXT REFERENCES k10_opportunities(opportunity_id) ON DELETE RESTRICT,
  first_batch_id TEXT NOT NULL REFERENCES k10_publication_batches(batch_id) ON DELETE RESTRICT,
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(catalyst_event_id, catalyst_event_revision)
    REFERENCES k10_event_revisions(event_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_opportunities_company ON k10_opportunities(company_code, first_batch_id);

CREATE TABLE k10_publication_samples (
  sample_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES k10_publication_batches(batch_id) ON DELETE RESTRICT,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  opportunity_id TEXT NOT NULL REFERENCES k10_opportunities(opportunity_id) ON DELETE RESTRICT,
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  event_id TEXT NOT NULL,
  event_revision INTEGER NOT NULL,
  company_code TEXT NOT NULL,
  category TEXT NOT NULL,
  source_marker TEXT NOT NULL,
  comparison_json TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  rank INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(batch_id, candidate_id),
  UNIQUE(batch_id, opportunity_id),
  FOREIGN KEY(event_id, event_revision) REFERENCES k10_event_revisions(event_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_samples_window ON k10_publication_samples(company_window_id, batch_id);

CREATE TABLE k10_opportunity_lifecycle_events (
  lifecycle_event_id TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES k10_opportunities(opportunity_id) ON DELETE RESTRICT,
  kind TEXT NOT NULL CHECK(kind IN ('published','evidence_update','risk','withdrawal','expired')),
  reason TEXT,
  source_refs_json TEXT NOT NULL,
  content_json TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_lifecycle_opportunity ON k10_opportunity_lifecycle_events(opportunity_id, occurred_at);

CREATE TABLE k10_company_window_selection_snapshots (
  company_window_id TEXT PRIMARY KEY REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK(state IN ('selected','skipped','unhandled')),
  action_ids_json TEXT NOT NULL,
  frozen_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE k10_company_window_actions (
  action_id TEXT PRIMARY KEY,
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  candidate_action_id TEXT NOT NULL UNIQUE REFERENCES k10_candidate_actions(action_id) ON DELETE RESTRICT,
  action TEXT NOT NULL CHECK(action IN ('observe','skip','restore','withdraw')),
  idempotency_key TEXT NOT NULL UNIQUE,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_window_actions_window ON k10_company_window_actions(company_window_id, created_at);

CREATE TABLE k10_company_window_observations (
  company_window_id TEXT PRIMARY KEY REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  observation_id TEXT NOT NULL UNIQUE REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  task_id TEXT NOT NULL UNIQUE REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);

CREATE TABLE k10_market_day_fact_revisions (
  company_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  availability TEXT NOT NULL CHECK(availability IN ('available','suspended','data_gap')),
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  pre_close REAL,
  limit_up_price REAL,
  close_limit_up INTEGER,
  touched_limit_up INTEGER,
  adj_factor REAL,
  metadata_json TEXT NOT NULL,
  source_refs_json TEXT NOT NULL,
  obtained_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(company_code, trade_date, revision)
);
CREATE INDEX idx_k10_market_facts_latest ON k10_market_day_fact_revisions(company_code, trade_date, revision DESC);

CREATE TABLE k10_company_window_evaluation_revisions (
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  state TEXT NOT NULL CHECK(state IN ('pending','due','completed','incomplete')),
  fact_refs_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  evaluated_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(company_window_id, revision)
);
"""


# V3 is additive: it records morning reports and user-requested analysis follow-ups without
# rewriting V2 publication, fixed-window, selection, or analysis history.
_V3 = r"""
CREATE TABLE k10_morning_reports (
  report_id TEXT PRIMARY KEY,
  scan_id TEXT NOT NULL REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  cutoff_at TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('completed','partial','failed','not_configured')),
  coverage_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, revision)
);
CREATE INDEX idx_k10_morning_reports_generated ON k10_morning_reports(scan_id, revision DESC, generated_at DESC, report_id);
CREATE TABLE k10_morning_report_items (
  report_id TEXT NOT NULL REFERENCES k10_morning_reports(report_id) ON DELETE RESTRICT,
  item_id TEXT NOT NULL,
  group_key TEXT NOT NULL CHECK(group_key IN ('major_contrary','thesis_changed','continuing_or_expiring','new','needs_review')),
  position INTEGER NOT NULL CHECK(position >= 0),
  opportunity_id TEXT REFERENCES k10_opportunities(opportunity_id) ON DELETE RESTRICT,
  company_window_id TEXT REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  status TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(report_id, item_id),
  UNIQUE(report_id, group_key, position)
);
CREATE INDEX idx_k10_morning_items_report ON k10_morning_report_items(report_id, group_key, position);

CREATE TABLE k10_analysis_requests (
  request_id TEXT PRIMARY KEY,
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  task_id TEXT NOT NULL UNIQUE REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE,
  global_revision INTEGER NOT NULL CHECK(global_revision >= 1),
  parent_revision INTEGER CHECK(parent_revision >= 1),
  kind TEXT NOT NULL CHECK(kind IN ('user_question','evidence_update')),
  question TEXT,
  source_refs_json TEXT NOT NULL,
  task_input_version TEXT NOT NULL,
  task_payload_json TEXT NOT NULL,
  task_budget_json TEXT NOT NULL,
  input_cutoff_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(observation_id, global_revision),
  CHECK((kind='user_question' AND question IS NOT NULL AND length(trim(question)) > 0) OR kind='evidence_update')
);
CREATE INDEX idx_k10_analysis_requests_window ON k10_analysis_requests(company_window_id, global_revision);
"""


# Execution settings are deliberately separate from the K10 strategy pack.  A recovery
# can bind a new, audited execution profile while retaining the original strategy
# revision, cutoff and frozen document references.
_V4 = r"""
CREATE TABLE k10_execution_config_revisions (
  config_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  payload_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (config_id, revision),
  UNIQUE (config_id, content_sha256)
);

CREATE TABLE k10_task_execution_bindings (
  task_id TEXT PRIMARY KEY REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  execution_config_id TEXT NOT NULL,
  execution_config_revision INTEGER NOT NULL,
  execution_content_sha256 TEXT NOT NULL,
  binding_kind TEXT NOT NULL CHECK(binding_kind IN ('scheduled','recovery')),
  bound_at TEXT NOT NULL,
  FOREIGN KEY(execution_config_id, execution_config_revision)
    REFERENCES k10_execution_config_revisions(config_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_task_execution_profile
  ON k10_task_execution_bindings(execution_config_id, execution_config_revision);

CREATE TABLE k10_scan_execution_bindings (
  scan_id TEXT PRIMARY KEY REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  task_id TEXT NOT NULL UNIQUE REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  execution_config_id TEXT NOT NULL,
  execution_config_revision INTEGER NOT NULL,
  execution_content_sha256 TEXT NOT NULL,
  binding_kind TEXT NOT NULL CHECK(binding_kind IN ('scheduled','recovery')),
  bound_at TEXT NOT NULL,
  FOREIGN KEY(execution_config_id, execution_config_revision)
    REFERENCES k10_execution_config_revisions(config_id, revision) ON DELETE RESTRICT
);

CREATE TABLE k10_execution_item_checkpoints (
  task_id TEXT NOT NULL REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  item_kind TEXT NOT NULL CHECK(item_kind IN ('document','event','global')),
  item_key TEXT NOT NULL,
  stage TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
  attempt_count INTEGER NOT NULL CHECK(attempt_count >= 0),
  network_attempt_count INTEGER NOT NULL CHECK(network_attempt_count >= 0),
  repair_attempt_count INTEGER NOT NULL CHECK(repair_attempt_count >= 0),
  elapsed_ms INTEGER NOT NULL CHECK(elapsed_ms >= 0),
  input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
  result_json TEXT,
  safe_error_code TEXT,
  safe_error_ref TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(task_id, item_kind, item_key, stage)
);
CREATE INDEX idx_k10_execution_items_task_status
  ON k10_execution_item_checkpoints(task_id, status, item_kind, stage);

CREATE TABLE k10_task_retry_schedules (
  task_id TEXT PRIMARY KEY REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  not_before_at TEXT NOT NULL,
  scheduled_attempt_count INTEGER NOT NULL CHECK(scheduled_attempt_count >= 1),
  failure_attempt_count INTEGER NOT NULL CHECK(failure_attempt_count >= 0),
  retry_kind TEXT NOT NULL CHECK(retry_kind IN ('continuation','failure')),
  safe_error_code TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_k10_task_retry_due ON k10_task_retry_schedules(not_before_at, task_id);
"""


# V6 removes the unpublished V3.0.5 budget/template product.  It adds only
# title-first audit records, bounded article membership, a generic attempt
# ledger and durable retirement facts.  V4 goes directly here; V5 is cleaned
# first by _apply_v6 without rewriting any business history.
_V6 = r"""
CREATE TABLE IF NOT EXISTS k10_run_controls (
  control_key TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK(state IN ('closed','open')),
  reason_code TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  changed_by TEXT NOT NULL
);
INSERT OR IGNORE INTO k10_run_controls(control_key,state,reason_code,changed_at,changed_by)
VALUES('k10_discovery','closed','unconfigured_closed','1970-01-01T00:00:00+00:00','schema-v6');

CREATE TABLE IF NOT EXISTS k10_fact_cache (
  cache_key TEXT PRIMARY KEY,
  source_refs_json TEXT NOT NULL,
  eligible_at TEXT NOT NULL,
  template_content_sha256 TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_input_sha256 TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_k10_fact_cache_eligible ON k10_fact_cache(eligible_at, cache_key);

CREATE TABLE k10_title_triage_policy_revisions (
  policy_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  content_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  approval_state TEXT NOT NULL CHECK(approval_state IN ('draft','approved','retired')),
  approved_at TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(policy_id, revision),
  UNIQUE(policy_id, content_sha256),
  CHECK((approval_state='approved' AND approved_at IS NOT NULL) OR
        (approval_state!='approved' AND approved_at IS NULL))
);

CREATE TABLE k10_title_triage_manifests (
  task_id TEXT PRIMARY KEY REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  input_manifest_sha256 TEXT NOT NULL,
  window_kind TEXT NOT NULL CHECK(window_kind IN ('evening','morning')),
  policy_id TEXT NOT NULL,
  policy_revision INTEGER NOT NULL,
  policy_content_sha256 TEXT NOT NULL,
  article_limit INTEGER NOT NULL CHECK(article_limit IN (40,80)),
  input_refs_json TEXT NOT NULL,
  batch_count INTEGER NOT NULL CHECK(batch_count >= 0),
  title_status TEXT NOT NULL CHECK(title_status IN ('frozen','partial')),
  selection_status TEXT NOT NULL CHECK(selection_status IN ('pending','frozen','partial')),
  created_at TEXT NOT NULL,
  FOREIGN KEY(policy_id, policy_revision) REFERENCES k10_title_triage_policy_revisions(policy_id, revision) ON DELETE RESTRICT
);

CREATE TABLE k10_title_triage_items (
  task_id TEXT NOT NULL REFERENCES k10_title_triage_manifests(task_id) ON DELETE RESTRICT,
  document_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  batch_index INTEGER NOT NULL CHECK(batch_index >= 0),
  disposition TEXT NOT NULL CHECK(disposition IN ('candidate','uncertain','protected','not_selected','merged','no_value','exact_duplicate')),
  matter_key TEXT,
  merged_document_id TEXT,
  merged_revision INTEGER,
  selection_rank INTEGER CHECK(selection_rank IS NULL OR selection_rank >= 1),
  audit_reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(task_id, document_id, revision),
  CHECK((merged_document_id IS NULL AND merged_revision IS NULL) OR
        (merged_document_id IS NOT NULL AND merged_revision IS NOT NULL AND merged_revision >= 1))
);
CREATE INDEX idx_k10_title_triage_items_task_rank ON k10_title_triage_items(task_id, selection_rank, document_id);

CREATE TABLE k10_title_selection_manifests (
  task_id TEXT PRIMARY KEY REFERENCES k10_title_triage_manifests(task_id) ON DELETE RESTRICT,
  selection_manifest_sha256 TEXT NOT NULL,
  selected_refs_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE k10_article_admissions (
  task_id TEXT NOT NULL REFERENCES k10_title_triage_manifests(task_id) ON DELETE RESTRICT,
  document_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  admission_kind TEXT NOT NULL CHECK(admission_kind IN ('selected','tavily_full_article')),
  state TEXT NOT NULL CHECK(state IN ('admitted','completed','missing_body','failed')),
  reason_code TEXT,
  admitted_at TEXT NOT NULL,
  completed_at TEXT,
  PRIMARY KEY(task_id, document_id, revision)
);
CREATE INDEX idx_k10_article_admissions_task_state ON k10_article_admissions(task_id, state, admission_kind);

CREATE TABLE k10_external_attempts (
  attempt_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  stage TEXT NOT NULL,
  item_key TEXT NOT NULL,
  attempt_key TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('started','succeeded','failed','unknown')),
  prompt_tokens INTEGER CHECK(prompt_tokens IS NULL OR prompt_tokens >= 0),
  completion_tokens INTEGER CHECK(completion_tokens IS NULL OR completion_tokens >= 0),
  total_tokens INTEGER CHECK(total_tokens IS NULL OR total_tokens >= 0),
  search_requests INTEGER CHECK(search_requests IS NULL OR search_requests >= 0),
  search_credits INTEGER CHECK(search_credits IS NULL OR search_credits >= 0),
  error_code TEXT,
  started_at TEXT NOT NULL,
  settled_at TEXT,
  UNIQUE(task_id, attempt_key)
);
CREATE INDEX idx_k10_external_attempts_task_state ON k10_external_attempts(task_id, state, stage);

CREATE TABLE k10_discovery_retirements (
  scan_id TEXT PRIMARY KEY REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  task_id TEXT NOT NULL UNIQUE REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  reason_code TEXT NOT NULL,
  retired_at TEXT NOT NULL
);
"""


# V7 persists the evidence-bounded investigation independently of the task
# checkpoint stream.  Snapshot revisions are append-only: a retry can prove it
# is replaying the same semantic input, and a GET can reconstruct the latest
# result without writing or reinterpreting historical K10 records.
_V7 = r"""
CREATE TABLE k10_research_snapshot_revisions (
  snapshot_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  task_id TEXT NOT NULL REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  event_id TEXT NOT NULL,
  event_revision INTEGER NOT NULL CHECK(event_revision >= 1),
  news_cutoff_at TEXT NOT NULL,
  verification_cutoff_at TEXT NOT NULL,
  context_sha256 TEXT NOT NULL,
  prompt_contract_revision TEXT NOT NULL,
  model_parameters_sha256 TEXT NOT NULL,
  research_status TEXT NOT NULL CHECK(research_status IN ('ready_for_comparison','continue_research','pending_verification','abandon_recommendation','background_only','comparison_complete')),
  execution_status TEXT NOT NULL CHECK(execution_status IN ('ok','paused','failed')),
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, revision),
  FOREIGN KEY(event_id, event_revision) REFERENCES k10_event_revisions(event_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_research_snapshots_task_latest
  ON k10_research_snapshot_revisions(task_id, snapshot_id, revision DESC);
CREATE UNIQUE INDEX idx_k10_research_snapshot_input_identity
  ON k10_research_snapshot_revisions(task_id,event_id,event_revision,context_sha256,prompt_contract_revision,model_parameters_sha256)
  WHERE revision=1;

CREATE TABLE k10_research_stage_results (
  snapshot_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 2),
  action TEXT NOT NULL CHECK(action IN ('extract_claims','plan_gaps','plan_queries','assess_evidence','close_research','compare_companies')),
  input_sha256 TEXT NOT NULL,
  result_json TEXT NOT NULL,
  safe_error_code TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, revision),
  UNIQUE(snapshot_id, action, input_sha256),
  FOREIGN KEY(snapshot_id, revision) REFERENCES k10_research_snapshot_revisions(snapshot_id, revision) ON DELETE RESTRICT
);

CREATE TABLE k10_research_claims (
  snapshot_id TEXT NOT NULL,
  snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision >= 2),
  claim_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  document_revision INTEGER NOT NULL CHECK(document_revision >= 1),
  claim_kind TEXT NOT NULL CHECK(claim_kind IN ('factual_assertion','forecast','opinion','promotion','rumor')),
  novelty TEXT NOT NULL CHECK(novelty IN ('new_fact','new_stage','background','republication','uncertain')),
  verification_status TEXT NOT NULL CHECK(verification_status IN ('verified','partially_supported','unverified','contradicted')),
  claim_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, snapshot_revision, claim_id),
  FOREIGN KEY(snapshot_id, snapshot_revision) REFERENCES k10_research_snapshot_revisions(snapshot_id, revision) ON DELETE RESTRICT,
  FOREIGN KEY(document_id, document_revision) REFERENCES k10_source_document_versions(document_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_research_claims_latest ON k10_research_claims(snapshot_id, claim_id, snapshot_revision DESC);

CREATE TABLE k10_research_evidence_links (
  snapshot_id TEXT NOT NULL,
  snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision >= 2),
  claim_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  document_revision INTEGER NOT NULL CHECK(document_revision >= 1),
  relation TEXT NOT NULL CHECK(relation IN ('supports','partially_supports','contradicts','duplicate','irrelevant','conflicts')),
  location TEXT NOT NULL,
  applicability_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, snapshot_revision, claim_id, document_id, document_revision),
  FOREIGN KEY(snapshot_id, snapshot_revision) REFERENCES k10_research_snapshot_revisions(snapshot_id, revision) ON DELETE RESTRICT,
  FOREIGN KEY(document_id, document_revision) REFERENCES k10_source_document_versions(document_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_research_evidence_claim ON k10_research_evidence_links(snapshot_id, claim_id, snapshot_revision DESC);

CREATE TABLE k10_research_questions (
  snapshot_id TEXT NOT NULL,
  snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision >= 2),
  question_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('open','answered','blocked','abandoned')),
  question_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, snapshot_revision, question_id),
  FOREIGN KEY(snapshot_id, snapshot_revision) REFERENCES k10_research_snapshot_revisions(snapshot_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_research_questions_latest ON k10_research_questions(snapshot_id, question_id, snapshot_revision DESC);

CREATE TABLE k10_research_query_paths (
  snapshot_id TEXT NOT NULL,
  snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision >= 2),
  path_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('planned','searched','no_result','blocked')),
  path_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, snapshot_revision, path_id),
  FOREIGN KEY(snapshot_id, snapshot_revision) REFERENCES k10_research_snapshot_revisions(snapshot_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_research_paths_latest ON k10_research_query_paths(snapshot_id, path_id, snapshot_revision DESC);

CREATE TABLE k10_research_fulltext_requests (
  snapshot_id TEXT NOT NULL,
  snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision >= 2),
  request_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  document_revision INTEGER NOT NULL CHECK(document_revision >= 1),
  state TEXT NOT NULL CHECK(state IN ('requested','admitted','rejected','fulfilled')),
  request_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, snapshot_revision, request_id),
  FOREIGN KEY(snapshot_id, snapshot_revision) REFERENCES k10_research_snapshot_revisions(snapshot_id, revision) ON DELETE RESTRICT,
  FOREIGN KEY(document_id, document_revision) REFERENCES k10_source_document_versions(document_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_research_fulltext_latest ON k10_research_fulltext_requests(snapshot_id, request_id, snapshot_revision DESC);

CREATE TABLE k10_research_company_assessments (
  snapshot_id TEXT NOT NULL,
  snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision >= 2),
  company_code TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('primary','alternative','tied','pending','excluded')),
  rank INTEGER CHECK(rank IS NULL OR rank >= 1),
  disclosure_json TEXT NOT NULL,
  assessment_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, snapshot_revision, company_code),
  FOREIGN KEY(snapshot_id, snapshot_revision) REFERENCES k10_research_snapshot_revisions(snapshot_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_research_assessments_latest ON k10_research_company_assessments(snapshot_id, company_code, snapshot_revision DESC);
"""

_DROP_V1 = (
    # Delete dependency children first.  This path is exercised only after a verified backup,
    # but must still work on a populated V1.4 database with foreign keys enabled.
    "k10_task_retry_schedules", "k10_execution_item_checkpoints", "k10_scan_execution_bindings", "k10_task_execution_bindings", "k10_execution_config_revisions", "k10_task_outbox", "k10_analysis_requests", "k10_morning_report_items", "k10_morning_reports", "k10_company_window_observations", "k10_tasks", "k10_evaluation_records", "k10_morning_updates",
    "k10_company_window_evaluation_revisions", "k10_market_day_fact_revisions", "k10_company_window_selection_snapshots", "k10_company_window_actions", "k10_opportunity_lifecycle_events", "k10_publication_samples", "k10_opportunities", "k10_company_windows", "k10_publication_batches", "k10_plan_commands", "k10_plan_revisions", "k10_analysis_revisions", "k10_observations", "k10_candidate_actions",
    "k10_candidates", "k10_source_watermark_updates", "k10_scans", "k10_company_mappings",
    "k10_event_revisions", "k10_events", "k10_source_document_versions", "k10_source_documents",
    "k10_run_config_revisions",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(db_path: Path) -> Path:
    if not isinstance(db_path, Path):
        raise TypeError("K10 数据库必须显式传入 pathlib.Path")
    return db_path


@contextmanager
def write_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """受控写连接；仅迁移和 store 写入口可使用。"""
    path = _path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    # sqlite3 的 executescript 会隐式提交，不能用它承载迁移。本连接先显式开启
    # 事务，所有 DDL 都逐条执行，因此任一失败会回滚整套版本而非遗留半套表。
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def read_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """严格只读：不存在或未迁移的库给调用方明确错误，零 DDL。"""
    path = _path(db_path)
    if not path.exists():
        raise SchemaUnavailable(f"K10 schema 不存在：{path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise SchemaUnavailable(f"K10 schema 无法只读打开：{path}") from exc
    try:
        yield conn
    finally:
        conn.close()


def _version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='k10_schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    row = conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone()
    return int(row[0] or 0)


def require_schema(conn: sqlite3.Connection) -> None:
    version = _version(conn)
    if version != SCHEMA_VERSION:
        raise SchemaUnavailable(
            f"K10 schema 版本为 {version or '未建立'}，当前需要 {SCHEMA_VERSION}；请通过受控迁移建立"
        )


def schema_version(db_path: Path) -> int:
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _version(conn)


def initialize_schema(db_path: Path) -> int:
    """受控前滚至当前 K10 schema；可重复执行，绝不由读取或 API GET 调用。"""
    with write_connection(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS k10_schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        version = _version(conn)
        if version > SCHEMA_VERSION:
            raise K10SchemaError(f"数据库 K10 schema {version} 比运行时 {SCHEMA_VERSION} 新")
        if version == 0:
            _apply_v1(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (1,?)", (_now(),))
            _apply_v2(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (2,?)", (_now(),))
            _apply_v3(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (3,?)", (_now(),))
            _apply_v4(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (4,?)", (_now(),))
            _apply_v6(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (6,?)", (_now(),))
        elif version == 1:
            _apply_v2(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (2,?)", (_now(),))
            _apply_v3(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (3,?)", (_now(),))
            _apply_v4(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (4,?)", (_now(),))
            _apply_v6(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (6,?)", (_now(),))
        elif version == 2:
            _apply_v3(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (3,?)", (_now(),))
            _apply_v4(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (4,?)", (_now(),))
            _apply_v6(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (6,?)", (_now(),))
        elif version == 3:
            _apply_v4(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (4,?)", (_now(),))
            _apply_v6(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (6,?)", (_now(),))
        elif version == 4:
            _apply_v6(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (6,?)", (_now(),))
        elif version == 5:
            _apply_v6(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (6,?)", (_now(),))
        elif version in {6, 7}:
            pass
        elif version != SCHEMA_VERSION:
            raise K10SchemaError(f"缺少从 K10 schema {version} 到 {SCHEMA_VERSION} 的迁移")
        if _version(conn) == 6:
            _apply_v7(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (7,?)", (_now(),))
        if _version(conn) == 7:
            from .v2_schema import apply
            apply(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (8,?)", (_now(),))
    return SCHEMA_VERSION


def _apply_v1(conn: sqlite3.Connection) -> None:
    """在调用方已开启的事务中逐条执行 V1 DDL，禁止 ``executescript`` 隐式提交。"""
    for statement in _V1.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _apply_v2(conn: sqlite3.Connection) -> None:
    """V1.4 lifecycle migration: remove retired plan projections and add opportunity facts."""
    for statement in _V2.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _apply_v3(conn: sqlite3.Connection) -> None:
    """Add V3 ledgers and preserve raw anomalous market facts during the CHECK upgrade."""
    _upgrade_market_fact_availability(conn)
    _upgrade_analysis_revision_attempts(conn)
    for statement in _V3.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _apply_v4(conn: sqlite3.Connection) -> None:
    """Add execution recovery ledgers without rewriting a frozen K10 strategy record."""
    for statement in _V4.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _apply_v6(conn: sqlite3.Connection) -> None:
    # Schema 5 was never released.  A developer database can contain its
    # budget/template tables, but the V3 title-first contract must not leave
    # those admission paths available.  The fact cache and pause control stay.
    for table in ("k10_screening_runs", "k10_task_execution_spend_reservations", "k10_screening_template_revisions"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    for statement in _V6.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _apply_v7(conn: sqlite3.Connection) -> None:
    for statement in _V7.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _upgrade_market_fact_availability(conn: sqlite3.Connection) -> None:
    """Allow the explicit ``anomaly`` raw-fact state while proving the V2 rows survive intact."""
    rows = conn.execute("SELECT * FROM k10_market_day_fact_revisions ORDER BY company_code,trade_date,revision").fetchall()
    before = sha256(repr([tuple(row) for row in rows]).encode("utf-8")).hexdigest()
    conn.execute("ALTER TABLE k10_market_day_fact_revisions RENAME TO k10_market_day_fact_revisions_v2")
    conn.execute("""
        CREATE TABLE k10_market_day_fact_revisions (
          company_code TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK(revision >= 1),
          availability TEXT NOT NULL CHECK(availability IN ('available','suspended','data_gap','anomaly')),
          open REAL, high REAL, low REAL, close REAL, pre_close REAL, limit_up_price REAL,
          close_limit_up INTEGER, touched_limit_up INTEGER, adj_factor REAL,
          metadata_json TEXT NOT NULL, source_refs_json TEXT NOT NULL, obtained_at TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(company_code, trade_date, revision)
        )
    """)
    conn.execute("INSERT INTO k10_market_day_fact_revisions SELECT * FROM k10_market_day_fact_revisions_v2")
    restored = conn.execute("SELECT * FROM k10_market_day_fact_revisions ORDER BY company_code,trade_date,revision").fetchall()
    after = sha256(repr([tuple(row) for row in restored]).encode("utf-8")).hexdigest()
    if len(rows) != len(restored) or before != after or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise K10SchemaError("行情事实 v2→v3 重建核验失败")
    conn.execute("DROP TABLE k10_market_day_fact_revisions_v2")
    conn.execute("CREATE INDEX idx_k10_market_facts_latest ON k10_market_day_fact_revisions(company_code, trade_date, revision DESC)")


def _downgrade_market_fact_availability(conn: sqlite3.Connection) -> None:
    """Rebuild the V2 CHECK only when it cannot discard an anomaly fact."""
    if conn.execute("SELECT 1 FROM k10_market_day_fact_revisions WHERE availability='anomaly' LIMIT 1").fetchone() is not None:
        raise K10SchemaError("schema 3 含 anomaly 行情事实，须恢复已核备份，不能降级丢失数据")
    rows = conn.execute("SELECT * FROM k10_market_day_fact_revisions ORDER BY company_code,trade_date,revision").fetchall()
    digest = sha256(repr([tuple(row) for row in rows]).encode("utf-8")).hexdigest()
    conn.execute("ALTER TABLE k10_market_day_fact_revisions RENAME TO k10_market_day_fact_revisions_v3")
    conn.execute("""
        CREATE TABLE k10_market_day_fact_revisions (
          company_code TEXT NOT NULL, trade_date TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1),
          availability TEXT NOT NULL CHECK(availability IN ('available','suspended','data_gap')),
          open REAL, high REAL, low REAL, close REAL, pre_close REAL, limit_up_price REAL,
          close_limit_up INTEGER, touched_limit_up INTEGER, adj_factor REAL, metadata_json TEXT NOT NULL,
          source_refs_json TEXT NOT NULL, obtained_at TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(company_code, trade_date, revision)
        )
    """)
    conn.execute("INSERT INTO k10_market_day_fact_revisions SELECT * FROM k10_market_day_fact_revisions_v3")
    restored = conn.execute("SELECT * FROM k10_market_day_fact_revisions ORDER BY company_code,trade_date,revision").fetchall()
    if len(rows) != len(restored) or digest != sha256(repr([tuple(row) for row in restored]).encode("utf-8")).hexdigest() or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise K10SchemaError("行情事实 v3→v2 重建核验失败")
    conn.execute("DROP TABLE k10_market_day_fact_revisions_v3")
    conn.execute("CREATE INDEX idx_k10_market_facts_latest ON k10_market_day_fact_revisions(company_code, trade_date, revision DESC)")


def _upgrade_analysis_revision_attempts(conn: sqlite3.Connection) -> None:
    """Retain failed/successful retry attempts at one immutable global analysis revision."""
    rows = conn.execute("SELECT * FROM k10_analysis_revisions ORDER BY rowid").fetchall()
    digest = sha256(repr([tuple(row) for row in rows]).encode("utf-8")).hexdigest()
    conn.execute("ALTER TABLE k10_analysis_revisions RENAME TO k10_analysis_revisions_v2")
    conn.execute("""
        CREATE TABLE k10_analysis_revisions (
          analysis_id TEXT PRIMARY KEY,
          observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
          revision INTEGER NOT NULL CHECK(revision >= 1),
          analysis_kind TEXT NOT NULL CHECK(analysis_kind IN ('pro','con','morning')),
          input_cutoff_at TEXT NOT NULL, input_lineage_json TEXT NOT NULL, content_json TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('queued','completed','partial','failed','not_configured')),
          created_at TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO k10_analysis_revisions SELECT * FROM k10_analysis_revisions_v2")
    restored = conn.execute("SELECT * FROM k10_analysis_revisions ORDER BY rowid").fetchall()
    if len(rows) != len(restored) or digest != sha256(repr([tuple(row) for row in restored]).encode("utf-8")).hexdigest() or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise K10SchemaError("分析修订 v2→v3 重建核验失败")
    conn.execute("DROP TABLE k10_analysis_revisions_v2")
    conn.execute("CREATE INDEX idx_k10_analysis_attempts_latest ON k10_analysis_revisions(observation_id, revision, analysis_kind, created_at DESC, analysis_id DESC)")


def _downgrade_analysis_revision_attempts(conn: sqlite3.Connection) -> None:
    duplicates = conn.execute(
        "SELECT 1 FROM k10_analysis_revisions GROUP BY observation_id,revision,analysis_kind HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if duplicates is not None:
        raise K10SchemaError("schema 3 含分析重试历史，须恢复已核备份，不能降级丢失尝试")
    rows = conn.execute("SELECT * FROM k10_analysis_revisions ORDER BY rowid").fetchall()
    digest = sha256(repr([tuple(row) for row in rows]).encode("utf-8")).hexdigest()
    conn.execute("ALTER TABLE k10_analysis_revisions RENAME TO k10_analysis_revisions_v3")
    conn.execute("""
        CREATE TABLE k10_analysis_revisions (
          analysis_id TEXT PRIMARY KEY,
          observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
          revision INTEGER NOT NULL CHECK(revision >= 1),
          analysis_kind TEXT NOT NULL CHECK(analysis_kind IN ('pro','con','morning')),
          input_cutoff_at TEXT NOT NULL, input_lineage_json TEXT NOT NULL, content_json TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('queued','completed','partial','failed','not_configured')),
          created_at TEXT NOT NULL,
          UNIQUE(observation_id, revision, analysis_kind)
        )
    """)
    conn.execute("INSERT INTO k10_analysis_revisions SELECT * FROM k10_analysis_revisions_v3")
    restored = conn.execute("SELECT * FROM k10_analysis_revisions ORDER BY rowid").fetchall()
    if len(rows) != len(restored) or digest != sha256(repr([tuple(row) for row in restored]).encode("utf-8")).hexdigest() or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise K10SchemaError("分析修订 v3→v2 重建核验失败")
    conn.execute("DROP TABLE k10_analysis_revisions_v3")


def _downgrade_v4(conn: sqlite3.Connection) -> None:
    """Only an empty V4 ledger can be DDL-downgraded; otherwise restore the verified backup."""
    for table in ("k10_execution_config_revisions", "k10_task_execution_bindings", "k10_scan_execution_bindings",
                  "k10_execution_item_checkpoints", "k10_task_retry_schedules"):
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
            raise K10SchemaError("schema 4 含执行恢复记录，须恢复已核备份，不能降级丢失数据")
    for table in ("k10_task_retry_schedules", "k10_execution_item_checkpoints", "k10_scan_execution_bindings",
                  "k10_task_execution_bindings", "k10_execution_config_revisions"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def _downgrade_v6(conn: sqlite3.Connection) -> None:
    """A V6 rehearsal can only drop empty title/attempt state.

    A database which arrived via the discarded Schema 5 path has already had
    tables removed during the forward migration; restoring its verified backup
    is the only lossless rollback.
    """
    if conn.execute("SELECT 1 FROM k10_schema_migrations WHERE version=5").fetchone() is not None:
        raise K10SchemaError("schema 5→6 已删除未发布预算表，须恢复已核备份，不能 DDL 回滚")
    for table in (
        "k10_title_triage_policy_revisions", "k10_title_triage_manifests", "k10_title_triage_items",
        "k10_title_selection_manifests", "k10_article_admissions", "k10_external_attempts",
        "k10_discovery_retirements", "k10_fact_cache",
    ):
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
            raise K10SchemaError("schema 6 含标题筛选、尝试或缓存记录，须恢复已核备份，不能降级丢失数据")
    rows = conn.execute("SELECT control_key,state,reason_code,changed_at,changed_by FROM k10_run_controls").fetchall()
    implicit = [("k10_discovery", "closed", "unconfigured_closed", "1970-01-01T00:00:00+00:00", "schema-v6")]
    if [tuple(row) for row in rows] != implicit:
        raise K10SchemaError("schema 6 含运行控制变更，须恢复已核备份，不能降级丢失数据")
    for table in (
        "k10_discovery_retirements", "k10_external_attempts", "k10_article_admissions",
        "k10_title_selection_manifests", "k10_title_triage_items", "k10_title_triage_manifests",
        "k10_title_triage_policy_revisions", "k10_fact_cache", "k10_run_controls",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def _downgrade_v7(conn: sqlite3.Connection) -> None:
    """Only an unused investigation schema can be rehearsal-downgraded.

    Research snapshots contain the exact evidence history needed to explain a
    recommendation.  A populated production database must restore its verified
    backup instead of deleting that history through a convenience rollback.
    """
    tables = (
        "k10_research_snapshot_revisions", "k10_research_stage_results", "k10_research_claims",
        "k10_research_evidence_links", "k10_research_questions", "k10_research_query_paths",
        "k10_research_fulltext_requests", "k10_research_company_assessments",
    )
    for table in tables:
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
            raise K10SchemaError("schema 7 含调查证据记录，须恢复已核备份，不能降级丢失数据")
    for table in reversed(tables):
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def rollback_schema(db_path: Path, *, target_version: int = 0) -> int:
    """仅用于演练/受控回滚；调用方必须先完成备份验证。"""
    if target_version not in {0, 2, 3}:
        raise ValueError("当前 K10 仅支持回滚到 schema 0、2 或 3")
    with write_connection(db_path) as conn:
        version = _version(conn)
        if version == 7:
            _downgrade_v7(conn)
            conn.execute("DELETE FROM k10_schema_migrations WHERE version=7")
            version = 6
        if version == 6:
            _downgrade_v6(conn)
            conn.execute("DELETE FROM k10_schema_migrations WHERE version=6")
            version = 4
        if target_version == 3:
            if version == 3:
                return 3
            if version != 4:
                raise K10SchemaError(f"不能从未知 K10 schema {version} 回滚到 3")
            _downgrade_v4(conn)
            conn.execute("DELETE FROM k10_schema_migrations WHERE version=4")
            return 3
        if target_version == 2:
            if version == 2:
                return 2
            if version == 4:
                _downgrade_v4(conn)
                conn.execute("DELETE FROM k10_schema_migrations WHERE version=4")
                version = 3
            if version != 3:
                raise K10SchemaError(f"不能从未知 K10 schema {version} 回滚到 2")
            # This is a controlled DDL rollback for rehearsal only. Production rollback must
            # restore the verified pre-migration backup so no V3 ledger is silently discarded.
            for table in ("k10_analysis_requests", "k10_morning_report_items", "k10_morning_reports"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            _downgrade_analysis_revision_attempts(conn)
            _downgrade_market_fact_availability(conn)
            conn.execute("DELETE FROM k10_schema_migrations WHERE version=3")
            return 2
        if version == 0:
            return 0
        if version != 4:
            raise K10SchemaError(f"不能回滚未知 K10 schema {version}")
        # V4 carries resumable work.  Unlike an empty-schema rehearsal, a
        # populated execution ledger may not be discarded by a convenience
        # downgrade; production must restore its verified predeploy backup.
        _downgrade_v4(conn)
        for table in _DROP_V1:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("DROP TABLE IF EXISTS k10_schema_migrations")
    return 0


__all__ = [
    "K10SchemaError", "SCHEMA_VERSION", "SchemaUnavailable", "initialize_schema",
    "read_connection", "require_schema", "rollback_schema", "schema_version", "write_connection",
]
