"""在 NB 上把 TuShare 原始行写成回放快照。本机测试注入假数据源，不读 token、不联网。

    python -m neckline.evaluation.snapshot --codes codes.txt --out market.json [--merge market.json]

token 只从环境变量 TUSHARE_TOKEN 读取。已在快照里的代码跳过。进度只打印计数。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, TextIO

from neckline.evaluation.market import KINDS, MarketSource, RawMarket, TuShareSource, split_code

_CODE = re.compile(r"\d{6}")


def _load(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"快照不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("行情快照不是对象")
    return payload


def _present(data: dict, code: str) -> bool:
    short, full = split_code(code)
    return short in data or full in data


def read_codes(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    codes: list[str] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        text = line.strip()
        if not text:
            continue
        if _CODE.fullmatch(text) is None:
            raise ValueError(f"第 {number} 行不是六位代码")
        if text not in seen:
            seen.add(text)
            codes.append(text)
    return codes


def capture(codes: list[str], out: Path, *, source: MarketSource, merge: Path | None = None,
            concurrency: int = 4, progress: Callable[[str], None] | None = None) -> dict:
    """抓取尚未入快照的代码并写出 JSON。返回写出的对象。"""
    if concurrency < 1:
        raise ValueError("concurrency 须为正整数")
    data = _load(merge) if merge is not None else {}
    pending: list[str] = []
    seen: set[str] = set()
    for code in codes:
        if code in seen or _present(data, code):
            continue
        seen.add(code)
        pending.append(code)
    total = len(pending)

    def fetch(code: str) -> tuple[str, dict]:
        try:
            raw = source.raw(code)
        except Exception:
            short, _full = split_code(code)
            raw = RawMarket(code=short, fetched_at=None,
                            errors={kind: "transport_error" for kind in KINDS})
        return raw.code or code, raw.as_dict()

    if pending:
        workers = min(concurrency, len(pending))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for index, (code, payload) in enumerate(executor.map(fetch, pending), start=1):
                data[code] = payload
                if progress is not None:
                    progress(f"{index}/{total}")
    elif progress is not None:
        progress("0/0")
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    partial = out.with_suffix(out.suffix + ".part")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, out)
    return data


def _print_count(message: str, stream: TextIO) -> None:
    print(message, file=stream, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 TuShare 原始行情写成回放快照")
    parser.add_argument("--codes", required=True, type=Path, help="每行一个六位代码")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--merge", type=Path, help="已有快照；其中已有的代码跳过")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args(argv)
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        print("未配置 TUSHARE_TOKEN", flush=True)
        return 2
    if args.concurrency < 1:
        print("concurrency 须为正整数", flush=True)
        return 2
    try:
        codes = read_codes(args.codes)
    except (OSError, ValueError) as exc:
        print(str(exc), flush=True)
        return 2
    try:
        with TuShareSource(token, timeout=args.timeout) as source:
            capture(codes, args.out, source=source, merge=args.merge, concurrency=args.concurrency,
                    progress=lambda message: _print_count(message, sys.stdout))
    except ValueError as exc:
        print(str(exc), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
