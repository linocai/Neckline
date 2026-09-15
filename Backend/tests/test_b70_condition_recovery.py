"""Fresh independent review: paragraph conditions and versioned catalogue recovery."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from neckline.k10 import research_material as material
from neckline.k10 import research_runtime as runtime_module
from neckline.k10.research_context import PROTOCOL, project_packet
from neckline.k10.research_contracts import ResearchStageResult
from tests.test_b70_context_repair import source_read
from tests.test_b70_qualification_repair import document, evidence, assert_exact_ranges


@pytest.mark.parametrize('caveat', [
    '本次交易尚需获得监管批准，能否完成存在不确定性。',
    '公司尚未签订正式合同。',
    '该金额不构成收入，也不代表实际采购。',
    '该订单仅为意向上限。',
    '该项目以主管部门批准为前提。',
    '本次交易需提交股东大会审议通过后方可实施。',
    '本协议须双方签字盖章后生效。',
    '该合同将在主管部门批准后生效。',
    '相关收入应以最终审计结果为准。',
    '该合同金额不少于十亿元。',
    '公司不同意履行该项合同。',
    '本协议未续期。', '该合同未续签。', '相关款项未到账。', '相关许可未延长。',
    '本次交易需通过国家市场监督管理总局审查。', '各方须遵守保密义务。',
    '公司应确保资金按时到账。', '该项目需等待主管部门批复。',
    'The transaction remains subject to regulatory approval.',
])
@pytest.mark.parametrize('position', ['tail', 'middle', 'before_fact'])
def test_subject_conditions_anywhere_in_parent_survive_actual_packet(caveat, position):
    fact = '目标订单金额十亿元。'
    background = '普通背景说明。' * 2200
    body = (fact + background + caveat if position == 'tail' else
        fact + background + caveat + '特此公告。' if position == 'middle' else caveat + background + fact)
    doc = document(body)
    locator = material.read_locator(doc, 'find:目标订单')['locators'][0]['locator']
    result = source_read(doc, locator, request_fits=lambda _: True)
    assert caveat in evidence(result['value'])
    assert_exact_ranges(result['value'], body)
    packet = project_packet('assess_evidence', {'contextResults': [result], 'questions': [], 'claims': [],
        'allowedEvidenceRefs': [{'documentId': 'd', 'revision': 1}]})
    assert packet['allowedEvidenceRefs'] == [{'documentId': 'd', 'revision': 1}]
    assert caveat in evidence(packet['contextResults'][0]['value'])


def legacy_catalogue(location='find:订单'):
    # B69's real catalogue shape: one long matching parent plus 130 paragraphs.
    body = '普通背景说明。'*1800 + '订单进展仍待确认。'*200 + '\n\n' + '\n\n'.join(
        f'订单第{i}段有待核实。' for i in range(130))
    doc = document(body)
    is_outline = location.startswith('outline')
    offset = 128 if ':128' in location else 0
    locators = [{'locator': 'paragraph:1', 'readable': True}] + [
        {'locator': f'paragraph:{3+2*i}', 'readable': True} for i in range(130)]
    value = {'sourceRef': {'documentId': 'd', 'revision': 1}, 'indexVersion': 'k10-source-index-3.3.0',
        'locators': locators[offset:offset+128], 'matchingLocatorCount': 131, 'locatorCount': 131,
        'offset': offset, 'nextLocation': ('outline:128' if is_outline else 'find:128:订单') if offset == 0 else None,
        'eligibleAtNewsCutoff': True}
    request = {'kind': 'source', 'sourceRef': value['sourceRef'], 'location': location}
    return doc, {'request': request, 'status': 'found', 'value': value, 'contentSha256': 'legacy-catalogue-fixture'}


@pytest.mark.parametrize('location', ['outline', 'find:订单', 'find:128:订单', 'outline:128',
    'within:paragraph:1:128'])
def test_stale_catalogue_is_replaced_before_it_can_be_counted_as_already_read(location):
    doc, old = legacy_catalogue(location)
    packet = {'questions': [], 'claims': [], 'contextResults': [old], 'allowedEvidenceRefs': []}
    safe = project_packet('assess_evidence', packet)['contextResults'][0]['value']
    assert safe['status'] == 'requires_current_read_protocol'
    assert 'locators' not in safe and 'nextLocation' not in safe
    runtime = object.__new__(runtime_module._Investigation)
    runtime.model = SimpleNamespace()
    runtime.documents = {doc.evidence_ref: doc}
    runtime.allowed = {doc.evidence_ref}
    runtime.state = {'snapshot': SimpleNamespace(research_status='continue_research'), 'questions': [], 'claims': [],
        'stageResults': [{'result': {'conclusion': {'runtimeContextReadInputSha256': 'original-paid-request',
            'runtimeContextReadProtocol': PROTOCOL, 'runtimeContextObjectSha256': None, 'runtimeContextRead': old}}}]}
    runtime._packet = lambda: {key: value for key, value in packet.items() if key != 'contextResults'}
    runtime._background_read_plan = lambda _: (None, None)
    records = []
    runtime._record = lambda result, **_: records.append(result)
    runtime._call = lambda action, extra: extra
    # Replayed paid reply may still request a cursor from the obsolete index.
    requested = {**old['request'], 'purpose': '查找订单'}
    result = runtime._continue_context('assess_evidence', ResearchStageResult('assess_evidence', context_requests=(requested,)),
        {'contextResults': [deepcopy(old)]}, request_input='original-paid-request')
    assert 'contextFeedback' not in result
    refreshed = result['contextResults'][0]
    value = refreshed['value']
    assert value['indexVersion'] == material.INDEX_VERSION
    assert value['offset'] == 0
    expected = material.read_locator(doc, material.catalogue_restart_location(location))
    assert value['locators'] == expected['locators']
    assert records[0].conclusion['runtimeContextReadInputSha256'] == 'original-paid-request'
    # The new cursor is not blocked by the old same-spelling request identity.
    following = runtime._continue_context('assess_evidence', ResearchStageResult('assess_evidence', context_requests=(
        {**requested, 'location': value['nextLocation']},)), result, request_input='next-paid-request')
    assert 'contextFeedback' not in following
    assert following['contextResults'][-1]['value']['offset'] == len(value['locators'])


def test_unversioned_local_denials_do_not_become_endless_stale_reads():
    denial = {'request': {'kind': 'source', 'sourceRef': {'documentId': 'd', 'revision': 1}, 'location': 'outline'},
        'status': 'found', 'value': {'status': 'requires_current_event_question', 'needsLocator': True}}
    packet = project_packet('assess_evidence', {'questions': [], 'claims': [], 'contextResults': [denial]})
    assert packet['contextResults'][0]['value'] == denial['value']


def test_sparse_conditions_share_their_union_instead_of_copying_it_per_sentence(monkeypatch):
    body = '目标订单。' + ('普通背景说明。该金额不构成收入。' * 1400)
    visited = []
    original = material._merge_support_ranges
    def merge(ranges):
        ranges = list(ranges)
        visited.append(len(ranges))
        return original(ranges)
    monkeypatch.setattr(material, '_merge_support_ranges', merge)
    page = material.read_locator(document(body), 'outline')
    assert page['locatorCount'] == 2801
    assert page['unreadableLocatorCount'] == page['locatorCount']
    assert sum(visited) < 12 * page['locatorCount']


def test_conditions_and_footnotes_remain_whole_when_capacity_is_insufficient():
    caveat = '本次交易尚需获得监管批准，具体条件详见[1]。'
    body = ('目标订单金额十亿元。' + '普通背景。'*3000 + caveat + '特此公告。'
        + '\n\n# 附录\n\n[1] 未获批准不构成合同。' + '补充条件。'*3000)
    doc = document(body)
    locator = material.read_locator(doc, 'find:目标订单')['locators'][0]['locator']
    result = source_read(doc, locator, request_fits=lambda _: True)
    assert 'text' not in result['value']
    assert result['value']['requiredCharacters'] > material.MAX_FRAGMENT_CHARACTERS


def test_sparse_conditions_in_cross_sentence_excerpt_are_shared_and_complete(monkeypatch):
    caveat = '该金额不构成收入。'
    body = '目标订单金额十亿元。' + '普通背景说明。'*2200 + ('普通背景说明。'+caveat)*60
    doc = document(body, body[:8000])
    blocks = material._blocks(body)
    original = material._continuous_ranges
    visited = []
    def merge(text, ranges):
        ranges = list(ranges)
        visited.append(len(ranges))
        return original(text, ranges)
    monkeypatch.setattr(material, '_continuous_ranges', merge)
    value = material.bounded_excerpt(doc)
    assert not value['needsLocator']
    assert evidence(value).count(caveat) == 60
    assert_exact_ranges(value, body)
    assert sum(visited) < 3 * len(blocks)


@pytest.mark.parametrize('background', [
    '市场需求持续增长。', '系统响应速度提升。', '生产能力不断提高。',
    '不同地区销售均增长。', '公司应用软件持续迭代。', '公司供应链覆盖全国。',
    '无线产品销量增长。', '相关技术适应市场发展。',
    '公司生产所需原材料主要来自国内供应商。', '公司现有经营条件保持稳定。',
    '公司生产计划管理系统运行稳定。',
    '公司虚拟现实设备销量增长。', '公司模拟芯片销量增长。',
    '公司未央区门店销售正常。', '未名医药公司业务稳定。',
    '公司受益于内需增长。', '公司出口业务受外需回暖带动。',
    '公司主营生活必需品供应。', '公司采用按需生产模式。',
])
def test_ordinary_word_parts_do_not_turn_whole_parent_into_qualifications(background):
    caveat = '该合同将在主管部门批准后生效。'
    body = '目标订单金额十亿元。' + background*1600 + caveat
    doc = document(body)
    locator = material.read_locator(doc, 'find:目标订单')['locators'][0]['locator']
    result = source_read(doc, locator, request_fits=lambda _: True)
    assert not result['value']['needsLocator']
    assert caveat in evidence(result['value'])
    assert_exact_ranges(result['value'], body)
    packet = project_packet('assess_evidence', {'questions': [], 'claims': [], 'contextResults': [result]})
    assert caveat in evidence(packet['contextResults'][0]['value'])


def test_real_cli_worker_recovery_refreshes_durable_catalogue_without_replaying_paid_prefix(tmp_path, monkeypatch):
    import json
    import sqlite3
    from datetime import timedelta
    from hashlib import sha256
    from neckline.k10 import store, pipeline
    from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
    from neckline.k10.investigation import InvestigationError
    from neckline.k10.verification import VerificationEvidenceBundle
    from neckline.k10.worker import run_once
    from tests import test_v310_pipeline_e2e as fixture
    from tests.test_b70_context_repair import install_context_request
    doc, old = legacy_catalogue()
    original_fetch = fixture._Gateway.fetch
    def fetch(self, **kwargs):
        original_fetch(self, **kwargs)
        # A real provider persists raw documents before returning evidence.
        store.append_document_version(document_id=doc.document_id, source_key='review-fixture', external_id='d',
            canonical_url=None, content_sha256=sha256(doc.original_text.encode()).hexdigest(),
            published_at=fixture.NOW.isoformat(), published_precision='exact', fetched_at=fixture.RUN_AT.isoformat(),
            original_text=doc.original_text, excerpt=None, fetch_version='fixture', metadata=doc.metadata,
            created_at=fixture.RUN_AT.isoformat(), db_path=tmp_path/'b39-e2e.sqlite')
        return VerificationEvidenceBundle('available', (doc,), (doc,), {'state': 'available', 'requestState': 'completed'})
    monkeypatch.setattr(fixture._Gateway, 'fetch', fetch)
    request = {**old['request'], 'questionId': 'q-1', 'purpose': '核对订单'}
    seen = install_context_request(monkeypatch, lambda _: request, action='assess_evidence', repeat=True)
    current_protocol = runtime_module._READ_PROTOCOL
    monkeypatch.setattr(runtime_module, '_READ_PROTOCOL', PROTOCOL)
    read = runtime_module.read_context
    local_reads = []
    def old_reader_once(request, **kwargs):
        local_reads.append(request)
        if len(local_reads) == 1:
            return {**deepcopy(old), 'request': {k:v for k,v in request.items() if k != 'purpose'}}
        return read(request, **kwargs)
    monkeypatch.setattr(runtime_module, 'read_context', old_reader_once)
    advance = runtime_module.advance_research
    interrupted = []
    def interrupt_after_local_write(**kwargs):
        if kwargs['action'] == 'assess_evidence' and kwargs['evidence_packet'].get('contextResults') and not interrupted:
            interrupted.append(True)
            monkeypatch.setattr(runtime_module, '_READ_PROTOCOL', current_protocol)
            raise InvestigationError('fixture interruption after durable local read', code='fixture_local_read_interrupted')
        return advance(**kwargs)
    monkeypatch.setattr(runtime_module, 'advance_research', interrupt_after_local_write)
    db, task_id, task, calls, gateway = fixture._run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task.status == 'failed' and interrupted
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)['checkpoint']['scanId']
    frozen = frozen_scan_input_sha256(scan_id=scan_id, db_path=db)
    with sqlite3.connect(db) as conn:
        paid = conn.execute("SELECT * FROM k10_external_attempts WHERE state='succeeded'").fetchall()
        stages = [json.loads(row[0]) for row in conn.execute('SELECT result_json FROM k10_research_stage_results')]
    failures = [row['conclusion']['runtimeFailedAction'] for row in stages
                if (row.get('conclusion') or {}).get('runtimeFailedAction')]
    assert failures[-1]['extra']['contextResults'][0]['value']['indexVersion'] == old['value']['indexVersion']
    assert recover_scan(db_path=db, scan_id=scan_id, execution_config_id='b39-execution', execution_config_revision=1,
        confirmed_input_sha256=frozen, now=fixture.RUN_AT) == task_id
    task = run_once(db_path=db, task_id=task_id, worker_id='b70-catalogue-recovery', lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'),
        clock=lambda: fixture.RUN_AT)
    assert task.status == 'completed'
    refreshed = seen[2]['contextResults'][-1]['value']
    assert refreshed['indexVersion'] == material.INDEX_VERSION and refreshed['offset'] == 0
    assert refreshed['locators'] == material.read_locator(doc, 'find:订单')['locators']
    assert gateway.search_paths == ['path-1', 'path-2']
    assert calls.count('titleBatch') == 1 and calls.count('research:plan_gaps') == 1
    assert frozen_scan_input_sha256(scan_id=scan_id, db_path=db) == frozen
    with sqlite3.connect(db) as conn:
        current = conn.execute("SELECT * FROM k10_external_attempts WHERE state='succeeded'").fetchall()
        assert all(row in current for row in paid)
        assert conn.execute('SELECT COUNT(*) FROM k10_tasks').fetchone()[0] == 1
    with fixture._api(db) as api:
        response = api.get(f'/api/v1/k10/scans/{scan_id}/assessments')
    assert response.status_code == 200 and response.json()['items']
