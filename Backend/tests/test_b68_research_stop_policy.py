import json

from neckline.k10.research_store import list_research_assessments
from neckline.k10.v2_store import read_report
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for


def test_real_worker_receives_current_stop_rule_before_comparison_and_publishes_unverified(tmp_path, monkeypatch):
    requests = {}
    def observe(request):
        wire = json.loads(request.content)
        message = wire['messages'][-1]['content']
        payload = json.loads(message.split('<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])
        if payload.get('action'):
            requests[payload['action']] = (message.split('<untrusted-k10-evidence>', 1)[0], payload)
    db, task_id, task, calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True,
        pending_ranking='legacy_wrong', request_observer=observe)
    assert task.status == 'completed'
    assert {'plan_gaps','plan_queries','assess_evidence','close_research','compare_companies'} <= requests.keys()
    for action, (instruction, payload) in requests.items():
        assert '缺少官方确认不构成待核关卡' in instruction, action
        assert '足以支持当前比较、并能说明剩余不确定性时结束调查' in instruction, action
        assert '未知信息保留为未知' in instruction, action
        assert payload['evidencePacket']['availableArticlePolicy']['fullTextQuota'] is None
    report = read_report(db_path=db)
    assert report['eveningCards'] and not report['incompleteReviews']
    card = report['eveningCards'][0]
    assert card['catalysts'][0]['verificationStatus'] == 'unverified' and card['uncertainty']
    assessments = list_research_assessments(db_path=db, task_id=task_id)
    assert any(row['role'] == 'primary' and row['evidenceDisclosure']['isRumor']
        and row['evidenceDisclosure']['conditionalAnalysis'] for row in assessments)
    assert calls.count('research:compare_companies') == 1
    assert len(gateway.search_paths) == 1
    response = client_for(db).get('/api/v1/k10/v2/reports/latest?window=evening')
    assert response.status_code == 200 and response.json()['report']['eveningCards'][0]['catalysts'] == card['catalysts']
