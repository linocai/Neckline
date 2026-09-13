from pathlib import Path
import socket
import sqlite3
from copy import deepcopy
import pytest

from neckline.k10.schema import initialize_schema
from neckline.k10.v2_profiles import import_profiles, UNIVERSE_ID, PROFILES_ID, read_profiles

INPUT = Path('/Users/linotsai/Lino/whynotme')


@pytest.mark.parametrize('optional_ref', [None, [None, 'source-1.detail', {'invalid': True}], {'invalid': True}, 42])
def test_missing_profile_source_does_not_abort_title_company_retrieval(monkeypatch, optional_ref):
    from neckline.k10 import v2_profiles
    index = [{'ts_code': '300052.SZ', 'name': '公司样本', 'match_terms': ['海外业务']}]
    profile = {
        'identity': {'ts_code': '300052.SZ'}, 'summary': '海外业务',
        'review_status': 'local_draft_awaiting_user', 'compiled_at': '2026-09-09',
        'market_distribution': {
            'denominator': {'description': '海外业务口径未核', 'source_ref': optional_ref},
            'items': [{'description': '海外业务收入', 'source_ref': 'source-1.detail'}],
            'caveat': '分母来源缺失，不能确认为已核事实',
        },
        'sources': [{'source_id': 'source-1'}, {'source_id': 'unrelated-source'}],
    }
    original = deepcopy(profile)
    monkeypatch.setattr(v2_profiles, 'read_profiles',
                        lambda **kwargs: deepcopy(index if kwargs.get('index_only') else [profile]))
    result = v2_profiles.retrieve_company_context(db_path=Path('/unused'), profiles_id=PROFILES_ID, query='海外业务')
    assert result['candidateCompanyCodes'] == ['300052.SZ']
    projected = result['companyProfiles'][0]
    assert projected['market_distribution']['denominator']['source_ref'] == optional_ref
    assert projected['market_distribution']['caveat'] == original['market_distribution']['caveat']
    assert projected['sources'] == [{'source_id': 'source-1'}]
    assert projected['review_status'] == 'local_draft_awaiting_user'
    assert profile == original


def test_external_connections_are_denied():
    with pytest.raises(RuntimeError, match='Offline tests'):
        socket.getaddrinfo('api.deepseek.com', 443)
    with socket.socket() as sock, pytest.raises(RuntimeError, match='Offline tests'):
        sock.connect(('1.1.1.1', 443))


def test_fixed_snapshot_atomic_and_idempotent(tmp_path):
    universe = INPUT / 'research/K10-v2初始股票池_20260909.json'
    profiles = INPUT / 'artifacts/output/k10-company-profiles-v2-20260909'
    if not universe.exists():
        pytest.skip('explicit frozen local input not installed')
    db = tmp_path / 'isolated.sqlite'
    initialize_schema(db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE historical_sentinel (value TEXT)")
        conn.execute("INSERT INTO historical_sentinel VALUES ('frozen K10-v1.4 evidence')")
    args = dict(universe_file=universe, profiles_dir=profiles, db_path=db, confirmed_target=db,
                universe_id=UNIVERSE_ID, profiles_id=PROFILES_ID, imported_at='2026-09-09T12:00:00+08:00')
    assert import_profiles(**args)['status'] == 'imported'
    assert import_profiles(**args)['status'] == 'unchanged'
    rows = read_profiles(db_path=db, profiles_id=PROFILES_ID)
    assert len(rows) == 1089
    assert {row['review_status'] for row in rows} == {'local_draft_awaiting_user'}
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM k10_v2_company_profile_evidence').fetchone()[0] == 1089
        assert conn.execute('SELECT value FROM historical_sentinel').fetchone()[0] == 'frozen K10-v1.4 evidence'
    from neckline.k10.v2_profiles import retrieve_company_context
    recalled = retrieve_company_context(db_path=db, profiles_id=PROFILES_ID, query='亚马逊云科技生成式AI认证变化')
    assert '300002.SZ' in recalled['candidateCompanyCodes']  # no company name in query
    company = next(row for row in recalled['companyProfiles'] if row['identity']['ts_code']=='300002.SZ')
    assert company['relationships'] and company['sources']
    assert 'evidence' not in company and 'notes' not in company
    assert company['review_status'] == 'local_draft_awaiting_user'
    before = db.read_bytes()
    corrupt = tmp_path / 'corrupt.json'
    corrupt.write_text('{}')
    with pytest.raises(ValueError, match='SHA-256'):
        import_profiles(**{**args, 'universe_file': corrupt})
    assert db.read_bytes() == before
    with pytest.raises(ValueError, match='confirmed-target'):
        import_profiles(**{**args, 'confirmed_target': tmp_path / 'wrong.sqlite'})
    assert db.read_bytes() == before
