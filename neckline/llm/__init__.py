"""LLM 供应商抽象层(plan 2.4/§3.4;V2-② 起 Provider 自填制,plan §五 V2-②/
§3.10-B)。统一 chat + 工具调用 + 联网搜索接口。**V2 起 provider 不再是 GLM/Kimi
枚举**——`llm_providers` 表(任意 OpenAI 兼容端点自填)+ `app_settings.
llm_task_routes`/`llm_default_provider` 任务路由,由 `neckline.llm.factory.
get_provider(task, ...)` 解析(见该函数模块头)。`GLMProvider`/`KimiProvider`
(`providers/glm.py`/`providers/kimi.py`)降级为预置参考实现,不再是解析链路
的一部分,详见各自模块头。

铁律:缺 provider / 缺 key / 网络异常 / 非法响应 → 优雅降级,绝不抛异常上抛到
报告管线(`neckline.llm.judge.judge_candidate` 据此输出「LLM 未激活」占位,不假装
分析过)。**诚实声明**:GLM/Kimi 的 endpoint、模型名、联网搜索 tool schema 已于
2026-07-20 按官方文档核实(见 `openai_compat.py`/`providers/*.py` 模块头注释的来源
链接),但本项目没有真实 key,"真调用成功"路径未做过活体验证——拿到 key 后应先
跑一次真连烟雾测试(手工脚本,非 pytest)确认协议假设仍然成立。
"""
