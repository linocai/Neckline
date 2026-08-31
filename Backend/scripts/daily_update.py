#!/usr/bin/env python3
"""每交易日盘后增量更新(plan 0.4)。复用 backfill.py 的落盘函数,只跑一天
(默认今天,可传参指定其它交易日)+ 尾部窗口重算 limit_derived(连板计数跨批次
边界需要窗口,见 backfill.run_limit_derived 的 30 自然日缓冲说明)。

**当日停牌名单**(`suspend_d`,v1.4-①-C)—— 事实层判「全天停牌 vs 盘中临时停牌」的
输入(裁定 12),见 `neckline/facts/pack.py::_suspend_flag_of`。**尽力而为**:失败只记
WARNING,绝不让主增量(daily/basic/adj/moneyflow)失败,也绝不改变退出码。

旧同花顺概念链已经退役并物理删除；日更只维护现行 K9 所需数据。

当前配额消耗:`suspend_d` 1 次/日、申万分类 3 + 2 次/日(S2)。

用法:
    python scripts/daily_update.py                # 今天(若非交易日则报错退出)
    python scripts/daily_update.py 20260717        # 指定某交易日补更新
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backfill  # noqa: E402  (同目录 scripts/backfill.py)

from neckline.calendar import CN_TZ, official_is_trading_day, reset_cache  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.db import init_schema  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_update")

LIMIT_DERIVED_TRAILING_DAYS = 15  # 尾部重算窗口(交易日),覆盖连板计数跨批次边界


def update_sw_industry(target: date) -> bool:
    """V2.5.0 S2:申万 2021 版行业分类日更(`sw_industry_classify` / `sw_industry_member`)。

    fp-4 只能消费目标交易日的不可变成员快照。因此日更刷新失败或未能写入
    ``target`` 快照时，必须让主任务失败；不能用当前归属替代历史回填，也不能把
    ``fetched_at`` 伪装为有效交易日。
    """
    from neckline.data import sw_industry

    stats = sw_industry.refresh(target_date=target)
    if not stats.ok:
        logger.error(
            "[sw_industry] 申万分类日更未通过，拒绝生成目标日 fp-4。原因:%s。补算:"
            "python -c \"from datetime import date; from neckline.data import sw_industry; print(sw_industry.refresh(target_date=date.today()))\"",
            stats.reason,
        )
        return False
    logger.info("[sw_industry] %s", stats.summary())
    return True


def build_and_freeze_fact_pack(target: date) -> None:
    """V2.5.0 S3:当日事实包构建 + 冻结(PROJECT_PLAN §5.3)。

    🔴 **数据未到齐 → 不冻结**(架构 §3.5):`build()` 返回 `IncompletePack` 时本函数
    **什么都不写**,只把缺口逐条打进日志 —— 报告层据此出「今天没跑成 · <缺口逐条>」。
    ⛔ 不冻一份残包、⛔ 不用昨天的数据顶今天的位。

    **已冻结过就跳过**(`PackAlreadyFrozen`):冻结不可覆盖(§5.3.2 纪律 3),补跑同一天
    是幂等的 no-op,不是错误。口径真变了走新 `pack_version`。

    目标日 SW2021 成员快照是 fp-4 的硬输入；日更已在调用本函数前完成该闸门。
    """
    from neckline.facts import v4 as fact_pack
    from neckline.facts.pack import IncompletePack
    from neckline.facts import store as fact_store

    try:
        built = fact_pack.build(target)
    except Exception:  # noqa: BLE001
        logger.error("[fact_pack] %s 构建异常(已吞,不阻断主增量)", target, exc_info=True)
        return
    if isinstance(built, IncompletePack):
        logger.error(
            "[fact_pack] %s **数据未到齐,不冻结**(报告将出「今天没跑成」)。缺口:%s。"
            "补齐后重跑:python scripts/daily_update.py %s",
            target, built.describe(), target.strftime("%Y%m%d"),
        )
        return
    try:
        frozen = fact_store.freeze_pack(built, origin=fact_store.ORIGIN_LIVE)
    except fact_store.PackAlreadyFrozen as e:
        logger.info("[fact_pack] %s 已冻结过,跳过(幂等):%s", target, e)
        return
    except Exception:  # noqa: BLE001
        logger.error("[fact_pack] %s 冻结异常(已吞,不阻断主增量)", target, exc_info=True)
        return
    logger.info(
        "[fact_pack] %s 已冻结:%d 行、%d 个二级行业、停牌异常 %d、sha256=%s…",
        target, frozen.row_count, len(built.industry_rows),
        frozen.suspend_anomaly_count, frozen.content_fingerprint[:12],
    )


def verify_readiness(target: date) -> bool:
    """日更的最终判据：19:00 消费前必须通过只读就绪检查。"""
    from neckline.facts import readiness

    result = readiness.preflight(target, pack_version="fp-4")
    if result.ready:
        logger.info("[readiness] %s 已就绪，冻结包=%s", target, result.pack_id)
        return True
    logger.error("[readiness] %s 未就绪，晚间链将只产出 not_run：%s", target, "；".join(result.gaps))
    return False



def update_suspend_list(target: date) -> None:
    """当日全市场停牌名单落盘；这不是事实包的阻断输入。"""
    from neckline.data.market_data import write_table_day
    from neckline.data.tushare_client import ts_suspend_d_all

    try:
        res = ts_suspend_d_all(target.strftime("%Y%m%d"))
        if not res.ok or res.data is None:
            logger.warning("[suspend_d] 拉取失败:%s（不落盘）", res.reason)
            return
        # 当日零停牌是**正常且有信息量**的结果(「今天没人停牌」≠「今天没查」),照样落盘。
        # 空表也要给显式 dtype —— 全 Null dtype 列会成为下一次 `_align_to_table_schema`
        # 的脏基准(v1.3.5 事故的同一条链:空分区是脏基准的唯一来源)。
        df = backfill._pdf_to_pl(res.data) if len(res.data) else pl.DataFrame(
            schema={"ts_code": pl.String, "trade_date": pl.Date, "suspend_type": pl.String}
        )
        write_table_day("suspend_d", target, df)
        logger.info("[suspend_d] %s 停牌 %d 只", target, df.height)
    except Exception:  # noqa: BLE001
        logger.warning("[suspend_d] 日更异常(已吞,不阻断主增量)", exc_info=True)


def update_top_list(target: date) -> None:
    """尽力获取龙虎榜原始事实；它不参与 K9-v3 的参数未配置安全态。"""
    from neckline.data.top_list import load_top_list

    try:
        frame = load_top_list(target, fetch_if_missing=True)
        from neckline.data.market_data import day_file_exists
        if day_file_exists("top_list", target):
            logger.info("[top_list] %s 已查，%d 行", target, frame.height)
        else:
            logger.warning("[top_list] %s 数据不可用；fp-4 将明确记录 unavailable", target)
    except Exception:  # noqa: BLE001
        logger.warning("[top_list] %s 获取异常；fp-4 将明确记录 unavailable", target,
                       exc_info=True)


def main() -> int:
    if not settings.tushare_token:
        logger.error("TUSHARE_TOKEN 缺失(.env),无法拉取。")
        return 1

    ensure_data_dirs()
    init_schema()
    reset_cache()

    target = (datetime.strptime(sys.argv[1], "%Y%m%d").date()
              if len(sys.argv) > 1 else datetime.now(CN_TZ).date())

    calendar_open = official_is_trading_day(target)
    if calendar_open is None:
        logger.error("%s 不在已落库的官方交易日历中；拒绝用工作日近似更新。先跑 scripts/init_calendar.py。", target)
        return 1
    if not calendar_open:
        logger.error("%s 不是交易日,无需更新。", target)
        return 1

    logger.info("增量更新交易日:%s", target)

    backfill.bootstrap_metadata()

    stats = backfill.backfill_day_tables([target], backfill.DAY_TABLES, force=True)
    for table, s in stats.items():
        logger.info("[%s] 新拉 %d 天(%d 行)、失败 %d 天", table, s["fetched"], s["rows"], s["failed"])

    backfill.backfill_index_daily(target, target)

    from neckline.calendar import trading_days_between

    all_days = trading_days_between(date(target.year - 1, 1, 1), target)
    window_start = all_days[-LIMIT_DERIVED_TRAILING_DAYS] if len(all_days) >= LIMIT_DERIVED_TRAILING_DAYS else all_days[0]
    backfill.run_limit_derived(window_start, target)

    # v1.4-①-B 增强项(尽力而为,失败不改退出码;放在主增量之后,免得它们的失败
    # 影响 EOD 主链路的落盘时序)。
    update_suspend_list(target)
    update_top_list(target)
    # V2.5.0 S2:申万二级分类日更(**判据输入**,失败打 ERROR;见函数 docstring)。
    if not update_sw_industry(target):
        logger.error("[sw_industry] 目标日快照未就绪，拒绝构建 fp-4。")
        return 1
    # V2.5.0 S3:事实包构建 + 冻结(架构第一层)。必须排在 `update_sw_industry` **之后**
    # (申万归属是中位数的输入)与全部行情落盘之后(完整性判定要看当日分区)。
    build_and_freeze_fact_pack(target)
    if not verify_readiness(target):
        # 16:05 进程必须让 systemd 看见失败；不得把半套数据伪装成成功更新。
        return 1
    logger.info("增量更新完成:%s", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
