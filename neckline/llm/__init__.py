"""LLM 供应商抽象层(plan 2.4/§3.4)。统一 chat + 工具调用 + 联网搜索接口,
GLM(智谱)/ Kimi(Moonshot)可插拔实现,provider 从 `.env` 的 `LLM_PROVIDER`/
`LLM_API_KEY` 选(见 `neckline.llm.factory.get_provider`)。

铁律:缺 provider / 缺 key / 网络异常 / 非法响应 → 优雅降级,绝不抛异常上抛到
报告管线(`neckline.llm.judge.judge_candidate` 据此输出「LLM 未激活」占位,不假装
分析过)。**诚实声明**:GLM/Kimi 的 endpoint、模型名、联网搜索 tool schema 已于
2026-07-20 按官方文档核实(见 `openai_compat.py`/`providers/*.py` 模块头注释的来源
链接),但本项目没有真实 key,"真调用成功"路径未做过活体验证——拿到 key 后应先
跑一次真连烟雾测试(手工脚本,非 pytest)确认协议假设仍然成立。
"""
