"""Independent review regression reproductions (synthetic, no source mutation)."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from thoth.common import build_unified_catalog, load_session_ledger, query_catalog
from thoth.sources.nisaba_archive import collect as collect_nisaba
from thoth.sources.cursor import collect as collect_cursor

UUID = "aaaaaaaa-1111-2222-3333-444444444444"


def write_listing(root, row, raw=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "conversations.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    if raw:
        p = root / "conversations" / row["conversation_id"]
        p.mkdir(parents=True, exist_ok=True)
        (p / "conversation.json").write_text(json.dumps({
            "mapping": {"a": {"message": {"content": {"parts": ["secret chat"]}}}},
            "update_time": 1740000000,
        }), encoding="utf-8")


def cursor_fixture(path, text):
    db = sqlite3.connect(path)
    db.executescript("""
       CREATE TABLE composerHeaders(
        composerId TEXT, workspaceId TEXT, createdAt INTEGER, lastUpdatedAt INTEGER,
        isArchived INTEGER, isSubagent INTEGER, recency INTEGER, checkpointAt INTEGER,
        subagentTypeName TEXT, value TEXT
       );
       CREATE TABLE cursorDiskKV(key TEXT, value TEXT);
    """)
    db.execute("INSERT INTO composerHeaders VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("composer-A", "ws-A", 1740000000000, 1740000000000, 0, 0, 0, None, None,
         json.dumps({"name": "Fixed title", "type": "head"})))
    db.execute("INSERT INTO cursorDiskKV VALUES(?,?)",
        ("bubbleId:composer-A:b1", json.dumps({"type": 1, "text": text})))
    db.commit()
    db.close()


class ReviewRegressions(unittest.TestCase):
    def test_codex_exec_desktop_subagents_not_mislabelled(self):
        from thoth.sources.codex import _codex_session_kind
        self.assertEqual(_codex_session_kind({
            "originator": "codex_exec", "source": "exec"
        }), "CODEX_EXEC")
        self.assertEqual(_codex_session_kind({
            "originator": "Codex Desktop", "source": "vscode"
        }), "CODEX_DESKTOP")
        self.assertEqual(_codex_session_kind({
            "originator": "codex_exec", "source": '{"subagent":"review"}'
        }), "CODEX_SUBAGENT")
        self.assertEqual(_codex_session_kind({
            "source": "vscode"
        }), "CODEX_VSCODE")
        self.assertEqual(_codex_session_kind({}), "UNKNOWN")

    def test_nisaba_symlink_parent_is_rejected_even_with_valid_uuid(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "archive"
            write_listing(root, {"conversation_id": UUID, "title": "Source",
                        "update_time": "2026-11-01T00:00:00Z"}, raw=False)
            out = Path(tmp) / "out"
            actual = Path.is_symlink
            with patch.object(Path, "is_symlink",
                              lambda self: self.name == "conversations" or actual(self)):
                result = collect_nisaba(out, nisaba_roots=[root],
                                        nisaba_accounts=["primary"])
            self.assertEqual(result["per_account"][0]["partial_this_run"], 1)
            item = next(iter(load_session_ledger(out, "chatgpt").values()))
            self.assertEqual(item["capture_status"], "PARTIAL")
            self.assertEqual(item["carrier_paths"], [])

    def test_nisaba_path_traversal_does_not_read_outside_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "archive"
            sensitive = base / "conversation.json"
            sensitive.write_text(json.dumps({"mapping": {"x": 123}, "secret": "OUTSIDE_SECRET"}),
                                 encoding="utf-8")
            write_listing(source, {"conversation_id": "../../",
                         "title": "Invalid path", "update_time": 1740000000}, raw=False)
            output = base / "output"
            result = collect_nisaba(output, nisaba_roots=[source],
                                    nisaba_accounts=["primary"])
            self.assertGreaterEqual(result["per_account"][0]["partial_this_run"], 1)
            catalog = (output / "chatgpt" / "index.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("OUTSIDE_SECRET", catalog)
            self.assertNotIn(str(sensitive), catalog)
            self.assertEqual(len(load_session_ledger(output, "chatgpt")), 1)

    def test_malicious_nested_membership_never_in_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            write_listing(source, {"conversation_id": UUID, "title": "Visible title",
                         "update_time": "2026-11-01T00:00:00Z",
                         "project_id": "project-1",
                         "memberships": [{"role": "owner", "body": "SENTINEL_SECRET",
                                         "credentials": {"password": "SENTINEL_SECRET"}}]})
            out = base / "out"
            collect_nisaba(out, nisaba_roots=[source], nisaba_accounts=["primary"])
            build_unified_catalog(out)
            for file in (out / "chatgpt" / "index.jsonl", out / "unified_catalog.jsonl"):
                txt = file.read_text(encoding="utf-8")
                self.assertNotIn("SENTINEL_SECRET", txt, str(file))
                self.assertNotIn('"memberships"', txt)
            row = query_catalog(out)[0]
            self.assertNotIn("extra", row)
            self.assertEqual(row["native_project_id"], "project-1")

    def test_two_archive_roots_must_not_reuse_one_explicit_account_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            a, b = base / "first", base / "second"
            for root in (a, b):
                write_listing(root, {"conversation_id": UUID, "title": root.name,
                              "update_time": "2026-11-01T00:00:00Z"})
            output = base / "output"
            with self.assertRaisesRegex(ValueError, "NISABA_ACCOUNT_LABEL_COLLISION"):
                collect_nisaba(output, nisaba_roots=[a, b],
                               nisaba_accounts=["same", "same"])
            self.assertFalse((output / "chatgpt" / "index.jsonl").exists())

    def test_cursor_bubble_mutation_with_unchanged_header_is_versioned(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            database = base / "cursor.db"
            cursor_fixture(database, "First content")
            out = base / "out"
            first = collect_cursor(out, db=database, workspace_storage=base / "missing")
            self.assertEqual(first["new_sessions_this_run"], 1)
            conn = sqlite3.connect(database)
            conn.execute("UPDATE cursorDiskKV SET value=? WHERE key=?",
                (json.dumps({"type": 1, "text": "Edited content"}),
                 "bubbleId:composer-A:b1"))
            conn.commit()
            conn.close()
            second = collect_cursor(out, db=database, workspace_storage=base / "missing")
            self.assertEqual(second["new_sessions_this_run"], 1)
            third = collect_cursor(out, db=database, workspace_storage=base / "missing")
            self.assertEqual(third["new_sessions_this_run"], 0)
            row = next(iter(load_session_ledger(out, "cursor").values()))
            self.assertEqual(row["version"], 2)

    def test_query_limit_is_bounded_and_invalid_limits_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "chatgpt" / "index.jsonl"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("".join(json.dumps({
                "source": "chatgpt", "account_id": "a",
                "session_id": str(i), "title": "Synthetic", "version": 1
            }) + "\n" for i in range(140)), encoding="utf-8")
            build_unified_catalog(base)
            self.assertEqual(len(query_catalog(base, limit=10**9)), 100)
            with self.assertRaisesRegex(ValueError, "THOTH_QUERY_LIMIT_INVALID"):
                query_catalog(base, limit=0)
            with self.assertRaisesRegex(ValueError, "THOTH_QUERY_LIMIT_INVALID"):
                query_catalog(base, limit=-1)


if __name__ == "__main__":
    unittest.main()
