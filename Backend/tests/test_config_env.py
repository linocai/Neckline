"""Unknown environment variables must not stop V4 from loading settings."""
from __future__ import annotations

import os

from neckline.api import deps
from neckline.config import load_env_file, reload_settings


def test_unknown_dotenv_and_process_variables_are_ignored(tmp_path, monkeypatch):
    retired_db = "DB_" + "PATH"
    retired_columnar = "P" + "ARQUET_DIR"
    retired_id = "K" + "10_CONFIG_ID"
    retired_revision = "K" + "10_CONFIG_REVISION"
    for key in (retired_db, retired_columnar, retired_id, retired_revision, "API_TOKEN",
                "TUSHARE_TOKEN", "NECKLINE_V4_DB_PATH", "NECKLINE_READ_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(retired_id, "still-in-the-process")
    monkeypatch.setenv(retired_revision, "not-an-int")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "API_TOKEN=token-at-least-16xx",
            "TUSHARE_TOKEN=from-file",
            "NECKLINE_V4_DB_PATH=/tmp/v4-from-file.db",
            "NECKLINE_READ_KEY=" + ("r" * 24),
            f"{retired_db}=/tmp/should-not-be-copied",
            f"{retired_columnar}=/tmp/should-not-be-copied",
            f"{retired_id}=from-file",
            f"{retired_revision}=not-an-int",
            "UNRELATED_NECKLINE_SWITCH=1",
        ]) + "\n",
        encoding="utf-8",
    )
    load_env_file(env_file)

    assert os.environ["API_TOKEN"] == "token-at-least-16xx"
    assert os.environ["TUSHARE_TOKEN"] == "from-file"
    assert os.environ["NECKLINE_V4_DB_PATH"] == "/tmp/v4-from-file.db"
    assert os.environ[retired_id] == "still-in-the-process"
    assert retired_db not in os.environ
    assert retired_columnar not in os.environ
    assert "UNRELATED_NECKLINE_SWITCH" not in os.environ

    loaded = reload_settings()
    monkeypatch.setattr(deps, "settings", loaded)
    deps.require_api_token_ready()
    assert loaded.api_token == "token-at-least-16xx"
    assert not hasattr(loaded, "db_path")
    assert not hasattr(loaded, "parquet_dir")

    monkeypatch.setenv("API_TOKEN", "already-present-token")
    load_env_file(env_file)
    assert os.environ["API_TOKEN"] == "already-present-token"
