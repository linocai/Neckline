"""概念板块数据落地(plan 1.6 / P2 板块年龄因子;本阶段首次拉取)。

数据源:同花顺板块(TuShare 600 档可用,§3.2)——`ths_index`(概念指数列表)/
`ths_daily`(板块指数日线,板块年龄用)/`ths_member`(当前成分,时点快照)。

存储:板块数据体量小(概念指数约 400 个 × ~1600 交易日 ≈ 65 万行),不按阶段 0 全市场
大表的「一日一文件」切,直接落三个扁平 Parquet 到 `data/parquet/`(仍在 data/ 下、
gitignored、Parquet 格式,与阶段 0 存储层一致):
    data/parquet/ths_index.parquet    概念指数元数据
    data/parquet/ths_daily.parquet    概念指数日线(2020-2026)
    data/parquet/ths_member.parquet   当前成分快照(历史成分不可得,见 P2 诚实说明)

限频退避由 tushare_client 内建(600 档 450/分保护)。断点续跑:输出已存在则跳过,
`--force` 重拉。运行:python -m scripts.backfill_concept [--force]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402

from neckline.config import settings, ensure_data_dirs  # noqa: E402
from neckline.data.tushare_client import ts_ths_index, ts_ths_daily, ts_ths_member  # noqa: E402

START = "20200101"
END = "20260722"


def _to_pl(df) -> pl.DataFrame:
    return pl.from_pandas(df) if df is not None and len(df) else pl.DataFrame()


def main(force: bool = False):
    ensure_data_dirs()
    pdir = settings.parquet_dir
    idx_path = pdir / "ths_index.parquet"
    daily_path = pdir / "ths_daily.parquet"
    member_path = pdir / "ths_member.parquet"

    # 1) 概念指数列表
    r = ts_ths_index(exchange="A", type_="N")
    if not r.ok:
        print(f"[FATAL] ths_index 拉取失败:{r.reason}")
        sys.exit(1)
    idx = _to_pl(r.data)
    idx.write_parquet(idx_path)
    codes = idx["ts_code"].to_list()
    print(f"[ths_index] {len(codes)} 个概念指数 → {idx_path.name}")

    # 2) 板块指数日线(逐板块全区间一次拉)
    if daily_path.exists() and not force:
        print(f"[ths_daily] 已存在,跳过(--force 重拉):{daily_path.name}")
    else:
        frames, ok, fail = [], 0, 0
        for i, code in enumerate(codes):
            r = ts_ths_daily(code, START, END)
            if r.ok and r.data is not None and len(r.data):
                frames.append(_to_pl(r.data))
                ok += 1
            else:
                fail += 1
            if (i + 1) % 50 == 0:
                print(f"  ths_daily 进度 {i+1}/{len(codes)}(ok={ok} fail={fail})")
        if frames:
            alld = pl.concat(frames, how="diagonal_relaxed")
            alld = alld.with_columns(
                pl.col("trade_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False)
            ).sort(["ts_code", "trade_date"])
            alld.write_parquet(daily_path)
            print(f"[ths_daily] {alld.height} 行(ok={ok} fail={fail}) → {daily_path.name}")
        else:
            print("[ths_daily] 无数据")

    # 3) 当前成分快照
    if member_path.exists() and not force:
        print(f"[ths_member] 已存在,跳过:{member_path.name}")
    else:
        frames, ok, fail = [], 0, 0
        for i, code in enumerate(codes):
            r = ts_ths_member(code)
            if r.ok and r.data is not None and len(r.data):
                df = _to_pl(r.data)
                if "ts_code" not in df.columns:
                    df = df.with_columns(pl.lit(code).alias("index_code"))
                else:
                    df = df.rename({"ts_code": "index_code"})
                frames.append(df)
                ok += 1
            else:
                fail += 1
            if (i + 1) % 100 == 0:
                print(f"  ths_member 进度 {i+1}/{len(codes)}(ok={ok} fail={fail})")
        if frames:
            allm = pl.concat(frames, how="diagonal_relaxed")
            allm.write_parquet(member_path)
            print(f"[ths_member] {allm.height} 行(ok={ok} fail={fail}) → {member_path.name}")
        else:
            print("[ths_member] 无数据")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
