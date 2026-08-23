#!/usr/bin/env python3
"""显式处理崩溃后遗留的市场方向 ``running`` sidecar。

本工具不在定时链内，也没有隐式数据库目标。操作者必须同时指明 pack、当前
``createdAt`` token 与原因；重试还必须再次逐字确认 pack id，才会产生一次外部调用。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.db import readonly_tables  # noqa: E402
from neckline.facts import direction_llm, direction_store  # noqa: E402
from neckline.facts.store import load_pack  # noqa: E402
from neckline.llm.factory import get_provider  # noqa: E402
from neckline.llm.router import TASK_MARKET_DIRECTION  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("settle", "retry"))
    parser.add_argument("--db", required=True, type=Path, help="已核对的目标 SQLite 路径")
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--expected-created-at", required=True, help="当前 running 行的 createdAt")
    parser.add_argument("--reason", required=True, help="本次人工处置原因")
    parser.add_argument("--report-date", help="retry 必填，YYYYMMDD；只用于本次用量账")
    parser.add_argument("--parquet-dir", type=Path, help="retry 读取事实包的 parquet 根目录")
    parser.add_argument(
        "--authorize-external-call",
        help="retry 必填，必须逐字等于 --pack-id；代表明确授权一次 LLM/Tavily 调用",
    )
    return parser


def _load_claimed_pack(pack_id: str, *, db_path: Path, parquet_dir: Path | None):
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(
            "SELECT trade_date, pack_version FROM fact_packs WHERE pack_id=?", (pack_id,)
        ).fetchone()
    if row is None:
        raise LookupError(f"找不到事实包 {pack_id}")
    trade_date = datetime.strptime(str(row[0]), "%Y%m%d").date()
    pack = load_pack(
        trade_date, pack_version=str(row[1]), parquet_dir=parquet_dir, db_path=db_path)
    if pack.pack_id != pack_id:
        raise RuntimeError(f"事实包身份不一致：期望 {pack_id}，实际 {pack.pack_id}")
    return pack


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.db.is_file():
        print(f"目标数据库不存在：{args.db}", file=sys.stderr)
        return 2

    current = direction_store.load(args.pack_id, db_path=args.db)
    if current is None:
        print(f"找不到方向 sidecar：{args.pack_id}", file=sys.stderr)
        return 2
    if current["state"] != direction_store.RUNNING_STATE:
        print(f"当前状态不是 running：{current['state']}", file=sys.stderr)
        return 2
    if current["createdAt"] != args.expected_created_at:
        print("createdAt 已变化，拒绝处置；请重新读取当前行后再决定。", file=sys.stderr)
        return 2

    if args.action == "settle":
        changed, row = direction_store.settle_running(
            pack_id=args.pack_id,
            expected_created_at=args.expected_created_at,
            reason=args.reason,
            db_path=args.db,
        )
        if not changed:
            print("处置失败：状态或 token 已被其他进程改变。", file=sys.stderr)
            return 3
        print(f"已人工结案：{row['packId']} → {row['state']}")
        return 0

    if args.authorize_external_call != args.pack_id:
        print("retry 必须用 --authorize-external-call 逐字确认 pack id。", file=sys.stderr)
        return 2
    if not args.report_date:
        print("retry 必须填写 --report-date YYYYMMDD。", file=sys.stderr)
        return 2
    try:
        report_date = datetime.strptime(args.report_date, "%Y%m%d").date()
        pack = _load_claimed_pack(
            args.pack_id, db_path=args.db, parquet_dir=args.parquet_dir)
    except (ValueError, LookupError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    provider = get_provider(TASK_MARKET_DIRECTION, db_path=args.db)
    if provider is None:
        print("当前未配置可用的市场方向模型与 Tavily；没有接管 claim，也没有产生费用。", file=sys.stderr)
        return 2
    changed, row = direction_llm.retry_once(
        pack,
        provider=provider,
        expected_created_at=args.expected_created_at,
        reason=args.reason,
        report_date=report_date,
        db_path=args.db,
    )
    if not changed:
        print("重试失败：状态或 token 已被其他进程改变；没有发起外部调用。", file=sys.stderr)
        return 3
    print(f"人工重试完成：{row['packId']} → {row['state']}")
    return 0 if row["state"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
