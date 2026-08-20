"""Industry template catalog and template-to-meeting application.

The first version intentionally stays small:

* Templates are read-only platform resources stored on disk.
* Each template is a directory containing ``template.json``, ``index.html`` and
  an optional cover image.
* Applying a template creates a normal meeting and writes the template HTML as
  that meeting's Demo V1.0 through the existing demo-version pipeline.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .server.config import DATA_DIR, DOCS_DIR, TEMPLATES_DIR
from .state import AudioSourceKind, MeetingState, Platform

logger = logging.getLogger(__name__)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_COVER_EXTS = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
_DEFAULT_PAGE_SIZE = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_safe_template_id(template_id: str) -> bool:
    return bool(template_id) and bool(_SAFE_ID_RE.match(template_id))


def _template_dir(template_id: str) -> Path:
    return TEMPLATES_DIR / template_id


def _read_template_meta(template_id: str) -> dict[str, Any]:
    meta_path = _template_dir(template_id) / "template.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"template meta not found: {template_id}")
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid template meta: {template_id}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"invalid template meta: {template_id}")
    return data


def _cover_file(template_id: str, meta: dict[str, Any]) -> Path | None:
    explicit = (meta.get("cover_file") or "").strip()
    if explicit:
        candidate = _template_dir(template_id) / explicit
        if candidate.is_file():
            return candidate.resolve()

    template_dir = _template_dir(template_id)
    if template_dir.is_dir():
        for child in sorted(template_dir.iterdir(), key=lambda p: p.name.lower()):
            if child.is_file() and child.suffix.lower() in _COVER_EXTS:
                return child.resolve()
    return None


def _template_dto(template_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": template_id,
        "name": str(meta.get("name") or template_id),
        "industry": str(meta.get("industry") or ""),
        "scenario": str(meta.get("scenario") or ""),
        "summary": str(meta.get("summary") or ""),
        "tags": [str(t) for t in (meta.get("tags") or [])],
        "featured": bool(meta.get("featured", False)),
        "sort_order": int(meta.get("sort_order", 0)),
        "updated_at": str(meta.get("updated_at") or ""),
        "cover_url": f"/api/templates/{template_id}/cover",
        "preview_url": f"/api/templates/{template_id}/preview",
    }


def _iter_template_ids() -> list[str]:
    if not TEMPLATES_DIR.is_dir():
        return []
    ids: list[str] = []
    for child in TEMPLATES_DIR.iterdir():
        if child.is_dir() and _is_safe_template_id(child.name):
            ids.append(child.name)
    return sorted(ids)


def _load_enabled_meta(template_id: str) -> dict[str, Any] | None:
    try:
        meta = _read_template_meta(template_id)
    except Exception as exc:
        logger.warning("skip template %s: %s", template_id, exc)
        return None
    if meta.get("enabled") is False:
        return None
    return meta


def list_templates(
    query: str = "",
    industry: str = "",
    sort: str = "default",
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Return enabled templates, with basic search/filter/pagination."""
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    q = (query or "").strip().lower()
    industry_value = (industry or "").strip()

    items: list[dict[str, Any]] = []
    industries: set[str] = set()
    for template_id in _iter_template_ids():
        meta = _load_enabled_meta(template_id)
        if meta is None:
            continue
        dto = _template_dto(template_id, meta)
        industry_name = dto["industry"]
        if industry_name:
            industries.add(industry_name)

        if industry_value and dto["industry"] != industry_value:
            continue

        if q:
            haystack = " ".join(
                [
                    dto["name"],
                    dto["industry"],
                    dto["scenario"],
                    dto["summary"],
                    " ".join(dto["tags"]),
                ]
            ).lower()
            if q not in haystack:
                continue

        items.append(dto)

    if sort in ("updated", "updated_at"):
        items.sort(key=lambda x: x["updated_at"], reverse=True)
    else:
        items.sort(key=lambda x: (x["sort_order"], x["updated_at"]), reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "templates": items[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "industries": sorted(industries),
    }


def get_template(template_id: str) -> dict[str, Any]:
    if not _is_safe_template_id(template_id):
        raise LookupError("template_not_found")
    meta = _load_enabled_meta(template_id)
    if meta is None:
        raise LookupError("template_not_found")
    dto = _template_dto(template_id, meta)
    dto["roles"] = [str(t) for t in (meta.get("roles") or [])]
    dto["modules"] = [str(t) for t in (meta.get("modules") or [])]
    return dto


def get_template_html(template_id: str) -> str:
    if not _is_safe_template_id(template_id):
        raise LookupError("template_not_found")
    meta = _load_enabled_meta(template_id)
    if meta is None:
        raise LookupError("template_not_found")
    entry = str(meta.get("entry_file") or "index.html")
    entry = entry.lstrip("/")
    if entry == "":
        entry = "index.html"
    entry_path = (_template_dir(template_id) / entry).resolve()
    template_root = _template_dir(template_id).resolve()
    if template_root not in entry_path.parents and entry_path != template_root:
        raise ValueError("invalid template entry file")
    if not entry_path.is_file():
        raise FileNotFoundError("template entry file not found")
    return entry_path.read_text(encoding="utf-8")


def get_template_cover(template_id: str) -> Path | None:
    if not _is_safe_template_id(template_id):
        raise LookupError("template_not_found")
    meta = _load_enabled_meta(template_id)
    if meta is None:
        raise LookupError("template_not_found")
    return _cover_file(template_id, meta)


def _application_path(request_id: str) -> Path:
    return DATA_DIR / "template_applications" / f"{request_id}.json"


def _load_application(request_id: str) -> dict[str, Any] | None:
    path = _application_path(request_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _save_application(record: dict[str, Any]) -> None:
    path = _application_path(str(record.get("request_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_template(
    template_id: str,
    user_id: str,
    project_name: str = "",
    meeting_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    """Create a meeting and initialize its Demo V1.0 from a template.

    The application is intentionally synchronous and idempotent when a stable
    ``request_id`` is supplied by the client.
    """
    if not _is_safe_template_id(template_id):
        return {"status": "failed", "code": "template_not_found", "retryable": False}
    meta = _load_enabled_meta(template_id)
    if meta is None:
        return {"status": "failed", "code": "template_not_found", "retryable": False}

    request_id = (request_id or "").strip() or uuid.uuid4().hex
    existing = _load_application(request_id)
    if existing and existing.get("status") == "success":
        return {
            "status": "success",
            "meeting_id": existing.get("meeting_id"),
            "reused": True,
            "demo": existing.get("demo"),
        }

    try:
        html = get_template_html(template_id)
    except Exception as exc:
        return {
            "status": "failed",
            "code": "template_init_failed",
            "error": str(exc)[:200],
            "retryable": False,
        }

    from .demo_version import write_demo_version
    from .storage import MeetingStorage

    storage = MeetingStorage(DATA_DIR)
    created_new = False
    resolved_meeting_id = (meeting_id or "").strip()
    try:
        if resolved_meeting_id:
            if not storage.exists(resolved_meeting_id):
                return {
                    "status": "failed",
                    "code": "meeting_not_found",
                    "retryable": False,
                }
            state = storage.load(resolved_meeting_id)
            if getattr(state, "owner_id", "") != user_id:
                return {"status": "failed", "code": "forbidden", "retryable": False}
            if _meeting_has_demo(resolved_meeting_id):
                return {"status": "failed", "code": "meeting_conflict", "retryable": False}
        else:
            resolved_meeting_id = f"TPL_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            state = MeetingState(
                meeting_id=resolved_meeting_id,
                platform=Platform.LOCAL,
                audio_source=AudioSourceKind.MICROPHONE,
                owner_id=user_id,
                project_name=(project_name or "").strip() or str(meta.get("name") or template_id),
            )
            storage.save(state)
            created_new = True

        legacy_dir = DOCS_DIR / resolved_meeting_id / "demo"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_path = legacy_dir / "demo.html"
        legacy_path.write_text(html, encoding="utf-8")

        version_result = write_demo_version(
            resolved_meeting_id,
            html,
            trigger="template_apply",
            docs_dir=DOCS_DIR,
        )
        if not version_result.get("ok"):
            raise RuntimeError(version_result.get("error") or "demo init failed")

        demo_version = version_result.get("version", 1)
        record = {
            "request_id": request_id,
            "user_id": user_id,
            "template_id": template_id,
            "meeting_id": resolved_meeting_id,
            "status": "success",
            "created_at": _now_iso(),
            "demo": {
                "status": "initialized",
                "version": demo_version,
                "preview_url": f"/api/meetings/{resolved_meeting_id}/demo/versions/{demo_version}/content",
                "download_url": f"/api/meetings/{resolved_meeting_id}/docs/demo/download",
            },
        }
        _save_application(record)
        return {
            "status": "success",
            "meeting_id": resolved_meeting_id,
            "reused": False,
            "demo": record["demo"],
        }
    except Exception as exc:
        logger.exception("apply template failed: template=%s meeting=%s", template_id, resolved_meeting_id)
        if created_new and resolved_meeting_id:
            try:
                storage.delete(resolved_meeting_id)
            except Exception:
                pass
            try:
                meeting_docs = DOCS_DIR / resolved_meeting_id
                if meeting_docs.exists():
                    import shutil

                    shutil.rmtree(meeting_docs, ignore_errors=True)
            except Exception:
                pass
        return {
            "status": "failed",
            "code": "demo_init_failed",
            "error": str(exc)[:200],
            "retryable": True,
        }


def _meeting_has_demo(meeting_id: str) -> bool:
    from .demo_version import list_versions

    if list_versions(meeting_id, docs_dir=DOCS_DIR):
        return True
    legacy = DOCS_DIR / meeting_id / "demo" / "demo.html"
    return legacy.is_file() and legacy.stat().st_size > 0


def get_application(request_id: str) -> dict[str, Any] | None:
    return _load_application(request_id)
