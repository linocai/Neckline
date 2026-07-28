"""概念板块日更 / 周更落盘单测(v1.4-①-C / §七 P0-3,`neckline/data/concept_data.py`)。

**本文件的头号守门项** = 扁平单文件原子追加的 dtype 声明护栏:构造一列**全空**的当日
增量,断言追加后该列 dtype 仍等于 `THS_DAILY_DTYPES` 声明值(不退化成 String)。
`ths_daily.parquet` 是 `write_table_day` 铁律的**唯一登记例外**,这条断言就是那份例外
的对价 —— 它一挂,例外的三条理由里的第 ③ 条(声明 cast 等价覆盖类型漂移)就不成立了。
"""

from __future__ import annotations

import json
from datetime import date

import polars as pl
import pytest

from neckline.data import concept_data as cd


class _Res:
    """TuShareResult 替身(duck-typed:只需 ok / data / reason)。"""
    def __init__(self, data=None, ok=True, reason="ok"):
        self.ok, self.data, self.reason = ok, data, reason


def _pdf(rows):
    import pandas as pd

    return pd.DataFrame(rows)


def _day_rows(td: str, codes=("883300.TI", "885362.TI"), **overrides):
    out = []
    for i, c in enumerate(codes):
        r = {
            "ts_code": c, "trade_date": td, "open": 100.0 + i, "high": 101.0, "low": 99.0,
            "close": 100.5, "pre_close": 100.0, "avg_price": 100.2, "change": 0.5,
            "pct_change": 0.5, "vol": 1.0e8, "turnover_rate": 3.0,
        }
        r.update(overrides)
        out.append(r)
    return out


# —— dtype 声明护栏(**头号守门项**)——————————————————————————————————————

def test_all_empty_column_does_not_drift_to_string(tmp_path):
    """某列当日**全空** → pandas object → polars String,历史文件却是 Float64。
    追加后该列 dtype **必须仍是声明值**(这正是 2026-07-27 毒化 902 个分区的那条链)。"""
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260722"))), tmp_path)
    # 当日增量里 turnover_rate 整列 None(pandas object → polars String / Null)
    incoming = pl.from_pandas(_pdf(_day_rows("20260723", turnover_rate=None)))
    assert incoming.schema["turnover_rate"] != pl.Float64      # 前提成立:进来的确实不是 Float64
    cd.upsert_ths_daily(incoming, tmp_path)

    got = pl.read_parquet(tmp_path / cd.THS_DAILY_FILE)
    for col, dtype in cd.THS_DAILY_DTYPES.items():
        assert got.schema[col] == dtype, f"{col} 漂了:{got.schema[col]} != {dtype}"
    assert got.filter(pl.col("trade_date") == date(2026, 7, 23))["turnover_rate"].null_count() == 2


def test_declaration_wins_over_existing_file(tmp_path):
    """**永远向声明看齐,永不向现有文件看齐**:即便历史文件本身被写脏(整列 String),
    追加一次之后也要被拉回声明 dtype(脏基准不许反悔,v1.3.5 事故的核心教训)。"""
    dirty = pl.from_pandas(_pdf(_day_rows("20260722"))).with_columns(
        pl.col("close").cast(pl.String), pl.col("vol").cast(pl.String)
    )
    dirty.write_parquet(tmp_path / cd.THS_DAILY_FILE)          # 绕过 upsert,伪造脏基准
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260723"))), tmp_path)

    got = pl.read_parquet(tmp_path / cd.THS_DAILY_FILE)
    assert got.schema["close"] == pl.Float64 and got.schema["vol"] == pl.Float64
    assert got.height == 4                                      # 脏行被 cast 回来,不是被丢掉


def test_trade_date_string_becomes_date(tmp_path):
    """TuShare 原样返 'YYYYMMDD' 字符串;落盘必须是 `pl.Date`(读侧
    `compute_sector_strength` 拿 date 对象比较,String 会静默筛不出任何行)。"""
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260722"))), tmp_path)
    got = pl.read_parquet(tmp_path / cd.THS_DAILY_FILE)
    assert got.schema["trade_date"] == pl.Date
    assert got["trade_date"].to_list() == [date(2026, 7, 22)] * 2


def test_missing_declared_column_is_filled_not_dropped(tmp_path):
    """声明里有、增量里没有的列 → 补全 null 列(不静默少一列导致读侧 KeyError)。"""
    rows = [{k: v for k, v in r.items() if k != "avg_price"} for r in _day_rows("20260722")]
    cd.upsert_ths_daily(pl.from_pandas(_pdf(rows)), tmp_path)
    got = pl.read_parquet(tmp_path / cd.THS_DAILY_FILE)
    assert "avg_price" in got.columns and got.schema["avg_price"] == pl.Float64


def test_unknown_new_column_is_kept(tmp_path):
    """TuShare 将来加列 → 原样保留,不擅自丢用户数据。"""
    cd.upsert_ths_daily(
        pl.from_pandas(_pdf(_day_rows("20260722", brand_new=7.5))), tmp_path)
    assert "brand_new" in pl.read_parquet(tmp_path / cd.THS_DAILY_FILE).columns


# —— upsert 语义 ——————————————————————————————————————————————————————

def test_upsert_replaces_same_day_rows_not_appends(tmp_path):
    """同日重灌 = **整段替换**,不产生重复行(当日数据分批发布时,skip-if-exists 会把
    半份数据冻成永久事实而无人喊;整段替换才自愈)。"""
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260722", close=1.0))), tmp_path)
    cd.upsert_ths_daily(
        pl.from_pandas(_pdf(_day_rows("20260722", close=2.0, codes=("883300.TI", "885362.TI", "999999.TI")))),
        tmp_path)
    got = pl.read_parquet(tmp_path / cd.THS_DAILY_FILE)
    assert got.height == 3                                    # 2 行被替换 + 1 行新增,无重复
    assert set(got["close"].to_list()) == {2.0}


def test_upsert_keeps_other_days(tmp_path):
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260722"))), tmp_path)
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260723"))), tmp_path)
    got = pl.read_parquet(tmp_path / cd.THS_DAILY_FILE)
    assert sorted(set(got["trade_date"].to_list())) == [date(2026, 7, 22), date(2026, 7, 23)]


def test_upsert_empty_is_noop(tmp_path):
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260722"))), tmp_path)
    before = (tmp_path / cd.THS_DAILY_FILE).read_bytes()
    assert cd.upsert_ths_daily(pl.DataFrame(), tmp_path) == 0
    assert (tmp_path / cd.THS_DAILY_FILE).read_bytes() == before


def test_no_tmp_file_left_behind(tmp_path):
    """原子替换:`.tmp` 不许留在盘上(留下来会让人误以为有半份数据可用)。"""
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260722"))), tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []


def test_max_ths_daily_date(tmp_path):
    assert cd.max_ths_daily_date(tmp_path) is None             # 无文件 → None(不是 0 也不是今天)
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260722"))), tmp_path)
    cd.upsert_ths_daily(pl.from_pandas(_pdf(_day_rows("20260717"))), tmp_path)
    assert cd.max_ths_daily_date(tmp_path) == date(2026, 7, 22)


# —— 日更编排 ——————————————————————————————————————————————————————

def test_update_ths_daily_counts_empty_and_failed_separately(tmp_path):
    """「当天数据尚未发布(0 行)」与「拉取失败」是两件事,不许混成一个数
    (§3.8「没有」与「没看」必须能分开)。"""
    calls = []

    def fetch(td):
        calls.append(td)
        if td == "20260728":
            return _Res(_pdf([]))                     # 当天尚未发布
        if td == "20260727":
            return _Res(None, ok=False, reason="限频")  # 真失败
        return _Res(_pdf(_day_rows(td)))

    stats = cd.update_ths_daily(
        [date(2026, 7, 23), date(2026, 7, 27), date(2026, 7, 28)], fetch=fetch, parquet_dir=tmp_path)
    assert stats == {"days": 3, "rows": 2, "empty": 1, "failed": 1}
    assert calls == ["20260723", "20260727", "20260728"]


def test_trailing_window_self_heals_yesterday(tmp_path):
    """尾窗重拉的意义:昨天拉的时候当天数据还没发布(0 行),今天再拉就补上了。"""
    published = {"20260727"}

    def fetch(td):
        return _Res(_pdf(_day_rows(td))) if td in published else _Res(_pdf([]))

    cd.update_ths_daily([date(2026, 7, 27), date(2026, 7, 28)], fetch=fetch, parquet_dir=tmp_path)
    assert cd.max_ths_daily_date(tmp_path) == date(2026, 7, 27)
    published.add("20260728")                          # 次日:0728 已发布,尾窗把它带回来
    cd.update_ths_daily([date(2026, 7, 27), date(2026, 7, 28)], fetch=fetch, parquet_dir=tmp_path)
    assert cd.max_ths_daily_date(tmp_path) == date(2026, 7, 28)


# —— 周更快照 ——————————————————————————————————————————————————————

def test_snapshot_due_by_cadence(tmp_path):
    today = date(2026, 7, 28)
    assert cd.snapshot_due("ths_index", today, 7, tmp_path) is True      # 无记录 → 该拉
    (tmp_path / cd.SNAPSHOT_META_FILE).write_text(json.dumps({"ths_index": "20260727"}))
    assert cd.snapshot_due("ths_index", today, 7, tmp_path) is False
    (tmp_path / cd.SNAPSHOT_META_FILE).write_text(json.dumps({"ths_index": "20260720"}))
    assert cd.snapshot_due("ths_index", today, 7, tmp_path) is True


def test_replace_snapshot_keeps_bak_and_refuses_empty(tmp_path):
    """重拉前留 `.bak`;拉空**绝不覆盖**(半份成分比旧成分更糟)。"""
    old = pl.DataFrame({"ts_code": ["883300.TI"], "name": ["沪深300样本股"]})
    old.write_parquet(tmp_path / cd.THS_INDEX_FILE)

    new = pl.DataFrame({"ts_code": ["883300.TI", "885362.TI"], "name": ["A", "B"]})
    assert cd.replace_snapshot("ths_index", cd.THS_INDEX_FILE, new, date(2026, 7, 28), tmp_path) is True
    assert pl.read_parquet(tmp_path / (cd.THS_INDEX_FILE + ".bak")).height == 1
    assert pl.read_parquet(tmp_path / cd.THS_INDEX_FILE).height == 2

    assert cd.replace_snapshot("ths_index", cd.THS_INDEX_FILE, pl.DataFrame(), date(2026, 7, 29), tmp_path) is False
    assert pl.read_parquet(tmp_path / cd.THS_INDEX_FILE).height == 2      # 旧快照原样保留


def test_update_snapshots_refuses_half_member_pull(tmp_path):
    """成分只拉回一部分(过半失败)→ **不覆盖**,旧 `ths_member` 原样保留。"""
    pl.DataFrame({"index_code": ["883300.TI"], "con_code": ["600001.SH"]}).write_parquet(
        tmp_path / cd.THS_MEMBER_FILE)
    idx = _pdf([{"ts_code": f"88{i:04d}.TI", "name": f"板块{i}"} for i in range(10)])

    def fetch_member(code):
        return _Res(_pdf([{"ts_code": code, "con_code": "600001.SH"}])) if code.endswith("0.TI") else _Res(_pdf([]))

    out = cd.update_ths_snapshots(
        date(2026, 7, 28), fetch_index=lambda: _Res(idx), fetch_member=fetch_member,
        parquet_dir=tmp_path)
    assert out["ths_index"] is True and out["ths_member"] is False
    assert pl.read_parquet(tmp_path / cd.THS_MEMBER_FILE).height == 1     # 旧成分没被半份覆盖


def test_update_snapshots_skips_when_not_due(tmp_path):
    """未到期 → 一次接口都不调(周更的成本是 ~400 次调用,不能每天烧)。"""
    (tmp_path / cd.SNAPSHOT_META_FILE).write_text(json.dumps({"ths_index": "20260727"}))
    called = []
    out = cd.update_ths_snapshots(
        date(2026, 7, 28), fetch_index=lambda: called.append(1) or _Res(None, ok=False),
        fetch_member=lambda c: _Res(None, ok=False), parquet_dir=tmp_path)
    assert called == [] and out == {"ths_index": False, "ths_member": False}


def test_update_snapshots_index_empty_aborts_everything(tmp_path):
    """板块列表拉空 → 成分也不动(没有列表就谈不上成分)。"""
    called = []
    out = cd.update_ths_snapshots(
        date(2026, 7, 28), fetch_index=lambda: _Res(_pdf([])),
        fetch_member=lambda c: called.append(c) or _Res(_pdf([])), parquet_dir=tmp_path)
    assert out == {"ths_index": False, "ths_member": False} and called == []


# —— 概念板块过滤(2026-07-28 实测踩到:不带 ts_code 的 ths_daily 返回全部板块指数)——

def _seed_index(tmp_path, codes):
    pl.DataFrame({"ts_code": list(codes), "name": [f"板块{c}" for c in codes]}).write_parquet(
        tmp_path / cd.THS_INDEX_FILE)


def test_update_ths_daily_filters_to_concept_indices(tmp_path):
    """`trade_date=` 调用返回的是**全部**板块指数(概念+行业+地域);落盘必须按
    `ths_index`(type='N' 概念)过滤 —— 否则 `compute_sector_strength` 的 top10 榜单
    语义会从「强势概念板块」悄悄变成「强势任意板块」,且非概念代码在报告上显示成裸代码。"""
    _seed_index(tmp_path, ["883300.TI", "885362.TI"])
    rows = _day_rows("20260722", codes=("883300.TI", "885362.TI", "700005.TI"))
    stats = cd.update_ths_daily([date(2026, 7, 22)],
                                fetch=lambda td: _Res(_pdf(rows)), parquet_dir=tmp_path)
    got = pl.read_parquet(tmp_path / cd.THS_DAILY_FILE)
    assert stats["rows"] == 2
    assert set(got["ts_code"].to_list()) == {"883300.TI", "885362.TI"}


def test_update_ths_daily_without_index_snapshot_keeps_all_and_warns(tmp_path, caplog):
    """全新环境没有 `ths_index` → 不过滤(有数据好过没数据),但必须喊一声。"""
    with caplog.at_level("WARNING"):
        cd.update_ths_daily([date(2026, 7, 22)],
                            fetch=lambda td: _Res(_pdf(_day_rows("20260722", codes=("700005.TI",)))),
                            parquet_dir=tmp_path)
    assert any("不做概念板块过滤" in r.getMessage() for r in caplog.records)
    assert pl.read_parquet(tmp_path / cd.THS_DAILY_FILE).height == 1


def test_filtered_to_nothing_counts_as_empty_not_failed(tmp_path):
    """过滤后一行不剩 → 计入 `empty`(没数据),不是 `failed`(没查到)。"""
    _seed_index(tmp_path, ["883300.TI"])
    stats = cd.update_ths_daily(
        [date(2026, 7, 22)],
        fetch=lambda td: _Res(_pdf(_day_rows("20260722", codes=("700005.TI",)))),
        parquet_dir=tmp_path)
    assert stats == {"days": 1, "rows": 0, "empty": 1, "failed": 0}
