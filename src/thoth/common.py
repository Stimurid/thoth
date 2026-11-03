"""Shared output layout + helpers for Thoth source collectors.

Every source produces the same tree so downstream (HYDRA / Hub / litops)
can treat them uniformly. Index rows are append-only versioned ledger
entries; the unified catalog keeps the latest row per identity_key.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote


INDEX_ROW_SCHEMA = "thoth.index_row.v2"
CATALOG_SCHEMA = "thoth.unified_catalog.v1"
DIGEST_SCHEMA = "thoth.daily_digest.v1"

CAPTURE_FULL = "FULL"
CAPTURE_METADATA_ONLY = "METADATA_ONLY"
CAPTURE_PARTIAL = "PARTIAL"
CAPTURE_POINTER = "POINTER"


# ─── time ──────────────────────────────────────────────────────────────

def to_iso_utc(value: Any) -> Optional[str]:
    """Accept unix float, int, datetime, or ISO string; return canonical ISO UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── UUID / identity ───────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def extract_uuid(text: str) -> Optional[str]:
    """Return the last UUID substring in *text*, or None.

    Codex rollout filenames historically prefixed a timestamp before the
    real thread UUID (`rollout-2026-09-03T...-<uuid>.jsonl`). Prefer the
    trailing match so identity keys normalize to the Desktop thread id.
    """
    if not text:
        return None
    matches = _UUID_RE.findall(text)
    return matches[-1].lower() if matches else None


def make_identity_key(source: str, account_id: Optional[str], session_id: str) -> str:
    acct = (account_id or "").strip() or "_"
    return f"{source}:{acct}:{session_id}"


def stable_source_id(label: Optional[str], root: Path) -> str:
    """Stable account / archive id for a Nisaba root.

    Prefer an explicit label; else hash the resolved path so two archives
    never collide even when conversation UUIDs overlap.
    """
    if label and str(label).strip():
        cleaned = _UNSAFE.sub("_", str(label).strip()).strip("_.")
        return cleaned or "account"
    digest = hashlib.sha256(str(root.resolve()).encode("utf-8", "replace")).hexdigest()
    return f"nisaba-{digest[:12]}"


# ─── session record (canonical shape) ──────────────────────────────────

@dataclass
class SessionRecord:
    """One archived session, source-agnostic.

    `events` is the full event stream from the source, verbatim as JSON-
    serializable dicts (or pointer stubs). `summary` is a short
    human-readable index entry.
    """
    source: str                       # "codex" | "claude-code" | "cursor" | "chatgpt"
    session_id: str
    title: str
    created_at: Optional[str]         # ISO UTC
    updated_at: Optional[str]         # ISO UTC
    project_hint: Optional[str]       # repo path, workspace folder, etc.
    message_count: int
    carrier_paths: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    account_id: Optional[str] = None
    capture_status: str = CAPTURE_FULL
    content_fingerprint: Optional[str] = None
    native_project_id: Optional[str] = None
    native_thread_id: Optional[str] = None
    open_hint: Optional[str] = None   # e.g. codex://threads/<uuid> — hint only

    def day_folder(self) -> str:
        iso = self.updated_at or self.created_at or now_iso()
        return iso[:10].replace("-", "/")

    def safe_filename(self) -> str:
        # Include account when present so multi-account chatgpt paths never collide.
        if self.account_id and self.account_id not in ("_", ""):
            return _safe_name(f"{self.account_id}__{self.session_id}", 120)
        return _safe_name(self.session_id, 80)

    def identity_key(self) -> str:
        return make_identity_key(self.source, self.account_id, self.session_id)

    def ensure_fingerprint(self) -> str:
        if self.content_fingerprint:
            return self.content_fingerprint
        self.content_fingerprint = fingerprint_carriers(self.carrier_paths) or fingerprint_metadata(
            self.updated_at, self.title, self.message_count, self.capture_status
        )
        return self.content_fingerprint

    def index_row(self, *, version: int = 1) -> Dict[str, Any]:
        fp = self.ensure_fingerprint()
        row: Dict[str, Any] = {
            "schema": INDEX_ROW_SCHEMA,
            "source": self.source,
            "account_id": self.account_id,
            "session_id": self.session_id,
            "identity_key": self.identity_key(),
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_hint": self.project_hint,
            "message_count": self.message_count,
            "carrier_paths": self.carrier_paths,
            "capture_status": self.capture_status,
            "content_fingerprint": fp,
            "version": version,
            "indexed_at": now_iso(),
            "extra": self.extra,
        }
        if self.native_project_id is not None:
            row["native_project_id"] = self.native_project_id
        if self.native_thread_id is not None:
            row["native_thread_id"] = self.native_thread_id
        if self.open_hint is not None:
            row["open_hint"] = self.open_hint
        return row


# ─── naming ────────────────────────────────────────────────────────────

_UNSAFE = re.compile(r"[^A-Za-z0-9._\-]")


def _safe_name(text: str, limit: int = 80) -> str:
    cleaned = _UNSAFE.sub("_", text or "").strip("_.")
    return (cleaned or "untitled")[:limit]


# ─── fingerprints ──────────────────────────────────────────────────────

def fingerprint_carriers(paths: Iterable[str]) -> Optional[str]:
    """mtime+size fingerprint over existing carrier files. Missing => PARTIAL signal."""
    parts: List[str] = []
    any_file = False
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            any_file = True
            st = path.stat()
            parts.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
        else:
            parts.append(f"{path.name}:MISSING")
    if not parts:
        return None
    digest = hashlib.sha256("|".join(parts).encode("utf-8", "replace")).hexdigest()[:20]
    kind = "stat" if any_file else "missing"
    return f"{kind}:{digest}"


def fingerprint_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()[:20]


def fingerprint_metadata(
    updated_at: Optional[str],
    title: str,
    message_count: int,
    capture_status: str,
) -> str:
    blob = f"{updated_at or ''}|{title}|{message_count}|{capture_status}"
    return "meta:" + hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:20]


# ─── SQLite read-only ──────────────────────────────────────────────────

def open_sqlite_readonly(path: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open SQLite read-only with WAL-safe URI variants and query_only.

    Tries plain `mode=ro` first (allows reading a live WAL), then
    `immutable=1` as a fallback for locked/copied snapshots.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    candidates = [
        f"file:{quote(str(path))}?mode=ro",
        path.as_uri() + "?mode=ro",
        f"file:{quote(str(path))}?mode=ro&immutable=1",
        path.as_uri() + "?mode=ro&immutable=1",
    ]
    last_err: Optional[BaseException] = None
    for uri in candidates:
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=timeout)
            try:
                conn.execute("PRAGMA query_only=ON")
            except sqlite3.Error:
                pass
            # Probe readability
            conn.execute("SELECT 1").fetchone()
            return conn
        except (sqlite3.Error, OSError) as err:
            last_err = err
            try:
                conn.close()  # type: ignore[name-defined]
            except Exception:
                pass
    raise sqlite3.OperationalError(f"cannot open readonly sqlite: {path}: {last_err}")


# ─── IO ────────────────────────────────────────────────────────────────

def write_session(root: Path, rec: SessionRecord) -> Optional[Path]:
    """Write one session: jsonl of events + .md summary.

    METADATA_ONLY / POINTER records skip event-body rewrite and only
    refresh a short markdown stub when useful. Returns .jsonl path or None.
    """
    if rec.capture_status in (CAPTURE_METADATA_ONLY, CAPTURE_POINTER) and not rec.events:
        day = rec.day_folder()
        out_dir = root / rec.source / "sessions" / day
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / f"{rec.safe_filename()}.md"
        md_path.write_text(_render_markdown(rec), encoding="utf-8")
        return None

    day = rec.day_folder()
    out_dir = root / rec.source / "sessions" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    base = rec.safe_filename()
    jsonl_path = out_dir / f"{base}.jsonl"
    md_path = out_dir / f"{base}.md"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for ev in rec.events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    md_path.write_text(_render_markdown(rec), encoding="utf-8")
    return jsonl_path


def append_index(root: Path, source: str, rows: Iterable[Dict[str, Any]]) -> int:
    """Append rows to <root>/<source>/index.jsonl. Returns count written."""
    idx = root / source / "index.jsonl"
    idx.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with idx.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def iter_index_rows(root: Path, source: str) -> Iterable[Dict[str, Any]]:
    idx = root / source / "index.jsonl"
    if not idx.exists():
        return
    try:
        with idx.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


def read_watermark(root: Path, source: str) -> Optional[str]:
    """Return the max updated_at seen in <source>/index.jsonl, or None.

    Kept for backward compatibility. Prefer per-session ledger checks.
    """
    best: Optional[str] = None
    for row in iter_index_rows(root, source):
        ts = row.get("updated_at") or row.get("created_at")
        if ts and (best is None or ts > best):
            best = ts
    return best


def _row_identity(row: Dict[str, Any]) -> Optional[str]:
    key = row.get("identity_key")
    if isinstance(key, str) and key:
        return key
    source = row.get("source")
    sid = row.get("session_id")
    if not source or not sid:
        return None
    return make_identity_key(str(source), row.get("account_id"), str(sid))


def load_session_ledger(root: Path, source: str) -> Dict[str, Dict[str, Any]]:
    """Latest-wins map identity_key -> index row for one source."""
    latest: Dict[str, Dict[str, Any]] = {}
    for row in iter_index_rows(root, source):
        key = _row_identity(row)
        if not key:
            continue
        prev = latest.get(key)
        if prev is None:
            latest[key] = row
            continue
        prev_v = int(prev.get("version") or 0)
        cur_v = int(row.get("version") or 0)
        if cur_v >= prev_v:
            # Equal version: prefer newer indexed_at / updated_at
            prev_ts = prev.get("indexed_at") or prev.get("updated_at") or ""
            cur_ts = row.get("indexed_at") or row.get("updated_at") or ""
            if cur_v > prev_v or cur_ts >= prev_ts:
                latest[key] = row
    return latest


def next_version(ledger: Dict[str, Dict[str, Any]], identity_key: str) -> int:
    prev = ledger.get(identity_key)
    if not prev:
        return 1
    return int(prev.get("version") or 0) + 1


def should_emit(
    rec: SessionRecord,
    ledger: Dict[str, Dict[str, Any]],
    *,
    since: Optional[str] = None,
    force: bool = False,
) -> Tuple[bool, str]:
    """Decide whether to append a new ledger version for *rec*.

    Returns (emit?, reason). Per-session fingerprints beat a global
    watermark so an older session that later grew still gets re-indexed.
    """
    if force:
        return True, "force"
    key = rec.identity_key()
    fp = rec.ensure_fingerprint()
    prev = ledger.get(key)
    if prev is None:
        # New session: optional global since gate still applies as a soft
        # filter for brand-new ids discovered after a watermark.
        ts = rec.updated_at or rec.created_at or ""
        if since and ts and ts <= since:
            # Still emit if caller wants discovery of METADATA gaps etc.;
            # for brand-new keys below watermark we skip to preserve
            # historical incremental behaviour.
            return False, "new_but_not_after_watermark"
        return True, "new"
    prev_fp = prev.get("content_fingerprint")
    if prev_fp and prev_fp == fp:
        return False, "unchanged_fingerprint"
    prev_ts = prev.get("updated_at") or prev.get("created_at") or ""
    cur_ts = rec.updated_at or rec.created_at or ""
    if cur_ts and prev_ts and cur_ts < prev_ts and prev_fp == fp:
        return False, "older_unchanged"
    return True, "changed"


def write_manifest(root: Path, source: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """Rebuild <source>/manifest.json from the index (latest-wins counts)."""
    ledger = load_session_ledger(root, source)
    sessions = len(ledger)
    rows_total = sum(1 for _ in iter_index_rows(root, source))
    oldest: Optional[str] = None
    newest: Optional[str] = None
    projects: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    accounts: Dict[str, int] = {}
    for row in ledger.values():
        ts = row.get("updated_at") or row.get("created_at")
        if ts:
            if oldest is None or ts < oldest:
                oldest = ts
            if newest is None or ts > newest:
                newest = ts
        p = row.get("project_hint") or ""
        if p:
            projects[p] = projects.get(p, 0) + 1
        st = row.get("capture_status") or CAPTURE_FULL
        by_status[st] = by_status.get(st, 0) + 1
        acct = row.get("account_id") or "_"
        accounts[str(acct)] = accounts.get(str(acct), 0) + 1
    manifest = {
        "source": source,
        "sessions": sessions,
        "index_rows_total": rows_total,
        "unique_sessions": sessions,
        "oldest_session_updated_at": oldest,
        "newest_session_updated_at": newest,
        "projects_distinct": len(projects),
        "accounts_distinct": len(accounts),
        "capture_status_counts": by_status,
        "built_at": now_iso(),
        "index_schema": INDEX_ROW_SCHEMA,
    }
    if extra:
        manifest.update(extra)
    out_dir = root / source
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )


# ─── unified catalog / digest / query ──────────────────────────────────

def discover_sources(root: Path) -> List[str]:
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and (p / "index.jsonl").is_file()
    )


def build_unified_catalog(root: Path) -> Dict[str, Any]:
    """Write metadata-only unified_catalog.jsonl + daily_digest.jsonl.

    Never embeds chat bodies. Latest ledger row wins per identity_key.
    """
    sources = discover_sources(root)
    latest: Dict[str, Dict[str, Any]] = {}
    for src in sources:
        for key, row in load_session_ledger(root, src).items():
            prev = latest.get(key)
            if prev is None:
                latest[key] = row
                continue
            if int(row.get("version") or 0) >= int(prev.get("version") or 0):
                latest[key] = row

    catalog_path = root / "unified_catalog.jsonl"
    digest_path = root / "daily_digest.jsonl"
    n = 0
    with catalog_path.open("w", encoding="utf-8") as fcat, digest_path.open("w", encoding="utf-8") as fdig:
        for key in sorted(latest.keys()):
            row = latest[key]
            ts = row.get("updated_at") or row.get("created_at") or ""
            date = ts[:10] if ts else ""
            # Strip any accidental body fields from extra for catalog safety.
            extra = dict(row.get("extra") or {})
            extra.pop("events", None)
            extra.pop("body", None)
            catalog_row = {
                "schema": CATALOG_SCHEMA,
                "identity_key": key,
                "source": row.get("source"),
                "account_id": row.get("account_id"),
                "session_id": row.get("session_id"),
                "title": row.get("title"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "project_hint": row.get("project_hint"),
                "native_project_id": row.get("native_project_id"),
                "native_thread_id": row.get("native_thread_id"),
                "message_count": row.get("message_count"),
                "capture_status": row.get("capture_status"),
                "content_fingerprint": row.get("content_fingerprint"),
                "version": row.get("version"),
                "carrier_paths": row.get("carrier_paths") or [],
                "open_hint": row.get("open_hint"),
                "extra": extra,
            }
            digest_row = {
                "schema": DIGEST_SCHEMA,
                "date": date,
                **{k: catalog_row[k] for k in catalog_row if k != "schema"},
            }
            fcat.write(json.dumps(catalog_row, ensure_ascii=False) + "\n")
            fdig.write(json.dumps(digest_row, ensure_ascii=False) + "\n")
            n += 1

    meta = {
        "schema": CATALOG_SCHEMA,
        "catalog": str(catalog_path),
        "digest": str(digest_path),
        "sessions": n,
        "sources": sources,
        "built_at": now_iso(),
    }
    (root / "unified_catalog.manifest.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return meta


def query_catalog(
    root: Path,
    *,
    source: Optional[str] = None,
    account_id: Optional[str] = None,
    session_id: Optional[str] = None,
    capture_status: Optional[str] = None,
    project_hint_substr: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Bounded JSON query over unified_catalog.jsonl (rebuild if missing)."""
    catalog = root / "unified_catalog.jsonl"
    if not catalog.is_file():
        build_unified_catalog(root)
    out: List[Dict[str, Any]] = []
    if not catalog.is_file():
        return out
    with catalog.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if source and row.get("source") != source:
                continue
            if account_id and str(row.get("account_id") or "") != account_id:
                continue
            if session_id and str(row.get("session_id") or "") != session_id:
                continue
            if capture_status and row.get("capture_status") != capture_status:
                continue
            if project_hint_substr:
                hint = str(row.get("project_hint") or "")
                if project_hint_substr.casefold() not in hint.casefold():
                    continue
            out.append(row)
            if len(out) >= limit:
                break
    return out


# ─── markdown rendering ────────────────────────────────────────────────

def _render_markdown(rec: SessionRecord) -> str:
    """Minimal front-matter + flat message dump. Source-faithful JSON stays in .jsonl."""
    lines: List[str] = []
    lines.append("---")
    lines.append(f'schema: "{INDEX_ROW_SCHEMA}"')
    lines.append(f'source: "{rec.source}"')
    if rec.account_id:
        lines.append(f'account_id: "{_md_escape(rec.account_id)}"')
    lines.append(f'id: "{rec.session_id}"')
    lines.append(f'identity_key: "{_md_escape(rec.identity_key())}"')
    lines.append(f'title: "{_md_escape(rec.title)}"')
    lines.append(f"capture_status: {rec.capture_status}")
    if rec.created_at:
        lines.append(f"created_at: {rec.created_at}")
    if rec.updated_at:
        lines.append(f"updated_at: {rec.updated_at}")
    if rec.project_hint:
        lines.append(f'project_hint: "{_md_escape(rec.project_hint)}"')
    if rec.open_hint:
        lines.append(f'open_hint: "{_md_escape(rec.open_hint)}"')
    lines.append(f"message_count: {rec.message_count}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {rec.title}")
    lines.append("")
    if rec.capture_status == CAPTURE_METADATA_ONLY:
        lines.append("_METADATA_ONLY: Desktop/index metadata present; rollout body absent._")
        lines.append("")
    if rec.capture_status == CAPTURE_PARTIAL:
        lines.append("_PARTIAL: source evidence missing or corrupt; recovered best-effort._")
        lines.append("")
    for i, ev in enumerate(rec.events):
        role = ev.get("role") or ev.get("author") or ev.get("type") or "event"
        ts = ev.get("timestamp") or ev.get("created_at")
        content = ev.get("content") or ev.get("text") or ev.get("message") or ""
        if isinstance(content, (list, dict)):
            content = json.dumps(content, ensure_ascii=False)
        content = str(content)
        if ts:
            lines.append(f"## [{i:04d}] {role} — {ts}")
        else:
            lines.append(f"## [{i:04d}] {role}")
        lines.append("")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


def _md_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')
