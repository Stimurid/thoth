"""Nisaba output as a Thoth source.

Reads one or more existing Nisaba archives (see github.com/Stimurid/nisaba)
and exposes each ChatGPT conversation as a SessionRecord with
source="chatgpt". Multiple roots are keyed by explicit account labels or
stable hashed source IDs so the same conversation UUID across accounts
never collides.

Input shape (Nisaba 0.1.0):
    <nisaba_root>/conversations.jsonl
    <nisaba_root>/account_manifest.json          # optional
    <nisaba_root>/conversations/<uuid>/conversation.json
                                       conversation.md
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from thoth.common import (
    CAPTURE_PARTIAL,
    CAPTURE_POINTER,
    SessionRecord,
    append_index,
    fingerprint_carriers,
    fingerprint_metadata,
    load_session_ledger,
    next_version,
    should_emit,
    stable_source_id,
    to_iso_utc,
    write_manifest,
)


NisabaSpec = Union[Path, Tuple[Path, Optional[str]], Dict[str, Any]]
_UUID = re.compile(r"^[a-fA-F0-9]{8}-(?:[a-fA-F0-9]{4}-){3}[a-fA-F0-9]{12}$")


def _scalar(value: Any, *, limit: int = 160) -> Optional[str]:
    """Only bounded primitive source metadata may enter the index."""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)[:limit]
    return None


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


def _read_account_manifest(root: Path) -> Dict[str, Any]:
    path = root / "account_manifest.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {"_corrupt": True, "path": str(path)}


def normalize_nisaba_specs(
    specs: Optional[Sequence[NisabaSpec]],
    *,
    labels: Optional[Sequence[Optional[str]]] = None,
) -> List[Tuple[Path, str]]:
    """Normalize CLI / API inputs into (root, account_id) pairs."""
    out: List[Tuple[Path, str]] = []
    if not specs:
        return out
    label_list = list(labels or [])
    for i, spec in enumerate(specs):
        label: Optional[str] = label_list[i] if i < len(label_list) else None
        root: Path
        if isinstance(spec, dict):
            root = Path(spec.get("path") or spec.get("root") or "")
            label = spec.get("account") or spec.get("label") or label
        elif isinstance(spec, tuple):
            root = Path(spec[0])
            if len(spec) > 1 and spec[1]:
                label = str(spec[1])
        else:
            root = Path(spec)
        account_id = stable_source_id(label, root)
        out.append((root, account_id))
    # Explicit account label collision would silently merge two archives.
    # Fail before writing any index if two roots claim the same identity.
    owners: Dict[str, str] = {}
    for root, identity in out:
        canonical = str(root.resolve()).casefold()
        if identity in owners and owners[identity] != canonical:
            raise ValueError("NISABA_ACCOUNT_LABEL_COLLISION")
        owners[identity] = canonical
    return out


def _conv_to_record(
    nisaba_root: Path,
    listing: Dict[str, Any],
    *,
    account_id: str,
    account_manifest: Dict[str, Any],
) -> SessionRecord:
    cid = str(listing.get("conversation_id") or "")
    # Conversation IDs come from external archive listings, not our own paths.
    # Never accept traversal, a drive prefix, or a path-like value here.
    valid_cid = bool(_UUID.fullmatch(cid))
    conversations_root = nisaba_root / "conversations"
    # The base directory itself may be a symlink outside the archive. Never
    # trust containment relative to that resolved symlink target.
    safe_base = (not conversations_root.is_symlink() and
                 conversations_root.resolve().is_relative_to(nisaba_root.resolve()))
    valid_cid = valid_cid and safe_base
    cdir = conversations_root / (cid if valid_cid else "_invalid_id_")
    if valid_cid and (cdir.is_symlink() or not cdir.resolve().is_relative_to(nisaba_root.resolve())):
        valid_cid = False
    conv_json = cdir / "conversation.json"
    md_path = cdir / "conversation.md"

    created = to_iso_utc(listing.get("create_time")) or listing.get("create_time")
    updated = to_iso_utc(listing.get("update_time")) or listing.get("update_time")
    if isinstance(created, (int, float)):
        created = to_iso_utc(created)
    if isinstance(updated, (int, float)):
        updated = to_iso_utc(updated)
    title = _scalar(listing.get("title")) or "Untitled"
    project_id = _scalar(listing.get("project_id") or listing.get("gizmo_id"))
    project_name = _scalar(listing.get("project_name"))

    message_count = 0
    events: List[Dict[str, Any]] = []
    capture = CAPTURE_POINTER
    carriers: List[str] = []

    if not valid_cid:
        capture = CAPTURE_PARTIAL
        fp = fingerprint_metadata(updated, str(title), 0, capture)
        return SessionRecord(
            source="chatgpt",
            session_id="invalid-" + hashlib.sha256(cid.encode("utf-8", "replace")).hexdigest()[:20],
            title=str(title)[:160],
            created_at=created if isinstance(created, str) else to_iso_utc(created),
            updated_at=updated if isinstance(updated, str) else to_iso_utc(updated),
            project_hint=None,
            message_count=0,
            carrier_paths=[],
            account_id=account_id,
            capture_status=capture,
            content_fingerprint=fp,
            extra={"error": "listing_invalid_conversation_id", "nisaba_root": str(nisaba_root)},
            events=[],
        )

    if valid_cid and not conv_json.is_symlink() and conv_json.is_file():
        carriers.append(str(conv_json))
        try:
            body = json.loads(conv_json.read_text(encoding="utf-8"))
            mapping = body.get("mapping") or {}
            message_count = len(mapping)
            created = to_iso_utc(body.get("create_time")) or created
            body_updated = to_iso_utc(body.get("update_time"))
            # The listing may be fresher than a stale/incomplete saved body.
            if body_updated and (not updated or body_updated > updated):
                updated = body_updated
            events = [{
                "ref": "conversation.json",
                "path": str(conv_json),
                "mapping_entries": message_count,
            }]
            if valid_cid and not md_path.is_symlink() and md_path.is_file():
                carriers.append(str(md_path))
                events.append({"ref": "conversation.md", "path": str(md_path)})
        except Exception as err:
            capture = CAPTURE_PARTIAL
            events = [{"ref": "conversation.json", "path": str(conv_json), "error": str(err)}]
    else:
        capture = CAPTURE_PARTIAL
        if valid_cid and not md_path.is_symlink() and md_path.is_file():
            carriers.append(str(md_path))
            events = [{"ref": "conversation.md", "path": str(md_path)}]

    hint = project_name or (f"project:{project_id}" if project_id else None)
    fp = fingerprint_carriers(carriers) or fingerprint_metadata(
        updated if isinstance(updated, str) else to_iso_utc(updated),
        str(title),
        message_count,
        capture,
    )

    manifest_meta = {
        k: _scalar(account_manifest.get(k), limit=100)
        for k in ("account_label", "export_id", "exported_at", "source")
        if k in account_manifest and _scalar(account_manifest.get(k), limit=100)
    }
    # Never copy secrets from manifest; only non-credential labels.
    for banned in ("token", "password", "cookie", "authorization", "api_key"):
        manifest_meta.pop(banned, None)

    extra = {
        "chatgpt": {
            "project_id": project_id,
            "project_name": project_name,
            "archived": bool(listing.get("archived")),
            # Raw memberships may contain nested messages, tokens or bodies.
            # Native project_id is the sole allowed membership evidence here.
            "membership_source": "NISABA_INDEX_PROJECT_ID_ONLY",
        },
        "nisaba_conversation_dir": str(cdir),
        "nisaba_root": str(nisaba_root),
        "account_manifest_keys": sorted(manifest_meta.keys()),
        "account_manifest": manifest_meta,
    }

    return SessionRecord(
        source="chatgpt",
        session_id=cid,
        title=str(title)[:160],
        created_at=created if isinstance(created, str) else to_iso_utc(created),
        updated_at=updated if isinstance(updated, str) else to_iso_utc(updated),
        project_hint=hint,
        message_count=message_count,
        carrier_paths=carriers,
        account_id=account_id,
        capture_status=capture,
        content_fingerprint=fp,
        native_project_id=str(project_id) if project_id else None,
        native_thread_id=cid,
        extra=extra,
        events=events,
    )


def collect_one(
    out_root: Path,
    *,
    nisaba_root: Path,
    account_id: str,
    since: Optional[str] = None,
    full: bool = False,
    ledger: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not nisaba_root.is_dir():
        return {
            "source": "chatgpt",
            "account_id": account_id,
            "skipped": True,
            "reason": f"nisaba root not found: {nisaba_root}",
            "capture_status": CAPTURE_PARTIAL,
        }

    ledger = ledger if ledger is not None else load_session_ledger(out_root, "chatgpt")
    listing_path = nisaba_root / "conversations.jsonl"
    account_manifest = _read_account_manifest(nisaba_root)
    rows: List[Dict[str, Any]] = []
    new_count = 0
    skipped_unchanged = 0
    skipped_old = 0
    partial_count = 0
    listed = 0

    if not listing_path.is_file():
        # Recoverable PARTIAL: root exists but listing missing.
        return {
            "source": "chatgpt",
            "account_id": account_id,
            "nisaba_root": str(nisaba_root),
            "skipped": False,
            "capture_status": CAPTURE_PARTIAL,
            "reason": f"conversations.jsonl missing: {listing_path}",
            "new_sessions_this_run": 0,
            "partial": True,
        }

    for listing in _read_jsonl(listing_path):
        listed += 1
        rec = _conv_to_record(
            nisaba_root, listing, account_id=account_id, account_manifest=account_manifest,
        )
        if rec.capture_status == CAPTURE_PARTIAL:
            partial_count += 1
        emit, reason = should_emit(rec, ledger, since=since, force=full)
        if not emit:
            if reason == "unchanged_fingerprint":
                skipped_unchanged += 1
            else:
                skipped_old += 1
            continue
        ver = next_version(ledger, rec.identity_key())
        # Pointer-based: do NOT re-write Nisaba conversation bodies into Thoth.
        row = rec.index_row(version=ver)
        rows.append(row)
        ledger[rec.identity_key()] = row
        new_count += 1

    if rows:
        append_index(out_root, "chatgpt", rows)

    return {
        "source": "chatgpt",
        "account_id": account_id,
        "nisaba_root": str(nisaba_root),
        "listed": listed,
        "new_sessions_this_run": new_count,
        "skipped_unchanged": skipped_unchanged,
        "skipped_as_old": skipped_old,
        "partial_this_run": partial_count,
    }


def collect(
    out_root: Path,
    *,
    nisaba_root: Optional[Path] = None,
    nisaba_roots: Optional[Sequence[NisabaSpec]] = None,
    nisaba_accounts: Optional[Sequence[Optional[str]]] = None,
    since: Optional[str] = None,
    full: bool = False,
) -> Dict[str, Any]:
    """Index one or many Nisaba roots into <out_root>/chatgpt/."""
    specs: List[NisabaSpec] = []
    if nisaba_roots:
        specs.extend(list(nisaba_roots))
    if nisaba_root is not None:
        specs.append(nisaba_root)
    pairs = normalize_nisaba_specs(specs, labels=nisaba_accounts)
    if not pairs:
        return {"source": "chatgpt", "skipped": True, "reason": "no nisaba roots"}

    ledger = load_session_ledger(out_root, "chatgpt")
    per_account: List[Dict[str, Any]] = []
    total_new = 0
    for root, account_id in pairs:
        res = collect_one(
            out_root,
            nisaba_root=root,
            account_id=account_id,
            since=since,
            full=full,
            ledger=ledger,
        )
        per_account.append(res)
        total_new += int(res.get("new_sessions_this_run") or 0)

    write_manifest(out_root, "chatgpt", extra={
        "accounts": [
            {"account_id": a, "nisaba_root": str(r)} for r, a in pairs
        ],
        "per_account": per_account,
        "new_sessions_this_run": total_new,
        "since_watermark": since,
        "note": "sessions are not re-emitted; carrier_paths point into Nisaba archives",
        "unique_sessions_indexed": len(load_session_ledger(out_root, "chatgpt")),
    })
    return {
        "source": "chatgpt",
        "accounts": len(pairs),
        "new_sessions_this_run": total_new,
        "per_account": per_account,
        "unique_sessions": len(load_session_ledger(out_root, "chatgpt")),
    }
