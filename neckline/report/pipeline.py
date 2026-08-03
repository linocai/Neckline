"""报告管线编排(plan 2.5,§2.6 历史回放的天然落地点)。串起情绪仪表盘(2.1)+
强势板块(2.2)+ 候选评分(2.3)+ LLM 审判(2.4,plan §五 v1.5-② 起 20 只全覆盖,
预算硬闸 `CANDIDATE_JUDGE_BUDGET_SECONDS`)+ 落库(store.py)+ markdown 渲染
(render.py)——`scripts/report.py` 的核心入口就是本模块的 `build_report`。

**历史回放(§2.6)天然支持**:`build_report(trade_date, ...)` 对任意历史交易日都
能跑(只要该日的 Parquet/SQLite 数据已落地),不需要另一套"回放专用"代码——
喂历史 = 回测、喂今日 = 报告,这里的"喂哪天"只是一个参数,同一份代码路径。

**策略大脑是唯一权威**(2026-07-20 用户拍板,凌驾于本文件以外的任何假设):
候选评分的规则从 `neckline.strategy.brain.get_active()` 读,大脑无现役版本时
**拒绝生成报告并抛出清晰错误**(这是配置缺陷,不是"优雅降级"的场景——报告没有
规则基础,生成出来的候选毫无意义,不该假装正常跑完)。
"""

from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline import decision_log
from neckline.data.top_list import top_list_lookup
from neckline.sentinel import positions as pos_store
from neckline.llm.base import LLMProvider
from neckline.llm.factory import get_provider
from neckline.llm.judge import JudgeResult, judge_candidate
from neckline.report import store
from neckline.report.candidates import Candidate
from neckline.report.intel_candidates import build_intel_candidates
from neckline.report.intel import IntelReport, compute_intel, empty_intel_report
from neckline.report.news_alerts import NewsAlertsReport, build_news_alerts, empty_news_alerts_report
from neckline.report.pending_track import track_pending_decisions
from neckline.report.render import render_markdown
from neckline.report.sectors import (
    SECTOR_DATA_STALE_MAX_LAG_DAYS,
    SectorDataFreshness,
    SectorScore,
    compute_sector_freshness,
    compute_sector_strength,
    load_index_names,
    load_member_map,
)
from neckline.report.holding_k4_check import HoldingK4Item, build_holding_k4_check
from neckline.report.industry_strength import load_industry_map
from neckline.report.industry_strength_store import (
    IndustryStrengthFreshness,
    industry_strength_status,
    load_industry_strength,
    refresh_command_hint,
)
from neckline.report.exec_hint import attach_exec_hints
from neckline.report.info_card import attach_info_card_summaries
from neckline.report import reference_plan_store
from neckline.report.reference_plan import judge_and_build_reference_plan
from neckline.report.sector_moneyflow import (
    SectorMoneyflowReport,
    compute_sector_moneyflow,
    empty_sector_moneyflow_report,
)
from neckline.report.sentiment import SentimentDashboard, compute_sentiment
from neckline.strategy import brain

logger = logging.getLogger(__name__)

TOP_N_TOTAL = 20
# v1.5-②-A(需求 9「20只全覆盖」):「前10只审、后10只只给分数不耗LLM」的旧分档
# 退役——`TOP_N_JUDGED` 与 `TOP_N_TOTAL` 现在相等,每票或出三件套、或出不买理由。
# 两个常量维持独立命名(不合并成一个)是刻意的:`top_n_judged` 仍是 `build_report`
# 的独立可覆盖参数,历史/未来若需要重开分档(如成本吃紧要收窄覆盖面)只改这一个
# 默认值,不必牵动 `TOP_N_TOTAL`(候选生成 universe 大小)。
TOP_N_JUDGED = 20

# v1.5-②-B(需求 9,plan §五「v1.5 LLM 预算账」):候选 LLM 审判墙钟预算,20 分钟。
# **与 `news_alerts.LLM_SCAN_BUDGET_SECONDS`(300s)是两本独立账,不共享、不合并**
# ——一个吃光另一个是最难查的那类故障(plan 原文)。20 只 × (信息卡取数 2.25~3.05s +
# 带搜索LLM调用 30~70s)串行最坏情形 ≈ 1461s,略超本预算——这是**刻意的**:预算硬闸
# 存在的意义就是在最坏情形下先止损(牺牲排名靠后的候选),不是保证 20 只都能审完。
# 具体测算见 PROJECT_PLAN.md「v1.5 LLM 预算账」一节(②-D 已回填实测/估算数字)。
CANDIDATE_JUDGE_BUDGET_SECONDS = 1200.0


@dataclass
class ReportBundle:
    trade_date: date
    strategy_version: str
    generated_at: str
    sentiment: SentimentDashboard
    sectors: List[SectorScore]
    candidates: List[Candidate]
    # ts_code -> JudgeResult:前 top_n_judged 只中**实际发起过调用**的那些(v1.5-②起
    # top_n_judged 恒等于 top_n_total,即"全部候选"这个集合)。预算耗尽后被跳过、
    # 没发起调用的候选不在这个字典里——查 `Candidate.judge_skipped` 分辨"没审"
    # 是否属于这一种(而非本函数/pipeline 自身的其他 bug)。
    judged: Dict[str, JudgeResult]
    markdown: str
    missed_entry_hint: str = ""     # v1.1-B.4 漏录兜底提示(无 → 空串)
    holding_k4_check: List[HoldingK4Item] = field(default_factory=list)      # v1.3-② 持仓 K4 体检 + D5 净浮盈
    intel: Optional[IntelReport] = None                    # v1.3-③-C1 复盘情报件(不阻断,失败降级见 empty_intel_report)
    sector_moneyflow: Optional[SectorMoneyflowReport] = None  # v1.3-③-C2 板块资金流(拥挤情报,非选股信号)
    news_alerts: Optional[NewsAlertsReport] = None          # v1.3-③-C4 消息面扫描(不阻断,失败降级见 empty_news_alerts_report)
    # v1.4-①-C 板块数据新鲜度(§七 P0-3):「当日暴起板块」与「题材持续天数」两路的可信度
    # 前提。**过期时必须显式标不可信,不静默降级为空**——「没有」和「没看」必须能分开。
    sector_freshness: Optional[SectorDataFreshness] = None
    # v1.4-⑩-F 行业强度数据新鲜度(§七 P0-23):与板块新鲜度是**两个独立故障**(一个是
    # `ths_daily` 概念板块日更,一个是 `industry_strength_daily` 预计算表),不许合并成
    # 一个 bool —— 合并就分不清哪个坏了。
    industry_freshness: Optional[IndustryStrengthFreshness] = None


def compute_missed_entry_hint(trade_date: date, db_path: Optional[Path] = None) -> str:
    """漏录兜底(plan v1.1-B.4):当日买点哨兵触发过 ≥1 次(`sentinel_events` 有
    sentinel=`entry` 记录)但 `positions` 表当日无新增开仓 → 返回一句提示,否则空串。
    **不改评分**,纯只读旁路。GET /report 与 build_report 共用本函数(单一源),前者
    每次读时实时算(用户补录后自动消失)。"""
    from neckline.sentinel.dedup import count_pushed_today
    from neckline.sentinel.positions import count_opens_on

    entry_events = count_pushed_today(trade_date, sentinel="entry", db_path=db_path)
    if entry_events <= 0:
        return ""
    if count_opens_on(trade_date, db_path=db_path) > 0:
        return ""
    return (
        f"今日 {entry_events} 只候选触达买点但台账无补录,"
        f"如已买入请补录 / 未买入请确认为何未执行。"
    )


def _scenario_review_position_ids(db_path: Optional[Path] = None) -> set:
    """有非空情景树待每日对照的持仓 position_id 集合(plan §五 v1.3-②-D「把待对照的持仓/
    决策挑出来」)。= 已成交(filled)且关联了持仓、且⑦情景树非空的决策日志的 position_id。
    **只读挑出,勾选兑现仍走既有 `POST /decisions/{id}/scenario-outcome`**(无新写路径)。"""
    ids: set = set()
    for d in decision_log.list_decisions(status=decision_log.STATUS_FILLED, db_path=db_path):
        if d.position_id is not None and d.contingency_scenarios:
            ids.add(d.position_id)
    return ids


def _judge_candidates_with_budget(
    candidates: List[Candidate],
    trade_date: date,
    *,
    provider: Optional[LLMProvider],
    top_list: Dict[str, dict],
    industry_scores: Optional[List[Any]],
    industry_map: Optional[Dict[str, str]],
    transport: Optional[Any],
    budget_seconds: float,
    save: bool,
    parquet_dir: Optional[Path],
    db_path: Optional[Path],
) -> Dict[str, JudgeResult]:
    """v1.5-② 候选 LLM 审判 + 参考件三件套的**预算感知**编排(需求 9「20 只全覆盖」)。
    `candidates` 须已按 rank 升序排好(调用方传 `candidates[:top_n_judged]`)——**按
    rank 升序逐一发起**(①-B 的单次调用机制`judge_and_build_reference_plan`一字不动,
    本函数只加一层墙钟预算计时 + 跳过记账)。

    **降级优先级(plan §五「v1.5 LLM 预算账」定死,不折中)**:预算耗尽时先牺牲
    **排名靠后的候选**——从耗尽的那一只起,`candidates` 尾部**全部**标
    `judge_skipped=True` 且**不发起调用**(不是"跳过这一只、继续试下一只";预算
    耗尽是单调的,没有理由认为下一只会更快)。**绝不牺牲已经在审的靠前候选**,
    也绝不因为参考件/审判异常而回头重试——异常走既有的逐票 try/except 退回普通
    审判(同 v1.5-① 姿势),预算计时不因异常处理而额外消耗。

    `judge_skipped` 与 `JudgeResult.degraded` 语义不同、不许合并(承
    `news_alerts.py` 的 `codes_skipped`/`codes_failed` 同一纪律,见
    `Candidate.judge_skipped` 字段注释)——本函数返回的 `judged` 字典**只含
    实际发起过调用的候选**,跳过的不在字典里,调用方从 `Candidate.judge_skipped`
    (本函数原地在候选对象上打的标)分辨"为什么这一只没有 `judged` 条目"。

    并发方案(v1.5-②-C,plan §五「不许拍脑袋开并发」,承 v1.4-⑥-B `news_alerts.py`
    先例原样照做):**本函数是纯串行实现**,理由与 `news_alerts._scan_llm_categories`
    完全一致(同一套 `llm/openai_compat.py` HTTP 层、GLM/Kimi 真实分钟级限频未经
    实测)——**施工期(2026-07-29)本地无任何可用 GLM key**(`.env`/环境变量/
    `app_settings.llm_api_key` 均空,已核实),限频无法实测,按"不许拍脑袋开并发"
    取保守分支 = 串行 + 预算硬闸。并发路留待有 key 时先实测 2/3/4 并发的 429
    率与稳定性再评估(届时对照 `news_alerts.py` 模块头(a)(b)(c) 三条理由逐条
    回答),不在本次顺手做。

    副作用(`save=True` 时):逐票落 `llm_judgments`/`reference_plans`;**预算耗尽时
    反过来删掉被跳过那批码当日的既有行**(v1.5.1,契约线 review 🟡-1,理由见循环内
    注释与 `store.delete_llm_judgments` docstring);原地在 `candidates` 列表的
    `Candidate` 对象上补 `.reference_plan`/`.judge_skipped`(`build_report` 随后把这批
    `Candidate` 对象整体落 `reports.candidates_json`,同 v1.5-① 既有姿势,本函数不新增
    落库路径)。"""
    judged: Dict[str, JudgeResult] = {}
    budget_start = time.monotonic()
    for i, c in enumerate(candidates):
        if time.monotonic() - budget_start >= budget_seconds:
            skipped_n = len(candidates) - i
            logger.warning(
                "候选 LLM 审判(v1.5-②)墙钟预算耗尽(预算 %.0fs),按 rank 升序审、"
                "剩余 %d 只标记 judgeSkipped 且不再发起调用(降级优先级:牺牲排名"
                "靠后的候选,绝不牺牲已在审的靠前候选,详见「v1.5 LLM 预算账」)",
                budget_seconds, skipped_n,
            )
            for skipped_c in candidates[i:]:
                skipped_c.judge_skipped = True
            if save:
                # v1.5.1(契约线 review 🟡-1):同日重跑 + 本跑预算耗尽时,**删掉**这批
                # 码当日的既有审判/参考件行——`/report` 的 `llmJudgment` 是从
                # `llm_judgments` 现连的,不删就会与 `judgeSkipped=true` 同时出现,
                # 一张卡上「(上一跑的)审判结论」+「本次预算耗尽未发起」互相打脸。
                # 收口在写侧,不在读侧遮蔽(藏真数据不是诚实)。首次生成时两个 DELETE
                # 各删 0 行,幂等无副作用。
                skipped_codes = [c.ts_code for c in candidates[i:]]
                store.delete_llm_judgments(trade_date, skipped_codes, db_path=db_path)
                reference_plan_store.delete_reference_plans(trade_date, skipped_codes, db_path=db_path)
            break
        top_row = top_list.get(c.ts_code)
        try:
            result, plan = judge_and_build_reference_plan(
                c, trade_date, provider=provider, top_list_row=top_row, transport=transport,
                industry_scores=industry_scores, industry_map=industry_map, top_list_t0=top_list,
                parquet_dir=parquet_dir, db_path=db_path,
            )
        except Exception:  # noqa: BLE001 —— 参考件三件套(v1.5-①)整体异常不得连带候选审判失败
            logger.warning(
                "参考件三件套(v1.5-①)整体异常(%s),候选照出、退回不带参考件的普通审判",
                c.ts_code, exc_info=True,
            )
            result = judge_candidate(c, provider=provider, top_list_row=top_row, transport=transport)
            plan = None
        judged[c.ts_code] = result
        if save:
            store.save_llm_judgment(trade_date, result, db_path=db_path)
        if plan is not None:
            c.reference_plan = plan.to_public_dict()
            if save:
                reference_plan_store.save_reference_plans(trade_date, [plan], db_path=db_path)
    return judged


def build_report(
    trade_date: date,
    *,
    llm_provider: Optional[LLMProvider] = None,
    llm_transport: Optional[Any] = None,
    top_n_total: int = TOP_N_TOTAL,
    top_n_judged: int = TOP_N_JUDGED,
    candidate_judge_budget_seconds: float = CANDIDATE_JUDGE_BUDGET_SECONDS,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    save: bool = True,
) -> ReportBundle:
    """生成 `trade_date` 这一天的完整盘后报告。

    `llm_provider`:显式传入用于测试注入(如 `_StubProvider`/真 provider + Mock
    Transport);为 `None`(默认,生产用法)时从 `.env` 现读(`llm.factory.get_provider()`)
    ——本项目当前无 key,现读结果恒为 `None`,LLM 审判段落走「未激活」占位链路。
    `save=False` 供只想拿 `ReportBundle`/跑历史回放对照、不想写库的调用方(如单测)。
    `candidate_judge_budget_seconds`(v1.5-②-B,默认 `CANDIDATE_JUDGE_BUDGET_SECONDS`
    =1200s):候选 LLM 审判的独立墙钟预算,暴露成参数供单测把预算调小以确定性触发
    耗尽路径(同 `news_alerts.build_news_alerts(llm_budget_seconds=...)` 先例)。
    """
    active = brain.get_active(db_path=db_path)
    if active is None:
        raise RuntimeError(
            "策略大脑无现役版本(strategy_versions 表 is_active=1 为空)——先跑 "
            "`python -m research.rule_v1 --commit` 定版规则 v1,再生成报告。"
        )

    sentiment = compute_sentiment(trade_date, parquet_dir=parquet_dir)
    sector_scores = compute_sector_strength(trade_date, parquet_dir=parquet_dir)
    # v1.4-①-C:板块数据新鲜度**独立于日更**先算(P0-3 要求「至少先加过期告警」)。
    # `compute_sector_strength` 无当日行时返空列表且不报错(优雅降级),从报告上看不出
    # 是「今天没行情」还是「板块表根本没更新」—— 这一行就是把两者分开的那个开关。
    sector_freshness = compute_sector_freshness(trade_date, parquet_dir=parquet_dir)
    if sector_freshness.stale:
        logger.warning(
            "板块数据过期:最新至 %s,落后 %s 个交易日(容忍上限 %s)——「当日暴起板块」与"
            "「题材持续天数」本日不可信,报告已显式标注。",
            sector_freshness.sector_data_date or "(无数据)", sector_freshness.lag_days,
            SECTOR_DATA_STALE_MAX_LAG_DAYS,
        )
    member_map = load_member_map(parquet_dir=parquet_dir)
    index_names = load_index_names(parquet_dir=parquet_dir)
    # v1.4-②:行业强度单一源(A2/B3 题材持续天数判据)。持仓 K4 体检 + 候选安检两处共用
    # 同一份(此处只算一次,同 sector_scores/member_map 既有姿势,免两处各自重算一遍全市场
    # 行业中位数);行业强度无 top_n 截断概念,天然是「全量」,不像 `sector_scores` 需要
    # 区分"报告展示用的 top-10"与"候选/持仓判据用的全量"两份。
    #
    # **v1.4-⑩(§七 P0-23):改为只读 `industry_strength_daily` 预计算表**,不再现算
    # (现算要扫全历史 784 万行,生产 2 vCPU/1.6G 上 700M cap OOM-kill、1400M cap 600s
    # 跑不完 → 当日无报告)。写在 16:05 日更、读在 16:35 报告,**职责不混:主链不写表**
    # (历史回放更不该顺手写表)。表缺行 → 空列表 + 保险丝:**降级方向 = 不拦(放行)**
    # —— A2 hard_cut 不触发、排序键① 全 None→+inf(序退化成 yellow_card→base_score→code,
    # 仍确定性可复现),并由 `dataFreshness` 三键 + 报告脚注**如实披露「没看」**。
    industry_map = load_industry_map(db_path=db_path)
    industry_scores = load_industry_strength(trade_date, db_path=db_path)
    industry_freshness = industry_strength_status(trade_date, db_path=db_path)
    if industry_freshness.stale or not industry_scores:
        logger.warning(
            "行业强度数据未就绪(表内最新至 %s,落后 %s 个交易日,当日可用行业 %d 个)——"
            "候选排序缺行业维度、题材持续天数与 A2/B3 本日不可得(降级方向=不拦),报告已显式标注。"
            "补算命令:%s",
            industry_freshness.latest_label(), industry_freshness.lag_days, len(industry_scores),
            refresh_command_hint(trade_date, trade_date),
        )

    # v1.3-③-C3:候选生成从 K1 entry mask 退役 → 情报筛选四步管线(需求 5,§2.3/§3.8-(b))。
    # 候选 = 「过完安检、值得关注的票」非「会涨的票」;`build_intel_candidates` 内部自算大
    # 板块拥挤度列表(需常驻板块 board_age,pipeline 的 top-10 sector_scores 不够大)。
    # ⚠ **V2-⑬-10:问询台海选池强制并入通道已删**(本处不再传强制并入名单)。
    # `industry_scores` = v1.4-② 行业强度(A2/B3 题材持续天数判据输入,pipeline 已算好一份)。
    candidates = build_intel_candidates(
        trade_date,
        active.rule,
        member_map=member_map,
        index_names=index_names,
        industry_scores=industry_scores,
        top_n=top_n_total,
        parquet_dir=parquet_dir,
        db_path=db_path,
    )

    provider = llm_provider or get_provider(db_path=db_path)
    top_list = top_list_lookup(trade_date, parquet_dir=parquet_dir)

    # v1.5-①+② 参考件三件套(需求 9,§2.0 第〇原则)+ 20 只全覆盖预算重排:一次 LLM
    # 调用同时产出「审判结论 + 三件套参考」(①-B 定死,不许拆成两次调用),按 rank
    # 升序审、受独立墙钟预算约束(②-B,`_judge_candidates_with_budget` docstring
    # 详述降级优先级/并发决策)。`judge_and_build_reference_plan` 内部含自身的降级链
    # (上下文装配异常退回默认上下文继续审判 / json 解析装配异常不影响已产出的审判
    # 结论),`_judge_candidates_with_budget` 逐票再包一层 try/except 兜底该函数自身
    # 的意外(同 C1/C2/C4/信息卡摘要姿势,§硬要求「核心管线对可选情报输入的调用必须
    # 包保险丝」)——异常时退回不带参考件的普通审判,**绝不因参考件失败而让某只候选
    # 没有审判结论**。`Candidate.reference_plan` 默认 `None`,只在成功产出时补上
    # (v1.5-①-F「候选照出、reference_plan=None,不冒充确认无参考」)。
    judged = _judge_candidates_with_budget(
        candidates[:top_n_judged], trade_date,
        provider=provider, top_list=top_list,
        industry_scores=industry_scores, industry_map=industry_map,
        transport=llm_transport, budget_seconds=candidate_judge_budget_seconds,
        save=save, parquet_dir=parquet_dir, db_path=db_path,
    )

    # v1.3-② 持仓 K4 每日体检 + D5 收盘净浮盈(EOD 权威计算,seam 落点):对每只 open 持仓
    # 在当日面板重算 K4 advisory 命中(读 DB K4,polars 镜像)+ 算好 D5 净浮盈 → 落
    # `holding_eod_check`(GET /positions 读快照嵌 k4Advisory;次日 precall 读 net_float)。
    # 复用报告已算好的 industry_scores/industry_map(v1.4-② 题材持续天数唯一源,不重复算)。
    holding_positions = pos_store.load_open_positions(db_path=db_path)
    holding_k4_check = build_holding_k4_check(
        trade_date, active.rule, holding_positions,
        industry_scores=industry_scores, industry_map=industry_map,
        scenario_position_ids=_scenario_review_position_ids(db_path=db_path),
        parquet_dir=parquet_dir, db_path=db_path,
    )

    # v1.3-③ 情报官 C1(复盘情报件)+ C2(板块资金流展示)。**不阻断主报告管线**
    # (硬要求④,同 LLM 降级链思想):两模块内部已逐项 `_safe()` 降级,这里再包
    # 一层 try/except 兜底编排逻辑自身的意外——任一整段异常都只记警告 + 落一份
    # 「计算异常」占位,绝不让 16:35 主报告任务崩。复用报告已算好的
    # member_map/index_names(板块成分/名称,C1/C2 均需要,不重复读 parquet)。
    try:
        intel = compute_intel(
            trade_date, member_map=member_map, index_names=index_names,
            parquet_dir=parquet_dir, db_path=db_path,
        )
    except Exception:  # noqa: BLE001 —— 情报节(C1)异常不得连带主报告失败
        logger.warning("情报节(C1)计算异常,已降级为空,不阻断主报告", exc_info=True)
        intel = empty_intel_report(trade_date, reason="情报节(C1)计算异常(详见服务端日志),已降级留空。")

    try:
        sector_moneyflow = compute_sector_moneyflow(
            trade_date, member_map=member_map, index_names=index_names, parquet_dir=parquet_dir,
        )
    except Exception:  # noqa: BLE001 —— 板块资金流(C2)异常不得连带主报告失败
        logger.warning("板块资金流(C2)计算异常,已降级为空,不阻断主报告", exc_info=True)
        sector_moneyflow = empty_sector_moneyflow_report(
            trade_date, reason="板块资金流(C2)计算异常(详见服务端日志),已降级留空。"
        )

    # v1.3-③-C4 消息面扫描。**V2-⑬-11 起扫描域只剩持仓**:自选池整链删除(裁定 #9-a)后
    # 次级扫描域暂空(`secondary_targets=[]`),隔日轮扫机制因此本版恒不触发——机制本体
    # **不拆**(⑭-A 把篮子成员接进次级域时原样复用,见 `news_alerts.py` 模块头 V2 登记)。
    # `db_path` 透传供减持类事件跨日去重查询。**不阻断主报告管线**(同 C1/C2 姿势,
    # 内部两个子扫描已各自降级,这里再包一层兜底编排逻辑自身的意外)。
    from neckline.report.candidates import _load_stock_names
    holding_codes = list(dict.fromkeys(h.ts_code for h in holding_positions))
    all_alert_codes = list(holding_codes)
    resolved_names = _load_stock_names(all_alert_codes, db_path) if all_alert_codes else {}
    position_targets = [(c, resolved_names.get(c) or c) for c in holding_codes]
    secondary_targets: List[tuple] = []
    try:
        news_alerts = build_news_alerts(
            trade_date, position_targets, secondary_targets,
            provider=provider, transport=llm_transport, db_path=db_path,
        )
    except Exception:  # noqa: BLE001 —— 消息面扫描(C4)异常不得连带主报告失败
        logger.warning("消息面扫描(C4)计算异常,已降级为未扫描,不阻断主报告", exc_info=True)
        news_alerts = empty_news_alerts_report(
            trade_date, reason="消息面扫描(C4)计算异常(详见服务端日志),已降级为未扫描。"
        )

    # v1.4-④ 信息卡摘要(不含 60 日序列,plan §五 v1.4-④-B):给当日候选原地补
    # `Candidate.info_card_summary`,随 `candidates_json` 一并落库,供 `CandidateOut.infoCard`
    # 列表页直接展示(免逐只再发请求)。快照/温和带零额外 I/O(直接读 `candidate.raw`/
    # `intel_rank`,候选生成时已装配好);消息面/龙虎榜复用本函数已算好的
    # `news_alerts.items`(内存态,此时尚未落库)与 `top_list`(第 192 行已拉取,同一份,
    # 不二次现拉)。**不阻断主报告管线**(同 C1/C2/C4 保险丝惯例,§硬要求④/项目
    # CLAUDE.md「核心管线对可选情报输入的调用必须包保险丝」)——异常时候选照出,只是
    # 这批候选当次没有信息卡摘要(`info_card_summary` 维持候选构造时的默认空 dict,
    # 客户端按"该信息暂不可用"处理,不冒充"确认无内容")。
    try:
        news_domain_codes = set(all_alert_codes)
        news_items_dicts = [
            {"ts_code": it.ts_code, "category": it.category, "summary": it.summary, "source": it.source}
            for it in news_alerts.items
        ]
        attach_info_card_summaries(
            candidates, trade_date,
            news_items=news_items_dicts, news_domain_codes=news_domain_codes,
            top_list=top_list, parquet_dir=parquet_dir, db_path=db_path,
            industry_ready=bool(industry_scores),   # v1.4-⑩-E:表缺行 → 快照如实缺省,不写 0
        )
    except Exception:  # noqa: BLE001 —— 信息卡摘要异常不得连带主报告失败
        logger.warning("信息卡摘要(v1.4-④)计算异常,候选照出,本次无摘要", exc_info=True)

    # v1.4-⑤-A 执行提示(exec_hint,需求 8 末段):给当日候选原地补 `Candidate.exec_hints`。
    # 零额外 parquet 读取(C1/C2/C4 直接读 `candidate.raw`,C3 按 ts_code 点查
    # `decision_log`)。**不阻断主报告管线**(同 C1/C2/C4/信息卡摘要保险丝惯例,§硬要求④/
    # 项目 CLAUDE.md「核心管线对可选情报输入的调用必须包保险丝」)——异常时候选照出,只是
    # 这批候选当次没有执行提示(`exec_hints` 维持候选构造时的默认空列表)。
    try:
        attach_exec_hints(candidates, trade_date, db_path=db_path)
    except Exception:  # noqa: BLE001 —— 执行提示异常不得连带主报告失败
        logger.warning("执行提示(v1.4-⑤-A)计算异常,候选照出,本次无执行提示", exc_info=True)

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    markdown = render_markdown(
        trade_date=trade_date,
        strategy_version=active.version,
        generated_at=generated_at,
        sentiment=sentiment,
        sectors=sector_scores,
        candidates=candidates,
        judged=judged,
        top_n_judged=top_n_judged,
        holding_k4_check=holding_k4_check,   # v1.5-③-C:持仓体检节(排在候选之前)
        intel=intel,
        sector_moneyflow=sector_moneyflow,
        news_alerts=news_alerts,
        sector_freshness=sector_freshness,
        industry_freshness=industry_freshness,   # v1.4-⑩-E 报告级披露
    )

    if save:
        store.save_report(
            trade_date,
            strategy_version=active.version,
            sentiment=_jsonable(sentiment),
            sectors=[_jsonable(s) for s in sector_scores],
            candidates=[_jsonable(c.public_dict()) for c in candidates],
            markdown=markdown,
            intel=intel.to_public_dict(),
            sector_moneyflow=sector_moneyflow.to_public_dict(),
            news_alerts_scan=news_alerts.scan_statuses_public(),
            # v1.4-①-C 板块三键 + v1.4-⑩-F 行业强度三键。**两件独立故障并列存放,不合并成
            # 一个 bool** —— 合并就分不清哪个坏了(既有 `stale` 语义一个字不改,仍只表板块)。
            data_freshness={
                **sector_freshness.to_public_dict(),
                **industry_freshness.to_public_dict(),
            },
            db_path=db_path,
        )
        # v1.3-② 持仓 K4 体检 + D5 净浮盈落库(同 `save=False` 不落库口径,防预览/单测副作用)。
        from neckline.report import holding_store
        holding_store.save_holding_eod_checks(trade_date, holding_k4_check, db_path=db_path)
        # v1.3-③-C4 消息面告警落库(同上,`save=False` 不落库;命中告警条目落独立表,
        # 扫描状态已随 `store.save_report` 落 `news_alerts_scan_json`)。
        from neckline.report import news_alerts_store
        news_alerts_store.save_news_alerts(trade_date, news_alerts.items, db_path=db_path)
        # v1.3-④ 挂单未成交追踪(同上,`save=False` 不落库、不推进 pending→expired
        # 状态机——预览/单测不应有这个副作用)。复用本函数已建立的 EOD 面板访问层,
        # 不新拉数据源;命中窗口内(offset≥1)的 pending 决策落一行,offset 达到
        # DECISION_PENDING_TRACK_DAYS(=5)同批转 expired(见该模块 docstring)。
        track_pending_decisions(trade_date, parquet_dir=parquet_dir, db_path=db_path)

    return ReportBundle(
        trade_date=trade_date,
        strategy_version=active.version,
        generated_at=generated_at,
        sentiment=sentiment,
        sectors=sector_scores,
        candidates=candidates,
        judged=judged,
        markdown=markdown,
        missed_entry_hint=compute_missed_entry_hint(trade_date, db_path=db_path),
        holding_k4_check=holding_k4_check,
        intel=intel,
        sector_moneyflow=sector_moneyflow,
        news_alerts=news_alerts,
        sector_freshness=sector_freshness,
        industry_freshness=industry_freshness,   # v1.4-⑩-F
    )


def _jsonable(obj: Any) -> Any:
    """dataclass / dict / list 递归转 JSON 安全结构(`date` -> ISO 字符串)。
    `store.save_report` 的 `sentiment`/`sectors`/`candidates` 参数需要能直接
    `json.dumps`,而 `SentimentDashboard.trade_date` 等字段是 `date` 对象。"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


__all__ = [
    "ReportBundle", "build_report", "compute_missed_entry_hint",
    "TOP_N_TOTAL", "TOP_N_JUDGED", "CANDIDATE_JUDGE_BUDGET_SECONDS",
]
