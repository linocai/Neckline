"""关注池组装(plan 阶段3 §2.4 的前置步骤;**V2-⑧-A 改组**)。哨兵每拍只对一个
有限、可解释的「关注池」批量拉价,不对全市场 ~5900 只票逐分钟轮询——理由见
`retreat.py` 模块头注释(免费源持续高频全市场轮询的限流/稳定性代价 vs 关注池
已覆盖真正需要盯的票)。

**V2-⑧-A 新组成(plan §五 V2-⑧-A)**::

    持仓 ∪ **T1/T2 篮子成员**(D0 冻结的前两档,`baskets`/`basket_members`)
         ∪ **相关板块指数** ∪ 昨日涨停股(宽度代理样本)

三处**刻意**的取舍,别当成疏漏:

    · **自选池已整链删除**(V2-⑬-11 执行完毕,裁定 #9-a):`neckline/watchlist.py`
      与 `report/watchlist_check.py` 已物理删除、`watchlist` 表停写留档;⑧-A 留下的
      两个恒空字段 `watchlist_codes`/`watchlist_candidates` 随之一并删除,
      `engine.py`/`precall.py` 的 `wu.candidates + wu.watchlist_candidates` 同步改写。
    · **候选已删**(V2-⑬-1 执行完毕):V1 候选榜与 `report.candidates.Candidate` 数据类
      全部退役。**证伪哨兵(⑪-A 点名的纪律分支)判定对象随之换成 T1/T2 篮子成员**
      —— 本模块新出 `targets: List[WatchTarget]`(轻量载体:码 / 名 / 全局证伪 spec /
      所属篮子),`engine.py` 遍历它而不是候选列表;判定逻辑一行未改(证伪 spec 本就是
      零入参的全局常量,见 `invalidation.invalidation_spec`)。
      ⚠ **买点哨兵(`sentinel/entry.py`)同批退役**:它 100% 由 K1 的 per-code
      `entry_spec`(platform_high / ma10 / breakout_vol_expand)驱动,V2 没有「单票买点
      计划」这个概念,给篮子成员现编一个买点 = 发明策略(§3.8 禁)。已如实登记。
    · **「相关板块 ETF/指数」本版落地为板块基准指数**(见 `BOARD_BENCHMARK_INDEX`):
      本项目**没有** ETF 成分/映射数据源(TuShare 600 元档未含,`ths_index` 是同花顺
      板块指数、免费实时源不认它的代码),硬编一份 ETF 清单等于凭空发明。指数是
      **可得且可核对**的那一半,已如实登记(⑧ 完工记录)。

**容量与配额(≤ `breadth_cap`,默认 200;V2-⑧-G 由"先到先得的优先队列"改成
"有界必需项全进 + 两份纪律测量样本各有保底")**:

    ①【有界必需项,无条件全进】持仓(三仓制 ≤3)+ T1/T2 篮子成员(≤7 篮 × ≤3 = ≤21)
      + 板块基准指数(≤5)= 上界 `MANDATORY_POOL_RESERVE`(29),**本来就有界**,
      不必让它们参与抢位;内部优先序仍是 `持仓 > 成员 > 指数`(真要挤〔`breadth_cap`
      被调得极小〕先挤没有纪律消费方的指数)。
    ②【两份纪律测量样本,各有保底】剩余 `breadth_cap − MANDATORY_POOL_RESERVE`
      (=171)个位在**主线切片**(`MAINLINE_SLICE_QUOTA_FLOOR`)与**昨日涨停**
      (`PREV_LIMIT_UP_QUOTA_FLOOR`)之间按保底分配,**谁不够用谁的实际量,余量归
      对方**。

⚠ **为什么测量预算按「必需项上界 29」算,而不是按"这一拍实际占了几个"算**:后者会
让 **LLM 多挑一个篮子成员就把某份测量样本挤小一只** —— 一个大小随 LLM 输出浮动的
样本,产生的是**不可靠的测量**(⑧-G-D 原文:会被别人挤掉、大小不可预期的样本 =
不可靠的测量)。按固定上界算,两份样本的实际取值**只是机械输入的函数**,与篮子里
有几只票**完全无关**(单测锁死:换篮子成员数 → 两份测量样本逐位不变)。代价是必需项
不满 29 时池子会略微欠填(≤29 个位空着),**刻意接受** —— 少 poll 几只的代价远小于
一个会漂的纪律测量。⚠ 若必需项**异常**超过 29(上游违约,如篮子超发),测量预算按
实际占用缩,不越 `breadth_cap`(降级路径,如实登记)。

⛔ **`breadth_cap` 一字不动(仍 200)** —— 抬它会改盘中轮询量与限流风险面(⑧-G-D
明文,守门单测锁死)。

⚠ **指数 / ETF 代码不会污染退潮哨兵的宽度样本**:`retreat.compute_breadth_snapshot`
按 `stock_basic` 元数据逐票判涨跌停,查无元数据的代码**结构上就被跳过**(它本来就
不可能"涨停/跌停")——这不是巧合,是那个函数早就写死的口径,新增指数代码因此
零影响于退潮判定(单测锁死)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import polars as pl

from neckline.calendar import prev_trading_day, trading_days_between
from neckline.data.board import Board, classify
from neckline.data.limit_derived import resolve_exempt_days
from neckline.data.market_data import get_market_slice, load_stock_basic, scan_table_range
from neckline.selection.basket_store import BasketRef, load_baskets_for_date
from neckline.sentinel import mainline
from neckline.sentinel.invalidation import invalidation_spec
from neckline.sentinel.positions import Position, load_open_positions

logger = logging.getLogger(__name__)

# 退潮哨兵市场宽度代理样本的容量上限(见模块头注释;避免极端全员涨停日把批量
# 请求撑到不合理大小——保守分块防线,不是精细调过的数字)。v1.1-C 起同时也是
# 关注池总上限;V2-⑧-A 改组后口径不变(仍 200,「与现状同量级」)。
DEFAULT_BREADTH_CAP = 200
# 计算前5日均量时的自然日回溯窗口(足够覆盖5个交易日,含长假缓冲)。
_VOLUME_LOOKBACK_DAYS = 15

# —— V2-⑧-G-D 池位配额(三个数,含义见模块头「容量与配额」)——————————————————
# 有界必需项的**上界**:持仓 ≤3 + T1/T2 篮子成员 ≤7 篮 × ≤3 = ≤21 + 板块指数 ≤5。
# plan §五 ⑧-G-D 原文的 ≈29,写死在这里当"测量预算"的分界线,让两份测量样本的大小
# **与 LLM 挑了几个成员无关**。
MANDATORY_POOL_RESERVE = 3 + 21 + 5
# 两份纪律测量样本的保底名额(合计 = 200 − 29 = 171,即设计点上把剩余池位分完)。
# **实测定数(2026-08-03,真实 parquet 只读,2026-07 全月 17 个交易日)**:
#   · 主线切片(K=4)40 ~ 100 只/日 → 保底 100 = 实测上界,**四天窗口内一次没被截断**;
#   · 昨日涨停 32 ~ 223 只/日(planner 引的 43~129 是 ⑧-F 四天窗口的范围)。
# ⚠ **如实登记:两份样本"都不被截断"在 200 池位内做不到** —— 07-21 两份合计需要 213
# 个不同代码(切片 100 + 涨停 121 − 重叠 8)> 171;更极端的 07-01 光昨日涨停就 223 只,
# **现状代码本来也在截它**(旧口径给涨停的额度同样是"剩余 ~171")。故按"新样本(主线
# 切片)优先吃满实测上界、老样本(昨日涨停)拿余量且不低于保底"定数,并把这条差异
# 交回 planner(⑧-G 完工记录 + §七 P3-37 一并回看)。
# ⛔ 这两个数是**池位分配**,不是判定阈值;调它们不改任何触发阈值,但会改测量样本的
# 大小,**改前先读 ⑧-G-D 与完工记录里的实测表**。
MAINLINE_SLICE_QUOTA_FLOOR = 100
PREV_LIMIT_UP_QUOTA_FLOOR = 71

# 盘中会被盯的篮子档位(T1/T2)。T3 不进盘中池是**容量取舍**,不是"T3 不重要"——
# 它在 EOD 那一拍照样被判(`basket_verify.run_eod_verification` 判全部档位)。
INTRADAY_BASKET_TIERS: Tuple[int, ...] = (1, 2)

# 「相关板块 ETF/指数」的可得落地 = **板块基准指数**(见模块头注释:没有 ETF 映射
# 数据源,不硬编清单)。代码取自 `scripts/backfill.py::INDEX_CODES` 同一批(本项目
# 追踪的指数),主板按交易所分沪深两支。
BOARD_BENCHMARK_INDEX: Dict[Board, str] = {
    Board.GEM: "399006.SZ",     # 创业板指
    Board.STAR: "000688.SH",    # 科创50
    Board.BSE: "899050.BJ",     # 北证50
}
MAIN_BOARD_INDEX_SH = "000001.SH"   # 上证综指
MAIN_BOARD_INDEX_SZ = "399001.SZ"   # 深证成指


@dataclass(frozen=True)
class WatchTarget:
    """盘中要被**证伪哨兵**判的一只票(V2-⑬-1 起 = T1/T2 篮子成员)。

    刻意做成「码 + 名 + 一份 spec」的轻量载体,不是把整张篮子卡塞进哨兵——哨兵只需要
    判据,拿到更多字段只会诱使后人在盘中重算(§2.4 铁律:盘中不产生新决策)。
    `invalidation_spec` 对所有目标是**同一份全局常量**(见
    `invalidation.invalidation_spec`),挂在每个目标上只是为了让判定函数保持
    duck-typed、与 V1 的调用形状逐字一致。"""
    ts_code: str
    name: str
    invalidation_spec: Dict[str, Any]
    basket_key: str = ""        # 来自哪个篮子(审计/文案用,不参与判定)


@dataclass
class WatchUniverse:
    trade_date: date            # 哨兵运行的这一天(今天)
    report_date: date           # prev_trading_day(trade_date)——篮子理应来自这天
    report_found: bool          # 该日报告是否真的生成过(找不到不代表篮子也没有)
    targets: List[WatchTarget]  # V2-⑬-1:证伪哨兵的判定对象 = T1/T2 篮子成员
    positions: List[Position]
    # 本拍纳入的**昨日涨停样本**(退潮宽度 / 炸板率的代理样本)。⚠ V2-⑧-G 起它是
    # 「`crc32(ts_code)` 升序取前 N 只」的**机械结果**(review 判定线 🟡-N1 改判,
    # 2026-08-03:原「按连板数降序」与被测量的量相关,见 `_load_prev_limit_up_codes`
    # docstring),不再是"扣掉别人之后剩下的"——极个别码可能同时是持仓 / 篮子成员
    # (`codes` 去重后只占一个池位)。
    breadth_extra_codes: List[str]
    # 池配额压缩前的**需求量**(D0 全部涨停股只数,截断由 `breadth_extra_codes` 体现;
    # ⑧-G-D 追加要求的留痕,见 `breadth_extra_payload()`)。⛔ 别把 `breadth_extra_codes`
    # 的长度(池配额压后的实际量,如 71)误当成"当天全部涨停股"。
    breadth_extra_needed: int
    # —— V2-⑧-A 新增两类来源 ————————————————————————————————————————————
    baskets: List[BasketRef] = field(default_factory=list)      # D0 的 T1/T2 篮子(含成员)
    basket_codes: List[str] = field(default_factory=list)       # 上面那些篮子的成员代码(去重)
    index_codes: List[str] = field(default_factory=list)        # 相关板块基准指数(只进存拍/语境)
    # —— V2-⑧-G:主线跳水的**配额切片**(纪律测量样本,派生见 `sentinel/mainline.py`)
    mainline_sample: mainline.MainlineSample = field(default_factory=mainline.MainlineSample)
    mainline_codes: List[str] = field(default_factory=list)      # 切片里**新占池位**的那些码
    codes: List[str] = field(default_factory=list)  # 去重后全部关注代码(拉价用)

    def breadth_extra_payload(self) -> Dict[str, Any]:
        """昨日涨停宽度代理样本的『需求量 vs 实际采纳量』留痕(⑧-G-D 追加要求,
        review 判定线 🟡-N1 一并处理,2026-08-03)。**形状照
        `mainline.MainlineSample.payload()` 的姊妹字段**(`codes`/`size`/
        `restricted_from` 同名同义)——两份纪律测量样本用同一套词汇,审计读一遍
        就都懂。落 `retreat_metrics.breadth_extra_sample_json`(体例照既有
        `hot_sector_sample_json`),防日后审计炸板率的人把 `size`(池配额压后的
        实际样本量,如 71)误当成"当天全部涨停股"——`restricted_from` 非 None
        时才是那个真实的需求量数字。"""
        size = len(self.breadth_extra_codes)
        return {
            "codes": list(self.breadth_extra_codes),
            "size": size,
            "restricted_from": self.breadth_extra_needed if self.breadth_extra_needed > size else None,
        }


def load_watch_universe(
    trade_date: date,
    *,
    breadth_cap: int = DEFAULT_BREADTH_CAP,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> WatchUniverse:
    """组装 `trade_date` 这一天的关注池。`db_path`/`parquet_dir` 均可覆盖,供单测
    注入隔离环境。无报告 / 无持仓 / 无篮子都不报错——空关注池是合法状态(如刚
    部署当天尚未跑过 `scripts/report.py`),调用方(engine.py/precall.py)据此
    优雅跳过对应哨兵。
    """
    from neckline.report import store

    report_date = prev_trading_day(trade_date)
    report = store.load_report(report_date, db_path=db_path)

    positions = load_open_positions(db_path=db_path)

    # —— V2-⑧-A:D0(= report_date)冻结的 T1/T2 篮子成员 ——————————————————
    baskets = _load_intraday_baskets(report_date, db_path=db_path)
    basket_codes: List[str] = []
    for b in baskets:
        for code in b.member_codes:
            if code not in basket_codes:
                basket_codes.append(code)

    # V2-⑬-1:证伪哨兵的判定对象 = T1/T2 篮子成员(名称查不到就退回代码,不猜)。
    names = load_stock_meta(basket_codes, db_path=db_path) if basket_codes else {}
    spec = invalidation_spec()
    basket_of: Dict[str, str] = {}
    for b in baskets:
        for code in b.member_codes:
            basket_of.setdefault(code, b.basket_key)
    targets = [
        WatchTarget(
            ts_code=code,
            name=(names[code].name if code in names else code),
            invalidation_spec=spec,
            basket_key=basket_of.get(code, ""),
        )
        for code in basket_codes
    ]

    # —— ①【有界必需项】持仓 > T1/T2 成员 > 板块指数,无条件全进(理由见模块头)——
    index_codes = _related_index_codes(
        list(dict.fromkeys(basket_codes + [p.ts_code for p in positions])), db_path=db_path
    )
    mandatory: List[str] = []
    seen = set()
    for c in [p.ts_code for p in positions] + basket_codes + index_codes:
        if c not in seen:
            seen.add(c)
            mandatory.append(c)
    if len(mandatory) > breadth_cap:
        mandatory = mandatory[:breadth_cap]
        seen = set(mandatory)
        index_codes = [c for c in index_codes if c in seen]

    # —— ②【两份纪律测量样本按保底分配剩余池位】——————————————————————————
    # ⚠ 两份样本的**取值只由机械输入决定**(切片大小 / 昨日涨停只数 / 三个常量),
    # ⛔ 刻意不看 `mandatory` 里已经有谁 —— 一旦"已经在池里的码不占额度",LLM 多挑
    # 一个恰好也在切片里的成员就会让样本多出一只,残留耦合 ②b 又回来了。重叠的码
    # 只是让池子略微欠填(去重后总数更小),**永远不会超过 `breadth_cap`**。
    budget = _measurement_budget(breadth_cap, len(mandatory))
    sample = _derive_mainline_sample(report_date, db_path=db_path, parquet_dir=parquet_dir)
    limit_up_all = _load_prev_limit_up_codes(report_date, parquet_dir=parquet_dir)
    quota_mainline = _mainline_quota(budget, sample.size, len(limit_up_all))
    sample = sample.restrict(quota_mainline)
    breadth_extra = limit_up_all[:max(0, budget - quota_mainline)]

    codes: List[str] = list(mandatory)
    mainline_codes: List[str] = []
    for c in sample.codes:
        if c not in seen:
            seen.add(c)
            mainline_codes.append(c)
            codes.append(c)
    for c in breadth_extra:
        if c not in seen:
            seen.add(c)
            codes.append(c)

    return WatchUniverse(
        trade_date=trade_date,
        report_date=report_date,
        report_found=report is not None,
        targets=targets,
        positions=positions,
        breadth_extra_codes=breadth_extra,
        breadth_extra_needed=len(limit_up_all),
        baskets=baskets,
        basket_codes=basket_codes,
        index_codes=index_codes,
        mainline_sample=sample,
        mainline_codes=mainline_codes,
        codes=codes,
    )


def _measurement_budget(breadth_cap: int, mandatory_used: int) -> int:
    """两份纪律测量样本可用的池位总数(⑧-G-D)。

    **按必需项的上界算,不按这一拍实际占了几个** —— 这正是"LLM 挪不动测量样本"这条
    不变量的落点(理由见模块头)。上界按 `breadth_cap` 等比缩放,好让单测里把池调得
    很小时仍有意义(设计点 `breadth_cap=200` 处恰为 `MANDATORY_POOL_RESERVE`=29);
    必需项**异常**超过上界时按实际占用缩,保证总量不越 `breadth_cap`。
    """
    reserve = MANDATORY_POOL_RESERVE * breadth_cap // DEFAULT_BREADTH_CAP
    return max(0, breadth_cap - max(mandatory_used, reserve))


def _mainline_quota(budget: int, need_mainline: int, need_limit_up: int) -> int:
    """主线切片能拿几个池位:**保底管够,余量归对方**(⑧-G-D)。

    `min(实际需要, max(自己的保底, 总预算 − 对方的实际需要))` —— 三个量都只跟**机械
    输入**有关(切片大小 / 昨日涨停只数 / 两个常量),⛔ 与篮子成员数无关。
    这个式子同时保证了对方的保底:主线最多拿到 `budget − PREV_LIMIT_UP_QUOTA_FLOOR`
    (当昨日涨停多到吃得下保底时),昨日涨停因此至少拿得到自己的保底。
    """
    if budget <= 0:
        return 0
    floor_mainline = MAINLINE_SLICE_QUOTA_FLOOR
    floors_total = MAINLINE_SLICE_QUOTA_FLOOR + PREV_LIMIT_UP_QUOTA_FLOOR
    if budget < floors_total:       # 池被调小(单测/极端配置):两个保底等比缩
        floor_mainline = budget * MAINLINE_SLICE_QUOTA_FLOOR // floors_total
    return max(0, min(need_mainline, max(floor_mainline, budget - need_limit_up)))


def _derive_mainline_sample(
    report_date: date, *, db_path: Optional[Path], parquet_dir: Optional[Path],
) -> mainline.MainlineSample:
    """主线跳水的机械切片(⑧-G)。派生失败 → 空样本 + 原因码,⛔ 绝不因此掀翻整个
    关注池(同 `_load_intraday_baskets` 的降级纪律:拿不到切片也还要盯持仓)。"""
    try:
        return mainline.derive_mainline_sample(
            report_date, db_path=db_path, parquet_dir=parquet_dir)
    except Exception:  # noqa: BLE001
        logger.warning("[universe] %s 主线切片派生失败,本拍主线跳水一路不判", report_date,
                       exc_info=True)
        return mainline.MainlineSample(unavailable_reason=mainline.REASON_SEED_FAILED)


def _load_intraday_baskets(report_date: date, *, db_path: Optional[Path]) -> List[BasketRef]:
    """D0 的 T1/T2 篮子。读库失败 / 无篮子 → 空列表(**合法状态**:V2 引擎还没跑过、
    或今日无篮子达到定档标准);⛔ 绝不因此掀翻整个关注池 —— 哨兵拿不到篮子也还要
    盯持仓。"""
    try:
        return load_baskets_for_date(report_date, tiers=INTRADAY_BASKET_TIERS, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[universe] 读 %s 的 T1/T2 篮子失败,本拍关注池不含篮子成员",
                       report_date, exc_info=True)
        return []


def _related_index_codes(codes: List[str], *, db_path: Optional[Path]) -> List[str]:
    """这批票**所在板块**的基准指数(去重、排序,**确定性**)。板块判定唯一源
    `load_stock_meta`(→ `data/board.classify`),⛔ 不自己写前缀正则。查无元数据的
    代码跳过(既判不了板块,也就给不出"相关指数",不猜)。"""
    if not codes:
        return []
    try:
        meta = load_stock_meta(codes, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[universe] 查板块元数据失败,本拍关注池不含板块指数", exc_info=True)
        return []
    out: set = set()
    for code, m in meta.items():
        if m.board == Board.MAIN:
            out.add(MAIN_BOARD_INDEX_SH if code.upper().endswith(".SH") else MAIN_BOARD_INDEX_SZ)
        elif m.board in BOARD_BENCHMARK_INDEX:
            out.add(BOARD_BENCHMARK_INDEX[m.board])
    return sorted(out)


def _load_prev_limit_up_codes(report_date: date, *, parquet_dir: Optional[Path]) -> List[str]:
    """D0 全部涨停股,按 `crc32(ts_code)` 升序(**不截断** —— ⑧-G 起截断由池配额
    统一裁,调用方才知道还剩几个位;这里返回"需求"而不是"配额后的结果")。

    ⚠ **排序键改判(review 判定线 🟡-N1,2026-08-03,PROJECT_PLAN §五 ⑧-G-D 第②条
    授权:「截断必须无偏——截取顺序不得与被测量的量相关」)**:原实现按
    `consec_limit_up_days` 降序(且未传 `maintain_order`,并列名次的相对顺序连
    "parquet 行序"这个近似说法都不保证,是 polars 排序实现的未文档化行为——比
    "不确定性"更差,是**未定义行为**)。核实结论:**该序与被测量的量相关,不只是
    不确定,是有偏**——本项目 ⑦-K7 审计已实证连板高度是双尾放大器:簇内连板高度
    最高的成员,涨停触达概率约 1.5×、**次日跌停概率约 3×** 于同簇其余成员(详见
    PROJECT_PLAN §五 ⑦-K7 完工记录;⛔ 本模块住 `sentinel/`,该发现的标注件实现
    禁止被本目录引用,这里只借它的**统计结论**,不接它的代码),而这份样本正好
    喂给 `retreat.compute_breadth_snapshot` 算跌停数 / 炸板率(CLAUDE.md 明载的退潮
    判据输入)——池位不够时优先保留连板高的,等于把跌停风险系统性更高的一批
    塞进分子,是 ⑧-F 那类有偏抽样的翻版。全仓未找到「截断优先保连板高」的注意力
    语义依据(`report/sentiment.py::max_consec_limit_up` 是独立的全市场 EOD 扫描,
    不消费这份顺序;本函数自 v1 阶段3引入起〔`baff9e5`〕就只服务于「退潮哨兵市场
    宽度代理样本」这一个目的,原始提交未附带任何展示/注意力用途的说明)。

    改用 `mainline.crc_rank`(全项目 crc32 排序唯一实现,不抄第二份)——与 ⑧-G-B
    主线切片那份姊妹测量样本同一套采样键,纯采样、与连板高度 / 板块 / 涨跌停幅度
    均无关,且跨进程跨天可复现(重写分区、行序打乱,两次加载结果逐位相同)。
    """
    prev_limit = get_market_slice(report_date, table="limit_derived", parquet_dir=parquet_dir)
    if prev_limit.is_empty():
        return []
    codes = prev_limit.filter(pl.col("is_limit_up"))["ts_code"].to_list()
    return sorted(codes, key=mainline.crc_rank)


def load_prev5_avg_volume(
    codes: List[str], as_of: date, parquet_dir: Optional[Path] = None
) -> Dict[str, float]:
    """`codes` 各自的前5个交易日平均成交量(手,EOD `daily.vol` 口径),供
    `intraday.intraday_vol_ratio` 的基准输入。`as_of` 通常是哨兵运行的今天——
    基准取"今天之前"的5个交易日,不含今天(今天尚未收盘,数据本就还没有)。

    一次批量 scan(不逐票查询),codes 数量在数百量级时开销可忽略。数据不足5天
    (如新股)→ 用实际可用天数的均值;完全无历史 → 该票不出现在返回 dict 里
    (调用方按 `.get(code)` 处理缺失,自然走 `intraday_vol_ratio` 的 no_base 分支)。
    """
    if not codes:
        return {}
    end = prev_trading_day(as_of)
    start = end - timedelta(days=_VOLUME_LOOKBACK_DAYS)
    df = scan_table_range("daily", start, end, parquet_dir=parquet_dir)
    if df.is_empty():
        return {}
    df = df.filter(pl.col("ts_code").is_in(codes)).select(["ts_code", "trade_date", "vol"])
    out: Dict[str, float] = {}
    for (code,), sub in df.group_by(["ts_code"]):
        vols = sub.sort("trade_date")["vol"].to_list()[-5:]
        if vols:
            out[code] = sum(vols) / len(vols)
    return out


@dataclass
class StockMeta:
    ts_code: str
    name: str
    board: Board
    is_st: bool
    list_date: Optional[date]


def load_stock_meta(codes: List[str], db_path: Optional[Path] = None) -> Dict[str, StockMeta]:
    """`ts_code -> StockMeta`,供退潮/持仓哨兵算涨跌停价(`limit_derived.
    compute_intraday_limit_prices` 需要 board/is_st/trade_date)与展示名称用。

    `is_st` 直接读 `stock_basic.name`(当前名称)前缀——不做历史 as-of 判定
    (那是回测/报告的关切,盘中哨兵只关心"现在"),但要求 `stock_basic` 是最新的
    (`scripts/daily_update.py` 每交易日盘后刷新 `bootstrap_metadata()`,见该脚本)。
    """
    if not codes:
        return {}
    sb = load_stock_basic(db_path)
    if sb.is_empty():
        return {}
    sb = sb.filter(pl.col("ts_code").is_in(codes))
    out: Dict[str, StockMeta] = {}
    for row in sb.iter_rows(named=True):
        name = row["name"] or row["ts_code"]
        is_st = name.strip().strip("*").upper().startswith("ST")
        out[row["ts_code"]] = StockMeta(
            ts_code=row["ts_code"],
            name=name,
            board=classify(row["market"], row["ts_code"]),
            is_st=is_st,
            list_date=row["list_date"],
        )
    return out


def is_new_stock_exempt(meta: StockMeta, trade_date: date) -> bool:
    """该票在 `trade_date` 是否仍处于新股涨跌幅豁免窗口(见
    `neckline.data.limit_derived.resolve_exempt_days`)。`list_date` 未知 → 保守按
    "已过豁免期"处理(不放过涨跌停判定;宁可误判老股为受限,不放过真正该防的新股)。

    **性能/日志噪音坑(施工中实测踩到,已修)**:A 股大量主板老股 `list_date` 在
    2015 年之前(`neckline.calendar` 的 trade_cal DB 覆盖默认从 2015-01-01 起,
    见 `scripts/init_calendar.py`)——若直接对每只票调用
    `trading_days_between(list_date, trade_date)` 算精确交易日数,老股会命中
    "查询早于DB覆盖范围"分支,退化为**逐自然日** `is_trading_day` 判断 + 每天
    一条 warning 日志,几十年区间循环下来极慢且刷屏。**先用自然日差做廉价预筛**
    ——超过30自然日(远大于任何板块的5交易日豁免窗口上限)必然已过豁免期,直接
    返回 False,不进入昂贵的精确计算;只有"看起来像最近一个月内上市"的票才会
    走精确的 `trading_days_between`(此时区间必然很小,即使意外落在DB覆盖范围
    外,回退成本也可忽略)。
    """
    if meta.list_date is None or meta.list_date > trade_date:
        return False
    if (trade_date - meta.list_date).days > 30:
        return False
    exempt_days = resolve_exempt_days(meta.board, meta.list_date)
    days_since_listing = len(trading_days_between(meta.list_date, trade_date))
    return 0 < days_since_listing <= exempt_days


__all__ = [
    "WatchTarget",
    "WatchUniverse",
    "load_watch_universe",
    "load_prev5_avg_volume",
    "StockMeta",
    "load_stock_meta",
    "is_new_stock_exempt",
    "DEFAULT_BREADTH_CAP",
    "MANDATORY_POOL_RESERVE",
    "MAINLINE_SLICE_QUOTA_FLOOR",
    "PREV_LIMIT_UP_QUOTA_FLOOR",
    "INTRADAY_BASKET_TIERS",
    "BOARD_BENCHMARK_INDEX",
    "MAIN_BOARD_INDEX_SH",
    "MAIN_BOARD_INDEX_SZ",
]
