"""阶段 1 策略研究(P1–P10 逐项过堂)的可复现 runner 包。

约定:所有 runner 以 `python -m research.<name>` 从仓库根运行,读同一份缓存面板
(`research/_cache/panel_full.parquet`,gitignored,`research.lab.get_panel` 惰性构建)。
结论逐节写进 `research/stage1_report.md`。研究铁律见任务说明(诚实否定、防过拟合、
样本内定参样本外验证、分层报告)。
"""
