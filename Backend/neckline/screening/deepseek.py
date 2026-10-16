"""DeepSeek Chat Completions 调用，带响应回执：相同请求只计费一次。

回执以请求体的 sha256 为键保存完整响应；同一请求再次出现时直接读回执，不再外呼。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx


@dataclass
class ChatResult:
    content: str
    usage: Mapping[str, Any]
    request_sha256: str
    from_receipt: bool
    seconds: float
    finish_reason: str | None


class ChatError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DeepSeekClient:
    def __init__(self, *, endpoint: str, model: str, api_key: str | None, receipts_dir: Path,
                 timeout_seconds: float, max_attempts: int) -> None:
        self.endpoint = endpoint
        self.model = model
        self._api_key = api_key
        self.receipts_dir = receipts_dir
        self.timeout = httpx.Timeout(timeout_seconds, connect=15.0)
        self.max_attempts = max_attempts
        receipts_dir.mkdir(parents=True, exist_ok=True)

    def request_body(self, messages: Sequence[Mapping[str, str]], options: Mapping[str, Any]) -> dict:
        body: dict[str, Any] = {"model": self.model, "messages": list(messages), "stream": False}
        body.update(options)
        return body

    def complete(self, messages: Sequence[Mapping[str, str]], options: Mapping[str, Any]) -> ChatResult:
        body = self.request_body(messages, options)
        digest = hashlib.sha256(canonical(body).encode()).hexdigest()
        receipt = self.receipts_dir / f"{digest}.json"
        if receipt.exists():
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            return self._result(saved["response"], digest, from_receipt=True, seconds=saved.get("seconds", 0.0))
        if not self._api_key:
            raise ChatError("没有回执，且未提供 DeepSeek 凭据")
        last_error = "unknown"
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                response = httpx.post(self.endpoint, json=body, timeout=self.timeout,
                                      headers={"Authorization": f"Bearer {self._api_key}"})
            except httpx.TransportError as exc:
                last_error = f"transport:{type(exc).__name__}"
            else:
                if response.status_code == 200:
                    payload = response.json()
                    seconds = round(time.monotonic() - started, 2)
                    tmp = receipt.with_suffix(".tmp")
                    tmp.write_text(canonical({"request_sha256": digest, "seconds": seconds,
                                              "received_at": datetime.now(timezone.utc).isoformat(),
                                              "response": payload}), encoding="utf-8")
                    tmp.replace(receipt)
                    return self._result(payload, digest, from_receipt=False, seconds=seconds)
                last_error = f"http:{response.status_code}"
                if response.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < self.max_attempts:
                time.sleep(5 * attempt)
        raise ChatError(last_error)

    @staticmethod
    def _result(payload: Mapping, digest: str, *, from_receipt: bool, seconds: float) -> ChatResult:
        choice = (payload.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        return ChatResult(content=content, usage=payload.get("usage") or {}, request_sha256=digest,
                          from_receipt=from_receipt, seconds=seconds, finish_reason=choice.get("finish_reason"))
