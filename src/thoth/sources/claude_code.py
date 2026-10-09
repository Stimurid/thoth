"""Claude Code chat collector.

On-disk shape (observed 2026-10-10):
    ~/.claude/projects/<project-slug>/<session-uuid>.jsonl

The <project-slug> is the absolute path of the project directory with
`/` replaced by `--`, prefixed by `C--` on Windows. One jsonl per session.
Events include `queue-operation`, `tool-use`, `assistant-message`,
`user-message`, etc. Timestamps are present on most events.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from thoth.common import SessionRecord, to_iso_utc


DEFAULT_HOME = Path(os.environ.get("CLAUDE_HOME") or (Path.home() / ".claude" / "projects"))


def _unslug_project(slug: str) -> str:
    """Reverse the <project-slug> encoding back to a readable path. Best-effort."""
    # On Windows, slug looks like C--projects-tools or C--Users-Homee-Projects-...
    s = slug
    # First `--` is drive separator on Windows, rest are `/` within path.
    if len(s) >= 2 and s[1] == '-' and s[0].isalpha():
        # "C--" -> "C:/"
        s = f"{s[0]}:/{s[3:]}"
        s = s.replace("--", "/")
    else:
        s = s.replace("--", "/")
    return s


def _iter_project_dirs(home: Path) -> Iterable[Path]:
    if not home.is_dir():
        return
    for p in home.iterdir():
        if p.is_dir():
            yield p


def _parse_session_file(path: Path, project_hint: str) -> Optional[SessionRecord]:
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

    sid = path.stem

    # title: first non-queue event's text or a prompt-looking field
    title = ""
    for e in events[:60]:
        t = e.get("type") or ""
        if t == "queue-operation":
            continue
        for k in ("title", "name", "prompt", "message", "text"):
            v = e.get(k)
            if isinstance(v, str) and v.strip():
                title = v.strip().split("\n")[0][:160]
                break
            if isinstance(v, dict):
                tt = v.get("content") or v.get("text")
                if isinstance(tt, str) and tt.strip():
                    title = tt.strip().split("\n")[0][:160]
                    break
        if title:
            break
    if not title:
        title = f"claude-code session {sid[:12]}"

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

    return SessionRecord(
        source="claude-code",
        session_id=sid,
        title=title,
        created_at=created,
        updated_at=updated,
        project_hint=project_hint,
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
    home = home or DEFAULT_HOME
    if not home.is_dir():
        return {"source": "claude-code", "skipped": True, "reason": f"home not found: {home}"}

    from thoth.common import write_session, append_index, write_manifest

    rows: List[Dict[str, Any]] = []
    new_count = 0
    skipped_old = 0
    total_files = 0

    for proj_dir in _iter_project_dirs(home):
        hint = _unslug_project(proj_dir.name)
        for p in proj_dir.glob("*.jsonl"):
            if not p.is_file():
                continue
            total_files += 1
            rec = _parse_session_file(p, hint)
            if rec is None:
                continue
            if since and (rec.updated_at or rec.created_at or "") <= since:
                skipped_old += 1
                continue
            write_session(out_root, rec)
            rows.append(rec.index_row())
            new_count += 1

    if rows:
        append_index(out_root, "claude-code", rows)
    write_manifest(out_root, "claude-code", extra={
        "home": str(home),
        "scanned_files": total_files,
        "new_sessions_this_run": new_count,
        "skipped_as_old": skipped_old,
        "since_watermark": since,
    })
    return {
        "source": "claude-code",
        "home": str(home),
        "scanned_files": total_files,
        "new_sessions_this_run": new_count,
        "skipped_as_old": skipped_old,
    }
