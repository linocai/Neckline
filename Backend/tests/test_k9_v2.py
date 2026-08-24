"""K9-v2 工程契约：参数、四路召回、版本隔离与可选证据。"""

from __future__ import annotations

import dataclasses
import json

import pytest

from neckline.db import readonly_connection
from neckline.k9 import params as P
from neckline.k9 import run
from neckline.k9.contract import PATTERN_LABEL, STRATEGY_VERSION, Pattern
from tests import k9_env


def _load(env, tmp_path, **overrides):
    return k9_env.params(env, tmp_path, **overrides)


def test_parameter_contract_has_no_defaults_and_loads_v2(isolated_env, tmp_path):
    loaded = _load(isolated_env, tmp_path)
    assert loaded.strategy_version == "K9-v2"
    assert loaded.fact_pack_version == "fp-3"
    assert loaded.label_contract_version == "d2-v1"
    assert P.assert_no_field_defaults(P.K9Params) == []
    assert dataclasses.is_dataclass(loaded) and loaded.__dataclass_params__.frozen


@pytest.mark.parametrize("path,value", [
    ("strategyVersion", "K9-v1"),
    ("factPackVersion", "fp-2"),
    ("schemaVersion", "k9-params-v1"),
])
def test_v1_or_wrong_contract_is_refused(isolated_env, tmp_path, path, value):
    target = tmp_path / "bad.json"
    target.write_text(json.dumps(k9_env.raw_params(**{path: value})), encoding="utf-8")
    with pytest.raises(P.ParamsUnavailable):
        P.load(target, db_path=isolated_env.db_path)


def test_example_is_deliberately_not_runnable(isolated_env):
    from pathlib import Path
    example = Path(__file__).parents[1] / "config" / "k9-params.example.json"
    raw = json.loads(example.read_text(encoding="utf-8"))
    missing, invalid, _ = P.validate(raw)
    assert missing or invalid
    approved = example.parent / "k9-params.json"
    loaded = P.load(approved, db_path=isolated_env.db_path)
    assert loaded.package_version == "k9-params-20260824-v2-r1"
    assert loaded.source_sha256 == "718bf7876d69936937edfdc7432bbea88ec1cd3e6e6107501acd325b7f1098df"


def test_unknown_keys_and_reversed_tiers_are_refused(isolated_env, tmp_path):
    extra = k9_env.raw_params()
    extra["typoThreshold"] = 1
    extra_path = tmp_path / "extra.json"
    extra_path.write_text(json.dumps(extra), encoding="utf-8")
    with pytest.raises(P.ParamsUnavailable, match="未声明键"):
        P.load(extra_path, db_path=isolated_env.db_path)

    reversed_tier = tmp_path / "reversed.json"
    reversed_tier.write_text(json.dumps(k9_env.raw_params(
        **{"channels.p1.strict.minVolMultiple": 0.5,
           "channels.p1.relaxed.minVolMultiple": 1.0}
    )), encoding="utf-8")
    with pytest.raises(P.ParamsUnavailable, match="strict 不得比 relaxed 更宽"):
        P.load(reversed_tier, db_path=isolated_env.db_path)


def test_four_channels_and_p1_p3_identity_are_separate(isolated_env, tmp_path):
    day = k9_env.seed(isolated_env)
    result = run.compute(
        day, params=_load(isolated_env, tmp_path),
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_pattern = {
        pattern: {hit.ts_code for hit in result.hits if hit.pattern is pattern}
        for pattern in Pattern
    }
    assert k9_env.P1_CODE in by_pattern[Pattern.P1]
    assert k9_env.P2_CODE in by_pattern[Pattern.P2]
    assert k9_env.P3_CODE in by_pattern[Pattern.P3]
    assert k9_env.P4_CODE in by_pattern[Pattern.P4]
    assert by_pattern[Pattern.P1].isdisjoint(by_pattern[Pattern.P3])
    assert k9_env.ONE_LINE_CODE not in by_pattern[Pattern.P2]
    assert PATTERN_LABEL[Pattern.P3] == "热门强博弈"
    assert PATTERN_LABEL[Pattern.P4] == "资金领先价格"


def test_missing_top_list_is_unknown_not_false(isolated_env, tmp_path):
    day = k9_env.seed(isolated_env)
    result = run.compute(
        day, params=_load(isolated_env, tmp_path),
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    p3_hits = [hit for hit in result.hits if hit.pattern is Pattern.P3]
    assert p3_hits and all(hit.evidence["topList"] is None for hit in p3_hits)


def test_persisted_rows_are_explicitly_k9_v2(isolated_env, tmp_path):
    day = k9_env.seed(isolated_env)
    result, run_id = run.run_k9(
        day, params=_load(isolated_env, tmp_path),
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert result.shortlist.strategy_version == STRATEGY_VERSION
    with readonly_connection(isolated_env.db_path) as conn:
        assert conn.execute(
            "SELECT strategy_version FROM k9_runs WHERE run_id=?", (run_id,)
        ).fetchone()[0] == "K9-v2"
        assert {row[0] for row in conn.execute(
            "SELECT DISTINCT strategy_version FROM k9_listing_entries"
        )} <= {"K9-v2"}


def test_same_pack_and_params_are_deterministic(isolated_env, tmp_path):
    day = k9_env.seed(isolated_env)
    params = _load(isolated_env, tmp_path)
    kwargs = dict(params=params, parquet_dir=isolated_env.parquet_dir,
                  db_path=isolated_env.db_path)
    first, second = run.compute(day, **kwargs), run.compute(day, **kwargs)
    assert [entry.to_row() for entry in first.shortlist.entries] == [
        entry.to_row() for entry in second.shortlist.entries]
