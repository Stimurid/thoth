"""Thoth — cold archive of locally-stored AI agent chats.

Named after the Egyptian god of writing, counting and scribes — pair for
Nisaba in function. Where Nisaba walks the ChatGPT web account, Thoth
walks the files that local AI agents leave on your disk.

Supported sources:
    - codex       : Codex CLI (~/.codex/sessions, session_index.jsonl, archived_sessions)
    - claude-code : Claude Code (~/.claude/projects/<slug>/<session>.jsonl)
    - cursor      : Cursor AI chats (globalStorage/state.vscdb SQLite)

CLI:
    python -m thoth <source> --output <dir> [--incremental] [--since <iso>]

Each source writes a per-day sliced archive:
    <output>/<source>/sessions/<YYYY>/<MM>/<DD>/<session_id>.jsonl
    <output>/<source>/sessions/<YYYY>/<MM>/<DD>/<session_id>.md
    <output>/<source>/index.jsonl       # one row per session with provenance
    <output>/<source>/manifest.json
"""

__version__ = "0.1.0"
