# Acceptance report — THOTH-UNIFIED-CORPUS-001 (executor run)

Date (UTC observed): 2026-11-03. Worktree: `jx-fc85811910b7`. Branch: `aiye/exec-fc85811910b7`.  
Ecology: THOTH `LOCAL_OBSERVED_ONLY`; runtime UNKNOWN. No push/merge; no live task/archive mutation; no cloud endpoints.

## Delivered

| Requirement | Evidence |
|---|---|
| Codex UUID normalize (legacy stem → UUID) | `extract_uuid` / `normalize_codex_session_id`; test `TestUuidNormalize`, `TestCodexEnrichment` |
| Desktop SQLite enrichment (title, project, cwd, account label, open_hint) | `sources/codex.py` `load_desktop_thread_index` + `enrich_from_desktop`; RO URI + `query_only` + version column fallback |
| `METADATA_ONLY` for SQLite thread without rollout | test asserts `capture_status=METADATA_ONLY`, `message_count=0` |
| Multi `--nisaba` + account labels; no UUID collision | `nisaba_archive.collect`; test duplicate UUID across `primary`/`account2` |
| Per-session incrementality + fingerprint; idempotent second run | `should_emit` / ledger versions; tests unchanged / older-changed / same-ts-diff-size |
| PARTIAL missing/corrupt + recovery | tests `TestPartialRecovery` |
| Unified catalog + daily digest auto on collect; query CLI | `build_unified_catalog`, `query`; CLI `catalog`/`digest`/`query` |
| HYDRA contract + README | `docs/HYDRA_INTEGRATION.md`, README 0.2.0 |
| Synthetic fixtures only | `tests/test_unified_corpus.py` |
| No live cloud / no Git snapshot #1 / no scheduled-task edit | not implemented / not touched |

## Tests executed

```text
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
→ Ran 10 tests in ~0.17s — OK
```

## Not verified in this run

- Live readback against real `~/Downloads/ChatGPT_Archive_*` (private; synthetic only).
- Full live Codex archive re-index timing/parity vs AORUS audit counts (1305/1336).
- HYDRA Mosaic parity shut-down gate (owner stage 5).
- Proteus claim write (executor scope is Thoth worktree code only; no world mutation attempted).

## Schema versions shipped

- `thoth.index_row.v2`
- `thoth.unified_catalog.v1`
- `thoth.daily_digest.v1`
- `thoth.query_result.v1`
- Package version `0.2.0`
