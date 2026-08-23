"""K9 LLM 路由、prompt 时效约束与 key 防泄漏守门。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, NamedTuple

from neckline.llm import router
from tests import guard_scan


class _Row(NamedTuple):
    name: str
    enabled: bool
    has_web_search: bool


def test_only_three_current_llm_tasks_are_registered():
    assert router.ALL_TASKS == (
        router.TASK_NEWS_SCAN,
        router.TASK_EXPLAIN,
        router.TASK_PLAYBOOK,
    )
    assert router.DEFAULT_SEARCH_TASKS == (router.TASK_NEWS_SCAN,)


def test_explicit_route_wins_even_if_provider_is_missing():
    name = router.resolve_task_provider_name(
        router.TASK_EXPLAIN,
        routes={router.TASK_EXPLAIN: "ghost"},
        default_provider="deepseek",
        rows=[_Row("deepseek", True, False)],
    )
    assert name == "ghost"


def test_unrouted_task_uses_default_provider():
    name = router.resolve_task_provider_name(
        router.TASK_NEWS_SCAN,
        routes={},
        default_provider="deepseek",
        rows=[_Row("deepseek", True, False)],
    )
    assert name == "deepseek"


def test_missing_task_uses_default_provider():
    assert router.resolve_task_provider_name(
        None, routes={}, default_provider="deepseek", rows=[]
    ) == "deepseek"


_NECKLINE_DIR = Path(__file__).resolve().parent.parent / "neckline"


def _calls_provider_chat(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "chat"
        for node in ast.walk(tree)
    )


def _imports_prompt_context(path: Path) -> bool:
    return any("prompt_context" in mod for mod in guard_scan.imports(path))


def test_every_provider_chat_call_site_imports_prompt_context():
    offenders: List[str] = []
    for path in sorted(_NECKLINE_DIR.rglob("*.py")):
        if _calls_provider_chat(path) and not _imports_prompt_context(path):
            offenders.append(str(path.relative_to(_NECKLINE_DIR.parent)))
    assert not offenders, (
        "以下模块调用了 provider.chat(...) 但未 import prompt_context:"
        f"{offenders}"
    )


_SECRET = "sk-guard-test-leaksentinel-12345"


def test_get_settings_family_never_leaks_provider_key(client, AUTH):
    response = client.post("/api/v1/settings/providers", headers=AUTH, json={
        "name": "leak-probe",
        "baseUrl": "https://x.invalid",
        "model": "m",
        "apiKey": _SECRET,
        "hasWebSearch": True,
        "searchEngine": "search_pro",
        "notes": "probe row for key-leak guard test",
    })
    assert response.status_code == 201
    assert _SECRET not in response.text

    for path in (
        "/api/v1/settings",
        "/api/v1/settings/providers",
        "/api/v1/settings/llm-routes",
    ):
        response = client.get(path, headers=AUTH)
        assert response.status_code == 200, path
        assert _SECRET not in response.text, f"{path} 响应里出现了明文 key"


def test_prompt_context_scanner_sees_relative_imports(tmp_path: Path):
    for rel in ("neckline", "neckline/llm"):
        directory = tmp_path / rel
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    bait = tmp_path / "neckline" / "llm" / "judge.py"
    bait.write_text("from . import prompt_context\n", encoding="utf-8")
    assert _imports_prompt_context(bait)
    bait2 = tmp_path / "neckline" / "llm" / "news_scan.py"
    bait2.write_text(
        "from ..llm.prompt_context import TIMELINESS_RULES\n", encoding="utf-8"
    )
    assert _imports_prompt_context(bait2)
    clean = tmp_path / "neckline" / "llm" / "factory.py"
    clean.write_text("from . import router\nimport json\n", encoding="utf-8")
    assert not _imports_prompt_context(clean)
