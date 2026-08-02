#!/usr/bin/env python3
"""每交易日盘后增量更新(plan 0.4)。复用 backfill.py 的落盘函数,只跑一天
(默认今天,可传参指定其它交易日)+ 尾部窗口重算 limit_derived(连板计数跨批次
边界需要窗口,见 backfill.run_limit_derived 的 30 自然日缓冲说明)。

**v1.4-①-C 新增两项(§七 P0-3 / P0-2)**:
  · **概念板块日更**(`ths_daily` 尾窗重拉 + `ths_index`/`ths_member` 周更)—— 此前三表
    只有一次性 backfill,压根不在日更清单里,导致「当日暴起板块」那一路候选长期失效
    (且因为降级得安静,从报告上看不出坏了)。落盘细节与 `write_table_day` 铁律的**唯一
    登记例外**见 `neckline/data/concept_data.py` 模块头。
  · **当日停牌名单**(`suspend_d`)—— 给持仓票「当日无 EOD 行」的 reason 定标签
    (`suspended` vs `data_gap`),见 `neckline/data/price_stale.py`。
  两项都**尽力而为**:失败只记 WARNING,绝不让主增量(daily/basic/adj/moneyflow)失败,
  也绝不改变退出码(它们是增强项,不是 EOD 主链路)。

**v1.4-⑩-C 新增第三项(§七 P0-23)**:**行业强度预计算落表**(`industry_strength_daily`)
  —— 只读当日那一个 `daily` 分区算一天,`persist_days` 递推;16:35 报告主链 / 信息卡端点 /
  问询台三处从此**只读表**,不再各自扫全历史 784 万行(生产 2 vCPU/1.6G 上 700M cap
  OOM-kill、1400M cap 600s 跑不完)。同样**尽力而为不改退出码,但日志用 ERROR** —— 它是
  **判据输入**(A2 hard_cut + 排序键①)不是增强项;失败日志带补算命令原文。

**V2-④b 新增第四项(K7 需求 1b,§七 P0-23)**:**行业题材阶段六态状态机落表**
  (`industry_stage_daily`)—— 只读 `industry_strength_daily` 当日行 + `limit_derived`
  当日一个分区 + 本表自己过去 5 个交易日的既有行,不扫任何全历史;排在
  `update_industry_strength` **之后**(依赖它刚写的 `persist_days`)。同样**尽力而为
  不改退出码,但日志用 ERROR**(未来 ⑥ 的 `driver_freshness` 判据输入,非增强项)。

新增配额消耗(与 §七 P4-20 一起算账,部署块 ⑨-E 复核):`ths_daily` 5 次/日(尾窗)、
`suspend_d` 1 次/日、`ths_index`+`ths_member` ~400 次/周。

用法:
    python scripts/daily_update.py                # 今天(若非交易日则报错退出)
    python scripts/daily_update.py 20260717        # 指定某交易日补更新
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backfill  # noqa: E402  (同目录 scripts/backfill.py)

from neckline.calendar import is_trading_day, reset_cache  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.db import init_schema  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_update")

LIMIT_DERIVED_TRAILING_DAYS = 15  # 尾部重算窗口(交易日),覆盖连板计数跨批次边界


def update_concept_boards(target: date) -> None:
    """v1.4-①-C:概念板块日更(`ths_daily` 尾窗)+ 周更(`ths_index`/`ths_member`)。
    **尽力而为**——任何异常只记 WARNING,不影响主增量与退出码。"""
    from neckline.calendar import trading_days_between
    from neckline.data.concept_data import (
        THS_DAILY_TRAILING_DAYS,
        max_ths_daily_date,
        update_ths_daily,
        update_ths_snapshots,
    )

    # 周更放在日更**之前**:`update_ths_daily` 要按 `ths_index` 快照过滤成概念板块
    # (见该函数 docstring 的 ⚠),先刷新快照能让当周新增板块当天就被纳入。
    try:
        done = update_ths_snapshots(target)
        logger.info("[ths_index/ths_member] 周更:%s", done)
    except Exception:  # noqa: BLE001
        logger.warning("[ths_index/ths_member] 周更异常(已吞,旧快照原样保留)", exc_info=True)

    try:
        window = trading_days_between(target - timedelta(days=30), target)[-THS_DAILY_TRAILING_DAYS:]
        stats = update_ths_daily(window)
        logger.info(
            "[ths_daily] 尾窗 %d 个交易日:写入 %d 行、当日尚未发布 %d 天、拉取失败 %d 天",
            stats["days"], stats["rows"], stats["empty"], stats["failed"],
        )
        newest = max_ths_daily_date()
        if newest is None:
            logger.warning("[ths_daily] 落盘后仍无任何数据 —— 板块相关情报本日不可信")
        elif newest < target:
            lag = max(len(trading_days_between(newest, target)) - 1, 0)
            logger.info("[ths_daily] 最新至 %s(落后报告日 %d 个交易日)", newest, lag)
    except Exception:  # noqa: BLE001
        logger.warning("[ths_daily] 日更异常(已吞,不阻断主增量)", exc_info=True)


def update_suspend_list(target: date) -> None:
    """v1.4-①-B:当日全市场停牌名单落盘(`suspend_d`,走 `write_table_day` 铁律路径)。
    **尽力而为**——拉不到就不落盘,`price_stale` 读不到该分区时 reason 如实降级为
    `unknown`(不猜成 suspended)。"""
    from neckline.data.market_data import write_table_day
    from neckline.data.tushare_client import ts_suspend_d_all

    try:
        res = ts_suspend_d_all(target.strftime("%Y%m%d"))
        if not res.ok or res.data is None:
            logger.warning("[suspend_d] 拉取失败:%s(不落盘,reason 将降级为 unknown)", res.reason)
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


def update_industry_strength(target: date) -> None:
    """v1.4-⑩-C(§七 P0-23):行业强度预计算落表(`industry_strength_daily`)。

    **只读当日那一个 `daily` 分区**算一天,`persist_days` 由「上一评定日 streak + 今日强度日
    标记」递推 —— 16:35 报告主链 / 信息卡端点 / 问询台三处从此**只读表**,不再各自扫全历史
    784 万行(生产 2 vCPU/1.6G 上根本跑不完)。

    **尽力而为**(异常吞掉、不改退出码,同 `update_suspend_list`/`update_concept_boards`
    两位先例)**但日志级别用 ERROR** —— 它是**判据输入**(A2 hard_cut + 排序键①),不是
    增强项;日志带**补算命令原文**,让运维看到就知道下一步敲什么。"""
    from neckline.report.industry_strength_store import (
        industry_strength_status,
        refresh_command_hint,
        refresh_industry_strength,
    )

    try:
        stats = refresh_industry_strength([target])
        fresh = industry_strength_status(target)
        if stats["rows"] == 0:
            logger.error(
                "[industry_strength] %s 未落任何行(缺 daily 分区 %d 天 / 无行业映射?)——"
                "今日报告的题材持续天数与 A2/B3 将走保险丝降级。补算:%s",
                target, stats["missing"], refresh_command_hint(target, target),
            )
        elif stats.get("holes"):
            # v1.4 review 🟡-2:落了行 ≠ 数是对的 —— 表里还留着断口时 streak 是桥过缺口
            # 算出来的。**这一条不许被上面那句 INFO 的绿意盖过去**,故单开 ERROR 分支。
            logger.error(
                "[industry_strength] %s 落 %d 行,但表内仍有 %d 个交易日**断口**(%s)——"
                "题材持续天数可能桥过缺口失真(A2/B3 与排序行业维度受影响)。"
                "先补齐那几天的 daily 分区再跑:%s",
                target, stats["rows"], len(stats["holes"]), ",".join(stats["holes"][:10]),
                refresh_command_hint(),
            )
        else:
            logger.info("[industry_strength] %s 落 %d 行(表内最新至 %s,落后 %d 个交易日)",
                        target, stats["rows"], fresh.latest_label(), fresh.lag_days)
    except Exception:  # noqa: BLE001
        logger.error(
            "[industry_strength] 日更异常(已吞,不阻断主增量)——**判据输入缺失**,"
            "今日报告的题材持续天数与 A2/B3 将走保险丝降级。补算:%s",
            refresh_command_hint(target, target), exc_info=True,
        )


def update_industry_stage(target: date) -> None:
    """V2-④b(plan §五 V2-④b,K7 需求 1b,§七 P0-23):行业题材阶段六态状态机预计算
    落表(`industry_stage_daily`)—— 只读 `industry_strength_daily` 当日行 + `limit_derived`
    当日一个分区 + 本表自己过去 5 个交易日的既有行,不扫任何全历史(见
    `neckline/scan/stage.py` 模块 docstring)。

    **尽力而为**(异常吞掉、不改退出码,同 `update_industry_strength` 先例)**但日志
    级别用 ERROR** —— 它是未来 ⑥ `driver_freshness` 维度的判据输入,不是增强项;
    日志带补算命令原文。**排在 `update_industry_strength` 之后**(依赖它刚写的
    `persist_days`)。"""
    from neckline.scan.stage import industry_stage_status, refresh_command_hint, refresh_industry_stage

    try:
        stats = refresh_industry_stage([target])
        fresh = industry_stage_status(target)
        if stats["rows"] == 0:
            logger.error(
                "[industry_stage] %s 未落任何行(industry_strength_daily 当日无行,源表本身"
                "可能未就绪)——未来 driver_freshness 六态判据本日走保险丝降级。补算:%s",
                target, refresh_command_hint(target, target),
            )
        else:
            logger.info("[industry_stage] %s 落 %d 行(表内最新至 %s,落后 %d 个交易日)",
                        target, stats["rows"], fresh.latest_label(), fresh.lag_days)
    except Exception:  # noqa: BLE001
        logger.error(
            "[industry_stage] 日更异常(已吞,不阻断主增量)——未来 driver_freshness 判据"
            "输入缺失。补算:%s",
            refresh_command_hint(target, target), exc_info=True,
        )


def update_scan_layer(target: date) -> None:
    """V2-④(plan §五 V2-④,P0-23):市场扫描层三张预计算表(`limit_cluster_daily`/
    `corr_matrix_daily`/`leader_structure_daily`)日更增量,固定顺序
    cluster→corr→leader(见 `neckline/scan/__init__.py`)。

    **尽力而为,WARNING 级别**(同 `update_suspend_list`/`update_concept_boards`
    两位先例,比 `update_industry_strength` 的 ERROR 级别更保守)——V2-④ 落地时
    尚无任何在线路径消费这三张表(⑤ 驱动聚合层未建),当日零行是**合法**的
    "今天没有涨停共振/没有够格的相关对"结果,不是判据输入缺失,不值得 ERROR
    级别报警;真出异常(数据管线本身坏了)才升级。**依赖当日 `limit_derived`
    与 `industry_strength_daily` 已落盘**,故排在 `run_limit_derived` 与
    `update_industry_strength` 两者之后。"""
    from neckline.scan import cluster, corr, leader, seeds

    try:
        c_stats = cluster.refresh_limit_clusters([target])
        r_stats = corr.refresh_corr_matrix([target])
        l_stats = leader.refresh_leader_structure([target])
        logger.info(
            "[scan_layer] %s cluster=%d行(同日簇%d/连板簇%d) corr=%d行 leader=%d行",
            target, c_stats["rows"], c_stats["same_day_clusters"], c_stats["consecutive_clusters"],
            r_stats["rows"], l_stats["rows"],
        )
        seed_set = seeds.generate_seeds(target)
        if seed_set is None:
            logger.warning(
                "[scan_layer] %s 无现役策略包 —— 今日不产出驱动种子(先跑 "
                "`python scripts/activate_pack.py --file packs/K4-pack.json --confirm`)",
                target,
            )
        else:
            counts = seed_set.counts()
            logger.info(
                "[scan_layer] %s 驱动种子:热点行业%d/暴起概念%d/涨停簇%d/异动簇%d(pack=%s)",
                target, counts["hot_industry"], counts["surging_concept"],
                counts["limit_cluster"], counts["anomaly_cluster"], seed_set.pack_version,
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "[scan_layer] 日更异常(已吞,不阻断主增量)。补算:"
            "`python scripts/scan_layer.py refresh --from %s --to %s`",
            target.strftime("%Y%m%d"), target.strftime("%Y%m%d"), exc_info=True,
        )


def main() -> int:
    if not settings.tushare_token:
        logger.error("TUSHARE_TOKEN 缺失(.env),无法拉取。")
        return 1

    ensure_data_dirs()
    init_schema()
    reset_cache()

    target = datetime.strptime(sys.argv[1], "%Y%m%d").date() if len(sys.argv) > 1 else date.today()

    if not is_trading_day(target):
        logger.error("%s 不是交易日,无需更新。若日历本身过期,先跑 scripts/init_calendar.py 补数据。", target)
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

    # v1.4-①-B/①-C 增强项(尽力而为,失败不改退出码;放在主增量之后,免得它们的失败
    # 影响 EOD 主链路的落盘时序)。
    update_suspend_list(target)
    update_concept_boards(target)
    # v1.4-⑩-C:排在两位增强项**之后**(它吃的是本次主增量刚落的当日 `daily` 分区,
    # 时序上必须在 `backfill_day_tables` 之后)。
    update_industry_strength(target)
    # V2-④b:排在 `update_industry_strength` 之后(依赖它刚写的 `persist_days`),
    # 在 `update_scan_layer` 之前(两者互不依赖,谁先谁后均可,这里选择紧跟着它的
    # 唯一上游一起收尾)。
    update_industry_stage(target)
    # V2-④:排在最后——依赖本次刚落的 `limit_derived`(上面 `run_limit_derived`)与
    # `industry_strength_daily`(上面 `update_industry_strength`)两者都已就绪。
    update_scan_layer(target)

    logger.info("增量更新完成:%s", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
