"""会议 Demo 内容和版本列表必须按 owner 隔离。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from vpbuddy import demo_version
from vpbuddy.server import api_utils, fastapi_app
from vpbuddy.server.auth import _create_token
from vpbuddy.state import MeetingState, Platform
from vpbuddy.storage import MeetingStorage


def _auth(user_id: str) -> dict[str, str]:
    token = _create_token(user_id, f"{user_id}@test.local")
    return {"Authorization": f"Bearer {token}"}


def test_demo_versions_and_content_require_meeting_owner(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    docs_dir = data_dir / "docs"
    docs_dir.mkdir(parents=True)
    monkeypatch.setattr(fastapi_app, "DATA_DIR", data_dir)
    monkeypatch.setattr(fastapi_app, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(api_utils, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(demo_version, "DOCS_DIR", docs_dir)

    meeting_id = "private-demo"
    MeetingStorage(data_dir).save(
        MeetingState(
            meeting_id=meeting_id,
            platform=Platform.LOCAL,
            owner_id="owner-a",
            project_name="private",
        )
    )
    result = demo_version.write_demo_version(
        meeting_id,
        "<!doctype html><html><body><h1>private demo</h1></body></html>",
        docs_dir=docs_dir,
    )
    assert result["ok"] is True

    client = TestClient(fastapi_app.app)
    versions_url = f"/api/meetings/{meeting_id}/demo/versions"
    content_url = f"{versions_url}/1/content"

    assert client.get(versions_url).status_code == 401
    assert client.get(versions_url, headers=_auth("owner-b")).status_code == 403
    owner_versions = client.get(versions_url, headers=_auth("owner-a"))
    assert owner_versions.status_code == 200
    assert owner_versions.json()["versions"][0]["version"] == 1

    assert client.get(content_url).status_code == 401
    assert client.get(content_url, headers=_auth("owner-b")).status_code == 403
    owner_content = client.get(content_url, headers=_auth("owner-a"))
    assert owner_content.status_code == 200
    assert "private demo" in owner_content.text
    assert owner_content.headers["cache-control"] == "private, no-store"
    assert client.get(
        f"{versions_url}/2/content", headers=_auth("owner-a")
    ).status_code == 404


def test_public_docs_static_route_is_not_exposed():
    client = TestClient(fastapi_app.app)
    assert client.get("/docs/any-meeting/demo_v1.html").status_code == 404

