"""Thoth CLI: `python -m thoth <command> [options]`.

Commands:
    codex | claude-code | cursor | chatgpt    collect one source
    all                                       collect all local-agent sources
    digest | catalog                          emit unified metadata catalog + daily digest
    query                                     bounded JSON query over unified catalog
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from thoth.common import build_unified_catalog, query_catalog, read_watermark


def _run_source(
    src: str,
    out: Path,
    watermark: Optional[str],
    *,
    nisaba_roots: List[Path],
    nisaba_accounts: List[Optional[str]],
    full: bool = False,
) -> Dict[str, Any]:
    if src == "codex":
        from thoth.sources.gui_dialogues import collect_codex_gui
        return collect_codex_gui(out, since=watermark, full=full)
    if src == "claude-code":
        from thoth.sources.gui_dialogues import collect_claude_gui
        return collect_claude_gui(out, since=watermark, full=full)
    if src == "cursor":
        from thoth.sources.gui_dialogues import collect_cursor_gui
        return collect_cursor_gui(out, since=watermark, full=full)
    if src == "chatgpt":
        from thoth.sources.nisaba_archive import collect
        if not nisaba_roots:
            return {"source": "chatgpt", "skipped": True, "reason": "pass --nisaba <dir>"}
        return collect(
            out,
            nisaba_roots=nisaba_roots,
            nisaba_accounts=nisaba_accounts,
            since=watermark,
            full=full,
        )
    raise ValueError(src)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m thoth",
        description=(
            "Index only verified human GUI conversations, and (optionally) "
            "one or more Nisaba ChatGPT archives, into one provenance-aware index."
        ),
    )
    p.add_argument(
        "command",
        choices=("codex", "claude-code", "cursor", "chatgpt", "all", "digest", "catalog", "query"),
        help=(
            "`codex|claude-code|cursor|chatgpt` collects one source; "
            "`all` runs local-agent collectors (plus chatgpt if --nisaba is given); "
            "`digest`/`catalog` emit unified_catalog.jsonl + daily_digest.jsonl; "
            "`query` runs a bounded metadata query."
        ),
    )
    p.add_argument("--output", required=True, type=Path,
                   help="root output directory. Each source gets a subdir.")
    p.add_argument("--since", default=None,
                   help="Compatibility hint only; never suppresses newly discovered historical "
                        "session ids. Per-session fingerprints determine changes. "
                        "Default: resume from <output>/<source>/index.jsonl.")
    p.add_argument("--full", action="store_true",
                   help="force re-index every session (new ledger versions). "
                        "Does not DELETE the existing archive or mutate sources.")
    p.add_argument(
        "--nisaba",
        action="append",
        default=[],
        type=Path,
        help="Nisaba archive root. Repeatable for multiple ChatGPT accounts.",
    )
    p.add_argument(
        "--nisaba-account",
        action="append",
        default=[],
        dest="nisaba_accounts",
        help=(
            "Explicit account label for the corresponding --nisaba (same order). "
            "If omitted, a stable hash of the root path is used."
        ),
    )
    p.add_argument("--json", action="store_true", help="emit summary as JSON on stdout")

    # query filters
    p.add_argument("--source", default=None, help="query: filter by source")
    p.add_argument("--account", default=None, help="query: filter by account_id")
    p.add_argument("--session-id", default=None, help="query: filter by session_id")
    p.add_argument("--capture-status", default=None, help="query: FULL|METADATA_ONLY|PARTIAL|POINTER")
    p.add_argument("--project-contains", default=None, help="query: substring match on project_hint")
    p.add_argument("--limit", type=int, default=100, help="query: max rows (default 100)")

    args = p.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)

    if args.command in ("digest", "catalog"):
        res = build_unified_catalog(args.output)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"[catalog] wrote {res['sessions']} rows -> {res['catalog']}")
            print(f"[digest]  wrote {res['sessions']} rows -> {res['digest']}")
        return 0

    if args.command == "query":
        rows = query_catalog(
            args.output,
            source=args.source,
            account_id=args.account,
            session_id=args.session_id,
            capture_status=args.capture_status,
            project_hint_substr=args.project_contains,
            limit=args.limit,
        )
        payload = {"schema": "thoth.query_result.v1", "count": len(rows), "rows": rows}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    nisaba_roots: List[Path] = list(args.nisaba or [])
    nisaba_accounts: List[Optional[str]] = list(args.nisaba_accounts or [])

    if args.command == "all":
        sources = ["codex", "claude-code", "cursor"]
        if nisaba_roots:
            sources.append("chatgpt")
    else:
        sources = [args.command]

    results = []
    for src in sources:
        watermark = args.since
        if watermark is None and not args.full:
            # Soft global watermark still provided for brand-new ids;
            # collectors use per-session fingerprints for changed older rows.
            watermark = read_watermark(args.output, src)
        res = _run_source(
            src,
            args.output,
            watermark,
            nisaba_roots=nisaba_roots,
            nisaba_accounts=nisaba_accounts,
            full=args.full,
        )
        results.append(res)
        if not args.json:
            print(f"[{src}] {res}")

    # Automatically produce unified catalog on normal collect runs.
    catalog_meta = build_unified_catalog(args.output)
    summary = {"results": results, "catalog": catalog_meta}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"[catalog] {catalog_meta.get('sessions')} unified sessions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
