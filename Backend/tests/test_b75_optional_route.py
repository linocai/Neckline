"""Do not reject an entire paid plan for a redundant malformed company route."""
import pytest
from neckline.k10.investigation import decode_stage_result
from tests.test_b72_query_path_resilience import fixture


@pytest.mark.parametrize('covered', [True,False])
def test_known_claim_only_company_route_requires_valid_alternative(covered):
    p,raw=fixture()
    p['_localState']['questions']=p['_localState']['questions'][:1]
    good=raw['queryPaths'][0]
    bad={**good,'pathId':'missing-company','query':good['query']+' incomplete','purposeKind':'company_event_link','targetRefs':[{'kind':'claim','claimId':'c1'}]}
    raw['queryPaths']=[good,bad] if covered else [bad]
    result=decode_stage_result(raw,action='plan_queries',evidence_packet=p)
    assert [x.path_id for x in result.query_paths]==(['valid'] if covered else ['missing-company'])
    if covered:assert result.conclusion['runtimeOutputSanitization']['discardedUnusableQueryPaths']==1


def test_real_cli_resumes_paid_plan_without_rebilling_redundant_company_route(tmp_path,monkeypatch):
    import sqlite3
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from neckline.k10 import pipeline
    from neckline.k10.discovery import DiscoveryDocument
    from neckline.k10.verification import VerificationEvidenceBundle
    from neckline.k10.worker import run_once
    from neckline.k10.v2_store import read_report
    from tests import test_v310_pipeline_e2e as e2e
    from tests import test_b72_query_path_resilience as old
    edited=[]
    request_packets=[]
    def edit(value):
        if value.get('action')!='research_round' or edited: return
        edited.append(True)
        claim_id=old.next_packet_claim_id(request_packets)
        old.edit_mixed_plan(value,claim_id=claim_id)
        if value.get('action')=='research_round':
            valid=next(path for path in value['queryPaths'] if path['questionId']=='q-2')
            value['queryPaths'].append({**valid,'pathId':'redundant-company','purposeKind':'company_event_link',
                'targetRefs':[{'kind':'claim','claimId':claim_id}]})
    from tests.test_b60_pool_filtering import edit_responses
    edit_responses(monkeypatch,edit)

    class VisibleGateway(e2e._Gateway):
        """A second direct round is justified only by real visible material."""
        def fetch(self, **kwargs):
            path = kwargs["query_path"]
            self.search_paths.append(path.path_id)
            self.search_routes.append(path.to_dict())
            document = DiscoveryDocument(
                "optional-route-confirmation", 1,
                "2026-09-08T12:30:00+00:00", "2026-09-08T12:35:00+00:00",
                "独立公告：订单状态仍待公司确认。", "独立公告：订单状态仍待公司确认。", {},
            )
            return VerificationEvidenceBundle("available", (document,), (document,), {
                "state": "available", "requestState": "completed", "reason": "fixture_visible_confirmation",
            })
    monkeypatch.setattr(e2e, "_Gateway", VisibleGateway)

    tick=[0.0]
    monkeypatch.setattr(pipeline,'time',SimpleNamespace(monotonic=lambda:tick[0]))
    advance=pipeline._CheckpointedDiscoveryModel.advance_research_round
    interrupted=[]
    packets=[]
    def pause_after_durable_plan(self,**kwargs):
        packets.append(kwargs['evidence_packet'])
        result=advance(self,**kwargs)
        if not interrupted:
            interrupted.append(True)
            tick[0]=10_000.0
        return result
    monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel,'advance_research_round',pause_after_durable_plan)

    db,task_id,first,calls,gateway=e2e._run(tmp_path,monkeypatch,v2=True,cli_entry=True,
        request_observer=old.capture_research_packets(request_packets))
    assert first.status=='queued' and interrupted
    assert calls.count('research:research_round')==1
    with sqlite3.connect(db) as conn:
        paid_before=conn.execute("SELECT input_sha256,result_json FROM k10_execution_item_checkpoints WHERE stage='model:investigation_research_round' AND status='completed'").fetchall()
        assert len(paid_before)==1
        retry_at=datetime.fromisoformat(conn.execute(
            'SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?',(task_id,)).fetchone()[0])
    tick[0]=0.0
    done=run_once(db_path=db,task_id=task_id,worker_id='b75-optional-route',lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),
        clock=lambda:retry_at+timedelta(seconds=1))
    assert done.status=='completed'
    assert len(packets) == 3 and packets[0] == packets[1]
    assert any(ref["documentId"] == "optional-route-confirmation" for ref in packets[2]["allowedEvidenceRefs"])
    # The paid request survives interruption; a second, distinct round compares the new search evidence.
    assert calls.count('research:research_round')==2
    with sqlite3.connect(db) as conn:
        paid_after=conn.execute("SELECT input_sha256,result_json FROM k10_execution_item_checkpoints WHERE stage='model:investigation_research_round' AND status='completed'").fetchall()
    assert len(paid_after)==2 and paid_before[0] in paid_after
    assert len({row[0] for row in paid_after})==2
    e2e.assert_search_routes(gateway, [('path-1','Company notice','q-1'), ('path-1-other','Company notice','q-2')])
    assert gateway.search_routes[1]['targetRefs'] == [{'kind':'company','companyCode':'300004.SZ'}]
    assert read_report(db_path=db)['status']=='completed'
