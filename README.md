# Thoth

Named after the Egyptian god of writing and scribes — pair for
[Nisaba](https://github.com/Stimurid/nisaba) in function. Where Nisaba
walks the ChatGPT web account, Thoth walks the files that **local AI
agents** leave on your disk.

A unified cold archive of locally-stored agent chat history, with
incremental daily collection, per-day slicing, per-project tagging, and
a uniform output layout shared with Nisaba so a downstream pipeline can
treat all your AI history as one addressable corpus.

## Supported sources (0.1.0)

| source | reads from | shape | tested at |
|---|---|---|---|
| `codex` | `~/.codex/sessions/<YYYY>/<MM>/<DD>/*.jsonl` + `~/.codex/archived_sessions` + `~/.codex/session_index.jsonl` | one JSONL event stream per session | Codex CLI 0.157.1 |
| `claude-code` | `~/.claude/projects/<project-slug>/<session-uuid>.jsonl` | one JSONL event stream per session | Claude Code 2.1.283 |
| `cursor` | `%APPDATA%/Cursor/User/globalStorage/state.vscdb` (SQLite): `composerHeaders` + `cursorDiskKV` bubble rows | one row per composer/chat session, bubbles joined | Cursor 1.x (as of 2026-10) |
| `chatgpt` | a [Nisaba](https://github.com/Stimurid/nisaba) archive root (pass `--nisaba <dir>`) | indexes existing Nisaba conversations without re-fetching | Nisaba 0.1.0 |

Not yet supported (roadmap, by request):
- Kilo (Cline successor) inside Cursor — their globalStorage format
- OpenCode — not installed on test machine
- Cline legacy state (retired on test machine; schema still readable)
- MCP servers' persisted conversation state

## Install

```powershell
# No dependencies beyond stdlib.
# Clone or pip-install directly:
git clone https://github.com/Stimurid/thoth
cd thoth
```

Python 3.11+. No `curl_cffi` or other deps — this reads only local
files.

## Use

```powershell
# One source:
$env:PYTHONPATH = "src"
python -m thoth codex --output D:\archives\agents

# All three local-agent sources at once:
python -m thoth all --output D:\archives\agents

# Include a Nisaba ChatGPT archive in the index:
python -m thoth all --output D:\archives\agents --nisaba D:\archives\chatgpt\primary

# Emit a flat daily digest across all sources (feeds downstream pipelines):
python -m thoth digest --output D:\archives\agents
# -> D:\archives\agents\daily_digest.jsonl, one row per session with
#    { date, source, session_id, title, project_hint, updated_at,
#      message_count, carrier_paths, extra }

# JSON summary (for CI / scripts):
python -m thoth all --output D:\archives\agents --json
```

**Incremental resume is default.** Thoth reads `index.jsonl` of each
source and only emits sessions with `updated_at` **after** the newest
previously recorded. Pass `--full` to ignore the watermark, or `--since
2026-01-01T00:00:00Z` to force an explicit cutoff.

## Output layout

```
<output>/
├── codex/
│   ├── sessions/<YYYY>/<MM>/<DD>/<session_id>.jsonl   # verbatim events
│   ├── sessions/<YYYY>/<MM>/<DD>/<session_id>.md      # readable render
│   ├── index.jsonl                                    # append-only per session
│   └── manifest.json                                  # summary / watermark
├── claude-code/   (same layout)
└── cursor/        (same layout)
```

The per-day slicing (`sessions/<YYYY>/<MM>/<DD>/`) is the pivot point
that lets you feed everything into a dated pipeline: a "what did I
discuss yesterday" query is just `ls <output>/*/sessions/<yesterday>/`.

Each session's `.md` has YAML frontmatter with `source`, `id`, `title`,
`created_at`, `updated_at`, `project_hint` — these drop straight into a
downstream project-tagging stage.

## Daily routine

See `scripts/install-daily-task.ps1` to register a Windows Scheduled
Task that calls `python -m thoth all --output <...>` once a day.

## Caveats

- **Cursor DB may be locked** while Cursor is running. Thoth opens with
  `mode=ro&immutable=1` which gives a stable snapshot even against a
  live writer, but very fresh writes (seconds-old) may be invisible
  until Cursor flushes.
- **"Session" is a mild lie for Codex/Claude Code.** Both tools call
  these files "rollouts" or "project sessions"; the semantic unit is a
  conversation turn, not a Session with capital S. Thoth normalizes.
- **Cursor `type` field decoding is empirical** — I decoded `1=user,
  2=assistant`; other values show up as `type_N`. The raw payload is
  preserved verbatim in the JSONL so if the encoding changes, no data
  loss, only cosmetic.
- **Workspace mapping is best-effort.** Cursor stores `workspaceId`
  pointing into `workspaceStorage/<hash>/workspace.json` — if the
  workspace was removed, the hint falls back to the raw id.
- **No deletion.** Thoth never deletes anything from source stores. If
  you want to GC an archive output directory, do it yourself.

## License

MIT. See `LICENSE`.

## Related

- [Nisaba](https://github.com/Stimurid/nisaba) — ChatGPT web account cold archive.
  Companion tool with the same output discipline.
