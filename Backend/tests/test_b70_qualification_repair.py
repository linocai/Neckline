"""Second independent B70 review: complete, bounded source qualifications."""
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from neckline.k10.discovery import DiscoveryDocument
from neckline.k10.research_context import project_packet
from neckline.k10.research_material import read_locator
from tests.test_b70_context_repair import source_read


def document(body, excerpt=None):
    return DiscoveryDocument('d', 1, '2026-09-13T12:00:00Z',
        '2026-09-13T12:00:00Z', body, excerpt, {'title': '订单公告'})


def evidence(value):
    return value.get('text', '') + ''.join(x['text'] for x in value.get('supportingContext', []))


def assert_exact_ranges(value, body):
    ranges = [(value['startOffset'], value['endOffset'], value['text'], value['textSha256'])]
    ranges.extend((x['startOffset'], x['endOffset'], x['text'], x['sha256'])
        for x in value['supportingContext'])
    ranges.sort()
    for a, b, text, digest in ranges:
        assert body[a:b] == text
        assert sha256(text.encode()).hexdigest() == digest
    assert all(left[1] <= right[0] for left, right in zip(ranges, ranges[1:]))
    assert sum(b-a for a, b, _, _ in ranges) == value['evidenceUnitCharacters']


def test_support_only_footnote_retains_contract_and_revenue_caveat():
    body = ('背景说明。' * 2500 + '目标订单预计新增一亿元。' + '上述金额口径详见[1]。'
        + '公司其他业务暂无变化。' * 10 + '\n\n# 附录\n\n'
        + '[1] 上述金额仅为意向上限，尚未签订合同，也不构成收入。')
    doc = document(body)
    locator = read_locator(doc, 'find:目标订单预计')['locators'][0]['locator']
    value = read_locator(doc, locator)
    assert '尚未签订合同，也不构成收入' in evidence(value)
    assert_exact_ranges(value, body)


@pytest.mark.parametrize('support', ['# 订单[1]\n\n', '上句背景详见[1]。', '上述金额口径详见[1]。'])
def test_footnotes_close_over_heading_neighbor_and_qualifier_with_cycles(support):
    prefix = support if support.startswith('#') else ''
    neighbor = support if support.startswith('上句') else ''
    qualifier = support if support.startswith('上述') else ''
    body = (prefix + '背景说明。' * 2500 + neighbor + '目标订单预计新增一亿元。' + qualifier
        + '其他业务暂无变化。' * 10 + '\n\n# 附录\n\n[1] 仅为意向上限，具体见[2]。'
        + '\n\n[2] 尚未签订合同，也不构成收入，口径参见[1]。')
    doc = document(body)
    locator = read_locator(doc, 'find:目标订单预计')['locators'][0]['locator']
    value = read_locator(doc, locator)
    assert evidence(value).count('[2] 尚未签订合同') == 1
    assert_exact_ranges(value, body)


def test_split_footnote_definition_is_never_shortened_to_its_first_sentence():
    body = ('背景说明。' * 2500 + '目标订单预计新增一亿元。上述口径详见[1]。普通背景。'
        + '\n\n# 附录\n\n[1] 本条口径如下。' + '解释文字。' * 2500 + '尚未签订合同，也不构成收入。')
    doc = document(body)
    locator = read_locator(doc, 'find:目标订单预计')['locators'][0]['locator']
    value = read_locator(doc, locator)
    assert 'text' not in value
    assert value['status'] == 'not_safely_readable'
    assert value['requiredCharacters'] > 12000


@pytest.mark.parametrize('suffix', ['', '上述口径详见[1]。'])
def test_cross_sentence_excerpt_keeps_negation_and_replayable_offsets(suffix):
    snippet = '目标产能预计达到一百台。假设这批全部售出。'
    body = ('背景说明。' * 2500 + snippet + '但目前没有收到订单。' + suffix
        + '公司其他业务暂无变化。' * 10 + '\n\n# 附录\n\n[1] 产能不代表收入。')
    doc = document(body, snippet)
    result = source_read(doc, 'excerpt', request_fits=lambda _: True)
    value = result['value']
    assert '但目前没有收到订单。' in evidence(value)
    if suffix:
        assert '[1] 产能不代表收入。' in evidence(value)
    assert_exact_ranges(value, body)
    replay = read_locator(doc, value['locator'])
    assert evidence(replay) == evidence(value)
    packet = project_packet('plan_gaps', {'contextResults': [result], 'questions': [], 'claims': [],
        'allowedEvidenceRefs': [{'documentId': 'd', 'revision': 1}]})
    assert packet['allowedEvidenceRefs'] == [{'documentId': 'd', 'revision': 1}]
    assert '但目前没有收到订单' in json.dumps(packet, ensure_ascii=False)


def test_cross_sentence_excerpt_capacity_includes_all_qualifications():
    snippet = '目标产能预计达到一百台。假设这批全部售出。'
    doc = document('背景说明。' * 2500 + snippet + '但目前没有收到订单。' + '尚未' + '甲' * 12000 + '。', snippet)
    result = source_read(doc, 'excerpt', request_fits=lambda _: True)
    assert 'text' not in result['value']
    assert result['value']['needsLocator'] is True
    packet = project_packet('plan_gaps', {'contextResults': [result], 'questions': [], 'claims': [],
        'allowedEvidenceRefs': [{'documentId': 'd', 'revision': 1}]})
    assert packet['allowedEvidenceRefs'] == []


def test_cross_sentence_excerpt_physical_preflight_sees_negation_before_withholding_text():
    snippet = '目标产能预计达到一百台。假设这批全部售出。'
    doc = document('背景说明。' * 2500 + snippet + '但目前没有收到订单。普通背景。', snippet)
    checked = []
    result = source_read(doc, 'excerpt', request_fits=lambda value: checked.append(value) or False)
    assert any('但目前没有收到订单。' in json.dumps(value, ensure_ascii=False) for value in checked)
    assert 'text' not in result['value']


def test_numbered_following_caveat_is_inherited_even_without_an_inline_marker():
    body = ('背景说明。' * 2500 + '目标订单预计新增一亿元。普通背景。'
        + '\n\n[1] 上述全部金额尚未签约。')
    doc = document(body)
    locator = read_locator(doc, 'find:目标订单预计')['locators'][0]['locator']
    value = read_locator(doc, locator)
    assert '[1] 上述全部金额尚未签约。' in evidence(value)
    assert_exact_ranges(value, body)


@pytest.mark.parametrize('with_note', [False, True])
def test_shared_qualifier_ranges_do_not_rescan_quadratic_source_text(monkeypatch, with_note):
    from neckline.k10 import research_material as material
    body = '目标订单。' + '尚未确认订单。' * 2000
    if with_note:
        body += '上述口径详见[1]。\n\n# 附录\n\n[1] 不构成收入。'
    scanned = []
    pattern = r'\[(\d+)\]|([①②③④⑤⑥⑦⑧⑨⑩])'
    for name in ('findall', 'finditer'):
        original = getattr(material.re, name)
        def track(expression, text, *args, _original=original, **kwargs):
            if expression == pattern:
                scanned.append(len(text))
            return _original(expression, text, *args, **kwargs)
        monkeypatch.setattr(material.re, name, track)
    page = read_locator(document(body), 'outline')
    assert page['locatorCount'] >= 2001
    assert all(entry['readable'] is False for entry in page['locators'])
    # Count source characters visited by reference parsing, not wall-clock time.
    # The complete directory may index all sentences but must share its scan.
    assert sum(scanned) <= 4 * len(body)


def test_cross_paragraph_excerpt_returns_refinement_instead_of_bare_text():
    doc = document('目标产能预计达到一百台。\n\n假设这批全部售出。\n\n但目前没有收到订单。',
        '目标产能预计达到一百台。\n\n假设这批全部售出。')
    result = source_read(doc, 'excerpt')['value']
    assert 'text' not in result
    assert result['needsLocator'] and result['locators']
    assert '但目前没有收到订单。' in evidence(read_locator(doc, result['locators'][1]['locator']))


@pytest.mark.parametrize('legacy', [True, False])
def test_resume_reinterprets_legacy_local_receipts_without_reissuing_paid_request(monkeypatch, legacy):
    from neckline.k10 import research_runtime as runtime_module
    from neckline.k10.research_context import PROTOCOL
    from neckline.k10.research_contracts import ResearchStageResult
    snippet = '目标产能预计达到一百台。假设这批全部售出。'
    doc = document('背景说明。' * 2500 + snippet + '但目前没有收到订单。普通背景。', snippet)
    request = {'kind': 'source', 'purpose': '核对订单',
        'sourceRef': {'documentId': 'd', 'revision': 1}, 'location': 'excerpt'}
    identity = {k: v for k, v in request.items() if k != 'purpose'}
    value = source_read(doc, 'excerpt')
    value['request'] = identity
    if legacy:
        value['value'] = {**value['value'], 'indexVersion': 'k10-source-index-3.3.0-b70',
            'text': snippet, 'supportingContext': []}
    runtime = object.__new__(runtime_module._Investigation)
    runtime.model = SimpleNamespace()
    runtime.documents = {doc.evidence_ref: doc}
    runtime.allowed = {doc.evidence_ref}
    runtime.state = {'snapshot': SimpleNamespace(research_status='continue_research'), 'questions': [], 'claims': [],
        'stageResults': [{'result': {'conclusion': {'runtimeContextReadInputSha256': 'exact-paid-request',
            'runtimeContextReadProtocol': PROTOCOL if legacy else runtime_module._READ_PROTOCOL,
            'runtimeContextObjectSha256': None, 'runtimeContextRead': value}}}]}
    runtime._packet = lambda: {'questions': [], 'claims': [], 'allowedEvidenceRefs': [request['sourceRef']]}
    runtime._background_read_plan = lambda _: (None, None)
    recorded, reads, calls = [], [], []
    runtime._record = lambda result, **_: recorded.append(result)
    runtime._call = lambda action, extra: calls.append((action, extra)) or extra
    original = runtime_module.read_context
    def read(*args, **kwargs):
        reads.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime_module, 'read_context', read)
    # Interruption may also leave the same old read in pending action extras.
    extra = {'contextResults': [value]} if legacy else None
    result = runtime._continue_context('plan_gaps', ResearchStageResult('plan_gaps', context_requests=(request,)),
        extra, request_input='exact-paid-request')
    assert len(reads) == int(legacy)
    assert len(recorded) == int(legacy)
    assert len(calls) == 1
    assert '但目前没有收到订单。' in evidence(result['contextResults'][0]['value'])
    if legacy:
        assert recorded[0].conclusion['runtimeContextReadInputSha256'] == 'exact-paid-request'
        assert recorded[0].conclusion['runtimeContextReadProtocol'] == runtime_module._READ_PROTOCOL


@pytest.mark.parametrize('mode,caveat', [
    ('support_footnote', None), ('cross_sentence_excerpt', None),
    ('subject_condition', '本次交易尚需获得监管批准，能否完成存在不确定性。'),
    ('approval_prerequisite', '本次交易需提交股东大会审议通过后方可实施。'),
    ('signature_prerequisite', '本协议须双方签字盖章后生效。'),
    ('effective_after_approval', '该合同将在主管部门批准后生效。'),
    ('audit_basis', '相关收入应以最终审计结果为准。'),
])
def test_real_cli_worker_sends_complete_qualifications_and_persists_readable_report(tmp_path, monkeypatch, mode, caveat):
    from neckline.k10 import store
    from neckline.k10.research_runtime import _Investigation
    from neckline.k10.research_store import read_research_state
    from neckline.k10.verification import VerificationEvidenceBundle
    from tests import test_v310_pipeline_e2e as fixture
    from tests.test_b70_context_repair import install_context_request
    snippet = '目标产能预计达到一百台。假设这批全部售出。'
    if caveat is not None:
        doc = document('目标订单金额十亿元。' + '普通背景说明。' * 2200 + caveat)
        location = read_locator(doc, 'find:目标订单')['locators'][0]['locator']
    elif mode == 'support_footnote':
        doc = document('背景说明。' * 2500 + '目标订单预计新增一亿元。上述金额口径详见[1]。普通背景。'
            + '\n\n# 附录\n\n[1] 尚未签订合同，也不构成收入。')
        location = read_locator(doc, 'find:目标订单预计')['locators'][0]['locator']
        caveat = '尚未签订合同，也不构成收入。'
    else:
        doc = document('背景说明。' * 2500 + snippet + '但目前没有收到订单。普通背景。', snippet)
        location, caveat = 'excerpt', '但目前没有收到订单。'
    original_fetch = fixture._Gateway.fetch
    def fetch(self, **kwargs):
        original_fetch(self, **kwargs)
        return VerificationEvidenceBundle('available', (doc,), (doc,), {'state': 'available', 'requestState': 'completed'})
    monkeypatch.setattr(fixture._Gateway, 'fetch', fetch)
    seen = install_context_request(monkeypatch, lambda packet: {'kind': 'source', 'questionId': 'q-1',
        'sourceRef': {'documentId': 'd', 'revision': 1}, 'location': location, 'purpose': '核对订单'}, action='assess_evidence')
    original_record = _Investigation._record
    recorded = []
    def record(self, result, **kwargs):
        outcome = original_record(self, result, **kwargs)
        if (result.conclusion or {}).get('runtimeContextRead'):
            durable = read_research_state(snapshot_id=self.identity, db_path=self.db_path)
            recorded.append(durable['stageResults'][-1]['result']['conclusion']['runtimeContextRead'])
        return outcome
    monkeypatch.setattr(_Investigation, '_record', record)
    db, task_id, task, _, gateway = fixture._run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task.status == 'completed'
    assert caveat in evidence(seen[1]['contextResults'][-1]['value'])
    assert caveat in evidence(recorded[0]['value'])
    assert len(evidence(seen[1]['contextResults'][-1]['value'])) < 12000
    assert gateway.search_paths == ['path-1', 'path-2']
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)['checkpoint']['scanId']
    with fixture._api(db) as api:
        response = api.get(f'/api/v1/k10/scans/{scan_id}/assessments')
    assert response.status_code == 200 and response.json()['items']
