"""Codex CLI / Desktop session collector.

On-disk shape (observed 2026-10/11):
    ~/.codex/sessions/<YYYY>/<MM>/<DD>/*.jsonl    — session event streams
    ~/.codex/archived_sessions/rollout-*.jsonl    — archived
    ~/.codex/state_5.sqlite                       — Desktop thread metadata
        threads(id, rollout_path, title, name, cwd, project_id,
                creator_account_id, creator_user_id, ...)
        projects(id, name, metadata, position)
        project_roots(project_id, position, path)

Legacy Thoth indexes sometimes stored the entire rollout filename stem
(including timestamp prefix) as session_id. New rows normalize to the
true UUID. Threads present in SQLite without a matching rollout are
emitted as METADATA_ONLY — never asserted as full capture.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from thoth.common import (
    CAPTURE_FULL,
    CAPTURE_METADATA_ONLY,
    CAPTURE_PARTIAL,
    SessionRecord,
    append_index,
    extract_uuid,
    fingerprint_carriers,
    fingerprint_metadata,
    load_session_ledger,
    next_version,
    open_sqlite_readonly,
    should_emit,
    to_iso_utc,
    write_manifest,
    write_session,
)


def _default_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _default_state_db(home: Optional[Path] = None) -> Path:
    if os.environ.get("CODEX_STATE_DB"):
        return Path(os.environ["CODEX_STATE_DB"])
    return (home or _default_home()) / "state_5.sqlite"

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _iter_session_files(home: Path) -> Iterable[Path]:
    """Yield every .jsonl session file under ~/.codex/sessions and archived_sessions."""
    for sub in ("sessions", "archived_sessions"):
        root = home / sub
        if not root.is_dir():
            continue
        for p in root.rglob("*.jsonl"):
            if p.is_file():
                yield p


def normalize_codex_session_id(raw: str, path: Optional[Path] = None) -> str:
    """Map legacy filename-prefixed ids to the canonical UUID."""
    for candidate in (raw, path.stem if path else ""):
        uid = extract_uuid(candidate or "")
        if uid:
            return uid
    return (raw or (path.stem if path else "") or "unknown").lower()


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

    sid_raw = ""
    for e in events[:5]:
        for k in ("session_id", "id", "thread_id"):
            if isinstance(e.get(k), str):
                sid_raw = e[k]
                break
        if sid_raw:
            break
    if not sid_raw:
        sid_raw = path.stem

    sid = normalize_codex_session_id(sid_raw, path)

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

    project = None
    for e in events[:50]:
        for k in ("cwd", "workspace", "repo", "project"):
            v = e.get(k)
            if isinstance(v, str) and v.strip():
                project = v.strip()
                break
        if project:
            break

    fp = fingerprint_carriers([str(path)])
    return SessionRecord(
        source="codex",
        session_id=sid,
        title=title,
        created_at=created,
        updated_at=updated,
        project_hint=project,
        message_count=len(events),
        carrier_paths=[str(path)],
        extra={
            "legacy_session_id": sid_raw if sid_raw.lower() != sid else None,
            "rollout_stem": path.stem,
        },
        events=events,
        capture_status=CAPTURE_FULL,
        content_fingerprint=fp,
        native_thread_id=sid,
        open_hint=f"codex://threads/{sid}" if _UUID_RE.fullmatch(sid) else None,
    )


def _ms_or_s_to_iso(value: Any, ms_value: Any = None) -> Optional[str]:
    if ms_value is not None:
        try:
            return to_iso_utc(float(ms_value) / 1000.0)
        except (TypeError, ValueError):
            pass
    return to_iso_utc(value)


def load_desktop_thread_index(
    state_db: Path,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Optional[str]]:
    """Return (threads_by_id, projects_by_id_name, error_or_None).

    Read-only. Never mutates authorization or Desktop state.
    """
    if not state_db.is_file():
        return {}, {}, f"state db not found: {state_db}"
    try:
        conn = open_sqlite_readonly(state_db)
    except Exception as err:
        return {}, {}, f"state db open failed: {err}"

    projects: Dict[str, Dict[str, Any]] = {}
    try:
        try:
            for row in conn.execute(
                "SELECT id, name, metadata, position FROM projects"
            ):
                pid, name, metadata, position = row
                roots: List[str] = []
                try:
                    for r in conn.execute(
                        "SELECT path FROM project_roots WHERE project_id=? ORDER BY position",
                        (pid,),
                    ):
                        if r[0]:
                            roots.append(str(r[0]))
                except Exception:
                    roots = []
                projects[str(pid)] = {
                    "id": pid,
                    "name": name,
                    "metadata": metadata,
                    "position": position,
                    "roots": roots,
                }
        except Exception:
            projects = {}

        threads: Dict[str, Dict[str, Any]] = {}
        cols = [
            "id", "rollout_path", "created_at", "updated_at", "cwd", "title",
            "name", "first_user_message", "project_id", "creator_account_id",
            "creator_user_id", "created_at_ms", "updated_at_ms", "source",
            "model", "archived",
        ]
        # Version fallback: select only columns that exist.
        try:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
        except Exception as err:
            conn.close()
            return {}, {}, f"pragma failed: {err}"
        select_cols = [c for c in cols if c in existing]
        if "id" not in select_cols:
            conn.close()
            return {}, {}, "threads.id missing"
        sql = f"SELECT {', '.join(select_cols)} FROM threads"
        for raw in conn.execute(sql):
            row = dict(zip(select_cols, raw))
            tid = str(row.get("id") or "").lower()
            if not tid:
                continue
            proj = projects.get(str(row.get("project_id"))) if row.get("project_id") else None
            threads[tid] = {
                "id": tid,
                "rollout_path": row.get("rollout_path"),
                "created_at": _ms_or_s_to_iso(row.get("created_at"), row.get("created_at_ms")),
                "updated_at": _ms_or_s_to_iso(row.get("updated_at"), row.get("updated_at_ms")),
                "cwd": row.get("cwd"),
                "title": row.get("title") or row.get("name"),
                "name": row.get("name"),
                "first_user_message": row.get("first_user_message"),
                "project_id": row.get("project_id"),
                "project_name": (proj or {}).get("name"),
                "project_roots": (proj or {}).get("roots") or [],
                "creator_account_id": row.get("creator_account_id"),
                "creator_user_id": row.get("creator_user_id"),
                "source": row.get("source"),
                "model": row.get("model"),
                "archived": row.get("archived"),
            }
        conn.close()
        return threads, {k: str(v.get("name") or "") for k, v in projects.items()}, None
    except Exception as err:
        try:
            conn.close()
        except Exception:
            pass
        return {}, {}, f"state db read failed: {err}"


def _account_label(creator_account_id: Any) -> Optional[str]:
    if creator_account_id is None or creator_account_id == "":
        return None
    # Stable opaque label — do not invent human account names.
    import hashlib
    digest = hashlib.sha256(str(creator_account_id).encode("utf-8")).hexdigest()[:8]
    return f"codex-acct-{digest}"


def enrich_from_desktop(rec: SessionRecord, thread: Dict[str, Any]) -> SessionRecord:
    """Merge Desktop SQLite metadata into a rollout-backed record."""
    if thread.get("title"):
        rec.title = str(thread["title"])[:160]
    if thread.get("cwd"):
        rec.project_hint = thread["cwd"]
    elif thread.get("project_roots"):
        rec.project_hint = thread["project_roots"][0]
    rec.native_project_id = thread.get("project_id")
    rec.native_thread_id = thread.get("id") or rec.session_id
    acct = _account_label(thread.get("creator_account_id"))
    if acct:
        rec.account_id = acct
    if thread.get("updated_at"):
        # Prefer Desktop updated_at when newer / present.
        if not rec.updated_at or thread["updated_at"] > rec.updated_at:
            rec.updated_at = thread["updated_at"]
    if thread.get("created_at") and not rec.created_at:
        rec.created_at = thread["created_at"]
    rec.open_hint = f"codex://threads/{rec.session_id}"
    rec.extra = {
        **(rec.extra or {}),
        "codex_desktop": {
            "thread_id": thread.get("id"),
            "project_id": thread.get("project_id"),
            "project_name": thread.get("project_name"),
            "project_roots": thread.get("project_roots") or [],
            "creator_account_id_present": thread.get("creator_account_id") is not None,
            "creator_account_label": acct,
            "cwd": thread.get("cwd"),
            "model": thread.get("model"),
            "archived": bool(thread.get("archived")),
            "rollout_path": thread.get("rollout_path"),
            "open_hint_note": "codex:// link is a navigation hint, not proof of cross-account continuation",
        },
    }
    return rec


def _metadata_only_record(thread: Dict[str, Any]) -> SessionRecord:
    tid = str(thread.get("id") or "").lower()
    title = (thread.get("title") or thread.get("name") or f"codex thread {tid[:12]}")[:160]
    acct = _account_label(thread.get("creator_account_id"))
    hint = thread.get("cwd") or (
        (thread.get("project_roots") or [None])[0]
    )
    fp = fingerprint_metadata(
        thread.get("updated_at"), title, 0, CAPTURE_METADATA_ONLY,
    )
    return SessionRecord(
        source="codex",
        session_id=tid,
        title=title,
        created_at=thread.get("created_at"),
        updated_at=thread.get("updated_at"),
        project_hint=hint,
        message_count=0,
        carrier_paths=[],
        account_id=acct,
        capture_status=CAPTURE_METADATA_ONLY,
        content_fingerprint=fp,
        native_project_id=thread.get("project_id"),
        native_thread_id=tid,
        open_hint=f"codex://threads/{tid}" if _UUID_RE.fullmatch(tid) else None,
        extra={
            "codex_desktop": {
                "thread_id": tid,
                "project_id": thread.get("project_id"),
                "project_name": thread.get("project_name"),
                "project_roots": thread.get("project_roots") or [],
                "creator_account_id_present": thread.get("creator_account_id") is not None,
                "creator_account_label": acct,
                "cwd": thread.get("cwd"),
                "model": thread.get("model"),
                "archived": bool(thread.get("archived")),
                "rollout_path": thread.get("rollout_path"),
                "open_hint_note": "codex:// link is a navigation hint, not proof of cross-account continuation",
            },
            "gap": "thread_in_sqlite_rollout_absent",
        },
        events=[],
    )


def collect(
    out_root: Path,
    *,
    home: Optional[Path] = None,
    state_db: Optional[Path] = None,
    since: Optional[str] = None,
    full: bool = False,
    include_metadata_only: bool = True,
) -> Dict[str, Any]:
    """Collect Codex sessions into <out_root>/codex/ layout. Returns summary."""
    home = home or _default_home()
    state_db = state_db or _default_state_db(home)
    if not home.is_dir() and not (state_db and state_db.is_file()):
        return {"source": "codex", "skipped": True, "reason": f"home not found: {home}"}

    ledger = load_session_ledger(out_root, "codex")
    threads, _project_names, db_err = load_desktop_thread_index(state_db)

    seen_ids: Set[str] = set()
    rows: List[Dict[str, Any]] = []
    new_count = 0
    skipped_unchanged = 0
    skipped_old = 0
    partial_count = 0
    metadata_only_count = 0
    total_files = 0
    enriched = 0

    # 1) Rollout-backed sessions
    if home.is_dir():
        for p in _iter_session_files(home):
            total_files += 1
            rec = _parse_session_file(p)
            if rec is None:
                partial_count += 1
                continue
            if rec.session_id in seen_ids:
                continue
            seen_ids.add(rec.session_id)

            thread = threads.get(rec.session_id)
            if thread is None:
                # Try match by rollout_path basename UUID
                for tid, meta in threads.items():
                    rp = meta.get("rollout_path") or ""
                    if extract_uuid(str(rp)) == rec.session_id or (
                        p.name and p.name in str(rp)
                    ):
                        thread = meta
                        break
            if thread:
                enrich_from_desktop(rec, thread)
                enriched += 1

            emit, reason = should_emit(rec, ledger, since=since, force=full)
            if not emit:
                if reason == "unchanged_fingerprint":
                    skipped_unchanged += 1
                else:
                    skipped_old += 1
                continue

            ver = next_version(ledger, rec.identity_key())
            write_session(out_root, rec)
            row = rec.index_row(version=ver)
            rows.append(row)
            ledger[rec.identity_key()] = row
            new_count += 1

    # 2) METADATA_ONLY gaps: SQLite threads with no rollout match
    if include_metadata_only and threads:
        for tid, thread in threads.items():
            if tid in seen_ids:
                continue
            # If rollout_path points at an existing file we somehow missed, skip gap.
            rp = thread.get("rollout_path")
            if rp and Path(str(rp)).is_file():
                # File exists but was outside sessions/archived_sessions — treat as PARTIAL pointer.
                rec = _metadata_only_record(thread)
                rec.capture_status = CAPTURE_PARTIAL
                rec.carrier_paths = [str(rp)]
                rec.content_fingerprint = fingerprint_carriers([str(rp)]) or rec.content_fingerprint
                rec.extra = {**(rec.extra or {}), "gap": "rollout_outside_known_trees"}
            else:
                rec = _metadata_only_record(thread)

            emit, reason = should_emit(rec, ledger, since=since, force=full)
            if not emit:
                if reason == "unchanged_fingerprint":
                    skipped_unchanged += 1
                else:
                    skipped_old += 1
                continue
            ver = next_version(ledger, rec.identity_key())
            write_session(out_root, rec)
            row = rec.index_row(version=ver)
            rows.append(row)
            ledger[rec.identity_key()] = row
            new_count += 1
            if rec.capture_status == CAPTURE_METADATA_ONLY:
                metadata_only_count += 1
            else:
                partial_count += 1
            seen_ids.add(tid)

    if rows:
        append_index(out_root, "codex", rows)
    write_manifest(out_root, "codex", extra={
        "home": str(home),
        "state_db": str(state_db),
        "state_db_error": db_err,
        "desktop_threads": len(threads),
        "scanned_files": total_files,
        "new_sessions_this_run": new_count,
        "skipped_unchanged": skipped_unchanged,
        "skipped_as_old": skipped_old,
        "metadata_only_this_run": metadata_only_count,
        "partial_this_run": partial_count,
        "enriched_from_desktop": enriched,
        "since_watermark": since,
        "unique_sessions_indexed": len(load_session_ledger(out_root, "codex")),
    })
    return {
        "source": "codex",
        "home": str(home),
        "state_db": str(state_db),
        "state_db_error": db_err,
        "scanned_files": total_files,
        "desktop_threads": len(threads),
        "new_sessions_this_run": new_count,
        "skipped_unchanged": skipped_unchanged,
        "skipped_as_old": skipped_old,
        "metadata_only_this_run": metadata_only_count,
        "partial_this_run": partial_count,
        "enriched_from_desktop": enriched,
        "unique_sessions": len(load_session_ledger(out_root, "codex")),
    }
