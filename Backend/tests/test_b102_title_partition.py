"""New wire may be trimmed; durable identities must never be repaired."""
import copy
import json

import pytest

from neckline.k10 import store
from neckline.k10.title_triage import (
    TitleDTO, TitleTriageProtocolError, isolate_batch_response,
    validate_canonical_batch_result,
)


def batch():
    items = tuple(TitleDTO(f'doc-{i}', 1, 'fixture', None, f'标题{i}') for i in range(4))
    raw = {'items': [dict(i=i, status='candidate', matterKey=f'matter-{i}',
                         stageKey='new', reason='可用事实') for i in range(4)]}
    return items, raw


@pytest.mark.parametrize('fault', ['reason', 'status', 'unicode', 'nan', 'foreign', 'missing', 'duplicate', 'extra', 'all_bad'])
def test_wire_partition_preserves_siblings(fault):
    items, raw = batch()
    if fault == 'reason':
        raw['items'][0]['reason'] = 42
    elif fault == 'status':
        raw['items'][0]['status'] = []
    elif fault == 'unicode':
        raw['items'][0]['matterKey'] = '\ud800'
    elif fault == 'nan':
        raw['items'][0]['stageKey'] = float('nan')
    elif fault == 'foreign':
        raw['items'][0]['i'] = 999999
    elif fault == 'missing':
        raw['items'].pop(0)
    elif fault == 'duplicate':
        raw['items'].append({**raw['items'][0], 'reason': '另一条模型判断'})
    elif fault == 'extra':
        raw['unused\udfff'] = float('inf')
        raw['items'][0]['unused'] = '\ud800'
    else:
        raw['items'] = [None, {'i': True}, {'i': -1}]
    before = json.dumps(raw, ensure_ascii=True)
    canonical = isolate_batch_response(raw, items)
    outcome = validate_canonical_batch_result(canonical, items)
    assert json.dumps(raw, ensure_ascii=True) == before
    expected = {t.ref for t in items} if fault in {'extra', 'duplicate'} else set() if fault == 'all_bad' else {t.ref for t in items[1:]}
    assert {r.ref for r in outcome.results} == expected
    assert set(outcome.failed_refs) == {t.ref for t in items} - expected
    json.dumps(canonical, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if fault != 'extra':
        assert canonical['rejectedCount'] > 0


@pytest.mark.parametrize('fault', ['foreign', 'missing', 'overlap', 'duplicate', 'wire_index', 'unicode', 'extra_key', 'bool_revision'])
def test_stored_partition_corruption_is_rejected(fault):
    items, raw = batch()
    raw['items'][0]['reason'] = 42
    canonical = isolate_batch_response(raw, items)
    corrupt = copy.deepcopy(canonical)
    if fault == 'foreign':
        corrupt['failedRefs'][0]['documentId'] = 'unknown'
    elif fault == 'missing':
        corrupt['failedRefs'] = []
    elif fault == 'overlap':
        corrupt['failedRefs'][0]['documentId'] = corrupt['items'][0]['documentId']
    elif fault == 'duplicate':
        corrupt['items'].append(corrupt['items'][0])
    elif fault == 'wire_index':
        corrupt['items'][0]['i'] = 0
        del corrupt['items'][0]['documentId']
    elif fault == 'unicode':
        corrupt['items'][0]['reason'] = '\ud800'
    elif fault == 'extra_key':
        corrupt['items'][0]['unexpected'] = 'bad canonical field'
    else:
        corrupt['failedRefs'][0]['revision'] = True
    with pytest.raises(TitleTriageProtocolError):
        validate_canonical_batch_result(corrupt, items)


def test_private_receipt_encoding_preserves_hashes_and_strict_business_fields():
    good = {'text': '中文 😀 𠀀', 'quote': '\\ud800'}
    assert store.private_response_json(good) == store._json(good)
    raw = {'text': '中文\ud800', 'key\udfff': 'exact'}
    assert json.loads(store.private_response_json(raw)) == raw
    checkpoint = {'scanId': 'valid', 'toolReceipts': {'paid': raw}}
    assert json.loads(store.task_checkpoint_json(checkpoint)) == checkpoint
    with pytest.raises(UnicodeEncodeError):
        store.task_checkpoint_json({**checkpoint, 'scanId': '\ud800'})


def test_old_full_checkpoint_stays_strict_and_no_value_remains_processed():
    items, raw = batch()
    raw['items'][0]['status'] = 'no_value'
    canonical = isolate_batch_response(raw, items)
    outcome = validate_canonical_batch_result(canonical, items)
    assert len(outcome.results) == 4 and not outcome.failed_refs
    assert outcome.results[0].status == 'no_value'
    canonical['items'].pop()
    with pytest.raises(TitleTriageProtocolError):
        validate_canonical_batch_result(canonical, items)
