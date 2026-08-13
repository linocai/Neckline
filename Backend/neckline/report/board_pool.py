"""板块池卫生线(plan §五 v1.3-③-C3-①,写死;C1/C2 里凡是按板块聚合的展示均
须复用本模块,不得各自另起一份清单——同 `neckline.data.board` 整段正则先例)。

**背景(2026-07-26 实测发现,用户拍板剔除)**:同花顺 394 个概念板块(`ths_index`/
`ths_member`)按成交额或成分数排名时,前排被"资格 / 宽基成分类标签"霸榜——
融资融券(3837 只)、深股通(1875)、沪股通(1640)、沪深300/中证500/上证180 样本股、
专精特新(1212)、国企改革(1468)、人民币贬值受益、中报预增……这些板块成分动辄
上千只,当"拥挤度"信号用等于没信号(几乎覆盖全市场)。

**双闸剔除**(任一命中即剔,互斥归因、不重复计入):
    ① 名称模式(`_NAME_DENY_PATTERNS`,单一源,禁止各处抄)——按板块中文名关键词
       匹配,命中即剔。
    ② 成分数上限(`MAX_CONSTITUENTS`)——防未来出现名称模式未覆盖的新宽基/资格
       标签(远期防御,见该常量注释的实测校准依据)。

**⚠ 成分数上限校准依据(2026-07-26,对照真实 `ths_member.parquet` 逐一核对,
不得凭直觉调整,调整前重新跑一遍同款核对)**:不能只按"成分数大"判定垃圾板块——
真正的大市值行业主题板块同样成分数上千(机器人概念 1213 只、专精特新 1213、
人工智能 1081、新能源汽车 1051、华为概念 1005、芯片概念 908),其中**机器人概念
与芯片概念还是用户 v1.3-③-C3 五个常驻板块之二**——若上限设得比这些还低,会把
用户自己指定的常驻板块一并剔除。`MAX_CONSTITUENTS=1500` 留出安全边际(高于
上述全部合法大主题板块,又能兜住 1600+ 的资格类标签),名称模式已覆盖当前全部
真实剔除对象,成分数闸目前是纯防御性闲置(0 案例命中),不是主力剔除手段。

**名称模式误伤复核**:朴素子串匹配对"重组"命中过"重组蛋白"(生物医药主题,
与公司重组毫无关系)——已加 `_NAME_PATTERN_ALLOWLIST` 精确名称豁免。**后续新增
关键词前,应比照此法重新跑一遍真实 `ths_index.parquet` 全量核对,排查有无同类
误伤**,不要凭直觉扩充关键词列表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List

# —— 名称模式黑名单(单一源;每个关键词均已对照 2026-07-24 真实 394 板块快照核对
# 命中范围,详见模块 docstring)——————————————————————————————————————————————
_NAME_DENY_PATTERNS: tuple = (
    "融资融券", "股通", "成份股", "样本股", "指数", "专精特新", "国企改革",
    "预增", "预减", "贬值", "升值", "破净", "送转", "回购", "增减持", "摘帽",
    "ST", "次新", "富时", "MSCI", "标普", "QFII", "AH股", "转债", "参股",
    "举牌", "重组", "壳资源", "绩优", "超跌", "机构", "北交所", "创业板", "科创",
)

# 名称模式误伤豁免(精确板块名,不参与上面的关键词剔除;见模块 docstring 核对结论)。
_NAME_PATTERN_ALLOWLIST: FrozenSet[str] = frozenset({"重组蛋白"})

# 成分数上限(第二道闸,当前为防御性闲置,见模块 docstring 校准依据)。
MAX_CONSTITUENTS = 1500

_DENY_REGEX = re.compile("(?i)(" + "|".join(re.escape(p) for p in _NAME_DENY_PATTERNS) + ")")

_GATE_LABEL = {
    "name_pattern": "名称模式(资格/宽基成分类标签)",
    "constituent_cap": f"成分数>{MAX_CONSTITUENTS}",
}


@dataclass
class ExcludedBoard:
    index_code: str
    name: str
    member_count: int
    gate: str   # name_pattern | constituent_cap


@dataclass
class BoardPoolResult:
    kept: FrozenSet[str] = field(default_factory=frozenset)   # 通过卫生线的 index_code 集合
    excluded: List[ExcludedBoard] = field(default_factory=list)

    def audit_lines(self) -> List[str]:
        """人读剔除审计(plan「剔了什么要能审计,落日志或报告脚注,不许静默吞板块」)。
        按闸门分组,每组按成分数降序列出板块名(N只)。"""
        by_gate: Dict[str, List[ExcludedBoard]] = {}
        for e in self.excluded:
            by_gate.setdefault(e.gate, []).append(e)
        out: List[str] = []
        for gate, items in by_gate.items():
            names = "、".join(
                f"{e.name}({e.member_count}只)" for e in sorted(items, key=lambda x: -x.member_count)
            )
            out.append(f"{_GATE_LABEL.get(gate, gate)}剔除 {len(items)} 个:{names}")
        return out


def count_members(member_map: Dict[str, List[str]]) -> Dict[str, int]:
    """`con_code -> [index_code,...]`(`sectors.load_member_map()` 的形状)反算
    每个板块的成分数(`index_code -> 成分数`),供卫生线成分数闸使用。"""
    counts: Dict[str, int] = {}
    for boards in member_map.values():
        for idx in boards:
            counts[idx] = counts.get(idx, 0) + 1
    return counts


def invert_member_map(member_map: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """`con_code -> [index_code,...]` 反转为 `index_code -> [con_code,...]`(板块 →
    成分股列表)。C1 题材龙头 / C2 板块资金流聚合均需要这个方向,单一源不各自反转一份。"""
    inv: Dict[str, List[str]] = {}
    for con_code, boards in member_map.items():
        for idx in boards:
            inv.setdefault(idx, []).append(con_code)
    return inv


def apply_hygiene(
    index_names: Dict[str, str],
    member_counts: Dict[str, int],
    *,
    max_constituents: int = MAX_CONSTITUENTS,
) -> BoardPoolResult:
    """对全量板块(`sectors.load_index_names()` 返回的全部 394 个,不限热榜)跑
    双闸剔除。`member_counts` 由 `count_members(member_map)` 给。返回通过闸的
    `kept`(index_code 集合)+ 剔除审计明细,互斥归因(先判名称模式,再判成分数,
    命中任一即不再往下判)。"""
    kept: List[str] = []
    excluded: List[ExcludedBoard] = []
    for code, name in index_names.items():
        n = member_counts.get(code, 0)
        if name not in _NAME_PATTERN_ALLOWLIST and _DENY_REGEX.search(name):
            excluded.append(ExcludedBoard(index_code=code, name=name, member_count=n, gate="name_pattern"))
            continue
        if n > max_constituents:
            excluded.append(ExcludedBoard(index_code=code, name=name, member_count=n, gate="constituent_cap"))
            continue
        kept.append(code)
    return BoardPoolResult(kept=frozenset(kept), excluded=excluded)


__all__ = [
    "BoardPoolResult",
    "ExcludedBoard",
    "MAX_CONSTITUENTS",
    "apply_hygiene",
    "count_members",
    "invert_member_map",
]
