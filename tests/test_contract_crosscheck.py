"""**契约三方对拍的机器判据**(plan §五 V2-⑭-C;⑮ 硬清单 Y5 点名「做成机器判据,
别靠人眼扫」)。

对拍的三方:`api/schemas.py` 声明 → `api/app.py` 转发 → Swift `Models.swift` /
`APIClient.swift` 解码。**逐字段对照表**是人读件,落
`archive/对照表/V2_契约三方对拍_20260803.md`;本文件只装**能机器判的那几条**:

1. **客户端调用面 ⊆ 服务端路由面** —— 客户端**任意 Swift 文件**里出现的每一个
   `/api/v1/...` 路径,服务端都必须真有那条路由。**这是 Y5 的防复发闸**:当前仓库
   构建出的客户端对 V2 服务端有 5 处 404 / 静默失败,其中 `PUT /settings/llm`
   **采集了用户的明文 key、发到一个不存在的端点、界面上还是一副成功的样子**。
2. **已知欠账清单是"精确集合"而不是"允许列表"** —— 断言用 `==` 不是 `<=`:
   ⑮ 删掉某一处而忘了更新清单 → 同样红。⛔ 一个只会越放越松的 allowlist 等于没有闸。
3. **新增 404/400 reason 与客户端 `mapReason` 互为闭包** —— 服务端 raise 的每个
   reason 字符串,客户端要么有对应 case,要么在**登记过的**待接线清单里。
   (CLAUDE.md 坑:404 的 fallback 是「持仓已清」,新 reason 不加 case 就会显示成
   那句驴唇不对马嘴的话。)

⚠ **本文件不是 review**:它是施工块内的自查(⑭-C 原文),不等于独立复审。

**V2.1-⑧(2026-08-08)收口的四处**(人读件 = `archive/对照表/V2.1_契约对拍_20260808.md` 三张表):
① 删除面 —— 问询台三条端点进 `test_deleted_v1_endpoints_have_no_server_route`(①  已落);
② 新增面 —— `/review/{overview,handoff}` 两条进路由面自检,④ 的 4 个只读新键两侧对拍;
③ **零新增 reason 的显式断言** —— 拿 V2.0.0 收官快照当分界线,闭包测试守不住的那一半
(「悄悄多一个 reason 但客户端同批加了 case」)由它守;④ `FROZEN_SNAPSHOT_DTOS` 扩容
到 6 项,并把「同前缀类型会切错块」这条人肉纪律做成机器判据。

**小审 🔵-2(2026-08-03)修补的两处结构性盲区**:① `client_call_surface()` 曾只扫
`APIClient.swift` 单文件(依赖"网络层只此一家"的架构惯例,当时靠人眼核实过其余
文件零 `/api` 字面量)——改为扫 `client/` 下**全部** `.swift` 文件,日后任何 View/
Model 直接拼 URL 立刻被机器抓到,不再依赖"人核实过一次就假设永远成立"。②
对拍原本只认路径形状、无 HTTP method 维度——新增
`test_client_call_methods_match_server_route_methods_where_determinable`,
**已知不完整**:只覆盖路径字面量直接传给 `get/post/put/delete(...)` 的调用点
(本文件实测约 36/56 处),调用点若先 `let path = "..."` 再传变量给这四个函数,
正则不解析变量绑定、**不纳入**这条 method 校验(宁可少覆盖一部分,不假装能可靠
解析变量流而产出假阳性)——两条测试合起来仍能抓住"POST 打到 GET-only 路径"这类
在直接字面量调用点上的回归。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CLIENT_DIR = _ROOT / "client"
_API_CLIENT = _ROOT / "client" / "Neckline" / "Networking" / "APIClient.swift"
_MODELS = _ROOT / "client" / "Neckline" / "Networking" / "Models.swift"

# Swift 里的路径字面量:`"/api/v1/positions/\(id)/close"` → `/api/v1/positions/{}/close`
_PATH_LITERAL = re.compile(r'"(/api/v1[^"]*)"')
_INTERPOLATION = re.compile(r"\\\([^)]*\)")
# 只认"字面量直接传给这四个私有请求方法"的调用点(见模块 docstring 🔵-2 第②点的
# 已知不完整声明)——`get(_ path: String, ...)` / `post(_ path: String, body:, ...)` 等。
_METHOD_CALL = re.compile(r'\b(get|post|put|delete)\(\s*"(/api/v1[^"]*)"')


def _normalize(path: str) -> str:
    """插值段归一成 `{}`、去掉查询串。FastAPI 的 `{basket_id}` 也归一成 `{}`,
    客户端代码里的**具体数字示例值**(如测试代码常写的字面量 `"42"`)同样折叠成
    `{}`(🔵-2 小审 2026-08-03:`client/NecklineTests/URLGateTests.swift` 用
    `"/api/v1/positions/42/close"` 断言 URL 拼接——`42` 与 `\\(id)` 插值在服务端
    眼里是同一个路径形状,折叠前会被误判成"多出的调用";已核实服务端真实路由
    没有任何固定的纯数字路径段,折叠不会掩盖真实回归)——对拍的是**路径形状**,
    不是参数名或具体取值(名字/取值不一致不影响 HTTP 能不能打通)。"""
    path = path.split("?", 1)[0]
    path = _INTERPOLATION.sub("{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    path = re.sub(r"/\d+(?=/|$)", "/{}", path)
    return path.rstrip("/") or "/"


def client_call_surface() -> set:
    """客户端**实际会打出去**的路径集合(含已删端点的活调用 —— 那正是本闸要抓的)。
    **🔵-2 小审 2026-08-03 起扫 `client/` 下全部 `.swift` 文件**(含 `NecklineTests/`),
    不再只锚 `APIClient.swift` 单文件(见模块 docstring)。"""
    texts = (p.read_text(encoding="utf-8") for p in sorted(_CLIENT_DIR.rglob("*.swift")))
    return {_normalize(m) for text in texts for m in _PATH_LITERAL.findall(text)}


def server_route_surface() -> set:
    from neckline.api.app import app

    return {_normalize(r.path) for r in app.routes
            if getattr(r, "path", "").startswith("/api/v1")}


def client_call_method_pairs() -> set:
    """客户端**能确定 HTTP method** 的 `(METHOD, 归一化路径)` 对——只覆盖
    `APIClient.swift` 里路径字面量直接传给 `get/post/put/delete(...)` 的调用点
    (模块 docstring 🔵-2 第②点已声明的已知不完整范围)。"""
    text = _API_CLIENT.read_text(encoding="utf-8")
    return {(method.upper(), _normalize(path)) for method, path in _METHOD_CALL.findall(text)}


def server_route_method_pairs() -> dict:
    """`{归一化路径: {服务端支持的 METHOD 集合}}`,只收 `/api/v1` 路由。"""
    from neckline.api.app import app

    out: dict = {}
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/v1"):
            continue
        methods = {m for m in (getattr(r, "methods", None) or set()) if m not in ("HEAD", "OPTIONS")}
        out.setdefault(_normalize(path), set()).update(methods)
    return out


# ⑮ 待删的**已知欠账**(V2 review 契约线 🟡 Y5 点名的五处 + 其对应路径形状)。
#
# ✅ **2026-08-03 V2-⑮ 已全部还清,本集合清空** —— 五处活调用(`PUT /settings/llm` +
# `POST /decisions/{id}/{link,cancel,revise,scenario-outcome}`)连同它们的请求体类型
# (`SettingsLLMRequest` 含**明文 apiKey**)已从 `APIClient.swift` 物理删除。
# ⚠ **空集合不是"闸松了",恰恰相反**:下面那条断言用 `==`,清空之后**任何**新增的
# 「打不到的调用」都会立刻红 —— 这是本闸最严的状态。⛔ 别再往里加条目当豁免用;
# 真要新增一笔债,得先有人在 plan 里登记它。
# ⚠ **2026-08-09 V2.2-⑤-B 曾挂上两笔债(熔断整体退役,用户裁定 #8)**:服务端先删了
# `GET /api/v1/circuit` 与 `POST /api/v1/circuit/unlock`,而客户端那两条活调用归 ⑥ 删。
# ✅ **2026-08-09 V2.2-⑥ 已还清,本集合重新清空** —— `APIClient.swift` 里
# `getCircuit()` / `unlockCircuit()`(连同只服务于后者的 `EmptyBody`)已物理删除,
# 客户端调用面重新 ⊆ 服务端路由面。**空集合 = 本闸最严的状态**(下面那条断言用 `==`)。
PENDING_CLIENT_CALLS_TO_BE_REMOVED_IN_15: set = set()


def test_client_call_surface_is_subset_of_server_routes_modulo_registered_debt():
    """**客户端调用面 ⊆ 服务端路由面**(⑮ 硬清单要求的机器判据)。

    差集必须**恰好等于**已登记的 ⑮ 欠账集合:
      · 多出来 = 新增了打不到的调用(回归)。
      · 少了   = ⑮ 删干净了但清单没更新(该收债了)。
    """
    missing = client_call_surface() - server_route_surface()
    assert missing == PENDING_CLIENT_CALLS_TO_BE_REMOVED_IN_15, (
        f"客户端调用面与服务端路由面不闭合。\n"
        f"  多出的(新回归,必须修):{sorted(missing - PENDING_CLIENT_CALLS_TO_BE_REMOVED_IN_15)}\n"
        f"  清单里有但代码已无(⑮ 已还债,请同步删清单):"
        f"{sorted(PENDING_CLIENT_CALLS_TO_BE_REMOVED_IN_15 - missing)}"
    )


def test_registered_debt_entries_are_really_still_in_the_client():
    """反向守门:清单里的每一条**必须真的还在客户端代码里**。
    防的是"债还完了、清单没删",那会让上面那条断言变成一句空话。"""
    surface = client_call_surface()
    stale = PENDING_CLIENT_CALLS_TO_BE_REMOVED_IN_15 - surface
    assert not stale, f"这些欠账在客户端已不存在,请从清单里删掉:{sorted(stale)}"


def test_client_call_methods_match_server_route_methods_where_determinable():
    """HTTP method 维度对拍(🔵-2 小审 2026-08-03,⑭-C 遗留盲区③的部分修补)——
    路径对得上不代表方法对得上("POST 打到 GET-only 路径"这类回归,原闸看不见)。

    ⚠ **已知不完整**(模块 docstring 已声明):只覆盖 `client_call_method_pairs()`
    能确定 method 的那部分调用点;路径层面的缺失已由
    `test_client_call_surface_is_subset_of_server_routes_modulo_registered_debt`
    抓,这里只管"路径两边都认、但 method 对不上"这一种新增维度。"""
    server_methods = server_route_method_pairs()
    bad = []
    for method, path in sorted(client_call_method_pairs()):
        allowed = server_methods.get(path)
        if allowed is None:
            continue   # 路径本身缺失不归本条测试管
        if method not in allowed:
            bad.append((method, path, sorted(allowed)))
    assert not bad, (
        f"客户端用了服务端该路径不支持的 HTTP method(method, 客户端路径, 服务端允许的 method):"
        f"{bad}"
    )


def test_new_v2_endpoints_are_reachable_shapes():
    """⑭-B 定稿的新端点必须真的挂上了(**路由面自检**;逐字段契约见 archive 对照表)。"""
    server = server_route_surface()
    for path in (
        "/api/v1/baskets",
        "/api/v1/baskets/{}",
        "/api/v1/baskets/{}/card",
        "/api/v1/baskets/{}/verification",
        "/api/v1/baskets/{}/review",
        "/api/v1/positions/{}/plans",
        "/api/v1/positions/{}/entry-snapshot",
        "/api/v1/profile/preference",
        "/api/v1/profile/capability",
        "/api/v1/packs",
        "/api/v1/packs/{}",
        "/api/v1/eval/weekly",
        "/api/v1/alerts",
        "/api/v1/alerts/{}",
        "/api/v1/alerts/parse",
    ):
        assert path in server, f"⑭-B 清单里的端点 {path} 没挂上"


def test_new_v21_endpoints_are_reachable_shapes():
    """**V2.1-⑤ 新增的两条复盘聚合端点必须真的挂上了**(路由面自检;⑧ 对拍表「新增面」
    第 1 行的机器判据)。

    ⚠ 这两条端点**一律不 404**(空态走 `available=false`),所以路由面在不在是它们
    唯一能被机器判的存在性判据 —— 少挂一条,客户端复盘板块的「累计」页会拿到
    `mapReason` 的 404 fallback 文案,那是驴唇不对马嘴的「持仓已清」(v1.4 `watchlist`
    案底),而不是「产物尚未生成」。"""
    server = server_route_surface()
    for path in ("/api/v1/review/overview", "/api/v1/review/handoff"):
        assert path in server, f"V2.1-⑤ 的端点 {path} 没挂上"


def test_deleted_v1_endpoints_have_no_server_route():
    """⑬ 删掉的十个端点 + `PUT /settings/llm` + V2.1-① 删掉的问询台三条,
    服务端零残留(路由面判据)。"""
    server = server_route_surface()
    for gone in (
        "/api/v1/watchlist", "/api/v1/watchlist/{}", "/api/v1/watchlist/reconcile-ths",
        "/api/v1/watchlist/export-ths", "/api/v1/breathing", "/api/v1/breathing/{}",
        "/api/v1/settings/intel-boards", "/api/v1/settings/llm",
        "/api/v1/decisions/{}/link", "/api/v1/decisions/{}/cancel",
        "/api/v1/decisions/{}/revise", "/api/v1/decisions/{}/scenario-outcome",
        # V2.1-①:问询台整链退役
        "/api/v1/inquiry", "/api/v1/inquiries", "/api/v1/inquiries/{}",
    ):
        assert gone not in server, f"{gone} 应已删除,服务端不该还有这条路由"


# —— reason 闭包(⑭-B:404/400 的 reason 与 `mapReason` 互为闭包)————————————

# 服务端 raise 的全部 reason 字符串(逐个从 `app.py` 抠出来,**新增会返 404/400 的
# 端点必须同步补进本集合** —— 这条测试本身就是那份检查清单)。
SERVER_REASONS = {
    "not_holding", "not_found", "not_trading_day", "future_buy_date",
    "report_not_found", "code_not_in_report", "already_exists",
    "invalid_task", "invalid_push_kinds", "invalid_rule", "duplicate_alert",
    # V2-⑭-B 新增三个
    "basket_not_found", "card_not_ready", "no_base_plan",
    # B1(2026-08-04 planner 裁定,小审 🔵 B-3):唯一一个**用 500 承载**的业务 reason
    # ——「卡有行但读不出」。⚠ 本集合的名字叫「服务端 reason」不叫「404 reason」,
    # 状态码不是它的判据;客户端 `send()` 的 500 分支已接进 `mapReason`,闭包照旧成立。
    "card_corrupt",
    # V2.3.3-⑤ D1 集合竞价确认层(K8.md §二十)新增两个,**照 B1 的三态分派**:
    # 404「当日无行 = 竞价层没跑过」/ 500「有行但读不出」。⛔ 两者必须分开 ——
    # 合并 = 客户端永远重试、永远显示"还没生成",而报告是冻结件、坏了不会自己好。
    "auction_not_ready", "auction_corrupt",
}

# ⑮ 待加 `mapReason` case 的字符串。
#
# ✅ **2026-08-03 V2-⑮ 已全部还清,本集合清空**:八个 reason 各建了独立 `APIError`
# case —— 三个 ⑭-B 全新 reason(`basket_not_found` / `card_not_ready` / `no_base_plan`)
# + 五个 409/422 reason(`already_exists` / `duplicate_alert` / `invalid_rule` /
# `invalid_task` / `invalid_push_kinds`),并把 `send()` 的 409 / 422 两个分支也接进
# `mapReason`(此前只有 400/404 走它)。
# 🔴 其中 `card_not_ready` 最要紧:404 的 fallback 是 `.notHolding`「持仓已清」——
# 不建 case,用户点开一个卡还没生成的篮子会看到「持仓已清」(v1.4 `watchlist` 有案底)。
PENDING_MAP_REASON_CASES_FOR_15: set = set()


def _server_reason_literals() -> set:
    text = (_ROOT / "neckline" / "api" / "app.py").read_text(encoding="utf-8")
    return set(re.findall(r'"reason":\s*"([a-z_]+)"', text)) | set(
        re.findall(r'REASON_[A-Z_]+\s*=\s*"([a-z_]+)"', text))


def test_server_reason_inventory_is_complete():
    """服务端真的 raise 的 reason 不许悄悄多出一个没登记的 —— 那正是「新增会返 404
    的端点忘了检查 `mapReason`」这个坑的入口。"""
    found = _server_reason_literals()
    unregistered = found - SERVER_REASONS
    assert not unregistered, (
        f"这些 reason 在 app.py 里出现但没登记进 SERVER_REASONS:{sorted(unregistered)}。"
        f"新增会返 404/400 的端点,必须同时检查客户端 `mapReason` 要不要加新 case"
        f"(⛔ 复用已有字符串不算'没加',全新字符串才需要新 case)。"
    )


def test_map_reason_covers_every_server_reason_modulo_registered_debt():
    """`mapReason` 的 case 集合 ∪ 已登记欠账 ⊇ 服务端 reason 集合。

    差集必须**恰好等于**已登记的 ⑮ 欠账 —— 同上,`==` 不是 `<=`。"""
    text = _API_CLIENT.read_text(encoding="utf-8")
    body = text.split("private func mapReason(", 1)[1].split("\n    }", 1)[0]
    cases = set(re.findall(r'case\s+"([a-z_]+)"', body))
    uncovered = SERVER_REASONS - cases
    assert uncovered == PENDING_MAP_REASON_CASES_FOR_15, (
        f"`mapReason` 与服务端 reason 面不闭合。\n"
        f"  没 case 也没登记(必须修):{sorted(uncovered - PENDING_MAP_REASON_CASES_FOR_15)}\n"
        f"  登记了但其实已有 case(请从清单删):"
        f"{sorted(PENDING_MAP_REASON_CASES_FOR_15 - uncovered)}"
    )


# **V2.0.0 收官时(commit `352f235`)的 reason 面快照**,逐字节抄自当时的
# `SERVER_REASONS`。V2.1 的对拍结论是 **零新增 reason**(①:删的三条端点本就不 raise
# 业务 404;⑤:两条新端点一律不 404、空态走 `available=false`)—— 这份快照就是那句
# 结论的**机器判据**。
_V20_REASON_SURFACE = frozenset({
    "not_holding", "not_found", "not_trading_day", "future_buy_date",
    "report_not_found", "code_not_in_report", "already_exists",
    "invalid_task", "invalid_push_kinds", "invalid_rule", "duplicate_alert",
    "basket_not_found", "card_not_ready", "no_base_plan", "card_corrupt",
})


# 🔴 **V2.3.3 起的新分界线**(2026-08-11):V2.1 / V2.2 / V2.3.x 一路「零新增 reason」,
# 到 V2.3.3-⑤ 因为新端点 `GET /auction` **第一次**动了 reason 面 —— 加的两条按 B1
# 三态分派(404 没跑过 / 500 读不出)。
# ⚠ 本条不是"把老断言删掉",而是**把分界线往前挪一格**:老快照仍在(上面
# `_V20_REASON_SURFACE`),新增面**逐条枚举**,任何计划外的第三条照样会红。
_V233_ADDED_REASONS = frozenset({"auction_not_ready", "auction_corrupt"})


def test_reason_surface_equals_v20_snapshot_plus_the_declared_v233_additions():
    """🔴 **reason 面只许按登记增长**(原「V2.1 零新增 reason」那条的接任者)。

    上面两条闭包测试只保证「服务端 reason ↔ 客户端 `mapReason`」两侧对得上;它们
    **不会**因为悄悄多引入一个 reason 而红(只要客户端同批加了 case 就仍闭合)。
    本条锁的是另一件事:**新增了哪几条必须是写下来的那几条** —— 它也是「老客户端
    装着不换包会不会看到错话」这条在线升级前提的依据(新增 reason = 老客户端吃
    fallback,必须是**有意**为之)。

    ⚠ 再新增,正确做法仍是**同时**更新 `SERVER_REASONS`、客户端 `mapReason` **和**
    这里的新增清单(把分界线再挪一格),⛔ 不是把本条删掉。
    """
    expected = set(_V20_REASON_SURFACE) | set(_V233_ADDED_REASONS)
    assert SERVER_REASONS == expected, (
        "reason 面与「V2.0.0 快照 + 已登记的 V2.3.3 新增」不一致。\n"
        f"  多出来的(没登记就加 = 老客户端会吃 fallback 文案):"
        f"{sorted(SERVER_REASONS - expected)}\n"
        f"  少掉的(⛔ 删 reason = 老客户端的 case 变死码,更要当心):"
        f"{sorted(expected - SERVER_REASONS)}"
    )
    # V2.0.0 那份快照本身**一条都不许少**(新增是往上加,不是换一批)。
    assert set(_V20_REASON_SURFACE) <= SERVER_REASONS
    # reason 侧闭包必须处在**最严状态**(欠账清单为空)才谈得上「零新增 reason」被守住。
    assert not PENDING_MAP_REASON_CASES_FOR_15
    # ⚠ **路由侧欠账清单 V2.2-⑤-B 曾非空(那两条熔断端点),⑥ 已还清 → 重新清空**。
    # 这里与 reason 侧刻意**拆成两句**而不是写在同一句 `and` 里:捆成一个判据的话,
    # 一旦路由侧挂债,这条「零新增 reason」的断言就会因为**跟 reason 无关**的原因变红,
    # 人只会把它当噪音关掉。⛔ 别再合回去。
    assert PENDING_CLIENT_CALLS_TO_BE_REMOVED_IN_15 == set(), (
        "路由侧欠账清单必须是空的:V2.2-⑤-B 登记的那两条熔断端点已由 ⑥ 从客户端删净。"
        "⛔ 想往里加新条目,先在 plan 里登记那笔债。"
    )


# —— V2.1 契约新增面:4 个只读新键(⑧ 对拍表「新增面」的机器判据)——————————

# `(pydantic 模型, 客户端 struct, A 类/B 类)`。**两条路刻意只填各自那一处**
# (④ 完工记录):live 路径的分数住 `tierHistory`,快照路径住 `basket` 自己;
# 客户端读法收口成 `basket.scorePercent ?? basket.tierHistory?.scorePercent`。
_V21_NEW_KEY_SITES = (
    ("TierOut", "Tier", "A"),       # 每次响应重拼
    ("BasketOut", "Basket", "B"),   # 随报告冻住
)
_V21_NEW_KEYS = ("scorePercent", "scoreContributions")


@pytest.mark.parametrize("model,struct,kind", _V21_NEW_KEY_SITES)
def test_v21_new_score_keys_are_declared_on_both_sides(model: str, struct: str, kind: str):
    """V2.1-④ 的 4 个只读新键(2 模型 × 2 键)必须**服务端声明了、客户端也解得出**。

    这条抓的是「服务端加了键、客户端没跟」以及反过来的那半边 —— 打分卡是纯展示层,
    键掉了不会报错,只会**静默显示不出分数**(而"没分"本身是个合法状态,⛔ 看不出
    是 bug)。"""
    schemas = (_ROOT / "neckline" / "api" / "schemas.py").read_text(encoding="utf-8")
    server_block = schemas.split(f"class {model}(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    client_block = _dto_body(struct)
    for key in _V21_NEW_KEYS:
        assert f"{key}:" in server_block, f"服务端 `{model}` 没有声明 `{key}`"
        assert key in client_block, f"客户端 `{struct}` 没有解 `{key}`"
    # 元素类型必须两侧都在(它不计入"键"数,但键没有它就是空壳)
    assert "class ScoreContribOut(BaseModel):" in schemas
    assert "struct ScoreContribution" in _MODELS.read_text(encoding="utf-8")


# —— 冻结快照类 DTO 的解码姿势(CLAUDE.md「落库快照两类论」)——————————————

# **写入当时冻住**的那一类:服务端升级永远不会给老快照补新键 → 客户端必须手写
# `init(from:)` 全字段 `decodeIfPresent` 兜底。⛔ 合成 `Codable` 的后果是「装了新
# App 的用户翻几周前的老卡 → 整张卡解不出」。
#
# **V2.1-⑧ 扩容三项**(④ 的两个新键正是随报告冻住的那一类,清单里却一直没有它们的家):
# `Basket` / `BasketDaily` 解的是 `reports.basket_daily_json`(**冻结快照**,老报告永远
# 不会被服务端补上 `scorePercent`),`ScoreContribution` 是这两键的元素类型、同样落在
# 快照里。⚠ 它们在 V2.1 之前就是 B 类,只是当时清单只收了三个"最痛"的;本次一并收编。
FROZEN_SNAPSHOT_DTOS = (
    "BasketCard", "BasketReview", "ReviewWeeklyResult",
    "Basket", "BasketDaily", "ScoreContribution",
    # —— V2.2-⑥ 扩容三项(⑥ 表格「`FROZEN_SNAPSHOT_DTOS` 扩容」的落点)——
    # `SelectionClock` 载的是 `selection_clock.mech_json`(D1 九项验证,**结案即冻**,
    # `INSERT OR IGNORE` 永不覆盖);`TradeClock` 载 `trade_clock.final_json`(结案八项);
    # `TradeClockEvent` 载 append-only 流水的 `mech_json`。三者都**不随服务端升级补键**
    # → 合成 `Codable` 会让「装了新 App 的用户翻几周前的老结案件」整条解不出。
    # ⚠ 同前缀陷阱在这里是真的:`SelectionClock` 之外还有 `SelectionClocksResponse`
    # (在 `APIClient.swift`,不在本切块器的扫描域)、`TradeClock` 之外还有
    # `TradeClockEvent` / `TradeClockNoteResult` —— 切块器用 `^struct <Name>\b`,
    # `\b` 保证不会切到同前缀邻居(下面 `test_dto_slicer_...` 是这条的守门)。
    "SelectionClock", "TradeClock", "TradeClockEvent",
    # —— V2.3.2-②-B:③b 的第二类行(股票级 OUT),随 `basketDaily` 冻结快照走 ——
    "OutCandidate",
    # —— V2.3.3-⑤ 竞价确认层七件 ——
    # **真 B 类两件**:`AuctionVerdict` 解的是 `auction_verdicts` 的 json 列、
    # `AuctionMemberRow` 解的是其中的 `members_json` —— 都是**写入当时冻住**的行,
    # 服务端升级永远不会给它们补新键。
    # 其余五件是每次响应重拼的 A 类,手写 `init(from:)` 是**白拿的保险**
    # (CLAUDE.md:A 类手写不亏);一并收进本闸,省得将来有人把某一件改回合成 Codable。
    # ⚠ 七个名字**两两不互为前缀**(切块器按 `^struct <Name>\b` 定位)。
    "AuctionVerdict", "AuctionMemberRow", "AuctionPayload", "AuctionDataStatus",
    "AuctionMarketOverview", "AuctionRiskItem", "AuctionIndexGap",
)

# ⚠ **同前缀陷阱**(CLAUDE.md 明写的坑):`Models.swift` 里 `struct BasketEvidence`
# (294 行)排在 `struct Basket`(811 行)**之前**,老写法 `models.split("struct Basket")`
# 会切到 `BasketEvidence` 的块上 —— 断言照样绿,守的却是另一个类型。故本文件不再用
# 裸 `split`,改用**行首 + 词边界**定位 + 下一个顶层声明收尾。
_TOP_LEVEL_DECL = re.compile(r"^(?:struct|enum|final class|class|extension|protocol)\s", re.M)


def _dto_body(name: str) -> str:
    """取 `Models.swift` 里 `struct <name>` **这一个**类型的源码块(到下一个顶层声明为止)。"""
    models = _MODELS.read_text(encoding="utf-8")
    m = re.search(rf"^struct {re.escape(name)}\b", models, re.M)
    if m is None:
        return ""
    rest = models[m.end():]
    nxt = _TOP_LEVEL_DECL.search(rest)
    return models[m.start():m.end() + (nxt.start() if nxt else len(rest))]


def test_dto_slicer_is_not_fooled_by_same_prefix_types():
    """本闸自己的守门:切块必须切到**同名那一个**类型,而不是同前缀的邻居。

    `Basket` 之前有 `BasketEvidence`/`BasketCard`/`BasketMember` 等一票同前缀类型 ——
    这条测试就是让「别在 B 类 DTO 前面放同前缀类型」那条人肉纪律**变成机器判据**
    (纪律靠人记就总有忘的一天;切错块的后果是**绿灯守着错的类型**)。"""
    body = _dto_body("Basket")
    assert body.startswith("struct Basket:") or body.startswith("struct Basket "), body[:40]
    assert "struct BasketEvidence" not in body and "struct BasketCard" not in body
    # V2.2-⑥ 新的同前缀族:`TradeClock` 前面就摆着 `TradeClockEvent`。
    tc = _dto_body("TradeClock")
    assert tc.startswith("struct TradeClock:") or tc.startswith("struct TradeClock "), tc[:40]
    assert "struct TradeClockEvent" not in tc and "struct TradeClockNoteResult" not in tc
    assert _dto_body("NoSuchDTOAnywhere") == ""


@pytest.mark.parametrize("name", FROZEN_SNAPSHOT_DTOS)
def test_frozen_snapshot_dtos_hand_write_init_from_decoder(name: str):
    """⑮ 落地后本条才会真正生效;在那之前,尚不存在的 DTO **跳过并说明原因**
    (⛔ 不静默通过 —— 那会让这条闸在最需要它的那一刻是空的)。"""
    body = _dto_body(name)
    if not body:
        pytest.skip(f"`{name}` 尚未在客户端定义(⑮ 的活);本闸在 ⑮ 落地后生效")
    assert "init(from decoder: Decoder)" in body, (
        f"`{name}` 是**写入当时冻住**的历史快照类 DTO,必须手写 `init(from:)` + 全字段 "
        f"`decodeIfPresent` 兜底(CLAUDE.md 落库快照两类论);合成 Codable 会让老快照解不出。"
    )
    assert "decodeIfPresent" in body, f"`{name}` 的 `init(from:)` 里没有 `decodeIfPresent` 兜底"


# —— V2.2-③-C 位置关三键(🔴 用户裁定 #11)的两侧声明 ——————————————————————

# 服务端本版**新增**的成员级键(全部可选)。⚠ **零删键纪律(〇b-3)在这里没有被
# 违反,因为没有键被删**:上一版曾计划发的 `landingState`(落地四态枚举)**一天都
# 没上过产**(V2.2 批 2 未部署,生产客户端从未见过它),故直接换成这三键,⛔ 不走
# 「先改客户端 decodeIfPresent → 下一版服务端才删」两步。**换得成的前提是那个键从未
# 上产** —— 后人别据此以为可以随手换键。
_V22_POSITION_KEYS = ("positionVerdict", "positionReason", "positionMetrics")
# 🔴 裁定 #12 的核心关三键(与位置三键**同构但独立**:一个问「位置对不对」、
# 一个问「是不是那一群的龙头」)。同样是纯新增、零删键 —— 上一版的
# `leader_rs_rank ≤ 3` 从未以任何键发到过客户端。
_V22_CORE_KEYS = ("coreVerdict", "coreReason", "coreMetrics")

# V2.2-③ 在**篮子卡**上新增的全部键(引擎三件套由 ③-E 加、位置三件套由 ③-C 加、
# 核心三件套由 ③-C2 加)。客户端侧统一归 **⑥ 契约与客户端** 那一块落地,本块只做
# 服务端半边 —— 故下面那条测试守的是「要么一个都没接、要么全接」,⛔ 不是「必须已接」。
_V22_CARD_MEMBER_KEYS = _V22_POSITION_KEYS + _V22_CORE_KEYS
_V22_CARD_TOP_KEYS = ("engineCode", "engineVersion", "skeletonVersion")


def test_server_declares_the_position_gate_keys_and_the_converter_maps_them():
    """位置关三键必须**服务端 DTO 声明了**且 **snake→camel 唯一转换点也映射了** ——
    少任何一头的后果都是静默的:卡上没有位置判定,而"没有位置判定"本身看起来
    像个合法状态(⛔ 看不出是 bug)。"""
    schemas = (_ROOT / "neckline" / "api" / "schemas.py").read_text(encoding="utf-8")
    block = schemas.split("class BasketMemberOut(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    for key in _V22_POSITION_KEYS:
        assert f"{key}:" in block, f"服务端 `BasketMemberOut` 没有声明 `{key}`"
    # 冻结卡的 snake 键 → 契约 camel 键,唯一转换点(⛔ API 层不另写一份)
    from neckline.report.basket_daily import card_member_to_public_dict as member_to_public_dict

    snake = {"ts_code": "600001.SH", "position_verdict": "weak",
             "position_reason": "支撑刚破又收回", "position_metrics": {"platform_days": 12}}
    out = member_to_public_dict(snake)
    assert out["positionVerdict"] == "weak"
    assert out["positionReason"] == "支撑刚破又收回"
    assert out["positionMetrics"] == {"platform_days": 12}
    # 老卡缺键 → 该键**不出现**(⛔ 不是补 null:「这一版卡没有这个概念」≠「有但为空」)
    assert set(_V22_POSITION_KEYS) & set(member_to_public_dict({"ts_code": "x"})) == set()


def test_server_declares_the_core_gate_keys_and_the_converter_maps_them():
    """🔴 裁定 #12 的契约侧同款守门:核心关三键必须**服务端 DTO 声明了**且
    **snake→camel 唯一转换点也映射了**。少任何一头的后果同样是静默的:卡上没有
    核心判定,而"没有核心判定"看起来像个合法状态(⛔ 看不出是 bug)。"""
    schemas = (_ROOT / "neckline" / "api" / "schemas.py").read_text(encoding="utf-8")
    block = schemas.split("class BasketMemberOut(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    for key in _V22_CORE_KEYS:
        assert f"{key}:" in block, f"服务端 `BasketMemberOut` 没有声明 `{key}`"
    from neckline.report.basket_daily import card_member_to_public_dict as member_to_public_dict

    snake = {"ts_code": "600001.SH", "core_verdict": "unfit",
             "core_reason": "行业内 30/42,是跟风",
             "core_metrics": {"industry_member_count": 42}}
    out = member_to_public_dict(snake)
    assert out["coreVerdict"] == "unfit"
    assert out["coreReason"] == "行业内 30/42,是跟风"
    # 🔴 分母必须原样透传到契约面(客户端要靠它把「第 3 名」读成 3/42)
    assert out["coreMetrics"] == {"industry_member_count": 42}
    assert set(_V22_CORE_KEYS) & set(member_to_public_dict({"ts_code": "x"})) == set()


def _strip_comments(text: str, markers: tuple = ("#", "//")) -> str:
    """剥掉整行注释再判。**这不是洁癖,是 CLAUDE.md 登记过的坑**:`npm-custom-http.conf`
    的护栏注释里写着它自己要防的那个域名,裸 grep 每次都红 —— **一个对自己的注释
    报警的闸门等于没有闸门**。本文件同款:`schemas.py` 里解释「为什么 `landingState`
    可以直接换掉」的那段注释,自己就带着这个词。"""
    keep = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if any(stripped.startswith(m) for m in markers):
            continue
        keep.append(line)
    return "\n".join(keep)


def test_landing_state_key_never_reaches_the_contract():
    """🔴 裁定 #11 的反向守门:作废的 `landingState`(四态枚举)⛔ 不许出现在契约
    任何一侧的**代码**里 —— 机械四态已整体删除,发一个"态"出去等于把被推翻的东西
    又讲了一遍。(注释里提它是**必要的**留痕,故先剥注释再判,见 `_strip_comments`。)"""
    schemas = (_ROOT / "neckline" / "api" / "schemas.py").read_text(encoding="utf-8")
    assert "landingState" not in _strip_comments(schemas)
    assert "landingState" not in _strip_comments(_MODELS.read_text(encoding="utf-8"))
    # 闸自己的守门:剥注释这一步不许把真声明也剥掉
    assert "positionVerdict:" in _strip_comments(schemas)


def test_v22_card_keys_are_ported_to_the_client_all_or_nothing():
    """**客户端半边归 ⑥ 那一块**(本块只做服务端)。这条不要求"已接",要求的是
    **别接一半**:引擎三件套与位置三件套同属 V2.2-③ 的卡形状(同一个
    `basket_card_v3`),接了其中任意一个就必须六个全接 —— 半份移植的后果是卡上
    某几格永远空着,而空着看起来像"这张卡没有这个数",⛔ 看不出是漏接。"""
    models = _MODELS.read_text(encoding="utf-8")
    keys = _V22_CARD_TOP_KEYS + _V22_CARD_MEMBER_KEYS
    present = [k for k in keys if k in models]
    assert present == [] or len(present) == len(keys), (
        f"V2.2-③ 的卡新增键只接了一部分:已接 {present},缺 "
        f"{[k for k in keys if k not in present]}(⑥ 落地时请一次接齐)"
    )


# —— V2.2-⑥ 客户端半边落地后的三条机器判据 ————————————————————————————————
#
# 前面那条 `test_v22_card_keys_are_ported_to_the_client_all_or_nothing` 守的是
# 「别接一半」;⑥ 完工后下面这三条守的是「接对了」。

# ③b 原因码:**唯一源 = 服务端 `report/basket_daily.py::DROPPED_REASON_LABEL`**。
# 客户端有一份中文短句镜像(界面一行要放得下,服务端那份带括号补充给 markdown 报告用)
# —— 两份**内容刻意不同、码必须相同**。⚠ 码漏一个的后果是静默的:界面上直接印英文
# 原因码,而"看不懂的码"不像 bug、只像"系统在说黑话"。
def test_client_maps_every_dropped_reason_code_the_server_can_emit():
    from neckline.report.basket_daily import DROPPED_REASON_LABEL

    body = _MODELS.read_text(encoding="utf-8")
    block = body.split("func nkDroppedReasonLabel(", 1)[1].split("\nfunc ", 1)[0]
    missing = [code for code in DROPPED_REASON_LABEL if f'case "{code}"' not in block]
    assert not missing, (
        f"客户端 `nkDroppedReasonLabel` 缺这些服务端会发的原因码:{sorted(missing)} —— "
        f"缺了就会在 ③b 列表里直接印英文码。⛔ 别指望 default 分支兜(它是原样透传)。"
    )


# 六关码 + 机械关 / 证据关二分:**唯一源 = 服务端 `selection/gates.py`**。
# ⛔ 二分错了不是配色问题:机械关**硬否决**、证据关**只降级**,混起来就是把
# 「这篮没了」和「这篮扣了一档」讲成同一件事(裁定 #6 / #11 / #12)。
def test_client_gate_split_matches_the_server_constants():
    from neckline.selection.gates import EVIDENCE_GATES, GATE_ORDER, MECH_GATES

    body = _MODELS.read_text(encoding="utf-8")
    order_line = body.split("let nkGateOrder: [String] = ", 1)[1].split("\n", 1)[0]
    for g in GATE_ORDER:
        assert f'"{g}"' in order_line, f"客户端 `nkGateOrder` 少了关口 `{g}`"
    assert order_line.index('"' + GATE_ORDER[0] + '"') < order_line.index('"' + GATE_ORDER[-1] + '"'), (
        "客户端 `nkGateOrder` 的顺序与服务端 `GATE_ORDER` 不同 —— 灯条顺序是管线顺序,不是字典序"
    )
    kind_block = body.split("func nkGateKind(", 1)[1].split("\nfunc ", 1)[0]
    mech_line = [ln for ln in kind_block.splitlines() if "return .mechanical" in ln]
    ev_line = [ln for ln in kind_block.splitlines() if "return .evidence" in ln]
    assert len(mech_line) == 1 and len(ev_line) == 1, "二分应各只有一条 case 行,便于机器核对"
    for g in MECH_GATES:
        assert f'"{g}"' in mech_line[0], f"`{g}` 在服务端是**机械关(硬否决)**,客户端没归对"
    for g in EVIDENCE_GATES:
        assert f'"{g}"' in ev_line[0], f"`{g}` 在服务端是**证据关(只降级)**,客户端没归对"
    # 反向:机械关不许混进证据关那一行(反之亦然)。
    for g in MECH_GATES:
        assert f'"{g}"' not in ev_line[0]
    for g in EVIDENCE_GATES:
        assert f'"{g}"' not in mech_line[0]


# 用户主观说明的长度上界:**领域层是唯一权威**(`review/trade_clock.USER_NOTE_MAX_CHARS`),
# 契约层已经靠 import 同源(`schemas.py`),客户端**只能抄一份数**(Swift 读不到 Python 常量)。
# 🔴 这条把那份抄写钉成机器判据:不同步 = 静默出现「客户端说能写、服务端说太长」——
# 用户写满 500 字点提交,拿到一个英文 422,而界面上的计数器一路是绿的。
def test_client_note_limit_mirrors_the_single_source():
    from neckline.review.trade_clock import USER_NOTE_MAX_CHARS

    body = _MODELS.read_text(encoding="utf-8")
    line = body.split("let nkTradeNoteMaxChars: Int = ", 1)[1].split("\n", 1)[0].strip()
    assert line == str(USER_NOTE_MAX_CHARS), (
        f"客户端 `nkTradeNoteMaxChars` = {line},服务端 `USER_NOTE_MAX_CHARS` = "
        f"{USER_NOTE_MAX_CHARS} —— 两处必须相等(唯一源在领域层,客户端那份只是镜像)。"
    )


# —— V2.3.2-⑤ 退出字段语义(K8.md §十九):契约**只加不删**的三处机器判据 ——————————
#
# 🔴 少任何一头的后果都是静默的:界面上仍写着「止损线」,而用户的章程早已改成
# 「亏损警戒 + 离场决策在你」—— 那是**系统替一版没说过这话的章程发言**,⛔ 看不出是 bug。
_LOSS_WARNING_KEYS = ("lossWarningPct", "lossWarningAction")


def test_server_declares_loss_warning_on_both_stop_line_endpoints():
    """`stopLine` 出现在哪两个 DTO 上,这两键就必须出现在哪两个 DTO 上
    (§五 V2.3.2 ⑤-B 第 4 条「`stopLine` 那两处同步补一句文案」的机器判据)。"""
    schemas = _strip_comments(
        (_ROOT / "neckline" / "api" / "schemas.py").read_text(encoding="utf-8"))
    for cls in ("class PositionOut(BaseModel):", "class EntrySuggestionOut(BaseModel):"):
        block = schemas.split(cls, 1)[1].split("\nclass ", 1)[0]
        assert "stopLine:" in block, f"{cls} 里找不到 `stopLine` —— 这条守门锚错了地方"
        for key in _LOSS_WARNING_KEYS:
            assert f"{key}:" in block, f"服务端 `{cls}` 没有声明 `{key}`"


def test_card_fingerprint_converter_maps_loss_warning_and_keeps_stop_pct():
    """冻结卡指纹 snake→camel 的**唯一转换点**必须带这两键,且 `stopPct` **不许删**
    (两步淘汰第一步:本版只加键;服务端删键要等下一版客户端先改可选,CLAUDE.md 铁律)。"""
    from neckline.report.basket_daily import _CARD_FINGERPRINT_KEYS, card_to_public_dict

    m = dict(_CARD_FINGERPRINT_KEYS)
    assert m["stop_pct"] == "stopPct", "⛔ 本版不许删 `stopPct`"
    assert m["loss_warning_pct"] == "lossWarningPct"
    assert m["loss_warning_action"] == "lossWarningAction"
    out = card_to_public_dict({"fingerprint": {
        "stop_pct": 0.05, "loss_warning_pct": 0.05, "loss_warning_action": "review"}})
    assert out["fingerprint"] == {
        "stopPct": 0.05, "lossWarningPct": 0.05, "lossWarningAction": "review"}
    # 老卡缺键 → 该键**不出现**(⛔ 不是补 null:「这一版卡没有这个概念」≠「有但为空」)
    assert card_to_public_dict({"fingerprint": {"stop_pct": 0.05}})["fingerprint"] == {"stopPct": 0.05}


def test_client_declares_loss_warning_on_every_dto_that_carries_stop_line():
    """客户端半边:凡带 `stopLine` / 止损比例的 DTO 都要能读出**这条线现在是什么**。

    ⚠ `lossWarningAction` 三处都要(它是**称呼与披露的判据**);`lossWarningPct` 只在
    要显示那个百分数的两处(`Position` 的披露句、`BasketFingerprint` 的口径指纹)——
    `EntrySuggestionRange` 只换称呼、不印比例,**刻意不接**,⛔ 别为了对称加一个没人读的字段。
    三处一律**可选属性**(老服务端 / 老卡缺键 → nil,⛔ 不炸)。"""
    models = _MODELS.read_text(encoding="utf-8")
    for owner in ("struct Position:", "struct EntrySuggestionRange", "struct BasketFingerprint"):
        block = models.split(owner, 1)[1].split("\n}\n", 1)[0]
        assert "var lossWarningAction: String? = nil" in block, f"{owner} 少了 lossWarningAction"
    assert "var lossWarningPct: Double? = nil" in models
    # 展示层换算走单一判据(⛔ 别在视图里各写一份 `== "review"`)
    assert 'var isLossWarningCharter: Bool { lossWarningAction == "review" }' in models
