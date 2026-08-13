"""④ 核心关的**机械读数层**(plan §五 V2.2-③-C2,🔴 **2026-08-09 用户裁定 #12**)。

════════════════════════════════════════════════════════════════════════════
🔴 **本模块零阈值、零及格线、零结论**(与 `scan/landing.py` 同款处置,裁定 #11/#12
是同一条道理的两次落地):机械侧**只把数算对、把缺的说清楚**,「这只是不是它那一群
的龙头」由 LLM 在 ⑤ `basket_reason` 那**一次**调用里判(⛔ LLM 增量仍是 0)。
⛔ 任何形如「≥ X 即通过」「行业内前 X%」的东西**一律不许出现在本模块里** —— 那正是
被裁定 #12 推翻的东西(守门单测 AST 扫死)。

**为什么改(如实登记,plan ③-C2 原文)**:核心关原判据 `leader_rs_rank ≤ 3` 取数自
`leader_structure_daily`,而那张表是**簇内**口径 —— 簇 = **当天一起涨停**且同行业/
同概念的 ≥2 只票。生产实测(20260807):全市场涨停 75 只 → 181 个簇 → 814 行,去重
**只有 75 只票有 `rs_rank`,占全市场 1.4%**,其余 98.6% 判不出核心地位 → `blocks_t1`
→ 结构性进不了 T1。**根子上的矛盾不是覆盖率,是尺子的取数域**:K8 三个引擎找的都是
「还没怎么涨、刚要动」的票(C1 健康回调后再启动 / Z1 早期右侧启动 / Y1 平台完成后
启动早期),这道关的入场券却是「今天必须涨停」—— **涨停是结果,K8 要的是结果之前
的那一刻**。⚠ **`≤3` 这个数本身没问题**(provenance = `audited`,H10 十二格审计),
错的是把它架在簇内口径上;挪到行业域后同一个「3」意思完全变了,**⛔ 不许直接搬**。

**「那一群」的取数域自 V2.4.0 P1.3 起是 `comparison_domain`(比较域)**:K8 §五-4 的
顺位是 ① 同一主要驱动的候选成员域 → ② 同一题材 / 方向 → ③ 传统行业分类。
🔴 **2026-08-12 用户裁定 #1(逐字)**:「本版只实现『共同驱动域 → 行业域』。题材域
明确记录 `theme_domain_not_implemented`,⛔ 不得使用 `ths_member` 参与判定。」
→ 本模块只产出 ①③ 两层,② 层**永不产出**(唯一实现 `resolve_comparison_domain()`)。
⛔ **概念板块 `ths_member` 一行都不许进判定路径**(`CLAUDE.md` v1.4-② 定案:概念板块
只做展示、不再是任何判据的数据源 —— 这条纪律因裁定 #1 完好无损);⛔ 也不许拿
`driver_kind=theme` 的种子成分顶替(候选 C 未被选中)。

**③ 层的读数(`industry_*` 六项)口径一字未动**:`stock_basic.industry`,一只票恰好
一个行业、100% 覆盖(110 个行业/日)。P1.3 明写「现有强度 / 辨识度 / 名次读数**原样
保留**」—— 比较域只是**多告诉 LLM 一句「你该拿它跟谁比、那一群有多大」**(五个审计
字段),⛔ **不改这六个读数的分母**(改了它们就名不副实,`industry_rs_rank_20d` 却按
驱动域算 = 又一处同名不同物)。

**要的是角色感知的核心资格,不再是「所有角色都得是龙头」**(V2.4.0 §2.10-A 取代
2026-08-09 裁定 12-c 的"一把尺"):`leader` 要率先转强、`core` 是容量核心 / 资金态度
代表、`elastic` 只需相关 + 位置合理 + 弹性突出。⛔ 但**机械侧仍然零容量读数**
(裁定 12-c 的这一半没变):`core` 那句「容量核心」是让 LLM 用它已有的证据说事,
**不是**让本模块新增市值 / 流通盘 / 承接类的量。⛔ 别"顺手补一个流通市值"。

**成本纪律(裁定 #12 明令)**:核心关读数**只需要候选成员那几只**,⛔ **不许为它新建
第三张全市场预计算表**。本模块按需现算:一次 20 交易日的 `daily` 年分区扫描(约
5500 × 20 ≈ 11 万行,列投影 6 列)+ 判定日一张 `limit_derived` 稀疏表 + 一张
`leader_structure_daily`。⚠ **本地实测不是生产结论**(CLAUDE.md 铁律);若日后确有
性能证据要求预计算,先量再建、⛔ 别先建。

**缺数不猜**:某读数算不出 → 进 `missing`(原因码见 `REASON_*`),**⛔ 不填 0、不填
默认值** —— 喂给 LLM 的必须是「这项没取到」而不是一个假数。

**`limit_derived` 是稀疏表(返工时真踩过,登记防回归)**:`data/limit_derived.py`
只落涨停/跌停/炸板命中行,某票某日**不在表里 = 「三者皆不成立」的确定事实**
(`consec_limit_up_days` 的那一天就是 0),**⛔ 不是「不知道」**。真正的「不知道」
只有一种:那一天的**分区文件本身不存在**(`limit_data_unavailable`)。全市场 5526 票
里单日只有 ~80 只在这张表里有行 —— 把「查无此行」当缺数,等于把 98% 的确定事实
错报成"没取到"。

**反向守门**:本模块零 import `neckline.report.score_display`、零 import
`neckline.sentinel.*`、零 import `neckline.selection.gates`(gates 只消费随成员带
下来的读数,方向单一)。
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import polars as pl

from neckline.data.market_data import _scan_table, _years_in_range, day_file_path
from neckline.report.industry_strength import _ret1d_from_daily, load_industry_map

logger = logging.getLogger(__name__)


# —— 读数键名契约(🔴 唯一源;写侧 = 本模块,读侧 = `selection/aggregate.py` 的
# prompt 装配与 `selection/gates.py` 的留痕。⛔ 不许改名、不许在别处抄第二份)。
# 分组 = plan §五 ③-C2 读数表逐行。⚠ 全部是**事实读数**,⛔ 无一条带阈值。————————
# ⚠ **标签刻意写短**:每只候选在 prompt 里占一行,长标签 ×N 只票会把上下文撑爆
# (成本铁律的另一面);每个读数「到底是什么」由 `CORE_METRIC_LEGEND` **在整段
# prompt 里解释一次**,⛔ 不逐行重复。
CORE_METRIC_GROUPS: Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...] = (
    # 🔴 分母必须给:没有它,「第 3 名」是 3/8 还是 3/80 完全没法读(裁定 #12 原文)。
    ("这一群有多大", (
        ("industry_member_count", "行业成员数"),
        ("industry_rs_ranked_count_20d", "20日可比数"),
    )),
    ("谁最强", (
        ("industry_rs_rank_20d", "20日名次"),
        ("industry_rs_pct_20d", "20日分位"),
    )),
    ("今天谁在领", (
        ("industry_ret_rank_1d", "当日名次"),
    )),
    ("龙头形态", (
        ("consec_limit_up_days", "连板高度"),
    )),
    ("资金辨识度", (
        ("industry_amount_share", "成交额占比"),
    )),
    # 补充读数:**有簇才给、缺席不挡任何档**(裁定 12-a)。由 `aggregate` 按该篮子
    # 解析出的簇填入(簇的选取依赖篮子声明的种子,本模块拿不到)——填充逻辑收在
    # `merge_cluster_supplement()` 一处,⛔ 调用方不自己拼键。
    ("补充·涨停簇内", (
        ("cluster_rs_rank", "簇内RS名次"),
        ("cluster_amount_share", "簇内额占比"),
        ("cluster_size", "簇内成员数"),
    )),
)

# 读数口径说明(**整段 prompt 里出现一次**;⛔ 里面一个阈值 / 及格线都没有,
# 只解释「这个数是什么、分母是谁」——裁定 #12 的 🔴 分母条款靠这一段落地)。
CORE_METRIC_LEGEND = (
    "读数口径:下面六项**一律按该票所属行业算**(一票一行业,100% 覆盖);"
    "「比较域」那一行说的是**该拿它跟谁比**(K8 §五-4 顺位:同一主要驱动的候选成员域 → "
    "同行业),两者**可能不是同一群** —— 比较域是 driver 时,名次读数仍是行业口径,"
    "请据此换算、⛔ 不要把行业名次当成驱动域内的名次;"
    "「行业成员数」= 当日该行业有行情的票数,也是「当日名次」与「成交额占比」的分母;"
    "「20日可比数」= 其中 20 日收益算得出来的票数,是「20日名次」的分母"
    "(⚠ 两个分母**不一样**,停牌 / 次新凑不满 20 根 bar 的票排不进 20 日名次);"
    "名次一律 **1 = 最强**;「20日分位」0~1、**1 = 最强**;"
    "「连板高度」= 截至当日的连续涨停天数(0 = 当日没涨停);"
    "「簇内」三项只有该票当日在涨停簇里才有,**取不到不代表它不是龙头**。"
)
CORE_METRIC_KEYS: Tuple[str, ...] = tuple(
    k for _group, items in CORE_METRIC_GROUPS for k, _label in items
)
# 三种读数的分界(守门单测按它对拍,⛔ 三者不许有交集、并集必须等于全集):
#   · 簇内补充 —— caller(`aggregate`)按该篮子选定的簇填,**缺席不挡任何档**;
#   · 逐票读数 —— 只需要这一只票自己的数据,**与行业映射无关**(行业查不到也照给);
#   · 行业域读数 —— 需要「它那一群」的全体成员,行业查不到就整组算不出。
CLUSTER_METRIC_KEYS: Tuple[str, ...] = (
    "cluster_rs_rank", "cluster_amount_share", "cluster_size",
)
STOCK_METRIC_KEYS: Tuple[str, ...] = ("consec_limit_up_days",)
INDUSTRY_METRIC_KEYS: Tuple[str, ...] = tuple(
    k for k in CORE_METRIC_KEYS
    if k not in CLUSTER_METRIC_KEYS and k not in STOCK_METRIC_KEYS
)


# ══════════════════════════════════════════════════════════════════════════
# 比较域(V2.4.0 P1.3;K8 §五-4「核心比较域按以下顺序确定」+ 2026-08-12 裁定 #1)
#
# 🔴 **五个审计字段恒落库、恒进 prompt**(裁定 #1 原文)——LLM 与事后审计都必须看得
# 见「这次是拿它跟谁比的、那一群有多大」。它们**不进 `CORE_METRIC_KEYS`**:那份是
# **数值读数**的键名契约(三分法守门按它对拍),比较域是**描述这次比较的元数据**,
# 混进去会让「读数」这个词同时指两种东西。
#
# 🔴 **回退触发条件是定义、不是阈值**(P1.3 逐字):「除自己以外的同域成员数 = 0」
# 才回退下一层 —— 自己跟自己比 = 没有比较域。⛔ **不许设 `peer_count ≥ N` 这类门槛**
# (够不够比由 LLM 看着 `peer_count` 判,审计规格 P1.3 末句「不增加机械及格线」)。
# ⚠ 上产后首周要回看 `peer_count` 分布,**⛔ 不因分布难看就加门槛**(裁定 #1 末句)。
# ══════════════════════════════════════════════════════════════════════════

COMPARISON_DOMAIN_DRIVER = "driver"      # ① 同一主要驱动的候选成员域(篮子合并前的呈现全集)
COMPARISON_DOMAIN_THEME = "theme"        # ② 同一题材 / 方向 —— 🔴 **本版永不产出**(裁定 #1)
COMPARISON_DOMAIN_INDUSTRY = "industry"  # ③ 传统行业分类(`stock_basic.industry`)
COMPARISON_DOMAINS: Tuple[str, ...] = (
    COMPARISON_DOMAIN_DRIVER, COMPARISON_DOMAIN_THEME, COMPARISON_DOMAIN_INDUSTRY,
)

# `domain_fallback_reason` 的取值(**只此三种**,⛔ 不许再编第四种):
DOMAIN_FALLBACK_NONE = ""                                    # 没回退 —— 用的就是 ① 驱动域
# ① 无同域成员 → ② 本版未实现(裁定 #1)→ 落到 ③ 行业域。
# ⚠ 「① 为什么没用上」不另设字段:`comparison_domain=industry` + 本原因码 **只可能**
# 由「驱动域除自己外 0 只」触发(那是唯一的回退触发条件),不存在第二种读法。
DOMAIN_FALLBACK_THEME_NOT_IMPLEMENTED = "theme_domain_not_implemented"
# ①③ 都没有同域成员(该票无行业映射 / 行业内当日只有它自己)—— **第三态**:
# 「没有比较域」既不是 driver 也不是 industry,`comparison_domain` 落 `None`。
# ⚠ 前缀刻意与上一条相同:按 `theme_domain_not_implemented` 过滤能同时捞到两者。
DOMAIN_FALLBACK_NO_COMPARABLE_DOMAIN = "theme_domain_not_implemented;no_comparable_domain"

# 五个审计字段的键名(唯一源;写侧 = 本模块,读侧 = `aggregate` 的 prompt 装配与
# `gates` 的留痕)。⛔ 不许改名、不许在别处抄第二份。
DOMAIN_AUDIT_KEYS: Tuple[str, ...] = (
    "comparison_domain", "comparison_domain_key", "peer_codes", "peer_count",
    "domain_fallback_reason",
)


DOMAIN_LABELS: Dict[Optional[str], str] = {
    COMPARISON_DOMAIN_DRIVER: "同一主要驱动的候选成员域",
    COMPARISON_DOMAIN_THEME: "同一题材/方向",
    COMPARISON_DOMAIN_INDUSTRY: "同行业(传统行业分类)",
    None: "**没有可用的比较域**",
}


def format_comparison_domain(
    metrics: Any, *, list_codes: bool = False, codes_hint: str = "",
) -> str:
    """比较域五字段 → prompt 里的一行人读串(**唯一格式化点**)。

    `list_codes=False`(prompt 缺省):只写「域 · 标识 · 同域成员数 · 回退原因」——
      🔴 **刻意不逐一列码**:驱动域的成员就是这颗种子上面那份清单(`codes_hint` 明说
      这一点),行业域动辄几十上百只,逐行列出去会把上下文撑爆而模型什么也做不了。
      **完整 `peer_codes` 恒落 `gate_evaluations.evidence_json`**(K8 §五-4 要的「保存
      同域成员」在审计侧一条不缺),这是 prompt 体积与审计完整性的刻意分工。
    `list_codes=True`:逐一列码(诊断 / 单测用)。
    """
    if not isinstance(metrics, Mapping):
        return "**本次未取得**"
    domain = metrics.get("comparison_domain")
    key = metrics.get("comparison_domain_key")
    count = metrics.get("peer_count")
    why = str(metrics.get("domain_fallback_reason") or "")
    bits = [DOMAIN_LABELS.get(domain, str(domain))]
    if key:
        bits.append(f"标识 {key}")
    bits.append(f"同域成员 {count if count is not None else '未取到'} 只(不含它自己)")
    if list_codes:
        codes = metrics.get("peer_codes") or []
        bits.append("成员 " + ("、".join(str(c) for c in codes) if codes else "无"))
    elif codes_hint and domain == COMPARISON_DOMAIN_DRIVER:
        bits.append(codes_hint)
    if why:
        bits.append(f"回退原因 {why}")
    return ";".join(bits)


def resolve_comparison_domain(
    code: str,
    *,
    driver_peer_codes: Sequence[str] = (),
    driver_domain_key: str = "",
    industry: Optional[str] = None,
    industry_peer_codes: Sequence[str] = (),
) -> Dict[str, Any]:
    """这只票**这一次**的比较域 → 五个审计字段(**全仓唯一实现**,⛔ 不抄第二份)。

    `driver_peer_codes`:该篮子**合并前各 seed 的 presented member universe**(并集)
      —— 🔴 K8 §五-4 原文「不使用最终入篮的一至三只股票形成自证循环」,故 ⛔ **不许
      传最终成员**。`driver_domain_key` = 该域的标识(种子编号,多颗用 `+` 连,升序)。
    `industry_peer_codes`:同行业**当日有行情行**的成员码(与 `industry_member_count`
      同一个横截面 —— 两个数必须来自同一份数据,否则「N 只里的第 3 名」对不上)。

    **`peer_codes` / `peer_count` 一律不含自己**(「除自己以外的同域成员数」是回退
    判据的字面口径);故 `peer_count` = `industry_member_count − 1`(自己有行情时)。
    """
    me = str(code or "")
    driver_peers = tuple(sorted({c for c in driver_peer_codes if c and c != me}))
    if driver_peers:
        return {
            "comparison_domain": COMPARISON_DOMAIN_DRIVER,
            "comparison_domain_key": str(driver_domain_key or ""),
            "peer_codes": list(driver_peers),
            "peer_count": len(driver_peers),
            "domain_fallback_reason": DOMAIN_FALLBACK_NONE,
        }
    # ② 同一题材 / 方向:🔴 **本版整层跳过**(裁定 #1)—— ⛔ 不得使用 `ths_member`
    # 参与判定、⛔ 也不得用 `driver_kind=theme` 的种子成分顶替(候选 C 未被选中)。
    industry_peers = tuple(sorted({c for c in industry_peer_codes if c and c != me}))
    if industry and industry_peers:
        return {
            "comparison_domain": COMPARISON_DOMAIN_INDUSTRY,
            "comparison_domain_key": industry,
            "peer_codes": list(industry_peers),
            "peer_count": len(industry_peers),
            "domain_fallback_reason": DOMAIN_FALLBACK_THEME_NOT_IMPLEMENTED,
        }
    return {
        "comparison_domain": None,
        "comparison_domain_key": industry or None,
        "peer_codes": [],
        "peer_count": 0,
        "domain_fallback_reason": DOMAIN_FALLBACK_NO_COMPARABLE_DOMAIN,
    }


# —— `missing` 的原因码词汇(唯一定义;喂 LLM 时原样透传,让它知道"没取到"具体是
# 哪一类,而不是笼统一个 null)。⚠ **原因码不是判据**,只是诚实披露。————————————
REASON_INDUSTRY_UNMAPPED = "industry_unmapped"          # stock_basic.industry 查无该票
REASON_NO_DAILY_ROW = "no_daily_row"                     # 当日无 daily 行(停牌 / 未上市)
REASON_INSUFFICIENT_HISTORY = "insufficient_history"     # 20 日窗口未凑满(该票交易行不够长)
REASON_INDUSTRY_TOO_SMALL = "industry_too_small"         # 行业内可比成员 < 2,分位无定义
REASON_AMOUNT_UNAVAILABLE = "amount_unavailable"         # 成交额缺失 / 行业当日总额为 0
REASON_LIMIT_DATA_UNAVAILABLE = "limit_data_unavailable"  # limit_derived 分区文件缺失
REASON_DAILY_DATA_UNAVAILABLE = "daily_data_unavailable"  # 行情表整段取不到(本次全员缺)
# ⚠ 下面两个**语义相反,⛔ 不许合并**(稀疏表那一课的同款):
REASON_NOT_IN_CLUSTER = "not_in_cluster"                 # 确定事实:当日不在任何涨停簇
REASON_CLUSTER_DATA_UNAVAILABLE = "cluster_data_unavailable"  # 簇表当日整段取不到 = 不知道


# —— 窗口/定义类常量(**事实口径的定义,不是及格线**,照 `scan/landing.py` 的既有
# 分工:窗口长度是"这个量是什么"的一部分,⛔ 不进策略包、⛔ 不是可调阈值)。————
RS_WINDOW_DAYS = 20              # 「20 日收益」的 20(字面即窗口长度)
CONSEC_LIMIT_LOOKBACK_DAYS = 15  # 连板高度回看上限(右截尾:15 = 「≥15」,见下)
_MIN_RANKABLE = 2                # 分位需要至少两个可比成员(数学定义,不是门槛)


@dataclass(frozen=True)
class CoreMetricsResult:
    """一天的核心关行业域读数。

    `available` **永远不挂在「读表成功」上**(CLAUDE.md P0-39):它等于「判定日的
    `daily` 横截面真的取到了行」= 本次真的算过。零行有两种相反成因(真没有 / 压根
    没跑),混成一句就把系统缺席讲成了市场判断。
    """

    metrics: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    missing: Dict[str, Dict[str, str]] = field(default_factory=dict)
    available: bool = False
    # V2.4.0 P1.3:行业 → **当日有行情行**的该行业成员码(升序)。比较域 ③ 的
    # `peer_codes` 取自这里 —— 与 `industry_member_count` **同一个横截面**(⛔ 别改成
    # 从 `stock_basic` 全量映射取:那份含停牌/未上市的票,会让「N 只里的第 3 名」
    # 的 N 与列出来的成员对不上,而且看不出来)。
    industry_peers: Dict[str, Tuple[str, ...]] = field(default_factory=dict)


def _recent_trading_days_before(d: date, n: int) -> List[date]:
    """`d` 严格之前最近 `n` 个交易日,升序(`scan/landing.py` 同名体例)。"""
    from neckline.calendar import prev_trading_day

    out: List[date] = []
    cur = d
    for _ in range(n):
        cur = prev_trading_day(cur)
        out.append(cur)
    return list(reversed(out))


def _rank_within_industry(
    df: pl.DataFrame, value_col: str, rank_col: str,
) -> pl.DataFrame:
    """行业内名次(1 = 最强),**先排定确定性 tie-break 再 ordinal**。

    ⚠ 这不是可选的讲究:`rank(method="ordinal")` 的并列由**行序**打散,而行序会随
    「读的是按年块还是单日分区」变化 → 同一天算出两种名次(2026-07-29 真数据打出来
    的洞,CLAUDE.md 已立规)。tie-break = `(值 降序, ts_code 升序)`,与
    `industry_strength._day_local_table` 的 `(median_ret 降序, industry 升序)` 同体例。
    """
    if df.is_empty():
        return df.with_columns(pl.lit(None, dtype=pl.Int64).alias(rank_col))
    return (
        df.sort([value_col, "ts_code"], descending=[True, False], nulls_last=True)
        .with_columns(
            pl.col(value_col).rank(method="ordinal", descending=True)
            .over("industry").cast(pl.Int64).alias(rank_col)
        )
    )


def _consec_limit_up(
    trade_date: date, codes: Sequence[str], parquet_dir: Optional[Path],
) -> Tuple[Dict[str, int], Dict[str, str]]:
    """连板高度(截至判定日的连续涨停天数)+ 缺数原因。

    **稀疏表语义(模块头已详述,这里是它的落地)**:某票某日不在 `limit_derived`
    里 = 「那天没涨停」的**确定事实**,连板计数在那里正常中断(值 = 0 或已累计的
    天数),⛔ 不标缺数。唯一的「不知道」= 该日**分区文件不存在**:
      · 判定日分区缺 → 整个读数不可得(`limit_data_unavailable`);
      · 回看途中某日分区缺、而计数**还在继续** → 真值 ≥ 已数到的天数,是个下界不是
        事实 → 照样标 `limit_data_unavailable`(⛔ 不把下界当结论报出去)。

    **右截尾**:回看至多 `CONSEC_LIMIT_LOOKBACK_DAYS` 天,饱和值 = 「≥ 该值」
    (同 `scan/landing.py::PLATFORM_DAYS_CAP` 的既有体例)。
    """
    values: Dict[str, int] = {}
    missing: Dict[str, str] = {}
    wanted = [c for c in codes if c]
    if not wanted:
        return values, missing

    days = list(reversed(
        _recent_trading_days_before(trade_date, CONSEC_LIMIT_LOOKBACK_DAYS - 1) + [trade_date]
    ))  # 由近及远
    present = {d: day_file_path("limit_derived", d, parquet_dir).exists() for d in days}
    if not present.get(trade_date, False):
        return values, {c: REASON_LIMIT_DATA_UNAVAILABLE for c in wanted}

    lf = _scan_table("limit_derived", parquet_dir,
                     years=_years_in_range(days[-1], trade_date))
    up: Dict[date, set] = {}
    if lf is not None:
        df = (
            lf.filter((pl.col("trade_date") >= days[-1]) & (pl.col("trade_date") <= trade_date))
            .select(["ts_code", "trade_date", "is_limit_up"]).collect()
        )
        for ts, td, flag in zip(df["ts_code"].to_list(), df["trade_date"].to_list(),
                                df["is_limit_up"].to_list()):
            if flag:
                up.setdefault(td, set()).add(ts)

    for code in wanted:
        n = 0
        truncated_unknown = False
        for d in days:
            if not present.get(d, False):
                # 走到这里必然 n ≥ 1(判定日分区上面已验在,计数一断就 break 了)
                # —— 计数还没断就撞上缺分区,真值只是个下界,⛔ 不当事实报。
                truncated_unknown = True
                break
            if code in up.get(d, set()):
                n += 1
            else:
                break
        if truncated_unknown:
            missing[code] = REASON_LIMIT_DATA_UNAVAILABLE
        else:
            values[code] = n
    return values, missing


def compute_core_metrics(
    trade_date: date,
    codes: Sequence[str],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> CoreMetricsResult:
    """候选成员的**行业域**核心读数(零判定、零阈值)。

    只算 `codes` 这几只(裁定 #12 的成本纪律:⛔ 不建第三张全市场预计算表),但排名
    的分母是**它们各自所在行业的全体成员** —— 一天一个切片 × 涉及的几个行业,取数走
    `_scan_table(years=…)` **年分区裁剪**(P1-26 既有修法,⛔ 不全 glob)。

    返回 `CoreMetricsResult`;`metrics[code]` 只含**真的算出来的**键,算不出的进
    `missing[code]`(键 → 原因码),**⛔ 不填 0**。`industry_peers` 是比较域 ③ 的
    成员名单(V2.4.0 P1.3),与 `industry_member_count` 同一个横截面。
    """
    wanted = sorted({c for c in codes if c})
    out_metrics: Dict[str, Dict[str, Any]] = {c: {} for c in wanted}
    out_missing: Dict[str, Dict[str, str]] = {c: {} for c in wanted}
    out_peers: Dict[str, Tuple[str, ...]] = {}
    if not wanted:
        return CoreMetricsResult()

    def _miss_all(reason: str, keys: Sequence[str] = INDUSTRY_METRIC_KEYS) -> None:
        for c in wanted:
            for k in keys:
                out_missing[c].setdefault(k, reason)

    def _finish(available: bool) -> CoreMetricsResult:
        """连板高度**与行业域读数各自独立**(它只需要这一只票自己的 `limit_derived`
        行,行业查不到、当日停牌、行情整段缺,都不该连累它)—— 故所有返回路径都从
        这里出去,⛔ 别在早退分支里把它漏掉。"""
        consec, consec_missing = _consec_limit_up(trade_date, wanted, parquet_dir)
        for c in wanted:
            if c in consec:
                out_metrics[c]["consec_limit_up_days"] = consec[c]
            elif c in consec_missing:
                out_missing[c]["consec_limit_up_days"] = consec_missing[c]
        return CoreMetricsResult(metrics=out_metrics, missing=out_missing,
                                 available=available, industry_peers=dict(out_peers))

    try:
        industry_of = load_industry_map(db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[core_metrics] 行业映射加载失败,核心关行业域读数本次全缺", exc_info=True)
        industry_of = {}

    industries = {industry_of[c] for c in wanted if c in industry_of}
    for c in wanted:
        if c not in industry_of:
            for k in INDUSTRY_METRIC_KEYS:
                out_missing[c][k] = REASON_INDUSTRY_UNMAPPED
    if not industries:
        return _finish(False)

    peers = sorted(c for c, ind in industry_of.items() if ind in industries)
    start = _recent_trading_days_before(trade_date, RS_WINDOW_DAYS - 1)[0]

    try:
        lf = _scan_table("daily", parquet_dir, years=_years_in_range(start, trade_date))
        window = (
            lf.filter(
                (pl.col("trade_date") >= start) & (pl.col("trade_date") <= trade_date)
                & pl.col("ts_code").is_in(peers)
            ).select(["ts_code", "trade_date", "close", "pre_close", "amount"]).collect()
            if lf is not None else pl.DataFrame()
        )
    except Exception:  # noqa: BLE001
        logger.warning("[core_metrics] 行情窗口取数失败,核心关行业域读数本次全缺", exc_info=True)
        window = pl.DataFrame()

    if window.is_empty():
        _miss_all(REASON_DAILY_DATA_UNAVAILABLE)
        return _finish(False)

    # `ret_1d = close/pre_close - 1` 的**唯一实现**(`industry_strength._ret1d_from_daily`)
    # —— 复权不变(同一行 close/pre_close 被同一个标量缩放,比值精确抵消),故 20 日
    # 收益取 `∏(1+ret_1d) − 1` 而不是两端收盘价相除:后者要 join `adj_factor` 才能跨
    # 除权日算对,前者天然免疫(TuShare `pre_close` 已是除权调整后的前收)。
    panel = _ret1d_from_daily(window)
    if panel.is_empty():
        _miss_all(REASON_DAILY_DATA_UNAVAILABLE)
        return _finish(False)
    ind_df = pl.DataFrame(
        {"ts_code": peers, "industry": [industry_of[c] for c in peers]},
        schema={"ts_code": pl.String, "industry": pl.String},
    )
    panel = panel.join(ind_df, on="ts_code", how="inner")

    day = panel.filter(pl.col("trade_date") == trade_date)
    if day.is_empty():
        _miss_all(REASON_DAILY_DATA_UNAVAILABLE)
        return _finish(False)

    # —— 分母①:当日有行情行的行业成员数(= 当日名次 / 成交额占比的分母)——
    member_count = {
        r["industry"]: int(r["n"])
        for r in day.group_by("industry").agg(pl.len().alias("n")).iter_rows(named=True)
    }
    # —— V2.4.0 P1.3:比较域 ③ 的成员名单(**与上面那个分母同一个横截面**)——
    peers_acc: Dict[str, List[str]] = {}
    for _ts, _ind in zip(day["ts_code"].to_list(), day["industry"].to_list()):
        peers_acc.setdefault(_ind, []).append(_ts)
    out_peers.update({k: tuple(sorted(v)) for k, v in peers_acc.items()})
    # —— 当日涨跌幅行业内名次 ——
    day_ranked = _rank_within_industry(day, "ret_1d", "industry_ret_rank_1d")
    ret_rank_1d = dict(zip(day_ranked["ts_code"].to_list(),
                           day_ranked["industry_ret_rank_1d"].to_list()))
    # —— 当日成交额占本行业总额比 ——
    amount_total = {
        r["industry"]: r["amt"]
        for r in day.group_by("industry").agg(pl.col("amount").sum().alias("amt")).iter_rows(named=True)
    }
    amount_of = dict(zip(day["ts_code"].to_list(), day["amount"].to_list()))

    # —— 20 日收益(凑满 20 根 bar 才算;缺一根 = insufficient_history,⛔ 不折算)——
    ret20 = (
        panel.group_by(["ts_code", "industry"])
        .agg(pl.len().alias("bars"), (pl.col("ret_1d") + 1.0).product().alias("growth"))
        .filter(pl.col("bars") == RS_WINDOW_DAYS)
        .with_columns((pl.col("growth") - 1.0).alias("ret_20d"))
        .select(["ts_code", "industry", "ret_20d"])
    )
    # 20 日名次的分母**与当日名次的分母不同**(停牌 / 次新凑不满窗口的票排不进来)
    # —— 两个分母都必须给出来,否则「第 3 名」到底是 3/几 仍然读不出(裁定 #12 的
    # 🔴 分母条款要的就是这个)。
    ranked_count = {
        r["industry"]: int(r["n"])
        for r in ret20.group_by("industry").agg(pl.len().alias("n")).iter_rows(named=True)
    }
    rs_ranked = _rank_within_industry(ret20, "ret_20d", "industry_rs_rank_20d")
    rs_rank_20d = dict(zip(rs_ranked["ts_code"].to_list(),
                           rs_ranked["industry_rs_rank_20d"].to_list()))

    day_codes = set(day["ts_code"].to_list())
    for code in wanted:
        ind = industry_of.get(code)
        if ind is None:
            continue
        m, miss = out_metrics[code], out_missing[code]

        n_day = member_count.get(ind)
        if n_day is None:
            miss["industry_member_count"] = REASON_DAILY_DATA_UNAVAILABLE
        else:
            m["industry_member_count"] = n_day
        n_ranked = ranked_count.get(ind, 0)
        m["industry_rs_ranked_count_20d"] = n_ranked

        if code not in day_codes:
            # 当日无行情行(停牌 / 未上市):**群体分母照给**(那是关于"这一群"的事实),
            # 该票自己的名次/占比如实标缺,⛔ 不猜。
            for k in ("industry_ret_rank_1d", "industry_amount_share"):
                miss[k] = REASON_NO_DAILY_ROW
        else:
            rk = ret_rank_1d.get(code)
            if rk is None:
                miss["industry_ret_rank_1d"] = REASON_NO_DAILY_ROW
            else:
                m["industry_ret_rank_1d"] = int(rk)
            amt, total = amount_of.get(code), amount_total.get(ind)
            if amt is None or total is None or float(total) <= 0.0:
                miss["industry_amount_share"] = REASON_AMOUNT_UNAVAILABLE
            else:
                m["industry_amount_share"] = round(float(amt) / float(total), 6)

        rs = rs_rank_20d.get(code)
        if rs is None:
            miss["industry_rs_rank_20d"] = REASON_INSUFFICIENT_HISTORY
            miss["industry_rs_pct_20d"] = REASON_INSUFFICIENT_HISTORY
        else:
            m["industry_rs_rank_20d"] = int(rs)
            if n_ranked < _MIN_RANKABLE:
                miss["industry_rs_pct_20d"] = REASON_INDUSTRY_TOO_SMALL
            else:
                # 分位 = 本行业内收益不高于它的成员占比(1 = 最强、0 = 最弱)。
                # **这是这个读数的定义,不是及格线** —— 名次与两个分母都已单独给出,
                # 分位只是免去调用方心算。
                m["industry_rs_pct_20d"] = round(
                    (n_ranked - int(rs)) / (n_ranked - 1), 6)

    return _finish(True)


def merge_cluster_supplement(
    metrics: Dict[str, Any],
    missing: Dict[str, str],
    *,
    rs_rank: Optional[int],
    amount_share: Optional[float],
    size: Optional[int],
    cluster_available: bool,
    in_cluster: bool,
) -> None:
    """把**簇内补充读数**并进一份行业域读数(就地改;唯一填充点)。

    裁定 12-a:簇内 `rs_rank` **降级为补充读数** —— 有簇才给、**缺席不挡任何档**。
    两种「没有」语义相反、⛔ 不许合并(稀疏表那一课的同款):
      · `in_cluster=False` → `not_in_cluster` = **确定事实**(这票今天不在任何涨停簇);
      · `cluster_available=False` → `cluster_data_unavailable` = **不知道**(表没取到)。
    """
    if not cluster_available:
        for k in CLUSTER_METRIC_KEYS:
            missing.setdefault(k, REASON_CLUSTER_DATA_UNAVAILABLE)
        return
    if not in_cluster:
        for k in CLUSTER_METRIC_KEYS:
            missing.setdefault(k, REASON_NOT_IN_CLUSTER)
        return
    for key, value in (("cluster_rs_rank", rs_rank),
                       ("cluster_amount_share", amount_share),
                       ("cluster_size", size)):
        if value is None:
            missing.setdefault(key, REASON_CLUSTER_DATA_UNAVAILABLE)
        else:
            metrics[key] = value


__all__ = [
    "CORE_METRIC_GROUPS", "CORE_METRIC_KEYS", "CORE_METRIC_LEGEND",
    "INDUSTRY_METRIC_KEYS", "CLUSTER_METRIC_KEYS", "STOCK_METRIC_KEYS",
    "COMPARISON_DOMAIN_DRIVER", "COMPARISON_DOMAIN_THEME", "COMPARISON_DOMAIN_INDUSTRY",
    "COMPARISON_DOMAINS", "DOMAIN_AUDIT_KEYS",
    "DOMAIN_FALLBACK_NONE", "DOMAIN_FALLBACK_THEME_NOT_IMPLEMENTED",
    "DOMAIN_FALLBACK_NO_COMPARABLE_DOMAIN", "DOMAIN_LABELS",
    "resolve_comparison_domain", "format_comparison_domain",
    "REASON_INDUSTRY_UNMAPPED", "REASON_NO_DAILY_ROW", "REASON_INSUFFICIENT_HISTORY",
    "REASON_INDUSTRY_TOO_SMALL", "REASON_AMOUNT_UNAVAILABLE",
    "REASON_LIMIT_DATA_UNAVAILABLE", "REASON_DAILY_DATA_UNAVAILABLE",
    "REASON_NOT_IN_CLUSTER", "REASON_CLUSTER_DATA_UNAVAILABLE",
    "RS_WINDOW_DAYS", "CONSEC_LIMIT_LOOKBACK_DAYS",
    "CoreMetricsResult", "compute_core_metrics", "merge_cluster_supplement",
]
