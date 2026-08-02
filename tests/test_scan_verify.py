"""扫描层三项自检 `neckline/scan/verify.py` 单测(plan §五 V2-④)。`test_scan_
layer_script.py` 已经过 CLI 覆盖了一条"干净数据全绿 + 一条注入错误被抓"的
端到端路径;本文件逐项证明**每一类**判据各自有牙齿(不是只测了 cluster_size
这一种)。
"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.db import connection
from neckline.scan import verify
from tests.conftest import insert_trade_cal

D0 = date(2024, 7, 1)
D1 = date(2024, 7, 2)


def _insert_cluster(env, trade_date, cluster_key, ts_code, *, cluster_size, anchor_industry="半导体", anchor_concept=None, consecutive_days=1):
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT INTO limit_cluster_daily "
            "(trade_date, cluster_key, ts_code, cluster_kind, cluster_size, consecutive_days, "
            " anchor_industry, anchor_concept, computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (trade_date.strftime("%Y%m%d"), cluster_key, ts_code, "same_day", cluster_size, consecutive_days,
             anchor_industry, anchor_concept, "2024-01-01T00:00:00+00:00"),
        )


def _insert_corr(env, trade_date, scope_key, code_a, code_b, *, window=None, corr_val=0.5):
    win = window if window is not None else verify.corr.PRICE_WINDOW_DAYS
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT INTO corr_matrix_daily (trade_date, scope_key, code_a, code_b, window, corr, n_obs, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date.strftime("%Y%m%d"), scope_key, code_a, code_b, win, corr_val, 20, "2024-01-01T00:00:00+00:00"),
        )


def _insert_leader(env, trade_date, cluster_key, ts_code, *, rs_rank=1, role_mech="leader"):
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT INTO leader_structure_daily "
            "(trade_date, cluster_key, ts_code, rs_rank, limit_height, amount_share, role_mech, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date.strftime("%Y%m%d"), cluster_key, ts_code, rs_rank, 1, 0.5, role_mech, "2024-01-01T00:00:00+00:00"),
        )


def test_empty_db_reports_reason(isolated_env):
    res = verify.verify_scan_layer(db_path=isolated_env.db_path)
    assert res["ok"] is False
    assert "reason" in res


def test_clean_consistent_data_is_ok(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    _insert_cluster(env, D0, "K1", "600001.SH", cluster_size=2)
    _insert_cluster(env, D0, "K1", "600002.SH", cluster_size=2)
    _insert_corr(env, D0, "K1", "600001.SH", "600002.SH")
    _insert_leader(env, D0, "K1", "600001.SH", rs_rank=1, role_mech="leader")
    _insert_leader(env, D0, "K1", "600002.SH", rs_rank=2, role_mech="core")

    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert res["ok"] is True, res


def test_detects_non_trading_day_row(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])   # D1 未登记为交易日
    _insert_cluster(env, D1, "K1", "600001.SH", cluster_size=2)
    _insert_cluster(env, D1, "K1", "600002.SH", cluster_size=2)

    res = verify.verify_scan_layer(D0, D1, db_path=env.db_path)
    assert res["ok"] is False
    assert "limit_cluster_daily" in res["non_trading_day_rows"]


def test_detects_cluster_size_mismatch(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    _insert_cluster(env, D0, "K1", "600001.SH", cluster_size=5)   # 实际只有 1 个成员
    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert res["ok"] is False
    assert any("cluster_size" in e for e in res["self_consistency_errors"])


def test_detects_anchor_not_mutually_exclusive(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    _insert_cluster(env, D0, "K1", "600001.SH", cluster_size=1, anchor_industry="半导体", anchor_concept="885001.TI")
    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert res["ok"] is False
    assert any("anchor_industry/anchor_concept" in e for e in res["self_consistency_errors"])


def test_detects_corr_reversed_pair(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    _insert_corr(env, D0, "K1", "600002.SH", "600001.SH")   # 反序
    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert res["ok"] is False
    assert any("应严格小于" in e for e in res["self_consistency_errors"])


def test_detects_invalid_role_mech(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    _insert_leader(env, D0, "K1", "600001.SH", rs_rank=1, role_mech="龙头")   # 非法枚举值
    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert res["ok"] is False
    assert any("role_mech" in e for e in res["self_consistency_errors"])


def test_detects_duplicate_rs_rank_in_same_cluster(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    _insert_leader(env, D0, "K1", "600001.SH", rs_rank=1, role_mech="leader")
    _insert_leader(env, D0, "K1", "600002.SH", rs_rank=1, role_mech="core")   # 并列未拆开
    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert res["ok"] is False
    assert any("重复" in e for e in res["self_consistency_errors"])


def test_detects_cluster_member_missing_from_leader_table(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    _insert_cluster(env, D0, "K1", "600001.SH", cluster_size=2)
    _insert_cluster(env, D0, "K1", "600002.SH", cluster_size=2)
    _insert_leader(env, D0, "K1", "600001.SH")   # 600002 漏跑
    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert res["ok"] is False
    assert any("600002.SH" in e and "leader_structure_daily" in e for e in res["self_consistency_errors"])


def test_detects_missing_corr_coverage_for_small_cluster(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    _insert_cluster(env, D0, "K1", "600001.SH", cluster_size=2)
    _insert_cluster(env, D0, "K1", "600002.SH", cluster_size=2)
    _insert_leader(env, D0, "K1", "600001.SH", rs_rank=1, role_mech="leader")
    _insert_leader(env, D0, "K1", "600002.SH", rs_rank=2, role_mech="core")
    # 未超规模上限,却完全没有 corr_matrix_daily 行
    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert res["ok"] is False
    assert any("corr_matrix_daily 无任何行" in e for e in res["self_consistency_errors"])


def test_oversized_cluster_does_not_require_corr_coverage(isolated_env):
    """规模超过 `MAX_SCOPE_MEMBERS_FOR_CORR` 的簇本就不会产出 corr 行(见
    `corr.build_scope_membership` 的规模上限),verify 不该为此报错。"""
    env = isolated_env
    insert_trade_cal(env, [D0])
    huge_size = verify.corr.MAX_SCOPE_MEMBERS_FOR_CORR + 5
    _insert_cluster(env, D0, "K1", "600001.SH", cluster_size=huge_size)
    _insert_leader(env, D0, "K1", "600001.SH", rs_rank=1, role_mech="leader")
    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert not any("corr_matrix_daily 无任何行" in e for e in res["self_consistency_errors"])


def test_detects_stale_window_fingerprint(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    _insert_corr(env, D0, "K1", "600001.SH", "600002.SH", window=15)   # 与现行常量(20)不符
    res = verify.verify_scan_layer(D0, D0, db_path=env.db_path)
    assert res["ok"] is False
    assert any("window=15" in m for m in res["fingerprint_mismatches"])
