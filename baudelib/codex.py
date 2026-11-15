"""Codex App Server adapter for Baude's host event interface."""
import itertools
import json
import os
import subprocess
import threading
from collections import deque

from baudelib.claude import ClaudeProc


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


class CodexProc(ClaudeProc):
    """Own one stdio App Server process per session, using Codex's approval and sandbox settings."""

    def __init__(self, on_msg, on_exit, cwd, model, resume=None, effort=None, env=None, exe=None, ephemeral=False):
        self.on_msg, self.on_exit = on_msg, on_exit
        self.model, self.effort = model, effort
        self.init_rid = "codex-initialize"
        self.asks, self.requests, self.streams = {}, {}, {}
        self.tools = set()
        self.patches = {}
        self.inputs = deque()
        self.ids, self.indices = itertools.count(1), itertools.count()
        self.thread = self.turn = None
        self.last_text = ""
        self.starting = self.interrupting = False
        self.lock = threading.RLock()
        self.p = subprocess.Popen([exe, "app-server", "--listen", "stdio://"], cwd=cwd,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, bufsize=1, env=os.environ | (env or {}))
        params = {"cwd": cwd}
        if model != "codex/default":
            params["model"] = model.removeprefix("codex/")
        if resume:
            params.update(threadId=resume, excludeTurns=True)
        elif ephemeral:
            params["ephemeral"] = True

        def opened(result):
            self.thread = result["thread"]["id"]
            self.emit("system", subtype="init", session_id=self.thread, model=result.get("model") or self.model)
            self.reply(self.init_rid, {"current_permission_mode": "default", "commands": []})
            self.advance()

        def failed(error):
            self.reply(self.init_rid, error=error)
            self.result(error)
            self.kill()

        def initialized(_):
            with self.lock:
                super(CodexProc, self).send({"method": "initialized"})
                self.rpc("thread/resume" if resume else "thread/start", params, opened, failed)

        self.rpc("initialize", {"clientInfo": {"name": "baude", "version": "0.1"}}, initialized, failed)
        threading.Thread(target=self.read_stdout, daemon=True).start()
        threading.Thread(target=self._read, args=(self.p.stderr, True), daemon=True).start()

    def emit(self, kind, **fields):
        self.on_msg(self, {"type": kind, **fields})

    def reply(self, rid, result=None, error=None):
        fields = {"subtype": "error", "error": error} if error else {"subtype": "success", "response": result or {}}
        self.emit("control_response", response={"request_id": rid, **fields})

    def rpc(self, method, params, done, failed):
        with self.lock:
            rid = next(self.ids)
            self.requests[rid] = done, failed
            super().send({"id": rid, "method": method, "params": params})

    def read_stdout(self):
        try:
            for line in self.p.stdout:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.emit("_stderr", text=line.rstrip())
                    continue
                self.receive(msg)
        finally:
            self.close_stdin()
            self.on_exit(self.p.wait())

    def receive(self, msg):
        method, rid = msg.get("method"), msg.get("id")
        if not method:
            with self.lock:
                callbacks = self.requests.pop(rid, None)
            if callbacks:
                done, failed = callbacks
                if "error" in msg:
                    failed(msg["error"].get("message") or "Codex request failed")
                else:
                    done(msg.get("result") or {})
            return
        params = msg.get("params") or {}
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
                    if self.effort:
                        params["effort"] = self.effort
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

    def reject(self, rid, message):
        with self.lock:
            super().send({"id": rid, "error": {"code": -32601, "message": message}})

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
                    super().send({"id": rid, "result": {"decision": decision}})
        elif kind == "control_request":
            self.request(msg["request_id"], msg.get("request") or {})

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
            else:
                error = f"Codex backend does not support {subtype}"
        self.reply(rid, result, error)
