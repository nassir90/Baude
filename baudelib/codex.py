"""Codex App Server adapter for Baude's host event interface."""
import itertools
import json
import os
import re
import subprocess
import threading
from collections import deque

from baudelib import accounts, wording
from baudelib.claude import ClaudeProc
from baudelib.models import CODEX_MODE_VALUES, codex_effort, codex_mode


def permission_params(mode, *, turn=False, read_only=False):
    """Manual and automatic review use the same workspace sandbox."""
    if read_only:
        return {"approvalPolicy": "on-request", "approvalsReviewer": "user",
                **({"sandboxPolicy": {"type": "readOnly", "networkAccess": False}} if turn
                   else {"sandbox": "read-only"})}
    full = mode == "bypassPermissions"
    params = {"approvalPolicy": "never" if full else "on-request",
              "approvalsReviewer": "auto_review" if mode == "auto" else "user"}
    if not turn:
        params["sandbox"] = "danger-full-access" if full else "workspace-write"
    elif full:
        params["sandboxPolicy"] = {"type": "dangerFullAccess"}
    else:
        params["sandboxPolicy"] = {"type": "workspaceWrite", "writableRoots": [],
                                   "networkAccess": False, "excludeTmpdirEnvVar": False, "excludeSlashTmp": False}
    return params


LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Codex's own housekeeping (usage reporting, telemetry): nothing the user can do about it, at any level.
QUIET_TARGETS = ("codex_analytics", "codex_otel", "codex_feedback")
NOT_SUPPORTED = re.compile(r"The '([^']+)' model is not supported when using Codex with a ChatGPT account")


def log_record(line):
    """(level, target, fields) of one line of Codex's JSON tracing log, or None for any other line."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or str(record.get("level", "")).upper() not in LEVELS:
        return None
    fields = record.get("fields")
    return str(record["level"]).upper(), str(record.get("target", "")), fields if isinstance(fields, dict) else {}


def plain(message, limit=300):
    """An error message on one line: an HTTP error's JSON body down to its own message, cut to a sentence's size."""
    head, sep, body = message.partition(": {")
    if sep:
        try:
            error = json.loads("{" + body).get("error")
            inner = error.get("message") if isinstance(error, dict) else error
            if isinstance(inner, str) and inner.strip():
                message = f"{head}: {inner}"
        except (ValueError, AttributeError):
            pass
    message = " ".join(message.split())
    return message if len(message) <= limit else message[:limit - 1].rstrip() + "…"


def stderr_notice(line, model=None):
    """(kind, text) of what the user should see of one line of Codex's stderr, or None. Its tracing log is for Codex's
    developers: warnings and below are dropped, but for the few that say something the user can act on (kind
    "model": the chat's model, (id, name), isn't one this account can use); errors are kept, as one plain line
    ("error"). Lines that aren't its log (a crash, say) are shown as they are ("raw")."""
    line = ANSI.sub("", line).rstrip()
    if not line.strip():
        return None
    record = log_record(line)
    if record is None:
        return "raw", line
    level, target, fields = record
    message = str(fields.get("message", ""))
    unsupported = NOT_SUPPORTED.search(message)
    if unsupported:
        if model is None or unsupported.group(1) != model[0]:
            return None  # (another model's: one it tried ahead, say, not the one this chat asks for)
        return "model", wording.say("model_providers.codex_unsupported", model=model[1])
    if level != "ERROR" or target.startswith(QUIET_TARGETS):
        return None
    if not message.strip():
        message = " ".join(f"{key}={value}" for key, value in fields.items() if key != "message") \
            or f"an error in {target or 'Codex'}"
    return "error", f"Codex: {plain(message)}"


def user_input(content):
    if not isinstance(content, (str, list)):
        raise ValueError("Codex input must be text or a list of content blocks")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    items = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("Invalid Codex content block")
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            items.append({"type": "text", "text": block["text"], "text_elements": []})
        elif kind == "image":
            source = block.get("source")
            if not isinstance(source, dict) or source.get("type") != "base64" or not all(
                isinstance(source.get(key), str) for key in ("media_type", "data")
            ):
                raise ValueError("Codex images must have a base64 source")
            items.append({"type": "image", "url": f'data:{source["media_type"]};base64,{source["data"]}'})
        else:
            raise ValueError(f"Codex does not support {kind} attachments in Baude")
    return items


def tool(item):
    kind = item["type"]
    if kind == "commandExecution":
        return "Bash", {"command": item.get("command", ""), "cwd": item.get("cwd")}, item.get("aggregatedOutput") or ""
    if kind == "fileChange":
        changes = item.get("changes", [])
        return "Codex file changes", {"changes": changes}, item.get("status") or ""
    if kind == "mcpToolCall":
        return (f'mcp__{item["server"]}__{item["tool"]}', item.get("arguments") or {},
                json.dumps(item.get("error") or item.get("result") or {}, ensure_ascii=False))
    return None


class AppServer(ClaudeProc):
    """A stdio JSON-RPC connection to one Codex App Server subprocess."""

    def __init__(self, exe, cwd, env, on_exit, stderr, profile=None):
        self.on_exit, self.stderr = on_exit, stderr
        self.lock = threading.RLock()
        self.ids, self.requests = itertools.count(1), {}
        self.said, self.said_lock = {}, threading.Lock()  # (notes shown: text → kind)
        self.error_code = None  # (the code of the last error Codex answered a request with)
        self.profile = accounts.selected("codex") if profile is None else profile
        auth = ["-c", 'cli_auth_credentials_store="file"'] if self.profile else []
        self.p = subprocess.Popen([exe, *auth, "app-server", "--listen", "stdio://"], cwd=cwd,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, bufsize=1, env=accounts.environment("codex", os.environ | (env or {}), self.profile),
                                  start_new_session=True)  # (a group of its own: what it runs, killed with it, kill_hard)

    def initialize(self, done, failed):
        def initialized(_):
            self.write({"method": "initialized"})
            done()

        self.rpc("initialize", {"clientInfo": {"name": "baude", "version": "0.1"}}, initialized, failed)
        threading.Thread(target=self.read_stdout, daemon=True).start()
        threading.Thread(target=self.read_stderr, daemon=True).start()

    def write(self, obj):
        with self.lock:
            ClaudeProc.send(self, obj)

    def read_stderr(self):
        for line in self.p.stderr:
            self.notice(line)

    def notice(self, line):
        notice = stderr_notice(line, self.current_model())
        if notice is None:
            return
        kind, text = notice
        with self.said_lock:  # (not self.lock: a long write to Codex's stdin holds that)
            if text in self.said:  # (each once: Codex repeats its warnings for every request)
                return
            self.said[text] = kind
        self.stderr(text)

    def forget(self, *kinds):
        """Notes of these kinds may be shown again."""
        with self.said_lock:
            self.said = {text: kind for text, kind in self.said.items() if kind not in kinds}

    def current_model(self):
        """(id, name) of the model this connection's chat uses, or None."""
        return None

    def rpc(self, method, params, done, failed):
        with self.lock:
            rid = next(self.ids)
            self.requests[rid] = done, failed
            self.write({"id": rid, "method": method, "params": params})

    def read_stdout(self):
        try:
            for line in self.p.stdout:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.notice(line)
                    continue
                self.receive(msg)
        finally:
            self.close_stdin()
            self.on_exit(self.p.wait())

    def receive(self, msg):
        method, rid = msg.get("method"), msg.get("id")
        if method:
            self.event(method, rid, msg.get("params") or {})
            return
        with self.lock:
            callbacks = self.requests.pop(rid, None)
        if callbacks:
            done, failed = callbacks
            if "error" in msg:
                self.error_code = msg["error"].get("code")  # (what the failed callback may ask about)
                failed(msg["error"].get("message") or "Codex request failed")
            else:
                done(msg.get("result") or {})

    def event(self, method, rid, params):
        if rid is not None:
            self.reject(rid, f"Unsupported Codex request: {method}")

    def reject(self, rid, message):
        self.write({"id": rid, "error": {"code": -32601, "message": message}})


def list_models(exe, env=None, timeout=15, profile=None):
    """Get the visible model catalog without creating a Codex thread."""
    from queue import Empty, Queue

    outcome = Queue()
    entries, cursors = [], set()

    def failed(error):
        outcome.put(RuntimeError(error))

    def page(result):
        entries.extend(result.get("data") or [])
        cursor = result.get("nextCursor")
        if not cursor:
            outcome.put(entries)
        elif cursor in cursors:
            failed("Codex model list returned a repeated cursor")
        else:
            cursors.add(cursor)
            app.rpc("model/list", {"cursor": cursor}, page, failed)

    app = AppServer(exe, None, env, lambda code: failed(f"App Server exited ({code})"), lambda _: None, profile)
    try:
        app.initialize(lambda: app.rpc("model/list", {}, page, failed), failed)
        try:
            result = outcome.get(timeout=timeout)
        except Empty:
            raise RuntimeError("Codex model list timed out") from None
        if isinstance(result, Exception):
            raise result
        return result
    finally:
        app.kill()
        try:
            app.p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            app.p.kill()
            app.p.wait()


SIGN_IN_ERRORS = ("codex account authentication required", "chatgpt authentication required")  # (what Codex says of a sign-in with no ChatGPT plan: an API key's, or none)


def rate_limits(exe, env=None, timeout=20, profile=""):
    """A Codex subscription's (profile's; "": the CLI's own sign-in, its CODEX_HOME) rate limits as its App Server reads
    them, without starting a thread: the snapshot's primary and secondary windows ({usedPercent, windowDurationMins,
    resetsAt}), None where Codex says its sign-in has none (an API key's, or none). Any other failure, a timeout
    included, raises RuntimeError."""
    from queue import Empty, Queue

    outcome = Queue()
    app = AppServer(exe, None, env, lambda code: outcome.put(RuntimeError(f"App Server exited ({code})")),
                    lambda _: None, profile)

    def failed(error):
        said = error.lower()
        auth = app.error_code == -32600 and any(text in said for text in SIGN_IN_ERRORS)
        outcome.put(None if auth else RuntimeError(error))

    try:
        app.initialize(lambda: app.rpc("account/rateLimits/read", {"excludeResetCreditDetails": True}, outcome.put, failed),
                       lambda error: outcome.put(RuntimeError(error)))
        try:
            result = outcome.get(timeout=timeout)
        except Empty:
            raise RuntimeError("Codex rate limits timed out") from None
        if isinstance(result, Exception):
            raise result
        snapshot = (result or {}).get("rateLimits")
        return snapshot if isinstance(snapshot, dict) else None
    finally:
        app.kill()
        try:
            app.p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            app.p.kill()
            app.p.wait()


class CodexProc(AppServer):
    """Translate one Codex thread to Baude's host event interface."""

    def __init__(self, on_msg, on_exit, cwd, model, resume=None, effort=None, env=None, exe=None, ephemeral=False,
                 catalog=(), mode="default", meta_system=None, meta_servers=None, dispatch=None,
                 recover_empty=False, profile=None, servers=None):
        self.on_msg = on_msg
        self.dispatch = dispatch or (lambda fn, *args: fn(*args))
        self.req_ids = itertools.count(1)
        self.context_usage = {}
        self.meta_only = meta_system is not None
        self.model, self.catalog = model, catalog
        self.read_only = ephemeral and not self.meta_only
        self.mode = "default" if self.read_only else codex_mode(mode)
        self.effort = codex_effort(catalog, model, effort)
        self.init_rid = "codex-initialize"
        self.asks, self.streams, self.patches = {}, {}, {}
        self.tools = set()
        self.inputs = deque()
        self.steers = []  # (messages sent as a turn's being started: given it once it has (steer))
        self.indices = itertools.count()
        self.thread = self.turn = None
        self.last_text = ""
        self.starting = self.interrupting = False
        super().__init__(exe, cwd, env, lambda code: self.dispatch(on_exit, code),
                         lambda line: self.emit("_stderr", text=line), profile)
        params = {"cwd": cwd, **permission_params(self.mode, read_only=self.read_only)}
        if model != "codex/default":
            params["model"] = model.removeprefix("codex/")
        if resume:
            params.update(threadId=resume, excludeTurns=True)
        elif ephemeral:
            params["ephemeral"] = True
        if servers:  # (MCP servers of the host's: a Bot's answer_task tool)
            params["config"] = {"mcp_servers": servers}

        def opened(result):
            self.thread = result["thread"]["id"]
            self.emit("system", subtype="init", session_id=self.thread, model=result.get("model") or self.model)
            self.reply(self.init_rid, {"current_permission_mode": self.mode, "commands": [],
                                       "applied": {"model": self.model, "effort": self.effort}})
            self.advance()

        def failed(error):
            self.stopping = True
            self.reply(self.init_rid, error=error)
            self.result(error)
            self.kill()

        def resume_failed(error):
            if recover_empty and "no rollout found" in error.lower():
                params.pop("threadId")
                params.pop("excludeTurns")
                self.emit("system", subtype="codex_discard_empty_thread")
                self.rpc("thread/start", params, opened, failed)
            else:
                failed(error)

        def start():
            if resume:
                self.rpc("thread/resume", params, opened, resume_failed)
            else:
                self.rpc("thread/start", params, opened, failed)

        def start_meta(result):
            servers = (result.get("config") or {}).get("mcp_servers") or {}
            params.update(baseInstructions=meta_system, sandbox="read-only", approvalPolicy="never",
                          config={
                              "mcp_servers": {**{name: {"enabled": False} for name in servers}, **(meta_servers or {})},
                              "features": {name: False for name in (
                                  "apps", "plugins", "shell_tool", "unified_exec", "multi_agent", "multi_agent_v2",
                                  "code_mode", "skill_search", "skill_mcp_dependency_install",
                              )},
                              "web_search": "disabled",
                          })
            start()

        self.initialize(lambda: self.rpc("config/read", {"cwd": cwd}, start_meta, failed) if self.meta_only else start(),
                        failed)

    def current_model(self):
        model = self.model
        if model == "codex/default":
            resolved = next((m for m in self.catalog if m["value"] == model), {}).get("resolvedModel")
            if not resolved:
                return None
            model = f"codex/{resolved}"
        choice = next((m for m in self.catalog if m["value"] == model), {})
        return model.removeprefix("codex/"), choice.get("displayName") or model.removeprefix("codex/")

    def emit(self, kind, **fields):
        self.dispatch(self.on_msg, self, {"type": kind, **fields})

    def reply(self, rid, result=None, error=None):
        fields = {"subtype": "error", "error": error} if error else {"subtype": "success", "response": result or {}}
        self.emit("control_response", response={"request_id": rid, **fields})

    def event(self, method, rid, params):
        if params.get("threadId") not in (None, self.thread):
            if rid is not None:
                self.reject(rid, "Request is for a different Codex thread")
            return
        if rid is not None:
            self.approval(method, rid, params)
        elif method == "turn/started":
            with self.lock:
                self.turn = params["turn"]["id"]
                self.starting = False
                if self.interrupting:
                    self.interrupt()
                else:
                    for msg in self.steers:
                        self.steer(msg)
                self.steers.clear()
            self.emit("system", subtype="codex_turn_started", session_id=self.thread)
        elif method == "turn/completed":
            turn = params["turn"]
            status = turn.get("status")
            error = (turn.get("error") or {}).get("message") or (status if status != "completed" else None)
            self.finish(error, turn.get("durationMs"), interrupted=status == "interrupted")
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") or {}
            used = (usage.get("last") or {}).get("totalTokens")
            if isinstance(used, int):
                with self.lock:
                    self.context_usage = {"totalTokens": used, "maxTokens": usage.get("modelContextWindow"),
                                          "showWithoutLimit": True}
                self.emit("system", subtype="codex_context", usage=self.context_usage)
        elif method == "item/agentMessage/delta":
            self.delta(params["itemId"], params["delta"])
        elif method in ("item/started", "item/completed"):
            self.item(params["item"], method == "item/completed")
        elif method == "serverRequest/resolved":
            with self.lock:
                self.asks.pop(params.get("requestId"), None)

    def result(self, error=None, duration=None, interrupted=False):
        # (a turn stopped ends as Claude Code's does: error_during_execution, Baude's chat says it stopped, not failed)
        subtype = "error_during_execution" if interrupted else "error" if error else "success"
        self.emit("result", subtype=subtype, result=error or self.last_text,
                  is_error=bool(error), duration_ms=duration or 0, num_turns=1)

    def finish(self, error, duration=None, interrupted=False):
        # Keep advance() blocked while the host accounts for this turn's result.
        with self.lock:
            self.turn = None
            self.starting = True
            self.interrupting = False
            self.streams.clear()
            self.tools.clear()
            self.patches.clear()
            self.asks.clear()
        self.result(error, duration, interrupted)
        self.last_text = ""
        with self.lock:
            self.starting = False
            self.inputs.extend(self.steers)  # (sent as a turn that never started was: turns of their own)
            self.steers.clear()
        self.advance()

    def advance(self):
        while True:
            with self.lock:
                if not self.thread or self.turn or self.starting or not self.inputs:
                    return
                msg = self.inputs.popleft()
                try:
                    params = {"threadId": self.thread, "input": user_input(msg["message"]["content"])}
                except (KeyError, ValueError) as exc:
                    error = str(exc)
                else:
                    if msg.get("uuid"):
                        params["clientUserMessageId"] = msg["uuid"]
                    if self.model != "codex/default":
                        params["model"] = self.model.removeprefix("codex/")
                    else:
                        default = next((m for m in self.catalog if m["value"] == self.model), {})
                        if default.get("resolvedModel"):
                            params["model"] = default["resolvedModel"]
                    if self.effort:
                        params["effort"] = self.effort
                    if not self.meta_only:
                        params.update(permission_params(self.mode, turn=True, read_only=self.read_only))
                    self.starting = True
                    self.forget("error", "raw")  # (a turn's own: one that comes back on a later turn is said again)
                    self.rpc("turn/start", params, lambda _: None, self.finish)
                    return
            self.result(error)

    def steer(self, msg):
        """(lock held) A message sent at the turn going on given to it (turn/steer): Codex takes it in as the turn goes
        on, with no turn of its own; one the turn's over before it's taken (or that Codex won't take into it)
        runs as the next turn."""
        def refused(_error):
            with self.lock:
                self.inputs.append(msg)
            self.advance()
        try:
            params = {"threadId": self.thread, "expectedTurnId": self.turn,
                      "input": user_input(msg["message"]["content"])}
        except (KeyError, ValueError):
            return refused(None)  # (said as the turn it runs as fails, advance)
        if msg.get("uuid"):
            params["clientUserMessageId"] = msg["uuid"]
        self.rpc("turn/steer", params, lambda _: None, refused)

    def interrupt(self):
        self.interrupting = not self.turn and (self.starting or bool(self.inputs))
        if self.turn:
            self.rpc("turn/interrupt", {"threadId": self.thread, "turnId": self.turn},
                     lambda _: None, lambda error: self.emit("_stderr", text=error))

    def delta(self, item_id, text):
        if item_id not in self.streams:
            index = self.streams[item_id] = next(self.indices)
            self.emit("stream_event", event={"type": "message_start", "message": {"id": item_id}})
            self.emit("stream_event", event={"type": "content_block_start", "index": index,
                                             "content_block": {"type": "text"}})
        self.emit("stream_event", event={"type": "content_block_delta", "index": self.streams[item_id],
                                         "delta": {"type": "text_delta", "text": text}})

    def assistant(self, item_id, block):
        self.emit("assistant", message={"id": item_id, "role": "assistant", "content": [block]})

    def item(self, item, completed):
        kind, item_id = item["type"], item["id"]
        if kind in ("agentMessage", "plan") and completed:
            if kind == "agentMessage":
                self.last_text = item["text"]
            self.assistant(item_id, {"type": "text", "text": item["text"]})
        elif kind == "reasoning" and completed and item.get("summary"):
            self.assistant(item_id, {"type": "thinking", "thinking": "\n".join(item["summary"])})
        elif (entry := tool(item)) is not None:
            name, inp, output = entry
            if kind == "fileChange" and not completed:
                self.patches[item_id] = item["changes"]
            if completed:
                self.patches.pop(item_id, None)
            if item_id not in self.tools:
                self.tools.add(item_id)
                self.assistant(item_id, {"type": "tool_use", "id": item_id, "name": name, "input": inp})
            if completed:
                self.emit("user", message={"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": item_id, "content": output,
                    "is_error": item.get("status") in ("failed", "declined") or bool(item.get("exitCode")),
                }]})

    def approval(self, method, rid, params):
        if method == "item/commandExecution/requestApproval" and params.get("networkApprovalContext"):
            name, inp = "Codex network access", params["networkApprovalContext"]
        elif method == "item/commandExecution/requestApproval" and params.get("command") \
                and not params.get("additionalPermissions"):
            name, inp = "Bash", {"command": params["command"], "cwd": params.get("cwd"),
                                 "description": params.get("reason") or ""}
        elif method == "item/fileChange/requestApproval" and not params.get("grantRoot") \
                and params.get("itemId") in self.patches:
            name, inp = "Codex file changes", {"changes": self.patches[params["itemId"]],
                                               "reason": params.get("reason") or ""}
        else:
            self.reject(rid, f"Unsupported Codex request: {method}")
            return
        with self.lock:
            self.asks[rid] = params
        self.emit("control_request", request_id=rid, request={"subtype": "can_use_tool",
                  "tool_name": name, "input": inp,
                  "tool_use_id": params.get("itemId") if name == "Bash" else None})

    def send(self, msg):
        kind = msg.get("type")
        if kind == "user":  # (at a turn: given it, steer; else a turn of its own)
            with self.lock:
                if self.turn and not self.interrupting:
                    return self.steer(msg)
                if self.starting and self.thread and not self.interrupting and not self.inputs:
                    return self.steers.append(msg)
                self.inputs.append(msg)
            self.advance()
        elif kind == "control_response":
            response = msg.get("response") or {}
            rid = response.get("request_id")
            with self.lock:
                params = self.asks.pop(rid, None)
                if params is not None:
                    allow = (response.get("response") or {}).get("behavior") == "allow"
                    decision = "accept" if allow else "decline"
                    if params.get("availableDecisions") and decision not in params["availableDecisions"]:
                        decision = "cancel"
                    self.write({"id": rid, "result": {"decision": decision}})
        elif kind == "control_request":
            self.request(msg["request_id"], msg.get("request") or {})

    def update_catalog(self, catalog):
        with self.lock:
            self.catalog = catalog
            self.effort = codex_effort(catalog, self.model, self.effort)

    def request(self, rid, request):
        subtype = request.get("subtype")
        result, error = {}, None
        with self.lock:
            if subtype == "interrupt":
                self.interrupt()
            elif subtype == "get_context_usage":
                result = self.context_usage
            elif subtype == "get_settings":
                result = {"applied": {"model": self.model, "effort": self.effort}}
            elif subtype == "set_model" and str(request.get("model")).startswith("codex/"):
                self.model = request["model"]
                self.forget("model")  # (back to a model it can't use: said again)
                self.effort = codex_effort(self.catalog, self.model, self.effort)
            elif subtype == "set_permission_mode" and isinstance(request.get("mode"), str) \
                    and request["mode"] in CODEX_MODE_VALUES:
                self.mode = request["mode"]
            elif subtype == "apply_flag_settings":
                effort = (request.get("settings") or {}).get("effortLevel")
                self.effort = codex_effort(self.catalog, self.model, effort)
            else:
                error = f"Codex backend does not support {subtype}"
        self.reply(rid, result, error)
