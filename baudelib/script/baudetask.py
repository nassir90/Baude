"""baudetask: a Bot's task's script, talking to the host that runs it, over the wire (docs/tasks-design.md). The host
puts this folder on the script's PYTHONPATH and its run in its environment; nothing here but Python's own library, so
it can be copied anywhere (the folder: _baudetask_vars.py beside it, its variables).

    import baudetask as task

    task.inputs            # the run's form, filled: by hand, from its trigger's payload, or by default
    task.payload           # what its trigger saw (a git push's {repo, branch, before, after, commit, message, author})
    task.vars              # the task's variables, each bound once ("Translation Model": task.vars.translation_model),
                           # typed (a model, a host, a pod...), its warning if it isn't there now; None if not bound
    task.say("deploying", task.inputs["branch"])           # a line in the run's log
    answer = task.ask("Which of these is the release?")    # its Bot asked: what it said
    plan = task.ask("Resolve this conflict: ...", form=[{"name": "keep", "type": "choice",
                                                         "choices": ["ours", "theirs"]}])  # {"keep": "ours"}
    task.fallback(error)   # a failure sent to its Bot mid-run (if the task notifies on failure): what it said
    chat = task.agent("The deploy failed: ...", wait=False)  # a chat of its own started, nested under its Bot,
                           # kept with the run: {session, text, failed}; its Bot's told what it says as its turn ends

    urgent = task.decide(text, task.noul("Is this urgent?"), model="studio/triage")  # a decider's answer:
    if urgent.confidence < 0.8: ...                     # .noul, .confidence (its host's decider triage, on studio)
    got = task.decide(text, {"team": task.choice("Which team?", ["billing", "sales"]),
                             "mood": task.score("How frustrated?", ["calm", "frustrated", "depressed"])})
    got["team"].choice, got["mood"].score, got["mood"].scores  # "billing", 1.2, {"calm": 0.1, ...}
    task.deciders()        # the deciders its host knows, each with whether it answers: [{value, name, reachable, ...}]
                           # (decide raises DecisionUnavailable when its decider can't be asked: the script's own
                           # code decides what then, ask() say; nothing else is asked instead)
    @task.main             # run with the inputs; an exception's traceback printed, the run failed (and sent to
    def deploy(inputs): ...  # the Bot by its host once it's over, if the task notifies on failure)

Run outside a task (no BAUDE_TASK_RUN), its asks are answered with stand-ins: each field its default, its first
choice, 0, false or "" (the script itself runs as it is, side effects and all), and its agents and decisions too
(each question's first option or lowest level, confidence 0). baude-oneshot and
baude-mcp, run by the script, start their chats under its Bot too (BAUDE_SPAWNED_BY). Inputs then come from
BAUDE_TASK_INPUTS, or a JSON object as the first argument."""
import json
import os
import socket
import sys
import threading
import time
import traceback

from _baudetask_vars import Model, vars  # noqa: E402,A004 (beside this file, on the script's path)

__all__ = ["inputs", "payload", "vars", "Model", "run", "say", "ask", "fallback", "agent", "main", "Refused", "decide",
           "deciders", "choice", "noul", "score", "Decision", "DecisionUnavailable"]

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
    """One connection to the host's socket, a request at a time (each thread has its own: _w)."""

    def __init__(self):
        self.sock, self.buf, self.req, self.sent = None, b"", 0, time.monotonic()

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(os.environ["BAUDE_SOCKET"])
        version = int(os.environ.get("BAUDE_WIRE_VERSION") or 0)
        self.request({"op": "hello", "client": "task", **({"version": version} if version else {})})

    def send(self, msg):
        self.sock.sendall((json.dumps(msg) + "\n").encode())
        self.sent = time.monotonic()

    def line(self, waits=False):
        """The next message from the host. waits: a wait that may take days, the host told it's still here every
        _PING_S however much it says meanwhile (it takes the connection for gone after 16 s of silence)."""
        while b"\n" not in self.buf:
            if waits:
                due = self.sent + _PING_S - time.monotonic()
                if due <= 0:
                    self.send({"op": "ping"})
                    due = _PING_S
                self.sock.settimeout(due)
            try:
                part = self.sock.recv(1 << 16)
            except socket.timeout:
                continue
            finally:
                if waits:
                    self.sock.settimeout(None)
            if not part:
                raise ConnectionError("the host closed the connection")
            self.buf += part
        got, self.buf = self.buf.split(b"\n", 1)
        if waits and time.monotonic() - self.sent >= _PING_S:  # (a host saying a lot: pinged all the same)
            self.send({"op": "ping"})
        return json.loads(got)

    def request(self, msg, waits=False):
        """The host's answer to msg. waits: one that may take days (an ask, answered as its Bot's turn ends)."""
        if self.sock is None and msg.get("op") != "hello":
            self.connect()
        self.req += 1
        self.send({**msg, "req": self.req})
        while True:
            got = self.line(waits)
            if got.get("req") == self.req:
                if got.get("op") == "error":
                    raise Refused(got.get("error") or got.get("detail") or "refused")
                return got


_wires = threading.local()


def _w():
    """This thread's connection to the host (asks from threads of one script go on at once, each on its own)."""
    w = getattr(_wires, "w", None)
    if w is None:
        w = _wires.w = _Wire()
    return w


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
    _w().request({"op": "task_report", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""), "say": text})


def ask(text, form=None, error=False):
    """The task's Bot asked text, waited for till its turn ends (days, if it must: the run waits for input
    meanwhile, its timeout not counting it): what it said; with form (fields as a task's form
    has them: {name, type: text|number|bool|choice, choices?, default?, required?}), the values it filled, checked
    against the form. Refused: it couldn't answer, or not as asked."""
    if not run:
        print(f"ask: {text}", flush=True)
        return _stand_in(form) if form else "(a stand-in answer)"
    got = _w().request({"op": "task_ask", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""), "text": str(text),
                         **({"form": form} if form else {}), **({"error": True} if error else {})}, waits=True)
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
    why it failed); else as soon as it's started (text None). model: a model's value, or a model variable
    (task.vars: its effort too). Refused: the host didn't start it."""
    if not run:
        print(f"agent: {prompt}", flush=True)
        return {"session": None, "text": None if not wait else "(a stand-in answer)", "failed": False}
    start = {"op": "start", "prompt": str(prompt), "title": title or " ".join(str(prompt).split())[:60],
             "mode": mode or "auto", "ask_timeout": 60, "ask_default": "deny", "attach": bool(wait), "answers": False,
             "spawned_by": os.environ.get("BAUDE_SPAWNED_BY", ""),
             "task_run": {"run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", "")},
             **{k: v for k, v in (("cwd", cwd), ("model", str(model) if model else None),
                                  ("effort", getattr(model, "effort", None))) if v}}
    if not start["spawned_by"]:
        start.pop("spawned_by")
    wire = _w()
    sid = wire.request(start)["session"]["id"]
    if not wait:
        return {"session": sid, "text": None, "failed": False}
    end, said = time.monotonic() + timeout_s, {"session": sid, "text": None, "failed": True}
    try:
        while time.monotonic() < end:
            got = wire.line(waits=True)
            if got.get("session") != sid:
                continue
            msg = got.get("msg") or {}
            if got.get("op") == "claude" and msg.get("type") == "control_request" and \
                    (msg.get("request") or {}).get("subtype") == "can_use_tool":  # (no one's here to ask: refused)
                wire.send({"op": "input", "session": sid, "msg": {
                    "type": "control_response", "response": {"subtype": "success", "request_id": msg.get("request_id"),
                                                             "response": {"behavior": "deny", "message": _DENIED}}}})
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
            wire.send({"op": "detach", "session": sid})
        except OSError:
            pass
    return said


class DecisionUnavailable(Refused):
    """decide's decider couldn't be asked (not running, not answering, not there, its host out of reach): why. Nothing
    else is asked instead: what to do then is the script's (ask(), say)."""


def choice(instructions, options):
    """A question picking one of options: a list of names, or {name: what it means}."""
    criteria = dict(options) if isinstance(options, dict) else {str(o): None for o in options}
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def noul(instructions, true=None, false=None):
    """A yes-or-no question (true, false: what each means, if it helps)."""
    criteria = {k: v for k, v in (("true", true), ("false", false)) if v is not None}
    return {"type": "noul", "instructions": instructions, **({"criteria": criteria} if criteria else {})}


def score(instructions, levels):
    """A question rating against levels, lowest first."""
    return {"type": "score", "instructions": instructions, "criteria": [str(x) for x in levels]}


class Decision:
    """A decider's answer to one question. choice: the option picked (choice); noul: the probability it's true (noul);
    score: the expected level, 0 the lowest (score); scores: each option's or level's probability, by its name;
    confidence: how sure it is, calibrated (a noul's: the probability of the side it leans to); model: whose."""

    def __init__(self, question, got, model=""):
        self.type = got.get("type") or question.get("type")
        self.choice, self.noul, self.score = got.get("choice"), got.get("noul"), got.get("score")
        probs = got.get("probabilities") or {}
        legend = got.get("legend") or {}
        self.scores = {legend.get(k, k): v for k, v in probs.items()} if self.type == "score" else dict(probs)
        self.confidence = got.get("confidence")
        if self.confidence is None and isinstance(self.noul, (int, float)):
            self.confidence = max(self.noul, 1 - self.noul)
        self.model = model

    def __repr__(self):
        said = {"choice": self.choice, "noul": self.noul, "score": self.score}.get(self.type)
        return f"Decision({self.type}={said!r}, confidence={self.confidence!r})"


def _stand_in_answer(q):
    crit = q.get("criteria")
    if q.get("type") == "choice":
        first = next(iter(crit), "")
        return {"type": "choice", "choice": first, "probabilities": {k: 0.0 for k in crit}, "confidence": 0.0}
    if q.get("type") == "score":
        return {"type": "score", "score": 0.0, "legend": {str(i): x for i, x in enumerate(crit)},
                "probabilities": {str(i): 0.0 for i in range(len(crit))}, "confidence": 0.0}
    return {"type": "noul", "noul": 0.0, "confidence": 0.0}


def decide(state, questions, model=None, images=None):
    """questions (one, made with choice, noul or score; or several, {name: question}) about state (text, or data)
    put to a decider (model: a deciders() value: its host's by its name, another host's as "<host>/<name>"; none:
    its host's first), through the task's host: one Decision, or {name: Decision}. DecisionUnavailable if the decider
    can't be asked; Refused for a question it won't take."""
    one = isinstance(questions, dict) and isinstance(questions.get("type"), str)
    asked = {"answer": questions} if one else dict(questions)
    if not run:
        print(f"decide: {', '.join(str(q.get('instructions')) for q in asked.values())}", flush=True)
        got = {"answers": {k: _stand_in_answer(q) for k, q in asked.items()}, "model": ""}
    else:
        got = _w().request({"op": "decide", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""),
                            "state": state, "questions": asked, **({"decider": model} if model else {}),
                            **({"images": list(images)} if images else {})}, waits=True)
        if got.get("unavailable"):
            raise DecisionUnavailable(got["unavailable"])
    answers = got.get("answers") or {}
    out = {k: Decision(q, answers.get(k) or {}, got.get("model") or "") for k, q in asked.items()}
    return out["answer"] if one else out


def deciders():
    """The deciders the task's host knows, its own and the other hosts' in its mesh: [{value (what decide's model
    takes), name, host_name, reachable (it answered when last looked at), model, error}] ([] outside a task)."""
    if not run:
        return []
    return _w().request({"op": "deciders", "what": "list"}).get("deciders") or []


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
