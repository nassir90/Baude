"""Host-local subscription routing, including resume after switching accounts."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from baudelib import accounts, claude, codex
from baudelib.server import Server
from baudelib.serversessions import ServerSessions


class AccountsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": temp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_switch_and_resume(self):
        host = MagicMock(spec=Server)
        host.lock, host.sessions, host.stopped = threading.RLock(), {}, threading.Event()
        host.env, host.exe, host.socket_path = {"ANTHROPIC_API_KEY": "elsewhere"}, "claude", str(self.folder / "socket")
        host.state_dir = str(self.folder)
        host.log = lambda message: None
        host.group_of.return_value = ""
        host.can_run.return_value = True
        ids = []
        for name in ("Work", "Personal"):
            profile = accounts.add("claude", name, "fixture-" + name)
            accounts.select("claude", profile)
            session = ServerSessions.start(host, {"cwd": str(self.folder), "draft": True})
            ids.append((profile, session))
        for profile, session in ids:
            self.assertEqual(profile, session.meta["profiles"]["claude"])
            self.assertNotIn("fixture-", json.dumps(session.meta))
            with patch.object(claude.subprocess, "Popen") as spawn, patch.object(claude.threading, "Thread"):
                ServerSessions.ensure_proc(host, session)
            env = spawn.call_args.kwargs["env"]
            self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "fixture-" + dict(accounts.profiles("claude"))[profile])
            self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual((accounts.root() / "profiles.sqlite").stat().st_mode & 0o777, 0o600)

    def test_codex_profile_isolation(self):
        profiles = {}
        for name in ("Work", "Personal"):
            folder = self.folder / name
            folder.mkdir()
            (folder / "auth.json").write_text("{}")
            profiles[name] = accounts.add("codex", name, str(folder))
        accounts.select("codex", profiles["Personal"])
        for name, profile in (("Work", profiles["Work"]), ("Personal", None)):
            with patch.object(codex.subprocess, "Popen") as spawn:
                codex.AppServer("codex", None, {"OPENAI_API_KEY": "elsewhere"}, None, None, profile)
            env = spawn.call_args.kwargs["env"]
            self.assertEqual(env["CODEX_HOME"], str(self.folder / name))
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertIn('cli_auth_credentials_store="file"', spawn.call_args.args[0])
        (self.folder / "Work" / "auth.json").unlink()
        with self.assertRaises(OSError):
            accounts.environment("codex", {}, profiles["Work"])

    def test_subscription_usage_with_selected_cli(self):
        executable = self.folder / "claude"
        executable.write_text(f"#!{sys.executable}\n" + """import json, os, sys
query = json.loads(sys.stdin.readline())
assert query["request"] == {"subtype": "get_usage", "skip_behaviors": True}
assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "fixture-personal"
assert "ANTHROPIC_API_KEY" not in os.environ
print(json.dumps({"type": "control_response", "response": {"request_id": query["request_id"], "response": {
    "rate_limits": {"five_hour": {"utilization": 25}, "seven_day": {"utilization": None}}
}}}))
""")
        executable.chmod(0o700)
        profile = accounts.add("claude", "Personal", "fixture-personal")
        with patch.dict(os.environ, {"BAUDE_CLAUDE": str(executable), "ANTHROPIC_API_KEY": "other"}):
            self.assertEqual(accounts.plan_usage(profile), [("Five hour", 25)])

    def test_direct_session_keeps_its_account(self):
        work = accounts.add("claude", "Work", "first")
        personal = accounts.add("claude", "Home", "second")
        accounts.remember_claude_session("chat", work)
        accounts.select("claude", personal)
        self.assertEqual(accounts.claude_session_profile("chat"), work)
        self.assertEqual(accounts.claude_session_profile("older-cli-chat"), "")
        self.assertEqual(accounts.claude_session_profile(None), personal)

    def test_defaults_and_duplicate_labels(self):
        ambient = {"OPENAI_API_KEY": "fixture"}
        self.assertEqual(accounts.environment("codex", ambient), ambient)
        profile = accounts.add("claude", "Team", "first")
        with self.assertRaises(ValueError):
            accounts.add("claude", "Team", "second")
        accounts.select("claude", profile)
        accounts.select("claude", "")
        self.assertEqual(accounts.environment("claude", {}), {})
        with self.assertRaises(ValueError):
            accounts.select("codex", profile)
