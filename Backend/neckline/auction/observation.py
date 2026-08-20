"""竞价层的**独立观察池**(2026-08-12 用户裁定 ①;§七 P1-78 的落点)。

🔴 **裁定原文(逐字,⛔ 不许加减)**:

    「`wu.codes` **继续保持最多 29 只,只服务持仓与关注提醒**。竞价**另建独立观察池**,
     按 K8 顺序取数:**同一主驱动 → 同题材方向 → 传统行业**,**完整取样、分批请求,
     不设 29 只上限**。**板块指数优先**;没有指数时使用**至少 3 只**有效对照股的
     **中位数**;不足 3 只返回 `data_insufficient`。**竞价强势股只在该独立观察池内排序,
     并明确标注观察范围,不冒充全市场排名。**」

**它解决的是什么**:V2.4.0 P0 把盘中关注池缩到上界 29 只(退潮判级退役,它专用的两份
测量样本随之删除),而竞价层此前把 `wu.codes` 当作 ①「竞价强势股」与 ② 板块对照股
**两个取样域** —— 29 只里凑够 3 只同行业对照股几乎不可能(§七 P1-78 登记的那条必然连带)。
裁定 ① 的做法是**把两件事彻底分开**:盘中关注池继续小而准(它服务持仓提醒),
竞价层**自己**按 K8 §五-4 的比较域顺序取一份完整的观察域。

🔴 **⛔ 这份池子绝不回接 `wu.codes`** —— 那正是裁定要拆开的东西。结构性保证:
本模块**零 import** `sentinel.universe` 的池子组装函数,产物只交给 `auction/collect.py`
用作**抓取清单的一段**与**两个取样域**;守门单测正反双向锁。

## 三层取数(K8 §五-4 顺序;⛔ 层序不可颠倒)

    ① **同一主驱动的候选成员域** —— 取 D0 **冻结卡**上每名成员的 `core_metrics`,
       当 `comparison_domain == "driver"` 时用它的 `peer_codes`。
       🔴 这是 D0 当时**真正用过的**那个比较域,⛔ 不在竞价层重算一遍
       (重算 = 给 K8 §五-4 造第二份事实源)。
    ② **同题材 / 方向** —— 🔴 **本版结构性缺席**:2026-08-12 用户**裁定 #1** 明写
       「题材域明确记录 `theme_domain_not_implemented`,**⛔ 不得使用 `ths_member`
       参与判定**」,而观察池的成员会进 `rel_to_sector` 的中位数 = **判据输入**。
       故本层**如实记 `theme_domain_not_implemented` 并跳到 ③**,
       ⛔ **一行 `ths_member` 都不许进来**(本仓「概念板块只做展示」那条纪律因此完好)。
       ⚠ 这是**已知的、写在明处的缺口**,不是遗漏 —— 要实现它必须先由用户解禁裁定 #1。
    ③ **传统行业分类** —— `stock_basic.industry`(本仓钉死的行业口径),
       该成员所属行业的**全部**成分股。🔴 **这一层是「完整取样」**:⛔ 不截断、
       ⛔ 不设「取前 N 只」(K8 没给那个数,红线 1)。

## 规模与请求量(⛔ 别拿"怕慢"当截断的理由)

`stock_basic` 现有 111 个行业、中位 23 只 / 均值 53 只 / 最大 347 只。T1/T2 至多 7 篮
× 至多 3 只成员 = 至多 21 只票、至多 21 个不同的行业 —— 上界因此在千位数量级。
抓取按 `data/realtime.py::_CHUNK_SIZE=400` **分批**(⛔ 逐票请求是明令禁止的),
每块两次请求(双源核验)。**实测**(2026-08-13,真源、盘后):400 码 1.5s · 1200 码 1.7s ·
2000 码 2.7s · 3200 码 4.1s —— 相对 9:26—9:29 那个 **180 秒**窗口有约 40 倍余量。
🔴 **⛔ 不许因为这份余量就去发明一个截断 N**:真有一天顶不住,那是**停手挂 §七 请用户拍板**
的事,不是工程侧自己定一个数。

## 「观察范围」必须随产物说出口

`ObservationPool.scope_note` 是**下发文案的单一源**:多少只、来自哪几层、题材层为什么缺席。
🔴 裁定原文「**明确标注观察范围,不冒充全市场排名**」—— ⛔ 不许只写在代码注释里。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.selection.basket_store import BasketRef

logger = logging.getLogger(__name__)

#: 三层的层码(**唯一源**;逐票留痕与文案都读它,⛔ 不在别处抄字面量)。
LAYER_DRIVER = "driver"          # ① 同一主驱动的候选成员域(D0 冻结卡上那一份)
LAYER_THEME = "theme"            # ② 同题材 / 方向 —— 🔴 本版结构性缺席
LAYER_INDUSTRY = "industry"      # ③ 传统行业分类(完整取样)
OBSERVATION_LAYERS: Tuple[str, ...] = (LAYER_DRIVER, LAYER_THEME, LAYER_INDUSTRY)

#: 🔴 题材层缺席的原因码 —— **与 `selection/core_metrics.py` 那一个同名同义**
#: (裁定 #1 的同一件事在选股侧与竞价侧各有一个落点)。⛔ 别改字面量:
#: 按这个串 grep 要能把两侧一起捞出来。
THEME_LAYER_UNIMPLEMENTED = "theme_domain_not_implemented"

#: 整层取不到时的原因码(⛔ 与「取到了、就是空的」分开 —— 系统缺席 ≠ 实质结论)。
POOL_UNAVAILABLE_NO_BASKET = "no_d0_basket"           # D0 一个 T1/T2 篮子都没有
POOL_UNAVAILABLE_INDUSTRY_MAP = "industry_map_unavailable"   # 整张行业表读不到
POOL_UNAVAILABLE_CARD = "basket_card_unavailable"     # 卡读不出(驱动层因此拿不到)


@dataclass(frozen=True)
class ObservationPool:
    """竞价层这一早晨的**独立观察池**(冻结件:构造完就不再变)。

    ⚠ 它只回答「今天在**哪一批票**里看竞价」,**⛔ 不含任何判定**。
    """

    #: 去重后的全部观察码(**确定性顺序**:先篮子成员、再驱动层、再行业层,各层内部升序)。
    codes: Tuple[str, ...] = ()
    #: 篮子成员自己(第 0 层,恒在;它们是被验证的对象,不是"对照")。
    member_codes: Tuple[str, ...] = ()
    #: ① 驱动层贡献的码(不含篮子成员)。
    driver_codes: Tuple[str, ...] = ()
    #: ③ 行业层贡献的码(不含上面两段)。
    industry_codes: Tuple[str, ...] = ()
    #: ② 题材层的状态 —— 🔴 本版恒 `theme_domain_not_implemented`(裁定 #1)。
    theme_layer_status: str = THEME_LAYER_UNIMPLEMENTED
    #: 行业层覆盖到的行业名(升序,供文案与审计)。
    industries: Tuple[str, ...] = ()
    #: 整池/某一层取不到时的原因码(⛔ 空元组 ≠ "都取到了",看 `codes` 才知道)。
    unavailable: Tuple[str, ...] = ()

    @property
    def size(self) -> int:
        return len(self.codes)

    @property
    def scope_note(self) -> str:
        """🔴 **观察范围的自述**(裁定 ① 逐字要求:「明确标注观察范围,不冒充全市场排名」)。

        ⛔ **这段字里不许有 Markdown**:它作为 `String` 下发,客户端 `Text(String)`
        不解析 Markdown,`**…**` 的星号会原样上屏(`CLAUDE.md` 两条守门管的就是这个)。
        """
        if not self.codes:
            why = "、".join(self.unavailable) if self.unavailable else "本次没有可取样的对象"
            return (f"竞价观察范围:本次没有建立观察池({why})—— "
                    f"「竞价强势股」与「板块对照股」两项因此都没有取样面,如实标未取得。")
        bits = [
            f"篮子成员 {len(self.member_codes)} 只",
            f"同一主驱动域 {len(self.driver_codes)} 只",
            f"同行业(完整成分){len(self.industry_codes)} 只,覆盖 {len(self.industries)} 个行业",
        ]
        note = (
            f"竞价观察范围:本次共 {self.size} 只 —— " + "、".join(bits) + "。"
            "它按 K8 的比较域顺序取数(同一主驱动 → 同题材方向 → 传统行业),"
            "与盘中关注池是两份互不相干的样本。"
            "「同题材方向」这一层本版没有实现(系统没有可用于判定的题材数据源),"
            "已如实跳过、直接落到传统行业层。"
            "行业层是该行业的全部成分股,没有截断。"
            "「竞价强势股」只在这个观察池里排序,"
            "它是「观察范围内的强势股」,不是全市场竞价排行。"
        )
        if self.unavailable:
            note += "本次有取不到的层:" + "、".join(self.unavailable) + "。"
        return note

    def to_dict(self) -> Dict[str, Any]:
        """留痕形状(落 `auction_reports` 的机械段 / 进 prompt 摘要)。"""
        return {
            "size": self.size,
            "member_codes": list(self.member_codes),
            "driver_codes": list(self.driver_codes),
            "industry_codes": list(self.industry_codes),
            "industries": list(self.industries),
            "theme_layer_status": self.theme_layer_status,
            "unavailable": list(self.unavailable),
            "scope_note": self.scope_note,
        }


def _driver_peers_from_card(card: Optional[Mapping[str, Any]]) -> List[str]:
    """一张 D0 冻结卡 → ① 驱动层的同域成员码。

    🔴 判据是 `core_metrics.comparison_domain == "driver"` —— **只有那时** `peer_codes`
    才是「同一主驱动的候选成员域」。⚠ 当它是 `industry` 时那份 `peer_codes` 是**行业域**,
    ⛔ 不能算进 ① 层(③ 层会另外完整取一遍行业,重复计一次只会让层次账目失真)。
    ⚠ 老卡(`basket_card_v4` 及更早)没有比较域五字段 → 该篮 ① 层贡献 0 只,
    这是**如实的空**,⛔ 不猜。
    """
    out: List[str] = []
    for m in ((card or {}).get("members") or []):
        cm = m.get("core_metrics")
        if not isinstance(cm, Mapping):
            continue
        if str(cm.get("comparison_domain") or "") != LAYER_DRIVER:
            continue
        for c in (cm.get("peer_codes") or []):
            if c:
                out.append(str(c))
    return out


def build_observation_pool(
    baskets: Sequence[BasketRef],
    *,
    d0_date: date,
    industry_of_all: Mapping[str, str],
    industry_map_available: bool = True,
    db_path: Optional[Path] = None,
    card_loader: Optional[Any] = None,
) -> ObservationPool:
    """按 K8 三层顺序组装竞价层的独立观察池(裁定 ①)。

    `industry_of_all`:**全市场** `ts_code → industry`(`report/industry_strength.
    load_industry_map` 的产物)—— 🔴 行业层要「完整取样」,所以这里要的是全市场那一份,
    ⛔ 不是关注池切片。
    `industry_map_available=False`:整张行业表没读到 = **系统缺席**,如实落
    `industry_map_unavailable`(⛔ 不与「这只票没登记行业」讲成同一句话,P0-39 纪律)。
    `card_loader`:注入点(单测 / 冒烟),缺省 = `basket_store.load_basket_card`。

    **永不抛异常**:任何一层取不到都只是这一层为空 + 一条原因码 ——
    竞价层照常出报告(K8 §二十 的既有诚实降级路径)。
    """
    member_codes: List[str] = []
    for b in baskets:
        for c in b.member_codes:
            if c and c not in member_codes:
                member_codes.append(c)
    member_set = set(member_codes)
    unavailable: List[str] = []
    if not baskets:
        unavailable.append(POOL_UNAVAILABLE_NO_BASKET)

    # —— ① 同一主驱动的候选成员域(读 D0 冻结卡,⛔ 不重算)——————————————————
    loader = card_loader
    if loader is None:
        from neckline.selection.basket_store import load_basket_card as loader  # noqa: N813
    driver_set: set = set()
    card_failed = 0
    for b in baskets:
        try:
            card = loader(b.basket_id, db_path=db_path)
        except Exception:  # noqa: BLE001 —— 可选情报的保险丝(§铁律)
            logger.warning("[auction] 读篮子 %s 的 D0 冻结卡失败,该篮驱动层贡献 0 只",
                           getattr(b, "basket_key", "?"), exc_info=True)
            card_failed += 1
            continue
        if card is None:
            card_failed += 1
            continue
        driver_set.update(_driver_peers_from_card(card))
    if card_failed:
        unavailable.append(POOL_UNAVAILABLE_CARD)
    driver_codes = tuple(sorted(c for c in driver_set if c not in member_set))

    # —— ② 同题材 / 方向:🔴 结构性缺席(裁定 #1)——————————————————————————
    # ⛔ 这里**一行 `ths_member` 都不许出现**。要实现这一层必须先由用户解禁裁定 #1;
    #    在那之前它如实记状态并跳到 ③(与 `selection/core_metrics.py` 同一个原因码)。

    # —— ③ 传统行业分类:**完整取样**(该行业全部成分股,⛔ 不截断)————————————
    industry_codes: Tuple[str, ...] = ()
    industries: Tuple[str, ...] = ()
    if not industry_map_available or not industry_of_all:
        unavailable.append(POOL_UNAVAILABLE_INDUSTRY_MAP)
    else:
        wanted_industries = sorted({
            industry_of_all[c] for c in member_codes
            if c in industry_of_all and industry_of_all[c]
        })
        industries = tuple(wanted_industries)
        want = set(wanted_industries)
        seen = member_set | set(driver_codes)
        industry_codes = tuple(sorted(
            c for c, ind in industry_of_all.items() if ind in want and c not in seen
        ))

    codes = tuple(list(member_codes) + list(driver_codes) + list(industry_codes))
    pool = ObservationPool(
        codes=codes,
        member_codes=tuple(member_codes),
        driver_codes=driver_codes,
        industry_codes=industry_codes,
        theme_layer_status=THEME_LAYER_UNIMPLEMENTED,
        industries=industries,
        unavailable=tuple(dict.fromkeys(unavailable)),
    )
    logger.info(
        "[auction] D0 %s 的竞价独立观察池:%d 只(成员 %d / 驱动域 %d / 行业 %d,"
        "覆盖 %d 个行业;题材层 %s)",
        d0_date, pool.size, len(member_codes), len(driver_codes), len(industry_codes),
        len(industries), THEME_LAYER_UNIMPLEMENTED,
    )
    return pool


__all__ = [
    "LAYER_DRIVER", "LAYER_THEME", "LAYER_INDUSTRY", "OBSERVATION_LAYERS",
    "THEME_LAYER_UNIMPLEMENTED",
    "POOL_UNAVAILABLE_NO_BASKET", "POOL_UNAVAILABLE_INDUSTRY_MAP", "POOL_UNAVAILABLE_CARD",
    "ObservationPool", "build_observation_pool",
]
