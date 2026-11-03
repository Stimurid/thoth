"""Synthetic acceptance of Thoth source MCP; never opens a real archive."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).with_name("thoth_mcp_port.py")
spec = importlib.util.spec_from_file_location("thoth_mcp_port_test", source)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class ThothMcpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        rows = [
            {"schema": "thoth.unified_catalog.v1", "source": "chatgpt",
             "account_id": "primary", "session_id": "identical",
             "title": "Shared name", "capture_status": "POINTER",
             "native_project_id": "project-A",
             "carrier_paths": ["c:/gpt-a.json"],
             "messages": [{"text": "PRIVATE_BODY_MUST_NOT_ESCAPE"}]},
            {"schema": "thoth.unified_catalog.v1", "source": "chatgpt",
             "account_id": "second", "session_id": "identical",
             "title": "Shared name", "capture_status": "PARTIAL",
             "native_project_id": "project-A",
             "carrier_paths": ["c:/gpt-b.json"]},
            {"schema": "thoth.unified_catalog.v1", "source": "codex",
             "account_id": "codex-acct-1", "session_id": "c1",
             "title": "Fix source", "capture_status": "FULL",
             "native_project_id": "project-A",
             "updated_at": "2026-11-03T14:00:00Z",
             "carrier_paths": ["c:/code.jsonl"]},
        ]
        self.db = self.root / "unified_catalog.jsonl"
        self.db.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        patcher = patch.dict(os.environ, {"THOTH_ARCHIVE_ROOT": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.baseline = self.db.read_bytes()

    def tearDown(self):
        self.assertEqual(self.db.read_bytes(), self.baseline, "MCP must not mutate source")

    def test_all_four_tools_readonly(self):
        names = {t.name for t in app.mcp._tool_manager.list_tools()}
        self.assertEqual(names, {"thoth_status", "thoth_search_sessions",
                                 "thoth_get_session", "thoth_projects"})
        s = app.status()
        self.assertEqual(s["total_records"], 3)
        self.assertEqual(len(s["account_instances"]), 3)
        self.assertEqual(s["capture_status_counts"]["PARTIAL"], 1)
        self.assertTrue(s["read_only"])

    def test_account_collision_and_exact_session(self):
        a = app.get_session("chatgpt", "primary", "identical")
        b = app.get_session("chatgpt", "second", "identical")
        self.assertTrue(a["ok"] and b["ok"])
        self.assertNotEqual(a["session"]["carrier_paths"], b["session"]["carrier_paths"])
        self.assertNotIn("PRIVATE_BODY_MUST_NOT_ESCAPE", json.dumps(a))
        self.assertFalse(a["session"]["raw_body_included"])

    def test_search_native_project_no_semantic_merge(self):
        x = app.search_sessions("Shared", source="chatgpt")
        self.assertEqual(x["matching"], 2)
        p = app.projects()
        self.assertEqual(p["matching"], 3)
        self.assertTrue(all(not row["owner_reviewed_merge"] for row in p["projects"]))

    def test_inputs_and_missing_config(self):
        with self.assertRaises(ValueError):
            app.search_sessions("x" * 161)
        with self.assertRaises(ValueError):
            app.get_session("codex", "", "some-id")
        os.environ.pop("THOTH_ARCHIVE_ROOT")
        with self.assertRaises(RuntimeError):
            app.status()


if __name__ == "__main__":
    unittest.main()
