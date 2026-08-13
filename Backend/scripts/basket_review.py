#!/usr/bin/env python3
"""盘后复盘 CLI(plan §五 V2-⑨-A / ⑨-B)。D+1 收盘后回答「D0 那份篮子判断,今天
哪里对了、哪里错了」,九项机械判 + 可选 LLM 解释,落 `basket_review_daily`。

**谁在生产上调它(⚠ Plan 未定死,如实登记)**:⑭-A 的 16:35 报告链会在
`basket_verify.run_eod_verification(...)` **之后**调 `review_day(...)`;⑭ 落地之前
用本脚本手动或挂 timer 驱动。⚠ **顺序要紧**:验证那一拍没跑过的话,机械判第 ⑦ 项
(验证与证伪时点)只会看到盘中暂态、甚至 `not_evaluated`。

用法::

    python scripts/basket_review.py                       # 今天(= D+1)
    python scripts/basket_review.py --date 20260724
    python scripts/basket_review.py --from 20260721 --to 20260725
    python scripts/basket_review.py --llm                 # 带 LLM 解释(需已配 provider)
    python scripts/basket_review.py show --date 20260724  # 只看已落库的复盘,不写库

**只写 `basket_review_daily` 一张表**(每日一行幂等,`INSERT OR IGNORE`);
不推送、不碰持仓、不改任何纪律判定。**只记录,不因单日失败改策略**(蓝图 4.9)。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import trading_days_between  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.llm.budget import BudgetLedger  # noqa: E402
from neckline.review import basket_review as br  # noqa: E402
from neckline.review import basket_review_store as store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("basket_review")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def resolve_days(args: argparse.Namespace) -> List[date]:
    if args.date_from or args.date_to:
        lo = _parse_date(args.date_from) if args.date_from else _parse_date(args.date_to)
        hi = _parse_date(args.date_to) if args.date_to else _parse_date(args.date_from)
        return trading_days_between(lo, hi)
    if args.date:
        return [_parse_date(args.date)]
    return [date.today()]


def _show(day: date, db_path: Optional[Path]) -> int:
    rows = store.list_reviews(date_from=day, date_to=day, db_path=db_path)
    if not rows:
        logger.info("%s 没有已落库的复盘。", day)
        return 0
    for r in rows:
        meta = r.mech.get("meta") or {}
        align = (r.mech.get("member_alignment") or {}).get("alignment")
        state = (r.mech.get("verification_timing") or {}).get("state")
        out = (r.mech.get("tier_vs_outcome") or {}).get("basket_ret_median")
        logger.info(
            "T%s %s(basket_id=%d,%s):同向率 %s,验证 %s,篮子收益中位 %s;"
            "LLM %s;包 %s / 条件集 %s",
            meta.get("tier"), meta.get("name"), r.basket_id, r.depth,
            "—" if align is None else f"{align:.0%}", state,
            "—" if out is None else f"{out:+.2%}",
            "有" if r.llm_text else f"缺({r.llm_skip_reason})",
            r.pack_version, r.ruleset_version,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", nargs="?", default="run", choices=["run", "show"])
    parser.add_argument("--date", help="被复盘的那个交易日 YYYYMMDD(= D+1,缺省今天)")
    parser.add_argument("--from", dest="date_from", help="区间起 YYYYMMDD(补算用)")
    parser.add_argument("--to", dest="date_to", help="区间止 YYYYMMDD")
    parser.add_argument("--llm", action="store_true", help="带 LLM 解释(需已配 provider)")
    parser.add_argument("--dry-run", action="store_true", help="只算不落库")
    parser.add_argument("--dump", help="把 mech_json 逐篮写到这个目录(排查用)")
    parser.add_argument("--db", dest="db", help="SQLite 路径(缺省 settings.db_path)")
    parser.add_argument("--parquet-dir", dest="parquet_dir", help="parquet 根目录(缺省 settings)")
    args = parser.parse_args()

    ensure_data_dirs()
    db_path = Path(args.db) if args.db else settings.db_path
    parquet_dir = Path(args.parquet_dir) if args.parquet_dir else None

    days = resolve_days(args)
    if args.cmd == "show":
        return _show(days[-1], db_path)

    provider = None
    if args.llm:
        from neckline.llm.factory import get_provider

        provider = get_provider(br.REVIEW_TASK, db_path=db_path)
        if provider is None:
            logger.warning("未解析到 %s 的 provider —— 本次只出机械判,LLM 段如实标缺席。",
                           br.REVIEW_TASK)

    for day in days:
        res = br.review_day(
            day, db_path=db_path, parquet_dir=parquet_dir,
            use_llm=bool(args.llm), provider=provider, ledger=BudgetLedger(),
            persist=not args.dry_run,
        )
        if not res.reviews:
            logger.info("%s(D0=%s):无可复盘的篮子。%s", day, res.d0, "; ".join(res.notes))
            continue
        logger.info(
            "%s(D0=%s):复盘 %d 篮(full %d / brief %d);落库 新增 %d / 已存在 %d;"
            "LLM 成功 %d 篮%s",
            day, res.d0, len(res.reviews),
            sum(1 for r in res.reviews if r.depth == br.DEPTH_FULL),
            sum(1 for r in res.reviews if r.depth == br.DEPTH_BRIEF),
            res.rows_inserted, res.rows_existing, res.llm_called,
            f",按降级次序丢弃 {res.llm_dropped}" if res.llm_dropped else "",
        )
        for note in res.notes:
            logger.warning("  ⚠ %s", note)
        if args.dump:
            out_dir = Path(args.dump)
            out_dir.mkdir(parents=True, exist_ok=True)
            for r in res.reviews:
                p = out_dir / f"mech_{day.strftime('%Y%m%d')}_{r.basket_key}.json"
                p.write_text(json.dumps(r.mech, ensure_ascii=False, indent=2, sort_keys=True),
                             encoding="utf-8")
            logger.info("  机械判已 dump 到 %s", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
