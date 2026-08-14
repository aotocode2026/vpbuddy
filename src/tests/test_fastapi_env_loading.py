from __future__ import annotations

from pathlib import Path


FASTAPI_APP = Path(__file__).resolve().parents[1] / "vpbuddy" / "server" / "fastapi_app.py"


def test_explicit_env_file_loads_missing_values_without_overwriting_process_env(
    tmp_path: Path, monkeypatch
) -> None:
    master = tmp_path / ".env"
    master.write_text(
        "DASHSCOPE_API_KEY=dash-from-master\n"
        "MINIMAX_API_KEY=mini-from-master\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VPBUDDY_ENV_FILE", str(master))
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-from-process")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    source = FASTAPI_APP.read_text(encoding="utf-8")
    bootstrap = source.split("\nimport uvicorn", 1)[0]
    namespace = {"__file__": str(FASTAPI_APP)}
    exec(compile(bootstrap, str(FASTAPI_APP), "exec"), namespace)

    assert __import__("os").environ["DASHSCOPE_API_KEY"] == "dash-from-master"
    assert __import__("os").environ["MINIMAX_API_KEY"] == "mini-from-process"
