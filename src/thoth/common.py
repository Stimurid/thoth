"""Shared output layout + helpers for Thoth source collectors.

Every source produces the same tree so downstream (nisaba/litops) can
treat them uniformly.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


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
            # Z suffix or offset
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── session record (canonical shape) ──────────────────────────────────

@dataclass
class SessionRecord:
    """One archived session, source-agnostic.

    `events` is the full event stream from the source, verbatim as JSON-
    serializable dicts. `summary` is a short human-readable index entry.
    """
    source: str                       # "codex" | "claude-code" | "cursor"
    session_id: str
    title: str
    created_at: Optional[str]         # ISO UTC
    updated_at: Optional[str]         # ISO UTC
    project_hint: Optional[str]       # repo path, workspace folder, etc.
    message_count: int
    carrier_paths: List[str] = field(default_factory=list)  # original file paths
    extra: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def day_folder(self) -> str:
        iso = self.updated_at or self.created_at or now_iso()
        return iso[:10].replace("-", "/")        # "2026/10/10"

    def safe_filename(self) -> str:
        return _safe_name(self.session_id, 80)

    def index_row(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "session_id": self.session_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_hint": self.project_hint,
            "message_count": self.message_count,
            "carrier_paths": self.carrier_paths,
            "extra": self.extra,
        }


# ─── naming ────────────────────────────────────────────────────────────

_UNSAFE = re.compile(r"[^A-Za-z0-9._\-]")

def _safe_name(text: str, limit: int = 80) -> str:
    cleaned = _UNSAFE.sub("_", text or "").strip("_.")
    return (cleaned or "untitled")[:limit]


# ─── IO ────────────────────────────────────────────────────────────────

def write_session(root: Path, rec: SessionRecord) -> Path:
    """Write one session: jsonl of events + .md summary. Returns .jsonl path."""
    day = rec.day_folder()                              # "2026/10/10"
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


def read_watermark(root: Path, source: str) -> Optional[str]:
    """Return the max updated_at seen in <source>/index.jsonl, or None."""
    idx = root / source / "index.jsonl"
    if not idx.exists():
        return None
    best: Optional[str] = None
    try:
        with idx.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = row.get("updated_at") or row.get("created_at")
                if ts and (best is None or ts > best):
                    best = ts
    except OSError:
        return None
    return best


def write_manifest(root: Path, source: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """Rebuild <source>/manifest.json from the index."""
    idx = root / source / "index.jsonl"
    sessions = 0
    oldest: Optional[str] = None
    newest: Optional[str] = None
    projects: Dict[str, int] = {}
    if idx.exists():
        with idx.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                sessions += 1
                ts = row.get("updated_at") or row.get("created_at")
                if ts:
                    if oldest is None or ts < oldest:
                        oldest = ts
                    if newest is None or ts > newest:
                        newest = ts
                p = row.get("project_hint") or ""
                if p:
                    projects[p] = projects.get(p, 0) + 1
    manifest = {
        "source": source,
        "sessions": sessions,
        "oldest_session_updated_at": oldest,
        "newest_session_updated_at": newest,
        "projects_distinct": len(projects),
        "built_at": now_iso(),
    }
    if extra:
        manifest.update(extra)
    (root / source / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )


# ─── markdown rendering ────────────────────────────────────────────────

def _render_markdown(rec: SessionRecord) -> str:
    """Minimal front-matter + flat message dump. Source-faithful JSON stays in .jsonl."""
    lines: List[str] = []
    lines.append("---")
    lines.append(f'source: "{rec.source}"')
    lines.append(f'id: "{rec.session_id}"')
    lines.append(f'title: "{_md_escape(rec.title)}"')
    if rec.created_at: lines.append(f"created_at: {rec.created_at}")
    if rec.updated_at: lines.append(f"updated_at: {rec.updated_at}")
    if rec.project_hint: lines.append(f'project_hint: "{_md_escape(rec.project_hint)}"')
    lines.append(f"message_count: {rec.message_count}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {rec.title}")
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
