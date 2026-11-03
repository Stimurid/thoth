# Thoth

Named after the Egyptian god of writing and scribes — pair for
[Nisaba](https://github.com/Stimurid/nisaba) in function. Where Nisaba
walks the ChatGPT web account, Thoth walks the files that **local AI
agents** leave on your disk, and indexes **both** Nisaba ChatGPT archival
roots into one provenance-aware, metadata-only unified catalog.

Owner line: `THOTH-UNIFIED-CORPUS-001` ([issue #2](https://github.com/Stimurid/thoth/issues/2)).
Downstream HYDRA contract: [`docs/HYDRA_INTEGRATION.md`](docs/HYDRA_INTEGRATION.md).

## Supported sources (0.2.0)

| source | reads from | shape | notes |
|---|---|---|---|
| `codex` | `~/.codex/sessions` + `archived_sessions` + **Desktop** `state_5.sqlite` (read-only) | rollout JSONL + thread/project enrichment | UUID-normalized; `METADATA_ONLY` for SQLite threads without rollout |
| `claude-code` | `~/.claude/projects/<project-slug>/<session-uuid>.jsonl` | one JSONL event stream per session | per-session fingerprints |
| `cursor` | `%APPDATA%/Cursor/User/globalStorage/state.vscdb` | composer headers + bubbles | RO SQLite with WAL fallback |
| `kimi-code` | `~/.kimi-code/session_index.jsonl` + per-session `state.json` / `wire.jsonl` | metadata pointers and exact IMPORTED_FROM lineage | 99 verified imported Claude/Codex sessions on AORUS; no copied chat bodies |
| `chatgpt` | one or more [Nisaba](https://github.com/Stimurid/nisaba) archive roots (`--nisaba`, repeatable) | pointer index into Nisaba; no body re-copy | account labels prevent UUID collisions across accounts |

Not yet supported (roadmap): OpenCode session SQLite, standalone MiniMax runtime, Kilo, Cline legacy and MCP server-owned conversation stores. Hub receipts are execution evidence, not independent human chat sessions.

## Install

```powershell
# No dependencies beyond stdlib. Python 3.11+.
git clone https://github.com/Stimurid/thoth
cd thoth
```

## Use

```powershell
$env:PYTHONPATH = "src"

# One source
python -m thoth codex --output D:\archives\agents
python -m thoth kimi-code --output D:\archives\agents

# All local agents + both Nisaba ChatGPT archives
python -m thoth all --output D:\archives\agents `
  --nisaba D:\archives\chatgpt\primary --nisaba-account primary `
  --nisaba D:\archives\chatgpt\account2 --nisaba-account account2

# Unified catalog + daily digest (also auto-built after collect runs)
python -m thoth catalog --output D:\archives\agents --json

# Bounded metadata query for HYDRA / Hub
python -m thoth query --output D:\archives\agents --source chatgpt --account account2 --limit 50
```

**Per-session incrementality is default.** Thoth keeps an append-only version
ledger per `identity_key` and skips unchanged content fingerprints. An older
session that grows after a newer global watermark is still re-indexed.
`--full` forces new ledger versions; it does not delete the archive or mutate
provider stores. `--since` is a compatibility hint, NOT an exclusion for newly discovered old sessions.

## Output layout

```
<output>/
├── codex/
│   ├── sessions/<YYYY>/<MM>/<DD>/<session_id>.jsonl   # verbatim events (FULL)
│   ├── sessions/<YYYY>/<MM>/<DD>/<session_id>.md
│   ├── index.jsonl                                    # append-only version ledger
│   └── manifest.json                                  # unique vs row counts
├── claude-code/   (same layout)
├── cursor/        (same layout)
├── kimi-code/     (metadata stubs + pointer index; verified imported-source edges)
├── chatgpt/       (index + manifest only; pointers into Nisaba)
├── unified_catalog.jsonl          # latest metadata row per identity_key
├── unified_catalog.manifest.json
└── daily_digest.jsonl             # same rows + date field
```

`capture_status`: `FULL` | `POINTER` | `METADATA_ONLY` | `PARTIAL`.

## Codex Desktop vs Codex Cloud

Thoth enriches from **local** Desktop SQLite + rollouts only. This includes
Codex CLI/exec jobs, VSCode runs, review/guardian subagents and actual Desktop
threads: a database row is NOT necessarily a human Desktop chat. The catalog
has session_kind, client_origin and native_thread_source to distinguish these.
Codex CLI
`cloud list|status|diff` is a cloud-task surface, **not** ChatGPT
Projects/conversations API. `codex://threads/<uuid>` open hints are
navigation hints — not proof of cross-account continuation. Thoth does not
call live cloud endpoints or switch accounts.

## Tests

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Synthetic fixtures only — no private conversation content in the repo.

## Daily routine

See `scripts/install-daily-task.ps1`. This upgrade does **not** alter the
live scheduled task; owners update that separately after acceptance.

## Caveats

- **Never mutates** provider stores, credentials, or authorization.
- **Cursor DB may be locked**; opened with RO URI + WAL-safe fallbacks.
- **No deletion** of source or archive history by Thoth.
- Temporary HYDRA Codex Mosaic ([hydra#107](https://github.com/Stimurid/hydra/pull/107)) is a read-only donor until audited parity — do not auto-merge/delete.

## License

MIT. See `LICENSE`.

## Related

- [Nisaba](https://github.com/Stimurid/nisaba) — ChatGPT web account cold archive
- [HYDRA integration contract](docs/HYDRA_INTEGRATION.md)
