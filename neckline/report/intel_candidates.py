"""候选池情报筛选管线(plan §五 v1.3-③-C3,需求 5,K1 选股逻辑退役)。

**产品语义变更(用户 2026-07-25 拍板,§2.3)**:候选列表不再是「系统认为会涨的票」,
改为「**过完安检、值得用户花注意力的票**」,终选权在用户。生成源从 **K1 entry mask
退役**——本模块**不调用 `strategy.momentum.build_entry_mask`**(单测 `test_intel_candidates`
直接断言),改走四步情报筛选管线:

    ① 板块层  = 拥挤度 top:**五板块常驻**(`settings_store.get_intel_watch_boards`,按
                `ths_index.name` **精确匹配**取 ts_code——禁关键词模糊:"芯片"会误命中
                汽车芯片/存储芯片、"机器人"会误命中人形机器人,实测见
                `settings_store.DEFAULT_INTEL_WATCH_BOARDS`)+ **当日暴起板块**(`compute_
                sector_strength` 拥挤度 top-N,**先过 `board_pool` 卫生线**剔资格/宽基标签)。
    ② 个股层  = 上述板块成员 ∩ **全板块 MAIN/GEM/STAR**(排 BSE,`base_universe_expr` 已含)
                ∩ 只过卫生线(`research.panel.base_universe_expr` 子集:非 ST + amount_ma20
                ≥2000万 + close≥2 + ma20 非空)∩ **非次新**(`~signals.forbid_new_stock(120)`,
                = days_since_listing≥120,与 K4 A4/base 口径同源)∩ **趋势向上**(`close>ma20`
                **粗代理**,标注)。**不套 K1 主板 only、不套 pullback/breakout 回调买点**
                (与 K1 entry mask 解耦,§3.8-(b))。
    ③ K4 安检 = 读 DB `K4.k4_advisory` 分区(`holding_k4_check.load_k4_sections`):
                `hard_cut` 命中 → **拦截出池**;`avoid_flag` 命中 → **打标保留**(机器不禁、
                情报展示给人判)。**复用 ②-A 的 polars 镜像评估器(`holding_k4_check` 同一份,
                阈值单一源,不写两遍)**——只是把持仓 I/O(逐票循环)换成全板块 bulk 面板
                I/O(见「性能坑」)。合成派发码 `A3b_belowyear_bigvol`(不在 DB,证据源=雷区
                地图 3-⑤)按 `_DEFAULT_SECTION` 归 avoid_flag(打标不拦,机器不禁;是否升级
                为 hard_cut 留用户拍板)。**题材类(A2/B3)的持续天数输入(v1.4-② 起)** = 该
                code 代表的**行业**(`stock_basic.industry`,一票一行业)当日
                `industry_strength.stock_persist_days`——不再用它所属概念板块的 board_age
                最大值(v1.3-② 遗留代理,已作废,见 `report/industry_strength.py` 与
                `holding_k4_check.py` 模块头「★」)。
    ④ 情报排序(**v1.4-③ 三级键,需求 8;取代 v1.3 起「板块资金流优先」旧公式**)= **依次**
                ① 行业强度排名 `industry_rank`(② 的 K2 拥挤探测器,升序,1=最强;**无
                industry / 成员<5 未参与排名 → 排最后〔+inf〕,不静默当 0**——0 会把无
                行业票错误顶到榜首)② 行业强度持续天数 `industry_persist_days`(② 唯一源,
                升序,第 1 天最新鲜;≥4 天已在 ③ K4 hard_cut〔A2〕剔,故实际取值
                ∈{0,1,2,3};H6 单调证据,同 ③ 用的同一个 `industry_strength` 值,单一源
                不两算)③ K4 黄牌数 `yellow_card_count`(仅数 DB 显式登记为 `avoid_flag`
                分区的命中,**不数 hard_cut,也不数不在 DB 的合成码**如
                `A3b_belowyear_bigvol`;升序,无牌靠前——**只是风险优先排序,无牌≠会涨**,
                盲选第一期已验)。`base_score` DESC / `code` ASC 只作**确定性兜底**(保证
                同名次可复现),不是第四/五维排序意图。**板块资金流强度(C2)自本块起从
                排序键移除,退为 `intelRank.sectorFlow` 并列展示**;**RS 线斜率/行业分歧度
                /龙虎榜/消息面/温和带等未经审计方向的量一律不得进排序键**(只进 ④ 信息卡
                展示,§3.8/需求 8 语义红线,白名单 `_SORT_KEY_INPUTS` 单测锁死)→ 出
                **20 只**交用户终选。

**§3.8 铁律「同码」重述的落地核对**:候选生成(本模块)与回测信号**解耦**——不声称
回测过的 alpha、输出「值得关注」非「会涨」。**纪律红绿灯(问询台 `api/inquiry.py` /
自选体检 `report/watchlist_check.py`)仍与报告同码**(`base_universe_expr` + config 禁买
过滤),本模块不碰它们。**`report/candidates.py` 的评分表达式 `_base_score_expr`、四件套
文案、`pattern_tags` 均复用**(展示排序分/四件套/形态标签同一份,不重写);候选 `rank` 由
情报排序决定、`score` = `_base_score_expr` 展示分(技术贴前高度,**非**排序键,见 §④)。

**性能坑(plan §五 v1.3-③-C3「③C1/C2 施工者点名交接」)**:`holding_k4_check` 的 K4 镜像
原按「≤3 持仓、逐票 `get_stock_history` 循环」写(内存友好但全板块数千只会很慢)。本模块
选 **(a):复用其判据表达式 + 换全市场 bulk 面板 I/O**——给 `holding_k4_check._build_holding_
feature_panel` 注入 `_bulk_load_codes_table`(一次 `scan_parquet` 谓词下推,按 code 集合过滤,
免逐票 N 次开文件),**特征/判据装配与阈值与 ② 完全同一份**(单一源)。两条 I/O 路径的
一致性由 `tests/test_intel_candidates.py::test_bulk_and_percode_loaders_agree` 直接对拍。

**生成域刻意含高弹**(GEM/STAR;用户知情拍板,与 K1「剔高弹」哲学相反,止损频率代价已在
策略线审计定价)——**不偷偷加回 K1 的高弹剔除**,只 `intelRank.highElasticity` 标注给人判。
"""

from __future__ import annotations

import glob
import logging
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import polars as pl

from neckline.data.board import classify_by_code
from neckline.data.market_data import load_stock_basic, table_dir
from neckline.report.board_pool import apply_hygiene, count_members, invert_member_map
from neckline.report.candidates import (
    Candidate,
    _base_score_expr,
    _load_stock_names,
    entry_plan_text,
    entry_spec,
    invalidation_spec,
    invalidation_text,
    pattern_tags,
    stop_loss_text,
    target_text,
)
from neckline.report.holding_k4_check import (
    _build_holding_feature_panel,
    _evaluate_hits,
    _load_k4_evidence,
    load_k4_sections,
)
from neckline.report.industry_strength import (
    IndustryStrength,
    industry_strength_lookup,
    stock_industry_rank,
    stock_persist_days,
)
from neckline.report.industry_strength_store import load_industry_strength
from neckline.report.sectors import (
    SectorScore,
    compute_sector_strength,
    load_index_names,
    load_member_map,
    sector_hot_lookup,
)
from neckline.report.sector_moneyflow import compute_sector_moneyflow, empty_sector_moneyflow_report
from neckline.research.panel import base_universe_expr
from neckline.settings_store import get_intel_watch_boards
from neckline.strategy import signals as S
from neckline.strategy.features import build_research_panel
from neckline.strategy.momentum import MomentumConfig

logger = logging.getLogger(__name__)

# 全板块 MAIN/GEM/STAR(排 BSE,`board.py::Board` 枚举码;base_universe_expr 已含 !=BSE,
# 此处显式再挡一道,贴 plan「全板块 MAIN/GEM/STAR」措辞)。
_ALLOWED_BOARDS = ("MAIN", "GEM", "STAR")
NON_NEW_MIN_DAYS = 120        # 非次新门槛(days_since_listing≥120,复用 signals.forbid_new_stock,同 K4 A4/base 口径)
BREAKOUT_TOP_N = 10           # 当日暴起板块 top-N(过卫生线后按拥挤度取)
TOP_N_CANDIDATES = 20         # 出榜候选数(交用户终选)
QUOTA_PER_PERMANENT_BOARD = 2  # 五常驻板块每个保底名额(用户 2026-07-26 拍板:长期盯的五方向每天都有情报到手,
                              # 不让当日最强题材簇占满整榜)。取该板块内情报排序最高的 2 只,**只从过完
                              # ②卫生线 + ③K4 hard_cut 的池子里选**——hard_cut 命中绝不因保底被捞回,合格票不足
                              # 2 只时有几只放几只、缺额退回公共池竞争(不许降卫生线/放宽 hard_cut)。

# 候选入选来源(带入 intelRank.source,供 ⑥ 客户端说清「为什么在榜」)。
SOURCE_QUOTA = "quota"            # 常驻板块保底入选
SOURCE_COMPETITION = "competition"  # 情报排序竞争入选
SOURCE_FORCED = "forced"          # 问询台海选池强制纳入(§2.5,豁免卫生线/hard_cut)
_ALL_BOARDS_TOP_N = 1000      # 远超真实概念板块总数(394,2026-07-24 快照),拿全量排序结果(同 intel.py)
_MONEYFLOW_ALL_TOP_N = 10 ** 9  # 拿 C2 全部板块资金流(非只 top-15),供逐候选查其板块净流入
# DB `k4_advisory` 无归属的合成码(A3b_belowyear_bigvol,证据源=雷区地图 3-⑤)默认归属:
# avoid_flag = 打标保留(机器不禁、给人判)。**不默认 hard_cut**——严守「hard_cut 单一源 = DB」,
# 不在 DB 之外自造硬剔判据(是否把年线下派发放量大阳升级为 hard_cut,留用户拍板,见 report ⑦)。
_DEFAULT_SECTION = "avoid_flag"

# —— v1.4-③ 排序键白名单(需求 8 语义红线:排序键只用审计过方向的量;单测断言 `_sort_key`
#    只读这五个键,见 tests/test_intel_candidates.py)——————————————————————————————————
_SORT_KEY_INPUTS = frozenset({
    "industry_rank", "industry_persist_days", "yellow_card_count", "base_score", "code",
})


def _sort_key(e: Dict[str, Any]) -> tuple:
    """情报排序键(v1.4-③,需求 8 定死,升序优先在前)——**依次**:
    ① `industry_rank`(行业强度当日排名,1=最强;`None`=未参与排名〔无 industry/成员<5〕
    → `+inf` 排最后,**不静默当 0**,0 会把无行业票错误顶到榜首);
    ② `industry_persist_days`(行业强度持续天数,升序,第 1 天最新鲜,H6 单调证据;
    ≥4 天已被 ③ K4 hard_cut〔A2〕拦出池,故实际取值 ∈{0,1,2,3}——这条自洽性**依赖 ②
    A2/B3 已切到 `industry_strength` 同一个量**,见模块 docstring);
    ③ `yellow_card_count`(K4 avoid_flag 命中数,升序,无牌靠前——**只是风险优先排序,
    无牌≠会涨**,盲选第一期已验)。
    `base_score` DESC / `code` ASC 只作**确定性兜底**(保证同名次可复现),不构成
    第四/五维排序意图。

    **只读 `_SORT_KEY_INPUTS` 白名单五键**——`sector_flow`(板块资金流,退为并列展示)、
    RS 线斜率/行业分歧度/龙虎榜/消息面/温和带等**未经审计方向的量禁止进本函数**
    (§3.8/需求 8 语义红线;白名单单测用可追踪访问的 dict 断言本函数运行期实际只碰
    这五个键,见 `tests/test_intel_candidates.py`)。"""
    rank = e["industry_rank"] if e["industry_rank"] is not None else float("inf")
    return (rank, e["industry_persist_days"], e["yellow_card_count"], -e["base_score"], e["code"])


# —— 行业闸(用户 2026-07-26 拍板方案二:行业当闸 + 概念当题材;2026-07-27 审计发现 share
#    版判据用错统计量,改判据为 lift——本节是缺陷修复,不是新功能,机制/落点不变)—————————
# **问题**:保底/竞争把「名义上挂在该板块、但与主题无关」的票推上榜(实测:机器人概念栏
# 给出九州通/重药控股〔医药商业〕、稀土永磁栏给出中炬高新〔厨邦酱油·食品〕)——成分归属
# 没错(同花顺沾边挂靠,立中集团挂 30 个板块/九州通挂 25 个),错的是**板块内排序用的全是
# 与主题无关的指标**(资金流/趋势/题材天数),当日最强题材的票就浮到不相干板块的前排。
# **修法**:用 `stock_basic.industry`(**一票一行业,无沾边**)对每个板块自动算「主导行业集合」
# ——个股必须**行业 ∈ 该板块主导行业集合**才能作为该板块的**代表票**(保底与竞争的板块归属
# 都过这道闸)。**数据驱动、不手配白名单**——对当日暴起板块自动同样生效(这是选此方案的关键)。
# 闸只作用于**板块归属/代表性**,不改卫生线、不改 K4 hard_cut/avoid_flag、不改情报排序公式本身。
# **判据(2026-07-27 由 share 改 lift,审计发现原判据统计量用错)**:原「板内占比 ≥5%」把长尾
# 行业整片砍掉——`stock_basic.industry` 分类有 110 个、颗粒极细,一个题材板块天然散落几十个
# 行业,固定 5% 线误杀长尾里的真主题票(实测:储能栏挡下「新型电力」21 只〔板内2.3%/全市场
# 0.6%〕、芯片概念栏挡下「IT设备」31 只〔板内3.4%/全市场1.5%〕、稀土永磁栏挡下「铝」2 只
# 〔板内3.1%/全市场0.5%〕——三者板内占比虽 <5%,相对全市场都显著富集,不该被挡)。改判据为
# **lift(富集度)= 该行业板内占比 ÷ 该行业全市场占比**(全市场分母见 `_market_industry_shares`
# = `stock_basic` 里有 industry 的股票总数,现 5536 只),`lift ≥ INDUSTRY_GATE_MIN_LIFT` 才算
# 主导——沾边挂靠的噪音行业(机器人概念的医药商业、稀土永磁的食品、芯片概念的汽车配件)相对
# 全市场并不富集(lift<1),仍被正确挡下,真实数据复现见 `tests/test_intel_candidates.py` ⑩组。
INDUSTRY_GATE_MIN_LIFT = 2.0     # 主导行业阈值 = lift ≥ 此值(启发式,待实盘校准;2026-07-22
                                 # 真实数据验:储能/新型电力 lift4.1、芯片概念/IT设备 lift2.3、
                                 # 稀土永磁/铝 lift5.7 均由旧闸误杀纠正为过闸;机器人概念/半导体
                                 # lift1.1、机器人概念/医药商业 lift0.5、稀土永磁/食品 lift0.9、
                                 # 芯片概念/汽车配件 lift0.6 仍正确挡下,五个原噪音反例无一复活)。
_INDUSTRY_GATE_EPS = 1e-9        # lift 阈值比较容差(除法产生的浮点噪声,同 sentinel/holding.py
                                 # `_EPS` 先例——项目里裸 >=/<= 比较派生浮点的通用坑,照此办理)。


def _market_industry_shares(industry_of: Dict[str, str]) -> Dict[str, float]:
    """全市场行业占比(lift 分母)。分母 = `stock_basic` 里**有 industry** 的股票总数(现 5536
    只)——**无 industry 的票不计入**(它们在 `_dominant_industries`/正文闸判定里本就走「不
    通过闸」分支,不该反过来稀释市场基准)。空 `industry_of`(缺表/缺列)→ 空 dict,与
    `_load_industry_map` 的优雅降级一致(下游 lift 查无市场占比 → 保守不通过)。"""
    counts = Counter(ind for ind in industry_of.values() if ind)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {ind: c / total for ind, c in counts.items()}


def _dominant_industries(
    members: List[str],
    industry_of: Dict[str, str],
    market_shares: Dict[str, float],
    min_lift: float = INDUSTRY_GATE_MIN_LIFT,
) -> Set[str]:
    """板块主导行业集合 = **lift**(板块内占比 ÷ 全市场占比,见 `_market_industry_shares`)
    ≥ `min_lift` 的行业(2026-07-27 由「板内占比 ≥5%」改判据,见模块「行业闸」节:固定占比线
    对 110 个细颗粒行业分类系统性误杀长尾主题票,lift 用全市场基准分辨「该行业在此板块是否
    真的富集」而非只看板内绝对占比)。**板内分母 = 全体成员**(无 industry 的成员计入分母、
    稀释板内占比,denom 口径与旧 share 版一致、未变)。**全市场分母见 `market_shares` 参数**;
    全市场查无该行业占比 → lift 未定义,保守不通过(理论上不会发生:凡是出现在成员
    `industry_of` 里的行业,必然也在其构建来源——同一份 `stock_basic` 全表——的 `market_shares`
    里,除非 `market_shares` 是用不同数据源算的)。空成员/无任何行业过阈 → 空集(该板块无
    代表票,不放宽——保守,守用户拍板)。"""
    if not members:
        return set()
    counts = Counter(ind for m in members if (ind := industry_of.get(m)))
    denom = len(members)
    dominant: Set[str] = set()
    for ind, c in counts.items():
        mkt_share = market_shares.get(ind)
        if not mkt_share:
            continue   # 全市场无此行业占比(异常态)→ lift 未定义,保守不通过
        lift = (c / denom) / mkt_share
        if lift >= min_lift - _INDUSTRY_GATE_EPS:
            dominant.add(ind)
    return dominant


def _bulk_load_codes_table(
    codes: List[str], start: date, end: date, table: str, parquet_dir: Optional[Path]
) -> pl.DataFrame:
    """全市场 bulk 区间加载器(注入 `holding_k4_check._build_holding_feature_panel`,替换
    ② 的逐票 `get_stock_history` 循环——数千只 universe 逐票会很慢,见模块「性能坑」)。
    **一次 `scan_parquet` + 谓词下推**:同时按 [start,end] 与 `ts_code ∈ codes` 过滤,
    parquet predicate pushdown 只物化 universe 相关行(免逐票 N 次开文件 + 免全市场物化)。
    表目录缺失/无文件 → 空 DataFrame(同 `market_data._scan_table` 优雅降级)。"""
    if not codes:
        return pl.DataFrame()
    d = table_dir(table, parquet_dir)
    if not d.exists():
        return pl.DataFrame()
    pattern = str(d / "year=*" / "*.parquet")
    if not glob.glob(pattern):
        return pl.DataFrame()
    code_set = list(dict.fromkeys(codes))
    return (
        pl.scan_parquet(pattern)
        .filter(
            (pl.col("trade_date") >= start)
            & (pl.col("trade_date") <= end)
            & pl.col("ts_code").is_in(code_set)
        )
        .collect()
    )


def _load_industry_map(db_path: Optional[Path]) -> Dict[str, str]:
    """`ts_code -> industry`(`stock_basic.industry`,一票一行业、无沾边;行业闸用)。缺表/缺列 →
    空 dict(优雅降级:此时所有票 industry=空 → 全不通过闸 → 候选空,保守,不放宽)。"""
    sb = load_stock_basic(db_path)
    if sb.is_empty() or "industry" not in sb.columns:
        return {}
    return dict(zip(sb["ts_code"].to_list(), sb["industry"].to_list()))


def _resolve_watch_board_codes(
    index_names: Dict[str, str], db_path: Optional[Path]
) -> Tuple[List[str], List[str]]:
    """五板块常驻名单(板块中文名)→ index_code **有序列表**(按配置名单顺序,dedup),**按
    `ths_index.name` 精确匹配**(禁关键词模糊,见模块 docstring)。返回 (解析到的 index_code
    有序列表, 未解析到的名字列表)。**顺序 load-bearing**:保底名额分配按此顺序认领(一票同属
    多个常驻时归**配置顺序最先轮到且仍有空额**的板块,见 `build_intel_candidates` 保底 pass)。
    精确名极少数情况对应多个 index_code → 全取(仍精确、不模糊;各自算一个常驻板块各 2 只保底)。"""
    names = get_intel_watch_boards(db_path)
    name_to_codes: Dict[str, List[str]] = {}
    for code, nm in index_names.items():
        name_to_codes.setdefault(nm, []).append(code)
    codes: List[str] = []
    seen: Set[str] = set()
    unresolved: List[str] = []
    for nm in names:
        hit = name_to_codes.get(nm)
        if hit:
            for c in hit:
                if c not in seen:
                    codes.append(c)
                    seen.add(c)
        else:
            unresolved.append(nm)
    return codes, unresolved


def build_intel_candidates(
    trade_date: date,
    rule: Dict[str, Any],
    *,
    member_map: Optional[Dict[str, List[str]]] = None,
    index_names: Optional[Dict[str, str]] = None,
    sector_scores: Optional[List[SectorScore]] = None,
    industry_scores: Optional[List[IndustryStrength]] = None,
    top_n: int = TOP_N_CANDIDATES,
    breakout_top_n: int = BREAKOUT_TOP_N,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    forced_codes: Optional[List[str]] = None,
) -> List[Candidate]:
    """候选情报筛选管线入口(角色对应旧 `candidates.build_candidates`,pipeline 侧替换点)。
    `rule` = 大脑现役 `brain.get_active().rule`(cfg 读 stop_pct/max_hold_days 等,单一源;
    **仅用于四件套文案/展示分,不用于 entry mask**)。`member_map`/`index_names` 由 pipeline
    传入(报告已加载,不重复读 parquet);`sector_scores`(大列表,拥挤度 + board_age,仍只用于
    板块展示/常驻暴起判定)缺省则内部 `compute_sector_strength(top_n=1000)` 自算。
    `industry_scores`(v1.4-② 起题材持续天数唯一源输入)缺省则内部
    **读 `industry_strength_daily` 预计算表**(v1.4-⑩ / §七 P0-23:此前是现算 = 全历史扫描,
    生产跑不完)——与 `sector_scores` 同一姿势,pipeline 已读好一份传入,免报告内部候选/持仓/
    问询三处各自再查一遍;表缺当日行 → 空列表,**降级方向 = 不拦**(A2 hard_cut 不触发、
    排序键① 全 None→+inf,序退化成 yellow_card→base_score→code 仍确定性可复现),由报告级
    `dataFreshness` 显式披露。`forced_codes` = 问询台海选池
    「初审通过」的票(§2.5)强制并入(用户点名,豁免 ② 卫生线与 ③ hard_cut,仅 K4 打标展示)。"""
    cfg = MomentumConfig(**rule["config"])
    member_map = member_map if member_map is not None else load_member_map(parquet_dir=parquet_dir)
    index_names = index_names if index_names is not None else load_index_names(parquet_dir=parquet_dir)
    forced_set = {c for c in (forced_codes or []) if c}

    # —— ① 板块层:五常驻 + 当日暴起 top-N(先过卫生线)————————————————————————
    all_scores = (
        sector_scores
        if sector_scores is not None
        else compute_sector_strength(trade_date, parquet_dir=parquet_dir, top_n=_ALL_BOARDS_TOP_N)
    )
    hygiene = apply_hygiene(index_names, count_members(member_map))
    kept_boards = hygiene.kept
    # 当日暴起 = 过卫生线的板块按拥挤度(compute_sector_strength 已按 board_ret_20d 降序)top-N。
    breakout_codes = [s.index_code for s in all_scores if s.index_code in kept_boards][:breakout_top_n]
    permanent_codes, unresolved = _resolve_watch_board_codes(index_names, db_path)
    if unresolved:
        logger.warning("候选情报管线:五常驻板块名未在 ths_index 精确匹配到:%s(跳过,不模糊回退)", unresolved)
    step1_boards: Set[str] = set(permanent_codes) | set(breakout_codes)
    # 板块展示(候选卡「所属热门板块(板块年龄N天…)」文案)只看候选**所属的 step① 板块**的
    # board_age,故 hot 限定 step① 内。**v1.4-② 起题材持续天数判据/排序输入不再读这份**
    # (见下方 `industry_hot`),`step1_hot` 现在只服务展示文案。
    all_hot = sector_hot_lookup(all_scores)
    step1_hot = {b: all_hot[b] for b in step1_boards if b in all_hot}

    # —— 行业闸(2026-07-26 方案二):对每个 step① 板块自动算主导行业集合,个股必须行业 ∈ 该
    #    集合才能作为该板块的代表票(gated 归属)。数据驱动、不手配白名单,当日暴起板块自动同样
    #    生效。无 industry 的票视为不通过闸(保守,无法判主题相关性),诚实落审计日志、不静默丢。—
    industry_of = _load_industry_map(db_path)
    # v1.4-②:题材持续天数唯一源(A2/B3 判据 + ④ 情报排序输入共用),复用上一行已加载的
    # `industry_of`(行业闸同一份 stock_basic.industry 映射,免二次读表)。
    industry_hot = industry_strength_lookup(
        industry_scores
        if industry_scores is not None
        else load_industry_strength(trade_date, db_path=db_path)
    )
    market_shares = _market_industry_shares(industry_of)
    inv = invert_member_map(member_map)
    board_members_of: Dict[str, List[str]] = {b: inv.get(b, []) for b in step1_boards}
    dominant_of: Dict[str, Set[str]] = {
        b: _dominant_industries(board_members_of[b], industry_of, market_shares) for b in step1_boards
    }
    gated_boards_of: Dict[str, List[str]] = {}   # code -> [该 code 过行业闸的 step① 板块](= 其代表的板块)
    all_nominal_members: Set[str] = set()        # step① 板块全体名义成员(未过行业闸,供板块状态诊断分母)
    _blocked_no_industry: Set[str] = set()       # 因无 industry 被挡(审计用,去重)
    for b in step1_boards:
        dom = dominant_of[b]
        for m in board_members_of[b]:
            all_nominal_members.add(m)
            ind = industry_of.get(m)
            if ind and ind in dom:
                gated_boards_of.setdefault(m, []).append(b)
            elif not ind:
                _blocked_no_industry.add(m)
    if _blocked_no_industry:
        # 审计(同 board_pool 剔除审计落日志姿势;不静默吞):无 industry 的成员一律不通过闸。
        logger.info("候选情报管线·行业闸:%d 只无 industry 的板块成员按不通过闸处理(保守,无法判主题相关性)",
                    len(_blocked_no_industry))
    today = build_research_panel(trade_date, trade_date, with_forward=False, parquet_dir=parquet_dir)
    # 板块归属(MAIN/GEM/STAR/BSE)取自 today 面板(merge_meta 已算,单一源 board.classify);
    # forced 票若不在 today(极端)退 classify_by_code 前缀兜底。K4 面板(holding_k4_check
    # 的 builder)不含 board 列,故 board 只从这里取,不从 K4 面板 row 取。
    board_by_code: Dict[str, str] = (
        dict(zip(today["ts_code"].to_list(), today["board"].to_list())) if not today.is_empty() else {}
    )
    # ② 卫生线在**全体(未过行业闸)step① 成员**上跑一遍 → raw ② survivors(供板块状态诊断
    # 区分「被行业闸挡」vs「被 K4 拦」,让 0 只/不足 2 只的板块能说清「为什么」——守项目一贯
    # 「『没有』和『没看』必须能分开」原则);universe 再叠加行业闸(只留过闸的代表票)。
    raw_survivor_codes: Set[str] = set()
    if not today.is_empty() and all_nominal_members:
        step2 = today.filter(
            pl.col("ts_code").is_in(list(all_nominal_members))
            & pl.col("board").is_in(list(_ALLOWED_BOARDS))
            & base_universe_expr()
            & ~S.forbid_new_stock(NON_NEW_MIN_DAYS)
            & (pl.col("close") > pl.col("ma20"))   # 趋势向上(粗代理,§② 标注)
        )
        raw_survivor_codes = set(step2["ts_code"].to_list())
    # 行业闸:universe survivor = 过②卫生线 且 过 ≥1 板块行业闸(在 gated_boards_of 里)。
    survivor_codes: Set[str] = {c for c in raw_survivor_codes if c in gated_boards_of}

    universe_codes = survivor_codes | forced_set
    if not universe_codes:
        return []

    # —— ③ K4 安检:全板块 bulk 面板(复用 ② 判据镜像,阈值单一源)→ hard_cut 拦 / avoid_flag 标 ——
    k4_panel = _build_holding_feature_panel(
        list(universe_codes), trade_date, parquet_dir, load_fn=_bulk_load_codes_table
    )
    if not k4_panel.is_empty():
        k4_panel = k4_panel.with_columns(_base_score_expr(cfg).alias("_base_score"))
    rows_by_code: Dict[str, Dict[str, Any]] = (
        {r["ts_code"]: r for r in k4_panel.to_dicts()} if not k4_panel.is_empty() else {}
    )
    sections = load_k4_sections(db_path)
    evidence = _load_k4_evidence(db_path)

    # —— ④ 情报排序输入:板块资金流(C2 全板块)————————————————————————————————
    # **保险丝(v1.3.5,2026-07-27 生产真踩后补)**:资金流只是情报**排序的一维输入**,
    # 不是候选生成的必要条件——拿不到就少一维排序,候选照出,**绝不允许掀翻整份报告**。
    # 2026-07-27 的 16:35 报告就是死在这一行:`moneyflow_dc` 分区 schema 分裂(历史空
    # 分区落成 String vs 真数据 Float64)→ 全表 scan_parquet SchemaError → 整个
    # `build_report` 崩、当日无报告。`pipeline.py` 里 C2 **展示节**那次调用早就包了同款
    # 降级,唯独本处(核心步骤内部对可选情报输入的调用)裸奔,故补齐。
    # **留痕不静默**:降级走 `empty_sector_moneyflow_report`(available=False + 诚实原因)
    # + WARNING 日志;同一底层故障必然让 pipeline 的 C2 节一并降级,报告「情报 · 板块
    # 资金流」栏会渲染出 unavailable_reason,用户看得见「本次不可用」而非静默空白。
    try:
        mf = compute_sector_moneyflow(
            trade_date, member_map=member_map, index_names=index_names,
            parquet_dir=parquet_dir, top_n=_MONEYFLOW_ALL_TOP_N,
        )
    except Exception:  # noqa: BLE001 —— 排序输入异常不得连带整份报告失败
        logger.warning(
            "候选情报管线·板块资金流(④ 排序输入)计算异常,已降级为不可用"
            "(候选照出、情报排序少一维),不阻断报告", exc_info=True,
        )
        mf = empty_sector_moneyflow_report(
            trade_date, reason="板块资金流计算异常(详见服务端日志),情报排序已降级。"
        )
    flow_by_board = {i.index_code: i.net_inflow_wan for i in mf.top_inflow} if mf.available else {}

    permanent_set = set(permanent_codes)
    hard_cut_codes: Set[str] = set()   # 命中 K4 hard_cut 的 universe 票(板块状态诊断:被安检拦下数)
    kept: List[Dict[str, Any]] = []
    for code in universe_codes:
        row = rows_by_code.get(code)
        # 板块归属**全部走行业闸后的 gated_boards_of**(= 该 code 真正代表的 step① 板块):资金流/
        # 保底归属/热门板块展示都只看代表的板块,不看沾边挂靠的板块(方案二核心)。**题材持续
        # 天数(v1.4-② 起)不再走板块代表关系**——直接是该 code 自己的 `stock_basic.industry`
        # 当日强度持续天数(一票一行业,不需要"代表哪个板块"这层间接)。
        its_step1_boards = gated_boards_of.get(code, [])
        its_permanent_boards = [b for b in its_step1_boards if b in permanent_set]
        persist = stock_persist_days(code, industry_of, industry_hot)
        # 排序键①(v1.4-③):行业强度当日排名。**None=未参与排名(无 industry/成员<5)**——
        # 调用方(下方 `_sort_key`)须把它当"排最后"处理,不得静默当 0(0 会把无行业票错误
        # 顶到榜首,plan §五 v1.4-③-A 明写)。
        industry_rank = stock_industry_rank(code, industry_of, industry_hot)
        hits = _evaluate_hits(row, persist, evidence)
        hard = [h for h in hits if sections.get(h.code, _DEFAULT_SECTION) == "hard_cut"]
        if hard:
            hard_cut_codes.add(code)   # 记 hard_cut(含 forced 豁免的,用于板块状态诊断计数)
        is_forced = code in forced_set
        if hard and not is_forced:
            continue   # ③ hard_cut 命中 → 拦截出池(forced 问询票用户点名,豁免硬剔、仅打标)
        if row is None:
            continue   # 无当日 EOD 数据(停牌/未上市)——无法出四件套候选卡,跳过
        # 保留候选的 K4 标注码:普通候选 = avoid_flag 命中;forced 票即使命中 hard_cut 也全数标注(诚实透出危险)。
        k4_flags = [h.code for h in hits]
        # 排序键③(v1.4-③):K4 黄牌数 = `k4_flags` 里**严格**属 DB `avoid_flag` 分区的命中数。
        # ⚠ **不用 `_DEFAULT_SECTION` 兜底**(与上面 `hard` 判定的 `.get(h.code, _DEFAULT_SECTION)`
        # 刻意不同)——那个默认值是给"拦截判定"用的(缺 DB 行时保守不拦);这里是"排序权重"用的,
        # 不在 DB 里明确登记为 avoid_flag 的码(hard_cut 命中、或不在 DB 的合成码如
        # `A3b_belowyear_bigvol`)一律不计入黄牌数(plan §五 v1.4-③-A 明写"不数 hard_cut,
        # 也不数不在 DB 的合成码")。
        yellow_card_count = sum(1 for h in hits if sections.get(h.code) == "avoid_flag")
        flows = [flow_by_board[b] for b in its_step1_boards if b in flow_by_board]
        sector_flow = max(flows) if flows else None
        kept.append({
            "code": code, "row": row, "k4_flags": k4_flags,
            "board": board_by_code.get(code) or classify_by_code(code),
            "industry": industry_of.get(code) or "",   # 出参带行业,让客户端说清「凭什么在这个板块栏」
            # sector_flow:v1.4-③ 起**并列展示,不进排序键**(需求 8,见下方 `_sort_key` 白名单)。
            "sector_flow": sector_flow,
            "industry_persist_days": persist,   # 排序键②(H6 单调证据,升序;≥4 已被③A2 hard_cut 剔)
            "industry_rank": industry_rank,     # 排序键①(K2 拥挤探测器,升序;None=+inf 排最后)
            "yellow_card_count": yellow_card_count,   # 排序键③(升序,无牌靠前;仅数 DB avoid_flag)
            "base_score": float(row.get("_base_score") or 0.0),
            "is_forced": is_forced, "its_step1_boards": its_step1_boards,
            "its_permanent_boards": its_permanent_boards,
            # 保底资格 = 过完 ②卫生线(survivor)且未命中 ③K4 hard_cut。forced 豁免票即使
            # 在 kept 里,若未过 ②/命中 hard 则**不**具保底/竞争资格(只经 forced 保证入榜)。
            "quota_eligible": (code in survivor_codes) and (not hard),
        })

    if not kept:
        return []

    kept.sort(key=_sort_key)   # 全局情报排序(最优在前),保底/竞争两 pass 都在此序上扫

    by_code = {e["code"]: e for e in kept}
    source_of: Dict[str, str] = {}
    selected_codes: List[str] = []
    quota_by_board: Dict[str, int] = {}   # 常驻 index_code -> 实际认领的保底数(板块状态诊断)

    # —— ④a 保底 pass(用户 2026-07-26 拍板):每个常驻板块(**按配置顺序**)取该板块内情报排序
    #    最高的至多 QUOTA 只,**仅从 quota_eligible 池**里选(hard_cut/未过② 绝不因保底捞回)。
    #    归属口径:一票同属多个常驻板块时,归**配置顺序里最先轮到且仍有空额**的板块认领(claim
    #    后不再被后续常驻或竞争重复计入),故一票只占一个保底名额。合格票不足 QUOTA → 有几只放
    #    几只、缺额自然退回下方竞争 pass 的公共池。————————————————————————————————————
    for pb in permanent_codes:   # 配置顺序;重复精确名对应的多 code 各算一个常驻板块
        if len(selected_codes) >= top_n:
            break
        picks = 0
        for e in kept:   # 已全局情报排序
            if picks >= QUOTA_PER_PERMANENT_BOARD or len(selected_codes) >= top_n:
                break
            code = e["code"]
            if code in source_of or not e["quota_eligible"]:
                continue
            if pb in e["its_permanent_boards"]:
                source_of[code] = SOURCE_QUOTA
                selected_codes.append(code)
                picks += 1
        quota_by_board[pb] = picks   # 该常驻板块实际认领保底数(可能 < QUOTA,缺额退回公共池)

    # —— ④b 竞争 pass:剩余名额(top_n − 实际保底数)按情报排序从公共池(未被认领的
    #    quota_eligible 票 = 常驻其余票 + 暴起板块票)竞争,填到 top_n。去重(source_of 已认领的跳过)。
    for e in kept:
        if len(selected_codes) >= top_n:
            break
        code = e["code"]
        if code in source_of or not e["quota_eligible"]:
            continue   # 非 quota_eligible(forced 豁免的 hard/未过②)不参与竞争,只走下方 forced 保证
        source_of[code] = SOURCE_COMPETITION
        selected_codes.append(code)

    # —— ④c forced 保证(§2.5「强制纳入」,不变):forced 票若未入 → 追加(source=forced,
    #    可略超 top_n,同既有语义;含 forced 豁免的 hard/非成员票)。————————————————————
    if forced_set:
        for e in kept:
            code = e["code"]
            if e["is_forced"] and code not in source_of:
                source_of[code] = SOURCE_FORCED
                selected_codes.append(code)

    # 组装:按情报排序重排(rank 反映情报强度,保底票落其自然位、由 source 标识来源),写入 source。
    top = [by_code[c] for c in selected_codes]
    for e in top:
        e["source"] = source_of[e["code"]]
    top.sort(key=_sort_key)

    # —— 常驻板块状态诊断(用户 2026-07-26 拍板:0 只/不足 2 只必须带「为什么」,守项目一贯
    #    「『没有』和『没看』必须能分开」原则,静默空白是最差表达)。每个常驻板块一条:过②卫生线
    #    survivor 数 / 过行业闸数 / 被行业闸挡数 / 被 K4 hard_cut 拦数 / 实际保底数 + 人读文案。—————
    board_status = _permanent_board_status(
        permanent_codes, index_names, board_members_of, raw_survivor_codes,
        dominant_of, industry_of, hard_cut_codes, quota_by_board,
    )

    names = _load_stock_names([e["code"] for e in top], db_path)
    out: List[Candidate] = []
    for i, e in enumerate(top, start=1):
        out.append(_build_intel_candidate(e, rank=i, cfg=cfg, step1_hot=step1_hot,
                                           member_map=member_map, index_names=index_names,
                                           names=names, board_status=board_status))
    return out


def _permanent_board_status(
    permanent_codes: List[str],
    index_names: Dict[str, str],
    board_members_of: Dict[str, List[str]],
    raw_survivor_codes: Set[str],
    dominant_of: Dict[str, Set[str]],
    industry_of: Dict[str, str],
    hard_cut_codes: Set[str],
    quota_by_board: Dict[str, int],
) -> List[Dict[str, Any]]:
    """每个常驻板块一条状态(诊断漏斗):`surviveCount`(过②卫生线成员)/ `industryGatePass`
    (其中行业过闸=属本板块主导行业)/ `industryGateBlocked`(行业不属主导被挡)/ `hardCutBlocked`
    (过闸但命中 K4 hard_cut 被拦)/ `quotaFilled`(实际保底数)+ `note`(人读文案,0 只/不足 2 只
    时说清「为什么」)。让空板块栏能区分「今天真没合格票」vs「系统坏了/被忘了」。"""
    out: List[Dict[str, Any]] = []
    for pb in permanent_codes:
        name = index_names.get(pb, pb)
        members = board_members_of.get(pb, [])
        dom = dominant_of.get(pb, set())
        survivors = [c for c in members if c in raw_survivor_codes]
        gate_pass = [c for c in survivors if (industry_of.get(c) or "") in dom]
        gate_blocked = len(survivors) - len(gate_pass)
        hardcut = sum(1 for c in gate_pass if c in hard_cut_codes)
        quota = quota_by_board.get(pb, 0)
        out.append({
            "board": name,
            "surviveCount": len(survivors),
            "industryGatePass": len(gate_pass),
            "industryGateBlocked": gate_blocked,
            "hardCutBlocked": hardcut,
            "quotaFilled": quota,
            "note": _board_status_note(name, len(survivors), gate_blocked, hardcut, len(gate_pass), quota),
        })
    return out


def _board_status_note(name: str, n_surv: int, gate_blocked: int, hardcut: int, gate_pass: int, quota: int) -> str:
    """板块状态人读文案。满额(≥QUOTA)简述;不足 QUOTA(含 0)时**说清「为什么」**(几只行业不属
    主导、几只命中 K4 安检、几只被在前常驻板块认领),0 只时明标「宁缺毋滥、非静默空白」。"""
    if quota >= QUOTA_PER_PERMANENT_BOARD:
        return f"{name}:保底 {quota} 只(过卫生线 {n_surv} 只、过行业闸合格 {gate_pass} 只)"
    parts: List[str] = []
    if gate_blocked:
        parts.append(f"{gate_blocked} 只行业不属本板块主导行业")
    if hardcut:
        parts.append(f"{hardcut} 只过闸但命中 K4 安检拦截")
    eligible_left = gate_pass - hardcut - quota   # 过闸+过K4 但没拿到名额(被配置在前的常驻板块认领)
    if eligible_left > 0:
        parts.append(f"{eligible_left} 只已被在前常驻板块认领")
    if n_surv == 0:
        reason = "无成员过卫生线(流动性/次新/趋势/ST 任一未过)"
    else:
        reason = "、".join(parts) if parts else f"仅 {quota} 只合格代表票"
    tail = ",宁缺毋滥、非静默空白" if quota == 0 else ""
    return f"{name}:保底 {quota} 只 —— {n_surv} 只过卫生线成员中 {reason}{tail}"


def _build_intel_candidate(
    e: Dict[str, Any],
    *,
    rank: int,
    cfg: MomentumConfig,
    step1_hot: Dict[str, SectorScore],
    member_map: Dict[str, List[str]],
    index_names: Dict[str, str],
    names: Dict[str, str],
    board_status: Optional[List[Dict[str, Any]]] = None,
) -> Candidate:
    """把情报管线的一个保留候选装配成 `Candidate`(复用 candidates.py 四件套文案/形态标签/
    展示分,同码不重写)。新增 `k4_flags`(K4 命中标注码)+ `intel_rank`(情报排序理由,
    v1.4-③ 起含三级排序键原样透出,让客户端/信息卡说清「这票为什么排这里」)。"""
    row = e["row"]
    code = e["code"]
    close = row["close"]
    board = e["board"]   # 取自 today 面板(merge_meta),K4 面板 row 无 board 列
    boards = member_map.get(code, [])
    hot_names = [
        f"{step1_hot[b].name}(板块年龄{step1_hot[b].board_age}天,20日{step1_hot[b].ret_20d:+.1%})"
        for b in boards if b in step1_hot
    ]
    sector_names = [index_names.get(b, b) for b in boards]
    stop_price = round(close * (1 - cfg.stop_pct), 2) if cfg.stop_pct else None
    spec = invalidation_spec()
    intel_rank = {
        # sectorFlow:v1.4-③ 起**并列展示,不参与排序**(需求 8;见 `_sort_key` 白名单)。
        "sectorFlow": round(e["sector_flow"], 1) if e["sector_flow"] is not None else None,
        # themePersistDays:v1.3 起既有字段名,**保留不改语义**(老客户端兼容)——值与下方新字段
        # `industryPersistDays` 同源同值(② 唯一源),两个字段名并存是刻意的向后兼容,不是笔误。
        "themePersistDays": e["industry_persist_days"],
        "highElasticity": board in S.HIGH_ELASTICITY_BOARDS,
        # 行业(stock_basic.industry;一票一行业)——过行业闸后带出参,客户端据此说清「凭什么在此板块栏」。
        "industry": e.get("industry", ""),
        # 常驻板块状态诊断(**报告级构件,每只候选携同一份**——build_intel_candidates 只能经候选列表
        # 进报告快照,0 保底板块自身无候选可挂,故挂在所有候选的 intel_rank 上让客户端从任一候选读到;
        # 每条含 survivor/过闸/被挡/被拦/保底数 + 人读文案,让 0 只/不足 2 只的板块能说清「为什么」,
        # 守项目「『没有』和『没看』必须能分开」原则)。
        "permanentBoardStatus": board_status or [],
        # 入选来源(quota=常驻保底 / competition=情报竞争 / forced=问询强制),供客户端说清
        # 「为什么在榜」。带在 intelRank 里(用户 2026-07-26 拍板「在既有 intelRank 里带来源标记」);
        # 落报告快照 JSON。
        "source": e.get("source", SOURCE_COMPETITION),
        # —— v1.4-③ 新增(需求 8,③-E):排序键三级原样透出 ————————————————————————————
        "industryRank": e["industry_rank"],           # 排序键①;None=未参与排名(无行业/成员<5)
        "industryPersistDays": e["industry_persist_days"],  # 排序键②;与 themePersistDays 同值同源
        "yellowCardCount": e["yellow_card_count"],     # 排序键③;仅数 DB avoid_flag,不数 hard_cut/合成码
    }
    return Candidate(
        ts_code=code,
        name=names.get(code, code),
        close=close,
        score=round(e["base_score"], 1),   # 展示排序分(技术贴前高度),**非**排序键(rank 由情报排序定)
        rank=rank,
        board=board,
        pattern_tags=pattern_tags(row),
        hot_sectors=hot_names,
        sector_names=sector_names,
        entry_plan=entry_plan_text(row, cfg),
        stop_loss=stop_loss_text(stop_price, cfg),
        target=target_text(cfg),
        invalidation_text=invalidation_text(spec),
        invalidation_spec=spec,
        entry_spec=entry_spec(row, cfg),
        k4_flags=e["k4_flags"],
        intel_rank=intel_rank,
        raw=row,
    )


__all__ = [
    "build_intel_candidates",
    "NON_NEW_MIN_DAYS",
    "BREAKOUT_TOP_N",
    "TOP_N_CANDIDATES",
    "QUOTA_PER_PERMANENT_BOARD",
    "SOURCE_QUOTA",
    "SOURCE_COMPETITION",
    "SOURCE_FORCED",
    "INDUSTRY_GATE_MIN_LIFT",
]
