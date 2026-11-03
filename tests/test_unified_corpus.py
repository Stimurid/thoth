"""Acceptance tests for THOTH-UNIFIED-CORPUS-001 (synthetic fixtures only)."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from thoth.common import (
    CAPTURE_METADATA_ONLY,
    CAPTURE_PARTIAL,
    CAPTURE_POINTER,
    build_unified_catalog,
    extract_uuid,
    load_session_ledger,
    make_identity_key,
    query_catalog,
)
from thoth.sources.codex import collect as collect_codex
from thoth.sources.codex import normalize_codex_session_id
from thoth.sources.nisaba_archive import collect as collect_nisaba


SHARED_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
LEGACY_STEM = "rollout-2026-01-02T03-04-05-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
META_ONLY_UUID = "ffffffff-1111-2222-3333-444444444444"
OLD_UUID = "11111111-2222-3333-4444-555555555555"


def _write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_nisaba_root(root: Path, conversation_id: str, *, title: str, updated: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "account_manifest.json").write_text(
        json.dumps({"account_label": root.name, "exported_at": "2026-09-30T00:00:00Z"}),
        encoding="utf-8",
    )
    listing = {
        "conversation_id": conversation_id,
        "title": title,
        "create_time": 1700000000,
        "update_time": updated,
        "project_id": "proj-1",
        "project_name": "Demo Project",
        "memberships": ["owner"],
    }
    _write_jsonl(root / "conversations.jsonl", [listing])
    cdir = root / "conversations" / conversation_id
    cdir.mkdir(parents=True, exist_ok=True)
    body = {
        "create_time": 1700000000,
        "update_time": 1700001000,
        "mapping": {
            "n1": {"message": {"content": {"parts": ["hi"]}}},
            "n2": {"message": {"content": {"parts": ["hello"]}}},
        },
    }
    (cdir / "conversation.json").write_text(json.dumps(body), encoding="utf-8")
    (cdir / "conversation.md").write_text(f"# {title}\n", encoding="utf-8")


def _build_codex_home(home: Path) -> Path:
    """Synthetic Codex home: one legacy-named rollout + Desktop SQLite."""
    home.mkdir(parents=True, exist_ok=True)
    sess = home / "sessions" / "2026" / "01" / "02"
    sess.mkdir(parents=True, exist_ok=True)
    rollout = sess / f"{LEGACY_STEM}.jsonl"
    events = [
        {"session_id": SHARED_UUID, "timestamp": "2026-01-02T03:04:05Z", "cwd": "C:/proj/a"},
        {"type": "user", "text": "hello", "timestamp": "2026-01-02T03:05:00Z"},
        {"type": "assistant", "text": "world", "timestamp": "2026-01-02T03:06:00Z"},
    ]
    _write_jsonl(rollout, events)

    # Older session that will be mutated after a newer watermark exists
    old = home / "sessions" / "2025" / "12" / "01" / f"rollout-2025-12-01T00-00-00-{OLD_UUID}.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(old, [
        {"session_id": OLD_UUID, "timestamp": "2025-12-01T00:00:00Z", "cwd": "C:/proj/old"},
        {"type": "user", "text": "old", "timestamp": "2025-12-01T00:01:00Z"},
    ])

    db = home / "state_5.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            metadata TEXT,
            position INTEGER
        );
        CREATE TABLE project_roots (
            project_id TEXT,
            position INTEGER,
            path TEXT
        );
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            rollout_path TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            cwd TEXT,
            title TEXT,
            name TEXT,
            first_user_message TEXT,
            project_id TEXT,
            creator_account_id TEXT,
            creator_user_id TEXT,
            created_at_ms INTEGER,
            updated_at_ms INTEGER,
            source TEXT,
            model TEXT,
            archived INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO projects(id,name,metadata,position) VALUES (?,?,?,?)",
        ("proj-desktop", "Alpha", "{}", 0),
    )
    conn.execute(
        "INSERT INTO project_roots(project_id,position,path) VALUES (?,?,?)",
        ("proj-desktop", 0, "C:/proj/a"),
    )
    # Thread matching the rollout
    conn.execute(
        "INSERT INTO threads(id,rollout_path,created_at,updated_at,cwd,title,name,"
        "first_user_message,project_id,creator_account_id,creator_user_id,"
        "created_at_ms,updated_at_ms,source,model,archived) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            SHARED_UUID, str(rollout), 1700000000, 1700002000, "C:/proj/a",
            "Desktop Title", "Desktop Title", "hello", "proj-desktop",
            "acct-99", "user-1", 1700000000000, 1700002000000, "cli", "gpt-test", 0,
        ),
    )
    # METADATA_ONLY gap — no rollout file
    conn.execute(
        "INSERT INTO threads(id,rollout_path,created_at,updated_at,cwd,title,name,"
        "first_user_message,project_id,creator_account_id,creator_user_id,"
        "created_at_ms,updated_at_ms,source,model,archived) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            META_ONLY_UUID, None, 1700003000, 1700004000, "C:/proj/gap",
            "Gap Thread", "Gap Thread", None, "proj-desktop",
            "acct-99", "user-1", 1700003000000, 1700004000000, "gui", "gpt-test", 0,
        ),
    )
    # Old session thread
    conn.execute(
        "INSERT INTO threads(id,rollout_path,created_at,updated_at,cwd,title,name,"
        "first_user_message,project_id,creator_account_id,creator_user_id,"
        "created_at_ms,updated_at_ms,source,model,archived) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            OLD_UUID, str(old), 1600000000, 1600001000, "C:/proj/old",
            "Old Thread", "Old Thread", "old", "proj-desktop",
            None, None, 1600000000000, 1600001000000, "cli", "gpt-test", 0,
        ),
    )
    conn.commit()
    conn.close()
    return db


class TestUuidNormalize(unittest.TestCase):
    def test_extract_uuid_from_legacy_stem(self):
        self.assertEqual(extract_uuid(LEGACY_STEM), SHARED_UUID)
        self.assertEqual(
            normalize_codex_session_id(LEGACY_STEM),
            SHARED_UUID,
        )


class TestNisabaMultiAccount(unittest.TestCase):
    def test_duplicate_uuid_across_accounts_no_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            a = tmp_path / "nisaba_a"
            b = tmp_path / "nisaba_b"
            _build_nisaba_root(a, SHARED_UUID, title="Acct A", updated="2026-09-01T00:00:00Z")
            _build_nisaba_root(b, SHARED_UUID, title="Acct B", updated="2026-09-02T00:00:00Z")
            out = tmp_path / "out"
            res = collect_nisaba(
                out,
                nisaba_roots=[a, b],
                nisaba_accounts=["primary", "account2"],
                full=True,
            )
            self.assertEqual(res["new_sessions_this_run"], 2)
            ledger = load_session_ledger(out, "chatgpt")
            self.assertEqual(len(ledger), 2)
            key_a = make_identity_key("chatgpt", "primary", SHARED_UUID)
            key_b = make_identity_key("chatgpt", "account2", SHARED_UUID)
            self.assertIn(key_a, ledger)
            self.assertIn(key_b, ledger)
            self.assertEqual(ledger[key_a]["title"], "Acct A")
            self.assertEqual(ledger[key_b]["title"], "Acct B")
            self.assertEqual(ledger[key_a]["capture_status"], CAPTURE_POINTER)


class TestCodexEnrichment(unittest.TestCase):
    def test_legacy_id_desktop_enrichment_and_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "codex_home"
            _build_codex_home(home)
            out = tmp_path / "out"
            res = collect_codex(out, home=home, state_db=home / "state_5.sqlite", full=True)
            self.assertGreaterEqual(res["new_sessions_this_run"], 3)
            ledger = load_session_ledger(out, "codex")
            # Normalized UUID key present; legacy stem must not be the session_id
            self.assertTrue(all(r["session_id"] != LEGACY_STEM for r in ledger.values()))
            shared_rows = [r for r in ledger.values() if r["session_id"] == SHARED_UUID]
            self.assertEqual(len(shared_rows), 1)
            shared = shared_rows[0]
            self.assertEqual(shared["title"], "Desktop Title")
            self.assertEqual(shared["native_project_id"], "proj-desktop")
            self.assertIsNotNone(shared.get("account_id"))
            self.assertTrue(str(shared.get("open_hint", "")).startswith("codex://threads/"))
            self.assertEqual(shared["capture_status"], "FULL")
            self.assertIn(SHARED_UUID, shared["identity_key"])

            meta_rows = [r for r in ledger.values() if r["session_id"] == META_ONLY_UUID]
            self.assertEqual(len(meta_rows), 1)
            self.assertEqual(meta_rows[0]["capture_status"], CAPTURE_METADATA_ONLY)
            self.assertEqual(meta_rows[0]["message_count"], 0)


class TestIncrementality(unittest.TestCase):
    def test_unchanged_second_run_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "codex_home"
            _build_codex_home(home)
            out = tmp_path / "out"
            r1 = collect_codex(out, home=home, state_db=home / "state_5.sqlite", full=True)
            r2 = collect_codex(out, home=home, state_db=home / "state_5.sqlite", full=False)
            self.assertGreater(r1["new_sessions_this_run"], 0)
            self.assertEqual(r2["new_sessions_this_run"], 0)
            self.assertGreater(r2["skipped_unchanged"], 0)
            # Ledger unique count stable; index may have historical rows
            ledger = load_session_ledger(out, "codex")
            self.assertEqual(len(ledger), r1["unique_sessions"])

    def test_older_session_change_detected_after_newer_watermark(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "codex_home"
            _build_codex_home(home)
            out = tmp_path / "out"
            collect_codex(out, home=home, state_db=home / "state_5.sqlite", full=True)
            # Mutate older carrier: same day stamp path but larger content
            old = home / "sessions" / "2025" / "12" / "01" / f"rollout-2025-12-01T00-00-00-{OLD_UUID}.jsonl"
            events = [
                {"session_id": OLD_UUID, "timestamp": "2025-12-01T00:00:00Z", "cwd": "C:/proj/old"},
                {"type": "user", "text": "old", "timestamp": "2025-12-01T00:01:00Z"},
                {"type": "assistant", "text": "grown", "timestamp": "2025-12-01T00:02:00Z"},
            ]
            _write_jsonl(old, events)
            # Soft watermark is newest overall; per-session fingerprint must still catch OLD
            r = collect_codex(out, home=home, state_db=home / "state_5.sqlite", full=False)
            self.assertGreaterEqual(r["new_sessions_this_run"], 1)
            ledger = load_session_ledger(out, "codex")
            old_row = next(r for r in ledger.values() if r["session_id"] == OLD_UUID)
            self.assertGreaterEqual(int(old_row["version"]), 2)
            self.assertEqual(old_row["message_count"], 3)

    def test_same_timestamp_different_carrier_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "codex_home"
            _build_codex_home(home)
            out = tmp_path / "out"
            collect_codex(out, home=home, state_db=home / "state_5.sqlite", full=True)
            rollout = home / "sessions" / "2026" / "01" / "02" / f"{LEGACY_STEM}.jsonl"
            # Keep timestamps identical, change size
            events = [
                {"session_id": SHARED_UUID, "timestamp": "2026-01-02T03:04:05Z", "cwd": "C:/proj/a"},
                {"type": "user", "text": "hello", "timestamp": "2026-01-02T03:05:00Z"},
                {"type": "assistant", "text": "world!!!!!!!!!!!!!!!!!!!!!!!!", "timestamp": "2026-01-02T03:06:00Z"},
            ]
            _write_jsonl(rollout, events)
            r = collect_codex(out, home=home, state_db=home / "state_5.sqlite", full=False)
            self.assertGreaterEqual(r["new_sessions_this_run"], 1)


class TestPartialRecovery(unittest.TestCase):
    def test_missing_nisaba_listing_then_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "nisaba_broken"
            root.mkdir()
            (root / "account_manifest.json").write_text("{}", encoding="utf-8")
            out = tmp_path / "out"
            res = collect_nisaba(out, nisaba_roots=[root], nisaba_accounts=["broken"], full=True)
            self.assertTrue(res["per_account"][0].get("partial") or res["per_account"][0].get("reason"))
            # Recover
            _build_nisaba_root(root, SHARED_UUID, title="Recovered", updated="2026-10-01T00:00:00Z")
            res2 = collect_nisaba(out, nisaba_roots=[root], nisaba_accounts=["broken"], full=True)
            self.assertEqual(res2["new_sessions_this_run"], 1)
            ledger = load_session_ledger(out, "chatgpt")
            self.assertEqual(len(ledger), 1)

    def test_corrupt_conversation_json_marked_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "nisaba_corrupt"
            _build_nisaba_root(root, SHARED_UUID, title="X", updated="2026-10-01T00:00:00Z")
            cjson = root / "conversations" / SHARED_UUID / "conversation.json"
            cjson.write_text("{not-json", encoding="utf-8")
            out = tmp_path / "out"
            collect_nisaba(out, nisaba_roots=[root], nisaba_accounts=["c"], full=True)
            ledger = load_session_ledger(out, "chatgpt")
            row = next(iter(ledger.values()))
            self.assertEqual(row["capture_status"], CAPTURE_PARTIAL)


class TestCatalogQuery(unittest.TestCase):
    def test_unified_catalog_and_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "codex_home"
            _build_codex_home(home)
            a = tmp_path / "nisaba_a"
            b = tmp_path / "nisaba_b"
            _build_nisaba_root(a, SHARED_UUID, title="Acct A", updated="2026-09-01T00:00:00Z")
            _build_nisaba_root(b, SHARED_UUID, title="Acct B", updated="2026-09-02T00:00:00Z")
            out = tmp_path / "out"
            collect_codex(out, home=home, state_db=home / "state_5.sqlite", full=True)
            collect_nisaba(out, nisaba_roots=[a, b], nisaba_accounts=["primary", "account2"], full=True)
            meta = build_unified_catalog(out)
            self.assertGreaterEqual(meta["sessions"], 5)
            self.assertTrue(Path(meta["catalog"]).is_file())
            self.assertTrue(Path(meta["digest"]).is_file())
            # No chat bodies in catalog
            with Path(meta["catalog"]).open(encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line)
                    self.assertNotIn("events", row)
                    blob = json.dumps(row)
                    self.assertNotIn("hello world!!!!!!!!!!!!!!!!!!!!!!!!", blob)
            hits = query_catalog(out, source="chatgpt", account_id="account2", limit=10)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["title"], "Acct B")
            gaps = query_catalog(out, capture_status=CAPTURE_METADATA_ONLY, limit=10)
            self.assertGreaterEqual(len(gaps), 1)


class TestCliSmoke(unittest.TestCase):
    def test_cli_all_with_multi_nisaba(self):
        from thoth.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "codex_home"
            _build_codex_home(home)
            a = tmp_path / "nisaba_a"
            _build_nisaba_root(a, SHARED_UUID, title="A", updated="2026-09-01T00:00:00Z")
            out = tmp_path / "out"
            # Point collectors via env overrides
            import os
            os.environ["CODEX_HOME"] = str(home)
            os.environ["CODEX_STATE_DB"] = str(home / "state_5.sqlite")
            # claude/cursor homes absent -> skipped cleanly
            os.environ["CLAUDE_HOME"] = str(tmp_path / "missing_claude")
            os.environ["CURSOR_STATE_DB"] = str(tmp_path / "missing_cursor.vscdb")
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main([
                    "all", "--output", str(out),
                    "--nisaba", str(a), "--nisaba-account", "primary",
                    "--json", "--full",
                ])
            self.assertEqual(rc, 0)
            self.assertTrue((out / "unified_catalog.jsonl").is_file())
            self.assertTrue((out / "daily_digest.jsonl").is_file())
            payload = json.loads(buf.getvalue())
            self.assertIn("catalog", payload)


if __name__ == "__main__":
    unittest.main()
