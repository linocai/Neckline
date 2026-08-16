"""报告管线编排(plan §五 V2-⑭-A;§2.6 历史回放的天然落地点)。

**本模块两个入口,职责刻意分开 —— 改之前先看清楚是哪一个**

1. **`build_report(trade_date, ...)` = 只出报告的那一段**。读**已经冻在库里的**东西
   (情绪 / 强势板块 / 持仓体检 / 情报件 / 资金流 / 消息面 / 篮子四表 / 复盘表),渲染
   五段 markdown,落 `reports` 表 + 三张报告自己的侧表。**它不批算扫描层、不调聚合、
   不定 Tier、不冻卡、不跑复盘** —— 那些是下面那个编排函数的事。
   👉 这条边界是**历史回放的生命线**(§2.6「喂历史=回测、喂今日=报告」):回放一份
   三个月前的报告**绝不能**顺手往 `baskets`/`corr_matrix_daily` 里写今天算出来的东西。
2. **批算侧住 `neckline/report/evening.py`**(⑧ 验证拍 → ④ 扫描层 → ⑤⑥⑦ 篮子 →
   ⑨ 复盘 → 调本模块的 `build_report` 收尾)。**⛔ 别把那条链搬回来**:本文件在
   `tests/test_scan_layer_guardrails.py` 的**在线模块清单**里,P0-23 守门单测逐字
   grep 禁止它出现扫描层的批算写入口名 —— 搬回来 = 为了迁就文件摆放而钝化一条被
   生产 OOM 打出来的真防线。(那条守门连注释一起 grep,所以这里连名字都不写。)

**策略大脑是唯一权威**(2026-07-20 用户拍板):大脑无现役版本时**拒绝生成报告并抛出
清晰错误**(这是配置缺陷,不是"优雅降级"的场景)。
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
from neckline.sentinel import positions as pos_store
from neckline.llm.base import LLMProvider
from neckline.llm.factory import get_provider
from neckline.report import store
from neckline.report.basket_daily import BasketDaily, build_basket_daily, empty_basket_daily
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
from neckline.report.sector_moneyflow import (
    SectorMoneyflowReport,
    compute_sector_moneyflow,
    empty_sector_moneyflow_report,
)
from neckline.report.sentiment import SentimentDashboard, compute_sentiment
from neckline.scan.freshness import ScanLayerFreshness, scan_layer_status
from neckline.strategy import brain

logger = logging.getLogger(__name__)

@dataclass
class ReportBundle:
    trade_date: date
    report_date: date
    strategy_version: str
    generated_at: str
    sentiment: SentimentDashboard
    sectors: List[SectorScore]
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
    # V2-⑭-A 市场扫描层新鲜度(V2-④ 的第三件独立故障:三张预计算表批算跑没跑)。
    # ⑨ 的完工记录说得清楚:扫描层没跑 → 当日无种子 → 当日无篮子,而「今天没有篮子」
    # 与「今天没看」是两回事。**同样不与上面两条合并**(那就是三合一 bool 的老病)。
    scan_freshness: Optional[ScanLayerFreshness] = None
    # V2-⑭-A 篮子日报三段(③ 今日篮子 / ③b 未定档 / ④ 昨日复盘)。
    basket_daily: Optional[BasketDaily] = None


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
    report_date: Optional[date] = None,
    llm_provider: Optional[LLMProvider] = None,
    llm_transport: Optional[Any] = None,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    save: bool = True,
    dropped_baskets: Optional[List[Any]] = None,
) -> ReportBundle:
    """生成 `trade_date` 这一天的**篮子日报**(五段,见 `render.py` 模块头)。

    ⚠ **本函数只读不算**(模块头第 1 条):篮子 / Tier / 卡 / 验证 / 复盘全部由
    `run_evening_chain` 在各自段落里算完落库,这里只把它们读回来渲染。

    `llm_provider`:显式传入用于测试注入(如 `_StubProvider`/真 provider + Mock
    Transport);为 `None`(默认,生产用法)时从 `.env` 现读(`llm.factory.get_provider()`)。
    `save=False` 供只想拿 `ReportBundle`/跑历史回放对照、不想写库的调用方(如单测)。

    `dropped_baskets`:⑥ 本次跑出来的 `TierResult.dropped`(③b 的数据源)。**⑥ 的溢出
    篮不进 `baskets` 表**(`baskets.tier` NOT NULL),报告快照是它唯一的落点 —— 由
    `run_evening_chain` 传进来。**`None` = 本次没跑 ⑥**(单出报告 / 历史回放)→ ③b 如实
    标「本段未取得」,⛔ 不现算一遍"当时会溢出哪些"(那是拿今天的包编造昨天的结论);
    **空列表 = 跑了、今天零溢出** → 节仍在,写「今日无未定档篮子」。
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

    # V2-⑭-A(④ 完工记录登记的「`dataFreshness.scanLayer*` 三键未接线」欠账在此兑现):
    # 扫描层三张预计算表跑没跑。**降级方向 = 不拦**(缺行 → 无种子 → 当日无篮子是合法
    # 输出),但必须显式披露 —— 否则「今天没有篮子」与「今天没跑扫描层」在报告上长得
    # 一模一样。整段包保险丝:新鲜度自己出问题不许掀翻报告。
    try:
        scan_freshness = scan_layer_status(trade_date, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("扫描层新鲜度查询异常(已降级为不可得,不阻断报告)", exc_info=True)
        scan_freshness = None
    if scan_freshness is not None and scan_freshness.stale:
        logger.warning(
            "市场扫描层数据未就绪(表内最新至 %s,落后 %s 个交易日)——今日驱动种子/篮子"
            "不可得(降级方向=不拦),报告已显式标注。",
            scan_freshness.latest_label(), scan_freshness.lag_days,
        )

    provider = llm_provider or get_provider(db_path=db_path)

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

    # V2-⑭-A ③③b④ 篮子日报三段(每段各自包保险丝,见 `basket_daily.py`)。**排在消息面
    # 之前**,因为下面的次级扫描域要用篮子成员。整段再包一层兜底:装配逻辑本身的意外
    # 也不许让当日无报告。
    try:
        basket_daily = build_basket_daily(
            trade_date, dropped=dropped_baskets, db_path=db_path, parquet_dir=parquet_dir,
        )
    except Exception:  # noqa: BLE001 —— 篮子日报整段异常不得连带主报告失败
        logger.warning("篮子日报(③/③b/④)装配异常,已降级为三段未取得,不阻断主报告", exc_info=True)
        basket_daily = empty_basket_daily(
            trade_date, "篮子日报装配异常(详见服务端日志),本次三段均未取得。"
        )

    # v1.3-③-C4 消息面扫描。**V2-⑭-A 起次级扫描域 = 今日篮子成员**(⑬-11 删掉自选池后
    # 次级域一度恒空,机制本体刻意留着等这里接线;见 `news_alerts.py` 模块头 V2 登记)。
    # ⚠ **两处必须同域**:`info_card._default_news_domain` 同步扩到篮子成员,否则信息卡
    # 会对着一批"其实扫过"的票说"不在扫描域"(⑬ 完工记录点名的欠账)。
    # `db_path` 透传供减持类事件跨日去重查询。**不阻断主报告管线**(同 C1/C2 姿势,
    # 内部两个子扫描已各自降级,这里再包一层兜底编排逻辑自身的意外)。
    from neckline.data.market_data import resolve_stock_names
    holding_codes = list(dict.fromkeys(h.ts_code for h in holding_positions))
    basket_codes = [c for c in dict.fromkeys(
        code for b in basket_daily.baskets for code in b.member_codes
    ) if c and c not in set(holding_codes)]
    all_alert_codes = list(holding_codes) + basket_codes
    resolved_names = resolve_stock_names(all_alert_codes, db_path) if all_alert_codes else {}
    position_targets = [(c, resolved_names.get(c) or c) for c in holding_codes]
    secondary_targets: List[tuple] = [(c, resolved_names.get(c) or c) for c in basket_codes]
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

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    visible_report_date = report_date or trade_date
    markdown = render_markdown(
        trade_date=trade_date,
        report_date=visible_report_date,
        strategy_version=active.version,
        generated_at=generated_at,
        sentiment=sentiment,
        sectors=sector_scores,
        holding_k4_check=holding_k4_check,   # v1.5-③-C:持仓体检节(排在候选之前)
        intel=intel,
        sector_moneyflow=sector_moneyflow,
        news_alerts=news_alerts,
        sector_freshness=sector_freshness,
        industry_freshness=industry_freshness,   # v1.4-⑩-E 报告级披露
        scan_freshness=scan_freshness,           # V2-⑭-A 第三件独立故障
        basket_daily=basket_daily,               # V2-⑭-A ③/③b/④ 三段
    )

    if save:
        store.save_report(
            trade_date,
            report_date=visible_report_date,
            strategy_version=active.version,
            sentiment=_jsonable(sentiment),
            sectors=[_jsonable(s) for s in sector_scores],
            candidates=[],   # ⑬-1:候选榜已删,列保留(⑭-A 换成篮子日报)
            markdown=markdown,
            intel=intel.to_public_dict(),
            sector_moneyflow=sector_moneyflow.to_public_dict(),
            news_alerts_scan=news_alerts.scan_statuses_public(),
            # v1.4-①-C 板块三键 + v1.4-⑩-F 行业强度三键 + V2-⑭-A 扫描层三键。
            # **三件独立故障并列存放,不合并成一个 bool** —— 合并就分不清哪个坏了
            # (既有 `stale` 语义一个字不改,仍只表板块)。扫描层那份取不到时**该三键
            # 整体缺席**,而不是补一份"看起来新鲜"的默认值(§3.8)。
            data_freshness={
                **sector_freshness.to_public_dict(),
                **industry_freshness.to_public_dict(),
                **(scan_freshness.to_public_dict() if scan_freshness is not None else {}),
            },
            basket_daily=basket_daily.to_public_dict(),
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
        report_date=visible_report_date,
        strategy_version=active.version,
        generated_at=generated_at,
        sentiment=sentiment,
        sectors=sector_scores,
        markdown=markdown,
        missed_entry_hint=compute_missed_entry_hint(trade_date, db_path=db_path),
        holding_k4_check=holding_k4_check,
        intel=intel,
        sector_moneyflow=sector_moneyflow,
        news_alerts=news_alerts,
        sector_freshness=sector_freshness,
        industry_freshness=industry_freshness,   # v1.4-⑩-F
        scan_freshness=scan_freshness,           # V2-⑭-A
        basket_daily=basket_daily,               # V2-⑭-A
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


__all__ = ["ReportBundle", "build_report", "compute_missed_entry_hint"]
