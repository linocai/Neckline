"""K10-v2 strategy binding and daily company report ledger."""
from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import store
from .config import validate_run_config, validate_execution_config
from .schema import read_connection, require_schema, write_connection
from .v2_profiles import INPUT_HASHES, STRATEGY_SHA256, UNIVERSE_ID, PROFILES_ID, read_profiles
from .universe import Eligibility


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _id(prefix, *parts):
    return prefix + '_' + sha256('\x1f'.join(parts).encode()).hexdigest()[:32]


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


def publish_cards(conn, *, report_id: str, scan_id: str, kind: str, snapshot_id: str, inputs, available_at: str):
    """Called inside opportunity publication transaction, including retries."""
    if conn.execute('SELECT 1 FROM k10_v2_report_runs WHERE report_id=? AND available_at IS NOT NULL', (report_id,)).fetchone():
        return
    cutoff, status = conn.execute('SELECT cutoff_at,status FROM k10_scans WHERE scan_id=?', (scan_id,)).fetchone()
    parent = None
    parent_codes = set()
    if kind == 'morning':
        row = conn.execute("SELECT report_id FROM k10_v2_report_runs WHERE window_kind='evening' AND julianday(cutoff_at)<julianday(?) ORDER BY julianday(cutoff_at) DESC LIMIT 1", (cutoff,)).fetchone()
        parent = row[0] if row else None
        if parent:
            parent_codes = {row[0] for row in conn.execute('SELECT company_code FROM k10_v2_report_cards WHERE report_id=?', (parent,))}
    conn.execute('INSERT INTO k10_v2_report_runs VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(report_id) DO UPDATE SET verification_cutoff_at=excluded.verification_cutoff_at,available_at=excluded.available_at,status=excluded.status,error_json=NULL',
                 (report_id, scan_id, snapshot_id, kind, parent, cutoff, available_at, available_at, status, None, available_at))
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
        coverage=conn.execute('SELECT content_json FROM k10_v2_report_coverage WHERE report_id=?',(row['report_id'],)).fetchone()
        report.update(json.loads(coverage[0]) if coverage else {'coverageGaps':[], 'incompleteReviews':[]})
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
            selection = store._current_company_window_action(conn, company_window_id=item['company_window_id'])
            state = selection[1] if selection else 'unhandled'
            card = {**json.loads(item['content_json']), 'cardId': item['card_id'], 'companyCode': item['company_code'],
                    'companyName': item['company_name'], 'rank': item['rank'], 'section': item['section'],
                    'companyWindowId': item['company_window_id'], 'currentSelectionState': {'observe': 'kept', 'skip': 'skipped', 'restore': 'unhandled', 'withdraw': 'unhandled', 'selected': 'kept', 'skipped': 'skipped', 'unhandled': 'unhandled'}[state],
                    'd1TradeDate': item['d1_trade_date'], 'd2TradeDate': item['d2_trade_date'], 'sampleClass': item['sample_class'], 'strategyVersion': 'K10-v2'}
            for catalyst in card['catalysts']:
                catalyst['lifecycleState'] = _current_lifecycle_state(conn, catalyst['opportunityId'])
            targets = conn.execute('SELECT opportunity_id FROM k10_opportunities WHERE company_window_id=?', (item['company_window_id'],)).fetchall()
            card['canSelect'] = store.selection_allowed_for_states(_current_lifecycle_state(conn,target[0]) for target in targets)
            report[{'evening':'eveningCards', 'updated':'updatedCards', 'added':'addedCards'}[item['section']]].append(card)
    return report


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


def read_morning_result(*, task_id, input_sha256, db_path):
    with read_connection(db_path) as conn:
        row=conn.execute('SELECT input_sha256,result_json,captured_at FROM k10_morning_review_results WHERE task_id=?',(task_id,)).fetchone()
    if row is None:return None
    if row[0]!=input_sha256:raise ValueError('晨间复核恢复输入发生变化')
    return {'raw':json.loads(row[1]),'capturedAt':row[2]}


def save_morning_result(*, task_id, input_sha256, raw, captured_at, db_path):
    with write_connection(db_path) as conn:
        row=conn.execute('SELECT input_sha256,result_json,captured_at FROM k10_morning_review_results WHERE task_id=?',(task_id,)).fetchone()
        if row is not None:
            if row[0]!=input_sha256:raise ValueError('晨间复核恢复输入发生变化')
            return {'raw':json.loads(row[1]),'capturedAt':row[2]}
        conn.execute('INSERT INTO k10_morning_review_results VALUES (?,?,?,?)',(task_id,input_sha256,_json(raw),captured_at))
    return {'raw':raw,'capturedAt':captured_at}


def refresh_morning_coverage_for_task(conn, *, task_id):
    task=conn.execute('SELECT kind,payload_json FROM k10_tasks WHERE task_id=?',(task_id,)).fetchone()
    if not task or task[0]!='morning_review':return
    scan_id=json.loads(task[1]).get('parentScanId')
    if not scan_id:return
    report=conn.execute('SELECT report_id FROM k10_v2_report_runs WHERE scan_id=?',(scan_id,)).fetchone()
    if not report:return
    incomplete=[];gaps=[]
    for child in conn.execute("SELECT task_id,status,error_text,payload_json FROM k10_tasks WHERE kind='morning_review'"):
        payload=json.loads(child[3])
        if payload.get('parentScanId')!=scan_id:continue
        if payload.get('sourceStatus')!='complete':gaps.append('morning_source_'+str(payload.get('sourceStatus')))
        if child[1]=='completed':continue
        gaps.append('morning_review_'+child[1])
        sample=conn.execute('SELECT opportunity_id,company_window_id,company_code FROM k10_publication_samples WHERE candidate_id=? ORDER BY rowid LIMIT 1',(payload.get('candidateId'),)).fetchone()
        if sample:
            reason = '模型服务限流，等待延后重试' if child[1]=='queued' and child[2]=='rate_limited' else child[2] or '晨间复核尚未完成'
            incomplete.append(dict(taskId=child[0],opportunityId=sample[0],companyWindowId=sample[1],companyCode=sample[2],status=child[1],reason=reason))
    coverage=dict(coverageGaps=sorted(set(gaps)),incompleteReviews=incomplete)
    original=conn.execute('SELECT status FROM k10_scans WHERE scan_id=?',(scan_id,)).fetchone()
    status='partial' if gaps else original[0]
    error=({'reason':'morning_review_not_configured' if any(row['status']=='not_configured' for row in incomplete) else 'morning_review_failed',
            'message':'部分晨间复核未完成，已完成内容保留'} if incomplete else None)
    conn.execute('INSERT INTO k10_v2_report_coverage VALUES (?,?) ON CONFLICT(report_id) DO UPDATE SET content_json=excluded.content_json',(report[0],_json(coverage)))
    conn.execute('UPDATE k10_v2_report_runs SET status=?,error_json=? WHERE report_id=?',(status,None if error is None else _json(error),report[0]))
