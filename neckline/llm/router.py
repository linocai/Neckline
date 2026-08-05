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
TASK_INQUIRY = "inquiry"                # 问询台
TASK_NL_ALERT = "nl_alert"              # 自然语言临时提醒解析

ALL_TASKS = (
    TASK_DRIVER_SEARCH,
    TASK_NEWS_SCAN,
    TASK_BASKET_REASON,
    TASK_TIER_RANK,
    TASK_SCRIPT,
    TASK_REVIEW,
    TASK_PROFILE,
    TASK_INQUIRY,
    TASK_NL_ALERT,
)

# 默认路由的「检索类」集合(plan 原文明确点名 driver_search/news_scan 两项)。
# ⚠ builder 推断,如实标注:问询台(TASK_INQUIRY)一并收录进检索类——plan §3.10-B
# 原文只举了两个例子、未列举问询台,但 `api/inquiry.py` 现有实现本就
# `provider.chat(enable_search=True, ...)`(§2.5「LLM 带工具调用/联网搜索」是问询台
# 的核心能力之一);若不把它归入检索类,配好双 provider 后问询台会默认拿到无搜索
# 能力的推理 provider,静默丢失搜索能力——这比"没做"更隐蔽,故按行为保真原则收录。
# 若与规划意图不符,以 planner 澄清为准,后续可从此元组摘除。
DEFAULT_SEARCH_TASKS = (TASK_DRIVER_SEARCH, TASK_NEWS_SCAN, TASK_INQUIRY)

# —— 读超时分级(§七 P0-40,2026-08-05 生产实打)————————————————————————————
# **病灶**:`OpenAICompatProvider.read_timeout=90.0` 那个数字是给**带联网搜索的
# 单次审判/问询**调的(v1.3.4 实测 30-60s+,25s 下 10 只审判 5 只 ReadTimeout)。
# V2 的**推理类**是另一种工作量:⑤ 的篮子聚合一次把 **20 颗种子 + 每颗的成员机械
# 数据**塞进同一个 prompt,再要一份结构化 JSON 出来 —— 2026-08-05 生产实测
# **3/3 次全部恰好 90s ReadTimeout**(12:29:43 起,每次整 90s),**确定性超长、
# 不是网络抖动**:重试三次只是把同一个"生成没跑完"重放三遍,当日必然不成篮。
#
# **为什么按 task 类别分级,而不是全局翻倍**:短读超时的价值在于**快速掐断真卡死
# 的连接**(LinoN `deepseek.py` 传下来的姿势),全局翻倍会把"真卡死"的每次等待也
# 拖成 240s、白白吃掉预算账。检索类的 90s 是**有实测背书的**,一字不动;只给
# 「大上下文 + 长结构化生成」这一类放宽。
#
# **为什么落在 `factory.get_provider(task)` 而不是 `chat()` 加参数**:后者要改每
# 一个调用点(⑤⑥⑦⑨ 五处 + 未来的),漏一个就退回 90s 且**看不出来**;工厂是所有
# provider 的唯一出生地,按 task 分级只需一处、天然全覆盖。⚠ 代价是**直接 new
# 出来的 provider 不受影响**(单测替身、`providers/{glm,kimi}.py` 参考实现)——
# 这正是我们要的:类属性默认值 90.0 保持不变,既有行为逐字节不变。
#
# **预算与 unit 超时的账**(改这个数字前先重算一遍):240s × `max_attempts=3` =
# 12 min 最坏,在推理账 `REASON_BUDGET_SECONDS=30min` 之内;但 `deploy/
# neckline-basket.service` 的 `TimeoutStartSec` 是**按这套算术定的**(检索账 20min
# + 一组超时溢出 + 推理账 30min + 一组溢出),故已同步从 3600 抬到 5400,见该文件注释。
LONG_CONTEXT_READ_TIMEOUT_SECONDS: float = 240.0

# 「大上下文 + 长结构化生成」的任务集合。⛔ 检索类(`DEFAULT_SEARCH_TASKS`)与
# 轻量解析类(`TASK_NL_ALERT`/`TASK_PROFILE`)不在其中 —— 它们维持 90s。
LONG_CONTEXT_TASKS = (TASK_BASKET_REASON, TASK_TIER_RANK, TASK_SCRIPT, TASK_REVIEW)


def read_timeout_for_task(task: Optional[str]) -> Optional[float]:
    """该任务该用多长的读超时。**`None` = 不覆盖**(用 provider 的类属性默认值
    90.0)—— 返回 `None` 而不是直接返回 90.0,是为了让"没有分级意见"与"分级后
    恰好等于默认值"两件事在调用侧分得开(provider 子类可能有自己的默认值)。"""
    if task in LONG_CONTEXT_TASKS:
        return LONG_CONTEXT_READ_TIMEOUT_SECONDS
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
    "TASK_INQUIRY",
    "TASK_NL_ALERT",
    "ALL_TASKS",
    "DEFAULT_SEARCH_TASKS",
    "LONG_CONTEXT_READ_TIMEOUT_SECONDS",
    "LONG_CONTEXT_TASKS",
    "ProviderLike",
    "read_timeout_for_task",
    "resolve_task_provider_name",
]
