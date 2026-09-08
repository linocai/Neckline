import pytest
from datetime import timedelta
from neckline.k10 import pipeline, store
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once
from neckline.k10.investigation import decode_stage_result, InvestigationError
from neckline.k10.research_contracts import Question, ResearchContractError
from tests.test_v310_pipeline_e2e import _run, _http_transport, RUN_AT


@pytest.mark.parametrize('shape,expected_calls', [('omitted', 1), ('wrapped', 1), ('wrong_repair', 2)])
def test_real_worker_normalizes_known_envelope_or_repairs_wrong_action(tmp_path, monkeypatch, shape, expected_calls):
    db, task_id, task, calls, gateway = _run(tmp_path, monkeypatch, action_shape=shape)
    assert task.status == 'completed'
    assert calls.count('research:plan_gaps') == expected_calls
    assert len(gateway.search_paths) == 2
    assert calls.count('understand') == 1


@pytest.mark.parametrize('payload', [{'unexpected': []}, {'claims': [{}]}, {'action': 'plan_queries', 'questions': []}])
def test_envelope_normalization_never_fills_facts_or_accepts_wrong_stage(payload):
    with pytest.raises(InvestigationError):
        decode_stage_result(payload, action='plan_gaps' if 'claims' not in payload else 'extract_claims')


@pytest.mark.parametrize('state', ['open', 'answered'])
def test_resolved_question_can_clear_gap_but_open_question_cannot(state):
    payload = {'questionId':'q','claimIds':['c'],'companyCodes':[], 'question':'original question',
               'knownEvidence':[], 'missingEvidence':[], 'supportCondition':'confirmation', 'refuteCondition':'denial',
               'decisionImpact':'changes assessment', 'state':state, 'resumeCondition':None}
    if state == 'open':
        with pytest.raises(ResearchContractError): Question.from_dict(payload)
    else:
        assert Question.from_dict(payload).missing_evidence == ()


def test_failed_research_recovery_group_needs_a_new_explicit_grant_to_retry(tmp_path, monkeypatch):
    db, task_id, first, _, _ = _run(tmp_path, monkeypatch, malformed_action='close_research')
    assert first.status == 'failed'
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)['checkpoint']['scanId']
    def recover():
        return recover_scan(db_path=db, scan_id=scan_id, execution_config_id='b39-execution', execution_config_revision=1,
            confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=db), now=RUN_AT)
    handlers = pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet')
    recover()
    bad_calls = _http_transport(monkeypatch, malformed_action='close_research', initial_query_round=1)
    second = run_once(db_path=db, worker_id='bad-recovery', lease_for=timedelta(minutes=5), handlers=handlers, clock=lambda: RUN_AT)
    assert second.status == 'failed'
    count = len(bad_calls)
    assert run_once(db_path=db, worker_id='no-grant', lease_for=timedelta(minutes=5), handlers=handlers, clock=lambda: RUN_AT) is None
    assert len(bad_calls) == count
    recover()
    calls = _http_transport(monkeypatch, initial_query_round=1)
    done = run_once(db_path=db, worker_id='fixed', lease_for=timedelta(minutes=5), handlers=handlers, clock=lambda: RUN_AT)
    assert done.status == 'completed'
    assert not {'titleBatch','titleGlobal','titleReview','understand','research:plan_gaps'} & set(calls)
    assert len(store.list_candidates(scan_id=scan_id, state='offered', db_path=db)) == 1
