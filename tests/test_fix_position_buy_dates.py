"""`scripts/oneoff/fix_position_buy_dates.py` 单测(v1.4-①-A / §七 P0-1,🔴 碰持仓判定)。

承阶段 0 教训「改脚本级写库代码先补一层单测」。重点锁四件事:
  ① 幂等(二次跑零改动);② 定格三列被清干净;③ ts_code 防呆断言真拦得住;
  ④ **其余行零改动**(逐行对拍,不是只数行数)。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "oneoff"))

from fix_position_buy_dates import (  # noqa: E402
    Fix,
    apply_buy_date_fixes,
    backup_db,
    diff_snapshots,
    snapshot,
    _parse_fix,
)


def _mk_db(tmp_path: Path, today: date = None) -> Path:
    """建一份最小可用库:trade_cal(近 30 自然日,工作日开市)+ 3 笔 open 持仓
    + 各一行 holding_eod_check(含一行已定格)。`today` 缺省取真实 `date.today()`
    (既有行为);冻结时钟的用例(§七 P1-46)显式传固定工作日。"""
    from neckline.db import init_schema

    db = tmp_path / "neckline.db"
    init_schema(db_path=db)
    conn = sqlite3.connect(str(db))
    try:
        today = today or date.today()
        rows = []
        for i in range(-30, 1):
            d = today + timedelta(days=i)
            rows.append(("SSE", d.strftime("%Y%m%d"), 1 if d.weekday() < 5 else 0, ""))
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (exchange, cal_date, is_open, pretrade_date) VALUES (?,?,?,?)",
            rows,
        )
        wrong = today.strftime("%Y%m%d")
        for pid, code, price, qty in ((1, "300759.SZ", 39.42, 500),
                                      (2, "300261.SZ", 4.952, 8000),
                                      (3, "002036.SZ", 7.184, 3000)):
            conn.execute(
                "INSERT INTO positions (id, ts_code, buy_price, qty, buy_date, status, note, "
                "created_at, updated_at, buy_fees) VALUES (?,?,?,?,?, 'open', ?, ?, ?, ?)",
                (pid, code, price, qty, wrong, f"note{pid}", "2026-07-27T06:00:00+00:00",
                 "2026-07-27T06:00:00+00:00", 5.0),
            )
        # 三行 EOD 体检:#1 已定格(必须被清)、#2/#3 未定格(不该被动)。
        conn.execute(
            "INSERT INTO holding_eod_check (position_id, trade_date, d_count, net_float, "
            "time_exit_state, max_hold_effective, k4_hits_json, has_strong, scenario_review, "
            "created_at, time_exit_locked_state, time_exit_locked_date, time_exit_locked_net_float) "
            "VALUES (1, '20260727', 5, -344.88, 'time_exit_next_day', 5, '[]', 0, 0, "
            "'2026-07-28T03:55:53+00:00', 'time_exit_next_day', '20260727', -344.88)"
        )
        for pid in (2, 3):
            conn.execute(
                "INSERT INTO holding_eod_check (position_id, trade_date, d_count, net_float, "
                "time_exit_state, max_hold_effective, k4_hits_json, has_strong, scenario_review, "
                "created_at) VALUES (?, '20260727', 1, NULL, 'holding', 5, '[]', 0, 0, "
                "'2026-07-28T03:55:53+00:00')",
                (pid,),
            )
        conn.commit()
    finally:
        conn.close()
    return db


def _last_weekday(back: int) -> str:
    d, seen = date.today(), 0
    while seen < back:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            seen += 1
    return d.strftime("%Y%m%d")


def _read(db: Path, sql: str, params=()):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# —— 解析 ——————————————————————————————————————————————————————————————

def test_parse_fix_spec():
    f = _parse_fix("3:002036.SZ:20260722")
    assert (f.position_id, f.expect_ts_code, f.new_buy_date) == (3, "002036.SZ", "20260722")


@pytest.mark.parametrize("bad", ["3:002036.SZ", "x:002036.SZ:20260722", "3:002036.SZ:2026-07-22"])
def test_parse_fix_rejects_malformed(bad):
    with pytest.raises(ValueError):
        _parse_fix(bad)


# —— 主路径 ——————————————————————————————————————————————————————————————

def test_dry_run_changes_nothing(tmp_path):
    """不带 --confirm 全程只读:报告照出,库一个字节不改。"""
    db = _mk_db(tmp_path)
    before = _read(db, "SELECT * FROM positions ORDER BY id")
    rep = apply_buy_date_fixes(db, [Fix(3, "002036.SZ", _last_weekday(4))], confirm=False)
    assert rep.dry_run is True and len(rep.changed) == 1
    assert _read(db, "SELECT * FROM positions ORDER BY id") == before


def test_fix_writes_buy_date_and_clears_locks(tmp_path):
    """真跑:买入日改对 + 定格三列清空 + integrity ok + **其余行零改动**。"""
    db = _mk_db(tmp_path)
    target = _last_weekday(4)
    conn = sqlite3.connect(str(db))
    before = snapshot(conn)
    conn.close()

    rep = apply_buy_date_fixes(
        db,
        [Fix(1, "300759.SZ", _last_weekday(1)), Fix(3, "002036.SZ", target)],
        confirm=True,
    )
    assert rep.integrity == "ok"
    assert _read(db, "SELECT buy_date FROM positions WHERE id=3")[0][0] == target
    # #1 的定格三列被清(它的买入日也变了 → 定格建立在错误 D 上)
    assert _read(
        db,
        "SELECT time_exit_locked_state, time_exit_locked_date, time_exit_locked_net_float "
        "FROM holding_eod_check WHERE position_id=1",
    ) == [(None, None, None)]
    assert len(rep.locks_cleared) == 1

    # 「其余行零改动」:逐行对拍,只允许 positions#1/#3 与 heoc#1 三行有 diff。
    conn = sqlite3.connect(str(db))
    after = snapshot(conn)
    conn.close()
    deltas = diff_snapshots(before, after)
    assert set(deltas) == {"positions", "holding_eod_check"}
    assert {rb[0] for rb, _ra in deltas["positions"]} == {1, 3}
    assert {rb[0] for rb, _ra in deltas["holding_eod_check"]} == {1}
    # #2 未在 fix 列表里 → 买入日与定格列一律不动
    assert _read(db, "SELECT buy_date FROM positions WHERE id=2")[0][0] == date.today().strftime("%Y%m%d")


def test_idempotent_second_run_zero_change(tmp_path):
    """幂等:同一份 --fix 再跑一次 → 全部走 skipped,库逐行不变。"""
    db = _mk_db(tmp_path)
    target = _last_weekday(4)
    apply_buy_date_fixes(db, [Fix(3, "002036.SZ", target)], confirm=True)

    conn = sqlite3.connect(str(db))
    before = snapshot(conn)
    conn.close()
    rep2 = apply_buy_date_fixes(db, [Fix(3, "002036.SZ", target)], confirm=True)
    conn = sqlite3.connect(str(db))
    after = snapshot(conn)
    conn.close()

    assert rep2.changed == [] and len(rep2.skipped) == 1
    assert diff_snapshots(before, after) == {}


def test_unchanged_position_does_not_bump_updated_at(tmp_path, monkeypatch):
    """已是目标值的笔连 `updated_at` 都不动(否则「零改动」是假的)。

    §七 P1-46(周末日期炸弹):本用例的目标日 = 「今天」本身,老写法直连
    `date.today()` → 逢周末目标日不是交易日,`_validate_target_dates` 直接
    SystemExit,全量套件周末必红。修法照 A7 `frozen_clock` 体例:把「今天」钉在
    固定周三 2026-07-29(库与 fix 目标同源),并 monkeypatch 脚本模块的 `date`
    名字让 `_validate_target_dates` 的 future 判定也用同一冻结时钟(⛔ 不改生产
    代码来迁就测试)。"""
    import fix_position_buy_dates as script_mod

    frozen = date(2026, 7, 29)   # 周三(交易日)

    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 29)

    monkeypatch.setattr(script_mod, "date", _FrozenDate)
    db = _mk_db(tmp_path, today=frozen)
    today = frozen.strftime("%Y%m%d")
    before = _read(db, "SELECT updated_at FROM positions WHERE id=2")[0][0]
    apply_buy_date_fixes(db, [Fix(2, "300261.SZ", today)], confirm=True)
    assert _read(db, "SELECT updated_at FROM positions WHERE id=2")[0][0] == before


# —— 防呆闸 ——————————————————————————————————————————————————————————————

def test_ts_code_mismatch_aborts_everything(tmp_path):
    """id 记错(ts_code 对不上)→ 整体中止,**连对的那笔也不写**。"""
    db = _mk_db(tmp_path)
    conn = sqlite3.connect(str(db))
    before = snapshot(conn)
    conn.close()

    with pytest.raises(SystemExit):
        apply_buy_date_fixes(
            db,
            [Fix(3, "002036.SZ", _last_weekday(4)), Fix(1, "600519.SH", _last_weekday(1))],
            confirm=True,
        )
    conn = sqlite3.connect(str(db))
    after = snapshot(conn)
    conn.close()
    assert diff_snapshots(before, after) == {}


def test_missing_position_aborts(tmp_path):
    db = _mk_db(tmp_path)
    with pytest.raises(SystemExit):
        apply_buy_date_fixes(db, [Fix(99, "002036.SZ", _last_weekday(4))], confirm=True)


def test_non_trading_day_target_aborts(tmp_path):
    """目标日不是交易日 → 中止(与 ①-A 服务端 `not_trading_day` 同口径)。"""
    db = _mk_db(tmp_path)
    d = date.today()
    while d.weekday() < 5:
        d -= timedelta(days=1)
    with pytest.raises(SystemExit):
        apply_buy_date_fixes(db, [Fix(3, "002036.SZ", d.strftime("%Y%m%d"))], confirm=True)


def test_future_target_aborts(tmp_path):
    db = _mk_db(tmp_path)
    future = (date.today() + timedelta(days=5)).strftime("%Y%m%d")
    with pytest.raises(SystemExit):
        apply_buy_date_fixes(db, [Fix(3, "002036.SZ", future)], confirm=True)


# —— 备份 ——————————————————————————————————————————————————————————————

def test_backup_makes_two_readable_copies(tmp_path):
    """`.backup`(在线一致性)+ `cp -p` 双保险,两份都必须能独立打开读到原数据。"""
    db = _mk_db(tmp_path)
    bak, cpbak = backup_db(db, "utest")
    for p in (bak, cpbak):
        assert p.exists()
        assert _read(p, "SELECT COUNT(*) FROM positions")[0][0] == 3
