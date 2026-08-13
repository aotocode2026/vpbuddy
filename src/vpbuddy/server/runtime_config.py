"""Runtime environment loading and AI provider readiness checks.

Docker/container environment variables always win.  Environment files exist
only as a backwards-compatible fallback for bare-metal deployments.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv


def env_file_candidates(env: Mapping[str, str] | None = None) -> list[Path]:
    """Return ordered, de-duplicated runtime env file candidates."""
    source = os.environ if env is None else env
    candidates: list[Path] = []

    explicit = source.get("VPBUDDY_ENV_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())

    data_dir = source.get("VPBUDDY_DATA_DIR", "").strip()
    if data_dir:
        # /var/lib/vpbuddy/meetings -> /var/lib/vpbuddy/.env
        candidates.append(Path(data_dir).expanduser().parent / ".env")

    # Existing GPU-server installation paths.
    candidates.extend((Path("/data/vpbuddy/.env"), Path("/data/vpbuddy/server/.env")))

    project_root = Path(__file__).resolve().parents[3]
    candidates.append(project_root / ".env")

    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def load_runtime_environment() -> Path | None:
    """Load all available env files without overriding higher-priority values."""
    first_loaded: Path | None = None
    for candidate in env_file_candidates():
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            if first_loaded is None:
                first_loaded = candidate
    return first_loaded


def provider_readiness(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Return secret-free configuration readiness for both provider paths."""
    source = os.environ if env is None else env
    dashscope_key = source.get("DASHSCOPE_API_KEY") or source.get("BAILIAN_API_KEY")
    minimax_key = source.get("MINIMAX_API_KEY")
    minimax_base_url = source.get("MINIMAX_BASE_URL")
    minimax_model = source.get("MODEL")

    dashscope_ready = bool(dashscope_key)
    minimax_ready = bool(minimax_key and minimax_base_url and minimax_model)
    return {
        "ready": dashscope_ready and minimax_ready,
        "dashscope": {"configured": dashscope_ready},
        "minimax": {
            "configured": minimax_ready,
            "base_url_configured": bool(minimax_base_url),
            "model": minimax_model or "",
        },
        "deliverables": {"configured": minimax_ready},
    }
