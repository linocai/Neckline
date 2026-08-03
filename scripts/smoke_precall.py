#!/usr/bin/env python3
"""盘前校准合成竞价快照冒烟(plan v1.1-A;仿 `scripts/smoke_sentinel.py` 姿势)。
**用途**:真实盘中/盘前无法活体验证时(非交易日 / 未到 9:25:30),拿某历史交易日的
**真实日线 open / pre_close / vol** 合成一份「集合竞价快照」,喂给与生产完全同一份
编排代码(`neckline.sentinel.precall.run_precall_tick`)跑,替代活体验证——**这不是
活体验证的替代品**,只是「盘前分支确实按预期工作」的一次有真实数据支撑的冒烟(真正
9:26 真机推达留 v1.1-H)。

**合成方法(诚实标注局限)**:集合竞价快照只需三个字段有真实结构——`open`(= 当日
真实开盘价,即竞价撮合价)、`pre_close`(判低开/证伪基准)、竞价量(合成为当日真实
`vol` 的一个小比例 `AUCTION_VOL_FRAC`,真实竞价量占比因票而异,这里只取一个代表值)。
`price` 取 `open`(竞价阶段 open 即当前价),`high/low` 均设为 `open`(竞价无盘中波动)。
四类判定(高开变形 / 低开证伪 / 竞价量能 / 持仓低开)+ D5 扫描据此在真实 gap 结构上跑。

**不污染真实数据**:整份复制 `data/neckline.db` 到临时副本,持仓/哨兵事件写入全落
临时副本;Parquet(只读)仍用真实 `data/parquet/`;交易日历(只读)用真实库。跑完
清理临时文件(除非 `--keep-db`)。
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402

from neckline.calendar import trading_days_between  # noqa: E402
from neckline.config import settings  # noqa: E402
from neckline.data.market_data import get_market_slice  # noqa: E402
from neckline.report.pipeline import build_report  # noqa: E402
from neckline.sentinel.positions import open_position  # noqa: E402
from neckline.sentinel.precall import run_precall_tick  # noqa: E402
from neckline.sentinel.quotes import Quote  # noqa: E402
from neckline.sentinel.universe import load_watch_universe  # noqa: E402
from neckline.strategy import brain  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smoke_precall")

# 竞价量约占当日总量的比例(**合成近似**,真实因票而异;取 2% 作代表值,足以让
# `judge_auction_volume` 的占比判定跑起来,不代表任何回测结论)。
AUCTION_VOL_FRAC = 0.02


def synthesize_auction_quote(row: dict) -> Quote:
    """从一行真实 `daily` EOD 记录合成一个「集合竞价快照」`Quote`(见模块头注释)。
    daily.vol 单位=手 → Quote.volume 手(不换算);daily.amount 单位=千元 → ×1000 归元。"""
    open_ = float(row["open"] or 0.0)
    return Quote(
        code=row["ts_code"].split(".")[0], name=row["ts_code"], price=round(open_, 2),
        pre_close=float(row["pre_close"] or 0.0), open=open_, high=open_, low=open_,
        volume=float(row["vol"] or 0.0) * AUCTION_VOL_FRAC,
        amount=float(row["amount"] or 0.0) * 1000.0 * AUCTION_VOL_FRAC,
        ts="集合竞价 合成", source="synthetic-auction",
    )


def _daily_rows_lookup(trade_date: date, codes: List[str], parquet_dir: Optional[Path] = None) -> Dict[str, dict]:
    df = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
    if df.is_empty():
        return {}
    df = df.filter(pl.col("ts_code").is_in(codes))
    return {r["ts_code"]: r for r in df.iter_rows(named=True)}


def _seed_d5_position(today: date, code_rows: Dict[str, dict], tmp_db: Path) -> Optional[str]:
    """造一只 buy_date 使 today 恰为 D5 的合成持仓(验证 D5 时间退出扫描)。买入价用
    「买入日的真实收盘价」(模拟已持有若干日),买入日 = today 前 (max_hold−1) 个交易日。"""
    cfg = brain.active_config(db_path=tmp_db)
    max_hold = int(cfg.get("max_hold_days") or 5)
    if not code_rows:
        return None
    code = next(iter(code_rows))
    days = trading_days_between(date(today.year - 1, 1, 1), today)
    if len(days) < max_hold:
        return None
    buy_date = days[-max_hold]   # [buy_date .. today] 闭区间 = max_hold 个交易日 → 今日 D{max_hold}
    buy_rows = _daily_rows_lookup(buy_date, [code], parquet_dir=None)
    buy_price = float(buy_rows[code]["close"]) if code in buy_rows else float(code_rows[code]["pre_close"])
    open_position(code, buy_price, 100, buy_date, note="冒烟合成 D5 持仓", db_path=tmp_db)
    logger.info("合成 D5 持仓:%s 买入价%.2f 买入日%s(今日应为 D%d)", code, buy_price, buy_date, max_hold)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report-day", default="20260716", help="D0 报告日 YYYYMMDD(篮子与卡的冻结日)")
    parser.add_argument("--today", default="20260717", help="合成集合竞价的交易日 YYYYMMDD")
    parser.add_argument("--keep-db", action="store_true", help="跑完保留临时 DB 副本(调试用)")
    args = parser.parse_args()

    report_day = datetime.strptime(args.report_day, "%Y%m%d").date()
    today = datetime.strptime(args.today, "%Y%m%d").date()

    if not settings.db_path.exists():
        logger.error("真实 %s 不存在,无法复制临时副本跑冒烟。", settings.db_path)
        return 1

    tmp_dir = Path(tempfile.mkdtemp(prefix="neckline_smoke_precall_"))
    tmp_db = tmp_dir / "neckline_smoke.db"
    shutil.copy2(settings.db_path, tmp_db)
    logger.info("已复制真实 DB 到临时副本(不污染生产):%s", tmp_db)

    try:
        logger.info("=== 用真实数据生成 %s 报告(供 %s 盘前校准)===", report_day, today)
        bundle = build_report(report_day, db_path=tmp_db, save=True)
        logger.info("报告已生成(策略大脑 %s)", bundle.strategy_version)

        wu = load_watch_universe(today, db_path=tmp_db, parquet_dir=None)
        rows = _daily_rows_lookup(today, wu.codes)
        _seed_d5_position(today, rows, tmp_db)

        # 持仓可能新增了 D5 票,重载 universe + 补它的行情
        wu = load_watch_universe(today, db_path=tmp_db, parquet_dir=None)
        rows = _daily_rows_lookup(today, wu.codes)
        quotes = {code: synthesize_auction_quote(rows[code]) for code in rows}
        missing = [c for c in wu.codes if c not in rows]
        if missing:
            logger.warning("%d/%d 只代码在 %s 无真实日线(停牌?),盘前对其无意见:%s",
                           len(missing), len(wu.codes), today, missing[:5])

        now = datetime.combine(today, time(9, 25, 30))
        logger.info("--- 盘前校准 tick @ %s(关注池 %d 只,拉到竞价快照 %d 只)---",
                    now.time(), len(wu.codes), len(quotes))
        res = run_precall_tick(
            now, db_path=tmp_db, parquet_dir=None,
            quotes_fn=lambda codes, _q=quotes: {c: _q[c] for c in codes if c in _q},
        )
        if not res.ran:
            logger.warning("盘前 tick 未执行(skipped=%s)——%s 是否真实交易日?", res.skipped_reason, today)
            return 0
        logger.info(
            "盘前校准结果:高开偏离剧本%d / 开盘即失效%d / 持仓止损预警%d / 竞价量能异常%d;"
            "D5 时间退出%d 只;汇总推送门槛(actionable)=%d",
            len(res.gap_up), len(res.low_open), len(res.position_low_open),
            len(res.auction), len(res.d5_exits), res.summary_actionable,
        )
        for code in res.gap_up:
            logger.info("  [高开偏离剧本] %s", code)
        for code in res.low_open:
            logger.info("  [开盘即失效] %s", code)
        for code in res.position_low_open:
            logger.info("  [持仓预警] %s", code)
        for code in res.auction:
            logger.info("  [竞价量能] %s", code)
        for ex in res.d5_exits:
            logger.info("  [D5 退出] %s(%s)D%d", ex.name, ex.ts_code, ex.d)
        logger.info("=== 盘前校准冒烟结束 ===")
        return 0
    finally:
        if args.keep_db:
            logger.info("--keep-db 已指定,临时 DB 保留:%s", tmp_db)
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.info("已清理临时 DB 副本。")


if __name__ == "__main__":
    raise SystemExit(main())
