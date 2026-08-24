#!/usr/bin/env python3
"""晚间编排链 CLI(V2.5.0 S7,PROJECT_PLAN §9.3)。

**段序定死**(改顺序前先读 `neckline/report/evening.py` 模块头):

    16:05 拉数(`scripts/daily_update.py`,**不在本脚本内**)
      → facts → direction → k9 → explain → playbook → report

**每段各自包保险丝**:任一段异常只记 WARNING、在报告里如实标该段缺席,链继续往下走。
⛔ 绝不因为某段失败而当日无报告 —— 唯一例外是最后那段报告本身炸了(那才是真的没有
报告,退出码非零)。

用法:
    python scripts/evening.py                         # 最近一个交易日,全链
    python scripts/evening.py 20260724                # 指定交易日
    python scripts/evening.py --segments facts,k9     # 只跑其中几段(三个 oneshot 的接缝)
    python scripts/evening.py --k9-params config/k9-params.v1.json
    python scripts/evening.py --no-save               # 不落 `k9_reports`(调试)
    python scripts/evening.py --notify                # 落库后触发 APNs
    python scripts/evening.py --scheduled             # 仅给 systemd:工作日=当天,周日=前一周五

🔴 **双日期契约⛔ 不许退化**(LRN-20260816-001,§12 坑 9):
`report_date` 管**标题 / 推送 / 可见身份**;`trade_date` 管 **EOD 读数 / 清单 / 预案 /
审计键**。周日 19:00 那一槽:`report_date=周日`、`trade_date=紧邻上一周五`;
**该周五休市则安全跳过**(⛔ 不回退到周四重发一份旧报告);
**同日已生成则整链跳过**(⛔ 不重复推送)。
这三条各有一条回归守门,见 `tests/test_weekend_report_schedule.py`。

⛔ **无默认参数包路径**(裁定 5):没传 `--k9-params` 就是「参数未配置」,报告首行会是
「今天没跑成 · 参数未配置」——**这是设计行为,不是故障**(§9.5)。市场事实与覆盖率成绩线
照常呈现；方向背景由 `facts/direction_llm.py` 读取冻结事实包生成，失败只显示
“方向解读暂未生成”，不影响机械清单。
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import is_trading_day, official_is_trading_day, prev_trading_day  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.db import readonly_connection  # noqa: E402
from neckline.report.evening import (  # noqa: E402
    CHAIN_SEGMENTS,
    STATUS_FAILED,
    run_evening_chain,
)
from neckline.report.store import K9_TABLE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("evening")

def _reports_dir(db_path: Path | None) -> Path:
    """报告 markdown 的落盘目录。

    🔴 **隔离跑必须连 markdown 一起隔离**:`--db` 指到别处时,`data_dir` 仍指向真实
    工作目录 —— 冒烟跑会把一份合成数据的报告写进真实 `Backend/data/reports/`
    (本片冒烟当场踩到)。⛔ 测试与冒烟不许往工作目录落任何东西(AGENTS.md)。
    """
    if db_path is not None:
        return db_path.resolve().parent / "reports"
    return settings.data_dir / "reports"


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
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (K9_TABLE,)
            ).fetchone() is None:
                return False
            row = conn.execute(
                f"SELECT generated_at FROM {K9_TABLE} WHERE trade_date=?",
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
        help="YYYYMMDD;报告标题/推送日期。周日定时自动取周日当天;行情仍读取 trade_date",
    )
    parser.add_argument("--segments", default=",".join(CHAIN_SEGMENTS),
                        help=f"逗号分隔,取值 {'/'.join(CHAIN_SEGMENTS)};缺省=全链")
    parser.add_argument(
        "--k9-params", default=None,
        help="K9 参数包 JSON 路径(⛔ 无默认路径:不传 = 参数未配置 = 报告「今天没跑成」)",
    )
    parser.add_argument("--no-save", action="store_true", help="不落 k9_reports 表/不写 md")
    parser.add_argument("--notify", action="store_true",
                        help="落库后触发 APNs 报告推送(受 kind=report_ready 开关)")
    parser.add_argument(
        "--scheduled", action="store_true",
        help="仅给 systemd:周一至周四绑定当天,周日绑定前一周五;休市则成功跳过",
    )
    parser.add_argument("--db", default=None, help="隔离库路径(冒烟用;缺省=真实库)")
    parser.add_argument("--parquet-dir", default=None, help="隔离 parquet 目录(冒烟用)")
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
        if official_is_trading_day(trade_date) is not True:
            logger.info("定时槽对应 %s,非交易日;安全跳过,不回退重跑旧报告。", trade_date)
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
        logger.error("报告日期格式错误,必须是 YYYYMMDD。")
        return 2
    if official_is_trading_day(trade_date) is not True:
        logger.error("%s 不是已落库官方交易日,无报告可生成。", trade_date)
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
            "定时槽对应 %s,但该报告已在 %s 生成;整条链安全跳过,避免重复报告与推送。",
            trade_date, scheduled_today,
        )
        return 0

    k9_params_path = Path(args.k9_params) if args.k9_params else None
    if k9_params_path is not None and not k9_params_path.exists():
        logger.error("参数包路径不存在:%s", k9_params_path)
        return 2

    logger.info(
        "晚间链 报告日=%s 行情截止=%s:段 %s(params=%s,save=%s)",
        report_date, trade_date, segments, k9_params_path, not args.no_save)
    try:
        res = run_evening_chain(
            trade_date, report_date=report_date, segments=segments,
            k9_params_path=k9_params_path, db_path=db_path, parquet_dir=parquet_dir,
            save=not args.no_save,
        )
    except RuntimeError as e:
        logger.error("晚间链失败:%s", e)
        return 1

    for seg in CHAIN_SEGMENTS:
        logger.info("  [%s] %s %s", seg, res.status.get(seg), res.stats.get(seg, ""))
    for n in res.notes:
        logger.warning("  ⚠ %s", n)

    if res.bundle is not None and not args.no_save:
        reports_dir = _reports_dir(db_path)
        reports_dir.mkdir(parents=True, exist_ok=True)
        out_path = reports_dir / f"{report_date.strftime('%Y%m%d')}.md"
        out_path.write_text(res.bundle.markdown, encoding="utf-8")
        logger.info("报告已写入 %s,并已落库 SQLite `k9_reports` 表。", out_path)
        if args.notify:
            _notify(res.bundle, db_path=db_path)

    # **失败段不吞成功退出码**:报告出了但某段炸了 → 退出码 3,systemd 那侧看得见
    # (`ExecMainStatus` 才是部署验收的判据,不是「timer 跑过了」)。
    if any(v == STATUS_FAILED for v in res.status.values()):
        logger.error("有段落失败(见上方 WARNING),退出码 3。")
        return 3
    return 0


#: 报告三态 → APNs 文案里的 `selection_state`。**全映射**,⛔ 无 fallback ——
#: 「今天没跑成」的日子推一句「选股已就绪」,是这条链上最容易犯的一次谎。
_PUSH_STATE = {
    "has_list": None,           # 正常文案
    "empty": None,              # 跑通了、结果为空 —— 照常文案,空清单可以被信任
    "not_run": "unavailable",   # 系统没工作 → 明确说「选股未完成」
}


def _notify(bundle, *, db_path: Path | None = None) -> None:
    """APNs 推送 —— **绝不因推送失败让链失败**(同既有体例)。"""
    try:
        from neckline.api.notify import push_report_ready

        state = bundle.state.value
        if state not in _PUSH_STATE:
            raise AssertionError(f"报告三态里冒出了 {state!r} —— 推送文案是全映射")
        outcome = push_report_ready(
            bundle.report_date.strftime("%Y-%m-%d"),
            data_date_disp=bundle.trade_date.strftime("%Y-%m-%d"),
            selection_state=_PUSH_STATE[state],
            db_path=db_path,
        )
        logger.info("APNs 报告推送:sent=%d failed=%d%s", outcome.sent, outcome.failed,
                    f" skipped={outcome.skipped_reason}" if outcome.skipped_reason else "")
    except Exception:  # noqa: BLE001
        logger.warning("APNs 报告推送异常(已吞,不影响报告落库)", exc_info=True)


if __name__ == "__main__":
    raise SystemExit(main())
