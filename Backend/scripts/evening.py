#!/usr/bin/env python3
"""16:35 晚间编排链 CLI(plan §五 V2-⑭-A)。

**顺序定死**(改顺序前先读 `neckline/report/pipeline.py` 模块头):

    16:05 拉数(`scripts/daily_update.py`,**不在本脚本内**)
      → ⑧ EOD 篮子验证拍   ← 判**昨日**冻的卡在**今日**收盘的表现,吃刚拉到的今日 EOD
      → ④ 市场扫描层批算 + ④b 行业阶段 + 驱动种子
      → ⑤ 驱动聚合 → ⑥ Tier 定档(事务1)→ ⑦ 卡冻结(事务2)
      → ⑨ 盘后复盘引擎
      → 篮子日报渲染 + 落库

**每段各自包保险丝**:任一段异常只记 WARNING、在报告里如实标该段缺席,链继续往下走。
⛔ 绝不因为某段失败而当日无报告 —— 唯一例外是最后那段报告本身炸了(那才是真的没有报告,
退出码非零)。

用法:
    python scripts/evening.py                       # 最近一个交易日,全链
    python scripts/evening.py 20260724              # 指定交易日
    python scripts/evening.py --segments scan,basket  # 只跑其中几段(⑯-D 分段跑的预演)
    python scripts/evening.py --no-llm              # 纯机械路径(离线冒烟 / 无 key 环境)
    python scripts/evening.py --no-save             # 不落 `reports` 表(调试)
    python scripts/evening.py --notify              # 落库后触发 APNs(16:35 timer 用)

⚠ **`--segments` 只挑跑哪几段,不改顺序**:传进去的集合会按 `CHAIN_SEGMENTS` 重排。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import is_trading_day, prev_trading_day  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.report.evening import (  # noqa: E402
    CHAIN_SEGMENTS,
    STATUS_FAILED,
    run_evening_chain,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("evening")

REPORTS_DIR = settings.data_dir / "reports"


def _default_trade_date() -> date:
    today = date.today()
    return today if is_trading_day(today) else prev_trading_day(today)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trade_date", nargs="?", default=None,
                        help="YYYYMMDD;缺省=最近一个交易日")
    parser.add_argument("--segments", default=",".join(CHAIN_SEGMENTS),
                        help=f"逗号分隔,取值 {'/'.join(CHAIN_SEGMENTS)};缺省=全链")
    parser.add_argument("--no-llm", action="store_true", help="纯机械路径,零 LLM 调用")
    parser.add_argument("--no-save", action="store_true", help="不落 reports 表/不写 md")
    parser.add_argument("--notify", action="store_true",
                        help="落库后触发 APNs 报告推送(受 kind=report_ready 开关)")
    parser.add_argument("--db", default=None, help="隔离库路径(冒烟用;缺省=真实库)")
    parser.add_argument("--parquet-dir", default=None, help="隔离 parquet 目录(冒烟用)")
    args = parser.parse_args()

    ensure_data_dirs()
    trade_date = (datetime.strptime(args.trade_date, "%Y%m%d").date()
                  if args.trade_date else _default_trade_date())
    if not is_trading_day(trade_date):
        logger.error("%s 不是交易日,无报告可生成。", trade_date)
        return 1

    segments = [s.strip() for s in args.segments.split(",") if s.strip()]
    unknown = [s for s in segments if s not in CHAIN_SEGMENTS]
    if unknown:
        logger.error("未知段名 %s;可用:%s", unknown, list(CHAIN_SEGMENTS))
        return 2

    db_path = Path(args.db) if args.db else None
    parquet_dir = Path(args.parquet_dir) if args.parquet_dir else None
    logger.info("晚间链 %s:段 %s(llm=%s,save=%s)", trade_date, segments,
                not args.no_llm, not args.no_save)
    try:
        res = run_evening_chain(
            trade_date, segments=segments, db_path=db_path, parquet_dir=parquet_dir,
            use_llm=not args.no_llm, save=not args.no_save,
        )
    except RuntimeError as e:
        logger.error("晚间链失败:%s", e)
        return 1

    for seg in CHAIN_SEGMENTS:
        logger.info("  [%s] %s %s", seg, res.status.get(seg), res.stats.get(seg, ""))
    for n in res.notes:
        logger.warning("  ⚠ %s", n)

    if res.bundle is not None and not args.no_save:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = REPORTS_DIR / f"{trade_date.strftime('%Y%m%d')}.md"
        out_path.write_text(res.bundle.markdown, encoding="utf-8")
        logger.info("报告已写入 %s,并已落库 SQLite `reports` 表。", out_path)
        if args.notify:
            _notify(trade_date, res.bundle)

    # **失败段不吞成功退出码**:报告出了但某段炸了 → 退出码 3,systemd 那侧看得见
    # (`ExecMainStatus` 才是部署验收的判据,不是"timer 跑过了")。
    if any(v == STATUS_FAILED for v in res.status.values()):
        logger.error("有段落失败(见上方 WARNING),退出码 3。")
        return 3
    return 0


def _notify(trade_date: date, bundle) -> None:
    """APNs 推送(plan 4B.5)——**绝不因推送失败让链失败**,同 `scripts/report.py` 体例。"""
    try:
        from neckline.api.notify import push_report_ready

        outcome = push_report_ready(trade_date.strftime("%Y-%m-%d"))
        logger.info("APNs 报告推送:sent=%d failed=%d%s", outcome.sent, outcome.failed,
                    f" skipped={outcome.skipped_reason}" if outcome.skipped_reason else "")
    except Exception:  # noqa: BLE001
        logger.warning("APNs 报告推送异常(已吞,不影响报告落库)", exc_info=True)
    try:
        from neckline.api.notify import push_holding_alert

        pushed = 0
        for it in bundle.holding_k4_check:
            if not it.has_strong:
                continue
            pushed += push_holding_alert(it.name, it.ts_code, it.strong_price_volume_labels()).sent
        logger.info("APNs 持仓派发警报:sent=%d", pushed)
    except Exception:  # noqa: BLE001
        logger.warning("APNs 持仓派发警报异常(已吞,不影响报告落库)", exc_info=True)


if __name__ == "__main__":
    raise SystemExit(main())
