# Thoth source census: AORUS, 2026-11-04

Status: read-only source/provenance evidence for collector design, not a claim of complete cloud/account history. Metadata only; do not publish raw conversations, credentials, user titles or personal IDs.

## Three counts, three semantic layers

Native session = one source application session. Execution attempt = one Hub job or spawned agent operation, possibly with many native sessions. Working line = a connected chain of sessions with proof of import, spawn or continuation. These counts must never be added as if equivalent.

## Actual measured sources

| Store | Measured records | Qualifier |
| --- | ---: | --- |
| Nisaba primary + second | 1,393 + 266 | independent ChatGPT archive roots, 63 current partial raw bodies |
| Codex state_5.sqlite | 1,344 | originator codex_exec 1,185; Codex Desktop 45; codex_work_desktop 6; other/unknown 108. Only 160 local rollout files present. Exact UUID overlap with Nisaba = zero; 3 thread_source chatgpt_handoff. **Not 1,344 human Desktop chats.** |
| Claude Code in Thoth | 453 | native indexed sessions, not necessarily all human-root |
| Cursor in Thoth | 11 | native indexed sessions |
| Kimi Code App | 101 | **87 imported_from claude-code, 12 imported_from codex, 2 Kimi-native**. All 99 imported source IDs exactly join current Thoth source session IDs. |
| OpenCode SQLite | 1,510 | 1,469 roots + 41 children; 840 under Hub job worktrees and 527 other Hub paths. Providers: MiniMax 841, OpenRouter 152, Anthropic 308, Tyumgu 106, fake/test 96, others. Provider != source archive. |
| MiniMax native runtime | 5 | local runtime SQLite session rows, distinct from model API usage within OpenCode |
| AIYE Hub receipts | 4,494 | executor actions, including opencode 785 and openrouter 200; these are not independent human chats |

## Exact Kimi continuity evidence

99 imported Kimi state.json records expose custom.imported_from, custom.imported_source_id, custom.imported_source_path and custom.imported_at. 87 Claude Code + 12 Codex exact native source IDs are already in Thoth. Kimi keeps the source ID but its own carrier/wire; link source and target with a proven IMPORTED_FROM edge. Two Claude-imported sessions show later Kimi model-request evidence, but mere imported prompts do not prove post-import model continuation. Keep any continued-with relationship separately verified by event timestamps.

## Proposed source contract

Record source_owner, client_transport, execution_kind (human_root, hub_job, spawned, imported, unknown), model_provider, account_id, native_session_id, native_project_id, capture_status and carriers. Lineage edges: IMPORTED_FROM, CONTINUED_WITH, SPAWNED_FROM, EXECUTED_BY, each supported by source-specific proof hashes/timestamps. Never merge by matching folder/title alone. User-facing counts must distinguish roots, execution traces, archive pointers, and unresolved metadata-only rows.

Next collectors: Kimi read-only session index + state/wire metadata and exact 99 edges; OpenCode read-only session SQLite with model provider facet and parent/HUB route; MiniMax 5 native records; Hub 4494 receipts as EXTERNAL_EXECUTION provenance (not sessions). Do not open huge OpenCode message/part bodies unnecessarily. Semantic project equivalence belongs to reviewed HYDRA Project Mosaic, not Thoth; Nisaba remains owner of ChatGPT raw bodies.

Acceptance: 87+12 Kimi import edges and 2 native, no fuzzy merges, idempotent updates, redacted-only source index, independent per-provider count QA, existing Hub/MCP/Hestia end-to-end readback, original ThothDaily intact until cutover. This document is discovery, not a declaration that extra collectors have already shipped.
