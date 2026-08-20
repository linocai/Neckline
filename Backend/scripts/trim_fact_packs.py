#!/usr/bin/env python3
"""事实包保留策略:滚动裁剪 parquet(PROJECT_PLAN §3.2 / §5.3)。

生产滚动保留 **250 个交易日**(`facts.store.RETENTION_PACKS`)的 `fact_pack` parquet;
更早的删掉。250 > `MAX_LOOKBACK_PACKS`(120) 有充足余量。

🔴 **只删 parquet,⛔ 绝不删 `fact_packs` 行** —— 审计要活得比数据久:parquet 被裁剪
之后,「那次跑用的是哪版包、指纹是多少、缺口是什么」仍然查得到。全历史包由 whynotme
侧自建(§3.2)。

用法:
    python scripts/trim_fact_packs.py --dry-run
    python scripts/trim_fact_packs.py
    python scripts/trim_fact_packs.py --keep 300
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import ensure_data_dirs  # noqa: E402
from neckline.db import init_schema  # noqa: E402
from neckline.facts import store as fact_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("trim_fact_packs")


def main() -> int:
    ap = argparse.ArgumentParser(description="事实包 parquet 滚动裁剪")
    ap.add_argument("--keep", type=int, default=fact_store.RETENTION_PACKS,
                    help=f"保留最近多少个已冻结交易日(默认 {fact_store.RETENTION_PACKS})")
    ap.add_argument("--dry-run", action="store_true", help="只列出将删的日子,不动文件")
    args = ap.parse_args()

    ensure_data_dirs()
    init_schema()
    removed = fact_store.trim_parquet(keep=args.keep, dry_run=args.dry_run)
    if not removed:
        logger.info("无需裁剪(已冻结的交易日数 <= %d,或更早的 parquet 已不在)", args.keep)
        return 0
    logger.info("%s %d 个交易日的 parquet:%s%s",
                "将删" if args.dry_run else "已删", len(removed),
                ", ".join(str(d) for d in removed[:10]),
                " …" if len(removed) > 10 else "")
    logger.info("⚠ `fact_packs` 清单行**原样保留**(审计要活得比数据久)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
