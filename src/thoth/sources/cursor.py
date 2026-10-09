"""Cursor AI chat (Composer) collector.

On-disk shape (observed 2026-10-10):
    %APPDATA%/Cursor/User/globalStorage/state.vscdb  (SQLite)

Tables:
    composerHeaders  — one row per Composer/Chat session
        composerId, workspaceId, createdAt (ms), lastUpdatedAt (ms),
        isArchived, isSubagent, recency, checkpointAt, subagentTypeName,
        value (JSON blob with head metadata)
    cursorDiskKV      — key-value, keys like `bubbleId:<composerId>:<bubbleId>`
        each row's value is a JSON blob with the bubble content, type, etc.

Note: Cursor state is often locked / being written to by a live Cursor
process. We open the DB in URI mode `?mode=ro&immutable=1` which snapshots
the view at the time of connect and never writes.

Also: not every composer in composerHeaders has bubbles persisted —
sessions deleted from the UI may still appear in headers with 0 bubbles.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from thoth.common import SessionRecord, to_iso_utc


DEFAULT_DB = Path(os.environ.get("CURSOR_STATE_DB") or (
    Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    / "Cursor" / "User" / "globalStorage" / "state.vscdb"
))

# Workspace id -> folder path (readable project hint)
DEFAULT_WORKSPACE_STORAGE = Path(os.environ.get("CURSOR_WORKSPACE_STORAGE") or (
    Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    / "Cursor" / "User" / "workspaceStorage"
))


def _read_workspace_map(ws_root: Path) -> Dict[str, str]:
    """Map workspaceId (dir name) -> readable folder URL. Best-effort."""
    out: Dict[str, str] = {}
    if not ws_root.is_dir():
        return out
    for d in ws_root.iterdir():
        if not d.is_dir():
            continue
        wsjson = d / "workspace.json"
        if not wsjson.is_file():
            continue
        try:
            data = json.loads(wsjson.read_text(encoding="utf-8", errors="replace"))
            folder = data.get("folder") or data.get("configuration") or ""
            if folder:
                out[d.name] = str(folder)
        except Exception:
            continue
    return out


def _ms_to_iso(ms: Any) -> Optional[str]:
    if ms is None:
        return None
    try:
        return to_iso_utc(float(ms) / 1000.0)
    except (TypeError, ValueError):
        return None


def _bubble_text(value: Any) -> str:
    """Best-effort extraction of displayable text from a bubble payload."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return str(value)[:4000]
    if isinstance(value, dict):
        for k in ("text", "richText", "content", "message", "query"):
            v = value.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, dict):
                tt = v.get("text") or v.get("content")
                if isinstance(tt, str) and tt.strip():
                    return tt
        # fall back to a short JSON representation
        try:
            return json.dumps(value, ensure_ascii=False)[:4000]
        except Exception:
            return str(value)[:4000]
    return str(value)[:4000]


def _parse_header_value(value: Any) -> Dict[str, Any]:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    if isinstance(value, dict):
        return value
    return {}


def _load_composer(
    conn: sqlite3.Connection,
    composer_id: str,
    ws_folder: Optional[str],
    header_row: Tuple[Any, ...],
) -> Optional[SessionRecord]:
    cid, ws_id, created_ms, updated_ms, is_archived, is_subagent, _rec, _ckpt, _stype, header_val = header_row
    head = _parse_header_value(header_val)

    # bubbles for this composer
    cur = conn.execute(
        "SELECT key, value FROM cursorDiskKV WHERE key LIKE ? ORDER BY key",
        (f"bubbleId:{composer_id}:%",),
    )
    events: List[Dict[str, Any]] = []
    for key, raw in cur.fetchall():
        try:
            payload = raw if isinstance(raw, (dict, list)) else (
                json.loads(raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw)
            )
        except Exception:
            payload = {"_raw": str(raw)[:1000]}
        bubble_id = key.split(":", 2)[2] if key.count(":") >= 2 else key
        text = _bubble_text(payload)
        # best-effort role from type: cursor's `type` field is numeric;
        # 1 = user, 2 = assistant (empirically; may be wrong, keep raw too)
        ptype = payload.get("type") if isinstance(payload, dict) else None
        role = {1: "user", 2: "assistant"}.get(ptype, f"type_{ptype}") if ptype is not None else "unknown"
        events.append({
            "bubble_id": bubble_id,
            "role": role,
            "type": ptype,
            "text": text,
            "payload": payload if isinstance(payload, dict) else {"_raw": payload},
        })

    if not events and head.get("type") != "head":
        return None  # empty and no head info — skip

    # title from head.value if any, else first user bubble
    title = head.get("name") or head.get("title") or ""
    if not title:
        for ev in events:
            if ev["role"] == "user" and ev["text"].strip():
                title = ev["text"].splitlines()[0][:160]
                break
    if not title:
        title = f"cursor composer {composer_id[:12]}"

    created_iso = _ms_to_iso(created_ms) or _ms_to_iso(head.get("createdAt"))
    updated_iso = _ms_to_iso(updated_ms) or _ms_to_iso(head.get("lastUpdatedAt")) or created_iso

    project_hint = ws_folder or (ws_id or "")

    extra = {
        "cursor_composer": {
            "workspaceId": ws_id,
            "isArchived": bool(is_archived),
            "isSubagent": bool(is_subagent),
            "subagentTypeName": _stype,
            "unifiedMode": head.get("unifiedMode"),
            "forceMode": head.get("forceMode"),
        },
    }

    return SessionRecord(
        source="cursor",
        session_id=composer_id,
        title=title,
        created_at=created_iso,
        updated_at=updated_iso,
        project_hint=project_hint or None,
        message_count=len(events),
        carrier_paths=[],
        extra=extra,
        events=events,
    )


def collect(
    out_root: Path,
    *,
    db: Optional[Path] = None,
    workspace_storage: Optional[Path] = None,
    since: Optional[str] = None,
    include_empty: bool = False,
) -> Dict[str, Any]:
    db = db or DEFAULT_DB
    ws_root = workspace_storage or DEFAULT_WORKSPACE_STORAGE
    if not db.is_file():
        return {"source": "cursor", "skipped": True, "reason": f"db not found: {db}"}

    from thoth.common import write_session, append_index, write_manifest

    ws_map = _read_workspace_map(ws_root)

    uri = f"file:{db}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as e:
        return {"source": "cursor", "skipped": True, "reason": f"db open failed: {e}"}

    cur = conn.execute(
        "SELECT composerId, workspaceId, createdAt, lastUpdatedAt, isArchived, "
        "isSubagent, recency, checkpointAt, subagentTypeName, value "
        "FROM composerHeaders"
    )
    header_rows = cur.fetchall()

    rows: List[Dict[str, Any]] = []
    new_count = 0
    skipped_old = 0
    skipped_empty = 0

    for header in header_rows:
        composer_id = header[0]
        ws_id = header[1]
        ws_folder = ws_map.get(ws_id)
        rec = _load_composer(conn, composer_id, ws_folder, header)
        if rec is None:
            skipped_empty += 1
            continue
        if not include_empty and rec.message_count == 0:
            skipped_empty += 1
            continue
        if since and (rec.updated_at or rec.created_at or "") <= since:
            skipped_old += 1
            continue
        write_session(out_root, rec)
        rows.append(rec.index_row())
        new_count += 1

    conn.close()

    if rows:
        append_index(out_root, "cursor", rows)
    write_manifest(out_root, "cursor", extra={
        "db": str(db),
        "workspace_storage": str(ws_root),
        "composer_headers_total": len(header_rows),
        "workspaces_mapped": len(ws_map),
        "new_sessions_this_run": new_count,
        "skipped_as_old": skipped_old,
        "skipped_empty": skipped_empty,
        "since_watermark": since,
    })
    return {
        "source": "cursor",
        "db": str(db),
        "composer_headers_total": len(header_rows),
        "new_sessions_this_run": new_count,
        "skipped_as_old": skipped_old,
        "skipped_empty": skipped_empty,
    }
