"""Kimi source lineage regressions: synthetic data only."""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from thoth.common import build_unified_catalog, load_session_ledger
from thoth.sources.kimi_code import collect

A = "aaaaaaaa-1111-2222-3333-444444444444"
B = "bbbbbbbb-1111-2222-3333-444444444444"
C = "cccccccc-1111-2222-3333-444444444444"


def fixture(home: Path):
    (home / "sessions").mkdir(parents=True)
    (home / "device_id").write_text("fake-device-identity", encoding="utf-8")
    index = []
    for i, (sid, source, imported) in enumerate((
        ("ses_" + A, "claude-code", A),
        ("ses_" + B, "codex", B),
        ("ses_" + C, None, None),
    )):
        path = home / "sessions" / f"wd_{i}" / sid
        path.mkdir(parents=True)
        custom = ({"imported_from": source, "imported_source_id": imported,
                   "imported_source_path": "c:/source/private.jsonl",
                   "imported_at": "2026-11-02T09:00:00Z"} if source else {})
        state = {"id": sid, "createdAt": 1793500000000, "updatedAt": 1793600000000,
                 "cwd": "C:/projects/demo", "title": f"Work {i}",
                 "version": 2, "custom": custom}
        (path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        wire = path / "agents" / "main" / "wire.jsonl"
        wire.parent.mkdir(parents=True)
        wire.write_text(json.dumps({
            "type": "agent.message.appended",
            "message": {"content": "PRIVATE_MESSAGE_DO_NOT_COPY"},
        }) + "\n", encoding="utf-8")
        index.append({"sessionId": sid, "sessionDir": str(path),
                      "workDir": "C:/projects/demo"})
    (home / "session_index.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in index),
        encoding="utf-8",
    )


class KimiTests(unittest.TestCase):
    def test_lineage_exact_import_without_body_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "kimi-home"
            fixture(home)
            out = Path(tmp) / "out"
            one = collect(out, home=home)
            self.assertEqual((one["listed"], one["imported"], one["native"]), (3, 2, 1))
            self.assertEqual(one["new_sessions_this_run"], 3)
            self.assertEqual(one["unique_sessions"], 3)
            ledger = load_session_ledger(out, "kimi-code")
            self.assertEqual(len(ledger), 3)
            edges = [row["lineage_refs"][0] for row in ledger.values() if row.get("lineage_refs")]
            self.assertEqual({e["source"] for e in edges}, {"claude-code", "codex"})
            self.assertEqual({e["session_id"] for e in edges}, {A, B})
            self.assertTrue(all(e["relation"] == "IMPORTED_FROM" for e in edges))
            self.assertTrue(all(row["capture_status"] == "POINTER" for row in ledger.values()))
            self.assertTrue(all(row["session_kind"] in ("IMPORTED_CARRIER", "KIMI_NATIVE")
                                for row in ledger.values()))
            catalog = build_unified_catalog(out)
            self.assertEqual(catalog["sessions"], 3)
            for f in (out / "kimi-code" / "index.jsonl", out / "unified_catalog.jsonl"):
                body = f.read_text(encoding="utf-8")
                self.assertNotIn("PRIVATE_MESSAGE_DO_NOT_COPY", body)
                self.assertNotIn("c:/source/private.jsonl", body)
            second = collect(out, home=home)
            self.assertEqual(second["new_sessions_this_run"], 0)
            self.assertEqual(second["skipped_unchanged"], 3)

    def test_one_wire_change_reindexes_only_one_kimi_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "kimi-home"
            fixture(home)
            out = Path(tmp) / "out"
            collect(out, home=home)
            wire = home / "sessions" / "wd_0" / ("ses_" + A) / "agents" / "main" / "wire.jsonl"
            with wire.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "agent.message.appended", "message": {"content": "new"}})+"\n")
            result = collect(out, home=home)
            self.assertEqual(result["new_sessions_this_run"], 1)
            record = next(x for x in load_session_ledger(out, "kimi-code").values()
                          if x["session_id"] == "ses_" + A)
            self.assertEqual(record["version"], 2)
            self.assertEqual(record["message_count"], 2)

    def test_reject_path_escape_outside_kimi_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "kimi-home"
            fixture(home)
            index = home / "session_index.jsonl"
            rows = [json.loads(x) for x in index.read_text(encoding="utf-8").splitlines()]
            rows[0]["sessionDir"] = str(Path(tmp) / "private" / ("ses_" + A))
            index.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
            out = Path(tmp) / "out"
            result = collect(out, home=home)
            self.assertEqual(result["blocked"], 1)
            self.assertEqual(result["new_sessions_this_run"], 2)


if __name__ == "__main__":
    unittest.main()
