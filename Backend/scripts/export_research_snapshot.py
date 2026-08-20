#!/usr/bin/env python3
"""给 whynotme 离线实验室导出一份**一致性快照**(V2.5.0 S13,PROJECT_PLAN §5.13)。

导出两样:

1. **SQLite 一致性快照**(`sqlite3.backup`,既有能力)—— 含 `sw_industry_classify` /
   `sw_industry_member` / `sw_industry_daily` / `fact_packs` / `k9_*` 全部表。
2. **事实包 parquet 目录**(`--include-fact-packs --start --end`,S13 新增)——
   把 `data/parquet/fact_pack/` 的指定区间**逐字节拷**出去,**保持
   `year=YYYY/YYYYMMDD.parquet` 分区布局不变**,附**逐日 sha256** 的 manifest。

🔴 **为什么必须逐字节相同**(§5.13 逐字):标定要跑在与生产**完全一样**的事实包上,
否则「联合通过率」这个数没有意义 —— 一边算出来的门槛拿到另一边就不是同一件事。
本脚本因此 `shutil.copy2` 原文件,⛔ 不重新写 parquet(重写会换压缩块与行组边界,
sha256 立刻对不上,而数据看起来一模一样 —— 这是最难发现的一类漂移)。

🔴 **manifest 是这份快照的身份证**:`packVersion`(事实层当前口径版本)、区间、
生成时间、**Neckline 版本**、逐文件 sha256。缺任何一项就没法回答「那次标定跑在
哪一版事实包上」。⛔ 不许拿 "unknown" 之类占位值糊过去 —— 读不出版本就该当场失败。

⛔ **本脚本不写 whynotme 的任何目录**:目的地由操作者用 `--out` 显式给,
事实包落在 `--out` **同级**的 `fact_pack/` 下。⛔ 也不 import `whynotme`(AGENTS.md)。

用法:

    # 只导 SQLite(老行为,一字未变)
    python scripts/export_research_snapshot.py --out /tmp/snap/neckline.db

    # 连事实包一起导(S13)
    python scripts/export_research_snapshot.py --out /tmp/snap/neckline.db \\
        --include-fact-packs --start 20260101 --end 20260724
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings

#: 事实包 parquet 的表名与分区布局的**唯一源** —— 与 `facts/store.py::PARQUET_TABLE`
#: 和 `market_data.day_file_path` 同一套(守门单测拿它们对拍,⛔ 不在这里另拼一套路径)。
FACT_PACK_TABLE = "fact_pack"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _neckline_version() -> str:
    """服务端版本号。**单一源 = `neckline/api/app.py::VERSION`**(客户端版本治理守门
    `tests/test_client_version_governance.py` 锁的也是它)。⛔ 不在这里另存一份常量,
    ⛔ 读不出也不许退回占位值 —— 那会让 manifest 说谎。"""
    from neckline.api.app import VERSION

    return VERSION


def export_snapshot(
    source: Path, destination: Path, *, force: bool = False,
    parquet_dir: Optional[Path] = None,
) -> dict:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    if destination.exists() and not force:
        raise FileExistsError(f"destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        target_connection = sqlite3.connect(str(temporary))
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()
    temporary.replace(destination)

    manifest = {
        "schemaVersion": 2,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "necklineVersion": _neckline_version(),
        "database": destination.name,
        "sha256": _sha256(destination),
        "bytes": destination.stat().st_size,
        "sourceKind": "neckline-sqlite-backup",
        # ⚠ 记的是**这次真正读的那个** parquet 根 —— 冒烟 / 测试传了 `--parquet-dir`
        # 时它必须跟着变。写死 `settings.parquet_dir` 会让一份跑在临时目录上的
        # manifest 指着生产目录说话(AGENTS.md:测试与冒烟⛔ 不许往工作目录落东西,
        # 也⛔ 不许在产物里冒充工作目录)。
        "parquetReadOnlyPath": str(Path(parquet_dir or settings.parquet_dir).resolve()),
    }
    return manifest


def _day_files(parquet_dir: Path, start: str, end: str) -> List[Path]:
    """`[start, end]`(闭区间,`YYYYMMDD` 串比较)内已有的 `fact_pack` 日分区文件,
    按日期升序。**只 glob 区间覆盖到的年份**(同 `market_data._scan_table` 的纪律:
    全 glob 要打开 1500+ 个 footer,§12 坑 1)。"""
    table_root = parquet_dir / FACT_PACK_TABLE
    if not table_root.is_dir():
        return []
    years = range(int(start[:4]), int(end[:4]) + 1)
    out: List[Path] = []
    for y in years:
        year_dir = table_root / f"year={y}"
        if not year_dir.is_dir():
            continue
        for f in year_dir.glob("*.parquet"):
            if start <= f.stem <= end:
                out.append(f)
    return sorted(out, key=lambda p: p.stem)


def export_fact_packs(
    destination_root: Path, start: str, end: str, *,
    parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None,
) -> dict:
    """把 `[start, end]` 的事实包 parquet **原样拷**到 `destination_root/fact_pack/`,
    **保持 `year=YYYY/` 分区布局**(whynotme 侧可以用与生产完全相同的路径约定读)。

    返回 manifest 片段:区间、`packVersion`、逐日 `{date, path, sha256, bytes}`、
    以及**区间内缺哪几天**(`missingDates`)。

    🔴 **缺日必须说出口**:标定方拿到 118 天而不是 120 天,与拿到 120 天是两件事。
    ⛔ 不静默少给。⚠ 「缺」的判据是**清单**(`fact_packs` 表里有那一天的行、parquet
    却不在):那才是真缺口;清单里就没有的日子是**那天根本没冻结过**,两者分开报。
    """
    from neckline.facts.pack import PACK_VERSION
    from neckline.facts.store import list_packs

    parquet_dir = Path(parquet_dir or settings.parquet_dir)
    out_dir = destination_root / FACT_PACK_TABLE
    files = _day_files(parquet_dir, start, end)

    copied: List[Dict[str, object]] = []
    for src in files:
        rel = Path(f"year={src.parent.name.split('=')[-1]}") / src.name
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # 🔴 逐字节拷贝(⛔ 不重写 parquet):见模块头。
        shutil.copy2(src, dst)
        copied.append({
            "date": src.stem,
            "path": str(Path(FACT_PACK_TABLE) / rel),
            "sha256": _sha256(dst),
            "bytes": dst.stat().st_size,
        })

    have = {c["date"] for c in copied}
    frozen = {row[0] for row in list_packs(pack_version=PACK_VERSION, db_path=db_path)
              if start <= row[0] <= end}
    return {
        "packVersion": PACK_VERSION,
        "start": start,
        "end": end,
        "fileCount": len(copied),
        "files": copied,
        # 清单里有、parquet 却拷不到 —— **真缺口**(多半是滚动裁剪已经删了那几天)。
        "missingDates": sorted(frozen - have),
        # 拷到了、清单里却没有 —— 孤儿文件,同样要说出口(⛔ 不静默带走)。
        "orphanDates": sorted(have - frozen),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=settings.db_path,
                        help="source SQLite database (default: configured Neckline DB)")
    parser.add_argument("--out", type=Path, required=True,
                        help="destination snapshot path")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing destination snapshot")
    parser.add_argument("--include-fact-packs", action="store_true",
                        help="also copy the fact_pack parquet partitions (needs --start/--end)")
    parser.add_argument("--start", default="", help="fact pack range start, YYYYMMDD")
    parser.add_argument("--end", default="", help="fact pack range end, YYYYMMDD")
    parser.add_argument("--parquet-dir", type=Path, default=None,
                        help="override the parquet root (tests / read-only snapshots)")
    args = parser.parse_args()

    if args.include_fact_packs:
        # ⛔ 不给区间挑一个默认值:「导哪一段」是操作者的决定,替他选一段等于
        # 让标定跑在一段他没打算用的数据上。
        for name, value in (("--start", args.start), ("--end", args.end)):
            if not (len(value) == 8 and value.isdigit()):
                parser.error(f"--include-fact-packs 需要 {name}=YYYYMMDD(收到 {value!r})")
        if args.start > args.end:
            parser.error(f"--start({args.start}) 不能晚于 --end({args.end})")

    manifest = export_snapshot(args.source, args.out, force=args.force,
                               parquet_dir=args.parquet_dir)
    if args.include_fact_packs:
        manifest["factPacks"] = export_fact_packs(
            args.out.expanduser().resolve().parent, args.start, args.end,
            parquet_dir=args.parquet_dir, db_path=args.source,
        )

    manifest_path = args.out.expanduser().resolve()
    manifest_path = manifest_path.with_suffix(manifest_path.suffix + ".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
