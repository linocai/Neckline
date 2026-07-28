"""`scripts/oneoff/fix_moneyflow_schema.py` 的 mock 单测(K3 · B0.1)。

承阶段 0 教训「改脚本级落盘代码先补一层 mock 单测」:在临时目录里合成一批**类型漂移
的分区文件**(部分列 String、部分 Float64,含空文件与含真数据文件),验证 `repair_
float_columns`:① 修后整表 `scan_parquet` 不再 SchemaError;② 真数据数值原样保留
(String→Float64 精确 cast);③ 空串/非法值→null;④ 幂等(二次运行零改动);⑤ 非目标
列(trade_date/ts_code/name)与列顺序不被动。

与真实 `data/` 完全隔离(纯 tmp_path),`scripts/report.py` 一样属**非纳测运行脚本**,
本测只锁其可导入核心函数。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts" / "oneoff")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import fix_moneyflow_schema as fix  # noqa: E402

FLOAT_COLS = fix.MONEYFLOW_FLOAT_COLS
# 完整列集(顺序照真实 moneyflow_dc):非漂移列 + 12 漂移列
NON_FLOAT_COLS = ["trade_date", "ts_code", "name"]
ALL_COLS = NON_FLOAT_COLS + FLOAT_COLS


def _write_str_file(path: Path, rows: list[dict]) -> None:
    """把 12 数值列写成 String(模拟 TuShare object→polars String 漂移分区)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "trade_date": [r["trade_date"] for r in rows],
        "ts_code": [r["ts_code"] for r in rows],
        "name": [r["name"] for r in rows],
    }
    for c in FLOAT_COLS:
        data[c] = [r.get(c) for r in rows]  # 值以字符串给
    df = pl.DataFrame(
        data,
        schema={
            "trade_date": pl.Date,
            "ts_code": pl.Utf8,
            "name": pl.Utf8,
            **{c: pl.Utf8 for c in FLOAT_COLS},
        },
    )
    df.write_parquet(path)


def _write_float_file(path: Path, rows: list[dict]) -> None:
    """canonical Float64 分区。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "trade_date": [r["trade_date"] for r in rows],
        "ts_code": [r["ts_code"] for r in rows],
        "name": [r["name"] for r in rows],
    }
    for c in FLOAT_COLS:
        data[c] = [r.get(c) for r in rows]
    df = pl.DataFrame(
        data,
        schema={
            "trade_date": pl.Date,
            "ts_code": pl.Utf8,
            "name": pl.Utf8,
            **{c: pl.Float64 for c in FLOAT_COLS},
        },
    )
    df.write_parquet(path)


def _str_row(td: date, code: str, name: str, base: float) -> dict:
    r = {"trade_date": td, "ts_code": code, "name": name}
    for i, c in enumerate(FLOAT_COLS):
        r[c] = f"{base + i:.2f}"  # 数值字符串
    return r


def _float_row(td: date, code: str, name: str, base: float) -> dict:
    r = {"trade_date": td, "ts_code": code, "name": name}
    for i, c in enumerate(FLOAT_COLS):
        r[c] = base + i
    return r


@pytest.fixture
def drifted_table(tmp_path: Path) -> Path:
    """合成 moneyflow_dc 类型漂移目录:
    - year=2020: 空文件(0 行)、12 列 String —— 早期空分区
    - year=2023: Float64 含数据 —— canonical
    - year=2026: String 含**真数据** —— 毒源(必须精确保值)
    """
    root = tmp_path / "moneyflow_dc"
    # 空 String 文件(0 行)
    _write_str_file(root / "year=2020" / "20200102.parquet", [])
    # canonical Float64 文件
    _write_float_file(
        root / "year=2023" / "20230911.parquet",
        [_float_row(date(2023, 9, 11), "000001.SZ", "平安银行", 100.0)],
    )
    # 毒源:String 含真数据 + 一行含空串(应→null)
    poison_rows = [
        _str_row(date(2026, 7, 20), "000938.SZ", "紫光股份", 37.69),
        _str_row(date(2026, 7, 20), "300750.SZ", "宁德时代", 376.43),
    ]
    poison_rows[1]["net_amount"] = ""  # 空串 → null
    poison_rows[1]["pct_change"] = "abc"  # 非法 → null
    _write_str_file(root / "year=2026" / "20260720.parquet", poison_rows)
    return root


def _whole_scan_ok(root: Path) -> int:
    """整表 scan 并**投影一个漂移列**强制 schema 对账(与真实 `scan_table_range` 的
    全列 collect 同款触发条件;若只 `select(pl.len())` 投影下推会绕开对账、测不出漂移)。
    返回读到的行数。"""
    return (
        pl.scan_parquet(str(root / "year=*" / "*.parquet"))
        .select(pl.col("pct_change"))
        .collect()
        .height
    )


def test_broken_before_repair(drifted_table: Path) -> None:
    """前置断言:修前整表 scan 必 SchemaError(证明夹具确实漂移)。"""
    with pytest.raises(Exception) as ei:
        _whole_scan_ok(drifted_table)
    assert "mismatch" in str(ei.value).lower() or "schema" in str(ei.value).lower()


def test_repair_makes_whole_scan_readable(drifted_table: Path) -> None:
    res = fix.repair_float_columns(drifted_table)
    assert res["scanned"] == 3
    # 2 个 String 文件(空 + 毒源)被修,Float64 文件跳过
    assert res["fixed"] == 2
    # 修后整表可读,总行数 = 0(空) + 1(canonical) + 2(毒源) = 3
    assert _whole_scan_ok(drifted_table) == 3
    # 各分区 12 列均为 Float64
    import glob

    for f in glob.glob(str(drifted_table / "year=*" / "*.parquet")):
        sch = pl.read_parquet_schema(f)
        for c in FLOAT_COLS:
            assert sch[c] == pl.Float64, f"{f}:{c} 未转 Float64"


def test_repair_preserves_real_values(drifted_table: Path) -> None:
    fix.repair_float_columns(drifted_table)
    df = pl.read_parquet(drifted_table / "year=2026" / "20260720.parquet").sort("ts_code")
    # 紫光股份第一行:pct_change=37.69, close=38.69(base+1), net_amount=39.69(base+2)...
    row0 = df.filter(pl.col("ts_code") == "000938.SZ").row(0, named=True)
    assert row0["pct_change"] == pytest.approx(37.69)
    assert row0["close"] == pytest.approx(38.69)
    assert row0["net_amount"] == pytest.approx(39.69)
    # 宁德时代那行:空串 net_amount→null,非法 pct_change→null,其余仍是数值
    row1 = df.filter(pl.col("ts_code") == "300750.SZ").row(0, named=True)
    assert row1["net_amount"] is None
    assert row1["pct_change"] is None
    assert row1["close"] == pytest.approx(377.43)  # 未受污染的列仍精确


def test_non_target_cols_and_order_untouched(drifted_table: Path) -> None:
    fix.repair_float_columns(drifted_table)
    df = pl.read_parquet(drifted_table / "year=2026" / "20260720.parquet")
    # 列顺序与非目标列类型不变
    assert df.columns == ALL_COLS
    assert df.schema["trade_date"] == pl.Date
    assert df.schema["ts_code"] == pl.Utf8
    assert df.schema["name"] == pl.Utf8
    # 名称原样
    names = set(df["name"].to_list())
    assert {"紫光股份", "宁德时代"} == names


def test_idempotent(drifted_table: Path) -> None:
    first = fix.repair_float_columns(drifted_table)
    assert first["fixed"] == 2
    second = fix.repair_float_columns(drifted_table)
    assert second["scanned"] == 3
    assert second["fixed"] == 0  # 二次运行零改动


def test_dry_run_does_not_write(drifted_table: Path) -> None:
    res = fix.repair_float_columns(drifted_table, dry_run=True)
    assert res["fixed"] == 2  # 报告 2 个待修
    # 但文件未落盘:整表 scan 仍坏
    with pytest.raises(Exception):
        _whole_scan_ok(drifted_table)
