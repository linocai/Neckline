"""K10 已批准的唯一公司硬排。

消息来源保持全市场；这里仅在事件已映射到具体公司后工作。白酒只按申万 2021
二级行业代码识别，不能从公司名称、题材或模型措辞推断。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from neckline.data.sw_industry import BAIJIU_L2_CODE


CHINEXT = "chinext"


@dataclass(frozen=True)
class CompanyMetadata:
    company_code: str
    board: Optional[str]
    is_st: Optional[bool]
    sw_l2_code: Optional[str]
    as_of: datetime

    def __post_init__(self) -> None:
        if not self.company_code.strip():
            raise ValueError("company_code 不能为空")
        if self.as_of.tzinfo is None:
            raise ValueError("公司元数据 as_of 必须带时区")


class CompanyMetadataProvider(Protocol):
    """调用方以事件资料截止时点注入当时元数据；本协议不读取当前工作数据库。"""

    def lookup(self, *, company_code: str, as_of: datetime) -> Optional[CompanyMetadata]:
        ...


@dataclass(frozen=True)
class Eligibility:
    state: str
    reason: Optional[str] = None

    @property
    def eligible(self) -> bool:
        return self.state == "eligible"


def evaluate_company(metadata: Optional[CompanyMetadata]) -> Eligibility:
    """只应用三条已批准硬排；资料缺失是 ``insufficient_metadata``，绝不猜测。"""
    if metadata is None:
        return Eligibility("insufficient_metadata", "缺少当时公司元数据")
    missing = [name for name, value in (
        ("board", metadata.board), ("is_st", metadata.is_st), ("sw_l2_code", metadata.sw_l2_code),
    ) if value is None or (isinstance(value, str) and not value.strip())]
    if missing:
        return Eligibility("insufficient_metadata", f"缺少当时元数据：{','.join(missing)}")
    if metadata.board != CHINEXT:
        return Eligibility("excluded", "非创业板")
    if metadata.is_st is True:
        return Eligibility("excluded", "ST 或 *ST")
    if metadata.sw_l2_code == BAIJIU_L2_CODE:
        return Eligibility("excluded", f"申万白酒行业 {BAIJIU_L2_CODE}")
    return Eligibility("eligible")


__all__ = [
    "BAIJIU_L2_CODE", "CHINEXT", "CompanyMetadata", "CompanyMetadataProvider", "Eligibility",
    "evaluate_company",
]
