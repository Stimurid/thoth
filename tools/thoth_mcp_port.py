"""Thoth's read-only MCP adapter: one existing source catalog, no second archive."""
from __future__ import annotations
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("thoth-unified-corpus")
RO = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                     idempotent_hint=True, open_world_hint=False)
SCHEMA = "thoth.mcp.source_catalog.v1"
CATALOG = "thoth.unified_catalog.v1"


def _rows():
    value = os.environ.get("THOTH_ARCHIVE_ROOT")
    if not value:
        raise RuntimeError("THOTH_ARCHIVE_ROOT_UNCONFIGURED")
    root = Path(value)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise RuntimeError("THOTH_ROOT_INVALID")
    catalog = root / "unified_catalog.jsonl"
    if not catalog.is_file() or catalog.is_symlink() or catalog.stat().st_size > 64_000_000:
        raise RuntimeError("THOTH_CATALOG_UNAVAILABLE")
    with catalog.open(encoding="utf-8") as f:
        for raw in f:
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if isinstance(row, dict) and row.get("schema") == CATALOG:
                yield row


def _text(v, n=160):
    if not isinstance(v, str) or len(v) > n:
        raise ValueError("THOTH_INPUT_INVALID")
    return v.casefold()


def _limit(v):
    try:
        return min(100, max(1, int(v)))
    except (TypeError, ValueError):
        raise ValueError("THOTH_LIMIT_INVALID") from None


def _card(row):
    def val(k, limit=280):
        v = row.get(k)
        return str(v)[:limit] if v is not None else None
    return {
        "source": val("source", 60),
        "account_id": val("account_id", 160),
        "session_id": val("session_id", 200),
        "native_project_id": val("native_project_id", 200),
        "native_thread_id": val("native_thread_id", 200),
        "title": val("title", 200),
        "project_hint": val("project_hint", 280),
        "updated_at": val("updated_at", 48),
        "capture_status": val("capture_status", 30),
        "open_hint": val("open_hint", 320),
        "carrier_paths": [str(x)[:400] for x in (row.get("carrier_paths") or [])[:5]
                          if isinstance(x, str)],
        "raw_body_included": False,
        "cross_provider_resume_proven": False,
    }


def _receipt():
    return {"schema": SCHEMA, "owner": "Thoth",
            "observed_utc": datetime.now(timezone.utc).isoformat(),
            "source": "THOTH_UNIFIED_CATALOG_JSONL",
            "read_only": True, "provider_live": False,
            "semantic_group_authority": "HYDRA_PROJECT_MOSAIC"}


def status():
    all_rows = list(_rows())
    counts = Counter(x.get("source") or "UNKNOWN" for x in all_rows)
    capture = Counter(x.get("capture_status") or "UNKNOWN" for x in all_rows)
    accounts = Counter((str(x.get("source") or ""), str(x.get("account_id") or "UNKNOWN"))
                       for x in all_rows)
    return {"ok": True, **_receipt(), "total_records": len(all_rows),
            "sources": dict(counts), "capture_status_counts": dict(capture),
            "account_instances": [{"source": a, "account_id": b, "sessions": n}
                                  for (a, b), n in sorted(accounts.items())]}


def search_sessions(query="", source="", account="", limit=30):
    q, s, a, lim = _text(query), _text(source, 80), _text(account, 160), _limit(limit)
    matches = []
    for row in _rows():
        if s and str(row.get("source") or "").casefold() != s:
            continue
        if a and str(row.get("account_id") or "").casefold() != a:
            continue
        hay = " ".join(str(row.get(k) or "") for k in
                       ("title", "project_hint", "session_id", "native_project_id")).casefold()
        if q and q not in hay:
            continue
        matches.append(_card(row))
    matches.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return {"ok": True, **_receipt(), "matching": len(matches), "count": min(lim, len(matches)),
            "sessions": matches[:lim]}


def get_session(source, account, session_id):
    s, a, sid = _text(source, 80), _text(account, 160), _text(session_id, 200)
    if not s or not a or not sid:
        raise ValueError("THOTH_EXACT_IDENTITY_REQUIRED")
    matches = [_card(row) for row in _rows()
               if str(row.get("source") or "").casefold() == s
               and str(row.get("account_id") or "UNKNOWN").casefold() == a
               and str(row.get("session_id") or "").casefold() == sid]
    return {"ok": len(matches) == 1, **_receipt(),
            "session": matches[0] if len(matches) == 1 else None,
            "error": None if len(matches) == 1 else "THOTH_IDENTITY_MISSING_OR_AMBIGUOUS"}


def projects(query="", limit=50):
    q, lim = _text(query), _limit(limit)
    grouped = {}
    for row in _rows():
        native = str(row.get("native_project_id") or "")
        if not native:
            continue
        key = (str(row.get("source") or ""), str(row.get("account_id") or "UNKNOWN"), native)
        v = grouped.setdefault(key, {"source": key[0], "account_id": key[1],
                                    "native_project_id": native, "sessions": 0,
                                    "semantic_group": None, "owner_reviewed_merge": False})
        v["sessions"] += 1
    vals = sorted(grouped.values(), key=lambda x: (-x["sessions"], x["native_project_id"]))
    if q:
        vals = [x for x in vals if q in x["native_project_id"].casefold()]
    return {"ok": True, **_receipt(), "matching": len(vals), "projects": vals[:lim],
            "native_only": True}


@mcp.tool(annotations=RO)
def thoth_status() -> dict:
    """Count current indexed source-account sessions and partial coverage."""
    return status()


@mcp.tool(annotations=RO)
def thoth_search_sessions(query: str = "", source: str = "",
                          account: str = "", limit: int = 30) -> dict:
    """Search metadata of Thoth's one catalog; do not return raw chat bodies."""
    return search_sessions(query, source, account, limit)


@mcp.tool(annotations=RO)
def thoth_get_session(source: str, account: str, session_id: str) -> dict:
    """Look up one exact source+account+session, read-only."""
    return get_session(source, account, session_id)


@mcp.tool(annotations=RO)
def thoth_projects(query: str = "", limit: int = 50) -> dict:
    """List native project IDs without guessing semantic equivalence."""
    return projects(query, limit)


if __name__ == "__main__":
    mcp.run()
