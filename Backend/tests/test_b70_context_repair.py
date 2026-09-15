"""B69 review reproductions: real capacity checks and CLI/worker boundaries, offline."""
from copy import deepcopy
import json

import httpx
import pytest

from neckline.k10.discovery import DiscoveryDocument
from neckline.k10.metering import MeteredProvider
from neckline.k10.research_context import read_context
from neckline.k10.research_material import read_locator
from neckline.llm.base import ChatMessage


def document(text, title="甲公司2025年年度报告"):
    return DiscoveryDocument("annual", 1, "2026-09-11T00:00:00+00:00",
        "2026-09-13T13:00:00+00:00", text, None, {"title": title})


def source_read(doc, location, **extra):
    question = {"questionId": "q", "state": "open", "claimIds": ["c"],
        "question": "当前订单是否确认？", "supportCondition": "订单已确认", "refuteCondition": "订单未确认"}
    return read_context({"kind": "source", "purpose": "核对订单", "questionId": "q",
        "sourceRef": {"documentId": doc.document_id, "revision": doc.revision}, "location": location},
        state={"questions": [question]}, documents={doc.evidence_ref: doc}, binding=None,
        eligible_refs={(doc.document_id, doc.revision)}, **extra)


def test_long_flattened_annual_report_requires_refinement_with_real_capacity_guard(tmp_path):
    body = "当前订单仍待确认。" + "历史沿革和一般业务背景。" * 10_000
    doc = document(body)
    provider = MeteredProvider(ledger_db=tmp_path / "never-opened.sqlite", ledger_task="review",
        api_key="unused-fixture", model="deepseek-flash", name="fixture",
        api_url="https://api.deepseek.com/chat/completions")
    checked = []
    def fits(candidate):
        checked.append(candidate)
        return provider.request_context_error([ChatMessage(role="user", content=json.dumps(candidate, ensure_ascii=False))],
            enable_search=False, model_options={"maxTokens": 32000, "thinking": {"type": "disabled"}}) is None
    result = source_read(doc, "paragraph:1", request_fits=fits)["value"]
    assert "text" not in result
    assert result["needsLocator"] is True
    assert body not in json.dumps(checked, ensure_ascii=False)
    assert not (tmp_path / "never-opened.sqlite").exists()


def test_background_find_follows_the_exact_pagination_cursor():
    doc = document("\n\n".join(f"第 {i} 段订单情况。" for i in range(130)))
    first = source_read(doc, "find:订单")["value"]
    assert len(first["locators"]) == 128
    second = source_read(doc, first["nextLocation"])["value"]
    assert len(second.get("locators", [])) == 2
    assert second["nextLocation"] is None
    assert second["locators"] == read_locator(doc, first["nextLocation"])["locators"]


def install_context_request(monkeypatch, request_for_packet, *, action="plan_queries", repeat=False):
    original = httpx.MockTransport
    seen = []
    def transport(handler):
        def respond(request):
            wire = json.loads(request.content)
            message = wire["messages"][-1]["content"]
            payload = json.loads(message.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])
            if payload.get("action") == action:
                seen.append(payload["evidencePacket"])
                if len(seen) == 1 or (repeat and len(seen) == 2):
                    reply = {"action": action, "contextRequests": [request_for_packet(seen[0])]}
                    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)},
                        "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6}})
            return handler(request)
        return original(respond)
    monkeypatch.setattr(httpx, "MockTransport", transport)
    return seen


@pytest.mark.parametrize("source_ref", [None, [None, "s.detail", {"invalid": True}], {"invalid": True}, 42])
def test_real_cli_worker_nullable_profile_field_keeps_report_readable(tmp_path, monkeypatch, source_ref):
    from neckline.k10 import v2_profiles
    from tests.test_v310_pipeline_e2e import _run, _api
    original = v2_profiles.read_profiles
    def read(**kwargs):
        rows = deepcopy(original(**kwargs))
        if not kwargs.get("index_only"):
            for row in rows:
                if row["identity"]["ts_code"] == "300002.SZ":
                    row["market_distribution"] = {"denominator": {"source_ref": source_ref},
                        "caveat": "分母来源缺失，不能确认为已核事实"}
                    row["sources"].append({"source_id": "s"})
        return rows
    monkeypatch.setattr(v2_profiles, "read_profiles", read)
    seen = install_context_request(monkeypatch, lambda packet: {
        "kind": "company_fields", "questionId": packet["questions"][0]["questionId"],
        "companyCode": "300002.SZ", "fields": ["market_distribution"], "purpose": "核对当前公司的市场口径"})
    db, task_id, task, _, gateway = _run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task.status == "completed"
    result = seen[1]["contextResults"][0]["value"]
    assert result["fields"]["market_distribution"]["denominator"]["source_ref"] == source_ref
    assert "不能确认为已核事实" in result["fields"]["market_distribution"]["caveat"]
    assert gateway.search_paths == ["path-1", "path-2"]
    from neckline.k10 import store
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    with _api(db) as api:
        response = api.get(f"/api/v1/k10/scans/{scan_id}/assessments")
    assert response.status_code == 200 and response.json()["items"]


def test_real_cli_worker_does_not_send_hidden_company_fields(tmp_path, monkeypatch):
    from tests.test_v310_pipeline_e2e import _run
    def request(packet):
        assert packet["questions"][0]["companyCodes"] == ["300002.SZ"]
        assert "301717.SZ" not in [row["identity"]["ts_code"] for row in packet["companyScope"]["companyProfiles"]]
        return {"kind": "company_fields", "questionId": packet["questions"][0]["questionId"],
            "companyCode": "301717.SZ", "fields": ["summary"], "purpose": "读取另一家公司"}
    seen = install_context_request(monkeypatch, request)
    _, _, task, _, gateway = _run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task.status == "completed"  # A rejected local hint must not destroy usable work.
    result = seen[1]["contextResults"][0]["value"]
    assert result.get("status") == "outside_company_scope"
    assert "fields" not in result and "identity" not in result
    assert gateway.search_paths == ["path-1", "path-2"]


def test_sentence_read_keeps_parent_caveat_footnote_and_exact_offsets():
    from neckline.k10.research_material import INDEX_VERSION
    body = '# 订单\r\n\r\n' + '历史业务说明。' * 2500 + '本次客户有采购意向[1]。但尚未构成订单。\r\n\r\n上述意向不构成收入。\r\n\r\n[1] 客户可以取消采购。'
    doc = document(body)
    page = read_locator(doc, 'find:采购意向')
    selected = source_read(doc, page['locators'][0]['locator'], request_fits=lambda _: True)['value']
    assert selected['indexVersion'] == INDEX_VERSION
    assert selected['text'] == '本次客户有采购意向[1]。'
    context = selected['supportingContext']
    for needed in ('但尚未构成订单。', '上述意向不构成收入。', '[1] 客户可以取消采购。'):
        assert any(needed in part['text'] for part in context)
    for part in [selected, *context]:
        assert body[part['startOffset']:part['endOffset']] == part['text']
    assert len(json.dumps(selected, ensure_ascii=False)) < len(body)


@pytest.mark.parametrize('kind', ['company_search', 'company_fields'])
@pytest.mark.parametrize('repeat', [False, True])
def test_real_cli_worker_unknown_company_question_is_a_local_rejection(tmp_path, monkeypatch, kind, repeat):
    from tests.test_v310_pipeline_e2e import _run
    seen = install_context_request(monkeypatch, lambda _: {'kind': kind, 'questionId': 'q-draft',
        'companyCode': '300002.SZ', 'fields': ['summary'], 'query': '订单公司', 'purpose': '核对公司'}, repeat=repeat)
    _, _, task, _, gateway = _run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task.status == 'completed'
    assert seen[1]['contextResults'][0]['value'] == {'status': 'outside_company_scope'}
    assert gateway.search_paths == ['path-1', 'path-2']
    if repeat:
        assert len(seen) == 4  # Two local replies plus the fixture's two legitimate query rounds.
        assert seen[2]['contextFeedback']['code'] == 'already_read'
        assert '完整提供' not in seen[2]['contextFeedback']['instruction']
        assert '不表示已读证据' in seen[2]['contextFeedback']['instruction']


def test_long_qualification_run_uses_shared_original_ranges_without_losing_caveats():
    from neckline.k10.research_material import _blocks
    body = '订单预期。' + '尚未签署合同。' * 4000
    blocks = _blocks(body)
    assert len(blocks) == 4001
    assert all(len(block.parent_qualifiers) <= 1 for block in blocks)
    start, end = blocks[0].parent_qualifiers[0]
    # The initial expectation is also qualified, in the same shared union.
    assert body[start:end] == body
    result = read_locator(document(body), blocks[0].locator)
    assert result['status'] == 'not_safely_readable' and 'text' not in result


def test_qualifier_and_adjacent_sentence_are_counted_and_sent_exactly_once():
    first = '尚未' + '甲' * 4400 + '。'
    second = '尚未' + '乙' * 4400 + '。'
    body = '背景说明。' * 2500 + '目标订单已确认。' + first + second
    doc = document(body)
    locator = read_locator(doc, 'find:目标订单已确认')['locators'][0]['locator']
    result = read_locator(doc, locator)
    assert result['text'] == '目标订单已确认。'
    parts = [result, *result['supportingContext']]
    ranges = sorted((part['startOffset'], part['endOffset']) for part in parts)
    assert all(end <= following for (_, end), (following, _) in zip(ranges, ranges[1:]))
    assert all(body[part['startOffset']:part['endOffset']] == part['text'] for part in parts)
    assert result['evidenceUnitCharacters'] == sum(end-start for start, end in ranges) == 8819
    assert any(first + second in part['text'] for part in result['supportingContext'])


def test_restored_legacy_full_body_and_company_read_never_reenter_wire_packet():
    from neckline.k10.research_context import project_packet, public_packet
    body = '旧版整文' * 30000
    packet = {'companyScope': {'profileSnapshotId': 'profiles', 'companyProfiles': [
        {'identity': {'ts_code': '300002.SZ'}, 'summary': '当前公司', 'relationships': []}]},
        'claims': [], 'questions': [], 'queryPaths': [], 'fulltextRequests': [], 'evidenceCards': [],
        'evidenceUpdates': [], 'allowedEvidenceRefs': [{'documentId': 'annual', 'revision': 1}],
        'contextResults': [
            {'request': {'kind': 'source'}, 'status': 'found', 'value': {'sourceRef': {'documentId': 'annual', 'revision': 1},
                'text': body, 'eligibleAtNewsCutoff': True, 'indexVersion': 'k10-source-index-3.3.0'}},
            {'request': {'kind': 'company_fields', 'companyCode': '301717.SZ', 'fields': ['summary']},
                'status': 'found', 'value': {'identity': {'ts_code': '301717.SZ'}, 'fields': {'summary': '不应重新外送的资料'}}}]}
    projected = public_packet(project_packet('plan_gaps', packet))
    encoded = json.dumps(projected, ensure_ascii=False)
    assert body not in encoded and '不应重新外送的资料' not in encoded
    assert projected['allowedEvidenceRefs'] == []
    assert projected['contextResults'][0]['value']['needsLocator'] is True


def test_same_question_search_manifest_authorizes_only_its_returned_fields():
    from neckline.k10.research_context import company_field_scope_error, project_packet
    question = {'questionId': 'q', 'companyCodes': ['300002.SZ'], 'claimIds': [], 'state': 'open'}
    packet = {'companyScope': {'profileSnapshotId': 'p', 'companyProfiles': [
        {'identity': {'ts_code': '300002.SZ'}, 'relationships': []}]},
        'claims': [], 'questions': [question], 'evidenceUpdates': [], 'evidenceCards': [], 'allowedEvidenceRefs': [],
        'contextResults': [{'request': {'kind': 'company_search', 'questionId': 'q'}, 'status': 'found',
            'value': {'profileSnapshotId': 'p', 'companyProfiles': [{'identity': {'ts_code': '301717.SZ'},
                'fieldManifest': [{'field': 'summary', 'contentSha256': 'hash'}]}]}}]}
    packet = project_packet('plan_queries', packet)
    request = {'kind': 'company_fields', 'questionId': 'q', 'companyCode': '301717.SZ', 'fields': ['summary']}
    assert company_field_scope_error(request, packet) is None
    assert company_field_scope_error({**request, 'questionId': None}, packet) == 'outside_company_scope'
    assert company_field_scope_error({**request, 'fields': ['relationships']}, packet) == 'outside_company_field_manifest'
    assert company_field_scope_error({**request, 'fields': ['sources']}, packet) == 'outside_company_field_manifest'
    assert project_packet('plan_queries', packet)['companyScope'] == packet['companyScope']


def test_oversized_authorized_company_field_is_withheld_with_real_capacity_and_after_restore(tmp_path, monkeypatch):
    from neckline.k10 import v2_profiles
    from neckline.k10.research_context import digest, project_packet, public_packet
    body = '公司业务背景。' * 3200 + '但该意向不构成收入。'
    profile = {'identity': {'ts_code': '300002.SZ'}, 'review_status': 'draft',
        'summary': body, 'sources': []}
    monkeypatch.setattr(v2_profiles, 'read_profiles', lambda **_: [profile])
    question = {'questionId': 'q', 'companyCodes': ['300002.SZ'], 'claimIds': [], 'state': 'open'}
    packet = project_packet('plan_queries', {'companyScope': {'profileSnapshotId': 'p', 'companyProfiles': [profile]},
        'questions': [question], 'claims': []})
    request = {'kind': 'company_fields', 'companyCode': '300002.SZ', 'fields': ['summary'],
        'questionId': 'q', 'purpose': '核对当前公司背景'}
    provider = MeteredProvider(ledger_db=tmp_path/'never-opened.sqlite', ledger_task='review',
        api_key='unused-fixture', model='deepseek-flash', name='fixture', api_url='https://api.deepseek.com/chat/completions')
    checked = []
    def fits(candidate):
        checked.append(candidate)
        return provider.request_context_error([ChatMessage(role='user', content=json.dumps(candidate, ensure_ascii=False))],
            enable_search=False, model_options={'maxTokens': 32000, 'thinking': {'type': 'disabled'}}) is None
    result = read_context(request, state={'questions': [question]}, documents={}, binding=('unused', 'p'),
        eligible_refs=set(), visible_packet=packet, request_fits=fits)['value']
    assert result['needsFieldRefinement'] and 'fields' not in result
    assert result['contentSha256'] == digest(profile)
    assert result['fieldManifest'][0]['contentSha256'] == digest(body)
    assert body not in json.dumps(checked, ensure_ascii=False)
    old = {'request': {k: v for k, v in request.items() if k != 'purpose'}, 'status': 'found',
        'value': {'profileSnapshotId': 'p', 'identity': profile['identity'], 'contentSha256': digest(profile),
            'fields': {'summary': body}, 'sources': []}}
    restored = public_packet(project_packet('plan_queries', {**packet, 'contextResults': [old]}))
    value = restored['contextResults'][0]['value']
    assert value['needsFieldRefinement'] and value['fieldManifest'][0]['contentSha256'] == digest(body)
    assert body not in json.dumps(restored, ensure_ascii=False)
    assert not (tmp_path/'never-opened.sqlite').exists()


def test_questionless_search_cannot_authorize_hidden_company_after_questions_exist(monkeypatch):
    from neckline.k10.research_context import company_field_scope_error, project_packet
    from neckline.k10 import v2_profiles
    question = {'questionId': 'q', 'companyCodes': ['300002.SZ'], 'claimIds': [], 'state': 'open'}
    request = {'kind': 'company_fields', 'companyCode': '301717.SZ', 'fields': ['summary']}
    search = {'request': {'kind': 'company_search'}, 'status': 'found', 'value': {'profileSnapshotId': 'p',
        'companyProfiles': [{'identity': {'ts_code': '301717.SZ'}, 'fieldManifest': [{'field': 'summary'}]}]}}
    raw = {'companyScope': {'profileSnapshotId': 'p', 'companyProfiles': []}, 'claims': [],
        'questions': [question], 'contextResults': [search]}
    assert company_field_scope_error(request, raw) == 'outside_company_scope'
    projected = project_packet('plan_queries', raw)
    assert projected['contextResults'][0]['value'] == {'status': 'outside_company_scope'}
    assert company_field_scope_error(request, projected) == 'outside_company_scope'
    monkeypatch.setattr(v2_profiles, 'retrieve_company_context', lambda **_: pytest.fail('unbound search reached profile DB'))
    result = read_context({'kind': 'company_search', 'query': '另一家公司', 'purpose': '查公司'},
        state={'questions': [question]}, documents={}, binding=('unused', 'p'), eligible_refs=set(), visible_packet=projected)
    assert result['value'] == {'status': 'outside_company_scope'}
    # Before questions exist, local discovery still supplies a visible manifest.
    assert company_field_scope_error(request, {**raw, 'questions': []}) is None


def test_real_cli_worker_background_pagination_reaches_last_page(tmp_path, monkeypatch):
    from tests import test_v310_pipeline_e2e as fixture
    from neckline.k10.verification import VerificationEvidenceBundle
    original_fetch = fixture._Gateway.fetch
    body = '\n\n'.join(f'第 {i} 段公司确认情况。' for i in range(130))
    doc = DiscoveryDocument('annual-confirmation', 1, '2026-09-07T12:00:00+08:00',
        fixture.RUN_AT.isoformat(), body, None, {'title': '甲公司2025年年度报告'})
    def fetch(self, **kwargs):
        original_fetch(self, **kwargs)
        return VerificationEvidenceBundle('available', (doc,), (doc,), {'state': 'available', 'requestState': 'completed'})
    monkeypatch.setattr(fixture._Gateway, 'fetch', fetch)
    original_transport = httpx.MockTransport
    seen = []
    def transport(handler):
        def respond(request):
            message = json.loads(request.content)['messages'][-1]['content']
            payload = json.loads(message.split('<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])
            if payload.get('action') == 'assess_evidence' and len(seen) < 4:
                packet = payload['evidencePacket']; seen.append(packet)
                if len(seen) <= 3:
                    location = 'find:确认' if len(seen) == 1 else packet['contextResults'][-1]['value']['nextLocation'] if len(seen) == 2 else packet['contextResults'][-1]['value']['locators'][-1]['locator']
                    reply = {'action': 'assess_evidence', 'contextRequests': [{'kind': 'source', 'questionId': 'q-1',
                        'sourceRef': {'documentId': doc.document_id, 'revision': 1}, 'location': location, 'purpose': '核对公司的确认情况'}]}
                    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(reply, ensure_ascii=False)},
                        'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 3, 'total_tokens': 6}})
            return handler(request)
        return original_transport(respond)
    monkeypatch.setattr(httpx, 'MockTransport', transport)
    _, _, task, _, gateway = fixture._run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task.status == 'completed'
    assert len(seen) == 4
    assert len(seen[2]['contextResults'][-1]['value']['locators']) == 2
    assert seen[3]['contextResults'][-1]['value']['text'] == '第 129 段公司确认情况。'
    assert seen[3]['contextResults'][-1]['value']['backgroundReadPlan']['questionId'] == 'q-1'
    assert gateway.search_paths == ['path-1', 'path-2']
