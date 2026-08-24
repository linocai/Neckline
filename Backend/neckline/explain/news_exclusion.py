"""消息面排除(V2.5.0 S9,K9 §二 末段 + 架构 §3.3)。

**爆雷 / 减持 / 立案 / 监管**四类在这一层查出并剔除;剔除后由排序中的**后备票补位**
(补位决定归编排器,见 `report/evening.py` —— 解释层不知道谁是第几名,双盲不破)。

🔴 **三态,⛔ 不许折平**:

    `clean`      查过了、干净 → 留在清单上;
    `excluded`   命中四类之一 → **剔除**;
    `unverified` **没查成**(没有 provider / 调用失败 / 模型没按格式收尾)。

`unverified` 为什么必须是第三态、而不能就近折成另外两个之一:
    · 折成 `clean` = 「没看」冒充「看过了没事」—— 本仓连续三版栽在这族病上;
    · 折成 `excluded` = 因为一次检索失败悄悄砍掉一只好票,而用户看到的只是
      「今天少了一只」,没有任何东西会报错。
两种都错,所以它自己占一格,并且**如实进报告**(「N 只未核实消息面」)。

🔴 **检索走 Tavily**:provider 由 `llm/factory.get_provider(TASK_NEWS_SCAN)` 给出 ——
那条路会把裸 provider 包成 `TavilyGroundedProvider`(V2.4.2 收口:⛔ 不用 Provider
自带联网,不被上游认识的组合会 `ok=True` **静默返 0 条**,而模型照样写得出像样的分析)。
拿不到 Tavily key → `get_provider` 返回 `None` → 全部 `unverified`,⛔ 不静默降级成
「都查过了、都干净」。

⚠ **本模块只回答「这只票该不该因为消息面被剔除」**,⛔ 不回答任何「这只票好不好」。
"""

from __future__ import annotations

import logging
import inspect
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from datetime import date
from pathlib import Path

from neckline.llm.base import LLMProvider, LLMResult
from neckline.llm.news_scan import (
    ALL_CATEGORIES,
    CATEGORY_BLOWUP,
    CATEGORY_INVESTIGATION,
    CATEGORY_REDUCTION,
    CATEGORY_REGULATORY,
    NewsScanResult,
    scan_news_for_code,
)

logger = logging.getLogger(__name__)

# 外部搜索固定三并发；不是策略参数，调用方不得扩大。
PROVIDER_CONCURRENCY = 3


class NewsState(str, Enum):
    """三态闭合枚举。⛔ 没有第四个取值,也 ⛔ 不许把其中两个当成同一件事。"""

    CLEAN = "clean"
    EXCLUDED = "excluded"
    UNVERIFIED = "unverified"


class NewsCategory(str, Enum):
    """K9 §二 末段逐字点名的四类。**闭合** —— 模型给出别的词一律不算命中。"""

    BLOWUP = CATEGORY_BLOWUP              # 爆雷
    REDUCTION = CATEGORY_REDUCTION        # 减持
    INVESTIGATION = CATEGORY_INVESTIGATION  # 立案
    REGULATORY = CATEGORY_REGULATORY      # 监管


#: 类别 → 人话(全映射,⛔ 无 fallback)。
CATEGORY_LABEL: Mapping[NewsCategory, str] = {
    NewsCategory.BLOWUP: "爆雷",
    NewsCategory.REDUCTION: "减持",
    NewsCategory.INVESTIGATION: "立案",
    NewsCategory.REGULATORY: "监管",
}
assert set(CATEGORY_LABEL) == set(NewsCategory)
assert {c.value for c in NewsCategory} == set(ALL_CATEGORIES), (
    "四类必须与 `llm/news_scan.py` 的闭合集合逐字相同 —— 两边各说各话时,"
    "模型给出的类别会静默落到没人认识的那一档,而那一档看起来就是「没命中」")


@dataclass(frozen=True)
class NewsVerdict:
    """一只票的消息面结论。"""

    ts_code: str
    state: NewsState
    category: Optional[NewsCategory] = None    # None = 未命中(clean / unverified)
    summary: str = ""
    reason: str = ""                           # `unverified` 的具体原因
    narrative: str = ""
    evidence: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    provider: str = ""
    model: str = ""

    @property
    def excluded(self) -> bool:
        return self.state is NewsState.EXCLUDED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tsCode": self.ts_code, "state": self.state.value,
            "category": None if self.category is None else self.category.value,
            "categoryLabel": None if self.category is None else CATEGORY_LABEL[self.category],
            "summary": self.summary, "reason": self.reason,
            "evidence": list(self.evidence),
            "provider": self.provider, "model": self.model,
        }


def _from_scan(ts_code: str, scan: NewsScanResult) -> NewsVerdict:
    evidence = tuple(
        {"title": h.title, "link": h.link, "publishDate": h.publish_date,
         "media": h.media}
        for h in (scan.search_hits or [])
    )
    if scan.degraded:
        # 🔴 **没查成** —— ⛔ 既不当「干净」也不当「命中」。
        return NewsVerdict(
            ts_code=ts_code, state=NewsState.UNVERIFIED,
            reason=scan.degrade_reason or "未知原因", narrative=scan.narrative,
            evidence=evidence, provider=scan.provider, model=scan.model)
    if not scan.hits:
        return NewsVerdict(ts_code=ts_code, state=NewsState.CLEAN,
                           narrative=scan.narrative, evidence=evidence,
                           provider=scan.provider, model=scan.model)
    # 命中多类时取**第一条**做主类(四类都足以剔除,主类只影响报告怎么写一句话)。
    code, summary = scan.hits[0]
    try:
        cat = NewsCategory(code)
    except ValueError:
        # 解析器的闭合标签之外的东西 —— 这不该发生;真发生了就是**没查成**,
        # ⛔ 不静默当成「干净」(那正好把一次解析故障讲成一句安心话)。
        logger.warning("[explain] %s 的消息面类别 `%s` 不在闭合枚举里,按未核实处理",
                       ts_code, code)
        return NewsVerdict(ts_code=ts_code, state=NewsState.UNVERIFIED,
                           reason=f"类别 `{code}` 不在闭合枚举里",
                           narrative=scan.narrative, evidence=evidence,
                           provider=scan.provider, model=scan.model)
    return NewsVerdict(ts_code=ts_code, state=NewsState.EXCLUDED, category=cat,
                       summary=summary, narrative=scan.narrative, evidence=evidence,
                       provider=scan.provider, model=scan.model)


def screen(
    items: Sequence[Tuple[str, Optional[str]]],
    *,
    provider: Optional[LLMProvider],
    transport: Optional[Any] = None,
    scan_fn: Optional[Callable[..., NewsScanResult]] = None,
    trade_date: Optional[date] = None,
    report_date: Optional[date] = None,
    pack_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[NewsVerdict]:
    """逐只查消息面。`items = [(ts_code, name), ...]`,**按传入顺序**逐只调用。

    ⚠ 输入顺序由调用方保证是 `ts_code` 升序(双盲第 ③ 条);本函数不再排一次
    —— 排两次会让「谁负责排序」变成一道要现场推理的题。

    ⚠ **一只票查炸了只影响它自己**:异常在这里被接住并落成 `unverified`,
    ⛔ 不掀翻整条链(那会让整天的清单卡在解释层)。
    """
    fn = scan_fn or scan_news_for_code
    # 默认扫描器会带回真实 provider/Tavily 用量；把 record 放到主线程，避免
    # SQLite 锁竞争反过来影响其他标的的联网检索。自定义测试 stub 没有该契约，
    # 不替它猜测调用/用量。
    audit_default_scan = fn is scan_news_for_code

    def one(item: Tuple[str, Optional[str]]) -> Tuple[NewsVerdict, Optional[NewsScanResult], Optional[str], int]:
        ts_code, name = item
        started = time.monotonic()
        try:
            kwargs: Dict[str, Any] = {"provider": provider, "transport": transport,
                                      "trade_date": trade_date, "report_date": report_date,
                                      "pack_id": pack_id, "db_path": db_path}
            if audit_default_scan:
                kwargs["record_usage"] = False
            # 既有测试会 monkeypatch 模块级扫描函数；按签名过滤新增的运行时参数，
            # 既保留正式调用的计量关联，也不会要求旧 stub 改签名。
            try:
                accepted = inspect.signature(fn).parameters
                if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in accepted.values()):
                    kwargs = {key: value for key, value in kwargs.items() if key in accepted}
            except (TypeError, ValueError):
                pass
            scan = fn(ts_code, name or "", **kwargs)
        except Exception as e:  # noqa: BLE001
            logger.warning("[explain] %s 消息面扫描异常,按未核实处理", ts_code, exc_info=True)
            return (NewsVerdict(ts_code=ts_code, state=NewsState.UNVERIFIED,
                                reason=f"扫描异常:{e}"), None, "调用异常",
                    int((time.monotonic() - started) * 1000))
        return _from_scan(ts_code, scan), scan, None, int((time.monotonic() - started) * 1000)

    with ThreadPoolExecutor(max_workers=PROVIDER_CONCURRENCY,
                            thread_name_prefix="nk-news") as pool:
        work = list(pool.map(one, items))

    out: List[NewsVerdict] = []
    for verdict, scan, error, duration_ms in work:
        out.append(verdict)
        if not audit_default_scan:
            continue
        try:
            from neckline.llm.usage import record
            if error is not None:
                record(task="news_scan", trade_date=trade_date, report_date=report_date,
                       pack_id=pack_id, outcome="failed", searched=True,
                       duration_ms=duration_ms, failure_reason=error, db_path=db_path)
            elif scan is not None and scan.usage_result is not None:
                result = scan.usage_result
                record(task="news_scan", result=result, trade_date=trade_date,
                       report_date=report_date, pack_id=pack_id, searched=True,
                       tavily_credits=result.tavily_credits,
                       duration_ms=scan.usage_duration_ms or duration_ms, db_path=db_path)
            else:
                record(task="news_scan", trade_date=trade_date, report_date=report_date,
                       pack_id=pack_id, outcome="skipped", failure_reason="未配置可用的 LLM provider",
                       duration_ms=duration_ms, db_path=db_path)
        except Exception:  # 审计失败不能吞掉已得到的逐票结论。
            logger.error("[explain] %s 的消息面用量审计写入失败", verdict.ts_code, exc_info=True)
    return out


def summarize(verdicts: Sequence[NewsVerdict]) -> Dict[str, int]:
    """三态计数(报告要如实写「N 只未核实」)。"""
    return {s.value: sum(1 for v in verdicts if v.state is s) for s in NewsState}


__all__ = [
    "NewsState", "NewsCategory", "CATEGORY_LABEL", "NewsVerdict",
    "PROVIDER_CONCURRENCY", "screen", "summarize",
]
