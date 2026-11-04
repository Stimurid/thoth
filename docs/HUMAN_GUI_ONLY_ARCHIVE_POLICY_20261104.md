# THOTH human GUI-only archive policy — AORUS 2026-11-04

## Non-negotiable source boundary

Default Thoth `all` indexes actual human GUI conversations and explicit cloud ChatGPT/Nisaba source records only. Codex CLI `exec`, SDK runs, spawned subagents, OpenCode, Hub receipts and standalone MiniMax telemetry are NOT dialogue sessions, and MUST NOT enter the user-facing catalog. CLI traces belong to a future independent retention decision, not the conversation archive. No command-line opt-in exists to re-enable them accidentally from this deployed CLI entrypoint.

- Codex Desktop: accept state_5.sqlite native rows only if originator is exactly `Codex Desktop` or `codex_work_desktop`, source is exactly `vscode`, thread_source is `user` or `chatgpt_handoff`. ChatGPT handoffs are marked not-new logical conversations. Source rollout absent => `METADATA_ONLY`, never fabricate text.
- Claude Code: accept only source JSONL records with `entrypoint=claude-desktop` and human user dialogue. `sdk-cli`, `cli`, mixed/unknown entrypoint carriers excluded. Native UUID duplicate across filesystem locations collapses into one dialogue; alternate paths kept as metadata. Keep visible text turns only, no tool calls/results, internal thinking or full raw event payloads.
- Cursor: require a non-subagent composer header and visible human user bubble. Keep visible text only, not bubble payloads and tool data. Cursor direct GUI usage is not assumed from provider model usage.
- ChatGPT: two separate Nisaba account roots; reference the existing archive, not duplicate raw bodies. Preserve account identity.
- Kimi: distinct stacked branch for 2 native conversations and 99 imported Claude/Codex carriers; never promote 99 imported carriers to additional logical user chats. Include only after source/UI acceptance.

## AORUS real source acceptance

Codex state_5.sqlite 1,360 thread rows: 39 native verified GUI roots (3 ChatGPT handoffs), 1,321 excluded. Claude Code 456 files: 173 GUI source carriers, 5 duplicate source UUIDs -> 168 distinct GUI sessions; 283 CLI/SDK excluded. Cursor 43 composers, 9 qualifying visible GUI sessions and 34 excluded. Nisaba 1,667 records from two archives. Isolated candidate total 1,883 source-carrier records; unchanged second run returned zero new versions for every source.

Source copies are not complete rich conversation archives; extracted visible-text-only output is marked `PARTIAL` and original provider carriers remain source of truth. Existing old corpus contains 8,588,803,983 bytes of historical Codex/Claude/Cursor data. 1,184 Codex source paths and 8 Claude paths no longer exist; DO NOT claim the old data is fully reproducible and DO NOT blindly delete it. Compressed and verified quarantine must precede removal of the old expanded copies.

## Rollback gates

1. Seal and test a locally retained 7z archive of the entire original Thoth archive, record SHA256, files/count and original root; never delete provider application stores.
2. Copy accepted clean candidate to original active Thoth archive path and deploy stable GUI-only runtime. Re-run one unchanged full-source increment with both Nisaba accounts and validate zero new copies.
3. Switch scheduled ThothDaily script to GUI-only runtime and pass user-facing source counts. Confirm no CLI session can be collected through entrypoint.
4. Only then delete the *expanded original archive*, not the verified compressed quarantine. Preserve rollback manifest and output under local archive-ops.

## Limits

Local AORUS acceptance does not prove Lenovo, Mac or cross-account cloud continuation. Unknown provenance stays excluded. The GUI-only corpus cannot be treated as proof that source providers retain every old message. A later merge/publish is still subject to independent code review.