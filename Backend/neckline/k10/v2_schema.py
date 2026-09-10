"""Schema 8 additions; existing publication/evidence ledgers are untouched."""
DDL = """
CREATE TABLE k10_morning_review_results (
 task_id TEXT PRIMARY KEY REFERENCES k10_tasks(task_id), input_sha256 TEXT NOT NULL,
 result_json TEXT NOT NULL, captured_at TEXT NOT NULL
);
CREATE TABLE k10_v2_report_coverage (
 report_id TEXT PRIMARY KEY REFERENCES k10_v2_report_runs(report_id), content_json TEXT NOT NULL
);

CREATE TABLE k10_v2_report_lifecycle_updates (
 report_id TEXT NOT NULL REFERENCES k10_v2_report_runs(report_id),
 lifecycle_event_id TEXT NOT NULL UNIQUE REFERENCES k10_opportunity_lifecycle_events(lifecycle_event_id),
 PRIMARY KEY(report_id,lifecycle_event_id)
);
CREATE TABLE k10_v2_title_company_hints (
 task_id TEXT NOT NULL REFERENCES k10_tasks(task_id), document_id TEXT NOT NULL, revision INTEGER NOT NULL,
 company_codes_json TEXT NOT NULL, PRIMARY KEY(task_id,document_id,revision)
);
CREATE TABLE k10_v2_stage_input_usage (
 task_id TEXT NOT NULL REFERENCES k10_tasks(task_id), operation TEXT NOT NULL, item_key TEXT NOT NULL,
 attempt INTEGER NOT NULL, input_characters INTEGER NOT NULL, profile_characters INTEGER NOT NULL,
 profile_count INTEGER NOT NULL, input_sha256 TEXT NOT NULL, profile_sha256 TEXT,
 PRIMARY KEY(task_id,operation,item_key,attempt)
);

CREATE TABLE k10_v2_universe_snapshots (
 snapshot_id TEXT PRIMARY KEY, strategy_version TEXT NOT NULL, content_sha256 TEXT NOT NULL,
 source_json TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE k10_v2_universe_members (
 snapshot_id TEXT NOT NULL REFERENCES k10_v2_universe_snapshots(snapshot_id),
 company_code TEXT NOT NULL, company_name TEXT NOT NULL, PRIMARY KEY(snapshot_id,company_code)
);
CREATE TABLE k10_v2_profile_snapshots (
 snapshot_id TEXT PRIMARY KEY, universe_snapshot_id TEXT NOT NULL REFERENCES k10_v2_universe_snapshots(snapshot_id),
 hashes_json TEXT NOT NULL, review_status TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE k10_v2_company_profiles (
 snapshot_id TEXT NOT NULL REFERENCES k10_v2_profile_snapshots(snapshot_id), company_code TEXT NOT NULL,
 profile_json TEXT NOT NULL, index_json TEXT NOT NULL, PRIMARY KEY(snapshot_id,company_code)
);
CREATE TABLE k10_v2_company_profile_evidence (
 snapshot_id TEXT NOT NULL, company_code TEXT NOT NULL, evidence_json TEXT NOT NULL, content_sha256 TEXT NOT NULL,
 PRIMARY KEY(snapshot_id,company_code), FOREIGN KEY(snapshot_id,company_code) REFERENCES k10_v2_company_profiles(snapshot_id,company_code)
);
CREATE TABLE k10_v2_strategy_snapshots (
 snapshot_id TEXT PRIMARY KEY, strategy_version TEXT NOT NULL, strategy_sha256 TEXT NOT NULL,
 universe_snapshot_id TEXT NOT NULL REFERENCES k10_v2_universe_snapshots(snapshot_id),
 profile_snapshot_id TEXT NOT NULL REFERENCES k10_v2_profile_snapshots(snapshot_id),
 config_id TEXT NOT NULL, config_revision INTEGER NOT NULL, execution_config_id TEXT NOT NULL, execution_config_revision INTEGER NOT NULL,
 content_json TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(config_id,config_revision) REFERENCES k10_run_config_revisions(config_id,revision),
 FOREIGN KEY(execution_config_id,execution_config_revision) REFERENCES k10_execution_config_revisions(config_id,revision)
);
CREATE TABLE k10_v2_report_runs (
 report_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL UNIQUE REFERENCES k10_scans(scan_id),
 strategy_snapshot_id TEXT NOT NULL REFERENCES k10_v2_strategy_snapshots(snapshot_id),
 window_kind TEXT NOT NULL CHECK(window_kind IN ('evening','morning')),
 parent_report_id TEXT REFERENCES k10_v2_report_runs(report_id), cutoff_at TEXT NOT NULL,
 verification_cutoff_at TEXT, available_at TEXT, status TEXT NOT NULL, error_json TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE k10_v2_report_cards (
 card_id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES k10_v2_report_runs(report_id),
 company_code TEXT NOT NULL, company_name TEXT NOT NULL, rank INTEGER NOT NULL,
 section TEXT NOT NULL CHECK(section IN ('evening','updated','added')),
 company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id),
 content_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(report_id,company_code), UNIQUE(report_id,rank)
);
CREATE TABLE k10_v2_card_catalysts (
 card_id TEXT NOT NULL REFERENCES k10_v2_report_cards(card_id), event_id TEXT NOT NULL,
 event_revision INTEGER NOT NULL, opportunity_id TEXT REFERENCES k10_opportunities(opportunity_id),
 content_json TEXT NOT NULL, PRIMARY KEY(card_id,event_id,event_revision),
 FOREIGN KEY(event_id,event_revision) REFERENCES k10_event_revisions(event_id,revision)
);
"""


def apply(conn):
    # Keep the retired title/evidence tables byte-for-byte; V2 has no body quota.
    from .schema import _V6
    names = ("k10_title_triage_manifests", "k10_title_triage_items", "k10_title_selection_manifests", "k10_article_admissions")
    for statement in _V6.split(";"):
        statement = statement.strip()
        if not any(statement.startswith("CREATE TABLE " + name + " (") or
                   (statement.startswith("CREATE INDEX ") and " ON " + name + "(" in statement)
                   for name in names):
            continue
        for name in names:
            statement = statement.replace(name, name.replace("k10_", "k10_v2_"))
        statement = statement.replace("idx_k10_", "idx_k10_v2_")
        statement = statement.replace("article_limit INTEGER NOT NULL CHECK(article_limit IN (40,80))", "input_count INTEGER NOT NULL CHECK(input_count >= 0)")
        conn.execute(statement)
    for statement in DDL.split(";"):
        if statement.strip():
            conn.execute(statement)
