"""Codex App Server adapter for Baude's host event interface."""
import itertools
import json
import os
import subprocess
import threading
from collections import deque

from baudelib.claude import ClaudeProc
from baudelib.models import CODEX_MODE_VALUES, codex_effort, codex_mode


def permission_params(mode, *, turn=False):
    """Manual and automatic review use the same workspace sandbox."""
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


def visible_stderr(line):
    try:
        record = json.loads(line)
    except ValueError:
        return True
    if not isinstance(record, dict) or record.get("target") != "codex_skills::interface":
        return True
    fields = record.get("fields")
    message = str(fields.get("message", "")) if isinstance(fields, dict) else ""
    return not message.startswith(("ignoring interface.icon_small:", "ignoring interface.icon_large:"))


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

    def __init__(self, exe, cwd, env, on_exit, stderr):
        self.on_exit, self.stderr = on_exit, stderr
        self.lock = threading.RLock()
        self.ids, self.requests = itertools.count(1), {}
        self.p = subprocess.Popen([exe, "app-server", "--listen", "stdio://"], cwd=cwd,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, bufsize=1, env=os.environ | (env or {}))

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
            if visible_stderr(line):
                self.stderr(line.rstrip())

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
                    self.stderr(line.rstrip())
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
                failed(msg["error"].get("message") or "Codex request failed")
            else:
                done(msg.get("result") or {})

    def event(self, method, rid, params):
        if rid is not None:
            self.reject(rid, f"Unsupported Codex request: {method}")

    def reject(self, rid, message):
        self.write({"id": rid, "error": {"code": -32601, "message": message}})


def list_models(exe, env=None, timeout=15):
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

    app = AppServer(exe, None, env, lambda code: failed(f"App Server exited ({code})"), lambda _: None)
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


class CodexProc(AppServer):
    """Translate one Codex thread to Baude's host event interface."""

    def __init__(self, on_msg, on_exit, cwd, model, resume=None, effort=None, env=None, exe=None, ephemeral=False,
                 catalog=(), mode="default"):
        self.on_msg = on_msg
        self.model, self.catalog = model, catalog
        self.mode = codex_mode(mode)
        self.effort = codex_effort(catalog, model, effort)
        self.init_rid = "codex-initialize"
        self.asks, self.streams, self.patches = {}, {}, {}
        self.tools = set()
        self.inputs = deque()
        self.indices = itertools.count()
        self.thread = self.turn = None
        self.last_text = ""
        self.starting = self.interrupting = False
        super().__init__(exe, cwd, env, on_exit, lambda line: self.emit("_stderr", text=line))
        params = {"cwd": cwd, **permission_params(self.mode)}
        if model != "codex/default":
            params["model"] = model.removeprefix("codex/")
        if resume:
            params.update(threadId=resume, excludeTurns=True)
        elif ephemeral:
            params["ephemeral"] = True

        def opened(result):
            self.thread = result["thread"]["id"]
            self.emit("system", subtype="init", session_id=self.thread, model=result.get("model") or self.model)
            self.reply(self.init_rid, {"current_permission_mode": self.mode, "commands": [],
                                       "applied": {"model": self.model, "effort": self.effort}})
            self.advance()

        def failed(error):
            self.reply(self.init_rid, error=error)
            self.result(error)
            self.kill()

        self.initialize(lambda: self.rpc("thread/resume" if resume else "thread/start", params, opened, failed),
                        failed)

    def emit(self, kind, **fields):
        self.on_msg(self, {"type": kind, **fields})

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
        elif method == "turn/completed":
            turn = params["turn"]
            status = turn.get("status")
            error = (turn.get("error") or {}).get("message") or (status if status != "completed" else None)
            self.finish(error, turn.get("durationMs"))
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") or {}
            used = (usage.get("last") or {}).get("totalTokens")
            if isinstance(used, int):
                self.emit("system", subtype="codex_context",
                          usage={"totalTokens": used, "maxTokens": usage.get("modelContextWindow"),
                                 "showWithoutLimit": True})
        elif method == "item/agentMessage/delta":
            self.delta(params["itemId"], params["delta"])
        elif method in ("item/started", "item/completed"):
            self.item(params["item"], method == "item/completed")
        elif method == "serverRequest/resolved":
            with self.lock:
                self.asks.pop(params.get("requestId"), None)

    def result(self, error=None, duration=None):
        self.emit("result", subtype="error" if error else "success", result=error or self.last_text,
                  is_error=bool(error), duration_ms=duration or 0, num_turns=1)

    def finish(self, error, duration=None):
        # Keep advance() blocked while the host accounts for this turn's result.
        with self.lock:
            self.turn = None
            self.starting = True
            self.interrupting = False
            self.streams.clear()
            self.tools.clear()
            self.patches.clear()
            self.asks.clear()
        self.result(error, duration)
        self.last_text = ""
        with self.lock:
            self.starting = False
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
                    params.update(permission_params(self.mode, turn=True))
                    self.starting = True
                    self.rpc("turn/start", params, lambda _: None, self.finish)
                    return
            self.result(error)

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
        if kind == "user":
            with self.lock:
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
            elif subtype == "get_settings":
                result = {"applied": {"model": self.model, "effort": self.effort}}
            elif subtype == "set_model" and str(request.get("model")).startswith("codex/"):
                self.model = request["model"]
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
