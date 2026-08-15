from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "start_vpbuddy.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
def test_sync_env_creates_exact_verified_copy(tmp_path: Path) -> None:
    deployment = tmp_path / "deployment"
    server = deployment / "server"
    server.mkdir(parents=True)
    master = deployment / ".env"
    content = (
        "DASHSCOPE_API_KEY=dash-test\n"
        "MINIMAX_API_KEY='mini key with spaces'\n"
        "MINIMAX_BASE_URL=https://example.invalid/v1\n"
        "VPBUDDY_DATA_DIR=/data/vpbuddy/data\n"
        "VPBUDDY_DOCS_DIR=/data/vpbuddy/data/docs\n"
        "VPBUDDY_KB_DIR=/data/vpbuddy/kb\n"
    )
    master.write_text(content, encoding="utf-8")

    env = os.environ.copy()
    env.update(
        VPBUDDY_MASTER_ENV=str(master),
        VPBUDDY_DIR=str(server),
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), "--sync-env-only"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (server / ".env").read_bytes() == master.read_bytes()
    assert "已验证同步" in result.stdout
    assert "dash-test" not in result.stdout
    assert "mini key" not in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
def test_sync_env_fails_truthfully_when_master_is_missing(tmp_path: Path) -> None:
    server = tmp_path / "server"
    server.mkdir()
    env = os.environ.copy()
    env.update(
        VPBUDDY_MASTER_ENV=str(tmp_path / "missing.env"),
        VPBUDDY_DIR=str(server),
    )

    result = subprocess.run(
        ["bash", str(SCRIPT), "--sync-env-only"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert not (server / ".env").exists()
    assert "不存在或不可读" in result.stderr
    assert "已验证同步" not in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.parametrize("kb_dir", ["", "data/chroma"])
def test_sync_env_rejects_missing_or_relative_kb_dir(
    tmp_path: Path, kb_dir: str
) -> None:
    deployment = tmp_path / "deployment"
    server = deployment / "server"
    server.mkdir(parents=True)
    master = deployment / ".env"
    master.write_text(
        "DASHSCOPE_API_KEY=dash-test\n"
        "MINIMAX_API_KEY=mini-test\n"
        "VPBUDDY_DATA_DIR=/data/vpbuddy/data\n"
        "VPBUDDY_DOCS_DIR=/data/vpbuddy/data/docs\n"
        f"VPBUDDY_KB_DIR={kb_dir}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        VPBUDDY_MASTER_ENV=str(master),
        VPBUDDY_DIR=str(server),
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), "--sync-env-only"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "VPBUDDY_KB_DIR" in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.parametrize(
    "data_dir,docs_dir",
    [
        ("", "/data/vpbuddy/data/docs"),
        ("data", "/data/vpbuddy/data/docs"),
        ("/data/vpbuddy/data", "docs"),
        ("/data/vpbuddy/data", "/data/vpbuddy/server/docs"),
    ],
)
def test_sync_env_rejects_invalid_or_nonpersistent_docs_dir(
    tmp_path: Path, data_dir: str, docs_dir: str
) -> None:
    server = tmp_path / "server"
    server.mkdir()
    master = tmp_path / ".env"
    master.write_text(
        "DASHSCOPE_API_KEY=dash-test\n"
        "MINIMAX_API_KEY=mini-test\n"
        f"VPBUDDY_DATA_DIR={data_dir}\n"
        f"VPBUDDY_DOCS_DIR={docs_dir}\n"
        "VPBUDDY_KB_DIR=/data/vpbuddy/kb\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(VPBUDDY_MASTER_ENV=str(master), VPBUDDY_DIR=str(server))
    result = subprocess.run(
        ["bash", str(SCRIPT), "--sync-env-only"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "VPBUDDY_DATA_DIR" in result.stderr or "VPBUDDY_DOCS_DIR" in result.stderr
