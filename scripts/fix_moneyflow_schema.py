#!/usr/bin/env python3
"""一次性修缮 `moneyflow_dc` 历史分区 TuShare 类型漂移(K3 · B0.1)。

**背景(k2_report §0.3 记载,阻断 K3 B3.1 资金面纳入)**:`moneyflow_dc` 的 12 个
数值列在部分历史分区落成 `String`,与主体分区的 `Float64` 冲突,`pl.scan_parquet`
整表读取直接:

    SchemaError: data type mismatch for column pct_change: incoming Float64 != target String

实测坏分区分两类:① 2020-2023 早期大量 **0 行空文件**(空列→pandas object→polars
String);② **2026-07-20/21/22 三个含真数据的分区**被写成 String(daily_update 落盘
时 `_align_to_table_schema` 依赖的整表 scan 已被①毒化、取不到干净 target schema,
遂退化为原样落 String——这三天正是 v1 上线首日毒化 Parquet 的同类现象)。

**修法(承 v1 上线首日 `_align_to_table_schema` 思路,但显式 canonical=Float64,
不依赖整表 scan 取 target——整表 scan 本身就是坏的,`_align_to_table_schema` 会连带
失败)**:逐分区文件读入(不整表 scan)→ 目标 12 列 `cast(Float64, strict=False)`
(空串 / 非法值→null)→ 原地重写同路径。**幂等**:已是 Float64 的列跳过不动,已修
文件二次运行零改动。

修缮后:整表 `scan_parquet` / `scan_table_range` 全窗可读,且 `write_table_day` 的
`_align_to_table_schema` 防线恢复(整表 scan 取得干净 Float64 target),后续 daily_update
的类型漂移会被自动 cast 回 Float64。

用法:
    python scripts/fix_moneyflow_schema.py            # 修 data/parquet/moneyflow_dc
    python scripts/fix_moneyflow_schema.py --dry-run  # 只报告不落盘

本脚本核心 `repair_float_columns` 可导入,mock 单测见 tests/test_fix_moneyflow_schema.py
(承阶段 0 教训:改脚本级落盘代码先补一层 mock 单测)。
"""

from __future__ import annotations

import argparse
import glob
import logging
import sys
from pathlib import Path
from typing import Dict, List, Sequence

import polars as pl

logger = logging.getLogger("fix_moneyflow_schema")

# moneyflow_dc 的 12 个数值列(canonical dtype = Float64)。非数值列
# (trade_date=Date, ts_code/name=String)不在此列、绝不动。
MONEYFLOW_FLOAT_COLS: List[str] = [
    "pct_change",
    "close",
    "net_amount",
    "net_amount_rate",
    "buy_elg_amount",
    "buy_elg_amount_rate",
    "buy_lg_amount",
    "buy_lg_amount_rate",
    "buy_md_amount",
    "buy_md_amount_rate",
    "buy_sm_amount",
    "buy_sm_amount_rate",
]


def repair_float_columns(
    table_root: Path,
    float_cols: Sequence[str] = MONEYFLOW_FLOAT_COLS,
    dry_run: bool = False,
) -> Dict[str, object]:
    """把 `table_root/year=*/*.parquet` 各分区里 `float_cols` 中**非 Float64** 的列
    显式 cast 到 Float64(strict=False,空串/非法值→null),原地重写。

    - **逐文件处理**(不整表 scan——整表 scan 正是坏的),显式 canonical=Float64,不
      依赖 `_align_to_table_schema` 的自动对齐(那条路径依赖整表 scan 取 target,会连带
      失败)。
    - **幂等**:一个文件若 `float_cols` 全部已是 Float64,直接跳过、不重写。
    - 非 `float_cols` 的列(trade_date / ts_code / name)原样保留,列顺序不变。

    返回:{"scanned": 文件数, "fixed": 修缮文件数, "fixed_files": [(path, [列名...]), ...]}。
    """
    pattern = str(table_root / "year=*" / "*.parquet")
    files = sorted(glob.glob(pattern))
    scanned = 0
    fixed_files: List = []
    for f in files:
        scanned += 1
        schema = pl.read_parquet_schema(f)
        need = [c for c in float_cols if c in schema and schema[c] != pl.Float64]
        if not need:
            continue
        df = pl.read_parquet(f)
        df = df.with_columns([pl.col(c).cast(pl.Float64, strict=False) for c in need])
        if not dry_run:
            df.write_parquet(f)
        fixed_files.append((f, need))
        logger.info("修缮 %s:%d 列 %s", Path(f).name, len(need), need)
    return {"scanned": scanned, "fixed": len(fixed_files), "fixed_files": fixed_files}


def _default_table_root() -> Path:
    # 延迟依赖 neckline.config,让脚本核心函数在纯 mock 目录下也可单测(不碰真 settings)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from neckline.config import settings

    return settings.parquet_dir / "moneyflow_dc"


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="修缮 moneyflow_dc 历史分区类型漂移")
    p.add_argument("--dry-run", action="store_true", help="只报告不落盘")
    p.add_argument("--table-root", default=None, help="覆盖表根目录(默认 settings.parquet_dir/moneyflow_dc)")
    args = p.parse_args(argv)

    root = Path(args.table_root) if args.table_root else _default_table_root()
    logger.info("表根目录:%s（dry_run=%s）", root, args.dry_run)
    res = repair_float_columns(root, dry_run=args.dry_run)
    logger.info("扫描 %d 文件,%s %d 文件", res["scanned"], "待修" if args.dry_run else "已修", res["fixed"])

    # 修缮后自检:整表 scan 应不再 SchemaError
    if not args.dry_run and res["fixed"] > 0:
        try:
            n = pl.scan_parquet(str(root / "year=*" / "*.parquet")).select(pl.len()).collect().item()
            logger.info("整表 scan 自检通过,总行数=%d", n)
        except Exception as e:  # noqa: BLE001
            logger.error("整表 scan 自检仍失败:%s", e)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
