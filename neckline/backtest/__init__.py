"""回测引擎(plan 0.7-0.8):Strategy/Portfolio/Broker 分层 + 事件驱动逐日循环。

    strategy.py      Strategy 接口 / Order / BacktestContext
    portfolio.py      持仓 / 现金 / T+1 锁定
    broker.py          撮合(涨跌停 / 停牌 / 滑点 / 手续费)
    engine.py           逐日循环主控
    report.py           净值曲线 + 统计指标
    walk_forward.py   样本内/外滚动窗口切分器(阶段 1 用)
"""

from neckline.backtest.broker import Broker, ExecutionResult
from neckline.backtest.engine import BacktestEngine
from neckline.backtest.portfolio import ClosedTrade, Portfolio, Position, TradeRecord
from neckline.backtest.report import BacktestReport, build_report
from neckline.backtest.strategy import BacktestContext, Order, Strategy
from neckline.backtest.walk_forward import WalkForwardWindow, generate_walk_forward_windows

__all__ = [
    "Broker",
    "ExecutionResult",
    "BacktestEngine",
    "ClosedTrade",
    "Portfolio",
    "Position",
    "TradeRecord",
    "BacktestReport",
    "build_report",
    "BacktestContext",
    "Order",
    "Strategy",
    "WalkForwardWindow",
    "generate_walk_forward_windows",
]
