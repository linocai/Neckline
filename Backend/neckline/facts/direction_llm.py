"""市场方向的事实层旁路。

只读取已经冻结的 FactPack；不 import K9、explain、playbook，也不参与任何选股决定。
每个 pack 幂等地只落一份 terminal sidecar。失败只影响报告背景。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from neckline.facts import direction_store
from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.json_block import split_narrative_and_json
from neckline.llm.usage import record
import neckline.llm.prompt_context as _prompt_context  # noqa: F401 - 调用点守门


SYSTEM = """你是盘后市场资料员。只依据给定的市场事实和联网证据，写一段不超过120字的
市场背景，并给出2至3个市场正在关注的方向。它们只是背景，不是荐股或交易建议。
不得提股票代码、候选名单、排序、目标价或买卖动作。
结尾给 JSON：{\"themes\":[{\"name\":\"方向\",\"reason\":\"依据\"}]}。"""


@dataclass(frozen=True)
class DirectionResult:
    state: str
    summary: str = ""
    themes: tuple[Dict[str, str], ...] = ()
    reason: Optional[str] = None


def _projection(pack: Any) -> str:
    market = dict(pack.market or {})
    limit_map = market.get("limitMap") or {}
    industry = market.get("industryStrength") or market.get("industries") or []
    industry_lines = []
    if isinstance(industry, list):
        for row in industry[:12]:
            if not isinstance(row, dict):
                continue
            industry_lines.append({"name": row.get("name") or row.get("l2Name"), "medianRet": row.get("medianRet")})
    fact = {"tradeDate": pack.trade_date.isoformat(), "marketMedianRet": market.get("marketMedianRet"),
            "limitUp": limit_map.get("limitUpCount"), "limitDown": limit_map.get("limitDownCount"),
            "zaban": limit_map.get("zabanCount"), "industries": industry_lines}
    return json.dumps(fact, ensure_ascii=False, separators=(",", ":"))


def _query(pack: Any) -> str:
    return f"{pack.trade_date.isoformat()} A股 盘后 市场主线 行业强弱 涨停梯队"


def run_once(pack: Any, *, provider: Optional[LLMProvider], report_date: Optional[date] = None,
             db_path: Optional[Path] = None,
             transport: Optional[Any] = None) -> Dict[str, Any]:
    claimed, existing = direction_store.claim(
        pack_id=pack.pack_id, trade_date=pack.trade_date.strftime("%Y%m%d"), db_path=db_path)
    if not claimed:
        # 已完成直接复用；仍在生成或前序任务崩溃时同样不再触发一次外部调用。
        # ``running`` 会被报告层诚实呈现为「暂未生成」，而不是伪造为成功背景。
        return existing
    if provider is None:
        record(task="market_direction", trade_date=pack.trade_date, report_date=report_date, pack_id=pack.pack_id,
               outcome="skipped", failure_reason="未配置可用的 LLM provider", db_path=db_path)
        return direction_store.complete_claim(pack_id=pack.pack_id, state="unavailable",
                                              failure_reason="方向解读暂未生成：未配置模型或联网搜索", db_path=db_path)
    started = time.monotonic()
    try:
        result = provider.chat([ChatMessage(role="system", content=SYSTEM),
                                ChatMessage(role="user", content="冻结市场事实：" + _projection(pack))],
                               enable_search=True, search_query=_query(pack), transport=transport)
    except Exception as exc:  # noqa: BLE001
        record(task="market_direction", trade_date=pack.trade_date, report_date=report_date, pack_id=pack.pack_id,
               outcome="failed", searched=True, duration_ms=int((time.monotonic()-started)*1000),
               failure_reason=f"调用异常:{type(exc).__name__}", db_path=db_path)
        return direction_store.complete_claim(pack_id=pack.pack_id, state="unavailable",
                                              failure_reason="方向解读暂未生成", db_path=db_path)
    credits = getattr(result, "tavily_credits", None)
    record(task="market_direction", result=result, trade_date=pack.trade_date, report_date=report_date, pack_id=pack.pack_id,
           searched=True, tavily_credits=credits, duration_ms=int((time.monotonic()-started)*1000), db_path=db_path)
    if not result.ok:
        return direction_store.complete_claim(pack_id=pack.pack_id, state="unavailable",
                                              provider=result.provider or None, model=result.model or None,
                                              failure_reason="方向解读暂未生成", db_path=db_path)
    narrative, block = split_narrative_and_json(result.content or "")
    raw_themes = block.get("themes") if isinstance(block, dict) else None
    themes = []
    if isinstance(raw_themes, list):
        for item in raw_themes[:3]:
            if isinstance(item, dict) and str(item.get("name") or "").strip() and str(item.get("reason") or "").strip():
                themes.append({"name": str(item["name"]).strip()[:32], "reason": str(item["reason"]).strip()[:160]})
    if not narrative.strip() or len(themes) < 2:
        return direction_store.complete_claim(pack_id=pack.pack_id, state="unavailable",
                                              provider=result.provider or None, model=result.model or None,
                                              evidence_count=len(result.search_hits), failure_reason="方向解读格式不完整", db_path=db_path)
    return direction_store.complete_claim(pack_id=pack.pack_id, state="ready",
                                          summary=narrative.strip()[:500], themes=themes, provider=result.provider or None,
                                          model=result.model or None, evidence_count=len(result.search_hits), db_path=db_path)


__all__ = ["DirectionResult", "run_once"]
