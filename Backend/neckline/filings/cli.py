"""人工核对公告 PDF 读数。

  python -m neckline.filings.cli <url或本地pdf路径>... --cache-dir <目录>

每份打印类型、读数 JSON 和给模型看的摘要。网络只访问巨潮静态站上的 PDF。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from neckline.filings.fetch import fetch_pdf
from neckline.filings.financials import filing_kind, parse_express, parse_forecast, parse_periodic, summary_text
from neckline.filings.reader import read_pdf

_MAX_PAGES = 20
_MAX_BYTES = 20_000_000
_TIMEOUT = 60.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="读取巨潮公告 PDF 的主要财务数字")
    parser.add_argument("targets", nargs="+", help="公告 PDF 的 URL，或本机路径")
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--max-pages", type=int, default=_MAX_PAGES)
    parser.add_argument("--max-bytes", type=int, default=_MAX_BYTES)
    parser.add_argument("--timeout", type=float, default=_TIMEOUT)
    args = parser.parse_args(argv)
    for index, target in enumerate(args.targets, start=1):
        print(f"--- {index} {target}")
        path = Path(target)
        if path.is_file():
            pdf = path
        else:
            fetched = fetch_pdf(target, cache_dir=args.cache_dir, max_bytes=args.max_bytes,
                                timeout_seconds=args.timeout)
            if fetched.error or fetched.path is None:
                print(f"error: {fetched.error}")
                continue
            pdf = fetched.path
            print(f"cache: {pdf.name} from_cache={fetched.from_cache}")
        document = read_pdf(pdf, max_pages=args.max_pages)
        if document.error and not any(page.strip() for page in document.pages):
            print(f"error: {document.error}")
            continue
        text = "\n".join(document.pages)
        kind = filing_kind(_title_hint(text))
        figures = _figures(kind, text)
        print(f"kind: {kind}")
        print("summary: " + (summary_text(figures) if figures is not None else "（非财务公告，无主要财务数字）"))
        print(json.dumps(None if figures is None else asdict(figures), ensure_ascii=False, indent=2))
    return 0


def _title_hint(text: str) -> str:
    """标题在开头几行。正文里的「会计差错更正」勾选框不是公告标题，不能拿来判断类型。"""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().isdigit()]
    return " ".join(lines[:6])[:400]


def _figures(kind: str | None, text: str):
    if kind == "periodic":
        return parse_periodic(text)
    if kind == "forecast":
        return parse_forecast(text)
    if kind == "express":
        return parse_express(text)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
