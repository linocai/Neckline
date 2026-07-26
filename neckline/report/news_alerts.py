"""晚间消息面公告扫描(plan §五 v1.3-③-C4)。16:35 报告新增「消息面」节——对
**持仓 + 自选**(不是全市场)票扫描四类消息:减持 / 立案 / 暴雷 / 监管(枚举码,
客户端展示层换算,沿 `boardLabel` 先例)。

**数据源侦察结论(2026-07-26,真实 token 活体探活,详见 PROJECT_PLAN.md §九
变更日志「v1.3-③-C4」条与本模块注释——这是后人不必重查的资产)**:

    · **`anns_d`(TuShare 通用公告接口)—— 不可用。** 真实调用返回:
      "抱歉，您没有接口(anns_d)访问权限，权限的具体详情访问：
      https://tushare.pro/document/1?doc_id=108。" 官方文档交叉核实:该接口是
      **独立付费权限**(公告信息单独 1000 元/年,与本项目 600 元档〔6000 积分〕
      的常规积分体系无关,需另外购买),当前未购——与 §3.2「新闻资讯为单独权限
      1000 元/年,未购」是**同一类型的决策口径**(但公告〔anns_d〕与新闻资讯是
      TuShare 两个不同的独立付费产品,此前 §3.2 只记了新闻资讯未购,本次侦察
      补上公告接口同样未购的结论)。旧接口名 `anns` 已废弃(调用报"请指定正确
      的接口名")。
    · **`stk_holdertrade`(股东增减持,结构化)—— 可用!** 真实调用返回数据,
      只需 2000 积分(在 6000 积分档覆盖范围内,非"单独权限"),字段含
      `in_de`(IN增持/DE减持)、`holder_type`(G高管/P个人/C公司)、
      `change_vol`/`change_ratio`/`ann_date` 等结构化字段——**比公告全文扫描
      更适合"减持"这一类**:结构化、无需 LLM 语义抽取、零幻觉风险、免 LLM 调用
      (省成本)。故本模块**「减持」类改用 `stk_holdertrade` 结构化数据,不用
      `anns_d`、也不用 LLM**,`in_de=='DE'` 即减持事件。这是比 plan 原文举例的
      `anns_d` 更优的方案,超出「查 anns_d」字面但同属「数据源侦察」的题中之义。
    · **立案 / 暴雷 / 监管三类 —— 无任何 TuShare 接口覆盖**(逐一核实:未找到
      "立案调查"/"监管处罚"/"违规处理"专属接口;`anns_d` 本可能间接覆盖但权限
      不可用;`disclosure_date` 是财务预约披露日期,与监管处罚无关,排除)。
      **三类全部走 LLM 联网搜索兜底**(`neckline.llm.news_scan`,复用 `judge.py`
      同一套 provider/降级链姿势,读超时守 90s)。

**架构(硬要求④「不阻断主报告管线」+ §3.4 缺 key 优雅降级)**:
    ① 减持:`_scan_reduction`(TuShare `stk_holdertrade`,结构化,免 LLM)——
       TuShare 无 token / 调用失败 → 该源降级为「未扫描」(`scanned=False`),
       不臆造「没有减持」。
    ② 立案/暴雷/监管:`_scan_llm_categories`(每票一次 LLM 调用,一次问三类,
       不是三次调用——控成本/控时长,见 `llm.news_scan` 模块头);
       `provider=None`(缺 key)→ 整批直接降级「未激活」,不发起任何网络调用,
       同 `judge.py` 姿势。

**"没扫到"(未激活/调用失败,未知态)vs"扫了没有"(确认无此类消息)必须能
区分**(§硬要求,不许静默当成"没有公告")——`NewsAlertsReport.scan_statuses`
逐源记录 `scanned: bool` + `reason`(+ LLM 源额外记 `codes_total`/`codes_failed`,
支持"部分标的失败"的颗粒度),`items` 为空**不代表**「确认没有」,读者须先看
`scan_statuses` 才能判断空列表的含义。`scan_statuses` 随该次报告生成落
`reports.news_alerts_scan_json`(同 `intel_json`/`sector_moneyflow_json` 惯例,
保证历史报告回放时仍能看到当时的扫描状态,不只是当次内存态)。

**落库**:`items`(真正命中的告警)落独立 `news_alerts` 表(`neckline.db`),
`trade_date` = **本次扫描所属报告日**(与本库其余表 `trade_date` 惯例一致,
不是公告事件本身的日期)。**已知简化(不做跨日按事件日去重)**:同一公告若
连续数日仍落在扫描窗口内被再次发现,会在数日的报告里重复出现——这是刻意的
简化(优先保证不漏报,复杂的跨日去重留后续增强,已记入完工报告)。存取见
`report/news_alerts_store.py`。

**系统永不代交易动作**(§3.8):本模块只扫描/归类/展示,不触发任何下单/撤单。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import polars as pl

from neckline.data.tushare_client import ts_stk_holdertrade
from neckline.llm.base import LLMProvider
from neckline.llm.news_scan import scan_news_for_code

logger = logging.getLogger(__name__)


class NewsCategory:
    """四类枚举码(服务端字面英文,客户端展示层换算,沿 `data.board.Board` /
    `boardLabel` 先例——不在服务端存中文)。"""
    REDUCTION = "REDUCTION"            # 减持(TuShare stk_holdertrade,结构化)
    INVESTIGATION = "INVESTIGATION"    # 立案(LLM)
    BLOWUP = "BLOWUP"                  # 暴雷(LLM)
    REGULATORY = "REGULATORY"          # 监管(LLM)


ALL_CATEGORIES = (
    NewsCategory.REDUCTION, NewsCategory.INVESTIGATION, NewsCategory.BLOWUP, NewsCategory.REGULATORY,
)

SOURCE_TUSHARE_HOLDERTRADE = "tushare_holdertrade"
SOURCE_LLM_PREFIX = "llm"   # 实际值 f"llm_{provider_name}",如 "llm_glm"

# 减持扫描回看窗口(自然日,非交易日——桥接长假,如国庆 7 天/春节约 7-10 天)。
_REDUCTION_LOOKBACK_DAYS = 10

# 同一 ts_code 在窗口内合并展示的减持事件条数上限(`news_alerts` 表 UNIQUE(ts_code,
# trade_date, category) 只留一行,多笔真实事件合并进一条 summary,防止无限拉长)。
_MAX_REDUCTION_EVENTS_IN_SUMMARY = 3

_HOLDER_TYPE_LABEL: Dict[str, str] = {"G": "高管", "P": "个人股东", "C": "机构股东"}

EVIDENCE_NOTE = (
    "减持:TuShare stk_holdertrade 结构化数据(股东增减持公告口径,强证据);"
    "立案/暴雷/监管:LLM 联网搜索兜底(TuShare 600 元档无结构化接口覆盖,详见模块"
    "docstring 数据源侦察结论),受限于搜索命中与模型解读,请以原文公告为准。"
)


@dataclass
class NewsAlertItem:
    ts_code: str
    name: str
    category: str      # NewsCategory 值
    summary: str
    source: str         # tushare_holdertrade | llm_<provider>

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "code": self.ts_code, "name": self.name, "category": self.category,
            "summary": self.summary, "source": self.source,
        }


@dataclass
class NewsAlertScanStatus:
    """扫描来源级状态(硬要求「没扫到 vs 扫了没有必须能区分开」的落地点)。"""
    source: str          # tushare_holdertrade | llm
    scanned: bool         # 是否真正执行了扫描(而非因缺 key/无 token 整体跳过)
    reason: str = ""      # 未扫描 / 部分失败的原因;全部正常时空串
    codes_total: int = 0  # 应扫描的标的数(仅 source=llm 有意义;tushare 是区间批量调用记 0)
    codes_failed: int = 0 # 调用失败/格式解析失败的标的数(仅 source=llm)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source, "scanned": self.scanned, "reason": self.reason,
            "codesTotal": self.codes_total, "codesFailed": self.codes_failed,
        }


@dataclass
class NewsAlertsReport:
    trade_date: date
    items: List[NewsAlertItem] = field(default_factory=list)
    scan_statuses: List[NewsAlertScanStatus] = field(default_factory=list)
    evidence_note: str = EVIDENCE_NOTE

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "tradeDate": self.trade_date.isoformat(),
            "items": [i.to_public_dict() for i in self.items],
            "scanStatuses": [s.to_public_dict() for s in self.scan_statuses],
            "evidenceNote": self.evidence_note,
        }

    def scan_statuses_public(self) -> List[Dict[str, Any]]:
        """`pipeline.py` 落 `reports.news_alerts_scan_json` 用(只落扫描状态,
        不落 `items`——后者已在独立 `news_alerts` 表,不重复存两份)。"""
        return [s.to_public_dict() for s in self.scan_statuses]


def empty_news_alerts_report(trade_date: date, reason: str) -> NewsAlertsReport:
    """`pipeline.py` 兜底工厂(同 `intel.empty_intel_report`/`sector_moneyflow.
    empty_sector_moneyflow_report` 姿势,外层保险丝——本模块内部两个子扫描各自
    已有降级,这里只应对编排逻辑自身出乎意料的异常)。两源均标 `scanned=False`,
    不假装扫描过。"""
    return NewsAlertsReport(
        trade_date=trade_date,
        scan_statuses=[
            NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=False, reason=reason),
            NewsAlertScanStatus(source=SOURCE_LLM_PREFIX, scanned=False, reason=reason),
        ],
    )


# —— ① 减持(TuShare stk_holdertrade,结构化,免 LLM)——————————————————————

def _scan_reduction(
    trade_date: date, codes: Set[str], names: Dict[str, str],
) -> Tuple[List[NewsAlertItem], NewsAlertScanStatus]:
    start = trade_date - timedelta(days=_REDUCTION_LOOKBACK_DAYS)
    res = ts_stk_holdertrade(start.strftime("%Y%m%d"), trade_date.strftime("%Y%m%d"))
    if not res.ok:
        return [], NewsAlertScanStatus(
            source=SOURCE_TUSHARE_HOLDERTRADE, scanned=False,
            reason=f"TuShare stk_holdertrade 调用失败:{res.reason}",
        )
    if res.data is None or len(res.data) == 0:
        return [], NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True)

    df = pl.from_pandas(res.data)
    if df.is_empty() or "ts_code" not in df.columns or "in_de" not in df.columns:
        return [], NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True)

    hits = df.filter((pl.col("in_de") == "DE") & pl.col("ts_code").is_in(list(codes)))
    if hits.is_empty():
        return [], NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True)

    # `news_alerts` 表 UNIQUE(ts_code, trade_date, category) 天然要求「同票同日同类只
    # 一行」——**主动合并**同一 ts_code 在窗口内的多笔减持事件成一条 summary,不能任由
    # 后写覆盖丢信息(2026-07-26 端到端真实数据验证时发现的真实必要性:①TuShare 对同一
    # 披露会原样返回重复行〔实测 301358.SZ 同一 holder/ann_date/change_vol/change_ratio
    # 出现两次,疑为其数据管线自身去重不彻底,非本模块解析错误〕,先按完整字段去重;
    # ②同一票在回看窗口内可能有多个不同股东/不同日各自的真实减持,合并展示而非只保留
    # 最后一条)。
    seen: set = set()
    by_code: Dict[str, List[Dict[str, Any]]] = {}
    for r in hits.iter_rows(named=True):
        key = (r["ts_code"], r.get("holder_name"), r.get("ann_date"), r.get("change_vol"), r.get("change_ratio"))
        if key in seen:
            continue
        seen.add(key)
        by_code.setdefault(r["ts_code"], []).append(r)

    items: List[NewsAlertItem] = []
    for code, rows in by_code.items():
        parts = [_format_reduction_event(r) for r in rows[:_MAX_REDUCTION_EVENTS_IN_SUMMARY]]
        extra = len(rows) - len(parts)
        summary = "；".join(parts)
        if extra > 0:
            summary += f";另有 {extra} 笔未展示(见 TuShare 原始披露)。"
        items.append(NewsAlertItem(
            ts_code=code, name=names.get(code, code), category=NewsCategory.REDUCTION,
            summary=summary, source=SOURCE_TUSHARE_HOLDERTRADE,
        ))
    return items, NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True)


def _format_reduction_event(r: Dict[str, Any]) -> str:
    holder = r.get("holder_name") or "未知股东"
    htype = _HOLDER_TYPE_LABEL.get(r.get("holder_type"), r.get("holder_type") or "")
    vol = r.get("change_vol")
    ratio = r.get("change_ratio")
    ann = r.get("ann_date") or ""
    vol_txt = f"{vol:,.0f} 股" if isinstance(vol, (int, float)) else "股数未知"
    ratio_txt = f"占总股本 {ratio:.2f}%" if isinstance(ratio, (int, float)) else "占比未知"
    return f"{holder}({htype})减持 {vol_txt},{ratio_txt},公告日 {ann}"


# —— ② 立案/暴雷/监管(LLM,一次问三类)——————————————————————————————————

def _scan_llm_categories(
    codes: Sequence[Tuple[str, str]],
    *, provider: Optional[LLMProvider], transport: Optional[Any] = None,
) -> Tuple[List[NewsAlertItem], NewsAlertScanStatus]:
    if provider is None:
        return [], NewsAlertScanStatus(
            source=SOURCE_LLM_PREFIX, scanned=False,
            reason="未配置 LLM_PROVIDER/LLM_API_KEY(缺 key,全部跳过,未发起任何网络调用)。",
            codes_total=len(codes),
        )
    if not codes:
        return [], NewsAlertScanStatus(source=SOURCE_LLM_PREFIX, scanned=True, codes_total=0)

    items: List[NewsAlertItem] = []
    failed = 0
    for ts_code, name in codes:
        r = scan_news_for_code(ts_code, name, provider=provider, transport=transport)
        if r.degraded:
            failed += 1
            logger.warning("消息面扫描(C4)LLM [%s %s] 降级:%s", ts_code, name, r.degrade_reason)
            continue
        for category, summary in r.hits:
            items.append(NewsAlertItem(
                ts_code=ts_code, name=name, category=category, summary=summary,
                source=f"{SOURCE_LLM_PREFIX}_{r.provider}",
            ))
    reason = (
        "" if failed == 0 else
        f"{failed}/{len(codes)} 只标的 LLM 调用失败或未按格式输出,已跳过"
        f"(不计入「确认无消息」,建议人工复核)。"
    )
    return items, NewsAlertScanStatus(
        source=SOURCE_LLM_PREFIX, scanned=True, reason=reason,
        codes_total=len(codes), codes_failed=failed,
    )


# —— 主入口 ——————————————————————————————————————————————————————————————

def build_news_alerts(
    trade_date: date,
    codes: Sequence[Tuple[str, str]],   # 去重后的 (ts_code, name),持仓 ∪ 自选
    *,
    provider: Optional[LLMProvider] = None,
    transport: Optional[Any] = None,
) -> NewsAlertsReport:
    """消息面扫描 I/O 入口(角色对应 `intel.compute_intel`/`sector_moneyflow.
    compute_sector_moneyflow`)。`codes` 为空(持仓+自选均为空)→ 直接空报告,
    零 I/O(两源均标 `scanned=True` 空操作——不是"缺 key"式的未扫描,是"没有
    扫描对象"这个更平凡的空态)。"""
    if not codes:
        return NewsAlertsReport(
            trade_date=trade_date,
            scan_statuses=[
                NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True),
                NewsAlertScanStatus(source=SOURCE_LLM_PREFIX, scanned=True, codes_total=0),
            ],
        )
    names = {c: n for c, n in codes}
    code_set = set(names)

    reduction_items, reduction_status = _scan_reduction(trade_date, code_set, names)
    llm_items, llm_status = _scan_llm_categories(list(codes), provider=provider, transport=transport)

    return NewsAlertsReport(
        trade_date=trade_date,
        items=reduction_items + llm_items,
        scan_statuses=[reduction_status, llm_status],
    )


__all__ = [
    "NewsCategory",
    "ALL_CATEGORIES",
    "SOURCE_TUSHARE_HOLDERTRADE",
    "SOURCE_LLM_PREFIX",
    "EVIDENCE_NOTE",
    "NewsAlertItem",
    "NewsAlertScanStatus",
    "NewsAlertsReport",
    "empty_news_alerts_report",
    "build_news_alerts",
]
