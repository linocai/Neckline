"""放量倍数 —— **全仓唯一实现**(K9 §3.0.1,裁定 13 / 14 / 15)。

> **放量倍数 = 当日成交量 ÷ 前 N 个交易日平均成交量**(N = `params.volume.maDays`)

三个判据用的是**同一个量**,因此只有这一处计算(⛔ 不许在三个地方各算一份):

| 用它的地方 | 判据 | 门槛 |
|---|---|---|
| 形态 1 放量启动 | 定义性:放量倍数 **≥ V** | `volume.eruptionMultiple` |
| 形态 2 超跌反弹 | 定义性「当日有实际换手」:放量倍数 **≥ 门槛** | `channels.p2.<档>.minVolMultiple` |
| 形态 3 中等生转强 | 定义性「当日尚未放量爆发」:放量倍数 **< V** | **同一个** `volume.eruptionMultiple` |

🔴 **形态 1 与形态 3 的互斥由判据本身保证**(裁定 15):`≥ V` 与 `< V` 是同一个量、
同一个 V 上的两个互补半区,合起来是全集、交集为空。⛔ 不靠事后仲裁,⛔ 不许给 V
分严格 / 放宽两档 —— 一分档就会出现「落在两个 V 之间」的票同时命中 p1(放宽)与
p3(严格),互斥当场破掉。

⚠ **它不是形态 4 的「量比」。** 量比的分母是前 **5** 个交易日均量(盘后口径,
§4.7:`daily_basic.volume_ratio` 与它实测完全一致,但只有 2 位小数、做排名会大量
并列 → 排名时自算 `vol/vol_ma5`)。两个量的**分母窗口不同**,⛔ 别混、⛔ 别合并。

**分母不含当日**:与 §4.7 实测确认过的量比口径(「全天成交量 ÷ **前** 5 个交易日
均量」)保持一致。含当日会让「今天放了多少量」这件事自己稀释自己。
"""

from __future__ import annotations

from typing import Optional

import polars as pl

from neckline.k9.contract import PackRange

#: 输出列名(⚠ 别叫 `volume_ratio` —— 那是形态 4 的另一个量,重名迟早出事)。
COLUMN = "vol_multiple"

#: 分母所需的**最少**历史天数比例。少于窗口一半的历史 → 该日整列判为不可用
#: (⛔ 不拿 3 天均量冒充 20 日均量:那会让上线首几天所有票都「放量」)。
_MIN_COVERAGE = 0.5


def compute(pack: PackRange, *, ma_days: int) -> pl.DataFrame:
    """放量倍数 `ts_code → vol_multiple`(两列 DataFrame)。

    历史不足(有效天数 < `ma_days` 的一半)或均量为 0 → 该票 `vol_multiple` 为
    **null**,⛔ 不填 0、不填 1:「算不出来」与「没放量」是两件事,前者不该让一只
    票通过 p3 的「尚未爆发」(< V)。所有门槛判定都必须先过 `is_not_null()`。
    """
    return _multiple(pack, ma_days=ma_days)


def _multiple(pack: PackRange, *, ma_days: int) -> pl.DataFrame:
    """「当日量 ÷ 前 N 日均量」的**唯一**实现。放量倍数与量比只差一个 N。"""
    if ma_days < 1:
        raise ValueError(f"均量窗口必须 >= 1,收到 {ma_days}")
    today = pack.select("ts_code", "vol")
    if today.is_empty():
        return pl.DataFrame(schema={"ts_code": pl.String, COLUMN: pl.Float64})
    today = pack.today.select(["ts_code", "vol"]).rename({"vol": "_vol_today"})

    hist = pack.history(days=ma_days, include_today=False)
    if hist.is_empty():
        return today.select("ts_code").with_columns(
            pl.lit(None, dtype=pl.Float64).alias(COLUMN))
    sessions = int(hist["trade_date"].n_unique())
    if sessions < max(1, int(ma_days * _MIN_COVERAGE)):
        return today.select("ts_code").with_columns(
            pl.lit(None, dtype=pl.Float64).alias(COLUMN))

    base = (
        hist.select(["ts_code", "vol"])
        .filter(pl.col("vol").is_not_null())
        .group_by("ts_code")
        .agg(pl.col("vol").mean().alias("_vol_ma"), pl.len().alias("_days"))
    )
    out = today.join(base, on="ts_code", how="left").with_columns(
        pl.when(
            pl.col("_vol_ma").is_not_null()
            & (pl.col("_vol_ma") > 0)
            & pl.col("_vol_today").is_not_null()
            & (pl.col("_days") >= max(1, int(ma_days * _MIN_COVERAGE)))
        )
        .then(pl.col("_vol_today") / pl.col("_vol_ma"))
        .otherwise(None)
        .alias(COLUMN)
    )
    return out.select(["ts_code", COLUMN])


#: 🔴 **量比的分母窗口 = 5 个交易日**(K9 §3.5 原文:「全天成交量 ÷ 过去 5 日均量」)。
#: 这**不是待标定参数**,是「量比」这个指标的**定义**的一部分 —— 换成别的天数算出来的
#: 就不叫量比了。§4.7 实测:它与 `daily_basic.volume_ratio` 相关系数 0.99997、最大绝对差
#: 0.005(= 2 位小数四舍五入半步),两者是同一个量。
#: ⚠ 排名**必须自算**:`volume_ratio` 只有 2 位小数,拿它排名会大量并列(§12 坑 4)。
VOLUME_RATIO_MA_DAYS = 5

#: 量比的输出列名(⚠ 与 `COLUMN` 是**两个不同的量**,见模块 docstring)。
RATIO_COLUMN = "vol_ratio_self"


def volume_ratio(pack: PackRange) -> pl.DataFrame:
    """形态 4 的**量比**(盘后口径)= 当日 vol ÷ 前 5 个交易日均量。

    与 `compute()` 的放量倍数是**两个不同的量**(分母窗口 5 vs `volume.maDays`),
    ⛔ 别合并、⛔ 别互相顶替。这里自算而不读 `daily_basic.volume_ratio`,唯一理由是
    后者只有 2 位小数、做排名会大量并列(§4.7 / §12 坑 4)。
    """
    df = _multiple(pack, ma_days=VOLUME_RATIO_MA_DAYS)
    return df.rename({COLUMN: RATIO_COLUMN})


def erupted(multiple: Optional[float], v: float) -> Optional[bool]:
    """「今天放量爆发了吗」的**唯一判据**(裁定 15)。

    `None` = 算不出来(历史不足 / 均量为 0)—— 调用方必须把它当作**两边都不中**,
    ⛔ 不许当成 False 塞进形态 3(那是拿「不知道」冒充「还没爆」)。
    """
    if multiple is None:
        return None
    return multiple >= v


__all__ = ["COLUMN", "RATIO_COLUMN", "VOLUME_RATIO_MA_DAYS",
           "compute", "volume_ratio", "erupted"]
