#!/usr/bin/env python3
"""盘后报告 CLI(plan 2.5)。串起情绪仪表盘(2.1)+ 强势板块(2.2)+ 持仓体检 + 情报件 /
板块资金流 / 消息面 -> markdown 落文件 + SQLite 存档(2.5)。

⚠ **V2-⑬ 过渡态**:候选评分四件套 + 20 只 LLM 审判 + 参考件三件套已按 §五 V2-⑬-1/2/3
删除;篮子日报的编排是 **⑭-A** 的活。此刻产出的是一份只剩市场语境与持仓体检的过渡报告。

用法:
    python scripts/report.py                # 最近一个已有数据的交易日
                                             # (今天若是交易日用今天,否则回退到上一交易日)
    python scripts/report.py 20260717        # 指定某交易日(含历史任意交易日回放,§2.6)
    python scripts/report.py 20260717 --no-save       # 只打印,不落库/不写文件(调试用)


产出:
    - markdown 打印到 stdout
    - 落文件 data/reports/<trade_date>.md(gitignored)
    - 落库 SQLite(`neckline.report.store`,幂等覆盖同一交易日/同一候选)

依赖:候选评分从策略大脑现役版本读规则(§2.6/§3.8 同码铁律),须先跑
    python -m research.rule_v1 --commit
落地 `strategy_versions` 表至少一个 `is_active=1` 版本,否则本脚本会报错退出
(这是配置缺陷,不是可优雅降级的场景——报告没有规则基础,生成出来的候选毫无意义)。
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
from neckline.report.pipeline import build_report  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("report")

REPORTS_DIR = settings.data_dir / "reports"


def _default_trade_date() -> date:
    today = date.today()
    return today if is_trading_day(today) else prev_trading_day(today)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trade_date", nargs="?", default=None, help="YYYYMMDD;缺省=最近一个已有数据的交易日")
    parser.add_argument("--no-save", action="store_true", help="只打印,不写文件/不落库(调试用)")
    # ⚠ **V2-⑬-1/⑬-2**:`--top-total`/`--top-judged` 两个参数随 20 只候选榜与单票 LLM
    # 审判一并删除(候选榜没了,"取前几只/审几只"无对象)。⑭-A 上篮子日报后,若需要
    # "只出前几档篮子"这类开关,那是新参数、不要复活这两个名字。
    parser.add_argument("--notify", action="store_true",
                        help="落库后触发 APNs 报告推送(受 kind=report_ready 开关);16:00 timer 用")
    args = parser.parse_args()

    ensure_data_dirs()

    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y%m%d").date()
    else:
        trade_date = _default_trade_date()

    if not is_trading_day(trade_date):
        logger.error("%s 不是交易日,无报告可生成。", trade_date)
        return 1

    logger.info("生成报告:%s(save=%s)", trade_date, not args.no_save)
    try:
        bundle = build_report(trade_date, save=not args.no_save)
    except RuntimeError as e:
        logger.error("生成报告失败:%s", e)
        return 1

    if not args.no_save:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = REPORTS_DIR / f"{trade_date.strftime('%Y%m%d')}.md"
        out_path.write_text(bundle.markdown, encoding="utf-8")
        logger.info("报告已写入 %s,并已落库 SQLite `reports` 表。", out_path)

        if args.notify:
            # APNs 报告推送(plan 4B.5;受 kind=`report_ready` 开关 + 无设备/无 APNs 配置优雅跳过,
            # 绝不因推送失败让报告任务失败)。
            try:
                from neckline.api.notify import push_report_ready
                outcome = push_report_ready(trade_date.strftime("%Y-%m-%d"))
                logger.info("APNs 报告推送:sent=%d failed=%d%s",
                            outcome.sent, outcome.failed,
                            f" skipped={outcome.skipped_reason}" if outcome.skipped_reason else "")
            except Exception:  # noqa: BLE001
                logger.warning("APNs 报告推送异常(已吞,不影响报告落库)", exc_info=True)

            # K4 持仓派发警报推送(受 kind=`holding_alert` 开关,V2-⑪ 起「重要不紧急」级)。只推**强价量证据**
            # 命中(年线下涨停/放量大阳派发/换手>10%);题材天数=弱证据只进看板不推(§2.4)。逐仓
            # 一条(≤3 仓),同样优雅跳过、绝不因推送失败让报告任务失败。
            try:
                from neckline.api.notify import push_holding_alert
                pushed = 0
                for it in bundle.holding_k4_check:
                    if not it.has_strong:
                        continue
                    outcome = push_holding_alert(it.name, it.ts_code, it.strong_price_volume_labels())
                    pushed += outcome.sent
                logger.info("APNs 持仓派发警报:强警示持仓 %d 只,sent=%d",
                            sum(1 for it in bundle.holding_k4_check if it.has_strong), pushed)
            except Exception:  # noqa: BLE001
                logger.warning("APNs 持仓派发警报异常(已吞,不影响报告落库)", exc_info=True)

    print(bundle.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
