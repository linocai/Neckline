from datetime import date
import sqlite3

import polars as pl

from neckline.data.market_data import write_table_day
from neckline.k10.v2_store import read_report
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for


def test_real_market_history_survives_worker_publication_and_report_api(tmp_path, monkeypatch):
    day = date(2026, 9, 8)
    parquet = tmp_path / 'parquet'
    write_table_day('daily', day, pl.DataFrame([{'ts_code': '300002.SZ', 'trade_date': day,
        'open': 10.0, 'high': 11.5, 'low': 10.0, 'close': 11.2, 'pre_close': 10.0, 'pct_chg': 12.0}]), parquet_dir=parquet)
    write_table_day('adj_factor', day, pl.DataFrame([{'ts_code': '300002.SZ', 'trade_date': day,
        'adj_factor': 1.0}]), parquet_dir=parquet)
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert task.status == 'completed'
    saved = read_report(db_path=db)['eveningCards'][0]['priceContext']
    assert saved and saved['pctChg'] == 12.0
    assert len(saved['sourceRefs']) == 2
    assert all(ref['dataFetchedAt'] == 'unknown' and ref['collectedAt'] for ref in saved['sourceRefs'])
    with sqlite3.connect(db) as conn:
        before = conn.execute('SELECT * FROM k10_v2_report_cards').fetchall()
    response = client_for(db).get('/api/v1/k10/v2/reports/latest?window=evening')
    assert response.status_code == 200
    card = response.json()['report']['eveningCards'][0]
    assert card['priceContext']['pctChg'] == 12.0
    for actual, original in zip(card['priceContext']['sourceRefs'], saved['sourceRefs']):
        assert all(actual[key] == value for key, value in original.items())
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT * FROM k10_v2_report_cards').fetchall() == before
