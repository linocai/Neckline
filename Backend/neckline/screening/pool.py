"""固定公司池（已批准的 1,089 家创业板公司）与直接点名匹配。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

_CODE = re.compile(r"(?<!\d)(30[01]\d{3})(?!\d)")


@dataclass(frozen=True)
class Company:
    code: str  # 300002.SZ
    name: str
    aliases: tuple[str, ...]

    @property
    def short_code(self) -> str:
        return self.code.split(".")[0]


class CompanyPool:
    """strict_names：简称是常用词或地名的公司（来源还会给这些词自动插代码），
    简称只在「简称：」「【简称」这类公司新闻格式里才算点名，代码也不作数；全称照常匹配。"""

    def __init__(self, companies: Iterable[Company], strict_names: Iterable[str] = ()) -> None:
        self.companies = {c.code: c for c in companies}
        by_name = {c.name: c.code for c in self.companies.values()}
        self.strict_names = tuple(strict_names)
        unknown = [n for n in self.strict_names if n not in by_name]
        if unknown:
            raise ValueError(f"常用词简称不在公司池里：{unknown}")
        self._strict = {by_name[n]: re.compile("【" + re.escape(n) + "|" + re.escape(n) + "[：:]")
                        for n in self.strict_names}
        self._by_short = {c.short_code: c.code for c in self.companies.values()}
        names: dict[str, set[str]] = {}
        for company in self.companies.values():
            for name in {company.name, *company.aliases}:
                if name and len(name) >= 2:
                    names.setdefault(name, set()).add(company.code)
        self._names = names

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping], strict_names: Iterable[str] = ()) -> "CompanyPool":
        companies = []
        for row in rows:
            index = row["index"]
            companies.append(Company(code=row["code"], name=row["name"] or index["name"],
                                     aliases=tuple(index.get("aliases") or ())))
        return cls(companies, strict_names)

    def __len__(self) -> int:
        return len(self.companies)

    def subset(self, codes: Iterable[str]) -> "CompanyPool":
        keep = [self.companies[code] for code in codes if code in self.companies]
        names = {c.name for c in keep}
        return CompanyPool(keep, [n for n in self.strict_names if n in names])

    def match(self, text: str) -> dict[str, list[str]]:
        """返回 {公司代码: [命中的名称或代码]}，只认公司名、别名和六位代码的直接点名。"""
        found: dict[str, list[str]] = {}
        for name, codes in self._names.items():
            if name in text:
                for code in codes:
                    strict = self._strict.get(code)
                    if strict and name == self.companies[code].name and not strict.search(text):
                        continue
                    found.setdefault(code, []).append(name)
        for short in set(_CODE.findall(text)):
            code = self._by_short.get(short)
            if code and code not in self._strict:
                found.setdefault(code, []).append(short)
        return found
