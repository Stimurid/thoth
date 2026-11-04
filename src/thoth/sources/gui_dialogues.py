"""Strict HUMAN GUI-only Thoth source collectors.

Explicit sources:
  - Codex Desktop: state_5.sqlite.originator plus source=vscode, thread_source=user.
  - Claude Code Desktop: per-record entrypoint=claude-desktop and genuine user event.
Anything unknown is EXCLUDED from the user-dialogue corpus (not guessed).
Neither collector scans/copies CLI/SDK/Hub event files in normal operation.
Original provider stores remain authoritative and read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from thoth.common import (
    CAPTURE_FULL, CAPTURE_METADATA_ONLY, CAPTURE_PARTIAL,
    SessionRecord, append_index, fingerprint_carriers, load_session_ledger,
    next_version, should_emit, to_iso_utc, write_manifest, write_session
)
from thoth.sources.codex import (
    _account_label, _metadata_only_record, load_desktop_thread_index
)
from thoth.sources.claude_code import _unslug_project

MAX_VISIBLE_MESSAGES = 20000
MAX_VISIBLE_CHARS = 4_000_000
MAX_EVENT_CHARS = 100_000
MAX_JSON_LINE_CHARS = 1_000_000
GUI_CODEX_ORIGINS = {"Codex Desktop", "codex_work_desktop"}
GUI_CODEX_SOURCES = {"vscode"}
GUI_CLAUDE_ENTRYPOINT = "claude-desktop"


def _visible_text(value: Any) -> str:
    if isinstance(value, str):
        return value[:MAX_EVENT_CHARS]
    if not isinstance(value, list):
        return ""
    parts: List[str] = []
    remaining = MAX_EVENT_CHARS
    for b in value:
        if isinstance(b, dict) and b.get("type") in ("text", "input_text", "output_text"):
            text = b.get("text")
            if isinstance(text, str) and remaining > 0:
                part = text[:remaining]
                parts.append(part)
                remaining -= len(part)
    return "\n".join(parts)


def _new_message(role: str, content: str, stamp: Any = None) -> Dict[str, str]:
    e = {"role": role, "content": content}
    ts = to_iso_utc(stamp)
    if ts:
        e["timestamp"] = ts
    return e


def _visible_codex_from_rollout(path: Path) -> Tuple[List[Dict[str, str]], bool]:
    """Extract user/assistant textual turns, never shell/tool/context events."""
    primary: List[Dict[str, str]] = []
    fallback: List[Dict[str, str]] = []
    overflow = False
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if len(line) > MAX_JSON_LINE_CHARS:
                overflow = True
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            kind = e.get("type")
            payload = e.get("payload")
            if not isinstance(payload, dict):
                continue
            if kind == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue
                text = _visible_text(payload.get("content"))
                if text.strip():
                    primary.append(_new_message(role, text, e.get("timestamp")))
            elif kind == "event_msg":
                typ = payload.get("type")
                role = {"user_message": "user", "agent_message": "assistant"}.get(typ)
                if not role:
                    continue
                val = payload.get("item") if typ == "user_message" else payload.get("message")
                if isinstance(val, dict):
                    val = val.get("content") or val.get("text")
                text = _visible_text(val)
                if text.strip():
                    fallback.append(_new_message(role, text, e.get("timestamp")))
            if len(primary) >= MAX_VISIBLE_MESSAGES or len(fallback) >= MAX_VISIBLE_MESSAGES:
                overflow = True
                break
    chosen = primary if primary else fallback
    output: List[Dict[str, str]] = []
    chars = 0
    for e in chosen:
        n = len(e["content"])
        if chars + n > MAX_VISIBLE_CHARS:
            overflow = True
            break
        if output and output[-1]["role"] == e["role"] and output[-1]["content"] == e["content"]:
            continue
        output.append(e)
        chars += n
    return output, overflow


def _source_stat(path: Optional[Path], metadata: Any = None) -> str:
    """Cheap per-session freshness, before reading original GUI event streams."""
    stat = None
    if path is not None and path.is_file():
        s = path.stat()
        stat = (s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    payload = json.dumps([stat, metadata], ensure_ascii=False, default=str, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _codex_source_path(thread: Dict[str, Any], home: Path) -> Optional[Path]:
    raw = thread.get("rollout_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    if (not candidate.is_file() or candidate.is_symlink()
            or not candidate.resolve().is_relative_to(home.resolve())):
        return None
    return candidate


def _write_new(out_root: Path, source: str, rec: SessionRecord,
               ledger: Dict[str, Dict[str, Any]], rows: List[Dict[str, Any]],
               *, since: Optional[str], full: bool) -> bool:
    emit, _ = should_emit(rec, ledger, since=since, force=full)
    if not emit:
        return False
    ver = next_version(ledger, rec.identity_key())
    write_session(out_root, rec)
    row = rec.index_row(version=ver)
    rows.append(row)
    ledger[rec.identity_key()] = row
    return True


def collect_codex_gui(
    out_root: Path, *, home: Optional[Path] = None,
    state_db: Optional[Path] = None, since: Optional[str] = None,
    full: bool = False,
) -> Dict[str, Any]:
    home = home or Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    state_db = state_db or (home / "state_5.sqlite")
    threads, projects, error = load_desktop_thread_index(state_db)
    if error:
        return {"source": "codex", "mode": "human-gui", "status": "PARTIAL",
                "error": "CODEX_DESKTOP_INDEX_UNAVAILABLE", "new_sessions_this_run": 0}
    ledger = load_session_ledger(out_root, "codex")
    new_rows: List[Dict[str, Any]] = []
    selected = 0
    excluded = 0
    metadata_only = 0
    handoffs = 0
    empty = 0
    for thread in threads.values():
        origin = thread.get("originator")
        source = thread.get("source")
        thread_source = thread.get("thread_source")
        if not (origin in GUI_CODEX_ORIGINS and source in GUI_CODEX_SOURCES
                and thread_source in ("user", "chatgpt_handoff")):
            excluded += 1
            continue
        selected += 1
        is_handoff = thread_source == "chatgpt_handoff"
        handoffs += int(is_handoff)
        rec = _metadata_only_record(thread)
        rec.session_kind = "CODEX_GUI_HANDOFF" if is_handoff else "CODEX_GUI_USER"
        rec.client_origin = str(origin)
        rec.native_thread_source = str(thread_source)
        rec.extra = {"gui_origin_verified": True, "original_store": "Codex Desktop SQLite",
                     "source_role": "USER_DIALOGUE_OR_HANDOFF",
                     "logical_new_dialogue": not is_handoff,
                     "raw_tools_archived": False}
        source_path = _codex_source_path(thread, home)
        rec.extra["source_stat"] = _source_stat(source_path, [
            rec.title, rec.updated_at, rec.account_id, rec.native_project_id,
            rec.session_kind, rec.native_thread_source,
        ])
        previous = ledger.get(rec.identity_key())
        if (previous and not full and
                (previous.get("extra") or {}).get("source_stat") == rec.extra["source_stat"]):
            continue
        if source_path:
            events, truncated = _visible_codex_from_rollout(source_path)
            if events:
                rec.events = events
                rec.message_count = len(events)
                rec.capture_status = CAPTURE_PARTIAL  # text only; not all rich blocks
                rec.carrier_paths = [str(source_path)]
                rec.extra["dialogue_text_only"] = True
                rec.extra["text_truncated"] = truncated
                rec.content_fingerprint = fingerprint_carriers([str(source_path)])
            else:
                empty += 1
                rec.events = []
                rec.capture_status = CAPTURE_METADATA_ONLY
        else:
            metadata_only += 1
        _write_new(out_root, "codex", rec, ledger, new_rows, since=since, full=full)
    if new_rows:
        append_index(out_root, "codex", new_rows)
    write_manifest(out_root, "codex", extra={
        "collection_policy": "PROVEN_HUMAN_GUI_ONLY_V1",
        "source_threads": len(threads), "confirmed_gui": selected,
        "excluded_cli_sdk_subagent_unproven": excluded,
        "chatgpt_handoffs_not_new_lines": handoffs,
        "metadata_only_rollouts_absent": metadata_only,
        "gui_rollouts_without_visible_dialogue": empty,
        "new_sessions_this_run": len(new_rows),
    })
    return {"source": "codex", "mode": "human-gui",
            "verified_gui": selected, "excluded": excluded,
            "handoffs": handoffs, "metadata_only": metadata_only + empty,
            "new_sessions_this_run": len(new_rows),
            "unique_sessions": len(load_session_ledger(out_root, "codex"))}


def _claude_entrypoint(path: Path) -> str:
    """Probe only a few initial events; never read CLI transcript contents."""
    with path.open(encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i >= 70:
                break
            if len(line) > MAX_JSON_LINE_CHARS:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            v = row.get("entrypoint")
            if isinstance(v, str):
                return v
    return "UNKNOWN"


def _claude_gui_record(path: Path, project_hint: str) -> Optional[SessionRecord]:
    """Preserve user-visible text only; omit CLI/SDK, thinking, tools and blobs."""
    messages: List[Tuple[str, Dict[str, str]]] = []
    chunk_by_message: Dict[str, int] = {}
    first = None
    last = None
    total_chars = 0
    overflow = False
    gui_user_events = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if len(line) > MAX_JSON_LINE_CHARS:
                overflow = True
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            entry = e.get("entrypoint")
            if entry is not None and entry != GUI_CLAUDE_ENTRYPOINT:
                # Mixed carrier is ambiguous; do not archive ANY of it silently.
                return None
            kind = e.get("type")
            role = "user" if kind == "user" else "assistant" if kind == "assistant" else None
            if not role or e.get("isSidechain") is True:
                continue
            if role == "user" and e.get("isMeta") is True:
                continue
            msg = e.get("message")
            if not isinstance(msg, dict):
                continue
            content = _visible_text(msg.get("content"))
            if not content.strip():
                continue
            if role == "user":
                gui_user_events += 1
            ts = to_iso_utc(e.get("timestamp"))
            if ts:
                first = min(first, ts) if first else ts
                last = max(last, ts) if last else ts
            native_id = msg.get("id") if role == "assistant" else e.get("uuid")
            key = str(native_id) if native_id else f"{len(messages)}"
            if key in chunk_by_message:
                # Streaming assistant revisions: retain latest visible content,
                # not all partial frames of the same message.
                i = chunk_by_message[key]
                previous = messages[i][1]
                if len(content) >= len(previous["content"]):
                    messages[i] = (key, _new_message(role, content, ts))
            else:
                if len(messages) >= MAX_VISIBLE_MESSAGES or total_chars + len(content) > MAX_VISIBLE_CHARS:
                    overflow = True
                    break
                chunk_by_message[key] = len(messages)
                messages.append((key, _new_message(role, content, ts)))
                total_chars += len(content)
    if not gui_user_events:
        return None
    events = [m for _, m in messages]
    title = next((m["content"].strip().split("\n")[0][:120] for m in events
                  if m["role"] == "user"), f"Claude Desktop session {path.stem[:12]}")
    rec = SessionRecord(
        source="claude-code", session_id=path.stem, title=title,
        created_at=first, updated_at=last, project_hint=project_hint,
        message_count=len(events), carrier_paths=[str(path)], events=events,
        capture_status=CAPTURE_PARTIAL, content_fingerprint=fingerprint_carriers([str(path)]),
        native_thread_id=path.stem, session_kind="CLAUDE_GUI_USER",
        client_origin="claude-desktop",
        extra={"gui_origin_verified": True, "dialogue_text_only": True,
               "original_store": "Claude Code Desktop", "text_truncated": overflow,
               "raw_tools_archived": False},
    )
    return rec


def collect_claude_gui(
    out_root: Path, *, home: Optional[Path] = None,
    since: Optional[str] = None, full: bool = False,
) -> Dict[str, Any]:
    home = home or Path(os.environ.get("CLAUDE_HOME") or (Path.home() / ".claude" / "projects"))
    if not home.is_dir():
        return {"source": "claude-code", "mode": "human-gui", "status": "PARTIAL",
                "reason": "CLAUDE_GUI_HOME_MISSING", "new_sessions_this_run": 0}
    ledger = load_session_ledger(out_root, "claude-code")
    rows: List[Dict[str, Any]] = []
    excluded = 0
    verified = 0
    blocked = 0
    candidates: Dict[str, List[Path]] = {}
    for folder in sorted(home.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue
        for path in sorted(folder.glob("*.jsonl")):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(home.resolve()):
                blocked += 1
                continue
            if _claude_entrypoint(path) != GUI_CLAUDE_ENTRYPOINT:
                excluded += 1
                continue
            # One native UUID may have multiple GUI carriers after relocation.
            # A duplicate is NOT an additional human conversation.
            candidates.setdefault(path.stem, []).append(path)
    duplicate_gui_carriers = sum(len(ps)-1 for ps in candidates.values())
    for native_id, paths in sorted(candidates.items()):
        # Deterministic and loss-aware. Prefer the largest source body as the
        # best candidate for the most complete visible conversation. Keep
        # alternate path evidence without generating duplicate index rows.
        chosen = max(paths, key=lambda x: (
            x.stat().st_size, x.stat().st_mtime_ns, str(x)
        ))
        alternates = [p for p in paths if p != chosen]
        signature = _source_stat(chosen, [
            (str(p), _source_stat(p)) for p in sorted(alternates)
        ])
        key = "claude-code:_:" + native_id
        previous = ledger.get(key)
        if (previous and not full and
                (previous.get("extra") or {}).get("source_stat") == signature):
            verified += 1
            continue
        rec = _claude_gui_record(chosen, _unslug_project(chosen.parent.name))
        if rec is None:
            blocked += 1
            continue
        verified += 1
        rec.extra["source_stat"] = signature
        rec.extra["alternate_gui_carrier_paths"] = [str(p) for p in alternates]
        rec.extra["multiple_carriers_not_additional_dialogues"] = bool(alternates)
        _write_new(out_root, "claude-code", rec, ledger, rows,
                   since=since, full=full)
    if rows:
        append_index(out_root, "claude-code", rows)
    write_manifest(out_root, "claude-code", extra={
        "collection_policy": "PROVEN_HUMAN_GUI_ONLY_V1",
        "verified_gui": verified, "excluded_cli_sdk_unproven": excluded,
        "alternate_gui_carriers": duplicate_gui_carriers,
        "ambiguous_or_empty": blocked,
        "new_sessions_this_run": len(rows),
    })
    return {"source": "claude-code", "mode": "human-gui",
            "verified_gui": verified, "excluded": excluded, "ambiguous": blocked,
            "alternate_carriers": duplicate_gui_carriers,
            "new_sessions_this_run": len(rows),
            "unique_sessions": len(load_session_ledger(out_root, "claude-code"))}


def collect_cursor_gui(
    out_root: Path, *, db: Optional[Path] = None,
    workspace_storage: Optional[Path] = None,
    since: Optional[str] = None, full: bool = False,
) -> Dict[str, Any]:
    """Only human Cursor composer bubbles, not subagents, tools or raw payloads."""
    from thoth.common import open_sqlite_readonly
    from thoth.sources.cursor import (
        _default_db, _default_workspace_storage, _read_workspace_map,
        _parse_header_value, _ms_to_iso,
    )
    db = db or _default_db()
    if not db.is_file():
        return {"source": "cursor", "mode": "human-gui", "status": "PARTIAL",
                "reason": "CURSOR_GUI_STATE_MISSING", "new_sessions_this_run": 0}
    ws_root = workspace_storage or _default_workspace_storage()
    ws_map = _read_workspace_map(ws_root)
    ledger = load_session_ledger(out_root, "cursor")
    rows: List[Dict[str, Any]] = []
    excluded_subagent = 0
    excluded_empty = 0
    verified = 0
    try:
        conn = open_sqlite_readonly(db)
        headers = list(conn.execute(
            "SELECT composerId,workspaceId,createdAt,lastUpdatedAt,isArchived,"
            "isSubagent,recency,checkpointAt,subagentTypeName,value "
            "FROM composerHeaders"
        ))
        for header in headers:
            cid, wsid, created_ms, updated_ms, is_archived, is_subagent, _, _, _, headval = header
            if is_subagent or not cid:
                excluded_subagent += 1
                continue
            visible = []
            size = 0
            for _, raw in conn.execute(
                "SELECT key,value FROM cursorDiskKV WHERE key LIKE ? ORDER BY key",
                (f"bubbleId:{cid}:%",)
            ):
                if len(visible) >= MAX_VISIBLE_MESSAGES or size >= MAX_VISIBLE_CHARS:
                    break
                try:
                    item = json.loads(raw.decode("utf-8","replace") if isinstance(raw,bytes)
                                      else raw) if isinstance(raw,(str,bytes)) else raw
                except (TypeError, ValueError):
                    continue
                if not isinstance(item, dict):
                    continue
                role = {1:"user", 2:"assistant"}.get(item.get("type"))
                if not role:
                    continue
                text = item.get("text") or item.get("richText") or item.get("content")
                if isinstance(text, dict):
                    text = text.get("text") or text.get("content")
                if not isinstance(text, str) or not text.strip():
                    continue
                txt = text[:MAX_EVENT_CHARS]
                if size + len(txt) > MAX_VISIBLE_CHARS:
                    break
                visible.append(_new_message(role, txt))
                size += len(txt)
            if not any(e["role"]=="user" for e in visible):
                excluded_empty += 1
                continue
            verified += 1
            head = _parse_header_value(headval)
            title = head.get("name") or head.get("title") or next(
                (e["content"].strip().split("\n")[0] for e in visible if e["role"]=="user"), "Cursor conversation"
            )
            title = str(title)[:160]
            fp = hashlib.sha256(json.dumps(
                [visible,title,updated_ms],ensure_ascii=False,sort_keys=True,
            ).encode("utf-8")).hexdigest()[:24]
            rec = SessionRecord(
                source="cursor", session_id=str(cid), title=title,
                created_at=_ms_to_iso(created_ms), updated_at=_ms_to_iso(updated_ms),
                project_hint=ws_map.get(wsid) or (str(wsid) if wsid else None),
                message_count=len(visible), carrier_paths=[f"cursor-db:{cid}"],
                events=visible, capture_status=CAPTURE_PARTIAL,
                content_fingerprint="cursor-gui-visible:" + fp,
                native_thread_id=str(cid), session_kind="CURSOR_GUI_USER",
                client_origin="cursor-composer",
                extra={"gui_origin_verified":True, "dialogue_text_only":True,
                       "raw_payloads_archived":False},
            )
            _write_new(out_root, "cursor", rec, ledger, rows, since=since, full=full)
        conn.close()
    except Exception:
        return {"source":"cursor","mode":"human-gui","status":"PARTIAL",
                "reason":"CURSOR_GUI_READ_FAILED","new_sessions_this_run":0}
    if rows:
        append_index(out_root,"cursor",rows)
    write_manifest(out_root,"cursor",extra={
        "collection_policy":"PROVEN_HUMAN_GUI_ONLY_V1",
        "verified_gui":verified, "excluded_subagents":excluded_subagent,
        "excluded_without_user_message":excluded_empty,
        "new_sessions_this_run":len(rows),
    })
    return {"source":"cursor","mode":"human-gui",
            "verified_gui":verified,
            "excluded":excluded_subagent+excluded_empty,
            "new_sessions_this_run":len(rows),
            "unique_sessions":len(load_session_ledger(out_root,"cursor"))}
