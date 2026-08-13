"""TuShare 薄封装(plan 0.2)。继承 LinoN `backend/app/data/tushare_client.py` 的姿势:

    TushareResult = { ok: bool, data: DataFrame | None, reason: str }

—— 绝不抛异常,token 缺失 / 初始化失败 / 限频 / 网络异常一律 ok=False。
`ts.pro_api(token)` 【直传】,**禁用 `ts.set_token()`**(会往家目录写缓存文件,
nologin 系统用户会炸;继承 LinoN 坑 5,§3.7)。

Neckline 相对 LinoN 的新增:全市场批量拉取(按 `trade_date` 一次拿全市场,
不带 `ts_code`)+ 限频退避(600 档 500 次/分钟)+ 失败重试(指数退避)。

字段单位(§3.7 铁律,读取方务必遵守,不在此层做单位换算——原样透传 TuShare 返回):
    daily.amount        千元
    daily.vol            手
    daily_basic.total_mv 万元
    moneyflow_dc.net_amount 万元(= buy_elg_amount 超大单 + buy_lg_amount 大单,东财主力口径)
    adj_factor.adj_factor 复权因子(前复权公式见 neckline.data.adjust)
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

from neckline.config import settings

logger = logging.getLogger(__name__)

# pandas / tushare 是重依赖,延迟到首次真正需要时再 import,
# 无 token 的降级路径在依赖未装时也不崩(直接走 reason 返回)。
_TS_PRO: Any = None
_TS_INIT_DONE = False
_TS_INIT_REASON = ""
_INIT_LOCK = threading.Lock()

# TuShare 600 元档 = 6000 积分,限频 500 次/分钟(plan §3.2)。留缓冲,
# 目标峰值控制在 450/分钟,不做满打满算的极限压榨。
_RATE_LIMIT_MAX_CALLS = 450
_RATE_LIMIT_PERIOD_SEC = 60.0

# 失败重试:限频 / 网络抖动等瞬时错误重试,退避秒数递增。
_MAX_ATTEMPTS = 4
_BACKOFF_SCHEDULE = (1.0, 3.0, 8.0)


@dataclass
class TushareResult:
    ok: bool
    data: Optional[Any]  # pandas.DataFrame | None(避免顶层强依赖 pandas 做类型标注)
    reason: str

    @classmethod
    def fail(cls, reason: str) -> "TushareResult":
        return cls(ok=False, data=None, reason=reason)

    @classmethod
    def success(cls, data: Any) -> "TushareResult":
        return cls(ok=True, data=data, reason="ok")


def to_ts_code(code: str) -> str:
    """裸代码 / 带前缀 → Tushare ts_code(如 '600000.SH')。"""
    c = code.strip().upper()
    if re.match(r"^\d{6}\.(SH|SZ|BJ)$", c):
        return c
    digits = re.sub(r"\D", "", c)
    if len(digits) != 6:
        return c  # 交给上游,Tushare 会自行报错(已被 try 包住)
    if digits.startswith("920"):
        return f"{digits}.BJ"
    if digits.startswith(("0", "3")):
        return f"{digits}.SZ"
    if digits.startswith(("4", "8")):
        return f"{digits}.BJ"
    if digits.startswith("6"):
        return f"{digits}.SH"
    return f"{digits}.SH"


class _RateLimiter:
    """滑动窗口限速器:阻塞式,超过窗口内调用上限就 sleep 到窗口腾出位置。"""

    def __init__(self, max_calls: int, period_sec: float) -> None:
        self.max_calls = max_calls
        self.period_sec = period_sec
        self._timestamps: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] > self.period_sec:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.max_calls:
                sleep_for = self.period_sec - (now - self._timestamps[0]) + 0.05
                if sleep_for > 0:
                    logger.info("限频保护:sleep %.2fs(窗口内已 %d 次调用)", sleep_for, len(self._timestamps))
                    time.sleep(sleep_for)
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] > self.period_sec:
                    self._timestamps.popleft()
            self._timestamps.append(time.monotonic())


_rate_limiter = _RateLimiter(_RATE_LIMIT_MAX_CALLS, _RATE_LIMIT_PERIOD_SEC)


def _get_pro() -> tuple[Optional[Any], str]:
    """惰性初始化 Tushare pro 客户端,结果缓存(token 在进程生命周期内不变)。"""
    global _TS_PRO, _TS_INIT_DONE, _TS_INIT_REASON
    if _TS_INIT_DONE:
        return _TS_PRO, _TS_INIT_REASON
    with _INIT_LOCK:
        if _TS_INIT_DONE:
            return _TS_PRO, _TS_INIT_REASON
        token = settings.tushare_token
        if not token:
            _TS_PRO, _TS_INIT_REASON, _TS_INIT_DONE = None, "token 缺失", True
            return _TS_PRO, _TS_INIT_REASON
        try:
            import tushare as ts  # 延迟导入

            # 【铁律】token 直传 pro_api,不调 set_token——set_token 会往用户
            # 家目录写缓存文件,nologin 系统服务用户无可写家目录会直接炸初始化。
            _TS_PRO = ts.pro_api(token)
            _TS_INIT_REASON = "ok"
        except ImportError:
            _TS_PRO, _TS_INIT_REASON = None, "tushare 包未安装"
        except Exception as e:  # 初始化失败(无效 token / 网络)
            _TS_PRO, _TS_INIT_REASON = None, f"Tushare 初始化失败: {e}"
        finally:
            _TS_INIT_DONE = True
        return _TS_PRO, _TS_INIT_REASON


def reset_client_cache() -> None:
    """清初始化缓存(测试 / token 录入后热切换用)。"""
    global _TS_PRO, _TS_INIT_DONE, _TS_INIT_REASON
    with _INIT_LOCK:
        _TS_PRO, _TS_INIT_DONE, _TS_INIT_REASON = None, False, ""


def _is_transient(msg: str) -> bool:
    """粗判是否值得重试的瞬时错误(限频 / 网络);权限类错误重试也不会好,不单独排除
    ——统一重试策略更简单,权限错误多试几次的代价(几秒)可接受。"""
    return True


def _call(api_name: str, **kwargs: Any) -> TushareResult:
    """统一调用包装:限速 → 拿 pro → 调 api → 捕获一切异常转 reason → 失败重试。"""
    pro, reason = _get_pro()
    if pro is None:
        return TushareResult.fail(reason)

    last_reason = "未知错误"
    for attempt in range(_MAX_ATTEMPTS):
        _rate_limiter.acquire()
        try:
            method = getattr(pro, api_name)
            df = method(**kwargs)
        except Exception as e:
            msg = str(e)
            if "每分钟" in msg or "频率" in msg or "limit" in msg.lower():
                last_reason = f"限频: {msg}"
            else:
                last_reason = f"网络/接口异常: {msg}"
            if attempt < _MAX_ATTEMPTS - 1 and _is_transient(msg):
                backoff = _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]
                logger.warning(
                    "%s(%s) 调用失败(第 %d/%d 次):%s,%.1fs 后重试",
                    api_name, kwargs, attempt + 1, _MAX_ATTEMPTS, msg, backoff,
                )
                time.sleep(backoff)
                continue
            return TushareResult.fail(last_reason)
        else:
            if df is None:
                last_reason = "接口返回空"
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)])
                    continue
                return TushareResult.fail(last_reason)
            return TushareResult.success(df)
    return TushareResult.fail(last_reason)


# —— 单票接口(问询台 / 阶段 1+ 用)——————————————————————————————————

def ts_daily(code: str, start: str, end: str) -> TushareResult:
    """单票日线。start/end 格式 'YYYYMMDD'。"""
    return _call("daily", ts_code=to_ts_code(code), start_date=start, end_date=end)


def ts_daily_basic(code: str, trade_date: str) -> TushareResult:
    return _call("daily_basic", ts_code=to_ts_code(code), trade_date=trade_date)


def ts_adj_factor(code: str, start: str, end: str) -> TushareResult:
    return _call("adj_factor", ts_code=to_ts_code(code), start_date=start, end_date=end)


def ts_moneyflow_dc(code: str, start: str, end: str) -> TushareResult:
    return _call("moneyflow_dc", ts_code=to_ts_code(code), start_date=start, end_date=end)


def ts_namechange(code: str) -> TushareResult:
    return _call(
        "namechange",
        ts_code=to_ts_code(code),
        fields="ts_code,name,start_date,end_date,ann_date,change_reason",
    )


# —— 全市场批量接口(阶段 0 backfill 主力;不带 ts_code,按 trade_date 一次全市场)——

def ts_daily_all(trade_date: str) -> TushareResult:
    """全市场当日 daily(开高低收/量额)。trade_date 'YYYYMMDD'。"""
    return _call("daily", trade_date=trade_date)


def ts_daily_basic_all(trade_date: str) -> TushareResult:
    """全市场当日 daily_basic(换手率/量比/市值/PE-PB)。"""
    return _call("daily_basic", trade_date=trade_date)


def ts_adj_factor_all(trade_date: str) -> TushareResult:
    """全市场当日复权因子。"""
    return _call("adj_factor", trade_date=trade_date)


def ts_moneyflow_dc_all(trade_date: str) -> TushareResult:
    """全市场当日 moneyflow_dc(东财源,net_amount=主力净额万元)。"""
    return _call("moneyflow_dc", trade_date=trade_date)


def ts_suspend_d_all(trade_date: str) -> TushareResult:
    """全市场当日**停牌**名单(plan §五 v1.4-①-B / §七 P0-2)。

    **2026-07-28 真 token 活体探活确认可用**(照 v1.3-③-C4 `anns_d` 探活的做法,结论写档):
    600 元档直接可调,`suspend_type='S'`(停牌;'R'=复牌)。当日实测 9 只、`002036.SZ`
    自 20260723 起连续在榜 —— 即该票是**真停牌**、不是数据源缺口(§七 P0-2 诊断结论)。

    字段:`ts_code`、`trade_date`、`suspend_timing`(盘中停牌时段,全天停牌为 None)、
    `suspend_type`。本项目只用「当日是否在停牌名单里」这一位信息,给持仓 `priceStale.reason`
    定标签(`suspended` vs `data_gap`)。拉不到 → 调用方降级成 `unknown`,**不崩、也不假装
    知道**(§3.8「没有」与「没看」必须能分开)。
    """
    return _call("suspend_d", trade_date=trade_date, suspend_type="S")


def ts_index_daily(ts_code: str, start: str, end: str) -> TushareResult:
    """单指数区间日线(上证/深成/创业板指等)。实测支持一次拿 6 年区间,不必分批。"""
    return _call("index_daily", ts_code=ts_code, start_date=start, end_date=end)


def ts_trade_cal(start: str, end: str, exchange: str = "SSE") -> TushareResult:
    """交易日历。start/end 'YYYYMMDD'。"""
    return _call("trade_cal", exchange=exchange, start_date=start, end_date=end)


def ts_stock_basic(list_status: str) -> TushareResult:
    """按上市状态拉股票基础信息。list_status: 'L'=上市 'D'=退市 'P'=暂未上市。

    字段含 `market`(主板/创业板/科创板/北交所,TuShare 原生分类,limit_derived
    板块判定优先用此字段;代码前缀正则只作它缺失时的 fallback)。
    """
    return _call(
        "stock_basic",
        exchange="",
        list_status=list_status,
        fields="ts_code,symbol,name,industry,market,list_date,delist_date,list_status",
    )


def ts_ths_index(exchange: str = "A", type_: str = "N") -> TushareResult:
    """同花顺板块指数列表(概念/行业/地域)。600 档可用(实测,§3.2「概念板块和成分」)。
    type: N=概念指数 I=行业指数 R=地域 …;板块年龄因子(1.6/P2)用概念指数(type='N')。
    返回 `ts_code`(如 883300.TI)、name、count(成分数)、list_date、type。"""
    return _call("ths_index", exchange=exchange, type=type_)


def ts_ths_member(index_code: str) -> TushareResult:
    """某板块【当前】成分股(con_code=成分票代码)。**注意:该接口是时点快照,无历史
    成分**——历史回测用当前成分做「股票→板块」映射会引入幸存者/前视偏差,1.6 板块年龄
    的股票级联动因此受限(见 stage1_report P2 节的诚实说明)。"""
    return _call("ths_member", ts_code=index_code)


def ts_ths_daily(index_code: str, start: str, end: str) -> TushareResult:
    """板块指数日线(open/high/low/close/pre_close/pct_change)。板块年龄用板块指数本身
    (无成分映射前视问题),度量「板块启动第几天 / 板块动量」。"""
    return _call("ths_daily", ts_code=index_code, start_date=start, end_date=end)


def ts_stk_holdertrade(start: str, end: str) -> TushareResult:
    """股东增减持(全市场,按公告日区间;plan §五 v1.3-③-C4「消息面扫描」减持类的
    结构化数据源)。**2026-07-26 真实 token 活体探活确认**:所需 2000 积分,在本项目
    600 元档(6000 积分)覆盖范围内可直接调用(**非**「单独权限」——与 `anns_d` 不同,
    见 `neckline.report.news_alerts` 模块头的完整侦察结论)。

    字段:`ann_date`(公告日 'YYYYMMDD')、`ts_code`、`holder_name`、`holder_type`
    (G高管/P个人/C公司)、`in_de`(IN增持/DE减持,消息面扫描只取 DE)、`change_vol`
    (变动股数)、`change_ratio`(占总股本比例 %)、`after_share`/`after_ratio`
    (变动后持股数/占比,可能为空)、`avg_price`(成交均价,可能为空)、`total_share`。
    单次最大 3000 行(官方文档);本项目按数日窗口调用,实测量级约 40-60 行/日,
    远低于该上限,不分页。"""
    return _call("stk_holdertrade", start_date=start, end_date=end)


def ts_top_list(trade_date: str) -> TushareResult:
    """龙虎榜每日明细(§3.2 可用接口,2000 积分档,600 档天然覆盖)。trade_date 'YYYYMMDD'。
    某日无股票上榜是正常情况(不是每个交易日都有龙虎榜),返回空表不代表失败。

    字段单位(2026-07-20 官方文档 https://tushare.pro/document/2?doc_id=106 +
    网页交叉核对确认,§3.7 铁律口径表新增一行):`l_buy`/`l_sell`/`l_amount`/
    `net_amount` 单位【万元】(与 moneyflow_dc.net_amount 同惯例,勿当元用);
    `net_rate`/`amount_rate`/`pct_change`/`turnover_rate` 是百分比数值(如 3.2
    即 3.2%);`close` 单位元。`amount`(当日总成交额)/`float_values`(流通市值)
    两个字段官方文档未明确单位、本项目**不消费**这两列(继承"字段单位不确定宁可
    不用,不猜"的教训),只取 l_buy/l_sell/net_amount/net_rate/reason 等已核实字段
    (见 neckline/data/top_list.py)。
    """
    return _call("top_list", trade_date=trade_date)


def ts_namechange_page(limit: int = 8000, offset: int = 0) -> TushareResult:
    """`namechange` 全量分页拉取(阶段 0.2 新增)。单页上限 8000(接口硬上限 1 万,
    留余量);调用方循环 offset 直到某页行数 < limit 为止(见 scripts/backfill.py
    的 `fetch_namechange_all`)。

    已知坑:该接口按 start_date 降序返回,offset 分页在多行同 start_date 的边界
    上偶有漏行风险(阶段 0 实测过);backfill 侧对当前状态为 ST/*ST 的代码做了
    单票补拉兜底,详见 backfill.py 注释。
    """
    return _call(
        "namechange",
        fields="ts_code,name,start_date,end_date,ann_date,change_reason",
        limit=limit,
        offset=offset,
    )


__all__ = [
    "TushareResult",
    "to_ts_code",
    "reset_client_cache",
    "ts_daily",
    "ts_daily_basic",
    "ts_adj_factor",
    "ts_moneyflow_dc",
    "ts_namechange",
    "ts_daily_all",
    "ts_daily_basic_all",
    "ts_adj_factor_all",
    "ts_moneyflow_dc_all",
    "ts_index_daily",
    "ts_trade_cal",
    "ts_stock_basic",
    "ts_namechange_page",
    "ts_ths_index",
    "ts_ths_member",
    "ts_ths_daily",
    "ts_top_list",
    "ts_stk_holdertrade",
]
