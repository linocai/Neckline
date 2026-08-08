"""任务 → Provider 路由(plan §五 V2-② / §3.10-B)。**任务常量单一源**——新增任何
消费某个 LLM 任务的模块,一律从本文件 import 任务常量,不许各处散抄字符串字面量。

默认双 Agent 分工(裁定 #2):检索类任务缺显式路由时,优先落到「带联网搜索能力
且已启用」的 provider;其余任务缺显式路由时回退 `app_settings.llm_default_provider`
(用户预期填一个纯推理 provider,如 DeepSeek,但本模块不强制校验这一点——自填制
下"谁是推理 provider"是配置事实,不是代码断言)。

本模块**不碰 DB / 不做 I/O**——`resolve_task_provider_name()` 是纯函数,输入已经
是调用方(`neckline.llm.factory`)查好的 `routes`/`default_provider`/`rows`,方便
不建库直接单测四态解析逻辑。**不 import `neckline.settings_store`**(避免与
`settings_store.set_llm_routes` 反向 import 本模块的 `ALL_TASKS` 校验形成循环)。
"""

from __future__ import annotations

from typing import Dict, Optional, Protocol, Sequence

# —— 任务常量(plan §五 V2-② 原文九项,逐字对应)——————————————————————————
TASK_DRIVER_SEARCH = "driver_search"    # 驱动证据检索(①市场扫描→②聚合层用)
TASK_NEWS_SCAN = "news_scan"            # 消息面扫描(立案/暴雷/监管三类)
TASK_BASKET_REASON = "basket_reason"    # 篮子逻辑与角色比较(②聚合层)
TASK_TIER_RANK = "tier_rank"            # 同档排序理由(③Tier 分层引擎)
TASK_SCRIPT = "script"                  # 明早证伪剧本
TASK_REVIEW = "review"                  # 盘后复盘解释
TASK_PROFILE = "profile"                # 画像总结
TASK_NL_ALERT = "nl_alert"              # 自然语言临时提醒解析

ALL_TASKS = (
    TASK_DRIVER_SEARCH,
    TASK_NEWS_SCAN,
    TASK_BASKET_REASON,
    TASK_TIER_RANK,
    TASK_SCRIPT,
    TASK_REVIEW,
    TASK_PROFILE,
    TASK_NL_ALERT,
)

# 默认路由的「检索类」集合(plan 原文明确点名 driver_search/news_scan 两项)。
# ⚠ **V2.1-① 起 `TASK_INQUIRY` 已随问询台整链退役从本元组移除**——它此前是
# builder 推断收录(问询台 `provider.chat(enable_search=True, ...)` 需要搜索能力
# 的 provider),现在连同问询台主体一起消失,不留影子档;`ALL_TASKS`/`__all__` 三处
# 同步摘除,反向 hasattr 守门见 `tests/test_v21_retirement_guard.py`。
DEFAULT_SEARCH_TASKS = (TASK_DRIVER_SEARCH, TASK_NEWS_SCAN)

# —— 大上下文推理:流式 + chunk 间隔超时(§七 P0-44,2026-08-05 晚间生产实打)——
# **P0-40 的病灶**:`OpenAICompatProvider.read_timeout=90.0` 那个数字是给**带联网
# 搜索的单次审判/问询**调的(v1.3.4 实测 30-60s+)。V2 的**推理类**是另一种工作量:
# ⑤ 的篮子聚合一次把 **20 颗种子 + 每颗的成员机械数据**塞进同一个 prompt,再要一份
# 结构化 JSON 出来 —— 08-05 中午 3/3 次恰好 90s ReadTimeout,**确定性超长、不是网络
# 抖动**。P0-40 把它抬到 240s,当天中午实测该调用 **173s**,通过。
#
# **P0-44 = 同一个病当晚复发,证明抬数字这条路本身是错的**:当晚 16:35 生产链
# **3/3 次精确各花 240s**(16:51:53/16:55:53/16:59:53)—— 晚高峰 GLM 吐字慢于中午,
# 240s 照样不够。**根子在于"整段生成必须在 X 秒内回完"这个判据要求我们提前猜准一个
# 与上游吞吐挂钩的数字,而那个数字每天都不一样**;再抬到 480 只是把下一次翻车推迟。
#
# **根治 = 换判据**:大上下文推理改**流式**(`stream: true`)。httpx 的 read 超时天然
# 作用在每次 socket 读上,于是它从「整段墙钟」变成「**chunk 与 chunk 之间**最多静默
# 多久」—— 判「还在不在吐字」而不是「一共要吐多久」,与吞吐无关,**不需要猜**。
# 生成多长都合法;真死了(90s 一个字都没有)照样快速掐断,短超时的原始价值没丢。
#
# **⛔ 检索类不开流式**:GLM 的 `web_search` tools 协议与流式的组合本项目从未验证过
# (v1.3.4 案底:不被上游认识的组合会 `ok=True` **静默返 0 条**),不拿生产赌。它们
# 维持非流式 + 有实测背书的 90s。
#
# **为什么落在 `factory.get_provider(task)` 而不是 `chat()` 加参数**:后者要改每
# 一个调用点(⑤⑥⑦⑨ 五处 + 未来的),漏一个就退回旧行为且**看不出来**;工厂是所有
# provider 的唯一出生地,按 task 分级只需一处、天然全覆盖。⚠ 代价是**直接 new
# 出来的 provider 不受影响**(单测替身、`providers/{glm,kimi}.py` 参考实现)——
# 这正是我们要的:类属性默认值(非流式 / 90.0)保持不变,既有行为逐字节不变。
#
# **预算与 unit 超时的账**(改这两个数字前先重算一遍,守门单测钉着):
#   · 检索账 20min(`SEARCH_BUDGET_SECONDS`)+ 一组非流式超时溢出(90s × 3 = 4.5min)
#   · 推理账 30min(`REASON_BUDGET_SECONDS`)+ 最后一次调用的整段生成溢出
#   「一组溢出」= 预算是**发起调用前**检查的,最后一次调用可以整组超时后才发现账空。
#   ⚠ **流式下单次调用的墙钟没有固定上限**(生成多长都合法),这是刻意的 —— 也正是
#   本条要治的东西。它由三层兜住:① chunk 间隔 90s(卡死立刻掐)② 预算账如实记录
#   实际耗时、下一次调用发起前就会判空 ③ `TimeoutStartSec` 外层封顶。按实测(中午
#   173s、晚高峰更慢)给单次生成留 **10min** 的悲观额度,总账 ≈ 64.5min < 90min,
#   **比 P0-40 时的 66.5min 还宽**,故 `deploy/neckline-basket.service` 的
#   `TimeoutStartSec=5400` **不必再动**(值不变 = 不必 daemon-reload)。
#   **观察判据**:journal 里 `流式生成完成:%.1fs` 那行若逼近 10min,回来重算这本账。
STREAM_CHUNK_GAP_TIMEOUT_SECONDS: float = 90.0

# 单次流式生成的悲观额度(**只用于预算算术与守门单测,不是运行时会去掐的超时**)。
# ⛔ 别把它接成一个真的 timeout —— 那就又变回"提前猜一个固定数字",正是 P0-44 要
# 根治的东西。
STREAM_GENERATION_BUDGET_ALLOWANCE_SECONDS: float = 600.0

# 「大上下文 + 长结构化生成」的任务集合 = 开流式的那一批。⛔ 检索类
# (`DEFAULT_SEARCH_TASKS`)与轻量解析类(`TASK_NL_ALERT`/`TASK_PROFILE`)不在其中。
LONG_CONTEXT_TASKS = (TASK_BASKET_REASON, TASK_TIER_RANK, TASK_SCRIPT, TASK_REVIEW)


def use_streaming_for_task(task: Optional[str]) -> bool:
    """该任务要不要走 SSE 流式(§七 P0-44)。**分级判据唯一实现**,`factory.
    get_provider()` 是唯一接线点。检索类恒 `False`(协议组合未验证,见上方注释)。"""
    return task in LONG_CONTEXT_TASKS


def read_timeout_for_task(task: Optional[str]) -> Optional[float]:
    """该任务该用多长的读超时。**`None` = 不覆盖**(用 provider 的类属性默认值
    90.0)—— 返回 `None` 而不是直接返回 90.0,是为了让"没有分级意见"与"分级后
    恰好等于默认值"两件事在调用侧分得开(provider 子类可能有自己的默认值)。

    ⚠ **P0-44 起,长上下文那一档返回的数字语义变了**:它不再是"整段生成的墙钟
    上限"(那是 P0-40 的 240s,已证伪并删除),而是**流式下的 chunk 间隔上限**。
    数值上又回到 90.0,但**含义完全不同,别看见 90 就以为回退了** —— 判据从
    「一共要吐多久」换成了「还在不在吐字」。"""
    if task in LONG_CONTEXT_TASKS:
        return STREAM_CHUNK_GAP_TIMEOUT_SECONDS
    return None


class ProviderLike(Protocol):
    """`resolve_task_provider_name` 对 `rows` 元素的最小结构化要求(鸭子类型,
    刻意不 import `settings_store.ProviderRecord`——避免引入不必要的模块耦合)。"""

    name: str
    enabled: bool
    has_web_search: bool


def resolve_task_provider_name(
    task: Optional[str],
    *,
    routes: Dict[str, str],
    default_provider: Optional[str],
    rows: Sequence[ProviderLike],
) -> Optional[str]:
    """决定这个任务该用哪个 provider **名字**。纯函数,不做存在性 / enabled / key
    校验——那是 `factory.get_provider()` 的下一步(找不到该名字对应的行,或行被
    禁用/无 key,一律在那一层判「不可用」返回 `None`)。

    优先级:
    ① **显式路由永远优先**(`routes[task]`)——即便指向的名字当前不存在/被禁用,
       也原样返回该名字,交给调用方统一走「不可用→None」,**不在这里悄悄跳过到
       默认值**:路由是用户显式配置,配错了要如实反映成"这个任务不可用",不能被
       "贴心地"绕过去用别的 provider(那样用户永远发现不了自己配错了)。
    ② 缺路由 且 `task` 属于 `DEFAULT_SEARCH_TASKS` → 挑 `rows` 中第一个
       `enabled and has_web_search` 的 provider(调用方需保证 `rows` 是稳定序,
       如按 `id` 升序,使这一步确定性可复现)。找不到 → 落到③。
    ③ 回退 `default_provider`(`app_settings.llm_default_provider`)。
    """
    if task:
        routed = routes.get(task)
        if routed:
            return routed
    if task in DEFAULT_SEARCH_TASKS:
        for row in rows:
            if row.enabled and row.has_web_search:
                return row.name
    return default_provider


__all__ = [
    "TASK_DRIVER_SEARCH",
    "TASK_NEWS_SCAN",
    "TASK_BASKET_REASON",
    "TASK_TIER_RANK",
    "TASK_SCRIPT",
    "TASK_REVIEW",
    "TASK_PROFILE",
    "TASK_NL_ALERT",
    "ALL_TASKS",
    "DEFAULT_SEARCH_TASKS",
    "STREAM_CHUNK_GAP_TIMEOUT_SECONDS",
    "STREAM_GENERATION_BUDGET_ALLOWANCE_SECONDS",
    "LONG_CONTEXT_TASKS",
    "ProviderLike",
    "read_timeout_for_task",
    "resolve_task_provider_name",
    "use_streaming_for_task",
]
