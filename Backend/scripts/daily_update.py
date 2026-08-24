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

from neckline.calendar import official_is_trading_day, reset_cache  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.db import init_schema  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_update")

LIMIT_DERIVED_TRAILING_DAYS = 15  # 尾部重算窗口(交易日),覆盖连板计数跨批次边界


def update_sw_industry() -> None:
    """V2.5.0 S2:申万 2021 版行业分类日更(`sw_industry_classify` / `sw_industry_member`)。

    🔴 **判据输入,不是增强项**:K9 全文的「相对强度」以申万**二级**行业当日成员涨跌幅
    中位数为基准(裁定 2),第三层排序的「行业热度分」也读它。故与 `update_suspend_list`
    同样**尽力而为不改退出码**,但**日志级别用 ERROR**
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


def verify_readiness(target: date) -> bool:
    """日更的最终判据：19:00 消费前必须通过只读就绪检查。"""
    from neckline.facts import readiness

    result = readiness.preflight(target)
    if result.ready:
        logger.info("[readiness] %s 已就绪，冻结包=%s", target, result.pack_id)
        return True
    logger.error("[readiness] %s 未就绪，晚间链将只产出 not_run：%s", target, "；".join(result.gaps))
    return False


def refresh_coverage(target: date) -> None:
    """V2.5.0 S4:覆盖率成绩线(PROJECT_PLAN §5.8.1)。

    🔴 **它是尺子**:以涨停为口径,⛔ 不读任何待标定参数,参数标定完成之前就能跑。
    必须排在事实包冻结**之后**(它读那份冻结包)。

    ⚠ **S7 起 `listing` / `dispositions` 真的接上了**(S4 登记的那条「清单开始产出
    的次日自动接上」):`report/evening.py::coverage_inputs` 把 **D−1** 的 K9 清单与
    全市场 disposition 翻成覆盖率层的 DTO。两者仍可能是 `None`(上线首日 / 昨天没跑
    成),那时 `coverage_all` 照旧落 **NULL**(⛔ 不是 0)。

    🔴 **接线为什么在编排器里**:守门单测断言 `scorecard/**` 零 import `neckline.k9`
    —— 尺子不许读被量的东西。策略侧信息只经 `k9_disposition` / `k9_listing_entries`
    这条**数据**通道进来。⛔ 别把 `coverage_inputs` 搬进 `scorecard/`。

    ⚠ **Plan 没写覆盖率挂在哪条链上**(§9.3 的晚间段序是 facts→k9→explain→playbook
    →report,没有 scorecard 段)。本片挂在 16:05 日更、紧随事实包冻结之后 ——
    它只读当日那一份冻结包,是秒级动作,不值得为它新增一个段。已登记进 §14。

    尽力而为**不改退出码**;它是成绩线不是判据输入,失败打 WARNING。
    """
    from neckline.report.evening import coverage_inputs
    from neckline.scorecard import coverage as coverage_mod

    try:
        listing, dispositions = coverage_inputs(target)
        day = coverage_mod.refresh_day(
            target, listing=listing, dispositions=dispositions)
    except Exception:  # noqa: BLE001
        logger.warning("[coverage] %s 覆盖率刷新异常(已吞,不阻断主增量)", target, exc_info=True)
        return
    if day is None:
        logger.info("[coverage] %s 无冻结事实包,本日无覆盖率(⛔ 不编一行 0)", target)
        return
    logger.info(
        "[coverage] %s 涨停 %d 只;昨日清单命中率 %s",
        target, day.limit_up_count,
        "NULL(昨天没有清单)" if day.coverage_all is None else f"{day.coverage_all:.1%}",
    )


def refresh_listing_scorecards(target: date) -> None:
    """当日事实包到位后补齐所有已走完 D2 的 K9-v2 清单成绩。"""
    from neckline.scorecard import listing

    try:
        opened = listing.open_due(target)
        count = listing.refresh_due(target)
    except Exception:  # noqa: BLE001
        logger.warning("[listing_scorecard] %s 刷新异常(已吞,不阻断行情日更)",
                       target, exc_info=True)
        return
    logger.info("[listing_scorecard] 截至 %s 已恢复 %d 条预测、刷新 %d 个清单日",
                target, opened, count)


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
    """尽力获取当日龙虎榜并落日分区；失败由 fp-3 冻结为 unavailable。

    龙虎榜只给 K9-v2 P3 做附加证据，缺失不能阻断事实包，更不能被解释为未上榜。
    """
    from neckline.data.top_list import load_top_list

    try:
        frame = load_top_list(target, fetch_if_missing=True)
        from neckline.data.market_data import day_file_exists
        if day_file_exists("top_list", target):
            logger.info("[top_list] %s 已查，%d 行", target, frame.height)
        else:
            logger.warning("[top_list] %s 数据不可用；fp-3 将明确记录 unavailable", target)
    except Exception:  # noqa: BLE001
        logger.warning("[top_list] %s 获取异常；fp-3 将明确记录 unavailable", target,
                       exc_info=True)


def main() -> int:
    if not settings.tushare_token:
        logger.error("TUSHARE_TOKEN 缺失(.env),无法拉取。")
        return 1

    ensure_data_dirs()
    init_schema()
    reset_cache()

    target = datetime.strptime(sys.argv[1], "%Y%m%d").date() if len(sys.argv) > 1 else date.today()

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
    update_sw_industry()
    # V2.5.0 S3:事实包构建 + 冻结(架构第一层)。必须排在 `update_sw_industry` **之后**
    # (申万归属是中位数的输入)与全部行情落盘之后(完整性判定要看当日分区)。
    build_and_freeze_fact_pack(target)
    if not verify_readiness(target):
        # 16:05 进程必须让 systemd 看见失败；不得把半套数据伪装成成功更新。
        return 1
    # V2.5.0 S4:覆盖率成绩线(尺子)。必须排在事实包冻结**之后** —— 它读那份冻结包。
    refresh_coverage(target)
    refresh_listing_scorecards(target)

    logger.info("增量更新完成:%s", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
