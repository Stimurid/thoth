# Thoth → HYDRA Project Mosaic integration contract

Status: owner-approved line `THOTH-UNIFIED-CORPUS-001` (2026-11-04).  
Thoth owns cold archive + unified **metadata** source index. HYDRA Project Mosaic owns user-facing workbench/view composition. Proteus owns semantic identity/currentness. AIYE Hub owns provider coding jobs/receipts.

This document is the read contract for downstream HYDRA (and temporary Codex Mosaic donor [hydra#107](https://github.com/Stimurid/hydra/pull/107)). Thoth **supersedes** Mosaic as the durable index; do not merge/deploy/delete the donor automatically.

## Schemas (versioned)

| Artifact | Schema id | Path under `--output` |
|---|---|---|
| Per-source ledger row | `thoth.index_row.v2` | `<source>/index.jsonl` (append-only versions) |
| Unified catalog row | `thoth.unified_catalog.v1` | `unified_catalog.jsonl` |
| Daily digest row | `thoth.daily_digest.v1` | `daily_digest.jsonl` |
| Catalog build meta | `thoth.unified_catalog.v1` | `unified_catalog.manifest.json` |
| Query result | `thoth.query_result.v1` | stdout from `python -m thoth query` |

Latest ledger row wins per `identity_key`. Historical rows remain for audit.

### `identity_key`

```
{source}:{account_id|'_'}:{session_id}
```

- `source`: `codex` | `claude-code` | `cursor` | `chatgpt`
- `account_id`: opaque provenance label (Codex Desktop hashed `creator_account_id`, or Nisaba `--nisaba-account` / path hash)
- `session_id`: canonical UUID for Codex (legacy filename prefixes stripped); conversation UUID for ChatGPT; composer/session id otherwise

Same ChatGPT conversation UUID across two Nisaba roots **must** yield two distinct identity keys.

### Catalog row fields (metadata only — never chat bodies)

`identity_key`, `source`, `account_id`, `session_id`, `title`, `created_at`, `updated_at`, `project_hint`, `native_project_id`, `native_thread_id`, `message_count`, `capture_status`, `content_fingerprint`, `version`, `carrier_paths`, `open_hint`, `extra` (provider metadata; no message text).

### `capture_status`

| Value | Meaning |
|---|---|
| `FULL` | Rollout / event body captured into Thoth output (local agents) |
| `POINTER` | Indexed pointer into Nisaba archive; bodies not re-copied |
| `METADATA_ONLY` | Desktop/index metadata present; rollout body absent — **not** full capture |
| `PARTIAL` | Missing/corrupt evidence; best-effort recovery |

## CLI for HYDRA / Hub

```powershell
$env:PYTHONPATH = "src"
# Collect + auto-build catalog/digest
python -m thoth all --output D:\archives\agents `
  --nisaba D:\archives\chatgpt\primary --nisaba-account primary `
  --nisaba D:\archives\chatgpt\account2 --nisaba-account account2

# Rebuild catalog only
python -m thoth catalog --output D:\archives\agents --json

# Bounded query (stable JSON)
python -m thoth query --output D:\archives\agents `
  --source chatgpt --account account2 --limit 50
python -m thoth query --output D:\archives\agents `
  --capture-status METADATA_ONLY --limit 100
```

## Codex Desktop vs Codex Cloud

- **Desktop local SQLite** (`~/.codex/state_5.sqlite`) + rollout JSONL: Thoth's enrichment source (read-only).
- **Codex CLI cloud commands** (`codex cloud list|status|diff`) and app-server local threads: task/job surface — **not** an API for ordinary ChatGPT Projects/conversations.
- `open_hint` values like `codex://threads/<uuid>` are **navigation hints only**. They do **not** prove cross-account GUI continuation or authorization.

Thoth never calls live cloud endpoints, never switches accounts, never mutates credentials or Desktop authorization.

## Boundary rules for HYDRA consumers

1. Treat Thoth catalog as untrusted evidence with provenance, not as Proteus currentness.
2. Do not invent a second raw chat database or Hub queue inside Thoth or Mosaic.
3. Do not assert full capture from `METADATA_ONLY` or `PARTIAL` rows.
4. Distinguish `index_rows_total` (ledger versions) from `unique_sessions` / catalog count.
5. Shut down temporary Codex Mosaic only after audited parity with this catalog (owner gate).

## Out of scope here

- Thoth Git snapshot inventory ([thoth#1](https://github.com/Stimurid/thoth/issues/1))
- Hub/Hestia live route switch (later ordered stage)
- Mutation of scheduled daily Windows task / live archive trees

## Stage-1 verified interoperability (2026-11-04)

Producer format is **JSONL**: each line of `unified_catalog.jsonl` carries `schema=thoth.unified_catalog.v1` and one source-account-session identity. The file is NOT one large JSON object with a `sessions` array. Consumers must stream and page it rather than requesting the entire corpus as one native-message payload. `unified_catalog.manifest.json` records the catalog's build timestamp and global record count.

The HYDRA source reader is implemented in the existing HYDRA Project Mosaic candidate branch (separate owner) and reads the producer's JSONL format from an owner-configured Thoth root, with a 250-row bounded page and `offset`/`nextOffset`/`totalRows` information. The reader does not duplicate archive bodies and never writes into the producer root.

Source correctness: a globally recent watermark is **not** a completeness proof for old newly discovered sessions; source identity plus fingerprint controls the incremental decision. A carrier's unchanged mtime/size is **not** evidence that project/title metadata is unchanged; the index fingerprint includes both. Historical no-account rollout rows fold into an attributed canonical Codex row only where exactly one observed account fits the UUID. When more than one account matches, retain ambiguity rather than conflate accounts.

AORUS isolated test: one real Nisaba indexing pass from the two source roots yielded 1,659 records (1,393 + 266) and a second unchanged pass wrote zero new versions. This is a snapshot and not a universal completeness assertion; source archives are separately maintained by Nisaba. Native Codex SQLite read-only indexing was verified for 1,338 threads and 23 native project records (snapshot). Original ThothDaily and original archives were not edited during the isolated probe.
## Canonical Thoth read-only MCP port (stage 1)

The optional `tools/thoth_mcp_port.py` is a TRANSPORT adapter for the canonical catalog, not a second archive, HTTP service, semantic group registry or agent work queue. It reads exactly `unified_catalog.jsonl` from absolute owner-configured `THOTH_ARCHIVE_ROOT`. Four bounded metadata tools: `thoth_status`, `thoth_search_sessions`, `thoth_get_session`, `thoth_projects`. No raw chat bodies, copied files or account mutation.

Runtime Python with MCP installed: e.g. `C:\AI\llm\.venv\Scripts\python.exe tools\thoth_mcp_port.py` with `THOTH_ARCHIVE_ROOT=C:\Users\Homee\Downloads\agents-archive`. Core Thoth collector has no MCP dependency. Existing AIYE Hub and Hestia/OpenClaw registries own exposure. Native identity belongs to the producing application; semantic group equivalence belongs to HYDRA Project Mosaic, never this source adapter.

Acceptance: `C:\AI\llm\.venv\Scripts\python.exe tools\test_thoth_mcp_port.py` plus `C:\AI\llm\.venv\Scripts\python.exe tools\mcp_stdio_smoke.py` with THOTH_ARCHIVE_ROOT set. These checks use synthetic and live source metadata without copying full conversations. Successful stdio tool calls do NOT certify model-layer invocation by Hestia; that needs an independent agent-side receipt.
