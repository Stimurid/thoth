"""Acceptance: default Thoth collectors NEVER ingest CLI/SDK/Hub runs."""
import json, os, sqlite3, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from thoth.sources.gui_dialogues import collect_codex_gui, collect_claude_gui
from thoth.common import load_session_ledger, build_unified_catalog

A = "aaaaaaaa-1111-2222-3333-444444444444"
B = "bbbbbbbb-1111-2222-3333-444444444444"
C = "cccccccc-1111-2222-3333-444444444444"
D = "dddddddd-1111-2222-3333-444444444444"
E = "eeeeeeee-1111-2222-3333-444444444444"


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


class GUIScopeTests(unittest.TestCase):
    def test_codex_guionly_filter_and_text_not_tool_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "codex_home"
            home.mkdir()
            db = home / "state_5.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("""CREATE TABLE threads(
                id TEXT PRIMARY KEY, rollout_path TEXT, created_at INTEGER,
                updated_at INTEGER, cwd TEXT, title TEXT, project_id TEXT,
                creator_account_id TEXT, source TEXT, originator TEXT,
                thread_source TEXT)""")
            gui = home / "sessions" / (A + ".jsonl")
            cli = home / "sessions" / (C + ".jsonl")
            jsonl(gui, [
                {"type": "session_meta", "payload": {"id": A, "originator": "Codex Desktop"}},
                {"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "A human GUI question"}]}},
                {"type": "response_item", "payload": {"type": "function_call",
                    "name": "shell", "arguments": "CLI_SECRET_NEVER_ARCHIVE"}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "Human-visible answer"},
                                {"type": "tool_use", "text": "hidden tool"}]}},
            ])
            jsonl(cli, [{"type": "response_item", "payload": {"type": "message",
                "role": "user", "content": [{"type": "input_text", "text": "CLI_SECRET_NEVER_ARCHIVE"}]}}])
            entries = [
                (A, str(gui), "vscode", "Codex Desktop", "user"),
                (B, None, "vscode", "codex_work_desktop", "chatgpt_handoff"),
                (C, str(cli), "exec", "codex_exec", "user"),
                (D, None, '{"subagent":"review"}', "Codex Desktop", "subagent"),
                (E, None, "vscode", None, "user"),
            ]
            for i, (sid, rollout, source, origin, thread_source) in enumerate(entries):
                conn.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, rollout, 1700000000, 1700000000+i, "C:/projects/test",
                     "A title", None, "acct-A", source, origin, thread_source))
            conn.commit(); conn.close()
            out = root / "out"
            first = collect_codex_gui(out, home=home)
            self.assertEqual(first["verified_gui"], 2)
            self.assertEqual(first["excluded"], 3)
            self.assertEqual(first["handoffs"], 1)
            self.assertEqual(first["new_sessions_this_run"], 2)
            ledger = load_session_ledger(out, "codex")
            self.assertEqual(len(ledger), 2)
            rows = list(ledger.values())
            self.assertEqual({r["session_id"] for r in rows}, {A, B})
            self.assertEqual({r["session_kind"] for r in rows},
                             {"CODEX_GUI_USER", "CODEX_GUI_HANDOFF"})
            self.assertEqual(sum(r["capture_status"] == "METADATA_ONLY" for r in rows), 1)
            txt = " ".join(f.read_text(encoding="utf-8")
                           for f in (out / "codex").rglob("*") if f.is_file())
            self.assertIn("A human GUI question", txt)
            self.assertIn("Human-visible answer", txt)
            self.assertNotIn("CLI_SECRET_NEVER_ARCHIVE", txt)
            self.assertNotIn("hidden tool", txt)
            second = collect_codex_gui(out, home=home)
            self.assertEqual(second["new_sessions_this_run"], 0)
            self.assertEqual(build_unified_catalog(out)["sessions"], 2)

    def test_claude_desktop_and_no_cli_sdk_or_tool_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "claude_projects"
            proj = home / "C--projects--demo"
            gui = proj / (A + ".jsonl")
            sdk = proj / (B + ".jsonl")
            cli = proj / (C + ".jsonl")
            jsonl(gui, [
                {"type": "queue-operation", "operation": "enqueue", "content": "Internal queue"},
                {"type": "user", "entrypoint": "claude-desktop", "uuid": A,
                 "message": {"role": "user", "content": [{"type": "text", "text": "GUI human prompt"},
                     {"type": "tool_result", "content": "DO_NOT_COPY_TOOL_OUTPUT"}]}},
                {"type": "assistant", "entrypoint": "claude-desktop", "uuid": B,
                 "message": {"id": "msg1", "role": "assistant", "content": [
                     {"type": "text", "text": "GUI assistant reply"},
                     {"type": "thinking", "thinking": "NO_PRIVATE_THINKING"},
                     {"type": "tool_use", "input": "NO_TOOL_CALLS"}]}},
                {"type": "file-history-snapshot", "snapshot": {"body": "NO_SNAPSHOT"}},
            ])
            jsonl(sdk, [
                {"type": "user", "entrypoint": "sdk-cli", "uuid": B,
                 "message": {"content": [{"type": "text", "text": "SDK_CLI_SECRET_NEVER_ARCHIVE"}]}}
            ])
            jsonl(cli, [
                {"type": "user", "entrypoint": "cli", "uuid": C,
                 "message": {"content": "CLI_SECRET_NEVER_ARCHIVE"}}
            ])
            out = root / "out"
            r1 = collect_claude_gui(out, home=home)
            self.assertEqual((r1["verified_gui"], r1["excluded"]), (1, 2))
            self.assertEqual(r1["new_sessions_this_run"], 1)
            row = next(iter(load_session_ledger(out, "claude-code").values()))
            self.assertEqual(row["session_kind"], "CLAUDE_GUI_USER")
            self.assertEqual(row["capture_status"], "PARTIAL")
            text = " ".join(p.read_text(encoding="utf-8")
                            for p in (out / "claude-code").rglob("*") if p.is_file())
            self.assertIn("GUI human prompt", text)
            self.assertIn("GUI assistant reply", text)
            for forbidden in ("SDK_CLI_SECRET_NEVER_ARCHIVE", "CLI_SECRET_NEVER_ARCHIVE",
                              "DO_NOT_COPY_TOOL_OUTPUT", "NO_PRIVATE_THINKING",
                              "NO_TOOL_CALLS", "NO_SNAPSHOT"):
                self.assertNotIn(forbidden, text)
            r2 = collect_claude_gui(out, home=home)
            self.assertEqual(r2["new_sessions_this_run"], 0)
            self.assertEqual(build_unified_catalog(out)["sessions"], 1)

    def test_cursor_gui_bubbles_only_excludes_subagents_and_tool_payloads(self):
        from thoth.sources.gui_dialogues import collect_cursor_gui
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); db=root/"cursor.vscdb"
            con=sqlite3.connect(db)
            con.execute("""CREATE TABLE composerHeaders(
                composerId TEXT, workspaceId TEXT, createdAt INTEGER,
                lastUpdatedAt INTEGER,isArchived INTEGER,isSubagent INTEGER,
                recency INTEGER,checkpointAt INTEGER,subagentTypeName TEXT,value TEXT)""")
            con.execute("CREATE TABLE cursorDiskKV(key TEXT, value TEXT)")
            con.execute("INSERT INTO composerHeaders VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("gui-composer","ws1",1700000000000,1700000000000,0,0,0,None,None,
                 json.dumps({"name":"Human GUI composer","type":"head"})))
            con.execute("INSERT INTO composerHeaders VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("child-run","ws1",1700000000000,1700000000000,0,1,0,None,None,
                 json.dumps({"name":"Subagent run","type":"head"})))
            for id, role, text in (("one",1,"Cursor human question"),
                                   ("two",2,"Cursor assistant answer"),
                                   ("three",99,"CURSOR_INTERNAL_TOOL_SECRET")):
                con.execute("INSERT INTO cursorDiskKV VALUES(?,?)",
                    ("bubbleId:gui-composer:"+id,
                     json.dumps({"type":role,"text":text,"tool_calls":["DO_NOT_KEEP_TOOL"]})))
            con.execute("INSERT INTO cursorDiskKV VALUES(?,?)",
                ("bubbleId:child-run:one",json.dumps({"type":1,"text":"SUBAGENT_CLI_SECRET"})))
            con.commit();con.close()
            out=root/"output"
            first=collect_cursor_gui(out,db=db,workspace_storage=root/"missing")
            self.assertEqual(first["verified_gui"],1)
            self.assertEqual(first["excluded"],1)
            self.assertEqual(first["new_sessions_this_run"],1)
            txt=" ".join(p.read_text(encoding="utf-8")
                         for p in (out/"cursor").rglob("*") if p.is_file())
            self.assertIn("Cursor human question",txt)
            self.assertIn("Cursor assistant answer",txt)
            self.assertNotIn("CURSOR_INTERNAL_TOOL_SECRET",txt)
            self.assertNotIn("DO_NOT_KEEP_TOOL",txt)
            self.assertNotIn("SUBAGENT_CLI_SECRET",txt)
            second=collect_cursor_gui(out,db=db,workspace_storage=root/"missing")
            self.assertEqual(second["new_sessions_this_run"],0)

    def test_ambiguous_claude_carrier_is_not_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); home=root/"claude"
            jsonl(home/"p"/(A+".jsonl"), [
                {"type":"user","entrypoint":"claude-desktop","uuid":A,
                 "message":{"content":"visible"}},
                {"type":"assistant","entrypoint":"sdk-cli",
                 "message":{"content":[{"type":"text","text":"MIXED_SHOULD_NOT_ARCHIVE"}]}}
            ])
            out=root/"out"
            res=collect_claude_gui(out,home=home)
            self.assertEqual(res["verified_gui"],0)
            self.assertEqual(res["ambiguous"],1)
            self.assertFalse((out/"claude-code"/"index.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
