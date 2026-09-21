"""K10-v2 strategy binding and daily company report ledger."""
from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from . import store
from .config import validate_run_config, validate_execution_config
from .schema import read_connection, require_schema, write_connection
from .v2_profiles import INPUT_HASHES, STRATEGY_SHA256, UNIVERSE_ID, PROFILES_ID, read_profiles
from .universe import Eligibility


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _id(prefix, *parts):
    return prefix + '_' + sha256('\x1f'.join(parts).encode()).hexdigest()[:32]


def delivery_identity_for_inputs(inputs) -> list[dict[str, Any]]:
    """Return the immutable, per-card identity behind a B76 delivery hash.

    Company codes alone cannot prove that a published card is the same
    comparison/evidence set that went into the one global ordering.  Keep the
    exact catalyst identities on the card itself so a later reader can
    recompute the delivery hash without consulting mutable candidates.
    """
    from .delivery import digest

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in inputs:
        comparison = dict(item.comparison)
        catalyst = {
            "candidateId": item.candidate_id,
            "eventId": item.event_id,
            "eventRevision": item.event_revision,
            "opportunityKey": item.opportunity_key,
            "catalystStage": item.catalyst_stage,
            "category": item.category,
            "comparisonSha256": digest(comparison),
            "evidenceRefs": [dict(ref) for ref in item.evidence_refs],
            "researchSnapshotId": comparison.get("researchSnapshotId"),
            "researchRevision": comparison.get("researchRevision"),
        }
        grouped.setdefault(item.company_code, []).append(catalyst)
    return [
        {"companyCode": company_code,
         "catalysts": sorted(catalysts, key=lambda row: (row["eventId"], row["eventRevision"], row["candidateId"]))}
        for company_code, catalysts in sorted(grouped.items())
    ]


def delivery_identity_from_cards(conn, *, report_id: str) -> list[dict[str, Any]]:
    """Read the exact persisted B76 identity rows, rejecting malformed cards."""
    rows: list[dict[str, Any]] = []
    for row in conn.execute(
        "SELECT company_code,content_json FROM k10_v2_report_cards WHERE report_id=? ORDER BY company_code,card_id",
        (report_id,),
    ):
        try:
            content = json.loads(row[1])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("日报卡片交付身份无效") from exc
        identity = content.get("deliveryIdentity") if isinstance(content, dict) else None
        if not isinstance(identity, dict) or identity.get("companyCode") != row[0]:
            raise ValueError("日报卡片缺少交付身份")
        catalysts = identity.get("catalysts")
        if not isinstance(catalysts, list) or any(not isinstance(item, dict) for item in catalysts):
            raise ValueError("日报卡片交付催化身份无效")
        rows.append({"companyCode": str(row[0]), "catalysts": catalysts})
    return rows


def bind_strategy(*, db_path: Path, snapshot_id: str, config_id: str, config_revision: int,
                  execution_config_id: str, execution_config_revision: int, created_at: str) -> dict:
    config = store.read_run_config(config_id=config_id, revision=config_revision, db_path=db_path)
    execution = store.read_execution_config(config_id=execution_config_id, revision=execution_config_revision, db_path=db_path)
    if not config or not execution or not validate_run_config(config['payload'], scope='discovery').ready or not validate_execution_config(execution['payload']).ready:
        raise ValueError('策略或执行配置未就绪')
    payload = config['payload']
    if payload.get('configVersion') != 'k10-v2' or payload.get('strategySnapshotId') != snapshot_id:
        raise ValueError('策略快照必须与配置显式绑定一致')
    content = dict(strategyVersion='K10-v2', strategySha256=STRATEGY_SHA256,
                   universeSnapshotId=UNIVERSE_ID, profileSnapshotId=PROFILES_ID, hashes=INPUT_HASHES,
                   configId=config_id, configRevision=config_revision,
                   executionConfigId=execution_config_id, executionConfigRevision=execution_config_revision,
                   configSha256=config['contentSha256'], executionSha256=execution['contentSha256'])
    with write_connection(db_path) as conn:
        require_schema(conn)
        source = conn.execute('SELECT hashes_json,review_status FROM k10_v2_profile_snapshots WHERE snapshot_id=? AND universe_snapshot_id=?', (PROFILES_ID, UNIVERSE_ID)).fetchone()
        if not source or json.loads(source[0]) != INPUT_HASHES or source[1] != 'local_draft_awaiting_user':
            raise ValueError('固定资料快照未导入或不一致')
        existing = conn.execute('SELECT content_json FROM k10_v2_strategy_snapshots WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        if existing:
            if existing[0] != _json(content):
                raise ValueError('策略快照不可覆盖；请显式指定新 ID')
            return content
        conn.execute('INSERT INTO k10_v2_strategy_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (snapshot_id, 'K10-v2', STRATEGY_SHA256, UNIVERSE_ID, PROFILES_ID, config_id, config_revision,
                      execution_config_id, execution_config_revision, _json(content), created_at))
    return content


def binding_status(*, db_path: Path, config: dict | None, execution: dict | None) -> dict:
    failure = {'state': 'not_configured', 'errors': ['今天没跑成 · 参数未配置']}
    if not config or not execution or config['payload'].get('configVersion') != 'k10-v2':
        return failure
    snapshot_id = config['payload'].get('strategySnapshotId')
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute('SELECT content_json FROM k10_v2_strategy_snapshots WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        if not row:
            return failure
        content = json.loads(row[0])
        expected = {'configId': config['configId'], 'configRevision': config['revision'], 'configSha256': config['contentSha256'],
                    'executionConfigId': execution['configId'], 'executionConfigRevision': execution['revision'], 'executionSha256': execution['contentSha256']}
        if any(content.get(k) != v for k, v in expected.items()) or content.get('hashes') != INPUT_HASHES or content.get('strategySha256') != STRATEGY_SHA256:
            return failure
        policy = execution['payload']['discovery']['titleTriagePolicy']
        approved = conn.execute('SELECT content_json,content_sha256,approval_state FROM k10_title_triage_policy_revisions WHERE policy_id=? AND revision=?', (policy['policyId'],policy['revision'])).fetchone()
        if not approved or approved[1] != policy['contentSha256'] or approved[2] != 'approved' or json.loads(approved[0]) != policy['content']:
            return failure
        for table, key in [('k10_v2_universe_members', UNIVERSE_ID), ('k10_v2_company_profiles', PROFILES_ID), ('k10_v2_company_profile_evidence', PROFILES_ID)]:
            if conn.execute(f'SELECT count(*) FROM {table} WHERE snapshot_id=?', (key,)).fetchone()[0] != 1089:
                return failure
        profile = conn.execute('SELECT hashes_json,review_status FROM k10_v2_profile_snapshots WHERE snapshot_id=?', (PROFILES_ID,)).fetchone()
        if not profile or json.loads(profile[0]) != INPUT_HASHES:
            return failure
    return {'state': 'configured', 'errors': [], 'strategySnapshotId': snapshot_id,
            'universeSnapshotId': UNIVERSE_ID, 'profileSnapshotId': PROFILES_ID,
            'universeSha256': INPUT_HASHES['universe'], 'profilesSha256': INPUT_HASHES['profiles'],
            'profileReviewStatus': profile[1], 'strategyVersion': 'K10-v2'}


class FixedCompanyPool:
    def __init__(self, *, db_path: Path, profiles_id: str):
        self.index = read_profiles(db_path=db_path, profiles_id=profiles_id, index_only=True)
        self.codes = {row['ts_code'] for row in self.index}

    def eligibility(self, company_code: str) -> Eligibility:
        return Eligibility('eligible') if company_code in self.codes else Eligibility('excluded', '不属于已确认固定股票池')

    def lookup(self, *, company_code, as_of):
        raise RuntimeError('固定池资格不得回退动态元数据')


def _write_report_delivery(
    conn, *, report_id: str, delivery: dict[str, Any],
    replace_unpublished_failed_delivery: bool = False,
) -> None:
    """Persist one immutable B76 delivery manifest with legacy coverage fields."""
    existing = conn.execute('SELECT content_json FROM k10_v2_report_coverage WHERE report_id=?', (report_id,)).fetchone()
    if existing is not None:
        try:
            coverage = json.loads(existing[0])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError('日报完整度记录无效') from exc
        old = coverage.get('delivery') if isinstance(coverage, dict) else None
        if old is not None and old != delivery:
            # A B76 pre-publication failure leaves a readable failure DTO but
            # no available report/card.  An explicitly authorized recovery of
            # that *same frozen task* must be able to replace this diagnostic
            # with the later lawful delivery.  A visible report, or a prior
            # complete/partial manifest, remains immutable.
            if not (
                replace_unpublished_failed_delivery
                and isinstance(old, Mapping)
                and old.get("outcome") == "failed"
                and delivery.get("outcome") in {"complete", "partial"}
            ):
                raise ValueError('已公开日报的 B76 交付清单不可覆盖')
        if not isinstance(coverage, dict):
            raise ValueError('日报完整度记录无效')
    else:
        coverage = {'coverageGaps': [], 'incompleteReviews': []}
    coverage['delivery'] = dict(delivery)
    legacy_gaps = coverage.get('coverageGaps')
    if not isinstance(legacy_gaps, list) or any(not isinstance(item, str) for item in legacy_gaps):
        legacy_gaps = []
    # B69 clients intentionally ignore ``delivery``.  Keep their pre-existing
    # coverage field derived from the exact same safe gap messages so a partial
    # B76 report never looks complete on an older decoder.
    delivery_gaps = delivery.get('gaps') if isinstance(delivery.get('gaps'), list) else []
    derived_gaps = [item.get('message') for item in delivery_gaps if isinstance(item, Mapping)
                    and isinstance(item.get('message'), str) and item['message']]
    coverage['coverageGaps'] = list(dict.fromkeys([*legacy_gaps, *derived_gaps]))
    if not isinstance(coverage.get('incompleteReviews'), list):
        coverage['incompleteReviews'] = []
    conn.execute('INSERT INTO k10_v2_report_coverage VALUES (?,?) ON CONFLICT(report_id) DO UPDATE SET content_json=excluded.content_json',
                 (report_id, _json(coverage)))


def write_report_materials(
    conn, *, report_id: str, materials: list[Mapping[str, Any]], result_available_at: str | None,
    delivery_deadline_at: str | None, unavailable_reason: Mapping[str, Any] | None = None,
) -> None:
    """Persist safe, unranked event materials with the report transaction."""
    if unavailable_reason is not None and materials:
        raise ValueError("不可用材料不能同时写入项目")
    existing = conn.execute(
        "SELECT delivery_deadline_at FROM k10_v2_report_delivery_metadata WHERE report_id=?", (report_id,)
    ).fetchone()
    if existing is not None and existing[0] is not None:
        if delivery_deadline_at is None:
            delivery_deadline_at = existing[0]
        elif delivery_deadline_at != existing[0]:
            raise ValueError("报告冻结截止时间不可覆盖")
    state = "unavailable" if unavailable_reason is not None else ("available" if materials else "empty")
    conn.execute("DELETE FROM k10_v2_report_materials WHERE report_id=?", (report_id,))
    for item in materials:
        required = {"materialId", "eventId", "eventTitle", "facts", "companyRelations", "uncertainties", "sourceRefs", "asOf"}
        if not isinstance(item, Mapping) or set(item) != required:
            raise ValueError("报告材料字段无效")
        if not all(isinstance(item[key], str) and item[key] for key in ("materialId", "eventId", "eventTitle", "asOf")):
            raise ValueError("报告材料身份无效")
        for key in ("facts", "companyRelations", "uncertainties", "sourceRefs"):
            if not isinstance(item[key], list):
                raise ValueError("报告材料集合无效")
        conn.execute(
            "INSERT INTO k10_v2_report_materials(report_id,material_id,event_id,event_title,facts_json,"
            "company_relations_json,uncertainties_json,source_refs_json,as_of) VALUES(?,?,?,?,?,?,?,?,?)",
            (report_id, item["materialId"], item["eventId"], item["eventTitle"], _json(item["facts"]),
             _json(item["companyRelations"]), _json(item["uncertainties"]), _json(item["sourceRefs"]), item["asOf"]),
        )
    conn.execute(
        "INSERT INTO k10_v2_report_delivery_metadata(report_id,result_available_at,delivery_deadline_at,materials_state,materials_reason_json,updated_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(report_id) DO UPDATE SET result_available_at=excluded.result_available_at,"
        "delivery_deadline_at=excluded.delivery_deadline_at,materials_state=excluded.materials_state,"
        "materials_reason_json=excluded.materials_reason_json,updated_at=excluded.updated_at",
        (report_id, result_available_at, delivery_deadline_at, state,
         None if unavailable_reason is None else _json(dict(unavailable_reason)),
         result_available_at or delivery_deadline_at or "1970-01-01T00:00:00+00:00"),
    )


def publish_cards(conn, *, report_id: str, scan_id: str, kind: str, snapshot_id: str, inputs,
                  available_at: str, delivery: dict[str, Any] | None = None,
                  materials: list[Mapping[str, Any]] | None = None,
                  result_available_at: str | None = None, delivery_deadline_at: str | None = None,
                  allow_unpublished_failed_delivery_replacement: bool = False):
    """Called inside opportunity publication transaction, including retries."""
    prior_report = conn.execute(
        'SELECT available_at FROM k10_v2_report_runs WHERE report_id=?', (report_id,)
    ).fetchone()
    if prior_report is not None and prior_report[0] is not None:
        return
    prior_card_count = (
        int(conn.execute('SELECT count(*) FROM k10_v2_report_cards WHERE report_id=?', (report_id,)).fetchone()[0])
        if prior_report is not None else 0
    )
    cutoff, status = conn.execute('SELECT cutoff_at,status FROM k10_scans WHERE scan_id=?', (scan_id,)).fetchone()
    delivery_identity = delivery_identity_for_inputs(inputs)
    if delivery is not None:
        from .delivery import digest
        outcome = delivery.get('outcome')
        if outcome not in {'complete', 'partial'}:
            raise ValueError('公开日报只能提交完整或带缺口交付')
        if delivery.get('eligibleSetSha256') != digest(delivery_identity):
            raise ValueError('B76 交付公司集合与卡片身份不一致')
        # Public report state keeps its established enum.  B76 ``complete``
        # is a delivery outcome, not a newly introduced database status.
        status = 'completed' if outcome == 'complete' else 'partial'
    parent = None
    parent_codes = set()
    if kind == 'morning':
        row = conn.execute("SELECT report_id FROM k10_v2_report_runs WHERE window_kind='evening' AND julianday(cutoff_at)<julianday(?) ORDER BY julianday(cutoff_at) DESC LIMIT 1", (cutoff,)).fetchone()
        parent = row[0] if row else None
        if parent:
            parent_codes = {row[0] for row in conn.execute('SELECT company_code FROM k10_v2_report_cards WHERE report_id=?', (parent,))}
    conn.execute('INSERT INTO k10_v2_report_runs VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(report_id) DO UPDATE SET verification_cutoff_at=excluded.verification_cutoff_at,available_at=excluded.available_at,status=excluded.status,error_json=NULL',
                 (report_id, scan_id, snapshot_id, kind, parent, cutoff, available_at, available_at, status, None, available_at))
    if delivery is not None:
        _write_report_delivery(
            conn, report_id=report_id, delivery=delivery,
            replace_unpublished_failed_delivery=(
                allow_unpublished_failed_delivery_replacement
                and prior_report is not None
                and prior_report[0] is None
                and prior_card_count == 0
            ),
        )
    if materials is not None:
        write_report_materials(conn, report_id=report_id, materials=materials,
                               result_available_at=result_available_at,
                               delivery_deadline_at=delivery_deadline_at)
    # Expiry is a fixed-window fact, appended at this controlled publication write.
    expired = conn.execute('SELECT o.opportunity_id,w.d2_close_at FROM k10_opportunities o JOIN k10_company_windows w ON w.company_window_id=o.company_window_id WHERE julianday(w.d2_close_at)<=julianday(?)', (available_at,)).fetchall()
    for opportunity_id, close_at in expired:
        terminal = conn.execute("SELECT 1 FROM k10_opportunity_lifecycle_events WHERE opportunity_id=? AND kind IN ('withdrawal','expired')", (opportunity_id,)).fetchone()
        if not terminal:
            conn.execute('INSERT INTO k10_opportunity_lifecycle_events VALUES (?,?,?,?,?,?,?,?)',
                (_id('expiry',opportunity_id,close_at),opportunity_id,'expired','固定两日观察窗口已结束','[]',_json({'scanId':scan_id}),close_at,available_at))
    _link_report_updates(conn, report_id=report_id, scan_id=scan_id, available_at=available_at)
    groups = {}
    for item in inputs:
        groups.setdefault(item.company_code, []).append(item)
    if kind == 'evening' and len(groups) > 30:
        raise ValueError('晚报最多 30 张不同公司卡')
    identity_by_code = {row['companyCode']: row for row in delivery_identity}
    for rank, (code, items) in enumerate(sorted(groups.items(), key=lambda pair: (min(item.display_rank or 1 for item in pair[1]), pair[0])), 1):
        mappings = []
        for item in items:
            classification = item.comparison['classification']['kind']
            if classification in {'initial', 'independent', 'material_stage'}:
                opportunity = conn.execute('SELECT opportunity_id,company_window_id FROM k10_opportunities WHERE opportunity_key=? AND company_code=?', (item.opportunity_key, code)).fetchone()
            else:
                opportunity = conn.execute('SELECT opportunity_id,company_window_id FROM k10_opportunities WHERE opportunity_id=? AND company_code=? AND opportunity_key=?', (item.related_opportunity_id, code, item.opportunity_key)).fetchone()
            if not opportunity:
                raise ValueError('日报卡催化未关联正式机会')
            headline = conn.execute('SELECT headline FROM k10_event_revisions WHERE event_id=? AND revision=?', (item.event_id, item.event_revision)).fetchone()[0]
            disclosure = item.comparison['differences'].get('evidenceDisclosure') or {}
            mappings.append({'eventId': item.event_id, 'eventRevision': item.event_revision,
                             'opportunityId': opportunity[0], 'companyWindowId': opportunity[1],
                             'headline': headline, 'summary': item.comparison['summary'],
                             'classification': item.comparison['classification']['kind'],
                             'verificationStatus': disclosure.get('verificationStatus', 'unverified')})
        # New sample is actionable when old and new catalysts share today's card.
        fresh = [mapping for mapping in mappings if mapping['classification'] in {'initial', 'independent', 'material_stage'}]
        target = (fresh or mappings)[0]['companyWindowId']
        source_refs, uncertainty = [], []
        for item in items:
            for ref in item.evidence_refs:
                if ref not in source_refs:
                    source_refs.append(ref)
            uncertainty.extend((item.comparison['differences'].get('evidenceDisclosure') or {}).get('unverifiedReasons') or [])
        member = conn.execute('SELECT company_name FROM k10_v2_universe_members WHERE snapshot_id=? AND company_code=?', (UNIVERSE_ID, code)).fetchone()
        if not member:
            raise ValueError('日报公司不属于固定池')
        card_id = _id('card', report_id, code)
        from .market_context import card_price_context
        price, price_context = card_price_context([item.comparison.get('marketContext', {}).get(code) for item in items], company_code=code, cutoff_at=cutoff)
        content = {'summary': '\n'.join(dict.fromkeys(item.comparison['summary'] for item in items)),
                   'twoDayReason': '\n'.join(dict.fromkeys(item.comparison['differences']['twoDayReason'] for item in items)),
                   'uncertainty': list(dict.fromkeys(uncertainty)), 'sourceRefs': source_refs, 'catalysts': mappings, 'priceReaction': price, 'priceContext': price_context,
                   'deliveryIdentity': identity_by_code[code],
                   'sourceMarker': kind, 'latePublication': any(json.loads(row[0]).get('latePublication', False) for row in conn.execute(
                       "SELECT l.content_json FROM k10_opportunity_lifecycle_events l JOIN k10_opportunities o ON o.opportunity_id=l.opportunity_id WHERE o.company_window_id=? AND l.kind='published'", (target,)))}
        conn.execute('INSERT INTO k10_v2_report_cards VALUES (?,?,?,?,?,?,?,?,?)',
                     (card_id, report_id, code, member[0], rank, 'evening' if kind == 'evening' else 'updated' if code in parent_codes else 'added', target, _json(content), available_at))
        for item, mapping in zip(items, mappings):
            conn.execute('INSERT INTO k10_v2_card_catalysts VALUES (?,?,?,?,?)',
                         (card_id, item.event_id, item.event_revision, mapping['opportunityId'], _json(mapping)))

    def finalize_timestamp(final_at):
        conn.execute('UPDATE k10_v2_report_runs SET available_at=?,verification_cutoff_at=?,created_at=? WHERE report_id=?',
                     (final_at, final_at, final_at, report_id))
        conn.execute('UPDATE k10_v2_report_cards SET created_at=? WHERE report_id=?', (final_at, report_id))
    return finalize_timestamp


def _link_report_updates(conn, *, report_id: str, scan_id: str, available_at: str):
    # Each durable change belongs to one report; no company/card is fabricated.
    for row in conn.execute("SELECT lifecycle_event_id,content_json,created_at,kind FROM k10_opportunity_lifecycle_events WHERE kind IN ('risk','withdrawal','expired','evidence_update')"):
        content = json.loads(row[1])
        if not store.reportable_lifecycle_update(row[3], content):
            continue
        first = conn.execute('SELECT created_at FROM k10_v2_report_runs ORDER BY julianday(created_at),report_id LIMIT 1').fetchone()[0]
        explicit = content.get('scanId')
        from datetime import datetime
        if explicit == scan_id or (not explicit and first and datetime.fromisoformat(first) <= datetime.fromisoformat(row[2]) <= datetime.fromisoformat(available_at)):
            conn.execute('INSERT OR IGNORE INTO k10_v2_report_lifecycle_updates VALUES (?,?)', (report_id,row[0]))


def _current_lifecycle_state(conn, opportunity_id):
    state = store._opportunity_state(conn, opportunity_id)
    rows = conn.execute("SELECT kind,content_json FROM k10_opportunity_lifecycle_events WHERE opportunity_id=? ORDER BY julianday(occurred_at),rowid", (opportunity_id,)).fetchall()
    events = [{"kind": row[0], "content": json.loads(row[1])} for row in rows]
    return store.lifecycle_state(store.project_opportunity_lifecycle(state, events))


def read_report(*, db_path: Path, report_id: str | None = None, window: str = 'evening', cursor: str | None = None, limit: int = 30):
    with read_connection(db_path) as conn:
        require_schema(conn)
        conn.row_factory = __import__('sqlite3').Row
        row = (conn.execute('SELECT * FROM k10_v2_report_runs WHERE report_id=?', (report_id,)).fetchone() if report_id else
               conn.execute('SELECT * FROM k10_v2_report_runs WHERE window_kind=? ORDER BY julianday(cutoff_at) DESC,report_id DESC LIMIT 1', (window,)).fetchone())
        if row is None:
            return None
        report = dict(reportId=row['report_id'], strategyVersion='K10-v2', strategySnapshotId=row['strategy_snapshot_id'],
                      windowKind=row['window_kind'], parentReportId=row['parent_report_id'], cutoffAt=row['cutoff_at'],
                      verificationCutoffAt=row['verification_cutoff_at'], availableAt=row['available_at'], status=row['status'],
                      eveningCards=[], updatedCards=[], addedCards=[], lifecycleUpdates=[], nextCursor=None)
        if row['available_at'] is None:
            active = conn.execute('SELECT t.status FROM k10_scan_execution_bindings b JOIN k10_tasks t ON t.task_id=b.task_id WHERE b.scan_id=?', (row['scan_id'],)).fetchone()
            if active and (active[0] == 'running' or (active[0] == 'queued' and row['status'] in {'failed', 'not_configured'})):
                report['status'] = active[0]
        coverage=conn.execute('SELECT content_json FROM k10_v2_report_coverage WHERE report_id=?',(row['report_id'],)).fetchone()
        report.update(json.loads(coverage[0]) if coverage else {'coverageGaps':[], 'incompleteReviews':[]})
        metadata = conn.execute(
            'SELECT result_available_at,delivery_deadline_at,materials_state,materials_reason_json FROM k10_v2_report_delivery_metadata WHERE report_id=?',
            (row['report_id'],),
        ).fetchone()
        if metadata is not None:
            try:
                reason = None if metadata[3] is None else json.loads(metadata[3])
            except (TypeError, ValueError, json.JSONDecodeError):
                reason = {"reason": "materials_unavailable", "message": "材料状态不可读取", "missing": []}
            count = int(conn.execute('SELECT count(*) FROM k10_v2_report_materials WHERE report_id=?', (row['report_id'],)).fetchone()[0])
            report.update({"resultAvailableAt": metadata[0], "deliveryDeadlineAt": metadata[1],
                           "materials": {"state": metadata[2], "count": count, "reason": reason}})
        changes = conn.execute('SELECT e.lifecycle_event_id,e.opportunity_id,o.company_window_id,o.company_code,e.kind,e.reason,e.created_at,e.source_refs_json FROM k10_v2_report_lifecycle_updates l JOIN k10_opportunity_lifecycle_events e ON e.lifecycle_event_id=l.lifecycle_event_id JOIN k10_opportunities o ON o.opportunity_id=e.opportunity_id WHERE l.report_id=? ORDER BY julianday(e.created_at),e.lifecycle_event_id', (row['report_id'],)).fetchall()
        for change in changes:
            member = conn.execute('SELECT m.company_name FROM k10_v2_strategy_snapshots s JOIN k10_v2_universe_members m ON m.snapshot_id=s.universe_snapshot_id WHERE s.snapshot_id=? AND m.company_code=?', (row['strategy_snapshot_id'],change[3])).fetchone()
            report['lifecycleUpdates'].append(dict(updateId=change[0],opportunityId=change[1],companyWindowId=change[2],companyCode=change[3],companyName=member[0] if member else change[3],kind='expiry' if change[4]=='expired' else change[4],reason=change[5] or '',createdAt=change[6],sourceRefs=json.loads(change[7])))
        cards = list(conn.execute('SELECT c.*,w.d1_trade_date,w.d2_trade_date,w.sample_class FROM k10_v2_report_cards c JOIN k10_company_windows w ON w.company_window_id=c.company_window_id WHERE report_id=? ORDER BY rank,card_id', (row['report_id'],)))
        additions = [item for item in cards if item['section'] == 'added']
        start = 0
        if cursor:
            found = next((i for i,item in enumerate(additions) if item['card_id'] == cursor), None)
            if found is None:
                raise ValueError('分页游标不属于当前报告')
            start = found + 1
        visible_added = additions[start:start+limit]
        if start + limit < len(additions):
            report['nextCursor'] = visible_added[-1]['card_id']
        for item in cards:
            if item['section'] == 'added' and item not in visible_added:
                continue
            content = json.loads(item['content_json'])
            # This is an internal read-back proof for B76 publication.  The
            # typed report DTO deliberately exposes only its digest.
            content.pop('deliveryIdentity', None)
            selection = store._current_company_window_action(conn, company_window_id=item['company_window_id'])
            state = selection[1] if selection else 'unhandled'
            card = {**content, 'cardId': item['card_id'], 'companyCode': item['company_code'],
                    'companyName': item['company_name'], 'rank': item['rank'], 'section': item['section'],
                    'companyWindowId': item['company_window_id'], 'currentSelectionState': {'observe': 'kept', 'skip': 'skipped', 'restore': 'unhandled', 'withdraw': 'unhandled', 'selected': 'kept', 'skipped': 'skipped', 'unhandled': 'unhandled'}[state],
                    'd1TradeDate': item['d1_trade_date'], 'd2TradeDate': item['d2_trade_date'], 'sampleClass': item['sample_class'], 'strategyVersion': 'K10-v2'}
            for catalyst in card['catalysts']:
                catalyst['lifecycleState'] = _current_lifecycle_state(conn, catalyst['opportunityId'])
            targets = conn.execute('SELECT opportunity_id FROM k10_opportunities WHERE company_window_id=?', (item['company_window_id'],)).fetchall()
            card['canSelect'] = store.selection_allowed_for_states(_current_lifecycle_state(conn,target[0]) for target in targets)
            report[{'evening':'eveningCards', 'updated':'updatedCards', 'added':'addedCards'}[item['section']]].append(card)
    return report


def read_report_materials(*, db_path: Path, report_id: str, cursor: str | None = None, limit: int = 30) -> dict[str, Any] | None:
    """Read one report's derived materials; no latest fallback and no DDL."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        exists = conn.execute('SELECT 1 FROM k10_v2_report_runs WHERE report_id=?', (report_id,)).fetchone()
        if exists is None:
            return None
        rows = list(conn.execute(
            'SELECT material_id,event_id,event_title,facts_json,company_relations_json,uncertainties_json,source_refs_json,as_of '
            'FROM k10_v2_report_materials WHERE report_id=? ORDER BY material_id', (report_id,),
        ))
    start = 0
    if cursor is not None:
        found = next((index for index, row in enumerate(rows) if row[0] == cursor), None)
        if found is None:
            raise ValueError('分页游标不属于当前报告材料')
        start = found + 1
    visible = rows[start:start + limit]
    return {"reportId": report_id, "items": [
        {"materialId": row[0], "eventId": row[1], "eventTitle": row[2], "facts": json.loads(row[3]),
         "companyRelations": json.loads(row[4]), "uncertainties": json.loads(row[5]),
         "sourceRefs": json.loads(row[6]), "asOf": row[7]} for row in visible
    ], "nextCursor": visible[-1][0] if start + limit < len(rows) else None}


def _record_incomplete_report(conn, *, scan_id, snapshot_id, state, error_code, created_at):
    row = conn.execute('SELECT window_kind,cutoff_at FROM k10_scans WHERE scan_id=?', (scan_id,)).fetchone()
    if not row:
        return
    parent = conn.execute("SELECT report_id FROM k10_v2_report_runs WHERE window_kind='evening' AND available_at IS NOT NULL AND julianday(cutoff_at)<julianday(?) ORDER BY julianday(cutoff_at) DESC LIMIT 1", (row[1],)).fetchone() if row[0] == 'morning' else None
    message = {'insufficient_balance': '余额不足，已停止本次任务',
               'rate_limited': '供应商限流，等待重试' if state == 'retry_pending' else '供应商限流，已停止本次任务',
               'not_configured': '今天没跑成 · 参数未配置'}.get(error_code, '今天没跑成 · 处理未完成')
    error = {'reason': error_code or 'incomplete', 'message': message}
    conn.execute('INSERT INTO k10_v2_report_runs VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(report_id) DO UPDATE SET status=excluded.status,error_json=excluded.error_json WHERE k10_v2_report_runs.available_at IS NULL',
        ('report_'+scan_id, scan_id, snapshot_id, row[0], parent[0] if parent else None, row[1], None, None, state, _json(error), created_at))


def record_incomplete_report(*, db_path: Path, scan_id: str, snapshot_id: str, state: str, error_code: str | None, created_at: str):
    """Expose the incomplete run while retaining preceding reports in history."""
    with write_connection(db_path) as conn:
        require_schema(conn)
        _record_incomplete_report(conn, scan_id=scan_id, snapshot_id=snapshot_id, state=state,
                                  error_code=error_code, created_at=created_at)


def ensure_b78_delivery_report(*, db_path: Path, scan_id: str, snapshot_id: str, kind: str,
                               created_at: str, delivery_deadline_at: str | None) -> str:
    """Create this task's reader identity before any long-running work.

    It is intentionally a controlled producer write, never an API fallback.
    The report remains non-public (`availableAt` is null), while a morning
    reader can still resolve its own frozen deadline instead of yesterday's
    report when a paid call is in flight.
    """
    if kind not in {"morning", "evening"}:
        raise ValueError("报告窗口无效")
    if kind == "evening" and delivery_deadline_at is not None:
        raise ValueError("晚报不得写整报截止时间")
    with write_connection(db_path) as conn:
        return _ensure_b78_delivery_report(conn, scan_id=scan_id, snapshot_id=snapshot_id,
            kind=kind, created_at=created_at, delivery_deadline_at=delivery_deadline_at, state="running")


def _ensure_b78_delivery_report(conn, *, scan_id: str, snapshot_id: str, kind: str,
                                created_at: str, delivery_deadline_at: str | None, state: str) -> str:
    require_schema(conn)
    if delivery_deadline_at is not None:
        delivery_deadline_at = store._utc_instant(delivery_deadline_at)
    row = conn.execute('SELECT window_kind,cutoff_at FROM k10_scans WHERE scan_id=?', (scan_id,)).fetchone()
    if row is None or row[0] != kind:
        raise ValueError("报告扫描身份无效")
    parent = (conn.execute(
        "SELECT report_id FROM k10_v2_report_runs WHERE window_kind='evening' AND available_at IS NOT NULL "
        "AND julianday(cutoff_at)<julianday(?) ORDER BY julianday(cutoff_at) DESC LIMIT 1", (row[1],)
    ).fetchone() if kind == "morning" else None)
    report_id = "report_" + scan_id
    existing = conn.execute(
        'SELECT scan_id,strategy_snapshot_id,window_kind,available_at FROM k10_v2_report_runs WHERE report_id=?',
        (report_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing[:3]) != (scan_id, snapshot_id, kind):
            raise ValueError("报告身份已绑定不同冻结输入")
    else:
        conn.execute(
            'INSERT INTO k10_v2_report_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (report_id, scan_id, snapshot_id, kind, parent[0] if parent else None, row[1],
             None, None, state, None, created_at),
        )
    if state == 'running':
        conn.execute("UPDATE k10_v2_report_runs SET status='running',parent_report_id=? WHERE report_id=? AND status='queued'",
                     (parent[0] if parent else None, report_id))
    metadata = conn.execute(
        'SELECT delivery_deadline_at FROM k10_v2_report_delivery_metadata WHERE report_id=?', (report_id,)
    ).fetchone()
    if metadata is not None:
        if (store._utc_instant(metadata[0]) if metadata[0] else None) != delivery_deadline_at:
            raise ValueError("报告冻结截止时间不可覆盖")
    else:
        write_report_materials(conn, report_id=report_id, materials=[], result_available_at=None,
                               delivery_deadline_at=delivery_deadline_at)
    return report_id


def _failed_delivery_counts(conn, *, task_id: str, coverage: Mapping[str, Any]) -> dict[str, int]:
    """Project durable work counts without inventing a ranking after failure."""
    raw_titles = coverage.get("titleDispositionCounts")
    if (isinstance(raw_titles, Mapping)
            and all(isinstance(raw_titles.get(key), int) and not isinstance(raw_titles.get(key), bool)
                    and raw_titles[key] >= 0
                    for key in ("input", "processed", "failed", "unprocessed"))
            and raw_titles["processed"] + raw_titles["failed"] + raw_titles["unprocessed"] == raw_titles["input"]):
        title_input = int(raw_titles["input"])
        title_processed = int(raw_titles["processed"])
        title_failed = int(raw_titles["failed"])
        title_unprocessed = int(raw_titles["unprocessed"])
    else:
        refs = coverage.get("titleInputManifest")
        if not isinstance(refs, list):
            refs = coverage.get("inputDocumentRefs")
        title_input = len(refs) if isinstance(refs, list) else 0
        title_processed = title_failed = 0
        title_unprocessed = title_input

    rows = conn.execute(
        "SELECT execution_status,COUNT(*) FROM k10_research_snapshot_revisions current "
        "WHERE current.task_id=? AND current.revision=("
        "SELECT MAX(latest.revision) FROM k10_research_snapshot_revisions latest "
        "WHERE latest.snapshot_id=current.snapshot_id) GROUP BY execution_status",
        (task_id,),
    ).fetchall()
    terminal_snapshots = sum(int(row[1]) for row in rows)
    declared_events = coverage.get("researchEventCount")
    event_input = max(terminal_snapshots,
                      int(declared_events) if isinstance(declared_events, int)
                      and not isinstance(declared_events, bool) and declared_events >= 0 else 0)
    event_failed = sum(int(row[1]) for row in rows if row[0] != "ok")
    event_processed = sum(int(row[1]) for row in rows if row[0] == "ok")
    event_unprocessed = max(0, event_input - event_failed - event_processed)
    return {
        "titleInput": title_input, "titleProcessed": title_processed,
        "titleFailed": title_failed, "titleUnprocessed": title_unprocessed,
        "eventInput": event_input, "eventProcessed": event_processed,
        "eventFailed": event_failed, "eventUnprocessed": event_unprocessed,
        "comparableCompanies": 0, "publishedCompanies": 0,
    }


def _b76_failed_delivery(conn, *, task_id: str, scan_id: str, coverage: Mapping[str, Any],
                         checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Make the diagnostic delivery manifest for a terminal pre-publication failure.

    This is deliberately a zero-card, ``rankingScope=none`` projection.  It
    exposes the durable work that did happen, but never promotes a stale
    priority result or silently treats a failed global operation as a subset
    ranking.
    """
    from .delivery import delivery_gap, delivery_manifest

    failed = conn.execute(
        "SELECT stage,safe_error_code FROM k10_execution_item_checkpoints "
        "WHERE task_id=? AND status='failed' ORDER BY updated_at DESC,rowid DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    code = checkpoint.get("safeErrorCode") if isinstance(checkpoint.get("safeErrorCode"), str) else None
    stage = "execution"
    if failed is not None:
        stage = str(failed[0])
        if code is None and isinstance(failed[1], str):
            code = str(failed[1])
    code = code or "execution_failed"
    title_manifest = coverage.get("titleInputManifest")
    if not isinstance(title_manifest, list):
        title_manifest = coverage.get("inputDocumentRefs")
    return delivery_manifest(
        outcome="failed", ranking_scope="none",
        counts=_failed_delivery_counts(conn, task_id=task_id, coverage=coverage),
        gaps=[delivery_gap(
            stage=stage, unit_kind="report", unit_id=scan_id, reason_code=code,
            message="本次报告在公开前终止，未生成新的排序或机会卡。",
            company_scope_known=False,
        )],
        input_manifest=title_manifest if isinstance(title_manifest, list) else [],
        eligible_set=[], ranking_input=None,
    )


def _safe_failed_report_materials(
    conn, *, task_id: str, strategy_snapshot_id: str, as_of: str,
) -> list[dict[str, Any]]:
    """Rebuild only reader-safe event material after final ordering fails.

    The task has no formal publication at this point, so this deliberately
    reads the persisted event revision and the current event snapshot only. It
    never consults candidate/card/window state and never promotes a company
    relation without its frozen relation source.  The material is useful for a
    user to inspect completed research, but is not a recommendation.
    """
    names = {
        str(row[0]): str(row[1])
        for row in conn.execute(
            "SELECT company_code,company_name FROM k10_v2_universe_members WHERE snapshot_id=?",
            (strategy_snapshot_id,),
        )
    }
    rows = conn.execute(
        "SELECT r.snapshot_id,r.revision,r.event_id,r.event_revision,e.headline,e.facts_json,e.source_refs_json,"
        "round.result_json "
        "FROM k10_research_snapshot_revisions r "
        "JOIN (SELECT snapshot_id,MAX(revision) revision FROM k10_research_snapshot_revisions "
        "      WHERE task_id=? GROUP BY snapshot_id) latest "
        "  ON latest.snapshot_id=r.snapshot_id AND latest.revision=r.revision "
        "JOIN k10_event_revisions e ON e.event_id=r.event_id AND e.revision=r.event_revision "
        "LEFT JOIN k10_research_round_results round "
        "  ON round.snapshot_id=r.snapshot_id AND round.revision=r.revision "
        "WHERE r.task_id=? AND r.execution_status='ok' "
        "ORDER BY r.event_id,r.event_revision,r.snapshot_id",
        (task_id, task_id),
    ).fetchall()
    materials: list[dict[str, Any]] = []
    for snapshot_id, revision, event_id, event_revision, headline, facts_json, source_refs_json, round_json in rows:
        try:
            facts_payload = json.loads(facts_json)
            event_refs = json.loads(source_refs_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(facts_payload, Mapping) or not isinstance(event_refs, list):
            continue
        verification = facts_payload.get("verification")
        coverage = verification.get("coverage") if isinstance(verification, Mapping) else None
        if not isinstance(coverage, Mapping) or coverage.get("state") != "available":
            continue
        source_refs = [
            {"documentId": ref["documentId"], "revision": ref["revision"]}
            for ref in event_refs
            if isinstance(ref, Mapping) and isinstance(ref.get("documentId"), str) and ref["documentId"]
            and isinstance(ref.get("revision"), int) and not isinstance(ref["revision"], bool)
        ]
        if not source_refs:
            continue
        claims: list[dict[str, Any]] = []
        raw_claims = facts_payload.get("researchClaims")
        if isinstance(raw_claims, list):
            for claim in raw_claims:
                if not isinstance(claim, Mapping) or not isinstance(claim.get("text"), str) or not claim["text"].strip():
                    continue
                source = claim.get("sourceRef")
                claim_refs = ([{"documentId": source["documentId"], "revision": source["revision"]}]
                              if isinstance(source, Mapping) and isinstance(source.get("documentId"), str)
                              and source["documentId"] and isinstance(source.get("revision"), int)
                              and not isinstance(source["revision"], bool) else [])
                claims.append({"text": claim["text"].strip(), "sourceRefs": claim_refs})

        mappings: list[Mapping[str, Any]] = []
        for company_code, affected_stage, relation_json, inference_json, uncertainty in conn.execute(
            "SELECT company_code,affected_stage,relation_evidence_json,inference_json,uncertainty "
            "FROM k10_company_mappings WHERE event_id=? AND event_revision=? ORDER BY mapping_id",
            (event_id, event_revision),
        ):
            try:
                relation_refs = json.loads(relation_json)
                inference = json.loads(inference_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            mappings.append({"companyCode": str(company_code), "affectedStage": affected_stage,
                             "relationEvidence": relation_refs, "inference": inference, "uncertainty": uncertainty})
        # A B78 direct round persists its validated mapping in the round row;
        # use it when private publication persistence has not yet run.
        if not mappings and isinstance(round_json, str):
            try:
                direct = json.loads(round_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                direct = None
            conclusion = direct.get("conclusion") if isinstance(direct, Mapping) else None
            raw_mappings = conclusion.get("companyMappings") if isinstance(conclusion, Mapping) else None
            if isinstance(raw_mappings, list):
                mappings = [item for item in raw_mappings if isinstance(item, Mapping)]
        relations: list[dict[str, Any]] = []
        uncertainties: list[str] = []
        seen_codes: set[str] = set()
        for mapping in mappings:
            code = mapping.get("companyCode")
            refs = mapping.get("relationEvidence")
            stage = mapping.get("affectedStage")
            if (not isinstance(code, str) or code not in names or code in seen_codes
                    or not isinstance(refs, list) or not refs or not isinstance(stage, str) or not stage):
                continue
            relation_refs = [
                {"documentId": ref["documentId"], "revision": ref["revision"]}
                for ref in refs if isinstance(ref, Mapping) and isinstance(ref.get("documentId"), str)
                and ref["documentId"] and isinstance(ref.get("revision"), int)
                and not isinstance(ref["revision"], bool)
            ]
            if len(relation_refs) != len(refs):
                continue
            inference = mapping.get("inference")
            relation = inference.get("relation") if isinstance(inference, Mapping) else None
            relation = relation.strip() if isinstance(relation, str) and relation.strip() else "关联环节：" + stage
            relations.append({"companyCode": code, "companyName": names[code], "relation": relation,
                              "sourceRefs": relation_refs})
            seen_codes.add(code)
            if isinstance(mapping.get("uncertainty"), str) and mapping["uncertainty"].strip():
                uncertainties.append(mapping["uncertainty"].strip())
        if not isinstance(verification.get("state"), str) or verification["state"] != "verified":
            summary = verification.get("summary")
            uncertainties.append("独立核验尚未完成：" + (summary.strip() if isinstance(summary, str) and summary.strip() else "待核"))
        materials.append({
            "materialId": _id("material", str(event_id), str(event_revision)),
            "eventId": str(event_id), "eventTitle": str(headline), "facts": claims,
            "companyRelations": relations, "uncertainties": list(dict.fromkeys(uncertainties)),
            "sourceRefs": source_refs, "asOf": as_of,
        })
    return materials


def record_scan_task_failure(conn, *, task_id, status, stage, checkpoint, created_at):
    """Project every scan failure in the same transaction as its task terminal state.

    This also covers worker checks and failures before execute_scan creates a scan.
    Only the task's exact frozen v2 binding can supply the report identity.
    """
    if status not in {'failed', 'not_configured'}:
        return
    task = conn.execute('SELECT kind,payload_json,input_cutoff_at FROM k10_tasks WHERE task_id=?', (task_id,)).fetchone()
    if not task or task[0] not in {'evening_scan', 'morning_scan'}:
        return
    payload = json.loads(task[1])
    kind = payload.get('windowKind')
    if kind not in {'evening', 'morning'} or task[0] != kind + '_scan':
        return
    config_id, revision = payload.get('configId'), payload.get('configRevision')
    config = conn.execute('SELECT payload_json FROM k10_run_config_revisions WHERE config_id=? AND revision=?', (config_id, revision)).fetchone()
    if not config:
        return
    configuration = json.loads(config[0])
    snapshot_id = configuration.get('strategySnapshotId')
    if configuration.get('configVersion') != 'k10-v2' or not isinstance(snapshot_id, str):
        return
    if not conn.execute('SELECT 1 FROM k10_v2_strategy_snapshots WHERE snapshot_id=? AND config_id=? AND config_revision=?', (snapshot_id, config_id, revision)).fetchone():
        return  # No invented strategy fallback; configuration API reports the missing binding.
    try:
        cutoff = datetime.fromisoformat(task[2])
    except (TypeError, ValueError):
        return
    if cutoff.tzinfo is None:
        return
    scan_id = _id('scan', kind, cutoff.isoformat(), task_id)
    conn.execute('INSERT INTO k10_scans VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(scan_id) DO NOTHING',
        (scan_id, kind, store._utc_instant(task[2]), config_id, revision, status,
         _json({'pipelineState': 'task_failed', 'failureStage': stage}), created_at, created_at))
    conn.execute("UPDATE k10_scans SET status=?,completed_at=? WHERE scan_id=? AND status IN ('queued','running')", (status, created_at, scan_id))
    code = 'not_configured' if status == 'not_configured' else checkpoint.get('safeErrorCode')
    _record_incomplete_report(conn, scan_id=scan_id, snapshot_id=snapshot_id, state=status,
                              error_code=code, created_at=created_at)
    from .delivery import is_b76_runtime_contract, is_current_runtime_contract
    if not is_b76_runtime_contract(payload.get("runtimeContract")):
        return
    report = conn.execute(
        "SELECT report_id,available_at FROM k10_v2_report_runs WHERE scan_id=?", (scan_id,)
    ).fetchone()
    if report is None or report[1] is not None:
        return
    scan = conn.execute("SELECT coverage_json FROM k10_scans WHERE scan_id=?", (scan_id,)).fetchone()
    try:
        coverage = json.loads(scan[0]) if scan is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        coverage = {}
    if not isinstance(coverage, dict):
        coverage = {}
    delivery = coverage.get("delivery")
    if not isinstance(delivery, Mapping):
        delivery = _b76_failed_delivery(conn, task_id=task_id, scan_id=scan_id,
                                        coverage=coverage, checkpoint=checkpoint)
        coverage["delivery"] = delivery
        coverage["rankingInput"] = None
        conn.execute("UPDATE k10_scans SET coverage_json=? WHERE scan_id=?", (_json(coverage), scan_id))
    _write_report_delivery(conn, report_id=str(report[0]), delivery=dict(delivery))
    # A failed final sort never writes formal cards, selection windows, or an
    # outbox.  It can still leave the user a read-only bundle of completed
    # event facts and explicitly uncertain relations.  This lives in the same
    # terminal task transaction as the failed report identity and manifest.
    if is_current_runtime_contract(payload.get("runtimeContract")):
        materials = _safe_failed_report_materials(
            conn, task_id=str(task_id), strategy_snapshot_id=str(snapshot_id), as_of=created_at,
        )
        write_report_materials(
            conn, report_id=str(report[0]), materials=materials,
            result_available_at=created_at, delivery_deadline_at=None,
        )
    if isinstance(checkpoint, dict):
        checkpoint.setdefault("scanId", scan_id)
        checkpoint.setdefault("delivery", dict(delivery))


def read_morning_result(*, task_id, input_sha256, db_path, work_item_id: str | None = None):
    if work_item_id is not None:
        with read_connection(db_path) as conn:
            row = conn.execute(
                'SELECT input_sha256,result_json,captured_at FROM k10_morning_review_work_items WHERE work_item_id=?',
                (work_item_id,),
            ).fetchone()
        if row is None or row[1] is None:
            return None
        if row[0] != input_sha256:
            raise ValueError('晨间复核恢复输入发生变化')
        return {'raw': json.loads(row[1]), 'capturedAt': row[2]}
    with read_connection(db_path) as conn:
        row=conn.execute('SELECT input_sha256,result_json,captured_at FROM k10_morning_review_results WHERE task_id=?',(task_id,)).fetchone()
    if row is None:return None
    if row[0]!=input_sha256:raise ValueError('晨间复核恢复输入发生变化')
    return {'raw':json.loads(row[1]),'capturedAt':row[2]}


def save_morning_result(*, task_id, input_sha256, raw, captured_at, db_path, work_item_id: str | None = None):
    if work_item_id is not None:
        with write_connection(db_path) as conn:
            row = conn.execute(
                'SELECT input_sha256,result_json,captured_at FROM k10_morning_review_work_items WHERE work_item_id=?',
                (work_item_id,),
            ).fetchone()
            if row is None:
                raise ValueError('晨间父任务工作项不存在')
            if row[0] != input_sha256:
                raise ValueError('晨间复核恢复输入发生变化')
            if row[1] is not None:
                return {'raw': json.loads(row[1]), 'capturedAt': row[2]}
            conn.execute(
                "UPDATE k10_morning_review_work_items SET result_json=?,captured_at=?,updated_at=? WHERE work_item_id=?",
                (_json(raw), captured_at, captured_at, work_item_id),
            )
        return {'raw': raw, 'capturedAt': captured_at}
    with write_connection(db_path) as conn:
        row=conn.execute('SELECT input_sha256,result_json,captured_at FROM k10_morning_review_results WHERE task_id=?',(task_id,)).fetchone()
        if row is not None:
            if row[0]!=input_sha256:raise ValueError('晨间复核恢复输入发生变化')
            return {'raw':json.loads(row[1]),'capturedAt':row[2]}
        conn.execute('INSERT INTO k10_morning_review_results VALUES (?,?,?,?)',(task_id,input_sha256,_json(raw),captured_at))
    return {'raw':raw,'capturedAt':captured_at}


def ensure_morning_review_work_item(*, scan_id: str, work_item_id: str, input_sha256: str,
                                    created_at: str, db_path: Path) -> None:
    """Reserve one parent-owned morning work item; no task is enqueued."""
    with write_connection(db_path) as conn:
        require_schema(conn)
        existing = conn.execute(
            'SELECT input_sha256 FROM k10_morning_review_work_items WHERE scan_id=? AND work_item_id=?',
            (scan_id, work_item_id),
        ).fetchone()
        if existing is not None:
            if existing[0] != input_sha256:
                raise ValueError('晨间父任务工作项冻结输入不一致')
            return
        conn.execute(
            'INSERT INTO k10_morning_review_work_items('
            'scan_id,work_item_id,input_sha256,status,result_json,report_item_json,safe_error_code,captured_at,created_at,updated_at'
            ') VALUES (?,?,?,?,?,?,?,?,?,?)',
            (scan_id, work_item_id, input_sha256, 'running', None, None, None, None, created_at, created_at),
        )


def read_morning_review_work_item(*, scan_id: str, work_item_id: str, input_sha256: str,
                                   db_path: Path) -> dict[str, Any]:
    """Read one parent-owned review without re-opening its external operation.

    A terminal row is the authoritative result for a reclaimed parent task.
    Its frozen input is verified before the caller can aggregate the stored
    report item; a missing or malformed terminal projection is corruption, not
    permission to call the provider again.
    """
    with read_connection(db_path) as conn:
        row = conn.execute(
            'SELECT input_sha256,status,report_item_json,safe_error_code FROM k10_morning_review_work_items '
            'WHERE scan_id=? AND work_item_id=?',
            (scan_id, work_item_id),
        ).fetchone()
    if row is None:
        raise ValueError('晨间父任务工作项不存在')
    if row[0] != input_sha256:
        raise ValueError('晨间父任务工作项冻结输入不一致')
    raw_item = row[2]
    item = None
    if raw_item is not None:
        try:
            item = json.loads(raw_item)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError('晨间父任务工作项报告投影损坏') from exc
        if not isinstance(item, dict):
            raise ValueError('晨间父任务工作项报告投影无效')
    return {'status': row[1], 'reportItem': item, 'safeErrorCode': row[3]}


def finish_morning_review_work_item(*, scan_id: str, work_item_id: str, status: str,
                                    result: Mapping[str, Any] | None, safe_error_code: str | None,
                                    updated_at: str, db_path: Path) -> None:
    if status not in {'completed', 'failed', 'not_configured'}:
        raise ValueError('晨间父任务工作项终态无效')
    with write_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            'SELECT status,report_item_json,safe_error_code FROM k10_morning_review_work_items WHERE scan_id=? AND work_item_id=?',
            (scan_id, work_item_id),
        ).fetchone()
        if row is None:
            raise ValueError('晨间父任务工作项不存在')
        encoded = None if result is None else _json(dict(result))
        if row[0] != 'running':
            if (row[0], row[1], row[2]) != (status, encoded, safe_error_code):
                raise ValueError('晨间父任务工作项终态不可覆盖')
            return
        conn.execute(
            'UPDATE k10_morning_review_work_items SET status=?,report_item_json=?,safe_error_code=?,updated_at=? '
            'WHERE scan_id=? AND work_item_id=?',
            (status, encoded, safe_error_code, updated_at, scan_id, work_item_id),
        )


def refresh_morning_coverage_for_scan(conn, *, scan_id: str) -> bool:
    """Project durable morning-child outcomes into an already-created V2 report.

    A B76 parent creates its report after it has run the frozen children.  The
    child terminal hook may therefore have observed no report yet.  Calling
    this helper from the parent's publication transaction closes that ordering
    gap without changing the immutable discovery delivery manifest.
    """
    if not isinstance(scan_id, str) or not scan_id:
        return False
    report=conn.execute('SELECT report_id FROM k10_v2_report_runs WHERE scan_id=?',(scan_id,)).fetchone()
    if not report:
        return False
    # B78 reviews are durable entries of the parent scan, not leased child
    # tasks.  Project those entries and the immutable aggregate into the V2
    # report while the parent publication transaction is still open.  The
    # legacy child projection below remains read-only compatibility for old
    # reports; a B78 scan must never recreate it.
    parent_items = conn.execute(
        "SELECT work_item_id,status,report_item_json,safe_error_code "
        "FROM k10_morning_review_work_items WHERE scan_id=? ORDER BY work_item_id",
        (scan_id,),
    ).fetchall()
    if parent_items:
        aggregate = conn.execute(
            "SELECT report_id,status,coverage_json FROM k10_morning_reports WHERE scan_id=? "
            "ORDER BY revision DESC,generated_at DESC,report_id DESC LIMIT 1",
            (scan_id,),
        ).fetchone()
        aggregate_gaps: list[str] = []
        aggregate_status = "partial"
        aggregate_report_id: str | None = None
        if aggregate is not None:
            aggregate_report_id = str(aggregate[0])
            aggregate_status = str(aggregate[1])
            try:
                aggregate_coverage = json.loads(aggregate[2])
            except (TypeError, ValueError, json.JSONDecodeError):
                aggregate_coverage = {}
            raw_gaps = aggregate_coverage.get("gaps") if isinstance(aggregate_coverage, Mapping) else None
            if isinstance(raw_gaps, list):
                aggregate_gaps = [gap for gap in raw_gaps if isinstance(gap, str) and gap]

        report_items: dict[str, tuple[str | None, str | None, str | None, Mapping[str, Any]]] = {}
        fallback_items: list[tuple[str | None, str | None, str | None, Mapping[str, Any]]] = []
        if aggregate_report_id is not None:
            for item_id, opportunity_id, window_id, item_status, content_json in conn.execute(
                    "SELECT item_id,opportunity_id,company_window_id,status,content_json "
                    "FROM k10_morning_report_items WHERE report_id=?", (aggregate_report_id,)):
                try:
                    content = json.loads(content_json)
                except (TypeError, ValueError, json.JSONDecodeError):
                    content = {}
                if not isinstance(content, Mapping):
                    content = {}
                item = (str(opportunity_id) if opportunity_id is not None else None,
                        str(window_id) if window_id is not None else None,
                        str(item_status) if item_status is not None else None, content)
                work_item_id = content.get("workItemId")
                if isinstance(work_item_id, str) and work_item_id:
                    report_items[work_item_id] = item
                elif item[2] != "completed":
                    fallback_items.append(item)

        incomplete: list[dict[str, Any]] = []
        gaps: list[str] = []
        fallback_cursor = 0
        for work_item_id, status, raw_item, safe_error_code in parent_items:
            work_status = str(status)
            item = report_items.get(str(work_item_id))
            if item is None and work_status != "completed" and fallback_cursor < len(fallback_items):
                item = fallback_items[fallback_cursor]
                fallback_cursor += 1
            source_status = None
            if item is not None:
                content = item[3]
                item_coverage = content.get("coverage")
                source_status = item_coverage.get("status") if isinstance(item_coverage, Mapping) else None
            if isinstance(source_status, str) and source_status != "complete":
                gaps.append("morning_source_" + source_status)
            if work_status == "completed":
                continue
            gaps.append("morning_review_" + work_status)
            opportunity_id, window_id = (item[0], item[1]) if item is not None else (None, None)
            company_code = None
            if window_id is not None:
                row = conn.execute(
                    "SELECT company_code FROM k10_company_windows WHERE company_window_id=?", (window_id,)
                ).fetchone()
                company_code = str(row[0]) if row is not None else None
            # A corrupt historical aggregate is still a partial report, but it
            # cannot be presented as a made-up opportunity review.
            if opportunity_id is not None and window_id is not None and company_code is not None:
                incomplete.append({
                    "taskId": str(work_item_id), "opportunityId": opportunity_id,
                    "companyWindowId": window_id, "companyCode": company_code,
                    "status": work_status,
                    "reason": (str(safe_error_code) if isinstance(safe_error_code, str) and safe_error_code
                               else "晨间复核未完成"),
                })

        existing = conn.execute('SELECT content_json FROM k10_v2_report_coverage WHERE report_id=?', (report[0],)).fetchone()
        try:
            coverage = json.loads(existing[0]) if existing is not None else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            coverage = {}
        if not isinstance(coverage, dict):
            coverage = {}
        existing_gaps = coverage.get('coverageGaps')
        if not isinstance(existing_gaps, list) or any(not isinstance(value, str) for value in existing_gaps):
            existing_gaps = []
        existing_gaps = [gap for gap in existing_gaps if not gap.startswith((
            'morning_source_', 'morning_review_',
        )) and gap not in {'needs_review_items', 'failed_report_items'}]
        coverage['coverageGaps'] = sorted(set([*existing_gaps, *aggregate_gaps, *gaps]))
        coverage['incompleteReviews'] = incomplete
        delivery = coverage.get('delivery')
        delivery_partial = isinstance(delivery, Mapping) and delivery.get('outcome') == 'partial'
        partial = (aggregate_status == 'partial' or any(str(row[1]) != 'completed' for row in parent_items)
                   or delivery_partial)
        error = None
        if incomplete:
            error = {
                'reason': ('morning_review_not_configured'
                           if any(item['status'] == 'not_configured' for item in incomplete)
                           else 'morning_review_failed'),
                'message': '部分晨间复核未完成，已完成内容保留',
            }
        conn.execute('INSERT INTO k10_v2_report_coverage VALUES (?,?) ON CONFLICT(report_id) DO UPDATE SET content_json=excluded.content_json',
                     (report[0], _json(coverage)))
        conn.execute('UPDATE k10_v2_report_runs SET status=?,error_json=? WHERE report_id=?',
                     ('partial' if partial else 'completed', None if error is None else _json(error), report[0]))
        return partial
    incomplete=[];gaps=[]
    # The parent may have a truthful partial aggregate even though every
    # child task reached a terminal `completed` state: a completed review can
    # still conclude that its opportunity needs review.  Preserve those
    # aggregate gaps when the parent publishes, otherwise the refresh below
    # would overwrite the V2 report back to `completed` before the atomic task
    # finalizer verifies it.  Task-state projections stay mutable so an
    # explicitly recovered failed child can later clear only its own gap.
    aggregate_gaps: list[str] = []
    aggregate_needs_review = False
    aggregate_report_id: str | None = None
    aggregate = conn.execute(
        "SELECT report_id,status,coverage_json FROM k10_morning_reports WHERE scan_id=? "
        "ORDER BY revision DESC,generated_at DESC,report_id DESC LIMIT 1",
        (scan_id,),
    ).fetchone()
    if aggregate is not None:
        aggregate_report_id = str(aggregate[0])
        try:
            aggregate_coverage = json.loads(aggregate[2])
        except (TypeError, ValueError, json.JSONDecodeError):
            aggregate_coverage = {}
        raw_aggregate_gaps = aggregate_coverage.get("gaps") if isinstance(aggregate_coverage, Mapping) else None
        if isinstance(raw_aggregate_gaps, list):
            # `morning_review_*` and the synthetic failed-item counter are
            # derived again from durable child rows below.  Retaining either
            # would make a later successful same-task recovery look partial
            # forever.  The remaining aggregate gaps describe a completed
            # review conclusion or an immutable parent/discovery boundary.
            aggregate_gaps = [
                gap for gap in raw_aggregate_gaps
                if isinstance(gap, str) and gap
                and not gap.startswith("morning_review_")
                and gap != "failed_report_items"
            ]
    child_opportunities: set[str] = set()
    dynamic_needs_review = False
    for child in conn.execute(
        "SELECT task_id,status,error_text,payload_json,checkpoint_json FROM k10_tasks WHERE kind='morning_review'"
    ):
        try:
            payload=json.loads(child[3])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if payload.get('parentScanId')!=scan_id:continue
        sample=conn.execute(
            'SELECT opportunity_id,company_window_id,company_code FROM k10_publication_samples '
            'WHERE candidate_id=? ORDER BY rowid LIMIT 1', (payload.get('candidateId'),)
        ).fetchone()
        if sample:
            child_opportunities.add(str(sample[0]))
        if payload.get('sourceStatus')!='complete':gaps.append('morning_source_'+str(payload.get('sourceStatus')))
        if child[1]=='completed':
            try:
                checkpoint = json.loads(child[4])
            except (TypeError, ValueError, json.JSONDecodeError):
                checkpoint = {}
            if isinstance(checkpoint, Mapping) and checkpoint.get('reportSection') == 'needs_review':
                dynamic_needs_review = True
            continue
        gaps.append('morning_review_'+child[1])
        if sample:
            reason = '模型服务限流，等待延后重试' if child[1]=='queued' and child[2]=='rate_limited' else child[2] or '晨间复核尚未完成'
            incomplete.append(dict(taskId=child[0],opportunityId=sample[0],companyWindowId=sample[1],companyCode=sample[2],status=child[1],reason=reason))
    if aggregate_report_id is not None:
        # The parent aggregate is immutable history.  Its `needs_review` rows
        # that correspond to a child are a *projection* of that child at the
        # time the parent first attempted its report: after an explicit same
        # task recovery the current child checkpoint decides whether that gap
        # remains.  A fallback with no child stays an intrinsic aggregate gap.
        aggregate_needs_review = any(
            row[0] not in child_opportunities
            for row in conn.execute(
                "SELECT opportunity_id FROM k10_morning_report_items "
                "WHERE report_id=? AND group_key='needs_review'", (aggregate_report_id,)
            )
        )
    if 'needs_review_items' in aggregate_gaps:
        aggregate_gaps = [gap for gap in aggregate_gaps if gap != 'needs_review_items']
        if aggregate_needs_review or dynamic_needs_review:
            aggregate_gaps.append('needs_review_items')
    existing = conn.execute('SELECT content_json FROM k10_v2_report_coverage WHERE report_id=?', (report[0],)).fetchone()
    try:
        coverage = json.loads(existing[0]) if existing is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        coverage = {}
    if not isinstance(coverage, dict):
        coverage = {}
    existing_gaps = coverage.get('coverageGaps')
    if not isinstance(existing_gaps, list) or any(not isinstance(item, str) for item in existing_gaps):
        existing_gaps = []
    # These are a projection of mutable child state.  Preserve discovery
    # delivery gaps, but replace a previous child/source projection when the
    # user explicitly recovers the same frozen review task.
    existing_gaps = [gap for gap in existing_gaps if not gap.startswith((
        'morning_source_', 'morning_review_',
    )) and gap != 'needs_review_items']
    coverage['coverageGaps'] = sorted(set([*existing_gaps, *aggregate_gaps, *gaps]))
    coverage['incompleteReviews'] = incomplete
    delivery = coverage.get('delivery')
    delivery_partial = isinstance(delivery, Mapping) and delivery.get('outcome') == 'partial'
    # A scan records the immutable state when the parent closed.  The V2
    # report also projects the current independently retryable children, so a
    # recovered child can clear a review-only partial without rewriting that
    # historical scan or a discovery-level partial delivery.
    status='partial' if aggregate_gaps or gaps or delivery_partial else 'completed'
    error=({'reason':'morning_review_not_configured' if any(row['status']=='not_configured' for row in incomplete) else 'morning_review_failed',
            'message':'部分晨间复核未完成，已完成内容保留'} if incomplete else None)
    conn.execute('INSERT INTO k10_v2_report_coverage VALUES (?,?) ON CONFLICT(report_id) DO UPDATE SET content_json=excluded.content_json',(report[0],_json(coverage)))
    conn.execute('UPDATE k10_v2_report_runs SET status=?,error_json=? WHERE report_id=?',(status,None if error is None else _json(error),report[0]))
    return bool(gaps)


def refresh_morning_coverage_for_task(conn, *, task_id):
    task=conn.execute('SELECT kind,payload_json FROM k10_tasks WHERE task_id=?',(task_id,)).fetchone()
    if not task or task[0]!='morning_review':
        return False
    try:
        payload=json.loads(task[1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    scan_id=payload.get('parentScanId') if isinstance(payload, Mapping) else None
    return refresh_morning_coverage_for_scan(conn, scan_id=scan_id)
