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
    python scripts/evening.py --notify              # 落库后触发 APNs
    python scripts/evening.py --scheduled           # 仅给 systemd:工作日=当天,周日=前一周五

⚠ **`--segments` 只挑跑哪几段,不改顺序**:传进去的集合会按 `CHAIN_SEGMENTS` 重排。
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import is_trading_day, prev_trading_day  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.db import readonly_connection  # noqa: E402
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


def _today() -> date:
    """Clock seam for the scheduled-date contract and its regression tests."""
    return date.today()


def _scheduled_trade_date(today: date) -> date:
    """Bind a timer slot to its intended session instead of a stale fallback.

    Mon–Thu slots target that calendar day.  The Sunday slot targets the
    immediately preceding Friday so weekend news can be included.  The caller
    still checks ``is_trading_day`` and cleanly skips a holiday Friday; it must
    never fall back to Thursday and regenerate an older report.
    """
    return today - timedelta(days=2) if today.weekday() == 6 else today


def _report_generated_on_local_day(
    trade_date: date,
    local_day: date,
    db_path: Path | None = None,
) -> bool:
    """Read-only idempotency guard for a scheduled slot.

    A manual Sunday backfill may already have regenerated Friday's report.  In
    that case the 19:00 slot must not repeat the expensive chain or APNs.  The
    guard checks only the frozen report timestamp and never initializes schema.
    """
    try:
        with readonly_connection(db_path) as conn:
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reports'"
            ).fetchone() is None:
                return False
            row = conn.execute(
                "SELECT generated_at FROM reports WHERE trade_date=?",
                (trade_date.strftime("%Y%m%d"),),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return False
    if row is None or not row[0]:
        return False
    try:
        generated = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return generated.astimezone(ZoneInfo("Asia/Shanghai")).date() == local_day


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trade_date", nargs="?", default=None,
                        help="YYYYMMDD;缺省=最近一个交易日")
    parser.add_argument(
        "--report-date", default=None,
        help="YYYYMMDD;报告标题/推送日期。周日定时自动取周日当天；行情仍读取 trade_date",
    )
    parser.add_argument("--segments", default=",".join(CHAIN_SEGMENTS),
                        help=f"逗号分隔,取值 {'/'.join(CHAIN_SEGMENTS)};缺省=全链")
    parser.add_argument("--no-llm", action="store_true", help="纯机械路径,零 LLM 调用")
    parser.add_argument("--no-save", action="store_true", help="不落 reports 表/不写 md")
    parser.add_argument("--notify", action="store_true",
                        help="落库后触发 APNs 报告推送(受 kind=report_ready 开关)")
    parser.add_argument(
        "--scheduled", action="store_true",
        help="仅给 systemd:周一至周四绑定当天,周日绑定前一周五;休市则成功跳过",
    )
    parser.add_argument("--db", default=None, help="隔离库路径(冒烟用;缺省=真实库)")
    parser.add_argument("--parquet-dir", default=None, help="隔离 parquet 目录(冒烟用)")
    parser.add_argument(
        "--direction-pipeline-config", default=None,
        help="V2.4.2 方向流水线 JSON 配置文件；未提供或不完整时选股如实显示不可用，不回退旧前20路径",
    )
    parser.add_argument(
        "--observe-selection-cost", action="store_true",
        help="仅经用户明确授权的一次性观察模式：Token/墙钟只记账不截断；方向与补位上限仍强制；不会自动启用",
    )
    args = parser.parse_args()

    ensure_data_dirs()
    if (args.trade_date or args.report_date) and args.scheduled:
        logger.error("显式交易日与 --scheduled 不能同时使用。")
        return 2
    scheduled_today = None
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y%m%d").date()
    elif args.scheduled:
        scheduled_today = _today()
        trade_date = _scheduled_trade_date(scheduled_today)
        if not is_trading_day(trade_date):
            logger.info("定时槽对应 %s，非交易日；安全跳过，不回退重跑旧报告。", trade_date)
            return 0
    else:
        trade_date = _default_trade_date()
    try:
        report_date = (
            scheduled_today
            if scheduled_today is not None
            else datetime.strptime(args.report_date, "%Y%m%d").date()
            if args.report_date
            else trade_date
        )
    except ValueError:
        logger.error("报告日期格式错误，必须是 YYYYMMDD。")
        return 2
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
    if (
        args.scheduled
        and scheduled_today is not None
        and _report_generated_on_local_day(trade_date, scheduled_today, db_path)
    ):
        logger.info(
            "定时槽对应 %s，但该报告已在 %s 生成；整条链安全跳过，避免重复报告与推送。",
            trade_date,
            scheduled_today,
        )
        return 0
    direction_pipeline_config = None
    if args.direction_pipeline_config:
        try:
            loaded = json.loads(Path(args.direction_pipeline_config).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.error("方向流水线配置读取失败:%s", exc)
            return 2
        if not isinstance(loaded, dict):
            logger.error("方向流水线配置必须是 JSON object。")
            return 2
        direction_pipeline_config = loaded
    logger.info("晚间链 报告日=%s 行情截止=%s:段 %s(llm=%s,save=%s,selection_budget_mode=%s)",
                report_date, trade_date, segments,
                not args.no_llm, not args.no_save,
                "observe_only" if args.observe_selection_cost else "enforce")
    try:
        res = run_evening_chain(
            trade_date, report_date=report_date, segments=segments,
            db_path=db_path, parquet_dir=parquet_dir,
            use_llm=not args.no_llm, save=not args.no_save,
            # Passing explicit None is intentional: V2.4.2 cannot silently
            # fall back to the historical first-20 aggregate route.
            direction_pipeline_config=direction_pipeline_config,
            selection_budget_mode=("observe_only" if args.observe_selection_cost else "enforce"),
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
        out_path = REPORTS_DIR / f"{report_date.strftime('%Y%m%d')}.md"
        out_path.write_text(res.bundle.markdown, encoding="utf-8")
        logger.info("报告已写入 %s,并已落库 SQLite `reports` 表。", out_path)
        if args.notify:
            _notify(report_date, res.bundle)

    # **失败段不吞成功退出码**:报告出了但某段炸了 → 退出码 3,systemd 那侧看得见
    # (`ExecMainStatus` 才是部署验收的判据,不是"timer 跑过了")。
    if any(v == STATUS_FAILED for v in res.status.values()):
        logger.error("有段落失败(见上方 WARNING),退出码 3。")
        return 3
    return 0


def _notify(report_date: date, bundle) -> None:
    """APNs 推送(plan 4B.5)——**绝不因推送失败让链失败**,同 `scripts/report.py` 体例。"""
    try:
        from neckline.api.notify import push_report_ready

        selection_state = getattr(getattr(bundle, "basket_daily", None), "selection_state", None)
        outcome = push_report_ready(
            report_date.strftime("%Y-%m-%d"),
            data_date_disp=bundle.trade_date.strftime("%Y-%m-%d"),
            selection_state=selection_state,
        )
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
