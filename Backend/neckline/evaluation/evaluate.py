"""三级评估：每条候选一次 DeepSeek，判「推」或「不推」，连同数据包写入 evaluations。

续跑用库里的数据包，请求体不变，回执命中就不重复计费。
结果不明（gap / unknown）不重发。①或②不过关却判推，整条无效。
观点引用对不上的逐条删掉并计数，不让整条失效。
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from neckline.evaluation.market import split_code
from neckline.evaluation.prompt import CONTRACT, system_prompt, user_prompt
from neckline.morning import db
from neckline.morning.schedule import CST
from neckline.screening.deepseek import ChatError, ChatRejected, ChatUnknown

_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_REF = re.compile(r"^[ma]\d+$")
_USAGE = ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "reasoning_tokens")
_VIEWS = ("媒体", "券商", "公司")
_VERDICTS = ("推", "不推")
_QUESTIONS = ("value", "priced", "money", "price")


@dataclass
class Evaluation:
    title: str
    verdict: str
    conclusion: str
    questions: dict[str, Any]
    risk: str
    views: list[dict[str, str]]
    dropped_views: int = 0
    vetoed: bool = False  # 模型判了推，但它自己的①或②不过关：按一票否决记为不推


    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "verdict": self.verdict,
            "conclusion": self.conclusion,
            "questions": self.questions,
            "risk": self.risk,
            "views": self.views,
            **({"vetoed": True} if self.vetoed else {}),
        }


@dataclass
class EvaluationItem:
    company: str
    state: str
    verdict: str | None
    evaluation: Evaluation | None
    error: str | None


@dataclass
class _Hit:
    item: EvaluationItem
    fatal: str | None = None
    counted_call: bool = False
    from_receipt: bool = False
    usage: dict[str, int] | None = None
    dropped: int = 0
    vetoed: bool = False


@dataclass
class EvaluationRun:
    items: list[EvaluationItem]
    stats: dict[str, Any]
    prepare_seconds: float = 0.0
    fatal: str | None = None


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _refs(package: Mapping) -> set[str]:
    found: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and _REF.fullmatch(value):
            found.add(value)

    for message in package.get("messages") or []:
        if isinstance(message, Mapping):
            add(message.get("id"))
    original = package.get("original") if isinstance(package.get("original"), Mapping) else {}
    for doc in original.get("documents") or []:
        if isinstance(doc, Mapping):
            add(doc.get("id"))
    return found


def _questions(raw: object) -> dict[str, Any] | str:
    if not isinstance(raw, Mapping):
        return "questions 不是对象"
    parsed: dict[str, Any] = {}
    for key in _QUESTIONS:
        block = raw.get(key)
        if not isinstance(block, Mapping):
            return f"questions 缺少 {key}"
        text = _text(block.get("text"))
        if not text:
            return f"{key}.text 为空"
        if key in ("value", "priced"):
            passed = block.get("pass")
            if not isinstance(passed, bool):
                return f"{key}.pass 不是布尔"
            parsed[key] = {"pass": passed, "text": text}
        else:
            parsed[key] = {"text": text}
    return parsed


def _views(raw: object, refs: set[str]) -> tuple[list[dict[str, str]], int] | str:
    if not isinstance(raw, list):
        return "views 不是列表"
    kept: list[dict[str, str]] = []
    dropped = 0
    for item in raw:
        if not isinstance(item, Mapping):
            dropped += 1
            continue
        by = _text(item.get("by"))
        name = _text(item.get("name"))
        text = _text(item.get("text"))
        ref = item.get("ref")
        ref_text = ref.strip() if isinstance(ref, str) else ""
        if by not in _VIEWS or not name or not text or ref_text not in refs:
            dropped += 1
            continue
        kept.append({"by": by, "name": name, "text": text, "ref": ref_text})
    return kept, dropped


def parse_output(content: str, package: Mapping) -> Evaluation | str:
    """解析一条评估输出。不合格时返回原因，不返回猜测后的结论。"""
    try:
        data = json_loads(content)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return "输出不是 JSON 对象"
    verdict = _text(data.get("verdict"))
    if verdict not in _VERDICTS:
        return "verdict 无效"
    title = _text(data.get("title"))
    if not title or len(title) > 40:
        return "title 无效"
    conclusion = _text(data.get("conclusion"))
    if not conclusion:
        return "conclusion 为空"
    risk = _text(data.get("risk"))
    if not risk:
        # 模型偶尔把风险写进 questions 里；只认这一种放错位置，内容照原文取，不补写
        nested = data.get("questions", {}).get("risk") if isinstance(data.get("questions"), dict) else None
        risk = _text(nested.get("text")) if isinstance(nested, dict) else _text(nested)
    if not risk:
        return "risk 为空"
    questions = _questions(data.get("questions"))
    if isinstance(questions, str):
        return questions
    vetoed = verdict == "推" and (not questions["value"]["pass"] or not questions["priced"]["pass"])
    if vetoed:
        verdict = "不推"  # 自相矛盾时以它自己的①②为准，宁缺毋滥
    views = _views(data.get("views"), _refs(package))
    if isinstance(views, str):
        return views
    kept, dropped = views
    return Evaluation(title=title, verdict=verdict, conclusion=conclusion, questions=questions, risk=risk,
                      views=kept, dropped_views=dropped, vetoed=vetoed)


def json_loads(content: str) -> Any:
    return json.loads(content)


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _usage(raw: Mapping | None) -> dict[str, int]:
    data = raw or {}
    details = data.get("completion_tokens_details")
    if not isinstance(details, Mapping):
        details = {}
    reasoning = data.get("reasoning_tokens")
    if reasoning is None:
        reasoning = details.get("reasoning_tokens")
    return {
        "prompt_tokens": _as_int(data.get("prompt_tokens")),
        "completion_tokens": _as_int(data.get("completion_tokens")),
        "prompt_cache_hit_tokens": _as_int(data.get("prompt_cache_hit_tokens")),
        "reasoning_tokens": _as_int(reasoning),
    }


def _empty_usage() -> dict[str, int]:
    return {key: 0 for key in _USAGE}


def _company_code(candidate: object) -> str:
    raw = getattr(candidate, "company", None)
    if raw is None:
        raw = getattr(candidate, "code", None)
    short, _full = split_code(str(raw or ""))
    if len(short) != 6 or not short.isdigit():
        raise ValueError(f"公司代码无效：{raw}")
    return short


def _cutoff_reached(now: datetime, cutoff: str) -> bool:
    if now.tzinfo is None:
        raise ValueError("时间必须带时区")
    current = now.astimezone(CST)
    hour, minute = cutoff.split(":")
    mark = current.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    return current >= mark


def _check_settings(settings: Mapping) -> tuple[str, int, Mapping]:
    if settings.get("contract") != CONTRACT:
        raise ValueError("evaluation.contract 须为 evaluation-v1")
    cutoff = settings.get("cutoff")
    if not isinstance(cutoff, str) or _HHMM.fullmatch(cutoff) is None:
        raise ValueError("evaluation.cutoff 须为 HH:MM")
    workers = settings.get("concurrency")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("evaluation.concurrency 须为正整数")
    options = settings.get("modelOptions")
    if not isinstance(options, Mapping):
        raise ValueError("evaluation.modelOptions 类型不对")
    return cutoff, workers, options


def _reuse(company: str, row: Mapping) -> EvaluationItem | None:
    state = row.get("state")
    if state == "prepared":
        return None
    if state == "succeeded" and isinstance(row.get("output"), Mapping):
        output = row["output"]
        questions = output.get("questions") if isinstance(output.get("questions"), Mapping) else {}
        views = output.get("views") if isinstance(output.get("views"), list) else []
        evaluation = Evaluation(
            title=str(output.get("title") or ""), verdict=str(row.get("verdict") or ""),
            conclusion=str(output.get("conclusion") or ""), questions=dict(questions),
            risk=str(output.get("risk") or ""), views=list(views),
        )
        return EvaluationItem(company, "succeeded", row.get("verdict"), evaluation, None)
    if state in ("succeeded", "invalid", "gap"):
        return EvaluationItem(company, str(state), None, None, row.get("error"))
    return EvaluationItem(company, str(state), None, None, row.get("error"))


def _settle(db_path: Path, *, report_id: str, company: str, state: str, settled_at: datetime,
            verdict: str | None = None, output: Any = None, error: str | None = None,
            usage: Mapping | None = None, seconds: float | None = None) -> None:
    with db.write(db_path) as conn:
        db.settle_evaluation(conn, report_id=report_id, company=company, state=state, settled_at=settled_at,
                             verdict=verdict, output=output, error=error, usage=usage, seconds=seconds)


def _from_output(company: str, parsed: Evaluation) -> EvaluationItem:
    return EvaluationItem(company, "succeeded", parsed.verdict, parsed, None)


def evaluate_candidates(candidates: list, *, report_id: str, db_path: Path, prepare: Callable[[object], Mapping],
                        client: Any, settings: Mapping, clock: Callable[[], datetime]) -> EvaluationRun:
    """准备（已有则沿用库里的包）然后评估。succeeded / invalid / gap 不重做。"""
    cutoff, workers, options = _check_settings(settings)
    system = system_prompt()
    jobs: list[tuple[str, Mapping]] = []
    prepared_at = time.monotonic()
    for candidate in candidates:
        company = _company_code(candidate)
        with db.read(db_path) as conn:
            existing = db.get_evaluation(conn, report_id, company)
        if existing is None:
            package = prepare(candidate)
            if not isinstance(package, Mapping):
                raise TypeError("数据包不是对象")
            with db.write(db_path) as conn:
                db.insert_prepared(conn, report_id=report_id, company=company, contract=CONTRACT,
                                   package=package, prepared_at=clock())
                existing = db.get_evaluation(conn, report_id, company)
        if existing is None:
            raise RuntimeError(f"评估没有写入：{company}")
        jobs.append((company, existing))
    prepare_seconds = round(time.monotonic() - prepared_at, 3)

    items: list[EvaluationItem | None] = [None] * len(jobs)
    pending: list[tuple[int, str, Mapping]] = []
    for index, (company, row) in enumerate(jobs):
        reused = _reuse(company, row)
        if reused is not None:
            items[index] = reused
        else:
            pending.append((index, company, row["package"]))

    hits: list[_Hit] = []

    def run(job: tuple[int, str, Mapping]) -> tuple[int, _Hit]:
        index, company, package = job
        now = clock()
        if _cutoff_reached(now, cutoff):
            _settle(db_path, report_id=report_id, company=company, state="gap", settled_at=now,
                    error="收口时刻前没评完")
            item = EvaluationItem(company, "gap", None, None, "收口时刻前没评完")
            return index, _Hit(item=item)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user_prompt(package)}]
        try:
            result = client.complete(messages, options)
        except ChatRejected as exc:
            return index, _Hit(item=EvaluationItem(company, "prepared", None, None, str(exc)),
                               fatal=str(exc), counted_call=True)
        except ChatUnknown as exc:
            sent = "上次调用结果不明" not in str(exc)
            _settle(db_path, report_id=report_id, company=company, state="gap", settled_at=clock(), error=str(exc))
            return index, _Hit(item=EvaluationItem(company, "gap", None, None, str(exc)), counted_call=sent)
        except ChatError as exc:
            text = f"调用失败：{exc}"
            _settle(db_path, report_id=report_id, company=company, state="gap", settled_at=clock(), error=text)
            return index, _Hit(item=EvaluationItem(company, "gap", None, None, text), counted_call=True)
        usage = _usage(result.usage)
        receipt = bool(result.from_receipt)
        common = {"counted_call": not receipt, "from_receipt": receipt, "usage": usage}
        if result.finish_reason != "stop":
            reason = f"finish:{result.finish_reason}"
            _settle(db_path, report_id=report_id, company=company, state="invalid", settled_at=clock(),
                    error=reason, output=result.content, usage=usage, seconds=result.seconds)
            return index, _Hit(item=EvaluationItem(company, "invalid", None, None, reason), **common)
        parsed = parse_output(result.content, package)
        if isinstance(parsed, str):
            _settle(db_path, report_id=report_id, company=company, state="invalid", settled_at=clock(),
                    error=parsed, output=result.content, usage=usage, seconds=result.seconds)
            return index, _Hit(item=EvaluationItem(company, "invalid", None, None, parsed), **common)
        _settle(db_path, report_id=report_id, company=company, state="succeeded", settled_at=clock(),
                verdict=parsed.verdict, output=parsed.as_dict(), usage=usage, seconds=result.seconds)
        return index, _Hit(item=_from_output(company, parsed), dropped=parsed.dropped_views,
                           vetoed=parsed.vetoed, **common)

    call_started = time.monotonic()
    if pending:
        with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as executor:
            for index, hit in executor.map(run, pending):
                items[index] = hit.item
                hits.append(hit)
    call_seconds = round(time.monotonic() - call_started, 3)

    done = [item for item in items if item is not None]
    usage = _empty_usage()
    calls = from_receipt = dropped = vetoed = 0
    fatal = None
    verdicts = {"推": 0, "不推": 0}
    invalid = gap = 0
    for hit in hits:
        if hit.counted_call:
            calls += 1
        if hit.from_receipt:
            from_receipt += 1
        if hit.usage:
            for key in _USAGE:
                usage[key] += hit.usage.get(key, 0)
        dropped += hit.dropped
        vetoed += hit.vetoed
        if hit.fatal and fatal is None:
            fatal = hit.fatal
    for item in done:
        if item.state == "succeeded" and item.verdict in verdicts:
            verdicts[item.verdict] += 1
        elif item.state == "invalid":
            invalid += 1
        elif item.state == "gap":
            gap += 1
    stats = {
        "candidates": len(done),
        "calls": calls,
        "fromReceipt": from_receipt,
        "usage": usage,
        "seconds": call_seconds,
        "verdicts": verdicts,
        "invalid": invalid,
        "gap": gap,
        "droppedViews": dropped,
        "vetoed": vetoed,
    }
    return EvaluationRun(items=done, stats=stats, prepare_seconds=prepare_seconds, fatal=fatal)
