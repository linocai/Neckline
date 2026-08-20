"""实时行情源(V2.5.0 S1:自 `sentinel/quotes.py` 原样搬入 `data/`)。

搬家理由(PROJECT_PLAN 裁定 7):盘中哨兵整块退役,但这块是承重墙 —— `Quote` /
`DualQuote` / `to_symbol` 被 `auction/`(竞价冻结抓取与报价校验)与
`review/parse.py`(交割单代码归一)消费,它本来就是**数据层**的东西,不是哨兵的。

⚠ `to_symbol` 的**后缀优先不可退化**(§12 坑 7):前缀启发式对指数会静默拿错标的
(`000001.SH` 上证综指会被判成 `sz000001` 平安银行)。单测已随文件一起搬到
`tests/test_data_realtime.py`,⛔ 不许简化。

以下为原模块头,内容未改 ——
盘中实时源(plan 阶段 3 §2.4/§3.7,新浪主 / 腾讯备)。继承 LinoN 已踩平的坑
(`/Users/linotsai/Lino/LinoN/backend/app/data/realtime.py`,权威见 LinoN CLAUDE.md
「数据源坑」节),重写点:

    · 用 `httpx` + 可注入 `transport`(而非 LinoN 的 `requests`),与本项目
      `neckline.llm.openai_compat` 已确立的「MockTransport 传统」一致,免联网单测。
    · `Quote` 不再自带 limit_up/limit_down——LinoN 版本内嵌了一个简化的 ±10%/±5%
      规则,漏了 20%(科创/创业板)、30%(北交所)。Neckline 已有权威的板块涨跌幅
      规则(`neckline.data.limit_derived`,§2.4「复用 limit_derived 的幅度规则算
      当日涨跌停价」),涨跌停价的计算挪到那边的 `compute_intraday_limit_prices`,
      调用方(retreat.py/holding.py)按需另算,`Quote` 只装「源里直接给的」市场数据。
    · `to_symbol` 补上北交所(bj 前缀),复用 `neckline.data.board.classify_by_code`
      的 BSE 正则(单一源,不再抄一份 8/4/920 前缀判断)。

字段单位(归一目标,§3.7 铁律,两源口径不同务必对齐):
    · 新浪:逗号分隔,GBK;volume 单位=股(÷100→手),amount 单位=元(原样);
      bid/ask 块「量先价后」;必须带 `Referer: https://finance.sina.com.cn`
      头,否则返回 `Kinsoku jikou desu!`(无数据)。
    · 腾讯:~ 分隔,GBK;volume(field6)单位=手(原样),amount(field37)单位=万元
      (×1e4→元);bid/ask 块「价先量后」(与新浪相反,易写反)。
    · 归一后 `Quote.volume` 单位=手、`Quote.amount` 单位=元——与
      `neckline.data.tushare_client` 的 EOD `daily.vol`(手)/`daily.amount`(千元,
      注意不同)是两套不同量纲,不要混用,VWAP 计算见 `intraday.py`。

源全挂 → 该票不在返回结果里(跳过),批量调用整体不崩;单批请求过大时按
`_CHUNK_SIZE` 拆分多次请求(sina/tencent 均未官方文档化单请求代码数上限,保守
分块以降低单次超时/被限流的风险)。
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from neckline.data.board import Board, classify_by_code

logger = logging.getLogger(__name__)

# 免费源偶发慢/被限流,给短超时 + 1 次重试(哨兵 1 分钟一拍,不能让单次请求拖垮节奏)。
_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 5.0
_MAX_ATTEMPTS = 2
# 单次请求代码数上限(保守分块,降低超时/限流风险;§2.4 工程要求「批量拉取(一次
# 请求多票)」——分块内仍是"一次请求多票",不是逐票请求)。
_CHUNK_SIZE = 400

_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
}
_TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
}

_SINA_URL = "https://hq.sinajs.cn/list={symbols}"
_TENCENT_URL = "https://qt.gtimg.cn/q={symbols}"


@dataclass
class Quote:
    code: str
    name: str
    price: float
    pre_close: float
    open: float
    high: float
    low: float
    volume: float          # 手
    amount: float           # 元
    ts: str                 # 数据时间(源自带,格式两源略有差异,仅供展示/记账)
    source: str             # "sina" | "tencent"
    bid: List[float] = field(default_factory=list)   # bid1..5 价
    ask: List[float] = field(default_factory=list)   # ask1..5 价

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# —— 代码 → 市场前缀(sina/tencent 符号) ——————————————————————————————

_SUFFIX_TO_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj"}


def to_symbol(code: str) -> str:
    """归一为带市场前缀的符号。**`ts_code` 自带的 `.SH/.SZ/.BJ` 后缀优先**;没有后缀
    才退回代码前缀启发式(6*→sh,0*/3*→sz,北交所走
    `neckline.data.board.classify_by_code` 单一源判定)。已带前缀的原样小写。

    ⚠ **为什么后缀必须优先(V2-⑧-A 加指数代码时发现的真洞)**:前缀启发式对**股票**
    永远与后缀一致(6 开头必在沪、0/3 必在深),但对**指数**会错得很安静 ——
    `000001.SH`(上证综指)会被判成 `sz000001`(平安银行),拉回来的是**另一个标的**
    的行情且完全看不出异常。改后对既有股票代码逐位等价(单测锁死),只修指数这一类。
    """
    c = code.strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    raw = code.strip().upper()
    digits = re.sub(r"\D", "", c)
    if not digits:
        return c
    if "." in raw:
        prefix = _SUFFIX_TO_PREFIX.get(raw.rsplit(".", 1)[1])
        if prefix:
            return prefix + digits
    if classify_by_code(digits) == Board.BSE:
        return "bj" + digits
    return ("sh" if digits.startswith("6") else "sz") + digits


def _bare_code(symbol_or_code: str) -> str:
    return re.sub(r"\D", "", symbol_or_code)


def _f(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


# —— 新浪 ——————————————————————————————————————————————————————————

_SINA_RE = re.compile(r'hq_str_([a-z]{2}\d+)="([^"]*)"')


def _fetch_sina(symbols: List[str], transport: Optional[Any]) -> Dict[str, str]:
    """返回 {symbol: 引号内原始字符串}。网络/HTTP 异常 → 空 dict,不抛。"""
    if not symbols:
        return {}
    try:
        import httpx
    except ImportError:  # pragma: no cover - 依赖未装时
        return {}
    url = _SINA_URL.format(symbols=",".join(symbols))
    text = _http_get_text(url, _SINA_HEADERS, transport, httpx)
    if text is None:
        return {}
    return dict(_SINA_RE.findall(text))


def _parse_sina(symbol: str, body: str) -> Optional[Quote]:
    """解析新浪单票字符串(逗号分隔,32 字段)。停牌/非法 → None。"""
    parts = body.split(",")
    if len(parts) < 32 or not parts[0]:
        return None
    name = parts[0]
    open_ = _f(parts[1])
    pre_close = _f(parts[2])
    price = _f(parts[3])
    high = _f(parts[4])
    low = _f(parts[5])
    volume_shares = _f(parts[8])     # 股
    amount_yuan = _f(parts[9])       # 元
    if pre_close <= 0 and price <= 0:
        return None
    # bid 块 10..19(量先价后),ask 块 20..29(量先价后)
    bid = [_f(parts[11 + i * 2]) for i in range(5)]
    ask = [_f(parts[21 + i * 2]) for i in range(5)]
    date_part = parts[30] if len(parts) > 30 else ""
    time_part = parts[31] if len(parts) > 31 else ""
    ts = f"{date_part} {time_part}".strip()
    return Quote(
        code=_bare_code(symbol),
        name=name,
        price=price if price > 0 else pre_close,
        pre_close=pre_close,
        open=open_,
        high=high,
        low=low,
        volume=round(volume_shares / 100, 2),   # 股 → 手
        amount=amount_yuan,
        ts=ts,
        source="sina",
        bid=bid,
        ask=ask,
    )


# —— 腾讯(降级) ————————————————————————————————————————————————————

_TENCENT_RE = re.compile(r'v_([a-z]{2}\d+)="([^"]*)"')


def _fetch_tencent(symbols: List[str], transport: Optional[Any]) -> Dict[str, str]:
    if not symbols:
        return {}
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return {}
    url = _TENCENT_URL.format(symbols=",".join(symbols))
    text = _http_get_text(url, _TENCENT_HEADERS, transport, httpx)
    if text is None:
        return {}
    return dict(_TENCENT_RE.findall(text))


def _parse_tencent(symbol: str, body: str) -> Optional[Quote]:
    """解析腾讯单票字符串(~ 分隔)。单位:volume(field6)=手(原样);
    amount(field37)=万元(×1e4→元)。bid/ask 为「价先量后」(与新浪相反)。"""
    parts = body.split("~")
    if len(parts) < 35 or not parts[1]:
        return None
    name = parts[1]
    price = _f(parts[3])
    pre_close = _f(parts[4])
    open_ = _f(parts[5])
    volume_lots = _f(parts[6])       # 手(原样)
    # bid 块 9..18(价先量后):价在 9,11,13,15,17
    bid = [_f(parts[9 + i * 2]) for i in range(5)]
    # ask 块 19..28(价先量后):价在 19,21,23,25,27
    ask = [_f(parts[19 + i * 2]) for i in range(5)]
    high = _f(parts[33]) if len(parts) > 33 else 0.0
    low = _f(parts[34]) if len(parts) > 34 else 0.0
    ts_raw = parts[30] if len(parts) > 30 else ""   # YYYYMMDDHHMMSS
    ts = _fmt_tencent_ts(ts_raw)
    amount_wan = _f(parts[37]) if len(parts) > 37 else 0.0
    if pre_close <= 0 and price <= 0:
        return None
    return Quote(
        code=_bare_code(symbol),
        name=name,
        price=price if price > 0 else pre_close,
        pre_close=pre_close,
        open=open_,
        high=high,
        low=low,
        volume=volume_lots,
        amount=round(amount_wan * 10000, 2),
        ts=ts,
        source="tencent",
        bid=bid,
        ask=ask,
    )


def _fmt_tencent_ts(raw: str) -> str:
    raw = (raw or "").strip()
    if len(raw) == 14 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} {raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
    return raw


def _http_get_text(url: str, headers: Dict[str, str], transport: Optional[Any], httpx_module: Any) -> Optional[str]:
    """一次 HTTP GET,GBK 解码。短超时 + 1 次重试(每次全新连接,姿势沿用
    `neckline.llm.openai_compat._post`);全部尝试失败 → None,调用方按"该源全挂"处理。"""
    timeout = httpx_module.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
    last_exc: Optional[BaseException] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            client_kwargs: Dict[str, Any] = {"timeout": timeout}
            if transport is not None:
                client_kwargs["transport"] = transport
            with httpx_module.Client(**client_kwargs) as client:
                resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning("实时源请求非200(%s,尝试%d/%d)", resp.status_code, attempt, _MAX_ATTEMPTS)
                continue
            resp.encoding = "gbk"
            return resp.text
        except Exception as e:  # noqa: BLE001  超时/网络异常 → 换新连接重试
            last_exc = e
            logger.warning("实时源请求异常(尝试%d/%d):%s", attempt, _MAX_ATTEMPTS, e)
    if last_exc is not None:
        logger.warning("实时源请求全部尝试失败:%s", last_exc)
    return None


def _chunks(items: List[str], size: int) -> List[List[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


# —— 对外 API ——————————————————————————————————————————————————————

def get_quotes(codes: List[str], transport: Optional[Any] = None) -> Dict[str, Quote]:
    """批量拉实时行情。新浪主源优先,主源缺的票用腾讯补;大批量按 `_CHUNK_SIZE`
    分块请求(仍是"批量拉取",不是逐票单独请求)。

    返回 {原始 code: Quote}。任一源全挂或单票解析失败 → 该票不在结果里(跳过),
    整体不崩(§2.4 铁律:实时源不可靠是常态,哨兵必须优雅处理缺数据)。
    """
    if not codes:
        return {}
    sym_to_code: Dict[str, str] = {}
    symbols: List[str] = []
    for c in codes:
        sym = to_symbol(c)
        sym_to_code[sym] = c
        symbols.append(sym)

    result: Dict[str, Quote] = {}

    for batch in _chunks(symbols, _CHUNK_SIZE):
        sina_raw = _fetch_sina(batch, transport)
        for sym in batch:
            body = sina_raw.get(sym)
            if body is None:
                continue
            q = _parse_sina(sym, body)
            if q is not None:
                result[sym_to_code[sym]] = q

        missing = [s for s in batch if sym_to_code[s] not in result]
        if missing:
            tencent_raw = _fetch_tencent(missing, transport)
            for sym in missing:
                body = tencent_raw.get(sym)
                if body is None:
                    continue
                q = _parse_tencent(sym, body)
                if q is not None:
                    result[sym_to_code[sym]] = q

    return result


def get_quote(code: str, transport: Optional[Any] = None) -> Optional[Quote]:
    """单票。全失败返回 None,不抛崩。"""
    return get_quotes([code], transport=transport).get(code)


# —— 🔴 V2.4.0 P2.2:有界**双源核验**(K8 §二十「主备源」)——————————————————————

@dataclass(frozen=True)
class DualQuote:
    """同一只代码的**两源原始读数**。🔴 **两个都留痕,⛔ 不许只存胜出的那一个**
    (K8 §二十 逐字:「两个来源的原始读数全部留存」)。

    `primary` = 新浪(主源)· `backup` = 腾讯(备源);拉不到 / 解不出 → `None`。
    ⚠ **本类不做任何"谁赢"的判定**:七项校验与冲突判定住 `neckline/auction/quality.py`
    (`sentinel/` 是「纯规则、零 LLM」的包,而"哪一源可用"是竞价层的语义)。
    """

    code: str
    primary: Optional[Quote] = None
    backup: Optional[Quote] = None

    @property
    def any_quote(self) -> Optional[Quote]:
        """任取一份**存在**的读数(⚠ 不代表它通过了校验 —— 那是 `quality.py` 的事)。"""
        return self.primary if self.primary is not None else self.backup


def get_quotes_dual(
    codes: List[str], transport: Optional[Any] = None
) -> Dict[str, DualQuote]:
    """🔴 **双源批量并行**:新浪一次批量 + 腾讯一次批量,返回**两源逐票原始读数**。

    与 `get_quotes()` 的区别只有一个:那个是「主源失败**才**降备源」(省一次请求),
    这个是「**两源都拉**」—— 因为 K8 §二十 要求对 T1/T2 成员及实际使用的关键基准
    做**有界双源核验**,而核验需要两个可以互相打架的读数。

    🔴 **⛔ 不允许逐票网络请求**(9:26 那一刻的限流面必须可控):仍走既有
    `_CHUNK_SIZE=400` 分块,每块**两次**请求。相对现役竞价抓取(1 次新浪 + 缺票时
    1 次腾讯)**净增 1 次 HTTP 请求 / 早晨**。

    🔴 **`get_quotes()` 行为逐位不变**(单测锁死):本函数是**新增**路径,
    ⛔ 没有改写那一个;普通上下文股票继续走「主源失败才降备源」。

    ⚠ **有界在调用面,不在本函数**:本函数老老实实拉 `codes` 全部,"哪些码值得双源"
    由调用方(`auction/collect.py`)决定 —— ⛔ 这里**不设「取前 N 个」的截断**
    (那需要一个 K8 没给的数,§五 P2.2 明写)。

    任一源全挂 / 单票解析失败 → 那一侧为 `None`,整体不崩(实时源不可靠是常态)。
    """
    if not codes:
        return {}
    sym_to_code: Dict[str, str] = {}
    symbols: List[str] = []
    for c in codes:
        sym = to_symbol(c)
        sym_to_code[sym] = c
        symbols.append(sym)

    primary: Dict[str, Quote] = {}
    backup: Dict[str, Quote] = {}
    for batch in _chunks(symbols, _CHUNK_SIZE):
        sina_raw = _fetch_sina(batch, transport)
        tencent_raw = _fetch_tencent(batch, transport)
        for sym in batch:
            code = sym_to_code[sym]
            sb = sina_raw.get(sym)
            if sb is not None:
                q = _parse_sina(sym, sb)
                if q is not None:
                    primary[code] = q
            tb = tencent_raw.get(sym)
            if tb is not None:
                q = _parse_tencent(sym, tb)
                if q is not None:
                    backup[code] = q

    return {
        sym_to_code[s]: DualQuote(code=sym_to_code[s],
                                  primary=primary.get(sym_to_code[s]),
                                  backup=backup.get(sym_to_code[s]))
        for s in dict.fromkeys(symbols)
    }


__all__ = ["Quote", "DualQuote", "to_symbol", "get_quotes", "get_quote", "get_quotes_dual"]
