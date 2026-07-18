"""Generation tracking with input hash dedup + finalize persistence (ADR-0058).

v0.23.2: Per issue #44 — input hash dedup prevents unnecessary agent runs,
finalize state persisted to disk (survives restart), generation records track
idempotency keys and input/output hashes.

Design principles from #44:
  - Input hash: sha256 of canonical inputs (transcript, items, materials, chat)
  - Idempotency key: meeting_id:artifact:revision:input_hash_prefix
  - Finalize: persisted .finalized marker prevents post-stop generations
  - No time gates: only causal events + content hashes
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default data root — same as task_manager / ui_server
_DEFAULT_DATA_DIR = Path(
    os.environ.get(
        "VPBUDDY_DATA_DIR",
        str(Path(__file__).resolve().parent.parent.parent / "data" / "meetings"),
    )
)

# ── Finalize state ──


def _finalize_path(meeting_id: str, data_dir: Path | str | None = None) -> Path:
    """Path to .finalized marker file."""
    d = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
    return d / f"{meeting_id}.finalized"


def is_finalized(meeting_id: str, data_dir: Path | None = None) -> bool:
    """Check if meeting has been finalized (stop/close called)."""
    return _finalize_path(meeting_id, data_dir).exists()


def mark_finalized(meeting_id: str, data_dir: Path | None = None) -> dict:
    """Persist finalize marker. Returns existing record if already finalized.

    Return dict always contains:
      - meeting_id
      - finalized_at
      - is_repeat: True if already finalized before this call
    """
    fp = _finalize_path(meeting_id, data_dir)
    if fp.exists():
        try:
            existing = json.loads(fp.read_text(encoding="utf-8"))
            existing["is_repeat"] = True
            return existing
        except Exception:
            pass
    record = {
        "meeting_id": meeting_id,
        "finalized_at": time.time(),
        "finalized_at_iso": _now_iso(),
        "is_repeat": False,
    }
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    logger.info("[generation] meeting %s finalized, marker written", meeting_id)
    return record


# ── Input hash computation ──


def _canonical_item(it: Any) -> dict:
    """Extract canonical fields from a TrackedItem (exclude ids, timestamps)."""
    d = {}
    for attr in ("text", "priority", "status"):
        val = getattr(it, attr, None)
        if hasattr(val, "value"):
            val = val.value
        d[attr] = str(val) if val is not None else ""
    return d


def compute_input_hash(meeting_id: str, data_dir: Path | None = None) -> str:
    """Compute deterministic sha256 hash of all inputs affecting doc/demo generation.

    Includes:
      - cleaned_text (transcript)
      - 5 tracked item lists (text + priority + status, no ids/timestamps)
      - uploaded file names + sizes (sorted)
      - recent chat messages (user/assistant roles, last 20)
    Excludes:
      - last_updated, created_at, speaker_map, IDs, version metadata
    """
    d = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
    canonical: dict[str, Any] = {}

    # 1. MeetingState
    state_path = d / f"{meeting_id}.json"
    if state_path.exists():
        try:
            state_raw = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state_raw = {}
    else:
        state_raw = {}

    canonical["cleaned_text"] = state_raw.get("cleaned_text", "") or ""

    # 5 tracked item lists: text + priority + status only
    for key in ("requirements", "goals", "features", "risks", "open_questions"):
        items = state_raw.get(key, []) or []
        canonical[key] = sorted(
            [
                {
                    "text": (it.get("text") or "").strip(),
                    "priority": str(it.get("priority", "")),
                    "status": str(it.get("status", "")),
                }
                for it in items
            ],
            key=lambda x: x["text"],
        )

    # 2. Uploaded files (names + sizes, sorted)
    upload_dir = d / "uploads" / meeting_id
    files: list[dict] = []
    if upload_dir.is_dir():
        for f in sorted(upload_dir.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "size": f.stat().st_size})
    canonical["materials"] = files

    # 3. Chat history (last 20 messages, user + assistant content only)
    chat_path = d / f"{meeting_id}.chat.json"
    chat_msgs: list[dict] = []
    if chat_path.exists():
        try:
            history = json.loads(chat_path.read_text(encoding="utf-8"))
            if isinstance(history, list):
                for m in history[-20:]:
                    role = m.get("role", "")
                    content = str(m.get("content", "") or "")[:500]
                    if content.strip():
                        chat_msgs.append({"role": role, "content": content})
        except Exception:
            pass
    canonical["chat_recent"] = chat_msgs

    # Serialize and hash
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Generation records ──

_GENERATIONS_DIR_NAME = "generations"


def _generations_path(meeting_id: str, data_dir: Path | str | None = None) -> Path:
    d = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
    return d / _GENERATIONS_DIR_NAME / f"{meeting_id}.json"


@dataclass
class GenerationRecord:
    """One generation attempt for a meeting's artifacts."""

    gen_id: int
    meeting_id: str
    artifact: str  # "docs" (batch_docs + demo together) or single kind
    input_hash: str
    idempotency_key: str
    revision: int
    status: str = "queued"  # queued | running | completed | stale | unchanged | failed
    output_hash: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "gen_id": self.gen_id,
            "artifact": self.artifact,
            "input_hash": self.input_hash,
            "idempotency_key": self.idempotency_key,
            "revision": self.revision,
            "status": self.status,
            "output_hash": self.output_hash,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict, meeting_id: str) -> "GenerationRecord":
        return cls(
            gen_id=d["gen_id"],
            meeting_id=meeting_id,
            artifact=d.get("artifact", "docs"),
            input_hash=d.get("input_hash", ""),
            idempotency_key=d.get("idempotency_key", ""),
            revision=d.get("revision", 0),
            status=d.get("status", "queued"),
            output_hash=d.get("output_hash"),
            created_at=d.get("created_at", 0),
            completed_at=d.get("completed_at"),
        )


# Per-meeting generation counter + lock
_counter_lock = threading.Lock()
_counters: dict[str, int] = {}


def _next_gen_id(meeting_id: str) -> int:
    with _counter_lock:
        _counters[meeting_id] = _counters.get(meeting_id, 0) + 1
        return _counters[meeting_id]


def _next_revision(meeting_id: str, data_dir: Path | None = None) -> int:
    """Revision = number of completed generations + 1."""
    records = load_generations(meeting_id, data_dir)
    completed = [r for r in records if r.status == "completed"]
    return len(completed) + 1


def load_generations(meeting_id: str, data_dir: Path | None = None) -> list[GenerationRecord]:
    """Load generation history for a meeting."""
    p = _generations_path(meeting_id, data_dir)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [GenerationRecord.from_dict(r, meeting_id) for r in raw]
    except Exception as e:
        logger.warning("[generation] load failed for %s: %s", meeting_id, e)
    return []


def save_generations(meeting_id: str, records: list[GenerationRecord], data_dir: Path | None = None):
    """Persist generation history (atomic write)."""
    p = _generations_path(meeting_id, data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(str(tmp), str(p))


def get_last_completed(meeting_id: str, artifact: str = "docs", data_dir: Path | None = None) -> GenerationRecord | None:
    """Get the last completed generation for an artifact."""
    records = load_generations(meeting_id, data_dir)
    for r in reversed(records):
        if r.artifact == artifact and r.status == "completed":
            return r
    return None


def create_generation(
    meeting_id: str,
    artifact: str = "docs",
    data_dir: Path | None = None,
) -> GenerationRecord:
    """Create a new generation record with idempotency key.

    Returns the record. Caller is responsible for setting status and saving.
    """
    input_hash = compute_input_hash(meeting_id, data_dir)
    revision = _next_revision(meeting_id, data_dir)
    gen_id = _next_gen_id(meeting_id)
    id_key = f"{meeting_id}:{artifact}:r{revision}:{input_hash[:16]}"

    # Check if same input_hash already has a completed generation
    last = get_last_completed(meeting_id, artifact, data_dir)
    if last and last.input_hash == input_hash:
        logger.info(
            "[generation] meeting %s input unchanged (hash=%s), reusing gen_id=%d",
            meeting_id, input_hash[:12], last.gen_id,
        )
        # Return existing record marked as unchanged
        return GenerationRecord(
            gen_id=gen_id,
            meeting_id=meeting_id,
            artifact=artifact,
            input_hash=input_hash,
            idempotency_key=id_key,
            revision=revision,
            status="unchanged",
            output_hash=last.output_hash,
        )

    record = GenerationRecord(
        gen_id=gen_id,
        meeting_id=meeting_id,
        artifact=artifact,
        input_hash=input_hash,
        idempotency_key=id_key,
        revision=revision,
        status="queued",
    )

    # Persist
    records = load_generations(meeting_id, data_dir)
    records.append(record)
    save_generations(meeting_id, records, data_dir)
    logger.info(
        "[generation] meeting %s new generation gen_id=%d rev=%d hash=%s",
        meeting_id, gen_id, revision, input_hash[:12],
    )
    return record


def complete_generation(
    meeting_id: str,
    gen_id: int,
    status: str = "completed",
    output_hash: str | None = None,
    data_dir: Path | None = None,
):
    """Mark a generation as completed/failed/stale."""
    records = load_generations(meeting_id, data_dir)
    for r in records:
        if r.gen_id == gen_id:
            r.status = status
            r.completed_at = time.time()
            if output_hash:
                r.output_hash = output_hash
            break
    save_generations(meeting_id, records, data_dir)


def is_stale_generation(meeting_id: str, gen_id: int, data_dir: Path | None = None) -> bool:
    """Check if a generation has been superseded by a newer one."""
    records = load_generations(meeting_id, data_dir)
    latest_completed = None
    for r in reversed(records):
        if r.status == "completed" and r.gen_id != gen_id:
            latest_completed = r.gen_id
            break
    return latest_completed is not None and latest_completed > gen_id


def should_skip_generation(
    meeting_id: str,
    artifact: str = "docs",
    data_dir: Path | None = None,
) -> tuple[bool, str, str | None]:
    """Check if a new generation should be skipped.

    Returns (skip, reason, idempotency_key_or_None).
    Reasons: "finalized" | "input_unchanged" | "running" | "" (don't skip)
    """
    # 1. Finalized check
    if is_finalized(meeting_id, data_dir):
        return (True, "finalized", None)

    # 2. Input unchanged check
    input_hash = compute_input_hash(meeting_id, data_dir)
    last = get_last_completed(meeting_id, artifact, data_dir)
    if last and last.input_hash == input_hash:
        return (True, "input_unchanged", last.idempotency_key)

    return (False, "", None)


# ── Helpers ──


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
