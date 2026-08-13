"""Serializable strategy configuration shared with the offline research lab.

The production application reads these fields from versioned strategy records.
Backtest behavior and strategy implementations live in the whynotme repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

@dataclass
class MomentumConfig:
    # —— 选股 ——
    strength: str = "volprice"          # limitup_gene | ret20 | ret20_pct | volprice | none
    strength_min_count: int = 1         # limitup_gene 用
    strength_min_ret: float = 0.15      # ret20 用
    strength_min_pct: float = 0.90      # ret20_pct 用
    buypoint: str = "pullback"          # pullback | breakout | either | none
    breakout_vol_expand: float = 1.5
    # —— 禁买过滤（P4/P5/P6，True=启用该过滤）——
    forbid_green_bigdown: Optional[float] = None   # 如 -0.03
    forbid_far_from_high: Optional[float] = None   # 如 -0.15
    forbid_new_days: Optional[int] = None          # 如 120
    forbid_high_elasticity: bool = False
    # 选股附加收紧（研究探针用；None=不启用）
    shallow_pullback: Optional[float] = None       # dist_from_high_20d >= 此值(如 -0.05)
    max_turnover: Optional[float] = None           # turnover_rate <= 此值
    # —— K2 研究扩展（§五B B4.0/B5.1/B5.3；一律默认关闭 = 与 K1 逐位相同，禁改上面既有字段语义）——
    require_mainline_member: bool = False          # B4:True 时 AND 面板的 is_mainline_member 列
    take_profit_fixed: Optional[float] = None      # B5:固定止盈 +X%(如 0.15;None=不启用,K1 回落止盈不变)
    high_elasticity_half: bool = False             # B5.3:True 时高弹票(创科北)单笔减半参与(而非黑名单剔除)
                                                   # 仅在 forbid_high_elasticity=False 时有意义;默认 False=不改 K1 sizing
    # —— K3 研究扩展(§五C B2 超跌买点;一律默认 None/off = 与 K1 逐位相同,禁改上面既有字段语义)——
    # 仅当 buypoint="oversold" 时被引用;默认 buypoint="pullback" 路径绝不触及下列字段/K3 列。
    oversold_depth_col: Optional[str] = None       # ret_1d|ret_5d|ret_10d|ret_20d|ret_5d_pct
    oversold_depth_max: Optional[float] = None     # 深度阈值(≤触发)
    oversold_trend: Optional[str] = None           # up|down|mid(趋势背景;None=不分趋势)
    oversold_pullback_max: Optional[float] = None  # dist_from_high_20d ≤(回撤门槛,臂③用)
    oversold_confirm: Optional[str] = None         # reclaim_ma5|reclaim_ma10|stabilize(启动确认,臂③)
    oversold_confirm_vol: Optional[float] = None   # 放量确认倍数(收复类)
    oversold_vol_max: Optional[float] = None       # 缩量上限(stabilize 用)
    # —— 排序（选 top-N 填仓；近零 alpha 下影响小，默认取最浅回调=最贴前高）——
    rank_by: str = "dist_from_high_20d"
    rank_desc: bool = True
    # —— 退出 ——
    stop_pct: Optional[float] = 0.05               # -5% 止损（None=不设止损）
    take_profit_retrace: Optional[float] = None    # 回落止盈阈值（None=不设）
    # V2.2-⑤ / §3.11-E **唯一一处类型放宽**:`int` → `Optional[int]`,`None` = **不设时间退出**
    # (与上一行 `stop_pct: Optional[float]` 的 `None` 语义同构)。⛔ **否决用哨兵位 9999**
    # ——哨兵位是"看不出来"的病。**默认值仍是 3、一字未动** → 旧 config 加载吃默认,K1 回测
    # 逐位不变(护栏 `tests/test_v13_exit_guardrail.py`;⚠ 六年真回测那一层已随策略档案迁出
    # 本仓、恒 skip,见 §七 P4-54,故逻辑层护栏是本仓唯一还跑得动的那道)。
    max_hold_days: Optional[int] = 3               # 时间退出（交易日）；v1.3 = 非浮盈单时间退出档；None=不设
    cooldown_days: int = 0                         # 同票亏损后冷却
    # —— v1.3 退出规则改革(§五 v1.3-①;一律默认 None/False = 与 K1 逐位相同,禁改上面字段语义)——
    # 默认值铁律:K1/K2/K3/v1.2 落库 config 均无这两字段,加载吃默认 → 时间退出仍在
    # max_hold_days 无条件触发 → K1 回测逐位不变(护栏 tests/test_v13_exit_guardrail)。
    # 新分支只在 time_exit_only_if_unprofitable=True 且 max_hold_days_profit 非空时进入。
    max_hold_days_profit: Optional[int] = None     # 浮盈单硬上限(交易日);None=不启用浮盈豁免=K1 行为
    time_exit_only_if_unprofitable: bool = False   # 时间退出仅对非浮盈单;False=无差别时间退出=K1 行为
    # —— V2.3.2-⑤ 退出字段语义换血(K8.md §十九;`v2.3-k8` 起)————————————————————
    # 🔴 **这两个字段是「对外语义」,回测/判分链一行都不读它们**:`_exit_reason` /
    # `exit_sim.py` 继续只认 `stop_pct`(那是**回测口径**)。它们回答的是另一个问题 ——
    # 「−5% 触发的是什么」:`loss_warning_action="review"` = **亏损警戒 + 由用户完成离场
    # 决策**,⛔ 系统**不得**据此触发任何自动卖出(K8.md §十三 逐字)。
    # ⚠ **默认 `None` = 该章程没有声明过这个语义**(⛔ 不是"声明为强制条件单"):老 config
    # 加载吃默认 → K1 六年回测与 `stop_is_advisory` 的老行为**逐位不变**(护栏
    # `tests/test_v13_exit_guardrail.py` + `brain.STOP_ADVISORY_CHARTERS` 白名单回退)。
    # ⛔ `stop_pct` 的字段名 / 默认值 `0.05` / 单一源地位**一字不动** —— 本版改的是它
    # **触发什么**,不是把 0.05 搬家,更不是在这里抄第二份(§五 ⑤-B / 〇b 红线 7)。
    loss_warning_pct: Optional[float] = None       # 亏损警戒线(K8 §十九 = 0.05);None=该章程未声明
    loss_warning_action: Optional[str] = None      # 警戒后的动作;"review"=交用户决策,⛔ 永不自动卖出
    # —— 仓位纪律 ——
    single_cap: float = 20000.0
    max_positions: int = 5
    max_exposure_frac: float = 0.60
    week_halving: bool = False                      # P10 挂起项开关：单周亏损后次周单笔减半
    week_halving_threshold: float = 0.05            # 挂起项定义口径：单周实现亏损 ≥ 本值×初始资金触发
                                                    # （5%=挂起项「次周单笔减半」；区别于 §2.1 已采纳的 2% 强制复盘线）



__all__ = ["MomentumConfig"]
