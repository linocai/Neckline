"""V2.5.0 发布前的现行门禁，不保留 K8 迁移时代的兼容性断言。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from neckline.api.app import VERSION
from neckline.k9 import params

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "Backend"
APP = ROOT / "App"
PRODUCTION_PARAMS_SHA256 = "5775641b989e9553ad29e0178a059007f1f663b422e8134130c99922e0dee952"


def test_release_version_and_build_are_aligned():
    project = (APP / "Neckline.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    assert VERSION == "v2.5.0"
    assert set(re.findall(r"MARKETING_VERSION = ([^;]+);", project)) == {"2.5.0"}
    assert set(re.findall(r"CURRENT_PROJECT_VERSION = ([^;]+);", project)) == {"10"}


def test_production_parameter_pack_is_the_user_approved_whynotme_artifact(tmp_path):
    path = BACKEND / "config" / "k9-params.json"
    assert path.exists()
    assert (BACKEND / "config" / "k9-params.example.json").exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PRODUCTION_PARAMS_SHA256

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["packageVersion"] == "k9-params-20260822-r1"
    assert document["factPackVersion"] == params.PACK_VERSION
    assert document["calibratedBy"] == "whynotme/K9-20260822"
    assert document["approvedBy"] == "Lino"

    loaded = params.load(path, db_path=tmp_path / "params-validation.db")
    assert loaded.package_version == document["packageVersion"]
    assert loaded.approved_at == document["approvedAt"]


def test_example_has_fixed_structure_and_placeholders_only():
    document = json.loads(
        (BACKEND / "config" / "k9-params.example.json").read_text(encoding="utf-8"))

    def dig(path):
        node = document
        for part in path.split("."):
            node = node[part]
        return node

    for path, expected in params.K9_FIXED_VALUES.items():
        assert dig(path) == expected
    assert document["industry"]["excludedL2Codes"] == ["801125.SI"]
    assert params.TO_BE_CALIBRATED in json.dumps(document, ensure_ascii=False)


def test_parameter_dataclasses_still_have_no_defaults():
    import dataclasses

    checked = 0
    for value in vars(params).values():
        if isinstance(value, type) and dataclasses.is_dataclass(value):
            checked += 1
            assert all(field.default is dataclasses.MISSING
                       and field.default_factory is dataclasses.MISSING
                       for field in dataclasses.fields(value))
    assert checked >= 10


def test_ios_build_for_testing_is_a_written_hard_gate():
    command = "build-for-testing -destination 'generic/platform=iOS Simulator'"
    for path in (ROOT / "AGENTS.md", ROOT / "README.md", ROOT / "PROJECT_PLAN.md"):
        assert command in path.read_text(encoding="utf-8"), f"{path.name} 缺 B14 门禁"


def test_retired_oneoff_directory_has_no_files():
    oneoff = BACKEND / "scripts" / "oneoff"
    assert not oneoff.exists() or not any(oneoff.iterdir())


def test_retired_k8_runtime_files_are_absent():
    for path in (
        BACKEND / "neckline" / "legacy_k8.py",
        BACKEND / "neckline" / "data" / "concept_data.py",
        BACKEND / "scripts" / "backfill_concept.py",
        BACKEND / "packs",
    ):
        assert not path.exists() or (path.is_dir() and not any(path.iterdir()))
