"""从巨潮静态站下载公告 PDF，按路径缓存在运行目录，不进库。

只接受 http(s)://static.cninfo.com.cn/ 上、路径以 .pdf 结尾的地址。下载先写临时文件，
确认未超限且以 %PDF 开头后再改名；任何异常都收成 error，不向外抛。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

_HOST = "static.cninfo.com.cn"


@dataclass(frozen=True)
class FetchResult:
    path: Path | None
    error: str | None
    from_cache: bool


def _allowed(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return False
    if parts.hostname != _HOST:
        return False
    return parts.path.endswith(".PDF") or parts.path.endswith(".pdf")


def _cache_name(url: str) -> str:
    """用路径最后两段做文件名，例如 finalpage/2026-10-22/1225613658.PDF → 2026-10-22_1225613658.pdf。"""
    parts = [p for p in urlsplit(url).path.split("/") if p and p not in (".", "..")]
    file_name = parts[-1][:-4] if parts else "file"
    parent = parts[-2] if len(parts) >= 2 else "file"
    raw = f"{parent}_{file_name}.pdf"
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", raw).strip("._") or "file.pdf"
    return safe if safe.endswith(".pdf") else safe + ".pdf"


def fetch_pdf(url: str, *, cache_dir: Path, max_bytes: int, timeout_seconds: float,
              transport: httpx.BaseTransport | None = None) -> FetchResult:
    if not _allowed(url):
        return FetchResult(None, "url_not_allowed", False)
    partial: Path | None = None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest = cache_dir / _cache_name(url)
        if dest.is_file():
            return FetchResult(dest, None, True)
        partial = cache_dir / f".{dest.name}.{os.getpid()}.part"
        timeout = httpx.Timeout(timeout_seconds)
        headers = {"User-Agent": "Mozilla/5.0"}
        with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False, headers=headers) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return FetchResult(None, "http_error", False)
                declared = response.headers.get("Content-Length")
                if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                    return FetchResult(None, "too_large", False)
                size = 0
                with partial.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            return FetchResult(None, "too_large", False)
                        handle.write(chunk)
        with partial.open("rb") as handle:
            if not handle.read(5).startswith(b"%PDF"):
                return FetchResult(None, "not_pdf", False)
        os.replace(partial, dest)
        partial = None
        return FetchResult(dest, None, False)
    except httpx.TimeoutException:
        return FetchResult(None, "timeout", False)
    except (httpx.HTTPError, OSError):
        return FetchResult(None, "transport_error", False)
    except Exception:
        return FetchResult(None, "transport_error", False)
    finally:
        if partial is not None and partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
