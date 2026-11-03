"""Thoth — cold archive of locally-stored AI agent chats.

Named after the Egyptian god of writing, counting and scribes — pair for
Nisaba in function. Where Nisaba walks the ChatGPT web account, Thoth
walks the files that local AI agents leave on your disk, and indexes both
Nisaba ChatGPT archival roots into one provenance-aware catalog.

Supported sources:
    - codex       : Codex CLI rollouts + Desktop state_5.sqlite enrichment
    - claude-code : Claude Code (~/.claude/projects/<slug>/<session>.jsonl)
    - cursor      : Cursor AI chats (globalStorage/state.vscdb SQLite)
    - chatgpt     : one or more Nisaba archive roots (pointer index)

CLI:
    python -m thoth <source|all|catalog|query> --output <dir> [options]
"""

__version__ = "0.2.0"
