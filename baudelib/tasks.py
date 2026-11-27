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

from baudelib import wire, wording

RUNTIMES = tuple(wire.DEFS["Task"]["properties"]["runtime"]["enum"])
KINDS = tuple(wire.DEFS["TaskTrigger"]["properties"]["kind"]["enum"])
TYPES = tuple(wire.DEFS["TaskField"]["properties"]["type"]["enum"])
TASKS_MAX, FIELDS_MAX, TRIGGERS_MAX = 50, 30, 10
SCRIPT_MAX = 256 * 1024
RUNS_KEPT, RUNS_SHOWN, LOG_SHOWN = 100, 50, 2000
CHATS_KEPT = 50  # (the chats a run started, kept in its record: the latest)
LOG_MAX = 4 * 1024 * 1024  # (a run's log, at most: what's written after, dropped)
TIMEOUT_S, TIMEOUT_MAX = 3600, 24 * 3600
ASK_S = 3600  # (an ask of its Bot waited for this long, at most)
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
    name = " ".join(str(task.get("name") or "").split())[:80] or (
        (script or "").strip().splitlines()[0][:40] if script else (old or {}).get("name")) or "Task"
    out = {"id": (old or {}).get("id") or _id(task.get("id")), "name": name, "runtime": runtime,
           "version": version_of(script) if script is not None else old["version"], "form": form,
           "triggers": triggers, "fallback": bool(task.get("fallback", (old or {}).get("fallback", True))),
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


def answered(text, form):
    """(values, None) from a Bot's answer to an ask with a form (a JSON object in it), or (None, what's wrong)."""
    start, end = (text or "").find("{"), (text or "").rfind("}")
    try:
        got = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        got = None
    if not isinstance(got, dict):
        return None, "the answer has no JSON object"
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
    return (None, "; ".join(wrong)) if wrong else (values, None)


def form_said(form):
    """How a form's asked for, of a Bot."""
    def one(f):
        kind = {"choice": "one of " + ", ".join(json.dumps(c) for c in f.get("choices", ())), "number": "a number",
                "bool": "true or false", "text": "text"}[f["type"]]
        return f"- {f['name']}: {kind}" + (f" ({f['label']})" if f.get("label") else "") + \
            (" (required)" if f.get("required") else "")
    return "Answer with only a JSON object with these fields:\n" + "\n".join(one(f) for f in form)


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
            "form": [], "triggers": [trigger], "fallback": False, "timeout_s": TIMEOUT_S,
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
    """What an ask of a run's Bot starts with: the task (name) failed, or asks, then a table of the run (its task's
    id tid, its record run): its ids, when it started (and ended), how long it's taken, the version, the command it
    runs, its exit code; a row left out that isn't known (an ask's run hasn't ended, nor has a failure it
    hands over itself)."""
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
    return "\n".join([wording.say("tasks.asked", name=name, error="error" if error else "ask"), "",
                      lines[0], "|---|---|", *lines[1:]])


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
    their scripts' asks carried to their Bots and answered at their turns' ends."""

    def __init__(self, server):
        self.server = server
        self.lock = threading.RLock()  # (taken after the server's lock, never before)
        self.stores = {}  # (a Bot's session id: its Store)
        self.running = {}  # (a run's id: {sid, task, proc, token, stop})
        self.waiting = {}  # (a Bot's session id: the ask sent it, waiting on its turn's end)
        self.polled = {}  # ((sid, trigger id): when it was last looked at)
        self.turns = {}  # (a Bot's session id: held while one ask of it waits on its turn: one at a time)
        self.tellers = set()  # (the chats a run started whose reports are being given their Bots: _tell)

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
        return {**task, "last": last[0] if last else None, "next": None if task.get("paused") or not nexts
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
            run = self.start(s, task, None, "manual", {}, inputs, by=by)
            return "task_started", {"session": s.id, "run": run}
        with self.lock:
            run = st.runs.get(msg.get("run"))
            if run is None:
                raise ValueError(wording.say("host_errors.no_run"))
            if op == "task_stop":
                self.stop(run["id"], wording.say("task_notes.stopped"))
            return "task_run", {"session": s.id, "run": dict(st.runs[run["id"]]), "log": st.read_log(run["id"]),
                                "bot": (s.meta.get("bot") or {}).get("name")}

    # ---- run
    def start(self, s, task, trigger, kind, payload, given=None, by=None):
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
                run.update(status="skipped", ended=now, seconds=0, error=wording.say("tasks.still_running"))
                st.put_run(run)
                return run
            st.put_run(run)
            token = secrets.token_hex(16)
            self.running[run["id"]] = {"sid": s.id, "task": task["id"], "name": task["name"], "proc": None,
                                       "token": token, "stop": None,
                                       "by": by if kind == "manual" else s.meta.get("started_by")}
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
            if task["runtime"] == "bot":
                self._cwd(sid, st)
                text, failed = self.ask(run["id"], st.script(task), task["name"])
                error = wording.say("tasks.bot_failed") if failed else None
            else:
                code, error = self._script(sid, st, task, run, token, self._cwd(sid, st))
        except Exception as e:  # noqa: BLE001 (said in its run, and on the page)
            error = f"{type(e).__name__}: {e}"
        with self.lock:
            held = self.running.get(run["id"]) or {}
            stopped = held.get("stop")
        hand = bool(error and not stopped and task.get("fallback") and task["runtime"] != "bot" and
                    not st.runs[run["id"]].get("handed"))
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

    def _update(self, st, rid, **fields):
        with self.lock:
            run = {**st.runs[rid], **{k: v for k, v in fields.items() if v is not None or k == "exit"}}
            st.put_run(run)
            return run

    def _script(self, sid, st, task, run, token, cwd):
        """A python or shell task's script run (its Bot's, sid: the chats it starts nested under it, BAUDE_SPAWNED_BY):
        (its exit code, why it failed or None). Stopped or out of time: its process group told to stop, and killed
        after GRACE_S whether or not its script's gone."""
        path = st.script_path(task)
        argv = [*runner(task), path]
        env = {**os.environ, "BAUDE_SOCKET": os.path.abspath(self.server.socket_path), "BAUDE_TASK_RUN": run["id"],
               "BAUDE_SPAWNED_BY": sid,
               "BAUDE_TASK_TOKEN": token, "BAUDE_TASK_INPUTS": json.dumps(run["inputs"]),
               "BAUDE_TASK_PAYLOAD": json.dumps(run["payload"] or {}), "BAUDE_TASK_NAME": task["name"],
               "BAUDE_WIRE_VERSION": str(wire.VERSION),
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
        end, code, timed_out = time.monotonic() + task["timeout_s"], None, False
        while code is None and not held.get("stop"):
            try:
                code = p.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                if time.monotonic() > end:
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
            return [{"session": r["sid"], "what": wording.say("tasks.task_running")} for r in self.running.values()]

    def blocking(self):
        """Each run going, as a pending restart lists it (RestartBlocker): [{kind, session, task, run, name, what}]."""
        with self.lock:
            return [{"kind": "task", "session": r["sid"], "task": r["task"], "run": rid, "name": r.get("name") or "",
                     "what": wording.say("tasks.task_running")} for rid, r in self.running.items()]

    # ---- a script's
    def held(self, msg):
        """(the run a script names, as it's held, its store) if its token is right, else None."""
        with self.lock:
            held = self.running.get(msg.get("run"))
            if held is None or not secrets.compare_digest(str(msg.get("token") or ""), held["token"]):
                return None
            return held, self.stores.get(held["sid"])

    def report(self, msg):
        got = self.held(msg)
        if got is None:
            raise ValueError(wording.say("host_errors.no_run"))
        got[1].log(msg["run"], "say", msg.get("say") or "")

    def script_ask(self, msg):
        """task_ask's answer (text, values), once its Bot's turn ends: ValueError if it can't be (a failure handed
        over by a task whose failures aren't handed to its Bot, too)."""
        got = self.held(msg)
        if got is None:
            raise ValueError(wording.say("host_errors.no_run"))
        held, st = got
        form = [clean_field(f) for f in msg.get("form") or []] if isinstance(msg.get("form"), list) else []
        text = str(msg.get("text") or "")
        with self.lock:
            task = st.find(held["task"]) or {"name": "Task", "fallback": False}
        if msg.get("error"):
            if not task.get("fallback"):
                raise ValueError(wording.say("host_errors.task_no_fallback"))
            self._update(st, msg["run"], handed=True)  # (the script's handed its failure over: the host doesn't again)
        said, failed = self.ask(msg["run"], text, task["name"], form=form, error=bool(msg.get("error")))
        if failed:
            raise ValueError(said or wording.say("tasks.bot_failed"))
        if not form:
            return said, None
        values, wrong = answered(said, form)
        if wrong:  # (once more, told what was wrong with it)
            said, failed = self.ask(msg["run"], wording.say("tasks.not_as_asked", wrong=wrong) + "\n\n" +
                                    form_said(form), task["name"])
            values, wrong = (None, said) if failed else answered(said, form)
            if wrong:
                raise ValueError(wording.say("tasks.not_as_asked", wrong=wrong))
        st.log(msg["run"], "answer", json.dumps(values), values=values)
        return said, values

    def ask(self, rid, text, name, form=None, error=False, held=None):
        """(what the Bot said, failed) for text given a run's Bot as a turn of its own, from its task (name), waited
        for till that turn ends; the ask and its answer in the run's log. held: an ended run's (its failure handed
        on), else the run's as it's going. One ask of a Bot at a time, each sent as it's idle: the turn it ends
        is that ask's, not one going on as it was sent (a user's, a stopped run's)."""
        live = held is None
        with self.lock:
            held = self.running.get(rid) if live else held
            turn = self.turns.setdefault(held["sid"], threading.Lock()) if held else None
        if held is None:
            return wording.say("host_errors.no_run"), True
        end = time.monotonic() + ASK_S
        while not turn.acquire(timeout=0.25):  # (stopped meanwhile: let go of at once)
            if held.get("stop") or time.monotonic() > end:
                return held.get("stop") or wording.say("tasks.no_answer"), True
        try:
            return self._ask(rid, held, text, name, form, error, live, end)
        finally:
            turn.release()

    def _ask(self, rid, held, text, name, form, error, live, end):
        with self.lock:
            st = self.stores.get(held["sid"])
            if live and rid not in self.running or st is None or held.get("stop"):
                return held.get("stop") or wording.say("host_errors.no_run"), True
        sid = held["sid"]
        st.log(rid, "ask", text, error=error or None)
        if live:
            self._update(st, rid, status="asking", asks=(st.runs[rid].get("asks") or 0) + 1)
        wait = {"run": rid, "done": threading.Event(), "text": None, "failed": False}
        said = asked_head(name, held.get("task") or "", st.runs.get(rid) or {"id": rid}, error) + "\n\n" + \
            text + ("\n\n" + form_said(form) if form else "")
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
                        srv.take_input(None, s, {"type": "user", "uuid": str(uuid.uuid4()),
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
                break
            time.sleep(0.25)
        if sent:
            wait["done"].wait(max(0.0, end - time.monotonic()))
        with self.lock:
            if wait in self.waiting.get(sid, []):
                self.waiting[sid].remove(wait)
        if not wait["done"].is_set() and not wait["failed"]:
            wait["text"], wait["failed"] = wording.say("tasks.no_answer"), True
        if not form or wait["failed"]:
            st.log(rid, "answer", wait["text"] or "", error=wait["failed"] or None)
        if live and rid in self.running:
            self._update(st, rid, status="running")
        return wait["text"] or "", wait["failed"]

    def asking(self, sid):
        """Is an ask of one of the Bot sid's tasks waiting on it (sent, or to be sent once it's idle)?"""
        with self.lock:
            turn = self.turns.get(sid)
            return bool(self.waiting.get(sid)) or turn is not None and turn.locked()

    def turn_over(self, s, msg):
        """(server's lock held) s's turn ended (its result, msg): the ask it was sent for, answered with what it
        said."""
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
        past ASK_S: said on the run, and still waited for; one it can't take (Elsewhere, its agent not starting):
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
                        elif c is not None and b is not None and time.monotonic() - begun > ASK_S and \
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
