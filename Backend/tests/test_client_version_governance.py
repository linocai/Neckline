"""客户端版本号治理守门(§五 v1.5-⑤-E,A2)。`neckline/api/app.py::VERSION`(去 v 前缀)
与客户端 `App/project.yml` 的 `settings.base.MARKETING_VERSION`、
`App/Neckline.xcodeproj/project.pbxproj` 里 **Neckline app target**(非
NecklineTests)每一处 `MARKETING_VERSION`,三者必须恒等——以后服务端升版号忘了改
客户端,本测试当场拦下并打印各自实际值,不必等真机换包才发现漂移。

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


def _project_yml_build() -> str:
    data = yaml.safe_load(_PROJECT_YML.read_text(encoding="utf-8"))
    value = (data.get("settings") or {}).get("base", {}).get("CURRENT_PROJECT_VERSION")
    assert value is not None, f"{_PROJECT_YML} 缺 settings.base.CURRENT_PROJECT_VERSION"
    return str(value)


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
    assert {server, package, pyproject} == {yml_version}, f"服务端包版本与 project.yml 版本号不一致。各方实际值:{actual}"
    assert set(pbx_versions) == {yml_version}, (
        f"pbxproj Neckline app target 版本号与 project.yml 不一致。各方实际值:{actual}"
    )


def test_release_icon_is_new_for_the_exact_marketing_version_and_build():
    version = _project_yml_version()
    build = _project_yml_build()
    icon = _project_yml_icon()
    expected = f"AppIconV{version.replace('.', '')}B{build}"
    assert icon == expected, f"AppIcon 必须随精确版本/build更名: {icon=} {expected=}"
    assert (_ASSETS / f"{icon}.appiconset").is_dir(), f"缺少发布图标资产 {_ASSETS / f'{icon}.appiconset'}"
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
