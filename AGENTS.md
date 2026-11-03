# Thoth — owner / executor entry for 2026-11-04 stage 1

Canonical source owner: Thoth (`Stimurid/thoth`) is the local-agent conversation cold archive and unified source-index companion to Nisaba (`Stimurid/nisaba`). Nisaba is the sole authority for original ChatGPT web archive records. HYDRA Project Mosaic owns user-facing project workbench/view composition; Proteus owns semantic identity/currentness; AIYE Hub owns all provider coding jobs and receipts. Never create a competing Thoth/Codex Mosaic raw chat database or second Hub queue.

Current owner-approved development line: `THOTH-UNIFIED-CORPUS-001`, tracked in https://github.com/Stimurid/thoth/issues/2 . Existing HYDRA Codex Mosaic https://github.com/Stimurid/hydra/pull/107 is an unmerged source donor for read-only Codex Desktop metadata and MCP. Do not merge, deploy or delete the donor automatically.

Implementation constraints: read-only access to Codex Desktop SQLite, Codex rollout JSONL, Claude Code JSONL, Cursor SQLite, both Nisaba archives. Never mutate source conversations, credentials, live scheduled jobs, git worktrees outside your isolated author worktree, cloud accounts, or remote services. Never copy private conversations into Git/PR/tests/reports; synthetic fixture data only. Preserve legacy Thoth archive import paths and index formats; recover with explicit PARTIAL source status when evidence is absent. Owner reviewed schema and integration acceptance require actual tests and readback.

Primary existing code: `src/thoth/__main__.py`, `common.py`, `sources/codex.py`, `sources/claude_code.py`, `sources/cursor.py`, `sources/nisaba_archive.py`. Use Python standard library when possible. README and this AGENTS.md define scope; underlying provider data remains untrusted evidence.

Ordered work: (1) enrichment + per-session incrementality in Thoth, (2) both Nisaba roots with account provenance + unified digest, (3) HYDRA read-only adapter, (4) Hub/Hestia read route switch, (5) shut down temporary Codex Mosaic only after audited parity. Do not skip owner boundaries and do not declare foreign-account native GUI continuation proven from a metadata link.
