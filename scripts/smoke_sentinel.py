#!/usr/bin/env python3
"""哨兵合成盘中冒烟(plan 阶段3)。**用途**:今天(2026-07-20)是周日,无法在真实
盘中轮询验证四哨兵——本脚本拿某个历史交易日的**真实日线数据**(已backfill落
Parquet)合成一份"盘中快照",喂给与生产环境完全同一份编排代码
(`neckline.sentinel.engine.run_tick`)跑,替代活体盘中验证。**这不是活体验证的
替代品,只是"代码路径确实按预期工作"的一次有真实数据支撑的冒烟检查**——真正
的盘中实测仍需用户在下一个交易日用 `scripts/sentinel.py` 跑。

默认回放:`report_day=2026-07-16` 生成候选(用于 2026-07-17 的开仓计划),
`today=2026-07-17` 合成盘中——后者是阶段0/2 记录的施工期真实大跌日(涨停34/
跌停212/炸板28%),对退潮哨兵是一次有说服力的压力测试。

**合成方法(诚实标注局限,§3.2「无分钟线」的直接后果)**:本项目盘中免费源
只有实时快照,没有历史分钟线可回放,因此"合成盘中快照"只能从**真实 EOD OHLC**
反推三个代表性检查点(早盘/盘中/尾盘),不是真分钟数据:
    · 早盘(09:45,elapsed≈15min,故意留在"early"量能折算窗口内):价=当日开盘价。
    · 盘中(10:35,elapsed≈65min):价 = 开盘价与当日最终方向的极值之间按 60% 比例
      插值(下跌日插向最低价、上涨日插向最高价,模拟"早盘趋势已现"但未到极值)。
    · 尾盘(14:50,elapsed≈230min):价 = 当日真实收盘价,high/low 用当日真实值
      (此时累计高低点确实约等于全天高低点,这一点是准确的,不是近似)。
成交量/成交额按各检查点的 `vol_frac`(见 `CHECKPOINTS`)折算到当日总量的一个
比例;VWAP 因此在三个检查点间**不随时间演化**(近似为全天平均 VWAP),这是
合成数据固有的简化,详见脚本内注释。

**不污染真实数据**:先把 `data/neckline.db` 整份复制到临时文件,持仓/哨兵事件的
写入全部落在临时副本;Parquet(只读)仍用真实 `data/parquet/`。跑完清理临时文件
(除非 `--keep-db`)。
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402

from neckline.calendar import trading_days_between  # noqa: E402
from neckline.config import settings  # noqa: E402
from neckline.data.market_data import get_market_slice  # noqa: E402
from neckline.report.pipeline import build_report  # noqa: E402
from neckline.sentinel.channels import ConsoleChannel  # noqa: E402
from neckline.sentinel.engine import run_tick  # noqa: E402
from neckline.sentinel.positions import open_position  # noqa: E402
from neckline.sentinel.quotes import Quote  # noqa: E402
from neckline.sentinel.universe import load_watch_universe  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smoke_sentinel")


@dataclass
class Checkpoint:
    label: str
    hh: int
    mm: int
    vol_frac: float   # 累计到该时刻的成交量/额占当日总量的比例
    is_final: bool = False   # 尾盘检查点:high/low 用当日真实值(此时刻确已成立)


CHECKPOINTS = [
    Checkpoint("早盘(09:45)", 9, 45, 0.10),
    Checkpoint("盘中(10:35)", 10, 35, 0.45),
    Checkpoint("尾盘(14:50)", 14, 50, 1.00, is_final=True),
]


def _synthesize_price(row: dict, checkpoint: Checkpoint) -> float:
    """按当日 OHLC 与检查点插值出一个"当时价格"(合成,非真实分钟价,见模块头
    注释)。尾盘检查点直接用真实收盘价。"""
    if checkpoint.is_final:
        return row["close"]
    open_, close, high, low = row["open"], row["close"], row["high"], row["low"]
    if close <= open_:  # 下跌日,插向最低价
        return open_ - (open_ - low) * 0.6
    return open_ + (high - open_) * 0.6  # 上涨日,插向最高价


def synthesize_quote(row: dict, checkpoint: Checkpoint) -> Quote:
    """从一行真实 `daily` EOD 记录 + 检查点定义,合成一个 `Quote`。"""
    price = _synthesize_price(row, checkpoint)
    open_ = row["open"]
    if checkpoint.is_final:
        high, low = row["high"], row["low"]
    else:
        high, low = max(open_, price), min(open_, price)
    # daily.vol 单位=手,Quote.volume 单位也是手(§3.7)——同单位,不需要换算;
    # daily.amount 单位=千元,Quote.amount 单位=元,×1000 归一。
    volume = float(row["vol"] or 0.0) * checkpoint.vol_frac
    amount = float(row["amount"] or 0.0) * 1000.0 * checkpoint.vol_frac
    return Quote(
        code=row["ts_code"].split(".")[0], name=row["ts_code"], price=round(price, 2),
        pre_close=row["pre_close"], open=open_, high=high, low=low,
        volume=volume, amount=amount, ts=f"{checkpoint.label} 合成", source="synthetic",
    )


def _daily_rows_lookup(trade_date: date, codes: List[str], parquet_dir: Optional[Path] = None) -> Dict[str, dict]:
    df = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
    if df.is_empty():
        return {}
    df = df.filter(pl.col("ts_code").is_in(codes))
    return {r["ts_code"]: r for r in df.iter_rows(named=True)}


def _pick_synthetic_positions(today: date, parquet_dir: Optional[Path] = None) -> List[dict]:
    """从 `today` 真实全市场行情里挑两只票合成持仓:跌幅最大的一只(压力测试
    止损逼近)+ 涨跌平缓的一只(对照组,预期不告警)。买入价用 `today` 前3个
    交易日的真实收盘价(模拟"已持有几天"),不是瞎编的数字。"""
    df = get_market_slice(today, table="daily", parquet_dir=parquet_dir)
    if df.is_empty():
        return []
    df = df.filter((pl.col("pre_close") > 0) & (pl.col("close") > 2.0)).with_columns(
        (pl.col("close") / pl.col("pre_close") - 1).alias("_ret")
    )
    worst = df.sort("_ret").head(1).to_dicts()
    calm = df.filter(pl.col("_ret").abs() < 0.005).head(1).to_dicts()
    picks = worst + calm
    days_before = trading_days_between(date(today.year - 1, 1, 1), today)
    buy_date = days_before[-4] if len(days_before) >= 4 else today
    buy_rows = _daily_rows_lookup(buy_date, [p["ts_code"] for p in picks], parquet_dir=parquet_dir)
    out = []
    for p in picks:
        buy_row = buy_rows.get(p["ts_code"])
        buy_price = buy_row["close"] if buy_row else p["pre_close"]
        out.append({"ts_code": p["ts_code"], "buy_price": buy_price, "buy_date": buy_date, "label": (
            "跌幅最大" if p is worst[0] else "涨跌平缓"
        )})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report-day", default="20260716", help="生成候选用的报告日 YYYYMMDD(默认20260716)")
    parser.add_argument("--today", default="20260717", help="合成盘中快照的交易日 YYYYMMDD(默认20260717,真实大跌日)")
    parser.add_argument("--keep-db", action="store_true", help="跑完不删除临时 DB 副本(调试用,打印其路径)")
    args = parser.parse_args()

    report_day = datetime.strptime(args.report_day, "%Y%m%d").date()
    today = datetime.strptime(args.today, "%Y%m%d").date()

    if not settings.db_path.exists():
        logger.error("真实 %s 不存在,无法复制出临时副本跑冒烟。", settings.db_path)
        return 1

    tmp_dir = Path(tempfile.mkdtemp(prefix="neckline_smoke_"))
    tmp_db = tmp_dir / "neckline_smoke.db"
    shutil.copy2(settings.db_path, tmp_db)
    logger.info("已复制真实 DB 到临时副本(不污染生产数据):%s", tmp_db)

    try:
        logger.info("=== 用真实数据生成 %s 的候选报告(用于 %s 开仓计划)===", report_day, today)
        bundle = build_report(report_day, db_path=tmp_db, save=True)
        logger.info(
            "候选%d只(策略大脑 %s);情绪仪表盘:涨停%d/跌停%d/炸板率%.0f%%/仓位额度%s",
            len(bundle.candidates), bundle.strategy_version,
            bundle.sentiment.limit_up_count, bundle.sentiment.limit_down_count,
            bundle.sentiment.zaban_rate * 100, bundle.sentiment.position_quota,
        )

        picks = _pick_synthetic_positions(today)
        for p in picks:
            open_position(p["ts_code"], p["buy_price"], 100, p["buy_date"], note=f"冒烟合成持仓({p['label']})", db_path=tmp_db)
            logger.info("合成持仓:%s(%s) 买入价%.2f 买入日%s", p["ts_code"], p["label"], p["buy_price"], p["buy_date"])

        wu = load_watch_universe(today, db_path=tmp_db, parquet_dir=None)
        logger.info("关注池:候选%d只 + 持仓%d只 + 昨日涨停股代理样本%d只,共去重%d只代码",
                    len(wu.candidates), len(wu.positions), len(wu.breadth_extra_codes), len(wu.codes))

        rows = _daily_rows_lookup(today, wu.codes)
        missing = [c for c in wu.codes if c not in rows]
        if missing:
            logger.warning("%d/%d 只代码在 %s 无真实日线数据(可能停牌),该检查点将拉不到行情:%s",
                            len(missing), len(wu.codes), today, missing[:5])

        channel = ConsoleChannel()
        for cp in CHECKPOINTS:
            now = datetime.combine(today, time(cp.hh, cp.mm))
            quotes_at_cp = {code: synthesize_quote(rows[code], cp) for code in rows}

            logger.info("--- 检查点:%s ---", cp.label)
            result = run_tick(
                now, channels=[channel], db_path=tmp_db, parquet_dir=None,
                quotes_fn=lambda codes, _q=quotes_at_cp: {c: _q[c] for c in codes if c in _q},
            )
            bs = result.breadth_snapshot
            if result.retreat_alert:
                retreat_state = "红色刹车"
            elif result.retreat_warning:
                retreat_state = "黄色预警"
            elif result.retreat_active:
                retreat_state = "已闩锁(更早红色)"
            else:
                retreat_state = "未触发"
            logger.info(
                "%s 汇总:拉到%d/%d只行情;关注池宽度(样本%d只)涨停%d/跌停%d/炸板率%.0f%%;"
                "退潮%s;买点信号%d个;证伪信号%d个;持仓告警%d个;本拍推送%d条(去重跳过%d条)",
                cp.label, result.quotes_fetched, result.watched_codes,
                bs.sample_size if bs else 0, bs.limit_up_count if bs else 0,
                bs.limit_down_count if bs else 0, (bs.zaban_rate * 100) if bs else 0.0,
                retreat_state,
                len(result.entry_signals), len(result.invalidation_signals), len(result.holding_alerts),
                len(result.pushed_events), result.skipped_duplicate,
            )
            for sig in result.entry_signals:
                logger.info("  [买点] %s(%s):%s", sig.name, sig.ts_code, sig.reason)
            for inv in result.invalidation_signals:
                logger.info("  [证伪] %s(%s):%s", inv.name, inv.ts_code, inv.reason_text)
            for alert in result.holding_alerts:
                for key, reason in alert.alerts.items():
                    logger.info("  [持仓·%s] %s:%s", key, alert.ts_code, reason)
            if result.retreat_alert:
                logger.info("  [退潮·红色刹车] %s", result.retreat_alert.reason_text)
            elif result.retreat_warning:
                logger.info("  [退潮·黄色预警] %s", result.retreat_warning)

        logger.info("=== 冒烟结束 ===")
        return 0
    finally:
        if args.keep_db:
            logger.info("--keep-db 已指定,临时 DB 副本保留在:%s", tmp_db)
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.info("已清理临时 DB 副本。")


if __name__ == "__main__":
    raise SystemExit(main())
