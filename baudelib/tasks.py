"""A Bot's tasks (docs/tasks-design.md): scripts its host keeps and runs, by hand or as one of their triggers fires (a
schedule; a git push to a branch of a repo on the host, seen by polling its head), each run given its form filled
(by hand, or from the trigger's payload), its Bot asked over the wire when the script can't decide or fails
(baudelib/script/baudetask.py: task_ask), and kept with its log: when it started, what started it, how long it took,
how it ended, the version of the script it ran.

Kept in the Bot's folder on its host (serverstate.bot_dir), tasks/: tasks.json (the tasks, each script by its version),
scripts/<version>.<ext> (every version of each script, by the first 12 hex of its SHA-256), runs/<id>.json and
runs/<id>.log (JSON lines: TaskLogLine), the latest RUNS_KEPT of them. A Bot's routines (what tasks were) are moved
over as its tasks are first read: each a bot task, its prompt the script, its schedule a trigger. No GTK."""
import datetime
import hashlib
import json
import math
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid

from baudelib import taskvars, wire, wording

RUNTIMES = tuple(wire.DEFS["Task"]["properties"]["runtime"]["enum"])
KINDS = tuple(wire.DEFS["TaskTrigger"]["properties"]["kind"]["enum"])
TYPES = tuple(wire.DEFS["TaskField"]["properties"]["type"]["enum"])
TASKS_MAX, FIELDS_MAX, TRIGGERS_MAX = 50, 30, 10
SCRIPT_MAX = 256 * 1024
RUNS_KEPT, RUNS_SHOWN, LOG_SHOWN = 100, 50, 2000
CHATS_KEPT = 50  # (the chats a run started, kept in its record: the latest)
LOG_MAX = 4 * 1024 * 1024  # (a run's log, at most: what's written after, dropped)
TIMEOUT_S, TIMEOUT_MAX = 3600, 24 * 3600
LOG_ENDING = 10  # (lines of a finished run's log its Bot is told, if it said nothing)
ENDING_MAX = 1500  # (characters of them)
ASK_S = 7 * 24 * 3600  # (an ask of its Bot waited for this long, at most; its run waiting for input meanwhile)
BUSY_SAID_S = 3600  # (a chat's report its Bot's been too busy to take for this long: said on the run)
RETELL_S = 5  # (a chat's report its Bot couldn't take: tried again after this long)
GRACE_S = 5  # (a run stopped: its script's given this long to stop, then killed)
MIN_EVERY, MAX_EVERY = 5, 60 * 24 * 31  # (a schedule's minutes: no more often than 5, no less often than a month)
POLL_S, POLL_MIN, POLL_MAX = 30, 10, 3600  # (a git push trigger's looks at its branch)
DAYS = (0, 1, 2, 3, 4, 5, 6)
EXT = {"python": "py", "shell": "sh", "bot": "md"}
SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "script")  # (baudetask, on a script's path)
_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII)
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,200}")


def version_of(script):
    return hashlib.sha256(script.encode()).hexdigest()[:12]


def _id(given=None):
    return given if isinstance(given, str) and wire.is_id(given) else uuid.uuid4().hex[:12]


def clean_field(f):
    if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not _FIELD.fullmatch(f["name"]):
        raise ValueError("a form field needs a name (letters, digits, _)")
    kind = f.get("type") if f.get("type") in TYPES else "text"
    out = {"name": f["name"], "type": kind}
    if isinstance(f.get("label"), str) and f["label"].strip():
        out["label"] = " ".join(f["label"].split())[:80]
    if kind == "choice":
        choices = [str(c)[:80] for c in f.get("choices") or [] if isinstance(c, (str, int, float))][:50]
        if not choices:
            raise ValueError(f"{f['name']}: a choice needs its choices")
        out["choices"] = choices
    if f.get("default") is not None:
        out["default"] = coerce(out, f["default"])
    if f.get("required"):
        out["required"] = True
    return out


def number_of(text):
    """text as a number, an int when it's whole: None when it's empty; ValueError when it isn't a finite number in
    plain digits (not nan, inf, 1e999 or 1_000)."""
    text = text.strip()
    if not text:
        return None
    if not _NUMBER.fullmatch(text):
        raise ValueError(text)
    n = float(text)
    if not math.isfinite(n):
        raise ValueError(text)
    return int(n) if n.is_integer() else n


def coerce(field, value):
    """value as field takes it, or ValueError."""
    kind = field.get("type")
    if kind == "number":
        if isinstance(value, bool):
            raise ValueError(f"{field['name']} is a number")
        try:
            n = number_of(value) if isinstance(value, str) else number_of(repr(value))
        except ValueError:
            n = None
        if n is None:
            raise ValueError(f"{field['name']} is a number")
        return n
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "yes", "1", "on"):
            return True
        if str(value).lower() in ("false", "no", "0", "off", ""):
            return False
        raise ValueError(f"{field['name']} is true or false")
    value = "" if value is None else value if isinstance(value, str) else json.dumps(value) \
        if isinstance(value, (dict, list)) else str(value)
    if kind == "choice" and value not in field.get("choices", ()):
        raise ValueError(f"{field['name']} is one of " + ", ".join(field.get("choices", ())))
    return value[:20000]


def next_run(t, after):
    """When a schedule t fires next after after (seconds since the epoch): every's from after; at's the next time of
    day it's on (local)."""
    if t.get("every"):
        return after + t["every"] * 60
    hour, minute = (int(x) for x in t["at"].split(":"))
    day = datetime.datetime.fromtimestamp(after)
    for ahead in range(8):
        when = (day + datetime.timedelta(days=ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when.weekday() in t["days"] and when.timestamp() > after:
            return when.timestamp()
    return after + 86400


def clock():
    """The host's clock, as its times of day are: its zone's name and how far ahead of UTC it is now (seconds)."""
    now = datetime.datetime.now().astimezone()
    return {"zone": now.tzname(), "offset": int(now.utcoffset().total_seconds())}


def clean_trigger(t, old=None, fields=()):
    """A trigger as it's given made one to keep (old: the same one as it was: when it fires next, what it saw), or
    ValueError."""
    if not isinstance(t, dict) or t.get("kind") not in KINDS:
        raise ValueError("a trigger is a schedule or a git push")
    old = old if old is not None and old.get("kind") == t["kind"] else None
    out = {"id": (old or {}).get("id") or _id(t.get("id")), "kind": t["kind"]}
    if t["kind"] == "schedule":
        if t.get("every") not in (None, ""):
            every = int(t["every"])
            if not MIN_EVERY <= every <= MAX_EVERY:
                raise ValueError(f"every is in minutes, {MIN_EVERY} at least and a month at most")
            out["every"] = every
        elif t.get("at"):
            if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(t["at"])):
                raise ValueError("at is a time of day, HH:MM")
            days = sorted({int(d) for d in (t.get("days") if t.get("days") is not None else DAYS) if int(d) in DAYS})
            if not days:
                raise ValueError("at needs a day to be on")
            h, m = str(t["at"]).split(":")
            out["at"], out["days"] = "%02d:%s" % (int(h), m), days
        else:
            raise ValueError("a schedule runs every so many minutes (every), or at a time of day (at)")
        same = old is not None and all(old.get(k) == out.get(k) for k in ("every", "at", "days"))
        out["next"] = old["next"] if same and old.get("next") else next_run(out, time.time())
    else:
        repo = os.path.expanduser(str(t.get("repo") or "").strip())
        if not repo or not os.path.isabs(repo):
            raise ValueError("a git push trigger needs a repo: a folder on the host")
        branch = str(t.get("branch") or "main").strip()
        if not _BRANCH.fullmatch(branch) or ".." in branch:
            raise ValueError("not a branch's name")
        out["repo"], out["branch"] = repo, branch
        out["poll_s"] = max(POLL_MIN, min(POLL_MAX, int(t.get("poll_s") or POLL_S)))
        if old is not None and old.get("repo") == repo and old.get("branch") == branch and old.get("seen"):
            out["seen"] = old["seen"]
    fills = t.get("fills") if isinstance(t.get("fills"), dict) else {}
    fills = {k: str(v)[:80] for k, v in fills.items() if k in fields and isinstance(v, str) and v}
    if fills:
        out["fills"] = fills
    if old is not None and old.get("fired"):
        out["fired"] = old["fired"]
    return out


def clean(task, old=None):
    """(task as it's kept, its script if one was given) for a task as it's given (old: the same one as it was), or
    ValueError saying what's wrong with it."""
    if not isinstance(task, dict):
        raise ValueError("a task is {name, runtime, script}")
    runtime = task.get("runtime") or (old or {}).get("runtime") or "python"
    if runtime not in RUNTIMES:
        raise ValueError("a task's runtime is python, shell or bot")
    script = task.get("script")
    if script is not None:
        if not isinstance(script, str) or not script.strip():
            raise ValueError("a task needs a script")
        if len(script.encode()) > SCRIPT_MAX:
            raise ValueError("its script is too long")
    elif old is None:
        raise ValueError("a task needs a script")
    form = [clean_field(f) for f in (task.get("form") or [])[:FIELDS_MAX]]
    if len({f["name"] for f in form}) != len(form):
        raise ValueError("two form fields have one name")
    olds = {t["id"]: t for t in (old or {}).get("triggers") or []}
    triggers = [clean_trigger(t, olds.get(t.get("id")) if isinstance(t, dict) else None, {f["name"] for f in form})
                for t in (task.get("triggers") or [])[:TRIGGERS_MAX]]
    variables = taskvars.clean_variables(task["variables"]) if task.get("variables") is not None else \
        list((old or {}).get("variables") or [])
    bindings = taskvars.clean_bindings(task["bindings"] if task.get("bindings") is not None else
                                       (old or {}).get("bindings"), variables)
    name = " ".join(str(task.get("name") or "").split())[:80] or (
        (script or "").strip().splitlines()[0][:40] if script else (old or {}).get("name")) or "Task"
    out = {"id": (old or {}).get("id") or _id(task.get("id")), "name": name, "runtime": runtime,
           "version": version_of(script) if script is not None else old["version"], "form": form,
           "triggers": triggers, "variables": variables, "bindings": bindings,
           "notify_failure": bool(task["notify_failure"] if "notify_failure" in task else
                                  task["fallback"] if "fallback" in task else
                                  (old or {}).get("notify_failure", True)),  # (fallback: an older head's)
           "notify_exit": bool(task.get("notify_exit", (old or {}).get("notify_exit", False))),
           "timeout_s": max(10, min(TIMEOUT_MAX, int(task.get("timeout_s") or (old or {}).get("timeout_s")
                                                     or TIMEOUT_S))),
           "paused": bool(task.get("paused"))}
    if old is not None and old.get("paused") and not out["paused"]:  # (woken: its schedules from now)
        for t in out["triggers"]:
            if t["kind"] == "schedule":
                t["next"] = next_run(t, time.time())
    return out, script


def fill(task, payload=None, fills=None, given=None):
    """(inputs, what's missing) for a run of task: each field from given (by hand), else from the trigger's payload
    (by fills, or its own name), else its default; a required one with nothing, missing. ValueError: one given that
    isn't as its field takes."""
    payload, fills, given = payload or {}, fills or {}, given or {}
    out, missing = {}, []
    for f in task.get("form") or []:
        name = f["name"]
        if name in given:
            out[name] = coerce(f, given[name])
        elif fills.get(name) in payload:
            out[name] = coerce(f, payload[fills[name]])
        elif name in payload:
            out[name] = coerce(f, payload[name])
        elif "default" in f:
            out[name] = f["default"]
        elif f.get("required"):
            missing.append(f.get("label") or name)
        else:
            out[name] = False if f["type"] == "bool" else ""
    return out, missing


def answered(got, form):
    """(values, None) from an answer to an ask with a form (the answer_task tool's answer: an object of its fields, or
    that as JSON), or (None, what's wrong)."""
    if isinstance(got, str):
        try:
            got = json.loads(got)
        except ValueError:
            pass
    if not isinstance(got, dict):
        return None, "the answer isn't an object of the form's fields"
    values, wrong = {}, []
    for f in form:
        if f["name"] not in got:
            if f.get("required"):
                wrong.append(f"{f['name']} is missing")
            continue
        try:
            values[f["name"]] = coerce(f, got[f["name"]])
        except ValueError as e:
            wrong.append(str(e))
            continue
        if f.get("required") and values[f["name"]] == "":
            wrong.append(f"{f['name']} is required: it can't be empty")
    wrong += [f"{k} isn't one of the form's fields" for k in got if k not in {f["name"] for f in form}]
    return (None, "; ".join(wrong)) if wrong else (values, None)


def in_reply(text, form):
    """answered for a Bot with no answer_task tool (its agent has no mesh tools): the JSON object in what it said."""
    start, end = (text or "").find("{"), (text or "").rfind("}")
    try:
        got = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        got = None
    return answered(got, form) if isinstance(got, dict) else (None, "the answer has no JSON object")


def _field_said(f):
    kind = {"choice": "one of " + ", ".join(json.dumps(c) for c in f.get("choices", ())), "number": "a number",
            "bool": "true or false", "text": "text"}[f["type"]]
    return f"- {f['name']}: {kind}" + (f" ({f['label']})" if f.get("label") else "") + \
        (" (required)" if f.get("required") else "")


def form_said(form, tool=True):
    """How an ask's to be answered, said to its Bot: with its answer_task tool, and the form's fields (or text); not
    tool (a Bot whose agent has no mesh tools): a JSON object of the form's fields in its reply."""
    if not tool:
        return wording.say("tasks.answer_json") + "\n" + "\n".join(_field_said(f) for f in form)
    if not form:
        return wording.say("tasks.answer_with_text")
    return wording.say("tasks.answer_with") + "\n" + "\n".join(_field_said(f) for f in form)


def from_routine(r):
    """(a task, its script) for a routine as it was kept (a Bot's prompt on a schedule): its id the routine's (or, if
    that's no id, one made from it), so one moved over again is the same task."""
    tid = r["id"] if wire.is_id(r.get("id")) else \
        hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()[:12]
    trigger = {"id": tid[:10] + "-s", "kind": "schedule", "next": r.get("next")}
    if r.get("every"):
        trigger["every"] = r["every"]
    else:
        trigger["at"], trigger["days"] = r.get("at") or "09:00", r.get("days") or list(DAYS)
    if trigger["next"] is None:
        trigger["next"] = next_run(trigger, time.time())
    prompt = str(r.get("prompt") or "")
    return {"id": tid, "name": r.get("name") or "Task", "runtime": "bot", "version": version_of(prompt),
            "form": [], "triggers": [trigger], "notify_failure": False, "notify_exit": False, "timeout_s": TIMEOUT_S,
            "paused": bool(r.get("paused"))}, prompt


def git(repo, *args, timeout=10):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if p.returncode:
        raise OSError((p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout).strip()
                      else f"git {args[0]} failed")
    return p.stdout.strip()


def pushed(t):
    """(a git push trigger's) (the branch's head now, its payload if it's moved since it was seen, else None)."""
    head = git(t["repo"], "rev-parse", "--verify", "--quiet", f"refs/heads/{t['branch']}^{{commit}}")
    if not t.get("seen") or head == t["seen"]:
        return head, None
    said = git(t["repo"], "log", "-1", "--format=%an%x00%s", head).split("\0", 1)
    return head, {"repo": t["repo"], "branch": t["branch"], "before": t["seen"], "after": head, "commit": head,
                  "author": said[0], "message": said[1] if len(said) > 1 else ""}


def runner(task):
    """What a python or shell task's script is run with (its path after it), else []: a bot task's is a turn."""
    return {"python": [sys.executable, "-u"], "shell": ["/bin/sh"]}.get(task["runtime"], [])


def command(task, path):
    """What a run of task runs, its script at path, as a command line (None: a bot task's, given its Bot as a turn)."""
    return shlex.join([*runner(task), path]) if runner(task) else None


def asked_head(name, tid, run, error):
    """What a message to a run's Bot starts with: the task (name) failed, finished (error "done") or asks, then a
    table of the run (its task's id tid, its record run): its ids, when it started (and ended), how long it's taken,
    the version, the command it runs, its exit code; a row left out that isn't known (an ask's run hasn't ended,
    nor has a failure it hands over itself)."""
    def at(t):
        zone = datetime.timezone(datetime.timedelta(seconds=run.get("offset") or 0))
        return datetime.datetime.fromtimestamp(t, zone).strftime("%Y-%m-%d %H:%M:%S")
    started, ended = run.get("started"), run.get("ended")
    seconds = run.get("seconds") if ended else time.time() - started if started else None
    rows = [("task", tid), ("run", run.get("id")), ("started", started and at(started)),
            ("ended", ended and at(ended)),
            ("duration", seconds is not None and wording.say("tasks.asked_seconds", seconds=round(seconds, 1))),
            ("version", run.get("version")), ("command", run.get("command")), ("exit", run.get("exit"))]
    rows = [(wording.say(f"tasks.asked_{k}"), str(v).replace("|", "\\|").replace("\n", " "))
            for k, v in rows if v is not None and v is not False and v != ""]
    lines = [f"| {k} | {v} |" for k, v in rows]
    kind = "done" if error == "done" else "error" if error else "ask"
    return "\n".join([wording.say("tasks.asked", name=name, error=kind), "", lines[0], "|---|---|", *lines[1:]])


def reap(run, folder):
    """A run's script's process group, left running by a host that was killed as it ran: killed, if it's still that
    run's (one of its processes runs the run's script)."""
    pgid, mark = run.get("pgid"), os.path.join(folder, "scripts", f"{run.get('version')}.")
    if not isinstance(pgid, int) or pgid <= 1:
        return
    try:
        listed = subprocess.run(["ps", "-A", "-o", "pgid=,command="], capture_output=True, text=True,
                                timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return
    if any(line.split(None, 1)[0] == str(pgid) and mark in line for line in listed.splitlines() if line.strip()):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


class Store:
    """One Bot's tasks, as they're kept in its folder (folder: <bot_dir>/tasks)."""

    def __init__(self, folder):
        from baudelib.serverstate import _read_json
        self.folder = folder
        kept = _read_json(os.path.join(folder, "tasks.json"))
        self.tasks = [t for t in (kept or {}).get("tasks") or [] if isinstance(t, dict) and t.get("id")]
        for t in self.tasks:  # (kept before there were two switches: its one, now "notify on failure"; the other off)
            if "fallback" in t:
                t["notify_failure"] = bool(t.pop("fallback"))
            t.setdefault("notify_failure", True)
            t.setdefault("notify_exit", False)
        self.runs = {}
        runs = os.path.join(folder, "runs")
        for n in sorted(os.listdir(runs)) if os.path.isdir(runs) else ():
            if n.endswith(".json") and isinstance(r := _read_json(os.path.join(runs, n)), dict) and r.get("id"):
                if r.get("status") in ("running", "asking"):  # (its host stopped as it ran: what it left, stopped)
                    reap(r, folder)
                    r.update(status="stopped", ended=r.get("ended") or r["started"],
                             error=r.get("error") or wording.say("tasks.host_stopped"))
                r.pop("dry", None)  # (kept before runs had no such field)
                self.runs[r["id"]] = r

    def save(self):
        from baudelib.serverstate import _write_json
        _write_json(os.path.join(self.folder, "tasks.json"), {"tasks": self.tasks})

    def find(self, tid):
        return next((t for t in self.tasks if t["id"] == tid), None)

    def script_path(self, task):
        return os.path.join(self.folder, "scripts", f"{task['version']}.{EXT[task['runtime']]}")

    def put_script(self, task, script):
        path = self.script_path(task)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                f.write(script)
            os.replace(path + ".tmp", path)

    def script(self, task):
        try:
            with open(self.script_path(task), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def latest(self, tid=None, limit=RUNS_SHOWN):
        runs = sorted((r for r in self.runs.values() if tid is None or r["task"] == tid),
                      key=lambda r: r["started"], reverse=True)
        return runs[:limit]

    def put_run(self, run):
        from baudelib.serverstate import _write_json
        self.runs[run["id"]] = run
        _write_json(os.path.join(self.folder, "runs", f"{run['id']}.json"), run)
        mine = [r for r in self.runs.values() if r["task"] == run["task"]]
        if len(mine) > RUNS_KEPT:  # (the task's oldest that are over, gone)
            self.drop([r for r in sorted(mine, key=lambda r: r["started"])[:len(mine) - RUNS_KEPT]
                       if r["status"] not in ("running", "asking")])

    def drop(self, runs):
        for old in runs:
            self.runs.pop(old["id"], None)
            for ext in ("json", "log"):
                try:
                    os.unlink(os.path.join(self.folder, "runs", f"{old['id']}.{ext}"))
                except OSError:
                    pass

    def forget(self, tid):
        """A deleted task's runs (but those going) and the scripts nothing kept now ran or runs, gone."""
        self.drop([r for r in list(self.runs.values())
                   if r["task"] == tid and r["status"] not in ("running", "asking")])
        used = {f"{t['version']}.{EXT[t['runtime']]}" for t in self.tasks} | \
            {r.get("version") for r in self.runs.values()}
        folder = os.path.join(self.folder, "scripts")
        for n in os.listdir(folder) if os.path.isdir(folder) else ():
            if n not in used and n.split(".")[0] not in used:
                try:
                    os.unlink(os.path.join(folder, n))
                except OSError:
                    pass

    def log(self, rid, kind, text, **more):
        path = os.path.join(self.folder, "runs", f"{rid}.log")
        try:
            if os.path.getsize(path) > LOG_MAX:
                return
        except OSError:
            pass
        line = {"at": round(time.time(), 3), "kind": kind, "text": str(text)[:20000],
                **{k: v for k, v in more.items() if v is not None}}
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def read_log(self, rid, limit=LOG_SHOWN):
        try:
            with open(os.path.join(self.folder, "runs", f"{rid}.log"), encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


class Tasks:
    """Every Bot's tasks on a host (server: its Server): read and set over the wire, run as they're asked for or due,
    their scripts' asks carried to their Bots and answered with their answer_task tools."""

    def __init__(self, server):
        self.server = server
        self.lock = threading.RLock()  # (taken after the server's lock, never before)
        self.stores = {}  # (a Bot's session id: its Store)
        self.running = {}  # (a run's id: {sid, task, proc, token, stop})
        self.waiting = {}  # (a Bot's session id: the ask sent it, waiting on its answer, or its turn's end)
        self.polled = {}  # ((sid, trigger id): when it was last looked at)
        self.turns = {}  # (a Bot's session id: held while one ask of it waits on its turn: one at a time)
        self.notices = {}  # (a Bot's session id: {task id: its finished run's notice, till sent: deliver})
        self.tellers = set()  # (the chats a run started whose reports are being given their Bots: _tell)
        self.lists = taskvars.Lists(server)  # (what's here of each type a variable's of: its choices, its warnings)
        self.warned = {}  # ((a Bot's tasks' folder, a task's id): {a variable's name: its warning}, as last looked at)
        self.checked = None  # (when every task's bound variables were last looked at: monotonic; None, never)

    # ---- kept
    def store(self, s):
        """(server's lock held) s's Store, its routines moved over first if it has any."""
        from baudelib.serverstate import bot_dir
        with self.lock:
            st = self.stores.get(s.id)
            if st is None:
                st = self.stores[s.id] = Store(os.path.join(bot_dir(self.server.state_dir, s.id), "tasks"))
            if s.meta.get("routines"):
                for r in s.meta["routines"]:
                    if not isinstance(r, dict):
                        continue
                    task, prompt = from_routine(r)
                    if st.find(task["id"]) is None and len(st.tasks) < TASKS_MAX:  # (moved before, the host killed
                        st.put_script(task, prompt)                               # before they were dropped: once)
                        st.tasks.append(task)
                st.save()
                s.meta.pop("routines", None)
                self.server.save(s)
                self.server.log(f"routines of {s.id} moved to tasks")
            return st

    def shown(self, st, task, script=False):
        """A task as the wire has it: its latest run, its next schedule (and its script)."""
        last = st.latest(task["id"], 1)
        nexts = [t["next"] for t in task["triggers"] if t["kind"] == "schedule" and t.get("next")]
        warned = self.warned.get((st.folder, task["id"])) or {}
        warned = {k: w for k, w in warned.items() if k in (task.get("bindings") or {})}
        return {**task, "fallback": task.get("notify_failure"),  # (what an older head's switch reads)
                **({"warnings": warned} if warned else {}),
                "last": last[0] if last else None, "next": None if task.get("paused") or not nexts
                else min(nexts), **({"script": st.script(task)} if script else {})}

    def listing(self, s, saved=None):
        st = self.store(s)
        with self.lock:
            return {"session": s.id, "tasks": [self.shown(st, t) for t in st.tasks], "clock": clock(),
                    **({"saved": saved} if saved else {})}

    def op(self, s, msg, by=None):
        """(server's lock held) tasks, task, run_task, task_run, task_stop for the Bot s (by: the member asking, as a
        chat's started_by has it, or None: a head here): (the answer's op, its fields), or ValueError, what's wrong."""
        op = msg["op"]
        if not s.meta.get("bot"):
            raise ValueError(wording.say("host_errors.tasks_bot_only"))
        st = self.store(s)
        if op == "tasks":
            saved = None
            if "set" in msg or "delete" in msg:
                with self.lock:
                    if "delete" in msg:  # (its runs going stopped; its runs and scripts gone)
                        for rid, held in list(self.running.items()):
                            if held["sid"] == s.id and held["task"] == msg["delete"]:
                                self.stop(rid, wording.say("task_notes.stopped"))
                        st.tasks = [t for t in st.tasks if t["id"] != msg["delete"]]
                        st.forget(msg["delete"])
                    else:
                        given = msg["set"] if isinstance(msg["set"], dict) else None
                        old = st.find(given.get("id")) if given else None
                        if given is None or old is None and len(st.tasks) >= TASKS_MAX:
                            raise ValueError(wording.say("host_errors.task_limit"))
                        task, script = clean(given, old)
                        if script is not None:
                            st.put_script(task, script)
                        st.tasks = [task if t["id"] == task["id"] else t for t in st.tasks] if old else \
                            st.tasks + [task]
                        saved = task["id"]
                    st.save()
                self.server.changed()
                self.server.woken.set()  # (a schedule may be sooner, a branch to look at: the watch told)
            return "tasks", self.listing(s, saved)
        if op == "task":
            with self.lock:
                task = st.find(msg.get("id"))
                if task is None:
                    raise ValueError(wording.say("host_errors.no_task"))
                return "task", {"session": s.id, "task": self.shown(st, task, script=bool(msg.get("script"))),
                                "runs": st.latest(task["id"]), "clock": clock()}
        if op == "run_task":
            with self.lock:
                task = st.find(msg.get("id"))
                if task is None or s.meta.get("archived"):
                    raise ValueError(wording.say("host_errors.no_task"))
            inputs = msg.get("inputs") if isinstance(msg.get("inputs"), dict) else {}
            run = self.start(s, task, None, "manual", {}, inputs, by=by, waited=bool(msg.get("waited")))
            return "task_started", {"session": s.id, "run": run}
        with self.lock:
            run = st.runs.get(msg.get("run"))
            if run is None:
                raise ValueError(wording.say("host_errors.no_run"))
            if op == "task_stop":
                self.stop(run["id"], wording.say("task_notes.stopped"))
            return "task_run", {"session": s.id, "run": dict(st.runs[run["id"]]), "log": st.read_log(run["id"]),
                                "bot": (s.meta.get("bot") or {}).get("name")}

    # ---- its variables
    def bind(self, s, msg):
        """task_bind for the Bot s: one of a task's variables bound (or not), and looked at at once for its warning
        (not under the server's lock: kubectl, docker). (the answer's op, its fields), or ValueError."""
        with self.server.lock:
            if not s.meta.get("bot"):
                raise ValueError(wording.say("host_errors.tasks_bot_only"))
            st = self.store(s)
            with self.lock:
                task = st.find(msg.get("id"))
                if task is None:
                    raise ValueError(wording.say("host_errors.no_task"))
                if not any(v["name"] == msg.get("name") for v in task.get("variables") or []):
                    raise ValueError(wording.say("tasks.no_variable"))
                bindings = dict(task.get("bindings") or {})
                b = taskvars.clean_binding(msg.get("binding"))
                if b is None:
                    bindings.pop(msg["name"], None)
                else:
                    bindings[msg["name"]] = b
                task["bindings"] = bindings
                st.save()
                task = dict(task)
            self.server.changed()
        self.look_at(st, task)
        with self.server.lock, self.lock:
            task = st.find(task["id"]) or task
            return "task", {"session": s.id, "task": self.shown(st, task), "runs": st.latest(task["id"]),
                            "clock": clock()}

    def choices(self, s, kind, allowed):
        """task_choices for the Bot s: what a variable of kind could be bound to here (allowed(capability): whether
        the one asking may read what it's of: a cluster's, Docker's, a database's). (op, fields), or ValueError."""
        if kind not in taskvars.TYPES:
            raise ValueError(wording.say("tasks.no_variable"))
        if not s.meta.get("bot"):
            raise ValueError(wording.say("host_errors.tasks_bot_only"))
        need = taskvars.NEEDS.get(kind)
        got = taskvars.choices(self.lists, kind) if need is None or allowed(need) else []
        return "task_choices", {"session": s.id, "type": kind, "choices": got}

    def look_at(self, st, task):
        """task's bound variables looked at now (not under the server's lock): their warnings, kept (warned) and
        given back; heads told if they changed."""
        said = taskvars.warnings(self.server, self.lists, task)
        with self.lock:
            changed = self.warned.get((st.folder, task["id"]), {}) != said
            self.warned[(st.folder, task["id"])] = said
        if changed:
            with self.server.lock:
                self.server.changed()
        return said

    def check_all(self):
        """Every Bot's tasks' bound variables looked at, for their warnings (on a thread of its own: due's, every
        CHECK_S)."""
        with self.server.lock:
            stores = [self.store(s) for s in list(self.server.sessions.values())
                      if s.meta.get("bot") and not s.meta.get("archived") and not s.deleting]
            with self.lock:
                tasks = [(st, dict(t)) for st in stores for t in st.tasks if t.get("bindings")]
        for st, task in tasks:
            if self.server.stopped.is_set():
                return
            try:
                self.look_at(st, task)
            except Exception as e:  # noqa: BLE001 (said, and looked at again next time)
                self.server.log(f"task {task['name']!r}: its variables not looked at: {e}")

    # ---- run
    def start(self, s, task, trigger, kind, payload, given=None, by=None, waited=False):
        """(server's lock held) A run of task begun (or, one of it running already, kept as skipped): its record.
        by: the member that ran it by hand (None: a head here, or the Bot's own tools on this host; a trigger's: the
        Bot's own starter's): what the chats it starts are judged as.
        ValueError: its form can't be filled."""
        st = self.store(s)
        fills = (trigger or {}).get("fills")
        inputs, missing = fill(task, payload, fills, given)
        if missing:
            raise ValueError(wording.say("tasks.missing", fields=", ".join(missing)))
        now = time.time()
        run = {"id": uuid.uuid4().hex[:16], "task": task["id"], "name": task["name"], "version": task["version"],
               "trigger": (trigger or {}).get("id"), "kind": kind, "payload": payload or None, "inputs": inputs,
               "status": "running", "started": now, "offset": clock()["offset"], "asks": 0,
               "command": command(task, st.script_path(task))}
        with self.lock:
            if self.going(s.id, task["id"]):
                last = (st.latest(task["id"], 1) or [None])[0]
                if last and last["status"] == "skipped" and last.get("kind") == kind and \
                        last.get("trigger") == run["trigger"]:  # (fired again while the same run goes, as one
                    n = (last.get("skips") or 1) + 1                 # waiting for input may for days: one record)
                    return self._update(st, last["id"], skips=n, ended=now,
                                        error=wording.say("tasks.still_running_n", n=n))
                run.update(status="skipped", ended=now, seconds=0, error=wording.say("tasks.still_running"))
                st.put_run(run)
                return run
            st.put_run(run)
            token = secrets.token_hex(16)
            self.running[run["id"]] = {"sid": s.id, "task": task["id"], "name": task["name"], "proc": None,
                                       "token": token, "stop": None,
                                       "by": by if kind == "manual" else s.meta.get("started_by"), "waited": waited}
        threading.Thread(target=self._run, args=(s.id, st, dict(task), run, token), daemon=True,
                         name=f"task-{run['id']}").start()
        self.server.changed()
        return dict(run)

    def going(self, sid, tid):
        with self.lock:
            return any(r["sid"] == sid and r["task"] == tid for r in self.running.values())

    def _cwd(self, sid, st):
        """Where a Bot's run runs: its folder (its worktree made again first, if it's gone), else its tasks'."""
        self.server.restore_folder(None, sid)
        with self.server.lock:
            s = self.server.sessions.get(sid)
            cwd = s.meta.get("cwd") if s is not None else None
        return cwd if isinstance(cwd, str) and os.path.isdir(cwd) else st.folder

    def _run(self, sid, st, task, run, token):
        error, code = None, None
        try:
            said = self.warn(st, task, run)
            if task["runtime"] == "bot":
                self._cwd(sid, st)
                text, failed, _values = self.ask(run["id"], st.script(task), task["name"])
                error = wording.say("tasks.bot_failed") if failed else None
            else:
                code, error = self._script(sid, st, task, run, token, self._cwd(sid, st), said)
        except Exception as e:  # noqa: BLE001 (said in its run, and on the page)
            error = f"{type(e).__name__}: {e}"
        with self.lock:
            held = self.running.get(run["id"]) or {}
            stopped = held.get("stop")
        hand = bool(error and not stopped and task.get("notify_failure") and task["runtime"] != "bot" and
                    not st.runs[run["id"]].get("handed"))
        told = bool(not error and not stopped and task.get("notify_exit") and task["runtime"] != "bot" and
                    not held.get("waited"))
        ended = time.time()
        status = "stopped" if stopped else "failed" if error else "done"
        self._update(st, run["id"], status=status, ended=ended, seconds=round(ended - run["started"], 3), exit=code,
                     error=stopped or error, handed=hand or None)
        with self.lock:  # (over before it's handed on: the Bot may run it again as it's handed it)
            self.running.pop(run["id"], None)
            for key in [k for k in self.polled if k[0] == sid]:  # (its branches looked at again now: a push
                self.polled.pop(key)                               # while it ran, run after it)
        self.server.log(f"task {task['name']!r} of {sid}: {status}" + (f" ({error})" if error else ""))
        self.server.changed()
        self.server.woken.set()
        if hand:
            tail = "\n".join(f"{x['kind']}: {x['text']}" for x in st.read_log(run["id"], 40))
            try:
                self.ask(run["id"], wording.say("tasks.handed", error=error, log=tail), task["name"], error=True,
                         held={"sid": sid, "task": task["id"], "stop": None})
            except Exception as e:  # noqa: BLE001
                st.log(run["id"], "status", f"{type(e).__name__}: {e}")
        elif told:
            self.notify(sid, task, run["id"])

    def warn(self, st, task, run):
        """A run's task's bound variables looked at as it starts: each that isn't there now a warning in its log (the
        run goes on all the same: its script's to decide). {name: warning}."""
        if not task.get("bindings"):
            return {}
        try:
            said = self.look_at(st, task)
        except Exception as e:  # noqa: BLE001 (not looked at: said, and run as it is)
            st.log(run["id"], "status", f"{type(e).__name__}: {e}")
            return {}
        for name, w in said.items():
            st.log(run["id"], "warning", wording.say("tasks.variable_warning", name=name, warning=w), variable=name)
        return said

    def finished_text(self, st, task, rid):
        """What a run that finished says to its Bot: the head and table, that nothing's asked, then its last say(), else
        the end of its output (nothing, if it printed none)."""
        log = [x for x in st.read_log(rid, 200) if x["kind"] in ("say", "out", "err")]
        said = [x for x in log if x["kind"] == "say"]
        end = ""
        if said:
            end = wording.say("tasks.finished_said", text=said[-1]["text"][-ENDING_MAX:])
        elif log:
            end = wording.say("tasks.finished_output",
                              text="\n".join(x["text"] for x in log[-LOG_ENDING:])[-ENDING_MAX:])
        return "\n\n".join(p for p in (asked_head(task["name"], task["id"], st.runs[rid], "done"),
                                        wording.say("tasks.finished"), end) if p)

    def notify(self, sid, task, rid):
        """A finished run's Bot told (not an ask: nothing waited for, no turn held): kept as the Bot's one notice of the
        task (a newer run's replaces it) till the Bot's idle, then sent by deliver."""
        with self.lock:
            st = self.stores.get(sid)
            if st is None or rid not in st.runs:
                return
            self.notices.setdefault(sid, {})[task["id"]] = {"run": rid, "task": task["id"], "name": task["name"],
                                                           "text": self.finished_text(st, task, rid)}
        self.server.woken.set()
        self.deliver()

    def deliver(self):
        """Each Bot's pending notices sent, one at a time, to a Bot that's idle with no ask of its tasks waiting on it
        (the host's watch comes by again for the rest). One it can't take: said on its run, and let go of."""
        srv = self.server
        with srv.lock:
            for sid in list(self.notices):
                with self.lock:
                    pending = self.notices.get(sid) or {}
                s = srv.sessions.get(sid)
                if not pending or s is None or s.deleting or s.meta.get("archived"):
                    with self.lock:
                        self.notices.pop(sid, None)
                    continue
                if s.busy or s.codex_queue or self.asking(sid):
                    continue
                with self.lock:
                    n = pending.pop(next(iter(pending)))
                    if not pending:
                        self.notices.pop(sid, None)
                try:
                    srv.take_input(None, s, {"type": "user", "uuid": str(uuid.uuid4()),
                                             "message": {"role": "user", "content": n["text"]}},
                                   {"kind": "host", "name": wording.say("tasks.sender", name=n["name"]),
                                    "host": srv.me["id"], "host_name": srv.me["name"], "session": sid,
                                    "task": n["task"], "run": n["run"]})
                except Exception as e:  # noqa: BLE001 (Elsewhere, or its agent not started)
                    srv.log(f"telling {sid} a task finished: {e}")
                    st = self.stores.get(sid)
                    if st is not None and n["run"] in st.runs:
                        st.log(n["run"], "status", str(e))

    def _update(self, st, rid, **fields):
        with self.lock:
            run = {**st.runs[rid], **{k: v for k, v in fields.items() if v is not None or k == "exit"}}
            st.put_run(run)
            return run

    def _script(self, sid, st, task, run, token, cwd, said=None):
        """A python or shell task's script run (its Bot's, sid: the chats it starts nested under it, BAUDE_SPAWNED_BY):
        (its exit code, why it failed or None). Stopped or out of time (its timeout_s, the time it waited for input
        not counted): its process group told to stop, and killed after GRACE_S whether or not its script's gone."""
        path = st.script_path(task)
        argv = [*runner(task), path]
        env = {**os.environ, "BAUDE_SOCKET": os.path.abspath(self.server.socket_path), "BAUDE_TASK_RUN": run["id"],
               "BAUDE_SPAWNED_BY": sid, "BAUDE_TASK_BOT": json.dumps(self.bot_of({"run": run["id"], "token": token})),
               "BAUDE_TASK_TOKEN": token, "BAUDE_TASK_INPUTS": json.dumps(run["inputs"]),
               "BAUDE_TASK_PAYLOAD": json.dumps(run["payload"] or {}), "BAUDE_TASK_NAME": task["name"],
               "BAUDE_WIRE_VERSION": str(wire.VERSION),
               "BAUDE_TASK_VARS": json.dumps(taskvars.for_script(task, said or {})),
               "PYTHONPATH": os.pathsep.join(p for p in (SCRIPT_DIR, os.environ.get("PYTHONPATH")) if p)}
        for k, v in run["inputs"].items():
            env[f"BAUDE_IN_{k.upper()}"] = v if isinstance(v, str) else json.dumps(v)
        with self.lock:
            held = self.running.get(run["id"])
            if held is None or held.get("stop"):
                return None, None
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
            held["proc"] = p
        self._update(st, run["id"], pid=p.pid, pgid=p.pid)

        def read(pipe, kind):
            for line in iter(pipe.readline, b""):
                st.log(run["id"], kind, line.decode("utf-8", "replace").rstrip("\n"))
        readers = [threading.Thread(target=read, args=(p.stdout, "out"), daemon=True),
                   threading.Thread(target=read, args=(p.stderr, "err"), daemon=True)]
        for t in readers:
            t.start()
        begun, code, timed_out = time.monotonic(), None, False
        while code is None and not held.get("stop"):
            try:
                code = p.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                if time.monotonic() - begun - self.input_s(held) > task["timeout_s"]:
                    timed_out = True
                    break
        if timed_out or held.get("stop"):  # (its script gone already or not: its group's what's killed)
            self._kill(p)
            code = p.wait()
            error = wording.say("tasks.timed_out", seconds=task["timeout_s"]) if timed_out else None
        else:
            error = None if code == 0 else wording.say("tasks.exited", code=code)
        for t in readers:  # (stopped: every process that held its output killed, so they end)
            t.join(timeout=2 * GRACE_S if timed_out or held.get("stop") else 5)
        return code, error

    @staticmethod
    def _kill(p):
        """A script's process group told to stop, then, GRACE_S on, killed (its script gone by then or not)."""
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            p.wait(timeout=GRACE_S)
        except subprocess.TimeoutExpired:
            pass
        end = time.monotonic() + GRACE_S
        while time.monotonic() < end:  # (its script gone: the rest of its group given what's left of the time)
            try:
                os.killpg(p.pid, 0)
            except OSError:
                return
            time.sleep(0.1)
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass

    def stop(self, rid, why):
        """A run stopped (why: said in it): its script told to stop at once (killed by its run's thread), an ask it
        waits on, or waits to make, let go of."""
        with self.lock:
            held = self.running.get(rid)
            if held is None:
                return
            held["stop"] = why
            p = held["proc"]
            for w in self.waiting.get(held["sid"], []):
                if w["run"] == rid:
                    w["text"], w["failed"] = why, True
                    w["done"].set()
        if p is not None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                pass

    def stop_all(self):
        """Every run stopped: the host's going."""
        with self.lock:
            rids = list(self.running)
        for rid in rids:
            self.stop(rid, wording.say("tasks.host_stopped"))

    def busy(self):
        """[{session, what}] for each run going (what a restart waits for)."""
        with self.lock:
            return [{"session": r["sid"], "what": wording.say("tasks.waiting_input" if r.get("asking") else
                                                              "tasks.task_running")} for r in self.running.values()]

    def blocking(self):
        """Each run going, as a pending restart lists it (RestartBlocker): [{kind, session, task, run, name, what,
        waiting}], waiting: one waiting for input (an ask of its Bot), which a restart would stop."""
        with self.lock:
            return [{"kind": "task", "session": r["sid"], "task": r["task"], "run": rid, "name": r.get("name") or "",
                     "what": wording.say("tasks.waiting_input" if r.get("asking") else "tasks.task_running"),
                     **({"waiting": True} if r.get("asking") else {})} for rid, r in self.running.items()]

    # ---- a script's
    def held(self, msg):
        """(the run a script names, as it's held, its store) if its token is right, else None."""
        with self.lock:
            held = self.running.get(msg.get("run"))
            if held is None or not secrets.compare_digest(str(msg.get("token") or ""), held["token"]):
                return None
            return held, self.stores.get(held["sid"])

    def bot_of(self, given):
        """The Bot ({id, name}) whose run given ({run, token}: a task's script's hello) names, if it's going here and
        its token's right; else None."""
        got = self.held(given) if isinstance(given, dict) else None
        if got is None:
            return None
        sid = got[0]["sid"]
        with self.server.lock:
            s = self.server.sessions.get(sid)
            bot = (s.meta.get("bot") or {}) if s is not None else {}
        return {"id": sid, "name": bot.get("name") or "Bot"}

    def report(self, msg):
        got = self.held(msg)
        if got is None:
            raise ValueError(wording.say("host_errors.no_run"))
        got[1].log(msg["run"], "say", msg.get("say") or "")

    def script_ask(self, msg):
        """task_ask's answer (text, values), once its Bot's answered it with its answer_task tool: ValueError if it
        can't be (a failure handed over by a task whose failures aren't handed to its Bot, too)."""
        got = self.held(msg)
        if got is None:
            raise ValueError(wording.say("host_errors.no_run"))
        held, st = got
        form = [clean_field(f) for f in msg.get("form") or []] if isinstance(msg.get("form"), list) else []
        text = str(msg.get("text") or "")
        with self.lock:
            task = st.find(held["task"]) or {"name": "Task", "notify_failure": False}
        if msg.get("error"):
            if not task.get("notify_failure"):
                raise ValueError(wording.say("host_errors.task_no_fallback"))
            self._update(st, msg["run"], handed=True)  # (the script's handed its failure over: the host doesn't again)
        tool = self.has_tool(held["sid"])
        said, failed, values = self.ask(msg["run"], text, task["name"], form=form, error=bool(msg.get("error")),
                                        tool=tool)
        if failed:
            raise ValueError(said or wording.say("tasks.bot_failed"))
        if tool or not form:
            return said, values if form else None
        values, wrong = in_reply(said, form)  # (no tool: the JSON in what it said; once more, told what was wrong)
        if wrong:
            said, failed, _values = self.ask(msg["run"], wording.say("tasks.not_as_asked", wrong=wrong) + "\n\n" +
                                             form_said(form, tool=False), task["name"])
            values, wrong = (None, said) if failed else in_reply(said, form)
            if wrong:
                raise ValueError(wording.say("tasks.not_as_asked", wrong=wrong))
        st.log(msg["run"], "answer", json.dumps(values), values=values)
        return said, values

    def has_tool(self, sid):
        """Has the Bot sid's agent the answer_task tool (the mesh's tools: not an Apple model's)?"""
        from baudelib import models
        with self.server.lock:
            s = self.server.sessions.get(sid)
            return s is not None and models.model_program(s.meta.get("model")) in ("claude", "pi", "codex")

    def ask(self, rid, text, name, form=None, error=False, held=None, tool=False):
        """(what the Bot said, failed, values) for text given a run's Bot as a turn of its own, from its task (name);
        the ask and its answer in the run's log. tool: answered with its answer_task tool (answer: what that's given,
        text, or a form's values), as soon as it's called; a turn that ends without it reminded once, then failed. Not
        tool: what it said as that turn ends. held: an ended run's (its failure handed on), else the run's as it's
        going. One ask of a Bot at a time, each sent as it's idle: the turn it ends is that ask's, not one going on as
        it was sent (a user's, a stopped run's). A run going waits for input till it's answered (ASK_S at most):
        asking, its clock stopped (input_s)."""
        live = held is None
        with self.lock:
            held = self.running.get(rid) if live else held
            turn = self.turns.setdefault(held["sid"], threading.Lock()) if held else None
            st = self.stores.get(held["sid"]) if held else None
        if held is None:
            return wording.say("host_errors.no_run"), True, None
        if live:
            self._waiting(st, rid, held, True)
        try:
            end = time.monotonic() + ASK_S
            while not turn.acquire(timeout=0.25):  # (stopped meanwhile: let go of at once)
                if held.get("stop") or time.monotonic() > end:
                    return held.get("stop") or wording.say("tasks.no_answer"), True, None
            try:
                return self._ask(rid, held, text, name, form, error, live, end, tool)
            finally:
                turn.release()
        finally:
            if live:
                self._waiting(st, rid, held, False)

    def _waiting(self, st, rid, held, on):
        """A run going waiting for input (on: an ask of its Bot begun; asks of one run may overlap), or not any more
        (its last ask over): asking, its clock stopped meanwhile (input_s), or running again. Heads told."""
        with self.lock:
            n = held["asking"] = held.get("asking", 0) + (1 if on else -1)
            now = time.monotonic()
            if on and n == 1:
                held["asked_at"] = now
            elif not on and n == 0:
                held["input_s"] = held.get("input_s", 0.0) + now - held.pop("asked_at", now)
            if st is not None and rid in self.running and (n == 1 if on else n == 0) and \
                    (st.runs.get(rid) or {}).get("status") in ("running", "asking"):  # (not one that's over)
                self._update(st, rid, status="asking" if on else "running")
        self.server.changed()

    def input_s(self, held):
        """How long a run's waited for input, all told (its asks of its Bot): what its timeout_s doesn't count."""
        with self.lock:
            return held.get("input_s", 0.0) + (time.monotonic() - held["asked_at"] if held.get("asking") else 0.0)

    def asks_of(self, sid):
        """The messages (their uuids) the Bot sid's been sent by its tasks' runs going, each run waiting for input
        till the turn it began ends: TaskAsks, as its listing has them."""
        with self.lock:
            return [w["said"] for w in self.waiting.get(sid, []) if w.get("live") and w.get("said")]

    def _ask(self, rid, held, text, name, form, error, live, end, tool):
        with self.lock:
            st = self.stores.get(held["sid"])
            if live and rid not in self.running or st is None or held.get("stop"):
                return held.get("stop") or wording.say("host_errors.no_run"), True, None
        sid = held["sid"]
        st.log(rid, "ask", text, error=error or None)
        if live:
            self._update(st, rid, asks=(st.runs[rid].get("asks") or 0) + 1)
        wait = {"run": rid, "name": name, "done": threading.Event(), "text": None, "failed": False,
                "said": str(uuid.uuid4()), "live": live, "tool": tool, "form": form or [], "answered": False,
                "values": None, "refused": None}
        said = asked_head(name, held.get("task") or "", st.runs.get(rid) or {"id": rid}, error) + "\n\n" + \
            text + ("\n\n" + form_said(form) if tool else "\n\n" + form_said(form, tool=False) if form else "")
        reminded = False
        while True:
            failed = self._send(st, sid, rid, held, wait, said, name, end)
            if failed is not None:
                return failed[0], failed[1], None
            if not tool or wait["answered"] or wait["failed"] or reminded:
                break
            reminded = True  # (its turn ended without the tool: told once more, then it's failed)
            wait["done"].clear()
            said = wording.say("tasks.answer_reminder", run=rid) + "\n\n" + form_said(form)
            wait["said"] = str(uuid.uuid4())  # (a message of its own: its from line says the run waits on it)
        if tool and not wait["answered"] and not wait["failed"]:
            wait["text"], wait["failed"] = wording.say("tasks.answer_refused", wrong=wait["refused"]) \
                if wait["refused"] else wording.say("tasks.not_answered"), True
        if wait["failed"] or not form:
            st.log(rid, "answer", wait["text"] or "", error=wait["failed"] or None)
        elif tool:  # (not tool: its values, once script_ask's found them in what it said)
            st.log(rid, "answer", json.dumps(wait["values"]), values=wait["values"])
        return wait["text"] or "", wait["failed"], wait["values"]

    def _send(self, st, sid, rid, held, wait, said, name, end):
        """said sent the Bot sid once it's idle (wait waiting on it: its next turn's end, or its answer_task tool),
        and waited for: None, or (why, True) if it couldn't be sent."""
        srv, sent = self.server, False
        while not sent:  # (sent once its Bot's idle: its next turn's end is this one's)
            with srv.lock:
                s = srv.sessions.get(sid)
                if s is None or s.deleting or s.meta.get("archived"):
                    return wording.say("host_errors.no_task"), True
                if not s.busy and not s.codex_queue:
                    with self.lock:
                        self.waiting.setdefault(sid, []).append(wait)
                    try:
                        srv.take_input(None, s, {"type": "user", "uuid": wait["said"],
                                                 "message": {"role": "user", "content": said}},
                                       {"kind": "host", "name": wording.say("tasks.sender", name=name),
                                        "host": srv.me["id"], "host_name": srv.me["name"], "session": sid,
                                        "task": held.get("task") or "", "run": rid})
                    except Exception as e:  # noqa: BLE001 (Elsewhere, or its agent not started)
                        with self.lock:
                            self.waiting[sid].remove(wait)
                        st.log(rid, "answer", str(e))
                        return str(e), True
                    sent = True
                    continue
            if held.get("stop") or time.monotonic() > end:
                wait["text"], wait["failed"] = held.get("stop") or wording.say("tasks.no_answer"), True
                return None
            time.sleep(0.25)
        while not wait["done"].wait(0.25):  # (stopped meanwhile: let go of)
            if held.get("stop") or time.monotonic() > end:
                break
        with self.lock:
            if wait in self.waiting.get(sid, []):
                self.waiting[sid].remove(wait)
            if not wait["done"].is_set() and not wait["failed"]:
                wait["text"], wait["failed"] = held.get("stop") or wording.say("tasks.no_answer"), True
        return None

    def answer(self, s, msg):
        """(server's lock held) answer_task: the ask of the run msg names that's waiting on the Bot s, answered (an
        answer, the form's fields; or text, an ask with no form), its run going on at once: (the answer's op, its
        fields), or ValueError, what's wrong with it (said to the model, which may try again)."""
        rid = msg.get("run")
        with self.lock:
            wait = next((w for w in self.waiting.get(s.id, []) if w["run"] == rid and w["tool"]), None)
            if wait is None:
                raise ValueError(wording.say("host_errors.task_not_asking", run=str(rid)))
            values, text = None, ""
            if wait["form"]:
                values, wrong = answered(msg.get("answer"), wait["form"])
                if wrong:
                    wait["refused"] = wrong
                    raise ValueError(wording.say("tasks.not_as_asked", wrong=wrong) + "\n\n" + form_said(wait["form"]))
            else:
                text = msg.get("text") if isinstance(msg.get("text"), str) else ""
                if not text.strip():
                    wait["refused"] = wording.say("tasks.answer_no_text")
                    raise ValueError(wording.say("tasks.not_as_asked", wrong=wait["refused"]))
            wait.update(answered=True, values=values, text=text)
            self.waiting[s.id].remove(wait)
            wait["done"].set()
        return "task_answered", {"session": s.id, "run": rid, "task": wait["name"],
                                 **({"values": values} if values is not None else {"text": text})}

    def asking(self, sid):
        """Is an ask of one of the Bot sid's tasks waiting on it (sent, or to be sent once it's idle)?"""
        with self.lock:
            turn = self.turns.get(sid)
            return bool(self.waiting.get(sid)) or turn is not None and turn.locked()

    def agent_gone(self, s):
        """(server's lock held) s's agent ended with its tasks' asks sent it unanswered: each failed at once (its turn
        will never end), not left for the next turn's end to answer."""
        with self.lock:
            for w in self.waiting.pop(s.id, []):
                w["text"], w["failed"] = wording.say("tasks.agent_gone"), True
                w["done"].set()

    def turn_over(self, s, msg):
        """(server's lock held) s's turn ended (its result, msg): the ask it was sent for, answered with what it
        said; one to be answered with its answer_task tool and not yet: over, unanswered (or failed, its turn's)."""
        with self.lock:
            for w in self.waiting.pop(s.id, []):
                w["text"], w["failed"] = str(msg.get("result") or ""), bool(msg.get("is_error"))
                w["done"].set()

    # ---- the chats a run starts
    def from_run(self, given):
        """(server's lock held) The run going that a start names (its task_run: {run, token}, from the run's
        environment), as a chat it starts takes it: {sid, task, run, name, by}; ValueError if it names none going,
        or its token's wrong."""
        if not isinstance(given, dict):
            raise ValueError(wording.say("host_errors.no_run"))
        got = self.held(given)
        if got is None:
            raise ValueError(wording.say("host_errors.no_run"))
        held = got[0]
        return {"sid": held["sid"], "task": held["task"], "run": given["run"], "name": held.get("name") or "",
                "by": held.get("by")}

    def add_chat(self, rid, sid, chat):
        """(server's lock held) chat, started by the run rid of the Bot sid: kept in its record (chats)."""
        with self.lock:
            st = self.stores.get(sid)
            if st is None or rid not in st.runs:
                return
            self._update(st, rid, chats=[*(st.runs[rid].get("chats") or []), chat][-CHATS_KEPT:])

    def chat_over(self, s, result=None, over="finished"):
        """(server's lock held) s, a chat a task's run started, at a turn's end (result: what it said; over: finished,
        or stopped or failed: interrupted, an error, or its agent ended before its turn did): its Bot told, as a
        message from it whose sender says the run and how it ended (once the Bot's idle, between its tasks' asks), and
        the run's log told too. Until the first turn that finishes: what s says after is its own chat's. What's to be
        told is kept in s's meta (task_run's pending) till its Bot's taken it, so a restart of the host sends it
        then (resume_told)."""
        came = s.meta.get("task_run")
        if not isinstance(came, dict) or came.get("ended") or came.get("told"):
            return
        bot = self.server.sessions.get(came.get("bot"))
        if bot is None:
            return
        text = str(result or "").strip() or wording.say("tasks.agent_said_nothing")
        over = over if over in wire.SENDER_OVER else "failed"
        st = self.store(bot)
        if came.get("run") in st.runs:
            st.log(came["run"], "agent", text, session=s.id, over=over, error=over != "finished" or None)
        came["pending"] = [*(came.get("pending") or []), {"id": str(uuid.uuid4()), "text": text, "over": over}]
        if over == "finished":
            came["ended"] = True
        self.server.save(s)
        self.telling(s.id)

    def resume_told(self):
        """(server's lock held) What chats a task's run started had still to tell their Bots as the host last stopped
        (task_run's pending): told now."""
        for s in list(self.server.sessions.values()):
            if isinstance(s.meta.get("task_run"), dict) and s.meta["task_run"].get("pending"):
                self.telling(s.id)

    def telling(self, cid):
        """(server's lock held) The chat cid's pending reports given its Bot, on a thread of its own (one per chat:
        each in turn, oldest first)."""
        with self.lock:
            if cid in self.tellers:
                return
            self.tellers.add(cid)
        threading.Thread(target=self._tell, args=(cid,), daemon=True, name=f"task-told-{cid[:8]}").start()

    def _tell(self, cid):
        """The chat cid's pending reports given its Bot, each as a message from cid, once the Bot's idle and no ask of
        its tasks is waiting on it (one at a time: ask's turn, taken for each look only), oldest first; each let go of
        once it's taken (its uuid the report's: taken once, even if it's sent again after a restart). The Bot busy
        past BUSY_SAID_S: said on the run, and still waited for; one it can't take (Elsewhere, its agent not starting):
        said on the run, and tried again after RETELL_S. Gone, the chat or its Bot: let go of. The host stopping:
        kept, for its next start."""
        srv, said, at, begun, retry = self.server, set(), None, 0.0, 0.0
        try:
            while not srv.stopped.is_set():
                with srv.lock:
                    c = srv.sessions.get(cid)
                    came = c.meta.get("task_run") if c is not None and not c.deleting else None
                    pending = came.get("pending") if isinstance(came, dict) else None
                    if not pending:
                        return
                    b = srv.sessions.get(came.get("bot"))
                    if b is None or b.deleting:  # (no Bot to tell)
                        came["pending"] = None
                        srv.save(c)
                        return
                    report = pending[0]
                    if report.get("id") != at:
                        at, begun, retry = report.get("id"), time.monotonic(), 0.0
                    with self.lock:
                        turn = self.turns.setdefault(b.id, threading.Lock())
                if time.monotonic() < retry or not turn.acquire(timeout=0.25):
                    time.sleep(0.25)
                    continue
                try:
                    with srv.lock:
                        c, b = srv.sessions.get(cid), srv.sessions.get(came.get("bot"))
                        if c is not None and b is not None and not c.deleting and not b.deleting and \
                                not b.busy and not b.codex_queue and not b.meta.get("archived"):
                            sender = {"kind": "subagent", "name": c.listing().get("title") or "",
                                      "host": srv.me["id"], "host_name": srv.me["name"], "session": cid,
                                      "bot": b.id, "task": came.get("task") or "", "task_name": came.get("name") or "",
                                      "run": came.get("run") or "", "over": report.get("over") or "finished"}
                            try:
                                srv.take_input(None, b, {"type": "user", "uuid": report["id"], "message": {
                                    "role": "user", "content": report["text"]}}, sender)
                            except Exception as e:  # noqa: BLE001 (Elsewhere, or its agent not started: again)
                                srv.log(f"telling {b.id} what {cid} said: {e}")
                                if ("error", at) not in said:
                                    said.add(("error", at))
                                    self._said(b, came, wording.say("tasks.agent_not_told", bot=self._bot(b),
                                                                    name=sender["name"], error=str(e)))
                                retry = time.monotonic() + RETELL_S
                            else:
                                came["pending"] = [r for r in came.get("pending") or [] if r.get("id") != at] or None
                                srv.save(c)
                                continue
                        elif c is not None and b is not None and time.monotonic() - begun > BUSY_SAID_S and \
                                ("busy", at) not in said:
                            said.add(("busy", at))
                            self._said(b, came, wording.say("tasks.agent_waiting", bot=self._bot(b),
                                                            name=c.listing().get("title") or ""))
                finally:
                    turn.release()
                time.sleep(0.25)
        finally:
            with self.lock:
                self.tellers.discard(cid)
            with srv.lock:  # (one more come as it let go: told too)
                c = srv.sessions.get(cid)
                came = c.meta.get("task_run") if c is not None else None
                if not srv.stopped.is_set() and isinstance(came, dict) and came.get("pending"):
                    self.telling(cid)

    def _said(self, bot, came, text):
        """(server's lock held) A line on the run of the Bot bot that came names (a chat it started's task_run)."""
        st = self.store(bot)
        if came.get("run") in st.runs:
            st.log(came["run"], "status", text)

    @staticmethod
    def _bot(bot):
        return (bot.meta.get("bot") or {}).get("name") or wording.say("sidebar.bot")

    # ---- due
    def due(self, block=False):
        """Triggers due now fired (each Bot's whose tasks aren't paused, not archived, not being deleted, the host
        not stopping): schedules come due; branches pushed to, looked at on a thread of their own (block: on this
        one). When the next one's due (seconds from now), or None if there's none."""
        srv, now, soonest, looks = self.server, time.time(), None, []
        self.deliver()
        if self.checked is None or time.monotonic() - self.checked >= taskvars.CHECK_S:
            self.checked = time.monotonic()
            threading.Thread(target=self.check_all, daemon=True, name="task-variables").start()
        with srv.lock:
            if srv.stopped.is_set():
                return None
            for s in list(srv.sessions.values()):
                if not s.meta.get("bot") or s.meta.get("archived") or s.deleting:
                    continue
                st = self.store(s)
                with self.lock:
                    changed = False
                    for task in st.tasks:
                        if task.get("paused"):
                            continue
                        for t in task["triggers"]:
                            if t["kind"] == "schedule" and t.get("next") is not None:
                                if t["next"] <= now:
                                    payload = {"time": datetime.datetime.fromtimestamp(now).isoformat(
                                        timespec="minutes")}
                                    t["fired"], t["next"] = now, next_run(t, now)  # (one missed: run once)
                                    changed = True
                                    try:
                                        self.start(s, task, t, "schedule", payload)
                                        t.pop("error", None)
                                    except ValueError as e:
                                        t["error"] = str(e)
                                wait = t["next"] - now
                            elif t["kind"] == "git_push":
                                at = self.polled.get((s.id, t["id"]), 0) + t.get("poll_s", POLL_S)
                                if at <= now:
                                    looks.append((s.id, task["id"], t["id"], dict(t)))
                                    self.polled[(s.id, t["id"])] = now
                                    at = now + t.get("poll_s", POLL_S)
                                wait = at - now
                            else:
                                continue
                            soonest = wait if soonest is None else min(soonest, wait)
                    if changed:
                        st.save()
        if looks and block:
            self.look(looks)
        elif looks:
            threading.Thread(target=self.look, args=(looks,), daemon=True, name="task-git").start()
        if self.notices:  # (a Bot busy: looked at again soon)
            soonest = 1.0 if soonest is None else min(soonest, 1.0)
        return soonest

    def look(self, looks):
        """Git push triggers' branches looked at (not under the lock: git), each moved since it was seen a run of its
        task, but not while one's going: its head's seen as that run starts, the pushes meanwhile run after it."""
        srv = self.server
        for sid, tid, trid, t in looks:
            try:
                head, payload = pushed(t)
                error = None
            except (OSError, subprocess.SubprocessError) as e:
                head, payload, error = t.get("seen"), None, str(e)[:300]
            with srv.lock:
                s = srv.sessions.get(sid)
                if s is None or srv.stopped.is_set():
                    continue
                st = self.store(s)
                with self.lock:
                    task = st.find(tid)
                    trig = next((x for x in (task or {}).get("triggers") or [] if x["id"] == trid), None)
                    if trig is None or trig.get("seen") != t.get("seen"):  # (gone, or looked at since)
                        continue
                    if payload is not None and self.going(sid, tid):
                        continue
                    if trig.get("seen") != head or trig.get("error") != error:
                        trig["seen"] = head
                        if error:
                            trig["error"] = error
                        else:
                            trig.pop("error", None)
                        if payload is not None:
                            trig["fired"] = time.time()
                            try:
                                self.start(s, task, trig, "git_push", payload)
                            except ValueError as e:
                                trig["error"] = str(e)
                        st.save()
                        srv.changed()
