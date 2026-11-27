"""baudetask: a Bot's task's script, talking to the host that runs it, over the wire (docs/tasks-design.md). The host
puts this folder on the script's PYTHONPATH and its run in its environment; nothing here but Python's own library, so
it can be copied anywhere.

    import baudetask as task

    task.inputs            # the run's form, filled: by hand, from its trigger's payload, or by default
    task.payload           # what its trigger saw (a git push's {repo, branch, before, after, commit, message, author})
    task.say("deploying", task.inputs["branch"])           # a line in the run's log
    answer = task.ask("Which of these is the release?")    # its Bot asked: what it said
    plan = task.ask("Resolve this conflict: ...", form=[{"name": "keep", "type": "choice",
                                                         "choices": ["ours", "theirs"]}])  # {"keep": "ours"}
    task.fallback(error)   # a failure sent to its Bot mid-run (if the task notifies on failure): what it said
    chat = task.agent("The deploy failed: ...", wait=False)  # a chat of its own started, nested under its Bot,
                           # kept with the run: {session, text, failed}; its Bot's told what it says as its turn ends

    @task.main             # run with the inputs; an exception's traceback printed, the run failed (and sent to
    def deploy(inputs): ...  # the Bot by its host once it's over, if the task notifies on failure)

Run outside a task (no BAUDE_TASK_RUN), its asks are answered with stand-ins: each field its default, its first
choice, 0, false or "" (the script itself runs as it is, side effects and all), and its agents too. baude-oneshot and
baude-mcp, run by the script, start their chats under its Bot too (BAUDE_SPAWNED_BY). Inputs then come from
BAUDE_TASK_INPUTS, or a JSON object as the first argument."""
import json
import os
import socket
import sys
import traceback

__all__ = ["inputs", "payload", "run", "say", "ask", "fallback", "agent", "main", "Refused"]

run = os.environ.get("BAUDE_TASK_RUN")
_DENIED = "Nobody was there to ask (a task's agent)."
_PING_S = 5  # (a wait this long without a word: the host told it's still here; it takes 16 s of silence for gone)


def _json_env(name, argv_at=None):
    text = os.environ.get(name)
    if text is None and argv_at is not None and len(sys.argv) > argv_at and sys.argv[argv_at].startswith("{"):
        text = sys.argv[argv_at]
    try:
        got = json.loads(text) if text else {}
    except ValueError:
        got = {}
    return got if isinstance(got, dict) else {}


inputs = _json_env("BAUDE_TASK_INPUTS", 1)
payload = _json_env("BAUDE_TASK_PAYLOAD")


class Refused(Exception):
    """The host said no: what it said."""


class _Wire:
    """One connection to the host's socket, a request at a time."""

    def __init__(self):
        self.sock, self.buf, self.req = None, b"", 0

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(os.environ["BAUDE_SOCKET"])
        version = int(os.environ.get("BAUDE_WIRE_VERSION") or 0)
        self.request({"op": "hello", "client": "task", **({"version": version} if version else {})})

    def line(self):
        while b"\n" not in self.buf:
            part = self.sock.recv(1 << 16)
            if not part:
                raise ConnectionError("the host closed the connection")
            self.buf += part
        got, self.buf = self.buf.split(b"\n", 1)
        return json.loads(got)

    def request(self, msg):
        if self.sock is None and msg.get("op") != "hello":
            self.connect()
        self.req += 1
        self.sock.sendall((json.dumps({**msg, "req": self.req}) + "\n").encode())
        while True:
            got = self.line()
            if got.get("req") == self.req:
                if got.get("op") == "error":
                    raise Refused(got.get("error") or got.get("detail") or "refused")
                return got


_wire = _Wire()


def _stand_in(form):
    return {f["name"]: f["default"] if "default" in f else (f.get("choices") or [""])[0]
            if f.get("type") == "choice" else 0 if f.get("type") == "number" else False
            if f.get("type") == "bool" else "" for f in form}


def say(*parts):
    """A line in the run's log (printed, outside a task)."""
    text = " ".join(str(p) for p in parts)
    if not run:
        print(text, flush=True)
        return
    _wire.request({"op": "task_report", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""), "say": text})


def ask(text, form=None, error=False):
    """The task's Bot asked text, waited for till its turn ends: what it said; with form (fields as a task's form
    has them: {name, type: text|number|bool|choice, choices?, default?, required?}), the values it filled, checked
    against the form. Refused: it couldn't answer, or not as asked."""
    if not run:
        print(f"ask: {text}", flush=True)
        return _stand_in(form) if form else "(a stand-in answer)"
    got = _wire.request({"op": "task_ask", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""), "text": str(text),
                         **({"form": form} if form else {}), **({"error": True} if error else {})})
    return got.get("values") if form else got.get("text", "")


def fallback(error):
    """A failure (an exception, or what went wrong) sent to the task's Bot, with the log so far: what it said."""
    if isinstance(error, BaseException):
        error = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    return ask(f"This failed:\n\n{error}", error=True)


def agent(prompt, wait=True, cwd=None, model=None, title=None, mode=None, timeout_s=3600):
    """A chat of its own started on the task's host with prompt (in cwd, the host's home if none; Auto at most, its
    asks for permission refused: at once while it's waited for, after a minute if not), nested under the task's Bot and kept with the run; the Bot's told
    what it says as its first turn ends. {session, text, failed}: with wait, what it said as that turn ended (or
    why it failed); else as soon as it's started (text None). Refused: the host didn't start it."""
    if not run:
        print(f"agent: {prompt}", flush=True)
        return {"session": None, "text": None if not wait else "(a stand-in answer)", "failed": False}
    start = {"op": "start", "prompt": str(prompt), "title": title or " ".join(str(prompt).split())[:60],
             "mode": mode or "auto", "ask_timeout": 60, "ask_default": "deny", "attach": bool(wait), "answers": False,
             "spawned_by": os.environ.get("BAUDE_SPAWNED_BY", ""),
             "task_run": {"run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", "")},
             **{k: v for k, v in (("cwd", cwd), ("model", model)) if v}}
    if not start["spawned_by"]:
        start.pop("spawned_by")
    sid = _wire.request(start)["session"]["id"]
    if not wait:
        return {"session": sid, "text": None, "failed": False}
    import time
    end, said = time.monotonic() + timeout_s, {"session": sid, "text": None, "failed": True}
    try:
        _wire.sock.settimeout(_PING_S)
        while time.monotonic() < end:
            try:
                got = _wire.line()
            except socket.timeout:  # (said something, so the host doesn't take it for gone)
                _wire.sock.sendall(b'{"op": "ping"}\n')
                continue
            if got.get("session") != sid:
                continue
            msg = got.get("msg") or {}
            if got.get("op") == "claude" and msg.get("type") == "control_request" and \
                    (msg.get("request") or {}).get("subtype") == "can_use_tool":  # (no one's here to ask: refused)
                _wire.sock.sendall((json.dumps({"op": "input", "session": sid, "msg": {
                    "type": "control_response", "response": {"subtype": "success", "request_id": msg.get("request_id"),
                                                             "response": {"behavior": "deny", "message": _DENIED}}}})
                                    + "\n").encode())
                continue
            if got.get("op") == "exited":
                said["text"] = "its agent ended before its turn did"
                break
            if got.get("op") == "claude" and msg.get("type") == "result":
                said.update(text=str(msg.get("result") or ""), failed=bool(msg.get("is_error")))
                break
    except (OSError, ValueError) as e:
        said["text"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            _wire.sock.settimeout(None)
            _wire.sock.sendall((json.dumps({"op": "detach", "session": sid}) + "\n").encode())
        except OSError:
            pass
    return said


def main(fn):
    """fn run with the inputs, as the script's main: an exception's traceback printed and the run failed. Its host
    sends the failure, with the end of the run's log, to the Bot once the run's over (if the task notifies on
    failure), so the Bot may run the task again as it's told it."""
    try:
        fn(inputs)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
    return fn
