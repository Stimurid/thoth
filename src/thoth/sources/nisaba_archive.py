"""Nisaba output as a Thoth source.

Reads an existing Nisaba archive (see github.com/Stimurid/nisaba) and
exposes each ChatGPT conversation as a SessionRecord with source="chatgpt".
This lets Thoth produce one unified index covering ChatGPT web + local
agents without re-fetching anything.

Input shape (Nisaba 0.1.0):
    <nisaba_root>/conversations.jsonl                 # one JSON per conversation
    <nisaba_root>/conversations/<uuid>/conversation.json
                                       conversation.md
                                       metadata.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from thoth.common import SessionRecord, to_iso_utc


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def _conv_to_record(nisaba_root: Path, listing: Dict[str, Any]) -> Optional[SessionRecord]:
    cid = listing.get("conversation_id") or ""
    if not cid:
        return None
    cdir = nisaba_root / "conversations" / cid
    conv_json = cdir / "conversation.json"
    md_path = cdir / "conversation.md"

    created = listing.get("create_time")
    updated = listing.get("update_time")
    title = listing.get("title") or "Untitled"
    project_id = listing.get("project_id") or listing.get("gizmo_id")
    project_name = listing.get("project_name")

    message_count = 0
    events: List[Dict[str, Any]] = []
    if conv_json.is_file():
        try:
            body = json.loads(conv_json.read_text(encoding="utf-8"))
            mapping = body.get("mapping") or {}
            message_count = len(mapping)
            created = to_iso_utc(body.get("create_time")) or created
            updated = to_iso_utc(body.get("update_time")) or updated
            # Keep events as a reference pointer rather than inlining 1 MB of body
            events = [{
                "ref": "conversation.json",
                "path": str(conv_json),
                "mapping_entries": message_count,
            }]
            if md_path.is_file():
                events.append({"ref": "conversation.md", "path": str(md_path)})
        except Exception:
            pass

    hint = project_name or (f"project:{project_id}" if project_id else None)

    extra = {
        "chatgpt": {
            "project_id": project_id,
            "project_name": project_name,
            "archived": bool(listing.get("archived")),
            "memberships": listing.get("memberships") or [],
        },
        "nisaba_conversation_dir": str(cdir),
    }

    return SessionRecord(
        source="chatgpt",
        session_id=cid,
        title=title,
        created_at=created,
        updated_at=updated,
        project_hint=hint,
        message_count=message_count,
        carrier_paths=[str(conv_json), str(md_path)] if conv_json.is_file() else [str(conv_json)],
        extra=extra,
        events=events,
    )


def collect(
    out_root: Path,
    *,
    nisaba_root: Path,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    if not nisaba_root.is_dir():
        return {"source": "chatgpt", "skipped": True, "reason": f"nisaba root not found: {nisaba_root}"}

    from thoth.common import append_index, write_manifest

    listing_path = nisaba_root / "conversations.jsonl"
    rows: List[Dict[str, Any]] = []
    new_count = 0
    skipped_old = 0

    for listing in _read_jsonl(listing_path):
        rec = _conv_to_record(nisaba_root, listing)
        if rec is None:
            continue
        if since and (rec.updated_at or rec.created_at or "") <= since:
            skipped_old += 1
            continue
        # Nisaba already stores the authoritative JSON+MD, we only
        # index them here — do NOT re-write session files.
        rows.append(rec.index_row())
        new_count += 1

    if rows:
        append_index(out_root, "chatgpt", rows)
    write_manifest(out_root, "chatgpt", extra={
        "nisaba_root": str(nisaba_root),
        "new_sessions_this_run": new_count,
        "skipped_as_old": skipped_old,
        "since_watermark": since,
        "note": "sessions are not re-emitted here; carrier_paths point back into the Nisaba archive",
    })
    return {
        "source": "chatgpt",
        "nisaba_root": str(nisaba_root),
        "new_sessions_this_run": new_count,
        "skipped_as_old": skipped_old,
    }
