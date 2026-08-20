"""当日**在市**的全市场票池(PROJECT_PLAN §5.4.8 / §6 S6 末条)。

🔴 **为什么这个东西必须存在**:事实包的行数 = 当日 `daily` 的行数,「今天一笔都没
交易过」的票**不在包里**(`facts/pack.py` 模块头第 2 条纪律)。而 §6 S6 要求
`k9_disposition` **覆盖全市场每一只票** —— 那张表存在的全部理由,就是回答「昨天
为什么没选中这只票」;对一只全天停牌的票查不出答案,等于这张表在它最该说话的
时候缺席。`facts/pack.py:22-24` 因此逐字点名:「全市场 disposition 要覆盖『一只票
都没交易过』的情形时,由 S6 自己去 union `stock_basic`,不是本层的事」。

**为什么这个函数落在 `facts/` 而不是 `k9/`**:守门 G3 —— `k9/**` ⛔ 不 import
`tushare_client` / `market_data`(取数唯一来源是事实层)。`stock_basic` 是事实层
的原料,策略层要用它就得经这里过一手,⛔ 不许在 k9 里另开一条取数路。

⚠ **快照语义**(与 §5.3.5 回填那条**同一个**已知语义差,⛔ 别写「自动检测并回改
历史」的机灵代码):`stock_basic` 是**当前**快照,不是那天的。拿它算历史某天的
在市全集,得到的是「今天还在册的票里,那天已经上市且还没退市的那些」。
对**当日**的生产链(晚间跑今天)这就是准确的;对回填 / 标定跑历史,与事实包回填
用今天的申万归属是同一种偏差,要重置就整段重跑。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import List, Optional

import polars as pl

from neckline.data.market_data import load_stock_basic

logger = logging.getLogger(__name__)


def market_universe(
    trade_date: date, *, db_path: Optional[Path] = None
) -> List[str]:
    """`trade_date` 当日在市的全部 `ts_code`(升序,去重)。

    判据只有两条,都走**日期列**:
      · `list_date` 非空且 `<= trade_date` —— 那天还没上市的票不在全集里;
      · `delist_date` 为空,或 `> trade_date` —— 那天之后才退市的票**算在市**。

    ⚠ ⛔ **不看 `list_status`**:那是一个「**现在**是 L / D / P」的当前标志,拿它
    过滤历史某天会把今天已退市、那天还在正常交易的票整只抹掉。`delist_date` 才是
    带日期、能按 as-of 判的那一列。

    `stock_basic` 为空(还没抓过)→ 返回空列表 + 一条 WARNING。⛔ 不抛:
    「上游没抓过」归 `facts/completeness.py` 判「今天没跑成」,不是这里的事。
    """
    sb = load_stock_basic(db_path)
    if sb.is_empty():
        logger.warning(
            "[universe] `stock_basic` 为空 —— 全市场票池取不出来。"
            "⚠ 这会让全市场 disposition 退化成「只覆盖当日事实包的行」")
        return []
    listed = sb.filter(
        pl.col("list_date").is_not_null()
        & (pl.col("list_date") <= trade_date)
        & (pl.col("delist_date").is_null() | (pl.col("delist_date") > trade_date))
    )
    return sorted({str(c) for c in listed["ts_code"].to_list() if c})


__all__ = ["market_universe"]
