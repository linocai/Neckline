"""**契约三方对拍的机器判据**(plan §五 V2-⑭-C;⑮ 硬清单 Y5 点名「做成机器判据,
别靠人眼扫」)。

对拍的三方:`api/schemas.py` 声明 → `api/app.py` 转发 → Swift `Models.swift` /
`APIClient.swift` 解码。**逐字段对照表**是人读件,落
`archive/V2_契约三方对拍_20260803.md`;本文件只装**能机器判的那几条**:

1. **客户端调用面 ⊆ 服务端路由面** —— 客户端 `APIClient.swift` 里出现的每一个
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
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_API_CLIENT = _ROOT / "client" / "Neckline" / "Networking" / "APIClient.swift"
_MODELS = _ROOT / "client" / "Models.swift"

# Swift 里的路径字面量:`"/api/v1/positions/\(id)/close"` → `/api/v1/positions/{}/close`
_PATH_LITERAL = re.compile(r'"(/api/v1[^"]*)"')
_INTERPOLATION = re.compile(r"\\\([^)]*\)")


def _normalize(path: str) -> str:
    """插值段归一成 `{}`、去掉查询串。FastAPI 的 `{basket_id}` 也归一成 `{}` ——
    对拍的是**路径形状**,不是参数名(名字不一致不影响 HTTP 能不能打通)。"""
    path = path.split("?", 1)[0]
    path = _INTERPOLATION.sub("{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    return path.rstrip("/") or "/"


def client_call_surface() -> set:
    """客户端**实际会打出去**的路径集合(含已删端点的活调用 —— 那正是本闸要抓的)。"""
    text = _API_CLIENT.read_text(encoding="utf-8")
    return {_normalize(m) for m in _PATH_LITERAL.findall(text)}


def server_route_surface() -> set:
    from neckline.api.app import app

    return {_normalize(r.path) for r in app.routes
            if getattr(r, "path", "").startswith("/api/v1")}


# ⑮ 待删的**已知欠账**(V2 review 契约线 🟡 Y5 点名的五处 + 其对应路径形状)。
# ⚠ **这是一份「已登记的债」,不是豁免**:⑮ 删完客户端调用之后,本集合必须**同步
# 清空**,否则下面那条 `==` 断言会因为"清单里有、代码里没有"而红 —— 这正是要的效果
# (⛔ 只会越放越松的 allowlist 等于没有闸)。
PENDING_CLIENT_CALLS_TO_BE_REMOVED_IN_15 = {
    "/api/v1/decisions/{}/link",              # ⑩-C 已删服务端写端点
    "/api/v1/decisions/{}/cancel",            # 同上
    "/api/v1/decisions/{}/revise",            # 同上
    "/api/v1/decisions/{}/scenario-outcome",  # 同上
    # 🔴 最糟的一处:`SettingsLLMRequest` 请求体含**明文 apiKey**,发到一个 V2 已删的
    # 端点,界面上还是一副成功的样子 = 假成功面 + 明文密钥打进空洞。⑮ 换 `/settings/providers*`。
    "/api/v1/settings/llm",
}


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

# ⑮ 待加 `mapReason` case 的字符串。⚠ 这是**已登记的债**:⑮ 加完 case 后必须从这里
# 删掉,否则上面那条 `==` 断言会因为"清单里有、代码里已覆盖"而红(要的就是这个效果)。
PENDING_MAP_REASON_CASES_FOR_15 = {
    # ——(a)V2-⑭-B 全新 reason,**fallback 猜不对文案**,⑮ 必须逐个加 case ——
    # 404 的 fallback 是 `.notHolding`「持仓已清」:`card_not_ready` 若不加 case,
    # 用户会看到「持仓已清」而不是「本篮的卡还没生成」(v1.4 `watchlist` 的
    # `not_found` 就是这么踩的)。
    "basket_not_found",   # 文案方向「找不到这个篮子」
    "card_not_ready",     # 文案方向「本篮的卡还没生成」——⛔ **不是**「篮子不存在」
    "no_base_plan",       # 文案方向「这笔仓没有可继承的计划基线」
    # ——(b)②/⑪ 已落地但客户端**尚未接线**的端点带来的 reason ——
    # `mapReason` 目前只在 400/404 两个分支被调用,而下面这些是 409/422;⑮ 接
    # Provider 设置屏与 NL 提醒时,要么扩 `mapReason` 的调用点、要么就近处理。
    # 登记在此是为了**不让它们悄悄留在盲区**,不是豁免。
    "already_exists",       # 409 `POST /settings/providers`(同名 provider)
    "duplicate_alert",      # 409 `POST /alerts`(同标的 + 规则逐字节相同)
    "invalid_rule",         # 422 `POST|PUT /alerts`(规则不合白名单)
    "invalid_task",         # 422 `PUT /settings/llm-routes`(未知任务名)
    "invalid_push_kinds",   # 422 `PUT /settings/push`(缺键 / 未登记 kind)
}


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
