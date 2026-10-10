"""Thoth CLI: `python -m thoth <command> [options]`.

Commands:
    codex | claude-code | cursor | chatgpt    collect one source
    all                                       collect all local-agent sources
    digest                                    emit daily_digest.jsonl across the archive
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from thoth.common import read_watermark


def _run_source(src: str, out: Path, watermark, nisaba_root: Path | None):
    if src == "codex":
        from thoth.sources.codex import collect
        return collect(out, since=watermark)
    if src == "claude-code":
        from thoth.sources.claude_code import collect
        return collect(out, since=watermark)
    if src == "cursor":
        from thoth.sources.cursor import collect
        return collect(out, since=watermark)
    if src == "chatgpt":
        from thoth.sources.nisaba_archive import collect
        if nisaba_root is None:
            return {"source": "chatgpt", "skipped": True, "reason": "pass --nisaba <dir>"}
        return collect(out, nisaba_root=nisaba_root, since=watermark)
    raise ValueError(src)


def _emit_digest(out: Path) -> dict:
    """Flatten all per-source index.jsonl into <out>/daily_digest.jsonl.

    One row per session; grouped-by-day lives in the day-sliced tree.
    Downstream (litops / archivarius) can slice this by `date` field.
    """
    sources = [p.name for p in out.iterdir() if p.is_dir() and (p / "index.jsonl").is_file()]
    digest_path = out / "daily_digest.jsonl"
    n = 0
    seen: set[tuple[str, str]] = set()
    with digest_path.open("w", encoding="utf-8") as fout:
        for src in sources:
            with (out / src / "index.jsonl").open("r", encoding="utf-8") as fin:
                for raw in fin:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        row = json.loads(raw)
                    except Exception:
                        continue
                    sid = row.get("session_id") or ""
                    if not sid:
                        continue
                    key = (src, sid)
                    if key in seen:
                        continue
                    seen.add(key)
                    ts = row.get("updated_at") or row.get("created_at") or ""
                    date = ts[:10] if ts else ""
                    digest_row = {
                        "date": date,
                        "source": row.get("source") or src,
                        "session_id": sid,
                        "title": row.get("title"),
                        "project_hint": row.get("project_hint"),
                        "updated_at": row.get("updated_at"),
                        "created_at": row.get("created_at"),
                        "message_count": row.get("message_count"),
                        "carrier_paths": row.get("carrier_paths") or [],
                        "extra": row.get("extra") or {},
                    }
                    fout.write(json.dumps(digest_row, ensure_ascii=False) + "\n")
                    n += 1
    return {"digest": str(digest_path), "sessions": n, "sources": sources}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m thoth",
        description="Cold-archive AI agent chats stored locally on disk, "
                    "and (optionally) a Nisaba ChatGPT archive, into one indexed tree.",
    )
    p.add_argument(
        "command",
        choices=("codex", "claude-code", "cursor", "chatgpt", "all", "digest"),
        help="`codex|claude-code|cursor|chatgpt` collects one source; "
             "`all` runs the three local-agent collectors (plus chatgpt if --nisaba is given); "
             "`digest` emits daily_digest.jsonl from existing per-source index files.",
    )
    p.add_argument("--output", required=True, type=Path,
                   help="root output directory. Each source gets a subdir.")
    p.add_argument("--since", default=None,
                   help="ISO UTC watermark; only sessions updated AFTER this are collected. "
                        "Default: resume from <output>/<source>/index.jsonl.")
    p.add_argument("--full", action="store_true",
                   help="ignore any existing watermark; collect everything. Does not DELETE "
                        "the existing archive.")
    p.add_argument("--nisaba", type=Path, default=None,
                   help="path to a Nisaba archive root. Required for `chatgpt`; "
                        "enables chatgpt in `all`.")
    p.add_argument("--json", action="store_true", help="emit summary as JSON on stdout")
    args = p.parse_args(argv)

    args.output.mkdir(parents=True, exist_ok=True)

    if args.command == "digest":
        res = _emit_digest(args.output)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"[digest] wrote {res['sessions']} rows -> {res['digest']}")
        return 0

    if args.command == "all":
        sources = ["codex", "claude-code", "cursor"]
        if args.nisaba:
            sources.append("chatgpt")
    else:
        sources = [args.command]

    results = []
    for src in sources:
        watermark = args.since
        if watermark is None and not args.full:
            watermark = read_watermark(args.output, src)
        res = _run_source(src, args.output, watermark, args.nisaba)
        results.append(res)
        if not args.json:
            print(f"[{src}] {res}")

    if args.json:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
