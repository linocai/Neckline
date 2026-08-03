"""纪律红绿灯判定项(**唯一实现**)。

**V2-⑬-11 迁移登记**:本函数原住 `neckline/report/watchlist_check.py`(自选体检)。
自选池 + 同花顺对账整链在 V2.0.0 按裁定 #9-a 删除,而**问询台是保留件**
(§五 ⑬「明确不动」:问询台主体 + `inquiry_log`),它的 `run_deterministic_checks`
一直与自选体检共用这一份判定 —— 因此本函数**原地搬家、逐字不改**到独立模块,
而不是随自选体检一起陪葬。搬家后唯一消费方 = `neckline/api/inquiry.py`。

⚠ 别在别处重抄一份:CLAUDE.md「纪律红绿灯要『拆解展示触发了哪条』时,不许手写
Python 重抄 `base_universe_expr()` 已 AND 成一个布尔的表达式(数值漂移)」——
选股域四项揉成一条组合文案、只有 config 可配的 P4/P5/P6 才逐项拆,就是本模块的体例。
"""

from __future__ import annotations

from typing import List, Tuple

import polars as pl

from neckline.strategy import signals as S
from neckline.strategy.momentum import MomentumConfig


def discipline_checks(cfg: MomentumConfig) -> List[Tuple[str, str, pl.Expr]]:
    """纪律红绿灯用到的禁买/黑名单判定项:(列名, 中文原因, 布尔表达式=True 表示
    触发该项禁买)。「选股域」四项(ST/北交所/价格/流动性/MA20 未形成)合并成一条
    展示原因(它们是 `research.panel.base_universe_expr()` 内部已经 AND 在一起的
    单一布尔源,该函数本身不可拆分——若在此处重新逐项手写等价条件,数值阈值会
    与 `base_universe_expr()` 各自维护一份,一旦上游改动就会漂移,故宁可损失一点
    展示粒度也不重复刻字面量);现役 config **可配的四项禁买过滤**(P4/P5/P6)按
    cfg 是否启用逐项决定是否纳入判定,不新拍任何阈值。

    **公开函数(plan §五 v1.3-⑤ 提升;V2-⑬-11 起本模块是它的家)**:
    `api/inquiry.py::run_deterministic_checks` 是唯一消费方,把命中项当**警告标注**
    (不拦人,见 `api/inquiry.py` 模块头)。~~自选体检 `score_watchlist` 把同一批
    命中项当红灯~~ —— 该消费方已随自选池删除(V2-⑬-11)。

    ⚠ **拆墙(v1.3.3,用户 2026-07-27 拍板)后本函数实际还剩什么**:高弹题材那条
    (`_dq_elastic`)由 `cfg.forbid_high_elasticity` 驱动,现役 v1.3.3 把它置 False,
    该分支**自然不再产出**任何红灯——本函数**没有任何硬编板块限制**,拆墙不需要改
    这里一行代码(`if cfg.forbid_high_elasticity:` 本就是唯一开关,已核对无残留硬编)。
    保留该分支不删:v1.3 仍是合法回退目标,回退后墙需要立刻回来。于是现役 config
    (P4/P5/P6 皆 None)下红灯**只剩真硬线**:选股域那一条组合原因(ST/退市风险 /
    北交所〔流动性薄〕/ 股价<2 元〔面值退市区〕/ 20 日均额<2000 万〔流动性太差〕/
    无 MA20〔次新未成形〕)。**板块限制与高弹已不在红灯之列。**"""
    from neckline.research.panel import base_universe_expr

    checks: List[Tuple[str, str, pl.Expr]] = [
        (
            "_dq_base",
            "不满足选股域(ST / 北交所 / 股价<2元 / 20日均额<2000万 / 无MA20 任一项,选股域清洗常开)",
            ~base_universe_expr(),
        ),
    ]
    if cfg.forbid_green_bigdown is not None:
        checks.append((
            "_dq_bigdown",
            f"绿盘大阴线(当日跌幅≤{cfg.forbid_green_bigdown:.0%},现役规则禁买)",
            S.forbid_green_bigdown(cfg.forbid_green_bigdown),
        ))
    if cfg.forbid_far_from_high is not None:
        checks.append((
            "_dq_farhigh",
            f"距20日高点过远(≤{cfg.forbid_far_from_high:.0%},现役规则禁买下跌途中票)",
            S.forbid_far_from_high(cfg.forbid_far_from_high),
        ))
    if cfg.forbid_new_days is not None:
        checks.append((
            "_dq_new",
            f"次新股(上市不足{cfg.forbid_new_days}自然日,现役规则剔除)",
            S.forbid_new_stock(cfg.forbid_new_days),
        ))
    if cfg.forbid_high_elasticity:
        # ⚠ 现役 v1.3.3 起 `forbid_high_elasticity=False` → 本分支不再产出红灯(拆墙)。
        # **不删该分支**:v1.3 是切换器白名单里的合法回退目标,回退后墙必须立刻回来。
        checks.append((
            "_dq_elastic",
            "高弹题材板块(创业板/科创板,20%涨跌幅易跌停,现役规则风控剔除)",
            S.forbid_high_elasticity(),
        ))
    return checks


__all__ = ["discipline_checks"]
