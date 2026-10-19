"""版本号守门(§五 v1.5-⑤-E,A2)。分两组，组内必须恒等：

- 后端：`neckline/api/app.py::VERSION`(去 v 前缀)、`neckline.__version__`、
  `pyproject.toml` 的 version。
- 客户端：`App/project.yml` 的 `settings.base.MARKETING_VERSION`，以及
  `App/Neckline.xcodeproj/project.pbxproj` 里 **Neckline app target**(非
  NecklineTests)每一处 `MARKETING_VERSION`。

4.0.1 起后端可以先于 App 升版本（B120：App 仍是 4.0.0，后端 4.0.1），
不再要求这两组彼此相等。客户端单一源仍是 project.yml。

**背景(现状就是病,v1.5-⑤-E 原文)**:`project.yml` 曾长期停在 `"1.0.0"`,`pbxproj`
被手改到 `"1.4.1"`——两边已漂移,`xcodegen generate` 一重跑就会把 app target 的版本
打回 `1.0.0`。**单一源 = `project.yml` 的 `settings.base.MARKETING_VERSION`**;pbxproj
里 app target 的两处(Debug/Release)须与之同步(本项目 `xcodegen generate` 未接入
CI,故这里只校验数值一致,不强制重跑生成器)。

**app target 判据**:该 `XCBuildConfiguration` 块内同时含字面 `PRODUCT_NAME = Neckline;`
才算——project 级默认配置块的 `PRODUCT_NAME = "$(TARGET_NAME)"`(`NecklineTests` 继承
这一份,现状 `"1.0.0"`)据此被精确排除,**不参与本测试比对**(它不装机、跟版本无关,
v1.5-⑤-E 原文明确写死「守门单测只比 app target 的」)。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PY = _REPO_ROOT / "neckline" / "api" / "app.py"
_PACKAGE_INIT = _REPO_ROOT / "neckline" / "__init__.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_PROJECT_YML = _REPO_ROOT.parent / "App" / "project.yml"
_PBXPROJ = _REPO_ROOT.parent / "App" / "Neckline.xcodeproj" / "project.pbxproj"
_ASSETS = _REPO_ROOT.parent / "App" / "Neckline" / "Resources" / "Assets.xcassets"


def _server_version() -> str:
    text = _APP_PY.read_text(encoding="utf-8")
    m = re.search(r'^VERSION\s*=\s*"v?([\d.]+)"', text, re.MULTILINE)
    assert m, f'未能在 {_APP_PY} 中找到形如 VERSION = "vX.Y.Z" 的声明'
    return m.group(1)


def _project_yml_version() -> str:
    data = yaml.safe_load(_PROJECT_YML.read_text(encoding="utf-8"))
    v = (data.get("settings") or {}).get("base", {}).get("MARKETING_VERSION")
    assert v, f"{_PROJECT_YML} 缺 settings.base.MARKETING_VERSION"
    return str(v)


def _package_version() -> str:
    text = _PACKAGE_INIT.read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([\d.]+)"', text, re.MULTILINE)
    assert m, f'未能在 {_PACKAGE_INIT} 中找到形如 __version__ = "X.Y.Z" 的声明'
    return m.group(1)


def _pyproject_version() -> str:
    text = _PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([\d.]+)"', text, re.MULTILINE)
    assert m, f'未能在 {_PYPROJECT} 中找到形如 version = "X.Y.Z" 的声明'
    return m.group(1)


def _project_yml_icon() -> str:
    data = yaml.safe_load(_PROJECT_YML.read_text(encoding="utf-8"))
    target = ((data.get("targets") or {}).get("Neckline") or {})
    value = (((target.get("settings") or {}).get("base") or {}).get("ASSETCATALOG_COMPILER_APPICON_NAME"))
    assert isinstance(value, str) and value, f"{_PROJECT_YML} 缺 Neckline AppIcon 设置"
    return value


def _pbxproj_app_target_versions() -> List[str]:
    """只抓「块内同时含 `PRODUCT_NAME = Neckline;`」的 `MARKETING_VERSION`(见模块头
    「app target 判据」)。buildSettings 块内不含嵌套 `{}`(数组值用 `(...)`,字符串用
    `"..."`),故非贪婪匹配到下一个 `};` 即为该块边界,不会跨块误吞。"""
    text = _PBXPROJ.read_text(encoding="utf-8")
    blocks = re.findall(r"buildSettings = \{(.*?)\n\s*\};", text, re.DOTALL)
    versions: List[str] = []
    for block in blocks:
        if "PRODUCT_NAME = Neckline;" not in block:
            continue
        m = re.search(r"MARKETING_VERSION = ([\d.]+);", block)
        assert m, f"Neckline app target 的 buildSettings 块缺 MARKETING_VERSION:\n{block}"
        versions.append(m.group(1))
    return versions


def test_client_and_server_marketing_version_all_equal():
    server = _server_version()
    yml_version = _project_yml_version()
    package = _package_version()
    pyproject = _pyproject_version()
    pbx_versions = _pbxproj_app_target_versions()

    actual = {
        "app.py::VERSION(去v前缀)": server,
        "neckline.__version__": package,
        "pyproject.toml::project.version": pyproject,
        "project.yml::settings.base.MARKETING_VERSION": yml_version,
        "pbxproj Neckline app target(Debug/Release)": pbx_versions,
    }
    assert len(pbx_versions) == 2, (
        "预期 pbxproj 里 Neckline app target(Debug + Release)各一处 MARKETING_VERSION,"
        f"实际抓到 {len(pbx_versions)} 处。各方实际值:{actual}"
    )
    assert server == package == pyproject, f"后端 VERSION、包版本与 pyproject 不一致。各方实际值:{actual}"
    assert set(pbx_versions) == {yml_version}, (
        f"pbxproj Neckline app target 版本号与 project.yml 不一致。各方实际值:{actual}"
    )


def test_mcp_and_jin10_report_the_package_version(tmp_path):
    """对外报的版本跟包版本走，不能再写死。"""
    import json

    import httpx

    from neckline.mcp.server import _build
    from neckline.mcp.settings import McpSettings
    from neckline.news.jin10 import PROTOCOL_VERSION, Jin10Client

    package = _package_version()
    settings = McpSettings("https://nk.linotsai.top", "ab" * 16, "cd" * 32, tmp_path / "oauth.db")
    assert _build(settings, object()).server.version == package

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}} if body.get("method") == "initialize" else {"tools": []}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result},
                              headers={"content-type": "application/json"})

    with Jin10Client(token="t", timeout_seconds=5, transport=httpx.MockTransport(handler)) as client:
        client.pagination_argument("list_flash")
    assert bodies[0]["params"]["clientInfo"] == {"name": "Neckline", "version": package}


def test_app_icon_is_one_asset_set_shared_by_both_configurations():
    icon = _project_yml_icon()
    assert [path.name for path in _ASSETS.glob("*.appiconset")] == [f"{icon}.appiconset"], (
        "只保留一份图标资产；图稿变化时才改名")
    pbx_text = _PBXPROJ.read_text(encoding="utf-8")
    assert pbx_text.count(f"ASSETCATALOG_COMPILER_APPICON_NAME = {icon};") == 2


def test_generated_application_product_preserves_explicit_file_type():
    text = _PBXPROJ.read_text(encoding="utf-8")
    expected = (
        r"/\* Neckline\.app \*/ = \{isa = PBXFileReference; "
        r"explicitFileType = wrapper\.application; includeInIndex = 0; "
        r"path = Neckline\.app; sourceTree = BUILT_PRODUCTS_DIR; \};"
    )
    assert re.search(expected, text), "xcodegen 后处理必须保留 Neckline.app 的 explicitFileType"
