"""ACP wire compatibility and Baude event translation without an agy installation."""
import os
import tempfile
import threading
import unittest

from baudelib.antigravity import AntigravityProc, content_blocks, discover, model_options, supported_version


FAKE = '''#!/usr/bin/env python3
import json, sys
def send(obj):
    print(json.dumps({"jsonrpc": "2.0", **obj}), flush=True)
prompt_id = None
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        send({"id": rid, "result": {"protocolVersion": 1, "agentCapabilities":
              {"loadSession": True, "sessionCapabilities": {"resume": {}}}}})
    elif method in ("session/new", "session/load", "session/resume"):
        send({"id": rid, "result": {"sessionId": "ag-123", "modes": {"availableModes": [{"id": "auto"}]},
              "configOptions": [{"id": "model", "type": "select", "options":
                                 [{"value": "gemini-3", "name": "Gemini 3"}]}]}})
    elif method in ("session/set_mode", "session/set_config_option"):
        send({"id": rid, "result": {}})
    elif method == "session/prompt":
        prompt_id = rid
        send({"id": 900, "method": "session/request_permission", "params":
              {"sessionId": "ag-123", "toolCall": {"toolCallId": "call-1", "title": "Run command",
               "rawInput": {"CommandLine": "true"}}, "options":
               [{"optionId": "yes", "kind": "allow_once", "name": "Allow"},
                {"optionId": "no", "kind": "reject_once", "name": "Deny"}]}})
    elif rid == 900:
        assert msg["result"]["outcome"]["optionId"] == "yes"
        send({"method": "session/update", "params": {"sessionId": "ag-123", "update":
              {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello"}}}})
        send({"id": prompt_id, "result": {"stopReason": "end_turn"}})
'''


class AntigravityTests(unittest.TestCase):
    def test_model_and_content_translation(self):
        self.assertEqual(model_options([{"id": "model", "type": "select", "options": [
            {"group": "g", "options": [{"value": "gemini-3", "name": "Gemini 3"}]}]}])[1]["value"],
                         "antigravity/gemini-3")
        self.assertEqual(content_blocks("hello"), [{"type": "text", "text": "hello"}])
        self.assertFalse(supported_version("1.5.0"))
        self.assertTrue(supported_version("agy_acp_server_1.5.1"))

    def test_session_turn_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = os.path.join(folder, "agy")
            with open(executable, "w") as file:
                file.write(FAKE)
            os.chmod(executable, 0o700)
            self.assertEqual(discover(executable, folder)[1]["value"], "antigravity/gemini-3")
            events = []
            ready = threading.Event()
            finished = threading.Event()

            def received(_proc, msg):
                events.append(msg)
                if msg["type"] == "control_request":
                    _proc.send({"type": "control_response", "response": {"request_id": msg["request_id"],
                                "subtype": "success", "response": {"behavior": "allow"}}})
                if msg["type"] == "control_response":
                    ready.set()
                if msg["type"] == "result":
                    finished.set()

            proc = AntigravityProc(received, lambda _code: None, folder, "antigravity/gemini-3",
                                   resume="ag-123", exe=executable)
            try:
                self.assertTrue(ready.wait(5))
                proc.send({"type": "user", "message": {"role": "user", "content": "Hi"}})
                self.assertTrue(finished.wait(5))
                self.assertTrue(any(e.get("message", {}).get("content") ==
                                    [{"type": "text", "text": "Hello"}] for e in events))
                self.assertEqual(next(e for e in events if e["type"] == "result")["subtype"], "success")
            finally:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
