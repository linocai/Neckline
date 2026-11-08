"""在 NB 上把 TuShare 原始行写成回放快照。本机测试注入假数据源，不读 token、不联网。

    python -m neckline.evaluation.snapshot --codes codes.txt --out market.json [--merge market.json]
    python -m neckline.evaluation.snapshot --sector --start YYYY-MM-DD --end YYYY-MM-DD --out sector.json

--sector 写同花顺概念、成分、流通市值，以及股票池回放要用的
stock_basic、stock_company、namechange、daily_basic。同一份 JSON 也可以当作 --pool-snapshot。
token 只从环境变量 TUSHARE_TOKEN 读取。已在快照里的代码跳过。进度只打印计数。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Callable, TextIO

from neckline.evaluation.market import KINDS, MarketSource, RawMarket, TuShareSource, split_code
from neckline.morning.report import load_config
from neckline.morning.schedule import CalendarGap
from neckline.morning.sectors import MEMBER_INTERVAL_SECONDS, SectorFetchError, capture_sector_payload

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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def capture_sector(out: Path, **kwargs) -> dict:
    """抓取板块回放快照并写成 JSON。kwargs 交给 capture_sector_payload。"""
    data = capture_sector_payload(**kwargs)
    _write_json(out, data)
    return data


def _print_count(message: str, stream: TextIO) -> None:
    print(message, file=stream, flush=True)


def _sector_main(args) -> int:
    if args.out is None or not args.start or not args.end:
        print("板块快照要 --out --start --end", flush=True)
        return 2
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError:
        print("日期须为 YYYY-MM-DD", flush=True)
        return 2
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        print("未配置 TUSHARE_TOKEN", flush=True)
        return 2
    try:
        prefixes = tuple(load_config().rules["companyPool"]["prefixes"])
    except Exception as exc:
        print(f"筛选规则配置无效：{type(exc).__name__}", flush=True)
        return 2
    try:
        capture_sector(args.out, start=start, end=end, prefixes=prefixes,
                       interval_seconds=MEMBER_INTERVAL_SECONDS,
                       progress=lambda message: _print_count(message, sys.stdout))
    except (SectorFetchError, CalendarGap, ValueError) as exc:
        text = str(exc)
        if token in text:
            text = "板块快照没能取全"
        print(text, flush=True)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 TuShare 原始行情写成回放快照")
    parser.add_argument("--codes", type=Path, help="每行一个六位代码")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--merge", type=Path, help="已有快照；其中已有的代码跳过")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--sector", action="store_true", help="改抓板块和股票池回放材料")
    parser.add_argument("--start", help="板块快照起始日 YYYY-MM-DD")
    parser.add_argument("--end", help="板块快照结束日 YYYY-MM-DD")
    args = parser.parse_args(argv)
    if args.sector:
        return _sector_main(args)
    if args.codes is None or args.out is None:
        print("要 --codes 和 --out", flush=True)
        return 2
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
