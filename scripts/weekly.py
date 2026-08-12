#!/usr/bin/env python3
"""周度作业(plan §五 V2.2-④-E,承 V2.1-⑥ **逐字继承**;§七 **P3-42** 在本版结案)。

**形态 = 候选 ③「折中」**(V2.1-⑥ planner 裁定,⛔ 不重新裁定):**自动只跑不依赖交割单
的三件**,**对账仍等用户上传**。

    步 1  画像批算            —— 复用 `neckline/profile/{preference,capability,store}`
    步 2  周度校准报告落盘     —— 复用 `neckline/eval/calibration.{build_report,write_report}`
    步 3  双时钟对账 + 归因    —— V2.2-④ 新增:交易时钟对账 → 校准产物自带 `iteration` 段
    步 4  OUT 研究影子对照复核 —— V2.3.2-③-B:**本作业唯一一次 LLM 调用**
    步 5  竞价周度机械聚合     —— V2.3.3-⑥-B:🔴 **零 LLM**(读两张表 + 一次分组)

**为什么没有第四步「对账」**:周复盘对账的必需输入(券商交割单)**只能由用户手动给**
(§八 第 7 项)—— 排了 timer 也补不出没上传的那一份。没有交割单那周的答案是:输出画像
+ 校准两件,对账段如实 `found=false`「本周尚未上传交割单」。

**三步各自 try/except、一步失败另一步照跑**(承晚间链的保险丝哲学);
**只要有任一步失败 → `exit 1`** —— 让 `ExecMainStatus` 说真话,验收才有判据
(§铁律:`timer 跑过 ≠ 任务成功`,以 `ExecMainStatus` + 时间戳为准)。

⛔ **不推送**:本作业不新增任何推送 `kind` → 不触发「新增推送须用户拍板」这条纪律。

⛔ **零写选股包**:步 3 只**产出建议**(`eval/iteration.py`),`selection_packs` 一个字
都不写(V2.1 裁定 #3 / K8 §十七「用户确认后,新规则从下一版本生效」);AST 守门单测锁死。

用法::

    python scripts/weekly.py                       # 上一个完整自然周(systemd 走这条)
    python scripts/weekly.py --week 20260803       # 含该日的那一周
    python scripts/weekly.py --skip-profile        # 只跑校准 + 双时钟
    python scripts/weekly.py --no-placebo          # 跳过安慰剂对照臂(快)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.eval import calibration  # noqa: E402
from neckline.eval.placebo import PLACEBO_DRAWS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("weekly")

#: 画像回看窗口(天)。**V2.1-⑥ 逐字继承的默认**(「窗口默认回看 90 天」),
#: 与 `scripts/profile.py --window-days` 的缺省同值 —— ⛔ 不在这里换一个。
PROFILE_WINDOW_DAYS = 90


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _target_week(args: argparse.Namespace) -> date:
    """作业默认跑**上一个完整自然周**:timer 在周六 09:00 触发,那一刻「本周」的
    周五刚收盘、周六周日还没到 —— 取上一周才是一个走完的窗口。⚠ 显式 `--week`
    优先(回放 / 补跑用)。"""
    if args.week:
        return _parse_date(args.week)
    return date.today() - timedelta(days=7)


def step_profile(as_of: date, db_path: Path, parquet_dir: Optional[Path]) -> str:
    """步 1:画像批算落表(**复用既有实现,⛔ 不重写**)。重跑幂等:同 `as_of` 覆盖。"""
    from neckline.profile import capability as cap
    from neckline.profile import preference as pref
    from neckline.profile import store as profile_store

    lo = (as_of - timedelta(days=PROFILE_WINDOW_DAYS)).strftime("%Y%m%d")
    hi = as_of.strftime("%Y%m%d")
    pref_rows = pref.compute_preference(lo, hi, db_path=db_path)
    cap_rows = cap.compute_capability(lo, hi, db_path=db_path, parquet_dir=parquet_dir)
    n1 = profile_store.save_preference(hi, pref_rows, db_path=db_path)
    n2 = profile_store.save_capability(hi, cap_rows, db_path=db_path)
    return f"画像批算 as_of={hi}:偏好 {n1} 行 / 能力 {n2} 行(窗口 {lo}→{hi})"


def step_trade_clocks(as_of: date, db_path: Path, parquet_dir: Optional[Path]) -> str:
    """步 3a:交易时钟对账(**以 `positions` 为唯一真相**,幂等)。

    ⚠ 顺序刻意在**校准报告之前**:校准产物里的 `iteration` 段要读交易时钟,先对账
    才不会把「今天刚结案的那笔」漏在窗口外。

    ⚠ `as_of` 传的是**今天**、不是那个周锚点(调用方负责):对账问的是「**此刻**
    持仓表长什么样」,不是"那一周的持仓表" —— 拿历史日期去对当前的账,会把
    `daily_check` 事件盖上一个过去的日戳。周锚点只用来定校准窗口。
    """
    from neckline.review.trade_clock import sync_from_positions

    res = sync_from_positions(as_of, db_path=db_path, parquet_dir=parquet_dir)
    for n in res.notes:
        logger.warning("  ⚠ %s", n)
    return (f"交易时钟对账 as_of={res.as_of}:新建 {res.opened} / 结案 {res.closed} / "
            f"事件 +{res.events} / 运行中 {res.running}")


def step_calibration(anchor: date, db_path: Path, parquet_dir: Optional[Path], *,
                     out_dir: Path, draws: int, with_placebo: bool,
                     with_tradable: bool, with_auction: bool = True) -> Tuple[str, List[str]]:
    """步 2 + 步 3b:周度校准报告落盘(**自带 V2.2-④ 的双时钟成绩单与四分类建议**)。

    ⚠ 落盘命名由 `calibration.write_report` 定死(`calibration_{from}_{to}.{md,json}`)
    —— `review/handoff.py` 与 `/eval/weekly` 都按这个约定**读产物**,⛔ 不另起一套。

    **返回 `(摘要, 降级段列表)`**。🔴 第二项是 §七 **P0-56** 加的:`build_report`
    「永不抛异常、炸了只记 note」——这个设计本身没问题(一段炸了不该拖垮另一段),
    **但它此前让整个作业以 `exit 0` 收场** —— 安慰剂对照臂、可交易判分、分层成绩单
    全炸掉,日志末行仍是「周度作业完成(全部步骤成功)」。铁律说「验收看
    `ExecMainStatus=0` 且本次时间戳」,那个绿灯于是盖在一次被掏空的跑上。
    ⛔ **别把这里改回只返回摘要** —— 报告照落盘(有多少算多少)、退出码照说真话,
    两件事都要。
    """
    lo, hi = calibration.week_bounds(anchor)
    if lo is None:
        return (f"{anchor} 所在那一周没有交易日,本周无校准窗口(如实跳过,不算失败)", [])
    report = calibration.build_report(
        lo, hi, db_path=db_path, parquet_dir=parquet_dir,
        with_tradable=with_tradable, with_placebo=with_placebo,
        with_auction=with_auction, draws=int(draws),
    )
    paths = calibration.write_report(report, out_dir)
    for n in report.notes:
        logger.warning("  ⚠ %s", n)
    it = report.iteration or {}
    th = (it.get("thresholds") or {})
    if not th.get("available"):
        logger.warning("  ⚠ 四分类分界线未配置 —— 本期只出统计量、**不给分类**"
                       "(骨架包 `config.iteration` 待用户拍板)")
    if report.degraded:
        logger.error("  🔴 本期有 %d 段**没跑成**:%s —— 报告已落盘但内容不完整,"
                     "本步按失败计(§七 P0-56)", len(report.degraded), ", ".join(report.degraded))
    return (f"周度校准报告 {report.date_from}→{report.date_to}:"
            f"{report.n_trading_days} 个交易日 / {report.n_baskets} 篮 / "
            f"{len(report.strata)} 层;选股时钟结案 "
            f"{(it.get('samples') or {}).get('selectionClock', 0)} 篮、"
            f"建议 {len(it.get('suggestions') or [])} 行 → {paths['markdown']}",
            list(report.degraded))


def step_out_shadow_review(anchor: date, db_path: Path) -> str:
    """步 4:OUT 研究影子对照的**周度 LLM 集中复核**(V2.3.2-③-B;K8 §十四)。

    🔴 **这是本版唯一新增的一次 LLM 调用**(一次管八只,⛔ 不逐票调用)——
    它顶到 `neckline-weekly.service::TimeoutStartSec` 与 `REVIEW_BUDGET_SECONDS`
    的关系上,unit 文件头有专门一节写这件事,改配额前先读那一节。

    ⛔ **只出研究结论**:不改 OUT 身份、不进 T1/T2、不计入正式样本、不产生任何交易
    动作(K8 §十四 逐字)。`provider=None`(无 key)→ 只出机械读数,不算失败。"""
    from neckline.llm.budget import BudgetLedger
    from neckline.llm.factory import get_provider
    from neckline.llm.router import TASK_REVIEW
    from neckline.review import out_shadow

    lo, hi = calibration.week_bounds(anchor)
    if lo is None:
        return f"{anchor} 所在那一周没有交易日,本周无 OUT 复核窗口(如实跳过)"
    try:
        provider = get_provider(TASK_REVIEW, db_path=db_path)
    except Exception:  # noqa: BLE001
        provider = None
        logger.warning("  ⚠ OUT 复核:LLM provider 取不到,本期只出机械读数", exc_info=True)
    res = out_shadow.review_week(
        anchor, lo, hi, provider=provider, ledger=BudgetLedger(), db_path=db_path)
    for n in res.notes:
        logger.warning("  ⚠ %s", n)
    scope = res.scope
    return (f"OUT 研究影子对照复核 {res.window[0]}→{res.window[1]}:"
            f"窗口内 OUT {res.universe} 只,复核 {res.reviewed} 只"
            f"({scope.top_n if scope else 0} 最强 + {scope.random_n if scope else 0} 随机"
            f"{',已扩大' if (scope and scope.expanded) else ''}),"
            f"明显错杀 {res.obvious_miskill} 只;LLM 段 {res.llm_stage}"
            f"{'(已落表)' if res.persisted else '(本周行已存在,未覆盖)'}")


def step_auction_eval(anchor: date, db_path: Path) -> str:
    """步 5:D1 集合竞价确认层的**周度机械聚合**(V2.3.3-⑥-B;K8 §二十 末段)。

    🔴 **零 LLM 调用**(与步 4 刻意相反):它只读两张 SQLite 表 + 做一次分组 ——
    `neckline-weekly.service::TimeoutStartSec` 刚由 1800 调到 3000(V2.3.2 批 ⑥ 实测),
    再塞一次 LLM 会立刻破配额账,故本步**永远不许**加模型调用。

    ⚠ 六个标签不在这里判:它们是 D1 收盘时由 `review/selection_clock.py` 的**第十项**
    判好、冻进 `selection_clock.mech_json` 的 —— 本步只**数**(⛔ 不重判)。

    ⚠ **同一份产物已随步 2 落盘**(`calibration.build_report` 的 `auction` 段,同一个
    函数、同一个窗口 → 逐位相同)。本步存在的意义是让它在 journal 里**有一句可核**
    + 让"这一步跑没跑成"有独立的退出码信号。`--skip-auction-eval` 两处一起关。

    🔴 **⛔ 零自动回写**:不改 K8、不改选股包、不改任何阈值(K8 §二十 末段逐字)。
    """
    from neckline.eval.auction_eval import build_auction_section

    lo, hi = calibration.week_bounds(anchor)
    if lo is None:
        return f"{anchor} 所在那一周没有交易日,本周无竞价聚合窗口(如实跳过)"
    section = build_auction_section(lo, hi, db_path=db_path)
    overall = section.get("overall") or {}
    counts = overall.get("counts") or {}
    focus = overall.get("focus") or {}
    th = section.get("thresholds") or {}
    if not th.get("available"):
        logger.warning("  ⚠ 竞价聚合:样本分界线未配置 —— 本期**只输出观察**、不提任何建议"
                       "(骨架包 `config.iteration` 待用户拍板)")
    return (f"竞价周度聚合 {lo}→{hi}:样本 {overall.get('n', 0)} 个 / "
            f"{len(section.get('byCell') or [])} 个 (行情状态 × 等级 × 引擎 × 版本) 单元格;"
            f"正确确认 {counts.get('correct_confirm', 0)} / 错误确认 {focus.get('wrong_confirm', 0)} / "
            f"中性 {counts.get('neutral_sample', 0)} / 正确否决 {counts.get('correct_veto', 0)} / "
            f"错误否决 {focus.get('wrong_veto', 0)} / 数据缺失 {counts.get('data_missing', 0)};"
            f"样本量闸 {overall.get('gate')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--week", help="含该日的那一周 YYYYMMDD(缺省 = 上一个完整自然周)")
    parser.add_argument("--out", help="校准产物目录(缺省 data/reports/calibration)")
    parser.add_argument("--draws", type=int, default=PLACEBO_DRAWS, help="对照臂每日抽样次数")
    parser.add_argument("--no-placebo", action="store_true", help="跳过安慰剂对照臂")
    parser.add_argument("--no-tradable", action="store_true", help="跳过可交易收益判分")
    parser.add_argument("--skip-profile", action="store_true", help="跳过步 1 画像批算")
    parser.add_argument("--skip-clocks", action="store_true", help="跳过步 3a 交易时钟对账")
    parser.add_argument("--skip-out-review", action="store_true",
                        help="跳过步 4 OUT 研究影子对照周度复核(唯一一次 LLM 调用)")
    parser.add_argument("--skip-auction-eval", action="store_true",
                        help="跳过步 5 竞价周度机械聚合(**同时**让校准产物不带 auction 段)")
    parser.add_argument("--db", dest="db", help="SQLite 路径(缺省 settings.db_path)")
    parser.add_argument("--parquet-dir", dest="parquet_dir", help="parquet 根目录(缺省 settings)")
    args = parser.parse_args()

    ensure_data_dirs()
    db_path = Path(args.db) if args.db else settings.db_path
    parquet_dir = Path(args.parquet_dir) if args.parquet_dir else None
    out_dir = Path(args.out) if args.out else (settings.data_dir / "reports" / "calibration")
    anchor = _target_week(args)
    logger.info("周度作业开始:目标周锚点 %s", anchor)

    failures: List[str] = []

    if args.skip_profile:
        logger.info("步 1 画像批算:--skip-profile,跳过")
    else:
        try:
            logger.info("步 1 %s", step_profile(anchor, db_path, parquet_dir))
        except Exception as exc:  # noqa: BLE001 —— 一步失败另一步照跑
            logger.error("步 1 画像批算失败:%s: %s", type(exc).__name__, exc, exc_info=True)
            failures.append("profile")

    if args.skip_clocks:
        logger.info("步 3a 交易时钟对账:--skip-clocks,跳过")
    else:
        try:
            # ⚠ 传**今天**不是周锚点 —— 见 `step_trade_clocks` docstring。
            logger.info("步 3a %s", step_trade_clocks(date.today(), db_path, parquet_dir))
        except Exception as exc:  # noqa: BLE001
            logger.error("步 3a 交易时钟对账失败:%s: %s", type(exc).__name__, exc, exc_info=True)
            failures.append("trade_clock")

    try:
        summary, degraded = step_calibration(
            anchor, db_path, parquet_dir, out_dir=out_dir, draws=args.draws,
            with_placebo=not args.no_placebo, with_tradable=not args.no_tradable,
            with_auction=not args.skip_auction_eval)
        logger.info("步 2/3b %s", summary)
        # 🔴 P0-56:`build_report` 不抛异常,所以"段炸掉"到不了上面那个 except ——
        # 必须在这里显式并进 `failures`,否则退出码会替一次被掏空的跑背书。
        if degraded:
            failures.append("calibration(降级段:" + ", ".join(degraded) + ")")
    except Exception as exc:  # noqa: BLE001
        logger.error("步 2/3b 周度校准报告失败:%s: %s", type(exc).__name__, exc, exc_info=True)
        failures.append("calibration")

    if args.skip_out_review:
        logger.info("步 4 OUT 研究影子对照复核:--skip-out-review,跳过")
    else:
        try:
            logger.info("步 4 %s", step_out_shadow_review(anchor, db_path))
        except Exception as exc:  # noqa: BLE001
            logger.error("步 4 OUT 研究影子对照复核失败:%s: %s",
                         type(exc).__name__, exc, exc_info=True)
            failures.append("out_shadow_review")

    if args.skip_auction_eval:
        logger.info("步 5 竞价周度机械聚合:--skip-auction-eval,跳过")
    else:
        try:
            logger.info("步 5 %s", step_auction_eval(anchor, db_path))
        except Exception as exc:  # noqa: BLE001 —— 一步失败另一步照跑
            logger.error("步 5 竞价周度机械聚合失败:%s: %s",
                         type(exc).__name__, exc, exc_info=True)
            failures.append("auction_eval")

    if failures:
        # 🔴 让 `ExecMainStatus` 说真话:任一步失败即非零退出(§铁律「timer 跑过 ≠
        # 任务成功」—— 验收看的就是这个码 + 本次那一跑的时间戳)。
        logger.error("周度作业有 %d 步失败:%s", len(failures), ", ".join(failures))
        return 1
    logger.info("周度作业完成(全部步骤成功)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
