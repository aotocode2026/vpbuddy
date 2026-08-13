from __future__ import annotations

import os
from pathlib import Path

from vpbuddy.server.runtime_config import (
    env_file_candidates,
    load_runtime_environment,
    provider_readiness,
)


def test_data_dir_env_candidate_uses_immediate_parent():
    candidates = env_file_candidates({"VPBUDDY_DATA_DIR": "/var/lib/vpbuddy/meetings"})
    assert Path("/var/lib/vpbuddy/.env") in candidates
    assert Path("/var/lib/.env") not in candidates


def test_env_file_does_not_override_container_environment(tmp_path, monkeypatch):
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "MINIMAX_API_KEY=stale-file-key\nMINIMAX_BASE_URL=https://file.invalid/v1\nMODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VPBUDDY_ENV_FILE", str(env_file))
    monkeypatch.setenv("MINIMAX_API_KEY", "container-key")
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL", raising=False)

    assert load_runtime_environment() == env_file
    assert os.environ["MINIMAX_API_KEY"] == "container-key"
    assert os.environ["MINIMAX_BASE_URL"] == "https://file.invalid/v1"
    assert os.environ["MODEL"] == "file-model"


def test_multiple_env_files_can_fill_independent_provider_values(tmp_path, monkeypatch):
    data_root = tmp_path / "runtime"
    meetings = data_root / "meetings"
    meetings.mkdir(parents=True)
    explicit = tmp_path / "primary.env"
    explicit.write_text("DASHSCOPE_API_KEY=dashscope-key\n", encoding="utf-8")
    (data_root / ".env").write_text("MINIMAX_API_KEY=minimax-key\n", encoding="utf-8")
    monkeypatch.setenv("VPBUDDY_ENV_FILE", str(explicit))
    monkeypatch.setenv("VPBUDDY_DATA_DIR", str(meetings))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    load_runtime_environment()

    assert os.environ["DASHSCOPE_API_KEY"] == "dashscope-key"
    assert os.environ["MINIMAX_API_KEY"] == "minimax-key"


def test_provider_readiness_requires_both_independent_paths():
    partial = provider_readiness({
        "DASHSCOPE_API_KEY": "dashscope-key",
        "MINIMAX_BASE_URL": "https://api.minimax.chat/v1",
        "MODEL": "minimax-m3",
    })
    assert partial["dashscope"]["configured"] is True
    assert partial["minimax"]["configured"] is False
    assert partial["ready"] is False

    ready = provider_readiness({
        "DASHSCOPE_API_KEY": "dashscope-key",
        "MINIMAX_API_KEY": "minimax-key",
        "MINIMAX_BASE_URL": "https://api.minimax.chat/v1",
        "MODEL": "minimax-m3",
    })
    assert ready["ready"] is True
    assert "minimax-key" not in str(ready)


def test_compose_explicitly_injects_minimax_configuration():
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MINIMAX_API_KEY:" in compose
    assert "MINIMAX_BASE_URL:" in compose
    assert "MODEL:" in compose
    assert "MINIMAX_API_KEY:?set MINIMAX_API_KEY" in compose
    assert "DASHSCOPE_API_KEY:?set DASHSCOPE_API_KEY" in compose


def test_start_script_loads_keys_independently_and_checks_readiness():
    root = Path(__file__).resolve().parents[2]
    script = (root / "start_vpbuddy.sh").read_text(encoding="utf-8")
    assert "for key in DASHSCOPE_API_KEY BAILIAN_API_KEY MINIMAX_API_KEY" in script
    assert '[ -z "$MINIMAX_API_KEY" ]' in script
    assert "http://127.0.0.1:$PORT/readyz" in script


def test_docker_context_excludes_real_env_files():
    root = Path(__file__).resolve().parents[2]
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    assert ".env\n" in dockerignore
    assert "!.env.example" in dockerignore
