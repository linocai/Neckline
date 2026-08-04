"""**契约三方对拍的机器判据**(plan §五 V2-⑭-C;⑮ 硬清单 Y5 点名「做成机器判据,
别靠人眼扫」)。

对拍的三方:`api/schemas.py` 声明 → `api/app.py` 转发 → Swift `Models.swift` /
`APIClient.swift` 解码。**逐字段对照表**是人读件,落
`archive/V2_契约三方对拍_20260803.md`;本文件只装**能机器判的那几条**:

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
_MODELS = _ROOT / "client" / "Models.swift"

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


def test_deleted_v1_endpoints_have_no_server_route():
    """⑬ 删掉的十个端点 + `PUT /settings/llm` 服务端零残留(路由面判据)。"""
    server = server_route_surface()
    for gone in (
        "/api/v1/watchlist", "/api/v1/watchlist/{}", "/api/v1/watchlist/reconcile-ths",
        "/api/v1/watchlist/export-ths", "/api/v1/breathing", "/api/v1/breathing/{}",
        "/api/v1/settings/intel-boards", "/api/v1/settings/llm",
        "/api/v1/decisions/{}/link", "/api/v1/decisions/{}/cancel",
        "/api/v1/decisions/{}/revise", "/api/v1/decisions/{}/scenario-outcome",
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


# —— 冻结快照类 DTO 的解码姿势(CLAUDE.md「落库快照两类论」)——————————————

# **写入当时冻住**的那一类:服务端升级永远不会给老快照补新键 → 客户端必须手写
# `init(from:)` 全字段 `decodeIfPresent` 兜底。⛔ 合成 `Codable` 的后果是「装了新
# App 的用户翻几周前的老卡 → 整张卡解不出」。
FROZEN_SNAPSHOT_DTOS = ("BasketCard", "BasketReview", "ReviewWeeklyResult")


@pytest.mark.parametrize("name", FROZEN_SNAPSHOT_DTOS)
def test_frozen_snapshot_dtos_hand_write_init_from_decoder(name: str):
    """⑮ 落地后本条才会真正生效;在那之前,尚不存在的 DTO **跳过并说明原因**
    (⛔ 不静默通过 —— 那会让这条闸在最需要它的那一刻是空的)。"""
    models = _MODELS.read_text(encoding="utf-8")
    marker = f"struct {name}"
    if marker not in models:
        pytest.skip(f"`{name}` 尚未在客户端定义(⑮ 的活);本闸在 ⑮ 落地后生效")
    body = models.split(marker, 1)[1]
    body = body.split("\nstruct ", 1)[0]
    assert "init(from decoder: Decoder)" in body, (
        f"`{name}` 是**写入当时冻住**的历史快照类 DTO,必须手写 `init(from:)` + 全字段 "
        f"`decodeIfPresent` 兜底(CLAUDE.md 落库快照两类论);合成 Codable 会让老快照解不出。"
    )
    assert "decodeIfPresent" in body, f"`{name}` 的 `init(from:)` 里没有 `decodeIfPresent` 兜底"
