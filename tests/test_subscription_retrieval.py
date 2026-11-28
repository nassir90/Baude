"""Direct subscription quota reads keep their account and window semantics."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from baudelib import antigravityquota, codex, hostaccounts


class CodexUsageTests(unittest.TestCase):
    def test_direct_usage_reads_selected_home_and_window_duration(self):
        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, "auth.json"), "w", encoding="utf-8") as f:
                json.dump({"auth_mode": "chatgpt", "tokens": {"access_token": "example-token",
                           "account_id": "example-account"}}, f)
            body = {"rate_limit": {"primary_window": {"used_percent": 17, "limit_window_seconds": 604800,
                                                      "reset_at": 1234567890}, "secondary_window": None}}

            def urlopen(request, timeout):
                self.assertEqual(request.full_url, "https://chatgpt.com/backend-api/wham/usage")
                self.assertEqual(request.get_header("Authorization"), "Bearer example-token")
                self.assertEqual(request.get_header("Chatgpt-account-id"), "example-account")
                self.assertEqual(timeout, 4)
                return io.BytesIO(json.dumps(body).encode())

            with patch("urllib.request.urlopen", side_effect=urlopen):
                self.assertEqual(codex.rate_limits(None, env={"CODEX_HOME": home}, timeout=4),
                                 {"primary": {"usedPercent": 17, "windowDurationMins": 10080,
                                              "resetsAt": 1234567890}})

    def test_expired_token_uses_codex_to_refresh(self):
        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, "auth.json"), "w", encoding="utf-8") as f:
                json.dump({"tokens": {"access_token": "expired"}}, f)
            failure = urllib.error.HTTPError("https://chatgpt.com/backend-api/wham/usage", 401, "", {}, None)
            with patch("urllib.request.urlopen", side_effect=failure), \
                    patch.object(codex, "_rate_limits_app_server", return_value={"primary": {"usedPercent": 25}}) as backup:
                self.assertEqual(codex.rate_limits("codex", env={"CODEX_HOME": home}),
                                 {"primary": {"usedPercent": 25}})
                backup.assert_called_once()


class AntigravityUsageTests(unittest.TestCase):
    def test_host_replies_with_quota_and_latest_context(self):
        answers = []
        tracked = {"context_used": 20, "context_size": 100}
        with patch.object(hostaccounts.accounts, "plan_usage", return_value=[("gemini_5_hour", 28, None)]), \
                patch.object(hostaccounts.accounts, "antigravity_usage", return_value=tracked):
            hostaccounts._usage(None, "", "antigravity", lambda **fields: answers.append(fields))
        self.assertEqual(answers, [{"usage": [{"name": "Gemini 5 hour", "key": "gemini_5_hour",
                                                "percent": 28.0}], "tracked": tracked}])

    def test_quota_summary_maps_shared_pools_and_skips_disabled_buckets(self):
        summary = {"groups": [{"buckets": [
            {"bucketId": "gemini-5h", "remainingFraction": .72, "resetTime": "2026-11-28T15:00:00Z"},
            {"bucketId": "gemini-weekly", "remainingFraction": .94},
            {"bucketId": "3p-5h", "remainingFraction": .5, "disabled": True},
            {"bucketId": "3p-weekly", "resetTime": "2026-11-29T15:00:00Z"}]}]}
        with patch.object(antigravityquota, "_servers", return_value=[(42, "csrf")]) as servers, \
                patch.object(antigravityquota, "_summary", return_value=summary):
            found = antigravityquota.read("/tmp/isolated")
        servers.assert_called_once_with("/tmp/isolated")
        self.assertEqual([item[:2] for item in found], [("gemini_5_hour", 28.0),
                                                        ("gemini_7_day", 6.0), ("claude_gpt_7_day", 100.0)])

    def test_named_account_only_probes_its_own_launcher(self):
        processes = ("100 1 agy acp --gemini-dir /tmp/one --app-data-dir antigravity-acp\n"
                     "101 100 language_server --app_data_dir antigravity-acp --csrf_token first\n"
                     "200 1 agy acp --gemini-dir /tmp/two --app-data-dir antigravity-acp\n"
                     "201 200 language_server --app_data_dir antigravity-acp --csrf_token second\n")

        def command(args, **_kwargs):
            return processes if args[0] == "ps" else "agy 201 user 1u IPv4 0t0 TCP 127.0.0.1:12345 (LISTEN)\n"

        with patch("subprocess.check_output", side_effect=command):
            self.assertEqual(list(antigravityquota._servers("/tmp/two")), [(12345, "second")])

    def test_hub_process_is_a_quota_server(self):
        processes = "200 1 /usr/local/bin/agy --hub --csrf_token direct\n"

        def command(args, **_kwargs):
            return processes if args[0] == "ps" else "agy 200 user 1u IPv4 0t0 TCP 127.0.0.1:54321 (LISTEN)\n"

        with patch("subprocess.check_output", side_effect=command):
            self.assertEqual(list(antigravityquota._servers()), [(54321, "direct")])

    def test_default_does_not_read_unrelated_ide_login(self):
        processes = "200 1 /Applications/Antigravity/language_server --app_data_dir antigravity --csrf_token ide\n"
        with patch("subprocess.check_output", return_value=processes) as run:
            self.assertEqual(list(antigravityquota._servers()), [])
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
