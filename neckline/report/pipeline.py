"""报告管线编排(plan 2.5,§2.6 历史回放的天然落地点)。串起情绪仪表盘(2.1)+
强势板块(2.2)+ 候选评分(2.3)+ LLM 审判(2.4,前10只)+ 落库(store.py)+
markdown 渲染(render.py)——`scripts/report.py` 的核心入口就是本模块的
`build_report`。

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
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline import decision_log
from neckline import watchlist as watchlist_store
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
from neckline.report.watchlist_check import WatchlistCheckItem, apply_llm_review, build_watchlist_check
from neckline.strategy import brain

logger = logging.getLogger(__name__)

TOP_N_TOTAL = 20
TOP_N_JUDGED = 10


@dataclass
class ReportBundle:
    trade_date: date
    strategy_version: str
    generated_at: str
    sentiment: SentimentDashboard
    sectors: List[SectorScore]
    candidates: List[Candidate]
    judged: Dict[str, JudgeResult]  # ts_code -> JudgeResult(仅前 top_n_judged 只)
    markdown: str
    missed_entry_hint: str = ""     # v1.1-B.4 漏录兜底提示(无 → 空串)
    watchlist_check: List[WatchlistCheckItem] = field(default_factory=list)  # v1.1-C.3 自选体检(独立一节)
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


def build_report(
    trade_date: date,
    *,
    llm_provider: Optional[LLMProvider] = None,
    llm_transport: Optional[Any] = None,
    top_n_total: int = TOP_N_TOTAL,
    top_n_judged: int = TOP_N_JUDGED,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    save: bool = True,
) -> ReportBundle:
    """生成 `trade_date` 这一天的完整盘后报告。

    `llm_provider`:显式传入用于测试注入(如 `_StubProvider`/真 provider + Mock
    Transport);为 `None`(默认,生产用法)时从 `.env` 现读(`llm.factory.get_provider()`)
    ——本项目当前无 key,现读结果恒为 `None`,LLM 审判段落走「未激活」占位链路。
    `save=False` 供只想拿 `ReportBundle`/跑历史回放对照、不想写库的调用方(如单测)。
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

    # 消费问询台海选池(§2.5 闭环报告侧;v1.1-D 问询窗口修复)——「初审通过」的票
    # 强制并入当晚候选评分 universe(只扩输入,不改评分逻辑)。消费窗口从「入池当日
    # 等于本报告日」改为「待消费(consumed_report_date IS NULL)∪ 已被本报告日消费过
    # (幂等补跑)」,修复 16:35 报告已生成后问询通过的票永久掉缝的生产真洞(详见
    # `neckline.api.stores.load_pending_inquiry_codes` docstring 根因说明)。lazy
    # import 沿 `review/reconcile.py` 惯例,让报告管线不在模块加载期依赖 api 包。
    from neckline.api.stores import load_pending_inquiry_codes, mark_inquiry_pool_consumed
    inquiry_codes = list(dict.fromkeys(
        p["ts_code"] for p in load_pending_inquiry_codes(trade_date, db_path=db_path)
    ))

    # v1.3-③-C3:候选生成从 K1 entry mask 退役 → 情报筛选四步管线(需求 5,§2.3/§3.8-(b))。
    # 候选 = 「过完安检、值得关注的票」非「会涨的票」;`build_intel_candidates` 内部自算大
    # 板块拥挤度列表(需常驻板块 board_age,pipeline 的 top-10 sector_scores 不够大),
    # forced_codes(问询台海选池)语义不变(§2.5 强制并入,豁免卫生线/hard_cut、仅 K4 打标)。
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
        forced_codes=inquiry_codes,
    )

    provider = llm_provider or get_provider(db_path=db_path)
    top_list = top_list_lookup(trade_date, parquet_dir=parquet_dir)

    # v1.5-① 参考件三件套(需求 9,§2.0 第〇原则):一次 LLM 调用同时产出「审判结论 +
    # 三件套参考」(①-B 定死,不许拆成两次调用)——`judge_and_build_reference_plan`
    # 内部含自身的降级链(上下文装配异常退回默认上下文继续审判 / json 解析装配异常
    # 不影响已产出的审判结论),这里再包一层 try/except 兜底该函数自身的意外
    # (同 C1/C2/C4/信息卡摘要姿势,§硬要求「核心管线对可选情报输入的调用必须包保险
    # 丝」)——异常时退回不带参考件的普通审判,**绝不因参考件失败而让某只候选没有
    # 审判结论**。`Candidate.reference_plan` 默认 `None`,只在成功产出时补上
    # (v1.5-①-F「候选照出、reference_plan=None,不冒充确认无参考」)。
    judged: Dict[str, JudgeResult] = {}
    for c in candidates[:top_n_judged]:
        top_row = top_list.get(c.ts_code)
        try:
            result, plan = judge_and_build_reference_plan(
                c, trade_date, provider=provider, top_list_row=top_row, transport=llm_transport,
                industry_scores=industry_scores, industry_map=industry_map, top_list_t0=top_list,
                parquet_dir=parquet_dir, db_path=db_path,
            )
        except Exception:  # noqa: BLE001 —— 参考件三件套(v1.5-①)整体异常不得连带候选审判失败
            logger.warning(
                "参考件三件套(v1.5-①)整体异常(%s),候选照出、退回不带参考件的普通审判",
                c.ts_code, exc_info=True,
            )
            result = judge_candidate(c, provider=provider, top_list_row=top_row, transport=llm_transport)
            plan = None
        judged[c.ts_code] = result
        if save:
            store.save_llm_judgment(trade_date, result, db_path=db_path)
        if plan is not None:
            c.reference_plan = plan.to_public_dict()
            if save:
                reference_plan_store.save_reference_plans(trade_date, [plan], db_path=db_path)

    # v1.1-C.3 自选体检(独立一节,同码复用候选评分管线,§2.3 报告拍板/§五
    # v1.1-C.3):不改候选评分/不进候选榜。空自选池 → `build_watchlist_check`
    # 直接返回 []、零额外 I/O(见该函数 docstring)。LLM 只审「当日状态较上一份
    # 报告变化的」∪「用户 pinned 的」(控成本,`apply_llm_review` 内部判定)。
    watchlist_items = [w.to_dict() for w in watchlist_store.list_watchlist(db_path=db_path)]
    watchlist_check = build_watchlist_check(
        trade_date, active.rule, watchlist_items,
        sector_scores=sector_scores, member_map=member_map, index_names=index_names,
        parquet_dir=parquet_dir, db_path=db_path,
    )
    previous_watchlist_snapshot = store.load_watchlist_snapshot_before(trade_date, db_path=db_path)
    apply_llm_review(
        watchlist_check, previous_watchlist_snapshot,
        provider=provider, top_list=top_list, transport=llm_transport,
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

    # v1.3-③-C4 消息面扫描:对象 = **持仓 ∪ 自选**,不是全市场(§硬要求)。持仓 /
    # 自选**分开传入**(不揉成一个列表)——`build_news_alerts` 的 LLM 侧按「持仓
    # 优先、自选靠后」顺序扫描,墙钟预算不够时牺牲的必须是自选(2026-07-26 必改,
    # 见 news_alerts.py 模块头)。展示名优先取自选池自带的 name(用户维护/入池时
    # 已解析),持仓票另经 stock_basic 解析(`Position` 无 name 字段)。`db_path`
    # 透传供减持类事件跨日去重查询(同一必改)。**不阻断主报告管线**(同 C1/C2
    # 姿势,内部两个子扫描已各自降级,这里再包一层兜底编排逻辑自身的意外)。
    from neckline.report.candidates import _load_stock_names
    holding_codes = list(dict.fromkeys(h.ts_code for h in holding_positions))
    watchlist_only_codes = list(dict.fromkeys(w["ts_code"] for w in watchlist_items))
    all_alert_codes = list(dict.fromkeys(holding_codes + watchlist_only_codes))
    resolved_names = _load_stock_names(all_alert_codes, db_path) if all_alert_codes else {}
    watchlist_name_map = {w["ts_code"]: w.get("name") for w in watchlist_items if w.get("name")}

    def _alert_name(c: str) -> str:
        return watchlist_name_map.get(c) or resolved_names.get(c) or c

    position_targets = [(c, _alert_name(c)) for c in holding_codes]
    watchlist_targets = [(c, _alert_name(c)) for c in watchlist_only_codes]
    try:
        news_alerts = build_news_alerts(
            trade_date, position_targets, watchlist_targets,
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
        watchlist_check=watchlist_check,
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
            watchlist=[_jsonable(w.public_dict()) for w in watchlist_check],
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
        # v1.1-D 问询窗口修复:报告落库成功后才标记消费(§根因见
        # `load_pending_inquiry_codes`/`mark_inquiry_pool_consumed` docstring)——
        # `save=False`(预览/单测)绝不应有这个副作用,故放在 `if save:` 内。
        mark_inquiry_pool_consumed(trade_date, db_path=db_path)
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
        watchlist_check=watchlist_check,
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


__all__ = ["ReportBundle", "build_report", "compute_missed_entry_hint", "TOP_N_TOTAL", "TOP_N_JUDGED"]
