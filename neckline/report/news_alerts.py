"""晚间消息面公告扫描(plan §五 v1.3-③-C4)。16:35 报告新增「消息面」节——对
**持仓 + 次级域**(不是全市场)票扫描四类消息:减持 / 立案 / 暴雷 / 监管(枚举码,
⚠ **V2-⑬-11 起次级域为空**〔自选池整链已删,⑭-A 接篮子成员〕→ 实际只扫持仓。
客户端展示层换算,沿 `boardLabel` 先例)。

**数据源侦察结论(2026-07-26,真实 token 活体探活,详见 PROJECT_PLAN.md §九
变更日志「v1.3-③-C4」条与本模块注释——这是后人不必重查的资产)**:

    · **`anns_d`(TuShare 通用公告接口)—— 不可用。** 真实调用返回:
      "抱歉，您没有接口(anns_d)访问权限，权限的具体详情访问：
      https://tushare.pro/document/1?doc_id=108。" 官方文档交叉核实:该接口是
      **独立付费权限**(公告信息单独 1000 元/年,与本项目 600 元档〔6000 积分〕
      的常规积分体系无关,需另外购买),当前未购——与 §3.2「新闻资讯为单独权限
      1000 元/年,未购」是**同一类型的决策口径**(但公告〔anns_d〕与新闻资讯是
      TuShare 两个不同的独立付费产品,此前 §3.2 只记了新闻资讯未购,本次侦察
      补上公告接口同样未购的结论)。旧接口名 `anns` 已废弃(调用报"请指定正确
      的接口名")。
    · **`stk_holdertrade`(股东增减持,结构化)—— 可用!** 真实调用返回数据,
      只需 2000 积分(在 6000 积分档覆盖范围内,非"单独权限"),字段含
      `in_de`(IN增持/DE减持)、`holder_type`(G高管/P个人/C公司)、
      `change_vol`/`change_ratio`/`ann_date` 等结构化字段——**比公告全文扫描
      更适合"减持"这一类**:结构化、无需 LLM 语义抽取、零幻觉风险、免 LLM 调用
      (省成本)。故本模块**「减持」类改用 `stk_holdertrade` 结构化数据,不用
      `anns_d`、也不用 LLM**,`in_de=='DE'` 即减持事件。这是比 plan 原文举例的
      `anns_d` 更优的方案,超出「查 anns_d」字面但同属「数据源侦察」的题中之义。
      **已收进「v1.3 客户端契约清单」的 `newsAlertsScan` 字段**(2026-07-26
      coordinator 复核后拍板,不算可选字段——见下方"没扫到 vs 扫了没有"节)。
    · **立案 / 暴雷 / 监管三类 —— 无任何 TuShare 接口覆盖**(逐一核实:未找到
      "立案调查"/"监管处罚"/"违规处理"专属接口;`anns_d` 本可能间接覆盖但权限
      不可用;`disclosure_date` 是财务预约披露日期,与监管处罚无关,排除)。
      **三类全部走 LLM 联网搜索兜底**(`neckline.llm.news_scan`,复用 `judge.py`
      同一套 provider/降级链姿势,读超时守 90s)。

**架构(硬要求④「不阻断主报告管线」+ §3.4 缺 key 优雅降级)**:
    ① 减持:`_scan_reduction`(TuShare `stk_holdertrade`,结构化,免 LLM)——
       TuShare 无 token / 调用失败 → 该源降级为「未扫描」(`scanned=False`),
       不臆造「没有减持」。
    ② 立案/暴雷/监管:`_scan_llm_categories`(每票一次 LLM 调用,一次问三类,
       不是三次调用——控成本/控时长,见 `llm.news_scan` 模块头);
       `provider=None`(缺 key)→ 整批直接降级「未激活」,不发起任何网络调用,
       同 `judge.py` 姿势。**受墙钟预算约束、持仓优先于自选**,见下方专节。

**"没扫到"(未激活/调用失败,未知态)vs"扫了没有"(确认无此类消息)必须能
区分**(§硬要求,不许静默当成"没有公告")——`NewsAlertsReport.scan_statuses`
逐源记录 `scanned: bool` + `reason`(+ LLM 源额外记 `codes_total`/`codes_failed`/
`codes_skipped`,支持"部分标的失败"/"预算耗尽未扫"的颗粒度),`items` 为空
**不代表**「确认没有」,读者须先看 `scan_statuses` 才能判断空列表的含义。
`scan_statuses` 随该次报告生成落 `reports.news_alerts_scan_json`(同
`intel_json`/`sector_moneyflow_json` 惯例,保证历史报告回放时仍能看到当时的
扫描状态,不只是当次内存态)。**`newsAlertsScan` 已正式收进「v1.3 客户端契约
清单」**(2026-07-26 coordinator 拍板:它是这条硬要求的落地机制,不是锦上添花
的可选字段)。

**LLM 侧墙钟预算 + 持仓优先(2026-07-26 coordinator 必改,生产风险)**:
    · **背景**:持仓 + 自选最多 33 只(≤3 仓 + ≤30 自选),每票一次 LLM 调用最坏
      耗时 = `read_timeout(90s) × max_attempts(3)` = 270s;33 只全扫最坏墙钟
      **接近一小时**,而 16:35 报告管线里候选审判(前 10 只)+ 自选体检(变化/
      pinned 子集)也在串行调 LLM——叠加起来足以把整份报告拖到深夜。项目在 v1
      上线首日已被 LLM 超时咬过一次(见项目 CLAUDE.md「带联网搜索的 LLM 调用
      不能沿用短读超时」),这里必须反过来守住「总耗时不能失控」这条线,不能让
      同一类问题以另一种形态复发。
    · **`_LLM_SCAN_BUDGET_SECONDS`(墙钟预算,命名常量,启发式估算,非实测,
      待实盘校准后调整——同 §2.4 情绪仪表盘阈值"未回测,靠实盘归因迭代"的诚实
      标注先例)**:单轮扫描的总墙钟预算,循环前记录起始时刻,**每次发起下一票
      调用前**检查已耗时是否超预算,超了就停止扫描剩余标的(不掐断正在进行中的
      单次调用——`scan_news_for_code` 本身是同步阻塞调用,没有可中途打断的钩子,
      故最坏情况下最后一次调用仍可能让总耗时略超预算,可接受)。
    · **持仓优先于次级域(硬要求,写死 + 单测锁死)**:`build_news_alerts` 签名把
      `position_codes`/`secondary_codes` **分开传入**(不是揉成一个已去重列表
      再指望调用方保证顺序)——内部按"先持仓、后次级"拼接 + 去重(同码优先保留
      持仓身份),预算不够时被跳过的必然是排在后面的次级标的,不会跳过持仓。
      **理由(持仓有真金风险,次级域只是关注)**:与 §2.1 纪律"持仓优先"的精神一致。
      ⚠ **V2-⑬-11 登记**:次级域原本 = 自选池;自选池整链已按裁定 #9-a 删除,
      `report/pipeline.py` 现传空列表 → 本模块的隔日轮扫机制**本版恒不触发**。
      机制与常量**刻意不拆**:⑭-A 把「篮子成员」接进次级域时原样复用(那时轮扫
      重新有意义),现在拆了到时要重写一遍。
    · **串行 + 预算封顶,不做并发(理由写死,供后续如需改并发时对照)**:
      (a) 本项目 LLM/HTTP 层(`openai_compat.py`)与整条报告管线全同步阻塞,
      引入并发(线程/asyncio)是本模块局部的架构突变,会给一个子模块单独引入
      新的并发安全面(`httpx.Client` 线程安全性、`MockTransport` 测试桩的并发
      语义)而不是复用既有模式;(b) GLM/Kimi 两家供应商的真实分钟级限频未经
      验证(不像 TuShare 有文档化的 500次/分钟,本项目对 GLM/Kimi 限频没有任何
      实测或文档依据),并发扫描有触发限频连锁失败的未知风险;(c) 串行 + 预算
      封顶已经完整解决"总耗时不失控"这个真实问题,不需要用增加复杂度换取"扫完
      更多标的"这个次要目标(§硬要求原话"别把降级链搞复杂")。**如果实盘发现
      预算内经常扫不完自选池、需要扫更多标的**,下一步应先验证供应商真实限频、
      再评估并发,而不是本次顺手做。

**自选隔日轮扫(v1.4-⑥-B,§七 P1-5)**:

    · **病**:3 持仓 + 16 自选 = 19 标的,2026-07-27 那次 300s 预算耗尽、**8 只自选
      未扫**(由 `codes_skipped` 如实披露,没有静默);自选只会越来越多。
    · **选型与依据(诚实标注,勿当成实测结论引用)**:plan §五-⑥-B 给的是二选一
      ——「限频允许 → 受控并发 + 预算抬到 600s」 vs 「限频不允许 → 自选隔日轮扫」,
      并要求**先实测供应商限频再选**。**施工环境(2026-07-29)本地无任何可用 GLM
      key**(`.env` 只有 `TUSHARE_TOKEN`,环境变量与 `app_settings.llm_api_key` 均空)
      → **限频无法实测**。按"不许拍脑袋开并发"的原则取**保守分支 = 自选隔日轮扫**,
      并发路**留待有 key 时实测再评估**(届时对照上面 (a)(b)(c) 三条理由逐条回答)。
      连带取舍:**`LLM_SCAN_BUDGET_SECONDS` 维持 300s 不抬** —— 轮扫把单次待扫量
      从 19 压到 ~11,预算已经够用;抬预算会同时抬高 16:35 报告的总墙钟与
      `neckline-report.service MemoryMax` 的压力(§七 P4-16),没必要为一个已经
      被轮扫解决的问题付这个账。
    · **规则**:**持仓每日必扫**(有真金风险,绝不轮空);**自选**按 `ts_code` 的
      **稳定哈希**(`zlib.crc32`,**不是 Python `hash()`** —— 后者带进程盐、
      `PYTHONHASHSEED` 一变分组就漂,历史报告无法复现)分 A/B 两组,按
      `trade_date.toordinal()` 的奇偶交替扫。**分组与日期都是纯函数**:同一天重跑
      报告 / 历史回放,轮到的组恒定,不依赖任何库里的轮转计数器。
    · **诚实披露**:`NewsAlertScanStatus` 新增 `rotation_group`(本次扫的是哪一组)
      与 `codes_rotation_deferred`(本日轮空的自选数)。**`codes_rotation_deferred`
      与 `codes_skipped`(预算耗尽没发起)、`codes_failed`(发起了但失败)、
      `codes_no_search`(搜索 0 命中)四者语义各不相同,不许合并**——"今天轮不到"
      与"今天没扫完"是两件事,读者据此才知道空 `items` 的含义。
    · **减持类(TuShare)不参与轮扫**:它是一次区间批量调用、免 LLM、成本与标的数
      无关,轮扫它只会白白降低覆盖 —— **轮扫是 LLM 侧的预算措施,不是全模块策略**。
    · **相邻交易日跨偶数个自然日时会连续两天扫同一组**(如节假日把 Fri→Wed 拉成
      5 天是奇数、但 Thu→Mon 之类跨 4 天是偶数)。不修:修它要么引入库里的轮转
      状态(历史回放不可复现)、要么查交易日历(为一个"偶尔多扫一次同一组"的小事
      引入日历依赖)。**如实披露在 `rotation_group` 里,不假装严格交替。**

**减持类跨日事件去重(2026-07-26 coordinator 必改,不接受"同一简化"现状)**:
    · **问题**:原实现 `news_alerts.trade_date` = 扫描/报告日,同一笔减持公告在
      连续多天的扫描窗口(`_REDUCTION_LOOKBACK_DAYS`)内会被反复"发现"、每天各
      生成一条新记录——用户会在一周内的每份报告里看到同一句话,训练用户忽略
      这一节,等于没做。
    · **修复**:`stk_holdertrade` 有 `ann_date`(事件/公告本身发生日),据此做
      **跨日事件级去重**——同一 `(ts_code, event_date, event_key)` 三元组只在
      **第一次被扫描到并落库的那份报告**里出现,此后的报告即使仍在回看窗口内
      重新扫到同一条 TuShare 原始行,也不再重复生成 `NewsAlertItem`(见
      `_scan_reduction` 里的 `news_alerts_store.load_seen_event_keys` 查询)。
      `event_key` = `holder_name|change_vol|change_ratio` 的拼接(同一持股人
      同一笔变动 = 同一事件;不同持股人 / 不同变动量 = 不同事件,各自独立记录,
      不因为"合并展示"互相覆盖丢信息——`news_alerts` 表因此从"一票一行"改为
      "**一事件一行**",`UNIQUE` 约束与 `event_key` 一起见 `neckline.db`)。
    · **`event_date` 与 `trade_date` 两列并存、职责不同**(落库时都要存,不是
      只存扫描日):`trade_date` = 首次记录该事件的报告日(审计留痕 + 同日重跑
      幂等的判据一部分);`event_date` = 事件本身的公告日(跨日去重的判据、
      历史回放时"这事哪天真实发生"的展示依据)——同 CLAUDE.md 记的"审计时间戳
      + 独立消费标记不用一个字段身兼两职"教训,这里是同一原则的应用。
    · **LLM 侧维持现状,不做跨日去重**(优先不漏报——立案/暴雷/监管这类事件本
      身可能是持续状态,连续扫到、连续提醒未必是错误;而且 LLM 自由文本没有
      可靠的结构化事件日,勉强瞎凑一个 `event_key` 反而可能把两个不同的事情误
      判成同一个、错误吞掉真实的新进展)。**LLM 来源的 item 恒 `event_date=None`
      /`event_key=""`,天然不参与去重查询**;差异已写进 `EVIDENCE_NOTE`,让
      读者知道"这一类可能连续几天重复出现,减持类不会"。

**落库**:`items` 落独立 `news_alerts` 表(`neckline.db`)。存取见
`report/news_alerts_store.py`。

**系统永不代交易动作**(§3.8):本模块只扫描/归类/展示,不触发任何下单/撤单。
"""

from __future__ import annotations

import logging
import time
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import polars as pl

from neckline.data.tushare_client import ts_stk_holdertrade
from neckline.llm.base import LLMProvider
from neckline.llm.news_scan import scan_news_for_code
from neckline.report import news_alerts_store

logger = logging.getLogger(__name__)


class NewsCategory:
    """四类枚举码(服务端字面英文,客户端展示层换算,沿 `data.board.Board` /
    `boardLabel` 先例——不在服务端存中文)。"""
    REDUCTION = "REDUCTION"            # 减持(TuShare stk_holdertrade,结构化)
    INVESTIGATION = "INVESTIGATION"    # 立案(LLM)
    BLOWUP = "BLOWUP"                  # 暴雷(LLM)
    REGULATORY = "REGULATORY"          # 监管(LLM)


ALL_CATEGORIES = (
    NewsCategory.REDUCTION, NewsCategory.INVESTIGATION, NewsCategory.BLOWUP, NewsCategory.REGULATORY,
)

SOURCE_TUSHARE_HOLDERTRADE = "tushare_holdertrade"
SOURCE_LLM_PREFIX = "llm"   # 实际值 f"llm_{provider_name}",如 "llm_glm"

# 减持扫描回看窗口(自然日,非交易日——桥接长假,如国庆 7 天/春节约 7-10 天)。
_REDUCTION_LOOKBACK_DAYS = 10

_HOLDER_TYPE_LABEL: Dict[str, str] = {"G": "高管", "P": "个人股东", "C": "机构股东"}

# LLM 侧扫描墙钟预算(秒;命名常量,启发式估算见模块头「LLM 侧墙钟预算」节,
# 待实盘校准)。5 分钟量级:约等于 1-2 只标的撞到最坏超时+重试(270s/只)仍能
# 收尾,不至于让整份 16:35 报告被消息面拖垮——这是"扫描阶段本身的止损线",
# 不是"保证扫完所有标的"的承诺。
# **v1.4-⑥-B 维持 300s 不抬**(理由见模块头「自选隔日轮扫」节:轮扫已把单次待扫量
# 压下来,抬预算会同时抬报告总墙钟与 MemoryMax 压力〔§七 P4-16〕)。
LLM_SCAN_BUDGET_SECONDS = 300.0

# 自选轮扫分组标签(v1.4-⑥-B)。两组,按交易日奇偶交替;取值进 `rotation_group` 如实下发。
ROTATION_GROUPS = ("A", "B")

EVIDENCE_NOTE = (
    "减持:TuShare stk_holdertrade 结构化数据(股东增减持公告口径,强证据),"
    "按 (股票,公告日,持股人+变动量) 事件级跨日去重——同一笔减持只在最先扫到它的"
    "那份报告里出现一次;"
    "立案/暴雷/监管:LLM 联网搜索兜底(TuShare 600 元档无结构化接口覆盖,详见模块"
    "docstring 数据源侦察结论),受限于搜索命中与模型解读,请以原文公告为准,且"
    "不做跨日去重(可能连续几天重复出现,与减持类不同)。"
    "LLM 侧扫描受墙钟预算约束、持仓优先于次级域,预算耗尽时被跳过的是次级域标的。"
)


@dataclass
class NewsAlertItem:
    ts_code: str
    name: str
    category: str      # NewsCategory 值
    summary: str
    source: str         # tushare_holdertrade | llm_<provider>
    event_date: Optional[str] = None   # 'YYYYMMDD' 事件本身发生日(REDUCTION=ann_date;LLM 恒 None)
    event_key: str = ""                 # 跨日去重键(REDUCTION 类非空;LLM 类恒空串,不参与去重)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "code": self.ts_code, "name": self.name, "category": self.category,
            "summary": self.summary, "source": self.source,
        }


@dataclass
class NewsAlertScanStatus:
    """扫描来源级状态(硬要求「没扫到 vs 扫了没有必须能区分开」的落地点,已收进
    「v1.3 客户端契约清单」)。"""
    source: str          # tushare_holdertrade | llm
    scanned: bool         # 是否真正执行了扫描(而非因缺 key/无 token 整体跳过)
    reason: str = ""      # 未扫描 / 部分失败 / 预算耗尽的原因;全部正常时空串
    codes_total: int = 0  # 应扫描的标的数(仅 source=llm 有意义;tushare 是区间批量调用记 0)
    codes_failed: int = 0 # 调用失败/格式解析失败的标的数(仅 source=llm)
    codes_skipped: int = 0  # 墙钟预算耗尽、根本没发起调用就跳过的标的数(仅 source=llm)
    # v1.3.4:调用成功、但联网搜索一条都没回来的标的数(仅 source=llm)。这类标的的
    # 「未发现三类消息」是**模型凭训练数据说的**,不是搜索证实的——与 codes_failed
    # (压根没答上来)、codes_skipped(没发起调用)同属「扫了 vs 没扫」的分辨维度,
    # 三者语义不同不可合并。0 命中为何会静默发生见 `llm.base.search_coverage_line`。
    codes_no_search: int = 0
    # v1.4-⑥-B 自选隔日轮扫(仅 source=llm):本次扫的是哪一组自选(`ROTATION_GROUPS`)
    # + 本日**轮空**(压根不在本次名单里)的自选数。**与 codes_skipped 语义不同不可合并**:
    # 前者是"今天轮不到它",后者是"排进名单了但预算耗尽没发起"。持仓不参与轮扫,恒被扫。
    rotation_group: str = ""
    codes_rotation_deferred: int = 0

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source, "scanned": self.scanned, "reason": self.reason,
            "codesTotal": self.codes_total, "codesFailed": self.codes_failed,
            "codesSkipped": self.codes_skipped, "codesNoSearch": self.codes_no_search,
            "rotationGroup": self.rotation_group,
            "codesRotationDeferred": self.codes_rotation_deferred,
        }


@dataclass
class NewsAlertsReport:
    trade_date: date
    items: List[NewsAlertItem] = field(default_factory=list)
    scan_statuses: List[NewsAlertScanStatus] = field(default_factory=list)
    evidence_note: str = EVIDENCE_NOTE

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "tradeDate": self.trade_date.isoformat(),
            "items": [i.to_public_dict() for i in self.items],
            "scanStatuses": [s.to_public_dict() for s in self.scan_statuses],
            "evidenceNote": self.evidence_note,
        }

    def scan_statuses_public(self) -> List[Dict[str, Any]]:
        """`pipeline.py` 落 `reports.news_alerts_scan_json` 用(只落扫描状态,
        不落 `items`——后者已在独立 `news_alerts` 表,不重复存两份)。"""
        return [s.to_public_dict() for s in self.scan_statuses]


def empty_news_alerts_report(trade_date: date, reason: str) -> NewsAlertsReport:
    """`pipeline.py` 兜底工厂(同 `intel.empty_intel_report`/`sector_moneyflow.
    empty_sector_moneyflow_report` 姿势,外层保险丝——本模块内部两个子扫描各自
    已有降级,这里只应对编排逻辑自身出乎意料的异常)。两源均标 `scanned=False`,
    不假装扫描过。"""
    return NewsAlertsReport(
        trade_date=trade_date,
        scan_statuses=[
            NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=False, reason=reason),
            NewsAlertScanStatus(source=SOURCE_LLM_PREFIX, scanned=False, reason=reason),
        ],
    )


def secondary_rotation_group(ts_code: str) -> str:
    """某只**次级域**票(V2-⑬-11 前 = 自选票)固定属于哪一组(v1.4-⑥-B)。**稳定哈希 `zlib.crc32`**,不是内置
    `hash()`(带进程盐,`PYTHONHASHSEED` 一变分组就漂 → 历史报告的 `rotationGroup`
    无法复现)。纯函数、无状态。"""
    return ROTATION_GROUPS[zlib.crc32(ts_code.encode("utf-8")) % len(ROTATION_GROUPS)]


def rotation_group_for_date(trade_date: date) -> str:
    """某个报告日轮到扫哪一组次级域标的(v1.4-⑥-B)。按 `toordinal()` 奇偶交替 —— **纯日期
    函数**:同一天重跑 / 历史回放恒定,不依赖库里的轮转计数器(计数器会让"重跑一次
    报告"就把轮转推进一格,历史不可复现)。相邻交易日跨偶数自然日时会连续两天同组,
    如实披露不假装严格交替(见模块头)。"""
    return ROTATION_GROUPS[trade_date.toordinal() % len(ROTATION_GROUPS)]


def _rotated_llm_targets(
    position_codes: Sequence[Tuple[str, str]], secondary_codes: Sequence[Tuple[str, str]],
    trade_date: date,
) -> Tuple[List[Tuple[str, str]], str, int]:
    """LLM 侧本次真正要扫的名单(v1.4-⑥-B)= **全部持仓** + **本日轮到那一组的次级域**,
    仍按「持仓优先、次级靠后」排序。返回 `(名单, 本日组标签, 轮空的次级标的数)`。

    **同时是持仓的次级标的不参与轮转**(它在持仓侧天天被扫,轮空计数里也不算它一份——
    否则会报出一个"其实每天都扫了"的假轮空数)。"""
    group = rotation_group_for_date(trade_date)
    position_set = {c for c, _ in position_codes}
    picked: List[Tuple[str, str]] = []
    deferred = 0
    seen: Set[str] = set()
    for code, name in secondary_codes:
        if code in position_set or code in seen:
            continue
        seen.add(code)
        if secondary_rotation_group(code) == group:
            picked.append((code, name))
        else:
            deferred += 1
    return _priority_ordered_unique(position_codes, picked), group, deferred


def _priority_ordered_unique(
    position_codes: Sequence[Tuple[str, str]], secondary_codes: Sequence[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """持仓在前、次级域在后拼接 + 去重(同码优先保留持仓那份 name)——供 LLM 侧
    按此顺序扫描,预算耗尽时天然先保证持仓、后牺牲次级(§硬要求,见模块头)。"""
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []
    for code, name in list(position_codes) + list(secondary_codes):
        if code in seen:
            continue
        seen.add(code)
        out.append((code, name))
    return out


# —— ① 减持(TuShare stk_holdertrade,结构化,免 LLM,事件级跨日去重)——————————

def _reduction_event_key(r: Dict[str, Any]) -> str:
    """事件去重键的组成部分之一(另需配合 ts_code + event_date,一起在
    `news_alerts_store.load_seen_event_keys` 里做匹配)。同一持股人同一笔变动
    (变动股数 + 变动比例)视为同一事件;不同持股人 / 不同变动量 = 不同事件,
    各自独立记录(§硬要求:去重不能吞掉真实发生的多笔独立事件)。"""
    holder = r.get("holder_name") or ""
    vol = r.get("change_vol")
    ratio = r.get("change_ratio")
    vol_s = f"{vol:.4f}" if isinstance(vol, (int, float)) else "NA"
    ratio_s = f"{ratio:.4f}" if isinstance(ratio, (int, float)) else "NA"
    return f"{holder}|{vol_s}|{ratio_s}"


def _format_reduction_event(r: Dict[str, Any]) -> str:
    holder = r.get("holder_name") or "未知股东"
    htype = _HOLDER_TYPE_LABEL.get(r.get("holder_type"), r.get("holder_type") or "")
    vol = r.get("change_vol")
    ratio = r.get("change_ratio")
    ann = r.get("ann_date") or ""
    vol_txt = f"{vol:,.0f} 股" if isinstance(vol, (int, float)) else "股数未知"
    ratio_txt = f"占总股本 {ratio:.2f}%" if isinstance(ratio, (int, float)) else "占比未知"
    return f"{holder}({htype})减持 {vol_txt},{ratio_txt},公告日 {ann}"


def _scan_reduction(
    trade_date: date, codes: Set[str], names: Dict[str, str], db_path: Optional[Path],
) -> Tuple[List[NewsAlertItem], NewsAlertScanStatus]:
    start = trade_date - timedelta(days=_REDUCTION_LOOKBACK_DAYS)
    res = ts_stk_holdertrade(start.strftime("%Y%m%d"), trade_date.strftime("%Y%m%d"))
    if not res.ok:
        return [], NewsAlertScanStatus(
            source=SOURCE_TUSHARE_HOLDERTRADE, scanned=False,
            reason=f"TuShare stk_holdertrade 调用失败:{res.reason}",
        )
    if res.data is None or len(res.data) == 0:
        return [], NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True)

    df = pl.from_pandas(res.data)
    if df.is_empty() or "ts_code" not in df.columns or "in_de" not in df.columns:
        return [], NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True)

    hits = df.filter((pl.col("in_de") == "DE") & pl.col("ts_code").is_in(list(codes)))
    if hits.is_empty():
        return [], NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True)

    # 去重 TuShare 自身重复披露行(实测发现:同一笔披露可原样返回两次,见模块头
    # 数据源侦察节),按完整字段去重,不靠事件键(事件键只取 holder+vol+ratio,
    # 万一同一持股人同一变动量真被拆成两条不同 ann_date 的行,不该被这一步吞掉)。
    seen_this_call: set = set()
    candidates: List[Tuple[str, str, str, Dict[str, Any]]] = []   # (ts_code, event_date, event_key, row)
    for r in hits.iter_rows(named=True):
        ekey = _reduction_event_key(r)
        ann_date = str(r.get("ann_date") or "")
        full_key = (r["ts_code"], r.get("holder_name"), ann_date, r.get("change_vol"), r.get("change_ratio"))
        if full_key in seen_this_call:
            continue
        seen_this_call.add(full_key)
        candidates.append((r["ts_code"], ann_date, ekey, r))

    # 跨日事件级去重(§硬要求,2026-07-26 必改2):已经在更早的报告里出现过的
    # 事件不再重复出现——查已落库的 (ts_code, event_date, event_key) 集合,过滤掉。
    seen_before = news_alerts_store.load_seen_event_keys(NewsCategory.REDUCTION, db_path=db_path)

    items: List[NewsAlertItem] = []
    for ts_code, ann_date, ekey, r in candidates:
        if (ts_code, ann_date, ekey) in seen_before:
            continue
        items.append(NewsAlertItem(
            ts_code=ts_code, name=names.get(ts_code, ts_code), category=NewsCategory.REDUCTION,
            summary=_format_reduction_event(r), source=SOURCE_TUSHARE_HOLDERTRADE,
            event_date=ann_date or None, event_key=ekey,
        ))
    return items, NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True)


# —— ② 立案/暴雷/监管(LLM,一次问三类,墙钟预算 + 持仓优先)——————————————————

def _scan_llm_categories(
    codes: Sequence[Tuple[str, str]],
    *, provider: Optional[LLMProvider], transport: Optional[Any] = None,
    budget_seconds: float = LLM_SCAN_BUDGET_SECONDS,
    rotation_group: str = "", rotation_deferred: int = 0,
) -> Tuple[List[NewsAlertItem], NewsAlertScanStatus]:
    """`codes` 须已按「持仓优先、自选靠后」排好序、且已按本日轮扫组筛过(见
    `_rotated_llm_targets`,由 `build_news_alerts` 负责)——本函数只管按序扫描 + 预算
    封顶,**不重排、不再筛**。`rotation_group`/`rotation_deferred` 只做如实透传披露。"""
    rot = {"rotation_group": rotation_group, "codes_rotation_deferred": rotation_deferred}
    rot_reason = (
        f"自选隔日轮扫:本次扫的是 {rotation_group} 组,{rotation_deferred} 只自选本日轮空"
        f"(明日轮到,不代表确认无消息;持仓每日必扫,不参与轮扫)。"
        if rotation_deferred else ""
    )
    if provider is None:
        return [], NewsAlertScanStatus(
            source=SOURCE_LLM_PREFIX, scanned=False,
            reason="未配置 LLM_PROVIDER/LLM_API_KEY(缺 key,全部跳过,未发起任何网络调用)。",
            codes_total=len(codes), **rot,
        )
    if not codes:
        return [], NewsAlertScanStatus(
            source=SOURCE_LLM_PREFIX, scanned=True, codes_total=0, reason=rot_reason, **rot,
        )

    items: List[NewsAlertItem] = []
    failed = 0
    skipped = 0
    no_search = 0
    start = time.monotonic()
    scanned_n = 0
    for ts_code, name in codes:
        if time.monotonic() - start >= budget_seconds:
            skipped = len(codes) - scanned_n
            logger.warning(
                "消息面扫描(C4)LLM 侧墙钟预算耗尽(%.0fs),跳过剩余 %d 只"
                "(按持仓优先/自选靠后顺序,被跳过的是排序靠后的自选标的)",
                budget_seconds, skipped,
            )
            break
        scanned_n += 1
        r = scan_news_for_code(ts_code, name, provider=provider, transport=transport)
        if r.degraded:
            failed += 1
            logger.warning("消息面扫描(C4)LLM [%s %s] 降级:%s", ts_code, name, r.degrade_reason)
            continue
        if not r.search_hits:
            no_search += 1
        for category, summary in r.hits:
            items.append(NewsAlertItem(
                ts_code=ts_code, name=name, category=category, summary=summary,
                source=f"{SOURCE_LLM_PREFIX}_{r.provider}",
            ))

    reason_parts: List[str] = []
    if rot_reason:
        reason_parts.append(rot_reason)
    if failed:
        reason_parts.append(
            f"{failed}/{len(codes)} 只标的 LLM 调用失败或未按格式输出,已跳过"
            f"(不计入「确认无消息」,建议人工复核)。"
        )
    if skipped:
        reason_parts.append(
            f"墙钟预算({budget_seconds:.0f}秒)耗尽,{skipped} 只标的未及扫描"
            f"(持仓优先已扫完,被跳过的是排序靠后的自选标的,不代表确认无消息)。"
        )
    if no_search:
        reason_parts.append(
            f"{no_search}/{len(codes)} 只标的联网搜索命中 0 条,其「未发现三类消息」"
            f"是模型凭训练数据说的、非搜索证实(不等于确认无消息,建议人工复核)。"
        )
    return items, NewsAlertScanStatus(
        source=SOURCE_LLM_PREFIX, scanned=True, reason="".join(reason_parts),
        codes_total=len(codes), codes_failed=failed, codes_skipped=skipped,
        codes_no_search=no_search, **rot,
    )


# —— 主入口 ——————————————————————————————————————————————————————————————

def build_news_alerts(
    trade_date: date,
    position_codes: Sequence[Tuple[str, str]],
    secondary_codes: Sequence[Tuple[str, str]],
    *,
    provider: Optional[LLMProvider] = None,
    transport: Optional[Any] = None,
    db_path: Optional[Path] = None,
    llm_budget_seconds: float = LLM_SCAN_BUDGET_SECONDS,
) -> NewsAlertsReport:
    """消息面扫描 I/O 入口(角色对应 `intel.compute_intel`/`sector_moneyflow.
    compute_sector_moneyflow`)。**`position_codes`/`secondary_codes` 分开传入
    (不是揉成一个列表)**——LLM 侧按「持仓优先、自选靠后」的顺序扫描,预算不够
    时牺牲的必然是自选(§硬要求,见模块头「LLM 侧墙钟预算 + 持仓优先」节)。
    两者均为空 → 直接空报告,零 I/O(两源均标 `scanned=True` 空操作——不是
    "缺 key"式的未扫描,是"没有扫描对象"这个更平凡的空态)。

    **v1.4-⑥-B 自选隔日轮扫**:减持类(TuShare 批量、免 LLM)仍覆盖**全量**持仓+自选;
    LLM 侧只扫「全部持仓 + 本日轮到那组自选」(`_rotated_llm_targets`),轮空数与组标签
    随 `newsAlertsScan` 如实下发。见模块头「自选隔日轮扫」节。

    `db_path`:减持类跨日事件去重要查 `news_alerts` 表历史记录,`None` → 走
    `settings.db_path`(生产默认);单测传隔离库路径。"""
    ordered = _priority_ordered_unique(position_codes, secondary_codes)
    if not ordered:
        return NewsAlertsReport(
            trade_date=trade_date,
            scan_statuses=[
                NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True),
                NewsAlertScanStatus(source=SOURCE_LLM_PREFIX, scanned=True, codes_total=0,
                                    rotation_group=rotation_group_for_date(trade_date)),
            ],
        )
    names = {c: n for c, n in ordered}
    code_set = set(names)

    reduction_items, reduction_status = _scan_reduction(trade_date, code_set, names, db_path)
    llm_targets, rot_group, rot_deferred = _rotated_llm_targets(
        position_codes, secondary_codes, trade_date,
    )
    llm_items, llm_status = _scan_llm_categories(
        llm_targets, provider=provider, transport=transport, budget_seconds=llm_budget_seconds,
        rotation_group=rot_group, rotation_deferred=rot_deferred,
    )

    return NewsAlertsReport(
        trade_date=trade_date,
        items=reduction_items + llm_items,
        scan_statuses=[reduction_status, llm_status],
    )


__all__ = [
    "NewsCategory",
    "ALL_CATEGORIES",
    "SOURCE_TUSHARE_HOLDERTRADE",
    "SOURCE_LLM_PREFIX",
    "EVIDENCE_NOTE",
    "LLM_SCAN_BUDGET_SECONDS",
    "ROTATION_GROUPS",
    "secondary_rotation_group",
    "rotation_group_for_date",
    "NewsAlertItem",
    "NewsAlertScanStatus",
    "NewsAlertsReport",
    "empty_news_alerts_report",
    "build_news_alerts",
]
