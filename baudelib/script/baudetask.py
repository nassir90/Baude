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
    asked = task.inbox("### Deploy 2.5?\n\n![staging](home.png)", ["Approve", {"label": "Deny", "style": "red"}],
                       pictures=["shots/home.png"], form=[{"name": "note", "type": "text"}])  # asked of a person, in
    got = asked.wait()     # every Inbox: the button pressed {button, label, values, by}; asked.poll() at once
    asked.revise(body="...")  # a new version of it;  asked.withdraw(): out of every Inbox
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

__all__ = ["inputs", "payload", "vars", "Model", "run", "say", "ask", "fallback", "inbox", "Asked", "Unanswered", "agent",
           "main", "Refused", "decide",
           "deciders", "choice", "noul", "score", "Decision", "DecisionUnavailable",
           *(SDK := ("emission", "terminal", "kube", "docker", "db", "chats", "subagents", "ops", "hosts", "models",
                    "subscriptions"))]

run = os.environ.get("BAUDE_TASK_RUN")
_DENIED = "Nobody was there to ask (a task's agent)."
_PING_S = 5  # (a wait this long without a word: the host told it's still here; it takes 16 s of silence for gone)
_IDLE_S = 10  # (a connection that's said nothing for this long between requests: another made for the next)


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
        if self.sock is not None and msg.get("op") != "hello" and time.monotonic() - self.sent > _IDLE_S:
            self.sock.close()  # (quiet so long the host may have let go of it: another)
            self.sock, self.buf = None, b""
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


class Unanswered(Refused):
    """An ask of a person that won't be answered now: its task withdrew it, or its run was stopped (status)."""

    def __init__(self, status):
        super().__init__(f"the ask was {status}")
        self.status = status


def _pictures(pictures):
    """inbox's pictures ({name: a path or bytes}, or paths, each named by its file's name) as the wire has them."""
    import base64
    items = pictures.items() if isinstance(pictures, dict) else ((os.path.basename(str(p)), p) for p in pictures)
    out = []
    for name, p in items:
        if not isinstance(p, (bytes, bytearray)):
            with open(os.path.expanduser(str(p)), "rb") as f:
                p = f.read()
        out.append({"name": str(name), "data": base64.b64encode(bytes(p)).decode()})
    return out


def _buttons(buttons):
    return [{"label": b} if isinstance(b, str) else dict(b) for b in buttons]


class Asked:
    """An ask raised in the Inbox (inbox()): id, its version (1 for the first) and its answer once it's had one
    ({button: its index, label, values: the form's, by: a Bot's name if a Bot answered it, else None})."""

    def __init__(self, aid, version, stand_in=None):
        self.id, self.version, self.answer, self.status, self._stand_in = aid, version, None, "waiting", stand_in

    def __repr__(self):
        return f"Asked({self.id!r}, status={self.status!r})"

    def _waited(self, timeout):
        if self._stand_in is not None:
            self.status, self.answer = "answered", self._stand_in
            return self.answer
        got = _w().request({"op": "inbox_wait", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""),
                            "ask": self.id, "timeout": timeout}, waits=True)
        self.status, self.answer = got.get("status") or "waiting", got.get("answer")
        if self.status in ("withdrawn", "stopped"):
            raise Unanswered(self.status)
        return self.answer

    def wait(self, timeout=None):
        """The answer, once a button's pressed (days, if it must: the run waits for input meanwhile, its timeout not
        counting it); None if timeout (seconds) went by first. Unanswered if it's withdrawn or the run's stopped."""
        return self._waited(timeout)

    def poll(self):
        """The answer if it's been answered, else None, at once."""
        return self._waited(0)

    def revise(self, body=None, buttons=None, form=None, title=None, pictures=None):
        """A new version of it (what's left out stays as it was): the person sees it at once. Its version now."""
        if self._stand_in is not None:
            print(f"inbox revise: {title or body or ''}", flush=True)
            self.version += 1
            return self.version
        msg = {"op": "inbox_revise", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""), "ask": self.id}
        for k, v in (("body", body), ("title", title), ("form", form)):
            if v is not None:
                msg[k] = v
        if buttons is not None:
            msg["buttons"] = _buttons(buttons)
        if pictures is not None:
            msg["pictures"] = _pictures(pictures)
        self.version = _w().request(msg)["version"]
        return self.version

    def withdraw(self):
        """Taken back: out of every Inbox, unanswered."""
        if self._stand_in is None:
            _w().request({"op": "inbox_withdraw", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""),
                          "ask": self.id})
        self.status = "withdrawn"


def inbox(body, buttons, form=None, title=None, pictures=None):
    """An ask of a person, raised in the Inbox of every app that may manage the task's Bot: body is Markdown, as a
    message is written (![alt](name) on a line of its own, or alone in a table's cell, shows the picture of that name);
    buttons, what may be pressed: each a label, or {label, style?: primary|secondary|blue|teal|green|yellow|orange|
    red|pink|purple|slate, picture?: a picture's name, drawn over its label}; form, fields filled as a button's pressed
    ({name, type: text|number|bool|choice, label?, choices?, default?, required?}, as a task's form has them);
    pictures: {name: a path or bytes}, or paths (each named by its file's name). Pressing a button answers it, with the
    form's values. An Asked: wait() or poll() for the answer, revise() it, withdraw() it. It stays in the Inbox,
    struck through, if the run ends with it unanswered. Refused: it isn't as an ask takes."""
    if not run:
        print(f"inbox: {title or body}", flush=True)
        first = _buttons(buttons)[0]["label"] if buttons else ""
        return Asked("stand-in", 1, {"button": 0, "label": first, "values": _stand_in(form) if form else {},
                                     "by": None})
    msg = {"op": "inbox_raise", "run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", ""), "body": str(body),
           "buttons": _buttons(buttons)}
    if title is not None:
        msg["title"] = str(title)
    if form:
        msg["form"] = form
    if pictures:
        msg["pictures"] = _pictures(pictures)
    got = _w().request(msg)
    return Asked(got["ask"], got.get("version") or 1)


def agent(prompt, wait=True, cwd=None, model=None, title=None, mode=None, timeout_s=3600, account=None):
    """A chat of its own started on the task's host with prompt (in cwd, the host's home if none; Auto at most, its
    asks for permission refused: at once while it's waited for, after a minute if not), nested under the task's Bot and kept with the run; the Bot's told
    what it says as its first turn ends. {session, text, failed}: with wait, what it said as that turn ended (or
    why it failed); else as soon as it's started (text None). model: a model's value, or a model variable
    (task.vars: its effort too). account: the subscription it runs on (subscriptions(): a Subscription, or an id or
    name of one, "" for the CLI's own sign-in), in place of the Bot's. Refused: the host didn't start it."""
    if not run:
        print(f"agent: {prompt}", flush=True)
        return {"session": None, "text": None if not wait else "(a stand-in answer)", "failed": False}
    start = {"op": "start", "prompt": str(prompt), "title": title or " ".join(str(prompt).split())[:60],
             "mode": mode or "auto", "ask_timeout": 60, "ask_default": "deny", "attach": bool(wait), "answers": False,
             "spawned_by": os.environ.get("BAUDE_SPAWNED_BY", ""),
             "task_run": {"run": run, "token": os.environ.get("BAUDE_TASK_TOKEN", "")},
             **{k: v for k, v in (("cwd", cwd), ("model", str(model) if model else None),
                                  ("effort", getattr(model, "effort", None))) if v},
             **({"account": account.get("id") if isinstance(account, dict) else account} if account is not None
                else {})}
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


def __getattr__(name):
    """The SDK's (baudetask_sdk: emission, terminal, kube, docker, db, chats, subagents, ops, hosts, models,
    subscriptions), loaded as it's first used."""
    if name in SDK:
        import baudetask_sdk
        return getattr(baudetask_sdk, name)
    raise AttributeError(f"module 'baudetask' has no attribute {name!r}")
