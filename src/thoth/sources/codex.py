"""Codex CLI session collector.

On-disk shape (observed 2026-10-10):
    ~/.codex/sessions/<YYYY>/<MM>/<DD>/*.jsonl    — session event streams
    ~/.codex/archived_sessions/rollout-*.jsonl    — archived
    ~/.codex/session_index.jsonl                  — one row per session

Each session jsonl has one JSON event per line. The first few events
typically establish session metadata; later events are tool-calls,
assistant messages, user messages.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from thoth.common import SessionRecord, to_iso_utc


DEFAULT_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _iter_session_files(home: Path) -> Iterable[Path]:
    """Yield every .jsonl session file under ~/.codex/sessions and archived_sessions."""
    for sub in ("sessions", "archived_sessions"):
        root = home / sub
        if not root.is_dir():
            continue
        for p in root.rglob("*.jsonl"):
            if p.is_file():
                yield p


def _parse_session_file(path: Path) -> Optional[SessionRecord]:
    """Load one Codex .jsonl file into a SessionRecord. Returns None on empty/corrupt."""
    events: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None
    if not events:
        return None

    # session id: prefer explicit, else parse from filename
    sid = ""
    for e in events[:5]:
        for k in ("session_id", "id", "thread_id"):
            if isinstance(e.get(k), str):
                sid = e[k]
                break
        if sid:
            break
    if not sid:
        # filename like rollout-2026-09-03T03-56-22-01a064c4-...jsonl
        stem = path.stem
        parts = stem.split("-")
        # take trailing uuid-ish tail if present, else stem
        for i in range(len(parts) - 4):
            tail = "-".join(parts[i:])
            if len(tail) >= 36 and tail.count("-") >= 4:
                sid = tail
                break
        if not sid:
            sid = stem

    # title: first non-empty text, else first event's type
    title = ""
    for e in events[:40]:
        for k in ("thread_name", "title", "name", "prompt"):
            v = e.get(k)
            if isinstance(v, str) and v.strip():
                title = v.strip()[:160]
                break
        if title:
            break
    if not title:
        title = f"codex session {sid[:12]}"

    # timestamps
    ts_candidates: List[str] = []
    for e in events:
        for k in ("timestamp", "created_at", "updated_at", "ts", "time"):
            v = e.get(k)
            iso = to_iso_utc(v)
            if iso:
                ts_candidates.append(iso)
                break
    created = min(ts_candidates) if ts_candidates else None
    updated = max(ts_candidates) if ts_candidates else None

    # project hint: look for cwd, repo, workspace
    project = None
    for e in events[:50]:
        for k in ("cwd", "workspace", "repo", "project"):
            v = e.get(k)
            if isinstance(v, str) and v.strip():
                project = v.strip()
                break
        if project:
            break

    return SessionRecord(
        source="codex",
        session_id=sid,
        title=title,
        created_at=created,
        updated_at=updated,
        project_hint=project,
        message_count=len(events),
        carrier_paths=[str(path)],
        extra={},
        events=events,
    )


def collect(
    out_root: Path,
    *,
    home: Optional[Path] = None,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect Codex sessions into <out_root>/codex/ layout. Returns summary."""
    home = home or DEFAULT_HOME
    if not home.is_dir():
        return {"source": "codex", "skipped": True, "reason": f"home not found: {home}"}

    from thoth.common import write_session, append_index, write_manifest

    seen_ids: set[str] = set()
    rows: List[Dict[str, Any]] = []
    new_count = 0
    skipped_old = 0
    total_files = 0

    for p in _iter_session_files(home):
        total_files += 1
        rec = _parse_session_file(p)
        if rec is None:
            continue
        if rec.session_id in seen_ids:
            continue
        if since and (rec.updated_at or rec.created_at or "") <= since:
            skipped_old += 1
            continue
        seen_ids.add(rec.session_id)
        write_session(out_root, rec)
        rows.append(rec.index_row())
        new_count += 1

    if rows:
        append_index(out_root, "codex", rows)
    write_manifest(out_root, "codex", extra={
        "home": str(home),
        "scanned_files": total_files,
        "new_sessions_this_run": new_count,
        "skipped_as_old": skipped_old,
        "since_watermark": since,
    })
    return {
        "source": "codex",
        "home": str(home),
        "scanned_files": total_files,
        "new_sessions_this_run": new_count,
        "skipped_as_old": skipped_old,
    }
