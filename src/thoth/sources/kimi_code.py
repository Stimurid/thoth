"""Kimi Code App source: metadata pointers + proven import lineage.

Kimi Code App stores its own session index and session state under
~/.kimi-code. It may import and continue Claude Code/Codex sessions.
Do not duplicate imported conversation bodies or collapse native identities.

Input: session_index.jsonl -> sessionDir/state.json -> agents/main/wire.jsonl.
The owner of native session text remains the original app's local files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from thoth.common import (
    CAPTURE_METADATA_ONLY,
    CAPTURE_PARTIAL,
    CAPTURE_POINTER,
    SessionRecord,
    append_index,
    fingerprint_carriers,
    load_session_ledger,
    next_version,
    should_emit,
    to_iso_utc,
    write_manifest,
    write_session,
)

_SESSION_ID = re.compile(r"^(?:ses|session)_[a-fA-F0-9-]{36}$")
_SOURCE_ID = re.compile(r"^[a-fA-F0-9-]{36}$")
_ALLOWED_IMPORT_SOURCES = {"claude-code", "codex"}
_MAX_INDEX_BYTES = 4 * 1024 * 1024
_MAX_STATE_BYTES = 128 * 1024


def _home() -> Path:
    return Path(os.environ.get("KIMI_CODE_HOME") or (Path.home() / ".kimi-code"))


def _stamp(value: Any) -> Optional[str]:
    if isinstance(value, (int, float)) and value > 1_000_000_000_000:
        value = value / 1000.0
    return to_iso_utc(value)


def _instance_id(home: Path) -> str:
    marker = home / "device_id"
    if marker.is_file() and not marker.is_symlink() and marker.stat().st_size < 256:
        seed = marker.read_bytes()
    else:
        seed = str(home.resolve()).encode("utf-8", "replace")
    return "kimi-instance-" + hashlib.sha256(seed).hexdigest()[:12]


def _within(home: Path, candidate: Path) -> bool:
    try:
        return candidate.resolve().is_relative_to(home.resolve())
    except (OSError, ValueError):
        return False


def _counts(path: Path) -> int:
    """Count message events from wire without materializing chat text."""
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 128 * 1024 * 1024:
        return 0
    count = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"agent.message.appended"' in line:
                count += 1
    return count


def _index(root: Path):
    path = root / "session_index.jsonl"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_INDEX_BYTES:
        return []
    out = []
    with path.open(encoding="utf-8") as stream:
        for raw in stream:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
            if len(out) >= 100000:
                break
    return out


def collect(
    out_root: Path,
    *,
    home: Optional[Path] = None,
    since: Optional[str] = None,
    full: bool = False,
) -> Dict[str, Any]:
    home = home or _home()
    if not home.is_dir():
        return {"source": "kimi-code", "skipped": True, "reason": "KIMI_HOME_NOT_FOUND"}
    sessions_root = home / "sessions"
    account = _instance_id(home)
    ledger = load_session_ledger(out_root, "kimi-code")
    listed = 0
    new = 0
    unchanged = 0
    partial = 0
    imported = 0
    native = 0
    blocked = 0
    rows: List[Dict[str, Any]] = []
    seen = set()

    for listing in _index(home):
        listed += 1
        sid = listing.get("sessionId")
        directory = listing.get("sessionDir")
        if (not isinstance(sid, str) or not _SESSION_ID.fullmatch(sid)
                or not isinstance(directory, str) or sid in seen):
            blocked += 1
            continue
        seen.add(sid)
        path = Path(directory)
        if (not path.is_absolute() or path.name != sid
                or not _within(sessions_root, path)):
            blocked += 1
            continue

        state_file = path / "state.json"
        wire = path / "agents" / "main" / "wire.jsonl"
        state: Dict[str, Any] = {}
        if (state_file.is_file() and not state_file.is_symlink()
                and _within(path, state_file) and state_file.stat().st_size < _MAX_STATE_BYTES):
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    state = data
            except (OSError, ValueError):
                pass
        wire_present = wire.is_file() and not wire.is_symlink() and _within(path, wire)
        capture = (CAPTURE_POINTER if state and wire_present else
                   CAPTURE_PARTIAL if state else CAPTURE_METADATA_ONLY)
        if capture != CAPTURE_POINTER:
            partial += 1
        title = str(state.get("title") or "Kimi session")[:160]
        project_hint = str(state.get("cwd") or listing.get("workDir") or "")[:280] or None
        imported_meta = state.get("custom") if isinstance(state.get("custom"), dict) else {}
        imported_src = imported_meta.get("imported_from")
        imported_id = imported_meta.get("imported_source_id")
        edges: List[Dict[str, Any]] = []
        if (isinstance(imported_src, str) and imported_src in _ALLOWED_IMPORT_SOURCES
                and isinstance(imported_id, str) and _SOURCE_ID.fullmatch(imported_id)):
            edges.append({
                "relation": "IMPORTED_FROM",
                "source": imported_src,
                "session_id": imported_id.lower(),
                "evidence": "KIMI_STATE_CUSTOM_IMPORT_FIELDS",
                "imported_at": _stamp(imported_meta.get("imported_at")),
                "cross_provider_resume_proven": False,
            })
            imported += 1
        else:
            native += 1
        paths = ([str(state_file)] if state_file.is_file() else [])
        if wire_present:
            paths.append(str(wire))
        rec = SessionRecord(
            source="kimi-code",
            session_id=sid,
            account_id=account,
            session_kind="IMPORTED_CARRIER" if edges else "KIMI_NATIVE",
            client_origin="kimi-code-app",
            title=title,
            created_at=_stamp(state.get("createdAt")),
            updated_at=_stamp(state.get("updatedAt")),
            project_hint=project_hint,
            message_count=_counts(wire) if wire_present else 0,
            carrier_paths=paths,
            capture_status=capture,
            content_fingerprint=fingerprint_carriers(paths),
            native_thread_id=sid,
            lineage_refs=edges,
            extra={"index_id_matched": state.get("id") == sid,
                   "origin_evidence": "KIMI_STATE_CUSTOM",
                   "wire_metadata_only": True},
            events=[],
        )
        emit, reason = should_emit(rec, ledger, since=since, force=full)
        if not emit:
            unchanged += 1
            continue
        version = next_version(ledger, rec.identity_key())
        write_session(out_root, rec)  # short stub only, NO raw event copy
        row = rec.index_row(version=version)
        rows.append(row)
        ledger[rec.identity_key()] = row
        new += 1

    if rows:
        append_index(out_root, "kimi-code", rows)
    write_manifest(out_root, "kimi-code", extra={
        "source_root": str(home), "source_account_instance": account,
        "native_sessions_seen": native,
        "imported_carriers_seen": imported,
        "invalid_entries_blocked": blocked,
        "listed": listed,
        "new_sessions_this_run": new,
        "unchanged_this_run": unchanged,
        "partial_this_run": partial,
    })
    return {
        "source": "kimi-code",
        "listed": listed,
        "native": native,
        "imported": imported,
        "blocked": blocked,
        "partial": partial,
        "new_sessions_this_run": new,
        "skipped_unchanged": unchanged,
        "unique_sessions": len(load_session_ledger(out_root, "kimi-code")),
    }
