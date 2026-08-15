#!/usr/bin/env python3
"""Merge recognized meeting deliverables into DATA_DIR/docs without overwrites.

Dry-run is the default. Pass --apply only after reviewing the report. Source files
are deliberately retained so the deployment backup remains a usable rollback.
"""
from __future__ import annotations

import argparse
import filecmp
import json
import shutil
from pathlib import Path


def meeting_ids(data_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in data_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        meeting_id = payload.get("meeting_id") if isinstance(payload, dict) else None
        if isinstance(meeting_id, str) and meeting_id and path.stem == meeting_id:
            ids.add(meeting_id)
    return ids


def merge(sources: list[Path], target: Path, ids: set[str], apply: bool) -> dict[str, int]:
    stats = {"meetings": 0, "copied": 0, "identical": 0, "conflicts": 0}
    seen_meetings: set[str] = set()
    operations: dict[Path, Path] = {}
    for source in sources:
        for meeting_id in sorted(ids):
            source_dir = source / meeting_id
            if not source_dir.is_dir():
                continue
            seen_meetings.add(meeting_id)
            for src in source_dir.rglob("*"):
                if src.is_dir():
                    continue
                dst = target / meeting_id / src.relative_to(source_dir)
                if dst.exists() or dst.is_symlink():
                    if src.is_file() and dst.is_file() and filecmp.cmp(src, dst, shallow=False):
                        stats["identical"] += 1
                    else:
                        stats["conflicts"] += 1
                        print(f"CONFLICT {src} -> {dst}")
                    continue
                prior = operations.get(dst)
                if prior is not None:
                    if src.is_file() and prior.is_file() and filecmp.cmp(src, prior, shallow=False):
                        stats["identical"] += 1
                    else:
                        stats["conflicts"] += 1
                        print(f"CONFLICT {src} <> {prior} -> {dst}")
                    continue
                operations[dst] = src
    stats["meetings"] = len(seen_meetings)
    if stats["conflicts"]:
        return stats
    if apply:
        for dst, src in operations.items():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_symlink():
                dst.symlink_to(src.readlink())
            else:
                shutil.copy2(src, dst)
    stats["copied"] = len(operations)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--source", action="append", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    target = args.target or args.data_dir / "docs"
    ids = meeting_ids(args.data_dir)
    if not ids:
        parser.error(f"no canonical meeting JSON files found in {args.data_dir}")
    stats = merge(args.source, target, ids, args.apply)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **stats}, ensure_ascii=False))
    return 2 if stats["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
