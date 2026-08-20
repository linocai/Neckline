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


def update_sw_industry() -> None:
    """V2.5.0 S2:申万 2021 版行业分类日更(`sw_industry_classify` / `sw_industry_member`)。

    🔴 **判据输入,不是增强项**:K9 全文的「相对强度」以申万**二级**行业当日成员涨跌幅
    中位数为基准(裁定 2),第三层排序的「行业热度分」也读它。故与 `update_suspend_list`
    / `update_concept_boards` 同样**尽力而为不改退出码**,但**日志级别用 ERROR**
    (同已退役的 `update_industry_strength` 旧例的理由)。

    ⚠ **两张表是「当前归属快照」,与 `target` 无关** —— 接口只给当前归属,故本函数
    不接受交易日参数;补跑历史日时它照样把表刷成今天的。这一点与事实包回填的语义差
    是同一件事(PROJECT_PLAN §5.3.5:回填包用的是**今天的**申万归属快照),
    ⛔ 别写「按 target 回改历史归属」的机灵代码。
    """
    from neckline.data import sw_industry

    stats = sw_industry.refresh()
    if not stats.ok:
        logger.error(
            "[sw_industry] 申万分类日更未通过(已吞,不阻断主增量)——**判据输入缺失**,"
            "今日相对强度与行业热度分将算不出。原因:%s。补算:"
            "python -c \"from neckline.data import sw_industry; print(sw_industry.refresh())\"",
            stats.reason,
        )
        return
    logger.info("[sw_industry] %s", stats.summary())


def build_and_freeze_fact_pack(target: date) -> None:
    """V2.5.0 S3:当日事实包构建 + 冻结(PROJECT_PLAN §5.3)。

    🔴 **数据未到齐 → 不冻结**(架构 §3.5):`build()` 返回 `IncompletePack` 时本函数
    **什么都不写**,只把缺口逐条打进日志 —— 报告层据此出「今天没跑成 · <缺口逐条>」。
    ⛔ 不冻一份残包、⛔ 不用昨天的数据顶今天的位。

    **已冻结过就跳过**(`PackAlreadyFrozen`):冻结不可覆盖(§5.3.2 纪律 3),补跑同一天
    是幂等的 no-op,不是错误。口径真变了走新 `pack_version`。

    与 `update_sw_industry` 同一姿势:**尽力而为不改退出码,但日志用 ERROR** ——
    它是整条 K9 链的输入,不是增强项。
    """
    from neckline.facts import pack as fact_pack
    from neckline.facts import store as fact_store

    try:
        built = fact_pack.build(target)
    except Exception:  # noqa: BLE001
        logger.error("[fact_pack] %s 构建异常(已吞,不阻断主增量)", target, exc_info=True)
        return
    if isinstance(built, fact_pack.IncompletePack):
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
    # V2.5.0 S2:申万二级分类日更(**判据输入**,失败打 ERROR;见函数 docstring)。
    update_sw_industry()
    # V2.5.0 S3:事实包构建 + 冻结(架构第一层)。必须排在 `update_sw_industry` **之后**
    # (申万归属是中位数的输入)与全部行情落盘之后(完整性判定要看当日分区)。
    build_and_freeze_fact_pack(target)
    # —— 🔴 V2.5.0 S1:三项 K8 日更已摘除 ————————————————————————————————————
    # `update_industry_strength`(`industry_strength_daily`)、`update_industry_stage`
    # (`industry_stage_daily`)、`update_scan_layer`(`limit_cluster_daily` /
    # `corr_matrix_daily` / `leader_structure_daily`)三个函数**已删除**:它们的落表
    # 模块随 K8 退役(`scan/` 整包、`report/industry_strength_store.py`),三张表按
    # 裁定 6 **保留只读、不迁移、不回填**,应用层写路径就此断开。
    # ⬆ 申万分类日更已由 S2 挂上(见上一行);S3 在此后挂**事实包构建冻结**。

    logger.info("增量更新完成:%s", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
