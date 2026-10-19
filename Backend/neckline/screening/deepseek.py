"""DeepSeek Chat Completions 调用，带响应回执：相同请求只计费一次。

回执以请求体的 sha256 为键。已成功的直接读回执；上次发出后结果不明（started / unknown）的
绝不自动重发、也不改判，交给上层记为缺口；明确没发出去或对方明确拒绝的（failed）才可以再发。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import httpx

_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


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


class ChatUnknown(ChatError):
    """请求可能已送达、结果不明：不重发，也不换个请求重做同一件事。"""


class ChatRejected(ChatError):
    """没有密钥，或对方以 401/402/403 拒绝：这批不重试。"""


def _rejected(status: int) -> ChatRejected:
    if status == 402:
        return ChatRejected("DeepSeek 余额不足（http:402）")
    return ChatRejected(f"DeepSeek 拒绝了请求（http:{status}）")


class Receipts(Protocol):
    def lookup(self, digest: str) -> Mapping[str, Any] | None:
        """返回 {"state": started|succeeded|failed|unknown, "response": …, "seconds": …} 或 None。"""

    def begin(self, digest: str) -> None: ...

    def succeed(self, digest: str, response: Mapping[str, Any], seconds: float) -> None: ...

    def fail(self, digest: str, code: str) -> None: ...

    def unknown(self, digest: str, code: str) -> None: ...


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class FileReceipts:
    """离线调参用：按回执协议原子写下 started / succeeded / failed / unknown。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        return self.directory / f"{digest}.json"

    def _write(self, digest: str, payload: Mapping[str, Any]) -> None:
        path = self._path(digest)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(canonical(dict(payload)), encoding="utf-8")
        tmp.replace(path)

    def lookup(self, digest: str) -> Mapping[str, Any] | None:
        path = self._path(digest)
        if not path.exists():
            return None
        saved = json.loads(path.read_text(encoding="utf-8"))
        state = saved.get("state")
        # 旧的成功回执只有 response、没有 state；那就是已成功，不能当成没发过再计费。
        if state is None and "response" in saved:
            state = "succeeded"
        if state not in ("started", "succeeded", "failed", "unknown"):
            return None
        return {"state": state, "response": saved.get("response"), "seconds": saved.get("seconds", 0.0)}

    def begin(self, digest: str) -> None:
        self._write(digest, {"request_sha256": digest, "state": "started"})

    def succeed(self, digest: str, response: Mapping[str, Any], seconds: float) -> None:
        self._write(digest, {"request_sha256": digest, "state": "succeeded", "seconds": seconds,
                             "received_at": datetime.now(timezone.utc).isoformat(), "response": response})

    def fail(self, digest: str, code: str) -> None:
        self._write(digest, {"request_sha256": digest, "state": "failed", "error_code": code})

    def unknown(self, digest: str, code: str) -> None:
        self._write(digest, {"request_sha256": digest, "state": "unknown", "error_code": code})


class DeepSeekClient:
    def __init__(self, *, endpoint: str, model: str, api_key: str | None, receipts: Receipts,
                 timeout_seconds: float, max_attempts: int, transport: httpx.BaseTransport | None = None) -> None:
        self.endpoint = endpoint
        self.model = model
        self._api_key = api_key
        self.receipts = receipts
        self.timeout = httpx.Timeout(timeout_seconds, connect=15.0)
        self.max_attempts = max_attempts
        self._transport = transport

    def request_body(self, messages: Sequence[Mapping[str, str]], options: Mapping[str, Any]) -> dict:
        body: dict[str, Any] = {"model": self.model, "messages": list(messages), "stream": False}
        body.update(options)
        return body

    def complete(self, messages: Sequence[Mapping[str, str]], options: Mapping[str, Any]) -> ChatResult:
        body = self.request_body(messages, options)
        digest = hashlib.sha256(canonical(body).encode()).hexdigest()
        saved = self.receipts.lookup(digest)
        if saved and saved["state"] == "succeeded":
            return self._result(saved["response"], digest, from_receipt=True, seconds=float(saved.get("seconds") or 0))
        if saved and saved["state"] in ("started", "unknown"):
            raise ChatUnknown("上次调用结果不明，不重发")
        if not self._api_key:
            raise ChatRejected("DeepSeek 凭据缺失")
        last = "unknown"
        for attempt in range(1, self.max_attempts + 1):
            self.receipts.begin(digest)
            started = time.monotonic()
            try:
                with httpx.Client(transport=self._transport, timeout=self.timeout) as client:
                    response = client.post(self.endpoint, json=body,
                                           headers={"Authorization": f"Bearer {self._api_key}"})
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last = f"connect:{type(exc).__name__}"  # 连接没建立，请求没发出去，可以再试
                self.receipts.fail(digest, last)
            except httpx.TransportError as exc:
                self.receipts.unknown(digest, f"transport:{type(exc).__name__}")
                raise ChatUnknown("请求可能已送达但没收到结果，不重发") from exc
            else:
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError:
                        self.receipts.unknown(digest, "body_invalid")
                        raise ChatUnknown("响应不是 JSON")
                    seconds = round(time.monotonic() - started, 2)
                    self.receipts.succeed(digest, payload, seconds)
                    return self._result(payload, digest, from_receipt=False, seconds=seconds)
                last = f"http:{response.status_code}"
                self.receipts.fail(digest, last)
                if response.status_code in (401, 402, 403):
                    raise _rejected(response.status_code)
                if response.status_code not in _RETRYABLE_STATUS:
                    break
            if attempt < self.max_attempts:
                time.sleep(5 * attempt)
        raise ChatError(last)

    @staticmethod
    def _result(payload: Mapping, digest: str, *, from_receipt: bool, seconds: float) -> ChatResult:
        choice = (payload.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        return ChatResult(content=content, usage=payload.get("usage") or {}, request_sha256=digest,
                          from_receipt=from_receipt, seconds=seconds, finish_reason=choice.get("finish_reason"))
