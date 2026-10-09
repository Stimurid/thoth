"""Thoth CLI: `python -m thoth <source> --output <dir> [options]`.

Available sources: codex, claude-code, cursor, all.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from thoth.common import read_watermark


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m thoth",
        description="Cold-archive AI agent chats stored locally on disk.",
    )
    p.add_argument(
        "source",
        choices=("codex", "claude-code", "cursor", "all"),
        help="which local agent to collect from",
    )
    p.add_argument("--output", required=True, type=Path,
                   help="root output directory. Each source gets a subdir.")
    p.add_argument("--since", default=None,
                   help="ISO UTC watermark; only sessions updated AFTER this are collected. "
                        "Default: resume from <output>/<source>/index.jsonl.")
    p.add_argument("--full", action="store_true",
                   help="ignore any existing watermark; collect everything. Does not DELETE "
                        "the existing archive — new sessions are appended to the index.")
    p.add_argument("--json", action="store_true", help="emit summary as JSON on stdout")
    args = p.parse_args(argv)

    args.output.mkdir(parents=True, exist_ok=True)
    sources = ("codex", "claude-code", "cursor") if args.source == "all" else (args.source,)

    results = []
    for src in sources:
        watermark = args.since
        if watermark is None and not args.full:
            watermark = read_watermark(args.output, src)
        if src == "codex":
            from thoth.sources.codex import collect
            res = collect(args.output, since=watermark)
        elif src == "claude-code":
            from thoth.sources.claude_code import collect
            res = collect(args.output, since=watermark)
        elif src == "cursor":
            from thoth.sources.cursor import collect
            res = collect(args.output, since=watermark)
        else:
            raise ValueError(src)
        results.append(res)
        if not args.json:
            print(f"[{src}] {res}")

    if args.json:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
