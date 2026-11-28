"""Google Antigravity's official ``agy acp`` agent as a Baude host process.

ACP is newline-delimited JSON-RPC over stdio.  This adapter deliberately leaves
file and terminal capabilities with agy; Baude only handles its permission
requests and renders its events.  Sign in with ``agy acp --login`` first.
"""
from collections import deque
import itertools
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid

from baudelib.claude import ClaudeProc
from baudelib.models import antigravity_choices
from baudelib import accounts


def content_blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks = []
    for block in content:
        if block.get("type") == "text":
            blocks.append({"type": "text", "text": block.get("text") or ""})
        elif block.get("type") == "image":
            source = block.get("source") or {}
            if source.get("type") != "base64":
                raise ValueError("Antigravity images must contain base64 data")
            blocks.append({"type": "image", "mimeType": source.get("media_type") or "image/png",
                           "data": source.get("data")})
        else:
            raise ValueError(f"Antigravity cannot send a {block.get('type')} attachment")
    return blocks


def model_options(config):
    option = next((o for o in config or () if o.get("id") == "model" and o.get("type") == "select"), None)
    return antigravity_choices(option.get("options") or []) if option else []


def supported_version(value):
    """agy 1.5.1 is the first release that serves non-allowlisted ACP clients."""
    match = re.match(r"^(?:v|agy_acp_server_)?(\d+)\.(\d+)\.(\d+)", value or "")
    return match is None or tuple(map(int, match.groups())) >= (1, 5, 1)


class AntigravityProc(ClaudeProc):
    """Translate ACP notifications and RPC responses to Baude's Claude-shaped host events."""

    def __init__(self, on_msg, on_exit, cwd, model, resume=None, env=None, exe=None, mode="default",
                 on_models=None, instructions=None, profile=""):
        self.on_msg, self.on_exit = on_msg, on_exit
        self.dispatch = lambda fn: fn()
        self.cwd, self.model, self.mode = cwd, model, mode
        self.on_models = on_models
        self.instructions = instructions
        self.profile = profile
        self.init_rid = "antigravity-initialize"
        self.req_ids = itertools.count(1)
        self.pending = {}
        self.asks = {}
        self.inputs = deque()
        self.lock = threading.RLock()
        self.thread = None
        self.turn = False
        self.stopping = False
        self.last_text = ""
        self.text_parts = []
        self.text_id = None
        self.tools = {}
        self.context_usage = {}
        self.started_at = 0
        self.resume = resume
        self.config = []
        self.available_modes = []
        args = [exe, "acp"]
        home = accounts.antigravity_home(profile) if profile else os.environ.get("BAUDE_ANTIGRAVITY_HOME")
        if home:
            args += ["--gemini-dir", os.path.abspath(os.path.expanduser(home)),
                     "--app-data-dir", "antigravity-acp"]
        self.p = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True,
                                  env=os.environ | (env or {}))
        threading.Thread(target=self._read_acp, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()
        self.rpc("initialize", {"protocolVersion": 1,
                                "clientCapabilities": {"fs": {"readTextFile": False,
                                                                "writeTextFile": False}, "terminal": False},
                                "clientInfo": {"name": "baude", "title": "Baude", "version": "1"}},
                 self._initialized, self._failed)

    def emit(self, kind, **fields):
        self.dispatch(lambda: self.on_msg(self, {"type": kind, **fields}))

    def reply(self, rid, result=None, error=None):
        response = {"request_id": rid, "subtype": "error" if error else "success"}
        response["error" if error else "response"] = error if error else result or {}
        self.emit("control_response", response=response)

    def write(self, message):
        with self.lock:
            try:
                self.p.stdin.write(json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False) + "\n")
                self.p.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def rpc(self, method, params, success, failure):
        rid = next(self.req_ids)
        with self.lock:
            self.pending[rid] = (success, failure)
        self.write({"id": rid, "method": method, "params": params})

    def _read_acp(self):
        for line in self.p.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                if line.strip():
                    self.emit("_stderr", text=line.strip())
                continue
            if not isinstance(message, dict):
                continue
            try:
                if "method" in message:
                    self.event(message)
                elif "id" in message:
                    with self.lock:
                        callback = self.pending.pop(message["id"], None)
                    if callback:
                        if "error" in message:
                            error = message["error"] if isinstance(message["error"], dict) else {}
                            callback[1](error.get("message") or "Antigravity request failed")
                        else:
                            callback[0](message.get("result") or {})
            except (KeyError, TypeError, ValueError) as exc:
                self._failed(f"Invalid Antigravity ACP response: {exc}")
                break
        code = self.p.wait()
        self.dispatch(lambda: self.on_exit(code))

    def _read_errors(self):
        for line in self.p.stderr:
            if line.strip():
                self.emit("_stderr", text=line.strip())

    def _failed(self, error):
        self.reply(self.init_rid, error=error)
        self.result(error)
        self.stopping = True
        self.kill()

    def _initialized(self, result):
        if result.get("protocolVersion") != 1:
            return self._failed("Antigravity does not support ACP v1")
        version = (result.get("agentInfo") or {}).get("version")
        if not supported_version(version):
            return self._failed(f"Antigravity {version} cannot serve Baude; update agy to 1.5.1 or later")
        caps = result.get("agentCapabilities") or {}
        params = {"cwd": self.cwd, "mcpServers": []}
        if self.instructions:
            if (caps.get("sessionCapabilities") or {}).get("systemPrompt") is not None:
                params["systemPrompt"] = {"mode": "append", "content": [{"type": "text", "text": self.instructions}]}
                self.instructions = None
            elif (((caps.get("_meta") or {}).get("antigravity") or {}).get("systemPrompt") or {}).get("append"):
                params["_meta"] = {"antigravity": {"systemPrompt": {"append": self.instructions}}}
                self.instructions = None
        if self.resume:
            params["sessionId"] = self.resume
            if "resume" in (caps.get("sessionCapabilities") or {}):
                method = "session/resume"
            elif caps.get("loadSession"):
                method = "session/load"
            else:
                return self._failed("Antigravity cannot resume this session")
        else:
            method = "session/new"
        self.rpc(method, params, self._opened, self._failed)

    def _opened(self, result):
        self.thread = result.get("sessionId") or self.resume
        if not self.thread:
            return self._failed("Antigravity did not return a session ID")
        self._set_config(result.get("configOptions") or [])
        self.available_modes = ((result.get("modes") or {}).get("availableModes") or [])
        if self.mode == "auto" and not any(m.get("id") == "auto" for m in self.available_modes):
            return self._failed("This Antigravity runtime does not offer Auto mode")
        if self.mode not in ("default", "auto", "bypassPermissions"):
            return self._failed(f"Unsupported Antigravity permission mode: {self.mode}")
        def ready():
            self.emit("system", subtype="init", session_id=self.thread, model=self.model)
            self.reply(self.init_rid, {"current_permission_mode": self.mode, "commands": [],
                                       "applied": {"model": self.model, "effort": None}})
            self.advance()
        if self.mode == "auto":
            self.rpc("session/set_mode", {"sessionId": self.thread, "modeId": "auto"},
                     lambda _result: self._select_model(ready), self._failed)
        else:
            self._select_model(ready)

    def _set_config(self, options):
        self.config = options
        choices = model_options(options)
        if choices and self.on_models:
            self.on_models(choices)

    def _select_model(self, done, failed=None):
        failed = failed or self._failed
        wanted = self.model.removeprefix("antigravity/")
        if wanted == "default":
            return done()
        choices = model_options(self.config)
        if choices and not any(c.get("resolvedModel") == wanted for c in choices):
            return failed(f"Antigravity model {wanted} is unavailable for this account")
        if self.config:
            self.rpc("session/set_config_option", {"sessionId": self.thread, "configId": "model", "value": wanted},
                     lambda result: (self._set_config(result.get("configOptions") or self.config), done()), failed)
        else:
            self.rpc("session/set_model", {"sessionId": self.thread, "modelId": wanted},
                     lambda _result: done(), failed)

    def advance(self):
        with self.lock:
            if not self.thread or self.turn or not self.inputs:
                return
            msg = self.inputs.popleft()
            self.turn = True
            self.last_text = ""
            self.text_parts = []
            self.text_id = None
            self.tools = {}
            self.started_at = time.monotonic()
        try:
            prompt = content_blocks((msg.get("message") or {}).get("content", ""))
        except ValueError as exc:
            self.finish(str(exc))
            return
        if self.instructions:
            prompt.insert(0, {"type": "text", "text": self.instructions})
            self.instructions = None
        self.rpc("session/prompt", {"sessionId": self.thread, "prompt": prompt}, self._prompt_done, self.finish)

    def _prompt_done(self, result):
        reason = result.get("stopReason")
        self.finish(None if reason in ("end_turn", "cancelled") else reason,
                    interrupted=reason == "cancelled")

    def result(self, error=None, interrupted=False):
        self.emit("result", subtype="error_during_execution" if interrupted else "error" if error else "success",
                  result=error or self.last_text, is_error=bool(error), num_turns=1,
                  duration_ms=int((time.monotonic() - self.started_at) * 1000) if self.started_at else 0,
                  usage={})

    def finish(self, error=None, interrupted=False):
        self._flush_text()
        with self.lock:
            self.turn = False
            self.asks.clear()
        self.result(error, interrupted)
        self.advance()

    def _text(self, update, thinking=False):
        block = update.get("content") or {}
        if block.get("type") != "text" or not block.get("text"):
            return
        text = block["text"]
        if thinking:
            self.emit("assistant", message={"id": str(uuid.uuid4()), "role": "assistant",
                                            "content": [{"type": "thinking", "thinking": text}]})
            return
        if not self.text_parts:
            self.text_id = str(uuid.uuid4())
            self.emit("stream_event", event={"type": "message_start", "message": {"id": self.text_id}})
            self.emit("stream_event", event={"type": "content_block_start", "index": 0,
                                               "content_block": {"type": "text"}})
        self.text_parts.append(text)
        self.last_text += text
        self.emit("stream_event", event={"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": text}})

    def _flush_text(self):
        if self.text_parts:
            self.emit("assistant", message={"id": self.text_id, "role": "assistant", "content": [
                {"type": "text", "text": "".join(self.text_parts)}]})
            self.text_parts = []
            self.text_id = None

    def _tool(self, update):
        tool_id = update.get("toolCallId")
        if not tool_id:
            return
        if tool_id not in self.tools:
            self._flush_text()
            name = update.get("title") or "Antigravity tool"
            inp = update.get("rawInput") or {}
            if not isinstance(inp, dict):
                inp = {"input": inp}
            self.tools[tool_id] = name
            self.emit("assistant", message={"id": tool_id, "role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": name, "input": inp}]})
        if update.get("status") in ("completed", "failed"):
            content = update.get("content") or []
            output = "\n".join((part.get("content") or {}).get("text", "") for part in content
                               if isinstance(part, dict) and isinstance(part.get("content"), dict))
            if not output and update.get("rawOutput") is not None:
                raw = update["rawOutput"]
                output = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            if len(output) > 32_000:
                output = "[Earlier output truncated]\n" + output[-32_000:]
            self.emit("user", message={"role": "user", "content": [{"type": "tool_result",
                "tool_use_id": tool_id, "content": output, "is_error": update["status"] == "failed"}]})
            self.tools.pop(tool_id, None)

    def event(self, message):
        method, params = message.get("method"), message.get("params") or {}
        if method == "session/update":
            if params.get("sessionId") != self.thread:
                return
            update = params.get("update") or {}
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                self._text(update)
            elif kind == "agent_thought_chunk":
                self._text(update, thinking=True)
            elif kind in ("tool_call", "tool_call_update"):
                self._tool(update)
            elif kind == "config_option_update":
                self._set_config(update.get("configOptions") or [])
            elif kind == "usage_update":
                self.context_usage = {"totalTokens": update.get("used"), "maxTokens": update.get("size"),
                                      "showWithoutLimit": True}
                self.emit("system", subtype="codex_context", usage=self.context_usage)
                cost = update.get("cost") if isinstance(update.get("cost"), dict) else {}
                try:
                    accounts.note_antigravity_usage(self.profile, self.thread, update.get("used"),
                                                    update.get("size"), cost.get("amount"), cost.get("currency"))
                except OSError as exc:
                    self.emit("_stderr", text=f"Antigravity usage could not be saved: {exc}")
        elif method == "session/request_permission" and "id" in message:
            rid = message["id"]
            tool = params.get("toolCall") or {}
            options = params.get("options") or []
            if self.mode == "bypassPermissions":
                choice = next((o for o in options if o.get("kind") == "allow_once"), None)
                if choice:
                    self.write({"id": rid, "result": {"outcome": {"outcome": "selected",
                                                               "optionId": choice["optionId"]}}})
                    return
            self.asks[rid] = options
            self.emit("control_request", request_id=rid, request={"subtype": "can_use_tool",
                      "tool_name": tool.get("title") or "Antigravity tool",
                      "input": tool.get("rawInput") or {}, "tool_use_id": tool.get("toolCallId")})
        elif "id" in message:
            self.write({"id": message["id"], "error": {"code": -32601, "message": "Method not supported"}})

    def send(self, msg):
        kind = msg.get("type")
        if kind == "user":
            with self.lock:
                self.inputs.append(msg)
            self.advance()
        elif kind == "control_response":
            answer = msg.get("response") or {}
            rid = answer.get("request_id")
            options = self.asks.pop(rid, [])
            allowed = (answer.get("response") or {}).get("behavior") == "allow"
            choice = next((o for o in options if o.get("kind") in
                           (("allow_once", "allow_always") if allowed else ("reject_once", "reject_always"))), None)
            outcome = {"outcome": "selected", "optionId": choice["optionId"]} if choice else {"outcome": "cancelled"}
            self.write({"id": rid, "result": {"outcome": outcome}})
        elif kind == "control_request":
            self.request(msg.get("request_id"), msg.get("request") or {})

    def request(self, rid, request):
        subtype = request.get("subtype")
        if subtype == "interrupt":
            if self.turn:
                self.write({"method": "session/cancel", "params": {"sessionId": self.thread}})
            self.reply(rid, {"running": self.turn})
        elif subtype == "get_context_usage":
            self.reply(rid, self.context_usage)
        elif subtype == "get_settings":
            self.reply(rid, {"applied": {"model": self.model, "effort": None}})
        elif subtype == "set_model" and str(request.get("model", "")).startswith("antigravity/"):
            self.model = request["model"]
            self._select_model(lambda: self.reply(rid), lambda error: self.reply(rid, error=error))
        elif subtype == "set_permission_mode":
            wanted = request.get("mode") or "default"
            if wanted not in ("default", "auto", "bypassPermissions") or wanted == "auto" and not any(
                    m.get("id") == "auto" for m in self.available_modes):
                return self.reply(rid, error="Antigravity does not support that permission mode")
            native = "auto" if wanted == "auto" else "default"
            self.rpc("session/set_mode", {"sessionId": self.thread, "modeId": native},
                     lambda _result: (setattr(self, "mode", wanted), self.reply(rid)),
                     lambda error: self.reply(rid, error=error))
        elif subtype == "apply_flag_settings":
            self.reply(rid)
        else:
            self.reply(rid, error=f"Antigravity backend does not support {subtype}")

    def kill(self):
        self.stopping = True
        if self.p.poll() is None:
            try:
                os.killpg(self.p.pid, signal.SIGTERM)
            except OSError:
                self.p.terminate()
            timer = threading.Timer(3, self.kill_hard)
            timer.daemon = True
            timer.start()

    def kill_hard(self):
        if self.p.poll() is None:
            try:
                os.killpg(self.p.pid, signal.SIGKILL)
            except OSError:
                self.p.kill()


def discover(exe, cwd, profile=""):
    """Ask a disposable ACP session for this account's available models."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="baude-antigravity-") as folder:
        result = []
        ready = threading.Event()
        proc = AntigravityProc(lambda _p, msg: ready.set() if msg.get("type") == "control_response" else None,
                               lambda _code: ready.set(), folder, "antigravity/default", exe=exe,
                               on_models=lambda choices: result.extend(choices), profile=profile)
        try:
            ready.wait(90)
            return result
        finally:
            proc.kill()
