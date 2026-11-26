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
    task.fallback(error)   # a failure handed to its Bot mid-run (if the task's failures are): what it said

    @task.main             # run with the inputs; an exception's traceback printed, the run failed (and handed to
    def deploy(inputs): ...  # the Bot by its host once it's over, if the task's failures are)

Run outside a task (no BAUDE_TASK_RUN), or run with dummy answers, its asks are answered by a dummy agent: each field
its default, its first choice, 0, false or "" (the script itself runs as it is, side effects and all). Inputs then
come from BAUDE_TASK_INPUTS, or a JSON object as the first argument."""
import json
import os
import socket
import sys
import traceback

__all__ = ["inputs", "payload", "run", "dry", "say", "ask", "fallback", "main", "Refused"]

run = os.environ.get("BAUDE_TASK_RUN")
dry = os.environ.get("BAUDE_TASK_DRY") == "1" or not run


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


def _dummy(form):
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
        return _dummy(form) if form else "(a dummy agent's answer)"
    got = _wire.request({"op": "task_ask", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""), "text": str(text),
                         **({"form": form} if form else {}), **({"error": True} if error else {})})
    return got.get("values") if form else got.get("text", "")


def fallback(error):
    """A failure (an exception, or what went wrong) handed to the task's Bot, with the log so far: what it said."""
    if isinstance(error, BaseException):
        error = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    return ask(f"This failed:\n\n{error}", error=True)


def main(fn):
    """fn run with the inputs, as the script's main: an exception's traceback printed and the run failed. Its host
    hands the failure, with the end of the run's log, to the Bot once the run's over (if the task's failures are), so
    the Bot may run the task again as it's handed it."""
    try:
        fn(inputs)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
    return fn
