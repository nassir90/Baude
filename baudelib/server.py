"""The headless server: Claude Code sessions started and steered over the network, each run headless (ClaudeProc) the
way Baude runs a chat: Claude's models in claude, Apple's in apple-claude, those on this machine in pi-claude
(baudelib.models says how each is started), whichever of them this machine has. A session is started with a prompt, a
folder, a model, an effort, a permission mode and a group, and gets a UUID that is its id from then on: claude's
--session-id,
and what it's resumed by. Its claude keeps going between turns, so whoever attaches next carries on with it, and stops
once nobody's used it for a while (idle_s): the next message resumes it. Clients (Baude) list the sessions, read their
transcripts and their subagents', rename them, attach to one to see what it does and to talk to it, and answer what it
asks. wire.py says how.

What it keeps (state_dir: the server's id, each session's settings) is its own; a claude session's transcript is Claude
Code's, and the server keeps one in the same form for a session whose program keeps none (apple-claude, pi-claude)."""
import base64
import fcntl
import itertools
import json
import os
import queue
import re
import select
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

from baudelib import agentproxy, claude, codex, emissionlink, emissiontools, hostfiles, hostfixes, mesh, meshtools, phonebrowser, routines, models, notes, providers, selfupdate, transcripts, wire, wording, worktree

IDLE_S = 30 * 60
EXIT_S = 4 * 3600  # (nothing running and nobody attached this long: the host process goes; a head starts it again)
DRAFT_S = 30  # (a new chat never written in, nobody attached this long: gone)
FRAME_KEYS = frozenset(wire.DEFS["EmissionFrame"]["properties"]) - {"op", "req"}  # (what a watched Emission says)
EPHEMERAL_S = wire.CONSTANTS["EPHEMERAL_S"]  # (a side question whose head's gone: gone this soon)
GRACE_MOST = wire.CONSTANTS["GRACE_MAX_S"]  # (start's grace: an ephemeral session waits at most this long for its head
# to come back)
APPS = frozenset({"baude", "record", "baude-android"})  # (heads listed as apps connected: a script that says
# anything else, or a host's mesh, is no app)
EMISSIONS_S = 30  # (this machine's Emissions, and the code each runs, asked this often: heads_look)
CHECK_SOCKET_S = 600  # (nobody attached: its socket looked at this often, in case its folder's been taken away)
TRANSCRIPT_CHUNK = 1 << 20
TAKEN = 64  # (a session's messages from heads, by uuid: the last this many kept, a repeat of one taken once)
FILE_LIMIT = 4 << 20
CATALOGUE_WAIT_S = 30  # (a folder's catalogue being made: waited for this long before what there is is sent)
CATALOGUE_LIMIT = 20000  # (files sent of a folder's, at most: its other names all are)
RENAME_S = 15  # (what a claude asked to rename its session is given to answer: then it's done without it)
SUBAGENT_FILES = (".jsonl", ".meta.json")  # (in a subagents folder: each subagent's transcript, and what it's for)
# (what a client may set on a session whose claude isn't running: kept for when it runs, said to be done)
SETTINGS = {"set_model": ("model", "model"), "set_permission_mode": ("mode", "mode")}
# what start takes for claude's own flags, each run: (param, flag); and how much of an ask's input a listing shows
CLAUDE_OPTIONS = (("system", "--append-system-prompt"), ("add_dirs", "--add-dir"), ("allowed_tools", "--allowedTools"),
                  ("disallowed_tools", "--disallowedTools"))
ASK_SHOWN = 400
COMMAND_TOOLS = ("Bash", "PowerShell")  # (a call of one running, or a background shell: a restart waits for it)
AGENT_TOOLS = ("Agent", "Task")
WHAT_SHOWN = 80


def _shown(value, depth=0):
    """An ask's input as a listing shows it: long strings cut short, deep or long things summed up."""
    if isinstance(value, str):
        return value if len(value) <= ASK_SHOWN else value[:ASK_SHOWN] + "…"
    if isinstance(value, dict) and depth < 3:
        return {k: _shown(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, list) and depth < 3:
        return [_shown(v, depth + 1) for v in value[:20]]
    return value if isinstance(value, (int, float, bool)) or value is None else f"({type(value).__name__})"


def _what(description, command=None):
    """A command as a listing shows it: what it says it's for, else its first line; cut short."""
    text = description if isinstance(description, str) and description.strip() else \
        (command.strip().splitlines() or [""])[0] if isinstance(command, str) else ""
    text = " ".join(text.split())
    return text if len(text) <= WHAT_SHOWN else text[:WHAT_SHOWN - 1] + "…"


def state_home():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "server")


def kept_sessions(state_dir=None):
    """The sessions a host process keeps in its state folder (None: this machine's), each one's settings as it saved
    them (Server.save), read as it loads them, whether it's running or not."""
    folder = os.path.join(state_dir or state_home(), "sessions")
    out = []
    for entry in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        try:
            with open(os.path.join(folder, entry), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and wire.is_id(meta.get("id")):
            out.append(meta)
    return out


def kept_session(sid, state_dir=None):
    """The settings this machine's host process keeps for a session (kept_sessions), by its id or claude's (cli_id),
    or None: not one of its own."""
    if wire.is_id(sid or ""):
        try:
            with open(os.path.join(state_dir or state_home(), "sessions", f"{sid}.json"), encoding="utf-8") as f:
                meta = json.load(f)
            if isinstance(meta, dict) and meta.get("id") == sid:
                return meta
        except (OSError, ValueError):
            pass
    return next((m for m in kept_sessions(state_dir) if sid and m.get("cli_id") == sid), None)


def lock_path(state_dir=None):
    """The lock a host process holds while it runs (one each): in its state folder, named for this machine."""
    return os.path.join(state_dir or state_home(), f"host-{wire.safe_name(socket.gethostname().split('.')[0])}.lock")


def running_pid(state_dir=None):
    """The pid of the host process running here, by its lock (held: one is), else None."""
    try:
        fd = os.open(lock_path(state_dir), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return None  # (not held: none's running)
        except OSError:
            pass
        text = os.read(fd, 32).decode(errors="replace").strip()
    finally:
        os.close(fd)
    if not text.isdigit():
        return None
    try:  # (its pid, not another's since: the lock's written as it's taken, and a pid's used again)
        ran = subprocess.run(["ps", "-o", "command=", "-p", text], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(text) if "baude_headless.py" in ran else None


def _said(msg):
    """A user message's text (its text blocks', if it's more than text), for a title."""
    content = (msg.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return " ".join(c.get("text") or "" for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def clean_env(env):
    """A head's environment for a session's claude: names and values that are text, nothing else."""
    return {k: v for k, v in env.items() if isinstance(k, str) and isinstance(v, str) and k and "=" not in k
            and "\0" not in k + v}


def trash(path):
    """Move a file or folder to the trash, as Baude's Move to Trash does: on macOS its user's ~/.Trash; else (and
    wherever XDG_DATA_HOME's set) the freedesktop one, $XDG_DATA_HOME/Trash, with what it was. Where it went."""
    import datetime
    import shutil
    name = os.path.basename(path.rstrip("/"))
    data = os.environ.get("XDG_DATA_HOME")
    if sys.platform == "darwin" and not data:
        folder, info = os.path.expanduser("~/.Trash"), None
    else:
        base = os.path.join(data or os.path.expanduser("~/.local/share"), "Trash")
        folder, info = os.path.join(base, "files"), os.path.join(base, "info")
        os.makedirs(info, mode=0o700, exist_ok=True)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    target, n = os.path.join(folder, name), 1
    while os.path.lexists(target) or info and os.path.lexists(os.path.join(info, os.path.basename(target) + ".trashinfo")):
        n += 1
        stem, ext = os.path.splitext(name)
        target = os.path.join(folder, f"{stem} {n}{ext}")
    if info:
        with open(os.path.join(info, os.path.basename(target) + ".trashinfo"), "w", encoding="utf-8") as f:
            f.write(f"[Trash Info]\nPath={urllib.parse.quote(os.path.abspath(path))}\n"
                    f"DeletionDate={datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
    shutil.move(path, target)
    return target


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def _read_json(path):
    """A JSON file's data, or None if it isn't there or isn't JSON."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Conn:
    """A client's connection: what it's sent goes out on a thread of its own, so a slow one holds nobody up."""

    def __init__(self, channel, origin=None, sender=None):
        self.channel, self.out = channel, queue.Queue()
        self.subscribed, self.answers = False, True  # (answers: what its sessions ask, if it's attached to them)
        self.origin, self.sender = origin, sender  # (a tunnel's: the member it came from; the chat there its relay
        # named, as that member has it: what's sent through it is from)
        self.archived = False  # (subscribed to archived sessions too)
        self.writer = threading.Thread(target=self._write, daemon=True, name="client-out")
        self.writer.start()

    def send(self, msg):
        self.out.put(msg)

    def _write(self):
        while (msg := self.out.get()) is not None:
            if not self.channel.send(msg):
                break

    def close(self, flush=0):
        """Nothing more sent. flush: what's queued written first (waited for that long at most), then its channel
        closed (else it's left as it is: the mesh's, say, which takes it on)."""
        self.out.put(None)
        if flush:
            self.writer.join(flush)
            self.channel.close()


class Session:
    def __init__(self, server, meta):
        self.server, self.meta = server, meta
        self.proc = None
        self.viewers, self.live = set(), set()  # attached Conns; those that see what claude says as it says it
        self.asks = {}            # claude's request id -> a can_use_tool it sent, not answered yet
        self.busy = False         # at a turn
        self.acting = False       # at a turn's tools: a tool call begun, no text begun since (turn_acting)
        self.streamed = False     # (its agent's said what it writes as it writes it: stream_event)
        self.routines_out = set()  # (its routines sent at a turn, not yet over: not sent again till it is)
        self.waiting_init = []    # (conn, its request id): initialize asked before claude answered its own
        self.queued = []          # (message, uuid) sent at a turn, kept once it's over (Server.keep)
        self.codex_queue = []     # Codex runs each follow-up as a separate turn.
        self.rewinds = {}         # request id sent on -> the record a revert keeps last (kept: its mark, once made)
        self.reverting = {}       # request id sent on -> the message a revert takes back (what it drops of Senders)
        self.senders = None       # (its sender store as it's kept, {uuid: sender}, once it's read: Server.senders)
        self.routes = {}          # request id sent on -> (conn, its own request id or the server's callback, undo)
        self.scan = self.info = self.stat = None  # the transcript: scan_session's state and info, (size, mtime)
        self.agents = None        # its subagents' files, as last looked at: {name: (size, mtime_ns)}
        self.used = time.time()
        self.deleting = self.deleted = False  # (Server.delete's: nothing more taken for it; nothing more kept of it)
        self.env = {}             # (the environment a head on this machine gave it: its claude's, never written)
        self.always_allow = set()  # (tools a head always allows: allowed here, with or without a head; memory only)
        self.renew = False        # (a Bot changed at a turn: its agent started again once the turn's over, set_bot)
        self.drop_timer, self.attached_once = None, False  # (drop_ephemeral's)
        self.app_tools = None     # (a head's own tools for its claude, start's app_tools: agentproxy.AppTools; memory only)
        # what its claude runs (commands): its Bash and PowerShell calls not answered yet, by tool_use_id (its
        # subagents' too: parent, the call they're in); its background tasks, by task_id. Not listed, its subagents:
        # their tasks running, task_id -> their Agent call's id; each Agent call, id -> (the call it's in, run in the
        # background); and those running in the background, by call id (what runs in one outlives a turn's result)
        self.calls, self.tasks, self.agent_tasks, self.agent_calls, self.background_agents = {}, {}, {}, {}, set()
        self.outputs, self.outputs_at = set(), 0  # (the output files its transcript names, and how far it's read for them)

    @property
    def id(self):
        return self.meta["id"]

    def state(self):
        if self.proc is None:
            return "stopped"
        return "waiting" if self.asks else "working" if self.busy else "idle"

    def note_written(self):
        """(lock held) How long its transcript is, as its own claude left it (or this host wrote to it, with none
        running): listed, so a head can tell a terminal's claude writing to it since (written) from its own."""
        path = self.transcript()
        try:
            self.meta["written"] = os.path.getsize(path) if path else None
        except OSError:
            self.meta["written"] = None

    def output_files(self):
        """The output files its transcript names (a background task's start or end): what's new in it since the last
        call is read."""
        path = self.transcript()
        try:
            size = os.path.getsize(path) if path else 0
            if size < self.outputs_at:
                self.outputs, self.outputs_at = set(), 0
            with open(path, "rb") as f:
                f.seek(self.outputs_at)
                data = f.read(size - self.outputs_at)
        except (OSError, TypeError):
            return self.outputs
        whole = data[:data.rfind(b"\n") + 1]
        for found in re.findall(rb"Output is being written to: (\S+)|<output-file>(.*?)</output-file>", whole):
            name = (found[0].rstrip(b".") or found[1]).decode("utf-8", "replace").strip()
            if name:
                self.outputs.add(name)
        self.outputs_at += len(whole)
        return self.outputs

    def commands(self):
        """What its claude is running, oldest first: its calls of Bash and PowerShell (but one waiting on its own
        permission ask: not started yet), and its background tasks other than subagents. Each {id (tool_use_id, or
        task_id), what, since, background}."""
        asked = {(m.get("request") or {}).get("tool_use_id") for m in self.asks.values()}
        running = [c for c in self.calls.values() if c["id"] not in asked] + list(self.tasks.values())
        return [{"id": c["id"], "what": c["what"], "since": c["since"], "background": c["background"]}
                for c in sorted(running, key=lambda c: c["since"])]

    def forget_commands(self):
        """Its claude's gone: nothing of what it ran is running (its background shells went with it)."""
        self.calls, self.tasks, self.agent_tasks, self.agent_calls, self.background_agents = {}, {}, {}, {}, set()

    def in_background_agent(self, tool):
        """Is tool (a call's parent) a subagent running in the background, or in one?"""
        for _depth in range(10):
            if tool is None:
                return False
            if tool in self.background_agents:
                return True
            tool = (self.agent_calls.get(tool) or (None, False))[0]
        return False

    def kept(self):
        """Is its transcript the server's (its program keeps none)?"""
        return not models.program(self.meta.get("model"))["transcript"]

    def transcript(self):
        """Its transcript, where it is (None before there's one): Claude Code's, or the server's (kept)."""
        if self.kept():
            path = os.path.join(self.server.state_dir, "transcripts", f"{self.id}.jsonl")
            return path if os.path.exists(path) else None
        path = self.meta.get("path")
        if path and os.path.exists(path):
            return path
        path = transcripts.session_file(self.meta.get("cli_id") or self.id)
        if path and path != self.meta.get("path"):
            self.meta["path"] = path
            self.server.save(self)
        return path

    def found_transcript(self):
        """Its transcript's path as it's been found (transcript, which the watch calls, finds it), without looking."""
        if self.kept():
            path = os.path.join(self.server.state_dir, "transcripts", f"{self.id}.jsonl")
        else:
            path = self.meta.get("path")
        return path if path and os.path.isfile(path) else None

    def subagents_dir(self, path=None):
        """The folder claude keeps its subagents' transcripts in, beside its own (path: that, if it's known); None for
        one the server keeps (its program has no subagents)."""
        path = None if self.kept() else path or self.transcript()
        return path[:-len(".jsonl")] + os.sep + "subagents" if path and path.endswith(".jsonl") else None

    def subagent_files(self, path=None):
        """Its subagents' files as they are now: {name: (size, mtime_ns)}."""
        out, folder = {}, self.subagents_dir(path)
        if folder is None:
            return out  # (not scandir(None): that's the folder the server's in)
        try:
            with os.scandir(folder) as found:
                for e in found:
                    if e.name.endswith(SUBAGENT_FILES) and not e.name.startswith(".") and e.is_file():
                        st = e.stat()
                        out[e.name] = (st.st_size, st.st_mtime_ns)
        except (OSError, TypeError):
            pass
        return out

    def mesh_tools(self):
        """Has its agent the mesh's tools (baudelib.meshtools): a Bot's always; another chat's if it's been given them."""
        return bool(self.meta.get("bot") or self.meta.get("mesh_tools"))

    def bot_listing(self):
        """A Bot's name, and when its picture last changed (None: no picture): what a head draws it with."""
        bot = self.meta.get("bot")
        if not bot:
            return None
        pic = os.path.join(bot_dir(self.server.state_dir, self.id), "picture")
        return {"name": bot.get("name") or "Bot", "since": bot.get("since"),
                "picture": os.path.getmtime(pic) if os.path.exists(pic) else None}

    def listing(self):
        info = self.info or {}
        size, mtime = self.stat or (0, None)
        prompt = " ".join((self.meta.get("prompt") or "").split())
        return {"id": self.id, "title": self.meta.get("title") or info.get("title") or prompt[:90] or None,
                "cwd": self.meta.get("cwd"), "model": self.meta.get("model"), "effort": self.meta.get("effort"),
                "mode": self.meta.get("mode"), "group": self.meta.get("group"), "created": self.meta.get("created"),
                "said": max(info.get("said") or 0, self.meta.get("said") or 0) or self.meta.get("created"),
                "updated": mtime or self.meta.get("created"), "size": size, "state": self.state(),
                "prompts": info.get("prompts") or 0, "cli_id": self.meta.get("cli_id"),
                "transcript": self.found_transcript(),
                "archived": bool(self.meta.get("archived")), "draft": bool(self.meta.get("draft")),
                "written": self.meta.get("written"),
                "subagents": max((m for _size, m in (self.agents or {}).values()), default=0),
                "commands": self.commands(), "cut": self.meta.get("cut"), "bot": self.bot_listing(),
                "acting": self.acting and self.busy and not self.asks,
                "on_phone": self.server.phones.phone(self.id) is not None,
                "fork_from": self.meta.get("fork_from"), "spawned_by": self.meta.get("spawned_by"),
                "start_key": self.meta.get("start_key"),
                "held": [{"id": h["id"], "text": h["text"], "shown": h.get("shown"), "sender": h.get("sender"),
                          "at": h.get("at")} for h in self.meta.get("held") or []],
                "senders": self.meta.get("senders") or 0,
                "mesh_tools": self.mesh_tools(), "always_allow": sorted(self.always_allow),
                "asks": [{"request_id": rid, "tool": (m.get("request") or {}).get("tool_name"),
                          "input": _shown((m.get("request") or {}).get("input"))} for rid, m in self.asks.items()]}


def turn_acting(msg, was, streamed=False):
    """Whether a turn's at its tools once msg (its agent's own, not a subagent's) has come, was being whether it
    was before: a tool call's begun (its block started, as it's streamed; or come whole, as some agents send their
    calls though they stream their text), and it is; text begun, and it isn't (it's writing to you). streamed: its
    agent streams what it writes, so text come whole is what was streamed already (late: after a call's begun, say),
    not looked at. Thinking, and the rest, leave it as it was: between two calls it's still at them. Its result, or
    its agent gone, ends it (from_claude, claude_exited)."""
    if msg.get("type") == "stream_event":
        event = msg.get("event") or {}
        if event.get("type") != "content_block_start":
            return was
        kinds = [(event.get("content_block") or {}).get("type")]
    else:
        content = (msg.get("message") or {}).get("content")
        kinds = [c.get("type") for c in content if isinstance(c, dict)] if isinstance(content, list) else []
    if any(k in ("tool_use", "server_tool_use") for k in kinds):
        return True
    if msg.get("type") != "stream_event" and streamed:
        return was
    return False if "text" in kinds else was


BOT_PICTURE_MAX = wire.CONSTANTS["BOT_PICTURE_MAX"]  # (bytes: a Bot's picture, as sent)
BOT_MEMORY_TOLD = 32 << 10  # (bytes of a Bot's memory put in what its agent's told: an argument, of 128 KiB at most on
# Linux; the rest it reads in the file)


def bot_dir(state_dir, sid):
    """A Bot's own folder on its host: its memory (memory.md), what the user's accepted of it (memory.seen.md: what the
    Bot's changes are shown against, on the Memory page) and its picture."""
    return os.path.join(state_dir, "bots", wire.safe_name(sid))


def bot_system(name, memory_path, memory):
    """What a Bot's agent is told at each start, after its harness's own instructions: who it is, and its memory (its
    first BOT_MEMORY_TOLD bytes, cut at a line's end; the rest it's told is in the file)."""
    told, rest = memory.strip(), ""
    if len(told.encode()) > BOT_MEMORY_TOLD:
        cut = told.encode()[:BOT_MEMORY_TOLD].decode(errors="ignore")
        told = cut[:cut.rfind("\n")] if "\n" in cut else cut
        rest = (f"\n\nThat's the first {len(told.encode()) >> 10} KB of your memory: the rest is in the file. Read "
                "it before you rely on what's there, and make the memory shorter.")
    return (f"You are {name}, a Bot: a chat the user keeps coming back to, with a name and a memory that last.\n"
            f"Your memory is the Markdown file {memory_path}. It is yours to keep: read it, and edit it with your "
            "file tools when you learn something worth keeping (who the user is, what they want, how they like to "
            "work, what's in progress), and take out what's no longer true. Keep it short and current; the user can "
            "read and edit it too.\n"
            "Changes of yours the user rejects disappear from the file: don't put them back unless they ask you to "
            "(memory.seen.md beside it is their copy of what they've accepted: leave it as it is).\n\n"
            + ("Your memory as this run starts:\n<memory>\n" + told + "\n</memory>" + rest if told
               else "Your memory is empty so far."))


def _mtime(path):
    """A file's mtime in ns, or None if there's none."""
    try:
        return os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return None


def _write_text(path, text):
    """A file's text replaced whole (written beside it, then moved over it: never half there)."""
    with open(path + ".new", "w") as f:
        f.write(text)
    os.replace(path + ".new", path)


class AlreadyRunning(OSError):
    """This machine's user has a host process already (its socket's taken): there's one each."""


class Server:
    """Serve sessions on a Unix socket (socket_path: wire.socket_path(), this user's one on this machine, unless
    another's given). Nothing's said on the network: a head on this machine finds it by its socket, and one on
    another reaches it over ssh."""

    def __init__(self, state_dir=None, group=None, name=None, socket_path=None, exe=None,
                 idle_s=IDLE_S, log=None, env=None, exit_s=EXIT_S, on_change=None):
        self.state_dir = state_dir or state_home()
        self.exe, self.idle_s, self.env, self.exit_s = exe, idle_s, env, exit_s  # (env: more for its claudes; exit_s 0: never)
        self.on_change = on_change or (lambda: None)  # (what it lists changed: a menu bar's item, say; on its own thread)
        self.woken = threading.Event()  # (_watch: someone's attached, a claude started or stopped)
        self.log = log or (lambda text: print(f"baude-server: {text}", file=sys.stderr, flush=True))
        # one each: a lock in its state folder held while it runs, taken before anything's read or written there
        # (one beside the socket would be lost with the socket's folder, which a session's end can take: logind's)
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        host = socket.gethostname().split(".")[0]
        self.lock_file = os.open(lock_path(self.state_dir), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        # (a head looking at it, running_pid, holds it for a moment; one stopping, for some seconds: waited for)
        end = time.monotonic() + (10 if os.path.exists(self.state_dir + "/.stopping") else 0.5)
        while True:
            try:
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > end:
                    os.close(self.lock_file)
                    raise AlreadyRunning(f"a host process is running here already ({self.state_dir})") from None
                time.sleep(0.05)
        try:
            os.unlink(self.state_dir + "/.stopping")
        except OSError:
            pass
        os.ftruncate(self.lock_file, 0)  # (who's running: running_pid's, for a head to tell it to listen again)
        os.write(self.lock_file, str(os.getpid()).encode())
        self.lock = threading.RLock()
        self.rids = itertools.count(1)
        self.codex_catalog = ()
        self.codex_refresh_at = 0.0
        self.codex_refreshing = False
        me = os.path.join(self.state_dir, "server.json")
        try:
            with open(me, encoding="utf-8") as f:
                self.me = json.load(f)
        except (OSError, ValueError):
            self.me = {}
        if not self.me.get("id"):
            self.me["id"] = str(uuid.uuid4())
        self.me["name"] = name or self.me.get("name") or host
        self.me["group"] = group or self.me.get("group") or self.me["name"]
        self.me.pop("advertise", None)  # (said on the network, once: nothing is now)
        _write_json(me, self.me)
        providers.found()  # (this machine's inference providers looked for now: there by the time a head says hello)
        self.sessions = {}
        for meta in kept_sessions(self.state_dir):
            try:
                self.sessions[meta["id"]] = Session(self, meta)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        self.conns = set()
        self.dirty = False  # (subscribers have yet to hear of a change: _watch)
        self.notes_file, self.notes_seen = notes.file_key(), notes.stamp()  # (records.json here as last looked at:
        # info's notes, _look)
        self.notes_ops = queue.Queue()  # (notes ops, in the order they came: _notes)
        threading.Thread(target=self._notes, daemon=True, name="notes").start()
        self.socket_path = socket_path or wire.socket_path()
        self.catalogue_lock, self.catalogue_asked = threading.Lock(), {}  # (catalogue's: when each folder was first asked for)
        self.agent_runs = {}  # (id: an agent run here for a caller elsewhere: agentproxy.Run)
        self.phones = phonebrowser.Phones(self)  # (Bots whose browser is on a phone, connected here as a head)
        if len(self.socket_path.encode()) > 100:
            raise OSError(f"{self.socket_path}: too long a path for a socket")
        self.listener, self.accepted, self.binding = None, 0.0, threading.Lock()
        self.wake_r, self.wake_w = os.pipe()  # (the accept's waits woken: a new socket, or shut down)
        self.bind()
        self.stopped, self.closed = threading.Event(), threading.Event()  # (closed: shutdown's done, socket and all)
        # the commit its own code is, as it started (None: not a git checkout); what its updater has ready, waiting for
        # commands to end (set_update); and the commit it's shutting down to start again on (shutdown's restart)
        self.commit = selfupdate.git(selfupdate.HERE, "rev-parse", "HEAD", timeout=5) or None
        self.update, self.restarting = None, None
        self.heads_apart = set()  # (the commits of heads on other code that have said hello: logged once each)
        # the apps connected here, by the code they run (heads that have subscribed, and this machine's Emissions:
        # info's heads, and this host's record's for the mesh); when each commit was made, as git here has it
        self.made_at, self.heads, self.emissions = {}, [], []
        self.made = self.made_of(self.commit)
        self.feedback_filed = _read_json(os.path.join(self.state_dir, "feedback-filed.json")) or {}  # (feedback's
        # notes filed, by id, kept: found before GitHub's search finds them, and after a restart)
        self.feedback_claims, self.feedback_lock = {}, threading.Lock()  # (notes being filed now: .filing's, here)
        # stopping, how far along (stop_procs): {"left": claudes still to stop, "total", "restart": to start again},
        # for the menu bar's item to say; None while it isn't
        self.quitting = None
        self.cutting = set()  # (sessions halt cut at a turn that was only generating: not cut if it ends as it stops)
        self.following = set()  # ((session, the one it waits on): waits of its agent's answered for it, answer_wait)
        self.mesh = mesh.Mesh(self)  # (the hosts it's linked to, and those they reach: mesh.py)

    def bind(self):
        """The socket listened on: made (again, if it's gone: its folder taken away, say), in a folder of its user's
        alone, where one left by a host process that's gone is taken over (the lock says none's running). One at a
        time (the watch, and a head's SIGUSR1, may both want it)."""
        with self.binding:
            self._bind()

    def _bind(self):
        wire.own_dir(os.path.dirname(self.socket_path))
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)  # (its folder's its user's alone already: the socket's theirs too)
        listener.listen(16)
        self.bound = os.stat(self.socket_path).st_ino
        listener.setblocking(False)  # (accepted when select says there's one: never waited on there)
        old, self.listener = self.listener, listener
        if old is not None:
            os.write(self.wake_w, b"x")  # (the accept loop takes up the new one)
            old.close()

    def still_bound(self):
        """Is its socket still there (not taken away with its folder)? Bound again if it isn't (and if that fails,
        said, and tried again next time)."""
        with self.binding:
            try:
                if os.stat(self.socket_path).st_ino == self.bound:
                    return True
            except OSError:
                pass
            if not self.stopped.is_set():
                self.log(f"{self.socket_path} went: listening there again")
                try:
                    self._bind()
                except OSError as e:
                    self.log(f"couldn't listen there again: {e}")
        return False

    # ---- what it is
    def info(self):
        with self.lock:
            return self._info()

    def models(self, said=None, fresh=False):
        """Every model it can run, as initialize lists them: Claude's (as claude last said; said, if it's just said it:
        fresh), then those of this machine's inference providers (baudelib.providers: Apple's, MLX's, Ollama's, an
        endpoint's), each with its provider."""
        claudes = [m for m in said or [] if models.model_program(m.get("value")) == "claude"] if fresh else []
        if claudes and claudes != self.me.get("claude_models"):
            self.me["claude_models"] = claudes
            _write_json(os.path.join(self.state_dir, "server.json"), self.me)
        codex_models = self.codex_models()
        return (claudes or self.me.get("claude_models") or []) + codex_models + models.runnable_models()

    def codex_models(self):
        exe = models.codex_exe()
        if not exe:
            return []
        with self.lock:
            if not self.codex_refreshing and time.monotonic() >= self.codex_refresh_at:
                self.codex_refreshing = True
                threading.Thread(target=self.refresh_codex_models, args=(exe,), daemon=True).start()
            return self.codex_catalog or [models.CODEX_DEFAULT]

    def refresh_codex_models(self, exe):
        choices, retry = None, 60
        try:
            choices = models.codex_choices(codex.list_models(exe, self.env))
            retry = 300
        except (OSError, RuntimeError) as exc:
            self.log(f"Codex model discovery: {exc}")
        finally:
            with self.lock:
                self.codex_refresh_at = time.monotonic() + retry
                self.codex_refreshing = False
                if choices is not None and choices != self.codex_catalog:
                    self.codex_catalog = choices
                    for session in self.sessions.values():
                        if isinstance(session.proc, codex.CodexProc):
                            session.proc.update_catalog(choices)
                    self.changed()

    def init(self, said, fresh=False):
        """What a session's program said to initialize (fresh: just now, not kept from before), with every model this
        machine can run in it."""
        return {**said, "models": self.models(said.get("models"), fresh)}

    def can_run(self, model):
        """Can this machine run a model: Claude's (claude's own say); one Ollama or an endpoint serves, with pi here
        (its server's say comes with the first message: pi-claude's); or one of runnable_models (its program and, for
        MLX's, the model itself here)?"""
        if (agent := models.proxied(model)) is not None:  # (another member's, run there: while it's in the mesh)
            return any(m.get("id") == agent[0] for m in self.mesh.view()["members"])
        if models.model_program(model) in ("claude", "codex"):
            return not models.launch(model, None, None)["missing"]
        if isinstance(model, str) and model.startswith(models.SERVED):
            return providers.pi_installed()
        return any(m["value"] == model for m in models.runnable_models())

    def _info(self):
        return {"id": self.me["id"], "name": self.me["name"], "group": self.me["group"], "version": wire.VERSION,
                "models": [m if models.model_program(m["value"]) == "codex" else
                           {"value": m["value"], "displayName": m.get("displayName") or m["value"]}
                           for m in self.models()],
                "host": socket.gethostname(), "home": os.path.expanduser("~"), "pid": os.getpid(),
                "checkout": selfupdate.HERE,  # (where its code is: a head here run from elsewhere is told, code_apart)
                "groups": sorted({s.meta.get("group") or self.me["group"] for s in self.sessions.values()}
                                 | {self.me["group"]}),
                "init": self.me.get("init") or None, "commit": self.commit, "made": self.made,
                **self.mesh.here_behind(), "update": self.update,
                "providers": self.mesh.providers_here(), "mesh": self.mesh.view(),
                "notes": self.notes_seen}  # (Record's notebooks here, as written last: notes.stamp)

    def made_of(self, commit):
        """When commit was made, in seconds, as git here has it (None: it hasn't it), kept once asked."""
        if not commit:
            return None
        if commit not in self.made_at:
            stamp = selfupdate.git(selfupdate.HERE, "show", "-s", "--format=%ct", commit, timeout=5)
            self.made_at[commit] = int(stamp) if stamp and stamp.isdigit() else None
        return self.made_at[commit]

    def versions(self):
        """The code it runs and the apps on this machine connected to it by theirs, for its record (mesh.Mesh.mine)."""
        return {"commit": self.commit, "made": self.made, "heads": self.heads}

    def heads_look(self):
        """The apps on this machine connected here looked at again (a head's subscribed, or gone; an Emission's come
        or gone): the mesh told of a change, and heads here. (Not with the lock held.)"""
        with self.lock:
            # (a tunnel's is its own host's; one over ssh is its own machine's host's, there)
            seen = {(c.head["client"], c.head["commit"]) for c in self.conns
                    if c.subscribed and getattr(c, "head", None) and c.head["here"] and c.origin is None}
        seen |= {(e["client"], e["commit"]) for e in self.emissions}
        heads = [{"client": client, "commit": commit, "made": self.made_of(commit)}
                 for client, commit in sorted(seen, key=lambda h: (h[0], h[1] or ""))]
        if heads != self.heads:
            self.heads = heads
            self.mesh.republish()

    def _emissions(self):
        """This machine's Emissions, and the code each runs, asked every EMISSIONS_S (they're no heads: the host
        process reaches them, emissionlink)."""
        while not self.stopped.wait(EMISSIONS_S):
            found = [{"client": "emission", "commit": c} for c in emissionlink.running_commits()]
            if found != self.emissions:
                self.emissions = found
                self.heads_look()

    def mesh_changed(self):
        """The mesh changed (a member came or went, a link's state, a provider): heads hear, with its info. (Never with
        the mesh's lock held: info takes it under this one's.)"""
        with self.lock:
            self.changed()

    def set_update(self, update):
        """What its updater says (info's update): None, or {to: the commit it'll restart onto, waiting: [{session,
        what}], the commands it's waiting for}. Heads hear of a change."""
        with self.lock:
            if update != self.update:
                self.update = update
                self.changed()

    def running(self):
        """(lock held) Every command its sessions' claudes are running (Session.commands): [{session, what}]."""
        return [{"session": s.id, "what": c["what"]} for s in self.sessions.values() for c in s.commands()]

    def delete(self, s, reply):
        """A session gone (deleting set as it's asked: nothing more's taken for it): its claude stopped and waited for,
        as what it writes as it ends is its transcript's; then its transcript (claude's, or the one kept here), the
        folder claude keeps beside it (its subagents'), pi's own copy of it and a Bot's folder, moved to the trash,
        where they can be got back, as Baude's Move to Trash does with a chat of its own; only then is it no longer
        listed or kept, and those attached to it told (deleted). One whose files can't be moved stays, and the error
        says why."""
        with self.lock:
            proc = s.proc
            if proc is not None:
                proc.stopping = True
                proc.close_stdin()  # (claude, its stdin closed, ends as it would at the end of a pipe)
        if proc is not None:
            try:
                proc.p.wait(timeout=5)
            except Exception:  # (one that doesn't, in time: made to)
                proc.kill()
                try:
                    proc.p.wait(timeout=2)
                except Exception:
                    pass
        with self.lock:
            path = s.transcript() if not s.kept() else os.path.join(self.state_dir, "transcripts", f"{s.id}.jsonl")
            program = models.program(s.meta.get("model"))["name"]
            names = {s.meta.get("cli_id") or s.id, s.id}
        paths = [path, path[:-len(".jsonl")] if path and path.endswith(".jsonl") else None,
                 bot_dir(self.state_dir, s.id),  # (a Bot's, or a chat's that was one: its memory and its picture)
                 self.senders_path(s.id)]  # (who its messages were from, with them)
        pi = os.path.expanduser("~/.local/share/baude/pi/sessions")  # (pi-claude's own: its session's, its id in its
        if program == "pi" and os.path.isdir(pi):                    # name, as pi has it: <when>_<id>.jsonl)
            paths += [os.path.join(pi, n) for n in sorted(os.listdir(pi)) if any(i in n for i in names)]
        went = []
        for p in paths:
            if p and os.path.exists(p):
                try:
                    went.append(trash(p))
                except OSError as e:
                    with self.lock:
                        s.deleting = False
                    if went:
                        return reply("error", error=wording.say("host_errors.trash_failed_some", path=p, why=e,
                                                                moved=", ".join(went)))
                    return reply("error", error=wording.say("host_errors.trash_failed", path=p, why=e))
        with self.lock:
            s.deleted = True
            if s.app_tools is not None:
                s.app_tools.end()
            s.meta.pop("cut", None)
            self.sessions.pop(s.id, None)
            try:
                os.remove(os.path.join(self.state_dir, "sessions", f"{s.id}.json"))
            except OSError:
                pass
            for c in list(s.viewers):  # (attached to it: it's gone, for good)
                c.send({"op": "deleted", "session": s.id})
            self.log(f"deleted {s.id}")
            self.changed()
        reply("deleted", session=s.id, trashed=went)

    def drop_ephemeral(self, s, later=False, meant=False):
        """(lock held) A side question's session (ephemeral), or a new chat never written in (draft), with nobody
        attached: gone, its claude too (it wrote nothing: --no-session-persistence, or nothing sent). Not at once: a
        head attaches again as it starts its claude again (/cd, a restart of its own), or attaches for the first time
        just after it's made it (a side question's): once nobody's been on it for DRAFT_S (a side question that had a
        head and lost it: its grace, or EPHEMERAL_S; meant, its head detached on purpose: EPHEMERAL_S)."""
        if not (s.meta.get("ephemeral") or s.meta.get("draft")) or s.viewers or self.sessions.get(s.id) is not s:
            return
        if not later:
            if s.drop_timer is not None:
                s.drop_timer.cancel()
            wait = (EPHEMERAL_S if meant else s.meta.get("grace") or EPHEMERAL_S) if s.meta.get("ephemeral") and \
                s.attached_once else DRAFT_S
            s.drop_timer = threading.Timer(wait, lambda: self._drop_later(s))
            s.drop_timer.daemon = True
            s.drop_timer.start()
            return
        s.drop_timer = None
        del self.sessions[s.id]
        if s.app_tools is not None:  # (its head's own tools with it)
            s.app_tools.end()
        if s.proc is not None:
            s.proc.stopping = True
            s.proc.kill()

    def _drop_later(self, s):
        with self.lock:
            self.drop_ephemeral(s, later=True)
            self.changed()

    def save(self, s):
        # (one deleted, a side question's, or a new chat's not written in yet: not kept)
        if not s.deleted and not s.meta.get("ephemeral") and not s.meta.get("draft"):
            _write_json(os.path.join(self.state_dir, "sessions", f"{s.id}.json"), s.meta)

    def listing(self, archived=False):
        """Its sessions, newest first; those archived only if asked for (archived); never a side question's
        (ephemeral). A new chat not written in yet is, as draft (a head leaves it out of its lists)."""
        with self.lock:
            return sorted((s.listing() for s in self.sessions.values()
                           if (archived or not s.meta.get("archived")) and not s.meta.get("ephemeral")),
                          key=lambda i: -(i["said"] or 0))

    # ---- running
    def serve_forever(self):
        """Take connections till it's shut down."""
        threading.Thread(target=self._watch, daemon=True, name="watch").start()
        threading.Thread(target=self._emissions, daemon=True, name="emissions").start()
        self.mesh.start()
        with self.lock:  # (waits its agents were at as it last stopped, which stopped them: answered for them)
            waits = [(s, w) for s in self.sessions.values() for w in s.meta.get("waits_on") or []]
        for s, w in waits:
            threading.Thread(target=self.answer_wait, args=(s, w), daemon=True, name="wait").start()
        self.log(f"serving {len(self.sessions)} sessions on {self.socket_path}, group {self.me['group']!r}"
                 + (f", commit {self.commit[:12]}" if self.commit else ""))
        while not self.stopped.is_set():
            listener = self.listener
            try:
                ready, _, _ = select.select([listener, self.wake_r], [], [])  # (nothing done till there's something)
                if self.wake_r in ready:
                    os.read(self.wake_r, 64)
                    continue
                sock, _ = listener.accept()
            except BlockingIOError:
                continue  # (gone before it was taken)
            except (OSError, ValueError):
                if not self.stopped.is_set() and listener is self.listener:
                    time.sleep(0.1)  # (out of file descriptors, say: not a loop that spins)
                continue
            if self.stopped.is_set():
                sock.close()
                break
            sock.setblocking(True)  # (macOS's accept hands on the listener's non-blocking)
            self.accepted = time.monotonic()
            threading.Thread(target=self.serve, args=(sock,), daemon=True, name="client").start()
        self.listener.close()

    def answer_wait(self, s, w):
        """A wait of a session's agent (waits_on's: a subagent's answer, or a chat's) that this host's stop cut short:
        followed till it's over (meshtools.Mesh.follow: the answer, or what's become of it), and that held for the
        session (held: what its agent's sent, told, and what heads show, shown), from that chat, till a head sends it
        or drops it: its claude isn't started for it. Stopped again meanwhile: kept, for the next start."""
        with self.lock:
            self.following.add((s.id, w["session"]))
        try:
            got = meshtools.Mesh(s.id, self.socket_path).follow(w)  # (as from this chat: what it asked, if it's sent
            # again, is from it, its relay and its input's as)
        except Exception as e:  # (said to the session as the call would have said it, its host named)
            got = {"session": w["session"], "answer": None, "error": f"{type(e).__name__}: {e}",
                   **meshtools.Mesh(socket=self.socket_path).named(
                       {"via": "mesh", "host": w["host"]} if w.get("host") else {"via": "local"})}
        with self.lock:
            self.following.discard((s.id, w["session"]))
            if self.stopped.is_set() or self.sessions.get(s.id) is not s or s.deleting:
                return
            waits = [x for x in s.meta.get("waits_on") or [] if x["session"] != w["session"]]
            s.meta["waits_on"] = waits or None
            host = got.get("host") or w.get("host") or self.me["id"]
            named = self.mesh.name_of(host)  # (its name as this host has it, if the answer didn't say)
            sender = wire.clean_sender({"kind": "chat" if w.get("kind") == "chat" else "subagent",
                                        "name": w.get("title"), "host": host, "session": w["session"],
                                        "host_name": got.get("host_name") or (named if named != host[:8] else None)})
            s.meta["held"] = (s.meta.get("held") or []) + [{
                "id": str(uuid.uuid4()), "text": meshtools.told(w, got), "shown": meshtools.shown(got),
                "sender": sender, "at": time.time()}]
            self.save(s)
            self.log(f"{s.id}: the answer it waited on from {w['session']} held for it")
            self.changed()

    def drop_waits(self, s):
        """(lock held) Its agent's waits (waits_on) let go of, but those answered for it (answer_wait): its turn's
        over, or its claude's gone otherwise than by this host's stop (stopped by a head, say), so nothing waits on
        them."""
        waits = s.meta.get("waits_on") or []
        kept = [w for w in waits if (s.id, w["session"]) in self.following]
        if len(kept) != len(waits):
            s.meta["waits_on"] = kept or None
            self.save(s)

    def shutdown(self, if_idle=False, restart=None):
        """Goodbye on the network, every claude stopped (asked to end, then made to: what it's written is kept, and
        resumed next time; one at a turn or asking marked cut, as that's cut short), the socket let go. Once. if_idle:
        not while a command's running (Session.commands: those are what's returned, [{session, what}], and nothing's
        done); else None, once it's done. restart: the commit it's to start again on, once it's shut down
        (restarting: baude_headless.py's serve does)."""
        busy, procs = self.halt(if_idle, restart)
        if procs is not None:
            self.stop_procs(procs)
        return busy

    def halt(self, if_idle=False, restart=None):
        """(shutdown's first half, all at once, so nothing starts between the look and the stop) Nothing more taken,
        unless (if_idle) a command's running; every session whose claude is at a turn or asking marked cut, and kept
        so: (busy, the claudes to stop). Those: None if it's not stopping (busy) or stopping already."""
        with self.lock:
            if self.stopped.is_set():
                if not if_idle:  # (stopping for an update, and told to stop: it goes, and doesn't start again)
                    self.restarting = None
                    if self.quitting is not None and self.quitting["restart"]:
                        self.quitting = {**self.quitting, "restart": False}
                        self.said_quitting()
                return None, None
            if if_idle and (busy := self.running()):
                return busy, None
            self.stopped.set()
            self.restarting = restart
            procs = []
            for s in self.sessions.values():
                if s.app_tools is not None:  # (the host going: its heads' tools let go of)
                    s.app_tools.end()
                if s.proc is None:
                    continue
                if s.busy or s.asks:  # (cut short: a head offers to carry it on)
                    self.cut(s)
                    if not s.asks:
                        self.cutting.add(s.id)
                procs.append(s.proc)
        return None, procs

    def said_quitting(self):
        """(lock held) How far along stopping it is (quitting), to the heads attached and the menu bar's item."""
        q = self.quitting
        for c in list(self.conns):
            c.send({"op": "stopping", "procs": q["total"], "left": q["left"], "restart": q["restart"]})
        self.on_change()

    def cut(self, s):
        """(lock held) A session's turn marked cut short by this host's stop (listed: cut), and kept so."""
        s.meta["cut"] = time.time()
        self.save(s)

    def stop_procs(self, procs, again=False):
        """(shutdown's second half, once halt's stopped it) Its connections, claudes and socket let go of. How far
        along, kept (quitting) and said as it goes, each claude that's stopped (said_quitting): in the menu bar's item
        and to the heads attached, whose connections are let go of once the last has. again: it's to start again (a
        head's restart of it, or an update's)."""
        self.woken.set()
        try:  # (one starting meanwhile waits for this one to go: Server)
            open(self.state_dir + "/.stopping", "w").close()
        except OSError:
            pass
        self.mesh.stop()
        for run in list(self.agent_runs.values()):  # (agents run here for chats elsewhere: stopped, their folders gone)
            run.end()
        with self.lock:
            procs = procs + [s.proc for s in self.sessions.values() if s.proc is not None and s.proc not in procs]
            self.quitting = {"left": len(procs), "total": len(procs), "restart": bool(again or self.restarting)}
            self.said_quitting()
        for p in procs:
            p.stopping = True
            p.close_stdin()  # (claude, its stdin closed, ends as it would at the end of a pipe)
        end = time.monotonic() + 5
        for p in procs:
            try:
                p.p.wait(timeout=max(0.1, end - time.monotonic()))
            except Exception:  # (one that doesn't, in time: made to)
                p.kill()
            with self.lock:
                self.quitting = {**self.quitting, "left": self.quitting["left"] - 1}
                self.said_quitting()
        while time.monotonic() < end + 2:  # (what each said last, read and kept: its exit's handled once it's all read)
            with self.lock:
                if not any(s.proc in procs for s in self.sessions.values()):
                    break
            time.sleep(0.05)
        with self.lock:
            conns = list(self.conns)
        for c in conns:  # (what it's said written first: the process may end the moment it's closed)
            c.close(flush=1)
        os.write(self.wake_w, b"x")  # (the accept loop sees it's stopped)
        try:
            if os.stat(self.socket_path).st_ino == self.bound:
                os.unlink(self.socket_path)
        except OSError:
            pass
        os.close(self.lock_file)
        self.closed.set()

    def serve(self, sock, origin=None, sender=None):
        """A connection served (a tunnel's through the mesh: origin, the member it came from; sender, who what's sent
        through it is from, as that member said: Conn's)."""
        sock.settimeout(wire.SILENT_S)  # (a client that's said nothing for this long is gone: they ping)
        ch = wire.Channel.over(sock)
        ch.serving = True
        conn = Conn(ch, origin, sender)
        with self.lock:
            self.conns.add(conn)
        self.woken.set()  # (someone's here: watched for them)
        try:
            for msg in ch:
                if msg.get("op") in ("join", "relay") and not self.stopped.is_set():  # (another host's link to
                    # here, or a tunnel through the mesh: the connection's the mesh's from now on; one whose fields
                    # aren't as the schema has them refused, as any request is)
                    if (refused := wire.refusal(msg)) is not None:
                        conn.send(refused)
                        continue
                    with self.lock:
                        self.conns.discard(conn)
                    conn.close()
                    (self.mesh.take if msg["op"] == "join" else self.mesh.relay)(ch, msg)
                    break
                try:
                    self.handle(conn, msg)
                except worktree.Gone as e:  # (a chat's folder gone: what to do, said as it is)
                    conn.send({"op": "error", "req": msg.get("req"), "error": str(e), "plain": True,
                               "gone": {"why": e.why, "folder": e.folder}})
                except Exception as e:  # (one bad request doesn't end the connection)
                    conn.send({"op": "error", "req": msg.get("req"), "error": f"{type(e).__name__}: {e}"})
        finally:
            if getattr(conn, "agent_run", None) is not None:  # (its caller gone: the agent with it)
                conn.agent_run.end()
            if getattr(conn, "agent_relay", None) is not None:
                conn.agent_relay[0].relay_gone(conn.agent_relay[1])
            for tools in list(getattr(conn, "app_tools", ())):  # (its chats' tools that were its own: waiting for it
                tools.unbind(conn)                               # to attach again, which binds them to it then)
            conn.gone = True  # (a relay starting meanwhile: let go of as it would be)
            self.phones.gone(conn)
            if getattr(conn, "emission_watch", None) is not None:  # (its pane gone: Emission's watcher with it)
                try:
                    conn.emission_watch.shutdown(socket.SHUT_RDWR)  # (its recv woken, Emission told)
                    conn.emission_watch.close()
                except OSError:
                    pass
            with self.lock:
                self.conns.discard(conn)
                for s in list(self.sessions.values()):
                    s.viewers.discard(conn)
                    s.live.discard(conn)
                    s.waiting_init = [w for w in s.waiting_init if w[0] is not conn]
                    s.routes = {k: v for k, v in s.routes.items() if v[0] is not conn}
                    self.drop_ephemeral(s)
            conn.close()
            ch.close()
            if conn.subscribed:
                self.heads_look()

    def emission_watch(self, conn, req, bot, msg):
        """A Bot's Emission here (started if it isn't) watched for conn: its frames (as JPEG) and state, a message
        each (emission_frame), till either end goes (emission_watch_end)."""
        size = {k: int(msg[k]) for k in ("max_w", "max_h") if isinstance(msg.get(k), int)}
        try:
            emissionlink.call_bot(bot["id"], bot.get("name"), {"op": "info"}, timeout=emissiontools.HOST_WAIT_S)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(emissionlink.profile_socket(emissionlink.bot_profile(bot["id"], None)))
            sock.sendall((json.dumps({"op": "watch", **size}) + "\n").encode())
        except (OSError, ValueError) as e:
            conn.send({"op": "error", "req": req, "error": str(e)})
            return
        conn.emission_watch = sock
        if getattr(conn, "gone", False):  # (its pane went while Emission was started)
            sock.close()
            return
        conn.send({"op": "emission_watching", "req": req})
        buffer = b""
        try:
            while (part := sock.recv(1 << 20)):
                buffer += part
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        said = json.loads(line)  # (Emission's line: what of it the schema's frame has)
                        conn.send({"op": "emission_frame", "req": req,
                                   **{k: v for k, v in said.items() if k in FRAME_KEYS}})
                    except ValueError:
                        continue
        except OSError:
            pass
        finally:
            conn.emission_watch = None
            try:
                sock.close()
            except OSError:
                pass
            conn.send({"op": "emission_watch_end", "req": req})

    def feedback(self, msg, reply):
        """A head's feedback (a phone's, which has no gh) filed as an issue by this host's gh, where Baude's Send
        Feedback files it (fixes.submit_issue_target): titled by its first line, its context under it, its note's id
        (note) in its last line, so a try that's asked again finds the issue it made rather than making another.
        check: where it would be filed, nothing filed."""
        import fixes  # (no GTK in it: feedback.py's own has)
        repo = fixes.submit_issue_target()
        if repo is None:
            return reply("error", error=wording.say("host_errors.feedback_no_target"))
        if msg.get("check"):
            return reply("feedback_target", repo=repo)
        text, note = msg.get("text"), msg.get("note")
        context = [c for c in msg.get("context") or [] if isinstance(c, str)] if isinstance(msg.get("context"), list) else []
        if not isinstance(text, str) or not text.strip() or not wire.is_id(note or ""):
            return reply("error", error="feedback needs its text and its note's id")
        first = next((t for line in text.splitlines() if (t := line.strip().lstrip("#").strip())), "Feedback")
        title = first if len(first) <= 90 else first[:89].rstrip() + "…"
        token = f"`baude-note-{note}`"  # (the note's own: an issue ending with it is the note's, whoever filed it)
        mark = f"_Sent from Baude for Android's Send Feedback, through {self.me['name']}_ {token}"
        with self.feedback_lock:  # (one filing a note at a time: another for it waits, and has what it made)
            claim = self.feedback_claims.get(note)
            mine = claim is None
            if mine:
                claim = self.feedback_claims[note] = threading.Event()
        if not mine:
            claim.wait(120)
        if note in self.feedback_filed:
            if mine:
                self._unclaim(note, claim)
            return reply("feedback_filed", repo=repo, **self.feedback_filed[note])
        if not mine:
            return reply("error", error=wording.say("host_errors.feedback_filing"))
        try:
            self._file_feedback(fixes, note, text, context, title, mark, token, repo, reply)
        finally:
            self._unclaim(note, claim)

    def _unclaim(self, note, claim):
        with self.feedback_lock:
            self.feedback_claims.pop(note, None)
        claim.set()

    def _file_feedback(self, fixes, note, text, context, title, mark, token, repo, reply):
        """(the note claimed) Filed, unless an issue of its own is there already (found by its id)."""
        body = text.strip()[:60000] + ("\n\n## Context\n" + "\n".join(f"- {c[:300]}" for c in context[:20]) if context
                                        else "") + "\n\n" + mark + "\n"
        ok, out = fixes.gh("issue", "list", "--repo", repo, "--state", "all", "--limit", "5", "--search",
                           f'"baude-note-{note}" in:body', "--json", "number,url,body")
        if not ok:
            return reply("error", error=out or wording.say("host_errors.gh_list_failed"))
        try:
            made = next((i for i in json.loads(out or "[]") if (i.get("body") or "").rstrip().endswith(token)), None)
        except ValueError:
            made = None
        if made is None:
            ok, out = fixes.gh("issue", "create", "--repo", repo, "--title", title, "--body-file", "-", body=body)
            url = next((line for line in out.splitlines() if line.startswith("https://")), None) if ok else None
            if url is None:
                return reply("error", error=out or wording.say("host_errors.gh_create_failed"))
            number = int(m.group(1)) if (m := re.search(r"/issues/(\d+)", url)) else None
            made = {"url": url, "number": number}
        with self.feedback_lock:
            self.feedback_filed[note] = {"url": made["url"], "number": made.get("number")}
            _write_json(os.path.join(self.state_dir, "feedback-filed.json"), self.feedback_filed)
        self.log(f"feedback {note} filed on {repo}: {made['url']}")
        reply("feedback_filed", url=made["url"], number=made.get("number"), repo=repo)

    def handle(self, conn, msg):
        op, req = msg.get("op"), msg.get("req")
        reply = lambda kind, **kw: conn.send({"op": kind, "req": req, **kw})  # noqa: E731
        if op == "ping":
            return None
        if (refused := wire.refusal(msg)) is not None:  # (an op no host takes, or fields not as the schema has them)
            return conn.send(refused)
        if self.stopped.is_set():  # (going: nothing more started, nothing more sent to a claude it's stopping)
            if self.quitting is not None:  # (its connections kept till its claudes have stopped, to say how far along:
                return None  # nothing answered, as an answer would be taken for the turn's end; theirs, as it closes)
            return reply("error", error="the host process is stopping")
        if op == "hello":
            head = msg.get("commit")  # (the head's code: one on other code than this, said in the log, once each)
            if isinstance(head, str) and self.commit and head != self.commit and head not in self.heads_apart:
                self.heads_apart.add(head)
                self.log(f"a head on {head[:12]} connected; this is on {self.commit[:12]}")
            if msg.get("client") in APPS:  # (listed as connected here once it subscribes, if it's on this machine)
                conn.head = {"client": msg["client"][:32], "commit": mesh.versions_of({"commit": head})["commit"],
                             "here": msg.get("here") is True}
            return reply("hello", server=self.info())
        if op == "subscribe":
            if not conn.subscribed:  # (a head's come: this machine's model providers looked at again, for it)
                threading.Thread(target=self.mesh.refresh, daemon=True, name="providers").start()
            was, conn.subscribed, conn.archived = conn.subscribed, True, bool(msg.get("archived"))
            if not was:
                self.heads_look()
            with self.lock:  # (as they are now: nothing's been read while nobody was attached)
                for s in self.sessions.values():
                    self.rescan(s)
            return reply("sessions", sessions=self.listing(conn.archived), server=self.info())
        if op == "quit":  # (the host process itself: its claudes stopped, its sessions kept for next time; a turn
            # going on cut short, and marked so)
            with self.lock:  # (if_idle, a head's restart of it: not while a command's running, which it would end)
                busy, procs = self.halt(bool(msg.get("if_idle")))
                if busy:
                    return reply("busy", sessions=sorted({b["session"] for b in busy}), commands=busy)
                reply("quitting")
            if procs is not None:
                threading.Thread(target=self.stop_procs, args=(procs, bool(msg.get("restart"))), daemon=True,
                                 name="quit").start()
            return None
        if op == "start":
            return self.start(msg, conn, req)
        if op == "read_file":
            return reply("file", **self.read_file(msg.get("path"), msg.get("limit")))
        if op == "task_output":
            return reply("file", **self.task_output(msg.get("session"), msg.get("path"), msg.get("limit")))
        if op == "notes":  # (Record's notebooks here, for another machine's Record: listed, or changed; one at a time,
            self.notes_ops.put((msg, reply))  # in the order asked, on a thread of their own: _notes)
            return None
        if op == "catalogue":  # (on a thread of its own: it waits for the catalogue to be made)
            def catalogue():
                try:
                    reply("catalogue", **self.catalogue(msg.get("cwd"), bool(msg.get("wait"))))
                except Exception as e:  # noqa: BLE001 (git or kubectl gone wrong: said, and asked again later)
                    reply("error", error=f"{type(e).__name__}: {e}")
            threading.Thread(target=catalogue, daemon=True, name="catalogue").start()
            return None
        if op in ("list_dir", "git", "signal_names", "fixes"):  # (a session's folder read for a head elsewhere: hostfiles; on a
            def read():                                  # thread of its own, as git may take a while)
                try:
                    if op == "list_dir":
                        reply("dir", **hostfiles.list_dir(self, msg.get("path")))
                    elif op == "fixes":
                        reply(op, **hostfixes.answer(self, msg))
                    else:
                        reply(op, **(hostfiles.git if op == "git" else hostfiles.signal_names)(self, msg))
                except (ValueError, OSError) as e:
                    reply("error", error=str(e))
                except Exception as e:  # noqa: BLE001 (anything else gone wrong: still answered)
                    reply("error", error=f"{type(e).__name__}: {e}")
            threading.Thread(target=read, daemon=True, name=op).start()
            return None
        if op == "feedback":  # (a head with no gh of its own: its feedback filed by this host's; on a thread of its own)
            def feedback():
                try:
                    self.feedback(msg, reply)
                except Exception as e:  # noqa: BLE001 (gh gone wrong: said)
                    reply("error", error=f"{type(e).__name__}: {e}")
            threading.Thread(target=feedback, daemon=True, name="feedback").start()
            return None
        if op == "link":  # (a head's: a link made from here)
            where = msg.get("where")
            self.mesh.link(where)
            return reply("linking", where=where)
        if op == "unlink":
            return reply("unlinked", any=self.mesh.unlink(msg.get("where") if isinstance(msg.get("where"), dict)
                                                          else None, msg.get("id")))
        if op == "infer":
            return self.mesh.infer(conn, msg)
        if op == "files":
            return self.mesh.files(conn, msg)
        if op == "agent":  # (a model of this machine's, run here as an agent for a chat elsewhere: agentproxy)
            if getattr(conn, "agent_run", None) is not None:
                return reply("error", error="this connection runs an agent already")
            run = agentproxy.Run(self, conn, msg)
            with self.lock:
                self.agent_runs[run.id] = run
            conn.agent_run = run
            return reply("agent_started", run=run.id)
        if op in ("mcp", "mcp_close") and msg.get("conn") in getattr(conn, "tool_relays", {}):  # (an app's tools')
            return conn.tool_relays[msg["conn"]].take(msg)
        if op in ("in", "close", "kill", "mcp", "mcp_close") and getattr(conn, "agent_run", None) is not None:
            return conn.agent_run.take(msg)
        if op == "emission":  # (a Bot's browser tools: its own Emission profile here, started for it if it isn't
            bot = msg.get("bot") if isinstance(msg.get("bot"), dict) else {}  # running; on a thread of its own)
            if not isinstance(bot.get("id"), str) or not bot["id"] or not isinstance(msg.get("call"), dict):
                return reply("error", error="emission needs a Bot (its id) and a call")
            if msg["call"].get("op") not in emissiontools.HOST_OPS:  # (the tools' requests only: no quit, open, …)
                return reply("error", error=f"emission takes the tools' requests, not {msg['call'].get('op')!r}")

            def browse():
                try:
                    if self.phones.phone(bot["id"]) is not None:  # (its browser on a phone now: asked there)
                        return reply("emission", result=self.phones.call(bot, msg["call"]))
                    reply("emission", result=emissionlink.call_bot(bot["id"], bot.get("name"), msg["call"],
                                                                   timeout=emissiontools.HOST_WAIT_S))
                except (OSError, ValueError) as e:
                    reply("error", error=str(e))
            threading.Thread(target=browse, daemon=True, name="emission").start()
            return None
        if op == "emission_watch":  # (a Browser pane elsewhere: a Bot's Emission here watched, its frames carried on
            bot = msg.get("bot") if isinstance(msg.get("bot"), dict) else {}  # this connection; one a connection)
            if not isinstance(bot.get("id"), str) or not bot["id"] or getattr(conn, "emission_watch", None):
                return reply("error", error="emission_watch needs a Bot (its id), one on a connection")
            if self.phones.phone(bot["id"]) is not None:
                return reply("error", error=wording.say("host_errors.browser_on_phone"))
            threading.Thread(target=self.emission_watch, args=(conn, req, bot, msg), daemon=True,
                             name="emission-watch").start()
            return None
        if op == "emission_serve":  # (a phone showing a Bot's browser, or not now: phonebrowser)
            return self.phones.serve(conn, msg, reply)
        if op == "emission_answer":  # (a phone's answer to an emission_call)
            return self.phones.answer(conn, msg)
        if op == "emission_watch_say":  # (what the pane says back: a frame taken, its new size)
            line = msg.get("line") if isinstance(msg.get("line"), dict) else {}
            line = {k: line[k] for k in ("ack", "max_w", "max_h") if isinstance(line.get(k), int)}
            sock = getattr(conn, "emission_watch", None)
            if sock is not None and line:
                try:
                    sock.sendall((json.dumps(line) + "\n").encode())
                except OSError:
                    pass
            return None
        if op == "mcp_relay":  # (one of an agent's MCP servers: a relay, carried to its caller)
            run = self.agent_runs.get(msg.get("run"))
            if run is None or run.relay(conn, msg) is None:
                return reply("error", error="no such agent here")
            return reply("relaying")
        if op == "mcp_line" and getattr(conn, "agent_relay", None) is not None:
            return conn.agent_relay[0].relay_line(conn.agent_relay[1], msg.get("line"))
        if op in ("deactivate", "activate"):  # (a member, by its id or where a link from here goes: mesh.deactivate)
            where = msg.get("where") if wire.is_where(msg.get("where")) else None
            done = (self.mesh.deactivate if op == "deactivate" else self.mesh.activate)(msg.get("id"), where)
            return reply(op + "d", any=done)
        if op == "input" and self.makes_folder(msg.get("msg") or {}) or op == "held" and msg.get("send"):
            self.restore_folder(conn, msg.get("session"))  # (its worktree removed: made again first, not under the
            # lock, which git would hold up)
        with self.lock:
            if self.stopped.is_set():  # (it stopped as this waited for the lock: halt's)
                return reply("error", error="the host process is stopping")
            s = self.sessions.get(msg.get("session"))
            if s is None:
                return reply("error", error=f"no session {msg.get('session')}")
            if s.deleting:
                return reply("error", error=wording.say("host_errors.being_deleted", session=s.id))
            if op == "attach":
                conn.answers = msg.get("answers", True) is not False  # (false: it won't answer what's asked)
                s.attached_once = True
                if s.drop_timer is not None:  # (a head back on it: not dropped after all)
                    s.drop_timer.cancel()
                    s.drop_timer = None
                if isinstance(msg.get("always_allow"), list):  # (tools a head always allows: added to those allowed
                    s.always_allow |= {t for t in msg["always_allow"] if isinstance(t, str)}  # here, kept in memory)
                    self.changed()
                if isinstance(msg.get("env"), dict):  # (a head here: its claude's environment, from its next start)
                    s.env = clean_env(msg["env"])
                s.viewers.add(conn)
                if msg.get("live"):
                    s.live.add(conn)
                if msg.get("app_tools") and s.app_tools is not None:  # (its head back: its tools carried to it again)
                    s.app_tools.bind(conn)
                return reply("attached", session=s.listing(), asks=list(s.asks.values()))
            if op == "detach":  # (meant: an ephemeral session its head's done with dropped at once, not after its grace)
                s.viewers.discard(conn)
                s.live.discard(conn)
                self.drop_ephemeral(s, meant=True)
                return None
            if op == "input":
                self.take_input(conn, s, msg.get("msg") or {}, self.sent_by(conn, msg))
                return reply("accepted", session=s.id) if req is not None else None
            if op == "stop":  # (restart: started again with the next message, those attached not told it went)
                if s.proc is not None:
                    s.proc.stopping = True
                    s.proc.restarting = bool(msg.get("restart"))
                    s.proc.kill()
                return reply("stopped", session=s.id)
            if op == "always_allow":  # (tools a head always allows: allowed here as claude asks, with or without it;
                if isinstance(msg.get("add"), list):  # add: these too, tools: these in place of all there were)
                    s.always_allow |= {t for t in msg["add"] if isinstance(t, str)}
                else:
                    s.always_allow = {t for t in msg.get("tools") or [] if isinstance(t, str)}
                self.changed()
                return reply("allowed", session=s.id, tools=sorted(s.always_allow))
            if op == "set_cwd":  # (/cd: claude carries on there, from its next message: started again in it)
                cwd = os.path.realpath(os.path.expanduser(msg.get("cwd") or ""))
                if not os.path.isdir(cwd):
                    return reply("error", error=wording.say("host_errors.not_a_folder", path=cwd))
                if s.busy:
                    return reply("error", error=wording.say("host_errors.at_a_turn"))
                s.meta["cwd"] = cwd
                self.save(s)
                if s.proc is not None:  # (started again in it with the next message: those attached go on)
                    s.proc.stopping = s.proc.restarting = True
                    s.proc.close_stdin()
                self.changed()
                return reply("cwd_set", session=s.id, cwd=cwd)
            if op in ("archive", "unarchive"):  # (archived: its claude stopped, and listed only if asked for)
                if op == "archive" and s.proc is not None:
                    s.proc.stopping = True
                    s.proc.kill()
                s.meta["archived"] = op == "archive"
                self.save(s)
                self.changed()
                return reply(op + "d", session=s.id)
            if op == "delete":  # (on a thread of its own: its claude's waited for, its files moved, not under the lock)
                s.deleting = True
                threading.Thread(target=self.delete, args=(s, reply), daemon=True, name="delete").start()
                return None
            if op == "senders":
                if (have := self.senders(s)) is None:
                    return reply("error", error=wording.say("host_errors.senders_unreadable"))
                return reply("senders", session=s.id, senders=dict(have), rev=s.meta.get("senders") or 0)
            if op == "transcript":
                return reply("transcript", session=s.id, **self.read_transcript(s, msg.get("offset") or 0,
                                                                               msg.get("limit")))
            if op == "subagents":
                return reply("subagents", session=s.id, **self.read_subagents(s, msg.get("have"), msg.get("limit")))
            if op == "rename":
                return self.rename(s, msg.get("title"), lambda: reply("renamed", session=s.listing()))
            if op == "bot":  # (made a Bot, renamed or its picture changed; or, bot None, a chat again)
                return reply("bot_set", session=self.set_bot(s, msg.get("bot")))
            if op == "held":  # (a message held for it: dropped, or sent as its user's input would be, and dropped then,
                # at once: kept if it can't be)
                held = s.meta.get("held") or []
                h = next((h for h in held if h.get("id") == msg.get("id")), None)
                if h is None:
                    return reply("error", error=wording.say("host_errors.no_held"))
                if msg.get("send"):
                    self.take_input(conn, s, {"type": "user", "message": {"role": "user", "content": h["text"]}},
                                    h.get("sender"), h["shown"] if isinstance(h.get("shown"), str) else
                                    meshtools.shown_of_told(h.get("text") or ""))
                s.meta["held"] = [x for x in held if x is not h] or None
                self.save(s)
                self.changed()
                return reply("held", session=s.id)
            if op == "waits_on":  # (its agent waits on another chat's answer, or no longer: kept, for a restart)
                on = msg.get("on") if isinstance(msg.get("on"), dict) else {}
                if not wire.is_id(on.get("session") or "") or on.get("host") is not None and not wire.is_id(on["host"]):
                    return reply("error", error="waits_on needs on: {session, host (None: this one)}")
                waits = [w for w in s.meta.get("waits_on") or [] if w["session"] != on["session"]]
                if not msg.get("done"):
                    waits.append({"session": on["session"], "host": on.get("host"),
                                  "kind": "chat" if on.get("kind") == "chat" else "subagent",
                                  "text": on["text"] if isinstance(on.get("text"), str) else None,
                                  "offset": on["offset"] if isinstance(on.get("offset"), int) else 0,
                                  "title": on["title"][:200] if isinstance(on.get("title"), str) else None,
                                  "until": float(on["until"]) if isinstance(on.get("until"), (int, float)) else 0})
                s.meta["waits_on"] = waits or None
                self.save(s)
                return reply("waits_on", session=s.id)
            if op == "mesh_tools":  # (a chat given the mesh's tools, or not: from its next start)
                s.meta["mesh_tools"] = bool(msg.get("on"))
                self.save(s)
                self.renew_agent(s)
                self.changed()
                return reply("mesh_tools_set", session=s.listing())
            if op == "bot_picture":
                return reply("bot_picture", session=s.id, picture=self.bot_picture(s))
            if op == "routines":  # (a Bot's routines: read; or one set (made, or changed by its id), or deleted (by its
                have = list(s.meta.get("routines") or [])  # id); each set checked, its next run worked out)
                if "set" in msg or "delete" in msg:
                    if not s.meta.get("bot"):
                        return reply("error", error=wording.say("host_errors.routines_bot_only"))
                    if "delete" in msg:
                        have = [r for r in have if r["id"] != msg["delete"]]
                    else:
                        given = msg["set"] if isinstance(msg["set"], dict) else None
                        old = next((r for r in have if given and r["id"] == given.get("id")), None)
                        if given is None or (old is None and len(have) >= 50):
                            return reply("error", error=wording.say("host_errors.routine_limit"))
                        try:
                            made = routines.clean(given, old)
                        except (ValueError, TypeError) as e:
                            return reply("error", error=str(e))
                        have = [made if r["id"] == made["id"] else r for r in have] if old else have + [made]
                    s.meta["routines"] = have
                    self.save(s)
                    self.changed()
                    self.woken.set()  # (the next due may be sooner: the watch told)
                return reply("routines", session=s.id, routines=have, clock=routines.clock())
            if op == "run_routine":  # (one of a Bot's routines run now, its schedule as it was)
                r = next((r for r in s.meta.get("routines") or [] if r["id"] == msg.get("id")), None)
                if r is None or not s.meta.get("bot") or s.meta.get("archived"):
                    return reply("error", error=wording.say("host_errors.no_routine"))
                self.run_routine(s, r, schedule=False)
                return reply("routines", session=s.id, routines=s.meta.get("routines") or [],
                             clock=routines.clock())
            if op == "memory":  # (a Bot's memory: read, or, text given, written; refused if the Bot's written it since
                # base_mtime, with what it wrote)
                mem = self.bot_memory(s, msg.get("text"), msg.get("seen"),
                                      **({"base_mtime": msg["base_mtime"]} if "base_mtime" in msg else {}))
                if "refused" in mem:
                    return reply("error", error=mem.pop("refused"), session=s.id, **mem)
                return reply("memory", session=s.id, **mem)
        self.log(f"refused: no op {op!r} here")  # (one the schema has that no host takes here: a reply's, say)
        return conn.send(wire.refused(wire.UNSUPPORTED, req, f"no op {op!r} here"))

    def run_routine(self, s, r, schedule=True):
        """(under the lock) A routine's run: its Bot given a turn with what to do, as if it were said (one asked for
        mid-turn taken after it); its next run worked out from now (schedule), or left as it was (run now). One
        sent and not yet taken (its Bot still on a turn from before) isn't sent again: the run's skipped."""
        now = time.time()
        if r["id"] in s.routines_out and s.busy:
            error = wording.say("host_errors.routine_skipped")
        else:
            error = None
            self.take_input(None, s, {"type": "user", "uuid": str(uuid.uuid4()),
                                      "message": {"role": "user", "content": routines.said(r, now)}})
            s.routines_out.add(r["id"])
        s.meta["routines"] = [(routines.ran(x, now, error) if schedule else {**x, "last": now})
                              if x["id"] == r["id"] else x for x in s.meta.get("routines") or []]
        self.save(s)
        self.log(f"routine {r['name']!r} of {s.id}: " + (error or "run"))

    def routines_due(self):
        """Routines due now run (each Bot's that are, not archived, not being deleted, the host not stopping): each
        one's folder made again first if it's gone, as for a message; when the next one's due (seconds from now), or
        None if there's none to run."""
        now, soonest, due = time.time(), None, []
        with self.lock:
            if self.stopped.is_set():
                return None
            for s in list(self.sessions.values()):
                if not s.meta.get("bot") or s.meta.get("archived") or s.deleting:
                    continue
                for r in s.meta.get("routines") or []:
                    if routines.due(r, now):
                        due.append((s.id, r["id"]))
                    elif not r.get("paused") and r.get("next") is not None:
                        soonest = r["next"] - now if soonest is None else min(soonest, r["next"] - now)
        for sid, _rid in due:  # (not under the lock: git)
            self.restore_folder(None, sid)
        with self.lock:
            for sid, rid in due:
                s = self.sessions.get(sid)
                if s is None or s.deleting or self.stopped.is_set():
                    continue
                r = next((x for x in s.meta.get("routines") or [] if x["id"] == rid), None)
                if r is None or not routines.due(r, time.time()):
                    continue
                try:
                    self.run_routine(s, r)
                except Exception as e:  # noqa: BLE001 (said, on the page too, and tried again at its next time)
                    self.log(f"routine {r.get('name')!r} of {s.id} didn't run: {type(e).__name__}: {e}")
                    s.meta["routines"] = [routines.ran(x, time.time(), f"didn't run: {e}") if x["id"] == rid else x
                                          for x in s.meta.get("routines") or []]
                    self.save(s)
                nxt = next((x["next"] for x in s.meta.get("routines") or [] if x["id"] == rid), None)
                if nxt is not None:
                    soonest = nxt - time.time() if soonest is None else min(soonest, nxt - time.time())
        return soonest

    def set_bot(self, s, bot):
        """(under the lock) A chat made a Bot (bot: {name, picture: base64 or None to keep it, or "" to drop it}), its
        name or picture changed, or (bot None) a chat again, its memory and picture kept in case it's made one again."""
        folder = bot_dir(self.state_dir, s.id)
        if bot is None:
            s.meta.pop("bot", None)
        else:
            if not isinstance(bot, dict) or not str(bot.get("name") or "").strip():
                raise ValueError(wording.say("host_errors.bot_needs_name"))
            os.makedirs(folder, mode=0o700, exist_ok=True)
            s.meta["bot"] = {"name": " ".join(str(bot["name"]).split())[:80],
                             "since": (s.meta.get("bot") or {}).get("since") or time.time()}
            if not os.path.exists(os.path.join(folder, "memory.md")):  # (and nothing in it accepted yet: all the Bot
                for name in ("memory.md", "memory.seen.md"):              # writes is shown as its changes)
                    with open(os.path.join(folder, name), "w") as f:
                        f.write("")
            pic = os.path.join(folder, "picture")
            if bot.get("picture") == "":
                if os.path.exists(pic):
                    os.remove(pic)
            elif bot.get("picture"):
                data = base64.b64decode(bot["picture"])
                if len(data) > BOT_PICTURE_MAX:
                    raise ValueError(wording.say("host_errors.picture_too_big"))
                with open(pic + ".new", "wb") as f:
                    f.write(data)
                os.replace(pic + ".new", pic)
        self.save(s)
        self.renew_agent(s)
        self.changed()
        return s.listing()

    def renew_agent(self, s):
        """(under the lock) A session's agent told what it's told at its start anew (a Bot made, its mesh's tools
        given): started again with its next message, after the turn it's at, if it's running."""
        if getattr(s, "proc", None) is not None:
            if s.busy:
                s.renew = True
            else:
                self.restart_agent(s)

    @staticmethod
    def restart_agent(s):
        """(under the lock) A session's agent started again with its next message, as set_cwd's is: what it's told
        at its start changed. Not at a turn (its end's waited for: from_claude)."""
        s.renew = False
        s.proc.stopping = s.proc.restarting = True
        s.proc.close_stdin()

    def edits_memory(self, s, request):
        """Is what a Bot's agent asks to do an edit of a file in its own folder (its memory's)?"""
        path = (request.get("input") or {}).get("file_path")
        if not s.meta.get("bot") or request.get("tool_name") not in ("Edit", "MultiEdit", "Write") or \
                not isinstance(path, str) or not path:
            return False
        folder = os.path.realpath(bot_dir(self.state_dir, s.id))
        path = os.path.realpath(os.path.join(s.meta.get("cwd") or "/", os.path.expanduser(path)))
        return path.startswith(folder + os.sep)

    def bot_picture(self, s):
        pic = os.path.join(bot_dir(self.state_dir, s.id), "picture")
        if not os.path.exists(pic):
            return None
        with open(pic, "rb") as f:
            return base64.b64encode(f.read()).decode()

    def bot_memory(self, s, text=None, seen=None, **base):
        """A Bot's memory: {text, seen, mtime, path}: memory.md's text and its mtime (in ns), and seen, what the user's
        accepted of it (memory.seen.md, made as the memory is the first time it's asked for, if there's none). text
        given: written, and seen with it if that's given; base_mtime given, only if memory.md's mtime is still that,
        else nothing's written and it's {refused: why, text, seen, mtime, path}, the memory as the Bot left it."""
        if not s.meta.get("bot"):
            raise ValueError(wording.say("host_errors.not_a_bot"))
        folder = bot_dir(self.state_dir, s.id)
        path, seen_path = os.path.join(folder, "memory.md"), os.path.join(folder, "memory.seen.md")
        if text is not None:
            if "base_mtime" in base and _mtime(path) != base["base_mtime"]:
                return {"refused": wording.say("host_errors.memory_changed"), **self.memory_now(path, seen_path)}
            os.makedirs(folder, mode=0o700, exist_ok=True)
            if seen is not None:
                _write_text(seen_path, str(seen))
            _write_text(path, str(text))
        return self.memory_now(path, seen_path)

    @staticmethod
    def memory_now(path, seen_path):
        """A Bot's memory as it is on the disk (bot_memory's)."""
        try:
            with open(path) as f:
                text = f.read()
        except FileNotFoundError:
            text = ""
        try:
            with open(seen_path) as f:
                seen = f.read()
        except FileNotFoundError:
            seen = text
            if os.path.isdir(os.path.dirname(seen_path)):
                _write_text(seen_path, seen)
        return {"text": text, "seen": seen, "mtime": _mtime(path), "path": path}

    @staticmethod
    def makes_folder(msg):
        """Is it what a session's folder is made again for, if it's gone: a message, or a revert (both run claude)?"""
        return msg.get("type") == "user" or (msg.get("type") == "control_request" and
                                             (msg.get("request") or {}).get("subtype") == "rewind_conversation")

    def restore_folder(self, conn, sid):
        """(not under the lock) A session's worktree that's gone (Claude Desktop removes a chat's as it archives it)
        made again from its branch (baudelib.worktree), before its claude starts there; those watching it, and conn,
        told so. Gone if it can't be."""
        with self.lock:
            s = self.sessions.get(sid)
            if s is None or s.deleting or not s.meta.get("cwd") or os.path.isdir(s.meta["cwd"]):
                return
            cwd, transcript = s.meta["cwd"], s.transcript()
        branch = worktree.restore(cwd, transcript)
        if branch is None:
            return
        self.log(f"made {cwd} again, on {branch}, for {sid}")
        name = (worktree.split(os.path.realpath(cwd)) or ("", os.path.basename(cwd)))[1]
        note = {"type": "_note", "text": f"worktree {name} was removed: made again from its branch {branch}"}
        with self.lock:
            for c in s.live | ({conn} if conn is not None else set()):
                c.send({"op": "claude", "session": sid, "msg": note})

    # ---- sessions
    def start(self, params, conn=None, req=None):
        """A new session, as start asks (wire.py): conn attached live to it first if it asks to be (attach), and told
        started once it's begun (its claude started, its prompt sent): before claude says anything of it, which waits
        on the lock this holds. Anything wrong, and it's not kept."""
        model = params.get("model") or None
        if params.get("adopt") is not None:
            return self.adopt(params, conn, req)
        with self.lock:
            src = self.sessions.get(params["fork_from"]) if params.get("fork_from") else None
        origin = src  # (the chat forked, or asked a side question of: its folder made again if it's gone)
        if params.get("fork_from") and params.get("ephemeral") and src is not None and src.transcript() is None:
            params = {**params, "cwd": params.get("cwd") or src.meta["cwd"]}  # (a side question in a chat with
            src = None                                                         # nothing written yet: a fresh one, there)
        elif params.get("fork_from") and (src is None or src.kept() or src.transcript() is None):
            raise ValueError(wording.say("host_errors.no_fork_source", session=params["fork_from"]))
        cwd = os.path.realpath(os.path.expanduser(params.get("cwd") or (src.meta["cwd"] if src else "~")))
        if origin is not None and not os.path.isdir(cwd) and cwd == os.path.realpath(origin.meta["cwd"]):
            worktree.restore(cwd, origin.transcript())  # (not under the lock: git; Gone if it can't be)
        if not os.path.isdir(cwd):
            raise ValueError(wording.say("host_errors.not_a_folder", path=cwd))
        if src and cwd != os.path.realpath(src.meta["cwd"]):  # (claude finds what it resumes by its folder)
            raise ValueError(wording.say("host_errors.fork_folder", path=src.meta["cwd"]))
        model = model or (src.meta.get("model") if src else None)
        mode = params.get("mode") or "default"
        if params.get("spawned_by") and mode not in meshtools.SUBAGENT_MODES:  # (a Bot's subagent: Auto at most;
            mode = "auto"  # a program without Auto's modes, launch's next best: manual)
        if params.get("ephemeral") and models.model_program(model) == "codex":
            mode = "default"
        run = models.launch(model, mode, params.get("effort") or None)
        if not self.can_run(model):
            raise ValueError(wording.say("host_errors.cant_run", host=self.me["name"], model=model))
        options = {k: params[k] for k, _flag in CLAUDE_OPTIONS if params.get(k)}
        if not isinstance(options.get("system", ""), str):
            raise ValueError("system is text")
        for k in ("add_dirs", "allowed_tools", "disallowed_tools"):
            if k in options:
                values = options[k] if isinstance(options[k], list) else [options[k]]
                if not all(isinstance(v, str) and v.strip() and not v.startswith("-") for v in values):
                    raise ValueError(f"{k} is a list of words, none starting with -")
                options[k] = values
        if (options or src) and run["program"] != "claude":
            raise ValueError(f"{', '.join([*options, *(['fork_from'] if src else [])])}: claude's, not {model}'s")
        if params.get("ask_default") not in (None, "deny", "allow"):
            raise ValueError("ask_default is deny or allow")
        tools = params.get("app_tools") or []  # (MCP servers the head runs itself: relayed back to it, AppTools)
        if not isinstance(tools, list) or not all(wire.is_id(t) for t in tools) or len(tools) > 4:
            raise ValueError("app_tools is a list of up to 4 names")
        if tools and (conn is None or run["program"] != "claude"):
            raise ValueError("app_tools are a head's, for a claude here")
        meta = {"id": str(uuid.uuid4()), "cwd": cwd, "model": model, "effort": run["effort"], "mode": run["mode"],
                "group": self.group_of(params), "title": params.get("title") or None,
                "prompt": params.get("prompt") or "", "created": time.time(), "options": options,
                "fork_from": (src.meta.get("cli_id") or src.id) if src else None,
                "ask_timeout": float(params["ask_timeout"]) if params.get("ask_timeout") else None,
                "ask_default": params.get("ask_default") or "deny", "ephemeral": bool(params.get("ephemeral")),
                "grace": min(float(params["grace"]), GRACE_MOST) if params.get("ephemeral") and
                isinstance(params.get("grace"), (int, float)) and params["grace"] > 0 else None,
                "spawned_by": params.get("spawned_by") if wire.is_id(params.get("spawned_by") or "") else None,
                "start_key": params.get("start_key") if wire.is_id(params.get("start_key") or "") else None,
                "draft": bool(params.get("draft")) and not (params.get("prompt") or "").strip()}
        with self.lock:
            if self.stopped.is_set():
                raise OSError("the host process is stopping")
            s = Session(self, meta)
            if tools:
                s.app_tools = agentproxy.AppTools(self, conn, s.id, tools)
            if isinstance(params.get("env"), dict):
                s.env = clean_env(params["env"])
            self.sessions[s.id] = s
            self.save(s)
            if conn is not None and params.get("attach"):
                conn.answers = params.get("answers", True) is not False
                s.attached_once = True
                s.viewers.add(conn)
                s.live.add(conn)
            try:
                if src is not None:  # (who its messages were from goes with the copy of them)
                    self.copy_senders(src, s)
                if meta["prompt"].strip():  # (from a chat whose mesh tools started it, if they did: input's as)
                    self.ensure_proc(s)
                    self.send_user(s, meta["prompt"], self.sent_by(conn, params) if conn is not None else None)
            except Exception:  # (its claude couldn't start: no session, and the error's the answer)
                if s.app_tools is not None:
                    s.app_tools.end()
                del self.sessions[s.id]
                if s.proc is not None:
                    s.proc.kill()
                for kept in (os.path.join(self.state_dir, "sessions", f"{s.id}.json"), self.senders_path(s.id)):
                    try:  # (nor who its prompt was from)
                        os.remove(kept)
                    except OSError:
                        pass
                raise
            if conn is not None:
                conn.send({"op": "started", "req": req, "session": s.listing(), "forked": bool(src)})
            self.log(f"started {s.id} in {cwd} ({meta['group']})")
            self.changed()
        return s

    def group_of(self, params):
        """A new session's group: as asked; the host's own if none's asked; none if asked for none ("": Baude's own
        chats, which are in no group till they're put in one)."""
        group = params.get("group")
        return group if isinstance(group, str) and (group or "group" in params) else self.me["group"]

    def adopt(self, params, conn=None, req=None):
        """start {adopt: id}: a conversation claude has here already (a chat Baude ran itself, or a terminal's), made
        one of this host's, by its own id, and resumed by it from then on (its transcript's where claude keeps it).
        One that's this host's already: as it is. Its claude starts with the first message (input)."""
        sid = params["adopt"]
        if not wire.is_id(sid):
            raise ValueError(f"not a session's id: {sid!r}")
        path = transcripts.session_file(sid)
        with self.lock:
            if self.stopped.is_set():
                raise OSError("the host process is stopping")
            s = self.sessions.get(sid)
            if s is not None and s.deleting:
                raise ValueError(wording.say("host_errors.being_deleted", session=sid))
            if s is None:
                if not path:
                    raise ValueError(wording.say("host_errors.no_adopt_source", session=sid))
                cwd = os.path.realpath(os.path.expanduser(params.get("cwd") or "~"))
                if not os.path.isdir(cwd) and not worktree.restorable(cwd):  # (a worktree that's gone: made again
                    raise ValueError(wording.say("host_errors.not_a_folder", path=cwd))  # with its first message)
                model = params.get("model") or None
                if not self.can_run(model):
                    raise ValueError(wording.say("host_errors.cant_run", host=self.me["name"], model=model))
                run = models.launch(model, params.get("mode") or "default", params.get("effort") or None)
                s = Session(self, {"id": sid, "cwd": cwd, "model": model, "effort": run["effort"], "mode": run["mode"],
                                   "group": self.group_of(params), "title": params.get("title") or None,
                                   "prompt": "", "created": time.time(), "options": {}, "path": path,
                                   "said": os.path.getmtime(path), "ask_default": "deny", "adopted": True})
                self.sessions[sid] = s
                self.save(s)
                self.log(f"took up {sid} in {cwd}")
            if isinstance(params.get("env"), dict):
                s.env = clean_env(params["env"])
            if conn is not None:
                conn.send({"op": "started", "req": req, "session": s.listing()})
            self.changed()
        return s

    def codex_empty_thread(self, s):
        """Older Baude builds stored an ID even when Codex had no rollout yet."""
        if s.meta.get("codex_started") or s.meta.get("context_usage"):
            return False
        path = s.transcript()
        if path is None:
            return True
        try:
            with open(path, encoding="utf-8") as transcript:
                return not any(line.strip() and json.loads(line).get("type") == "assistant" for line in transcript)
        except (OSError, ValueError):
            return False

    def ensure_proc(self, s):
        """(lock held) The session's claude, started if it isn't running: with its id until it's written a transcript,
        resumed after. One being stopped is let go of first (what's sent now is the next one's)."""
        if s.proc is not None and getattr(s.proc, "stopping", False):
            old, s.proc, s.busy = s.proc, None, False
            s.forget_commands()
            old.kill()
            try:
                old.p.wait(5)
            except Exception:  # (still going: a claude of its own, about to go)
                pass
            self.drop_routes(s)
        if s.proc is not None:
            return s.proc
        if not os.path.isdir(s.meta["cwd"]):  # (gone, and not made again before this: restore_folder's)
            raise worktree.missing(s.meta["cwd"])
        resumed = s.transcript() is not None
        run = models.launch(s.meta.get("model"), s.meta.get("mode"), s.meta.get("effort"),
                            s.meta.get("cli_id") or s.id, resumed=resumed)
        if not self.can_run(s.meta.get("model")):
            raise OSError(wording.say("host_errors.cant_run", host=self.me["name"], model=s.meta.get("model")))
        extra = list(run["extra"])
        if run["program"] == "claude":
            if s.meta.get("fork_from") and not resumed:  # (its first run: a copy of the other's conversation, which
                run["resume"], extra = s.meta["fork_from"], ["--fork-session"]  # claude names itself: cli_id)
            for key, flag in CLAUDE_OPTIONS:
                value = (s.meta.get("options") or {}).get(key)
                if value:
                    extra += [flag, *(value if isinstance(value, list) else [value])]
            if s.meta.get("ephemeral"):  # (a side question: sees the conversation, writes nothing of its own)
                extra += ["--no-session-persistence"]
        if s.meta.get("bot") and run["program"] in ("claude", "pi"):  # (who it is, and its memory, each start)
            mem = self.bot_memory(s)
            told = bot_system(s.meta["bot"]["name"], mem["path"], mem["text"])
            if run["program"] == "claude" and "--append-system-prompt" in extra:  # (one: its own system with it)
                at = extra.index("--append-system-prompt") + 1
                extra[at] = extra[at] + "\n\n" + told
            else:
                extra += ["--append-system-prompt", told]
            if run["program"] == "claude":  # (its memory's folder its to edit, as the chat's own is, without asking)
                folder = os.path.dirname(mem["path"])
                extra += ["--add-dir", folder]
                rules = [f"{tool}(/{folder}/**)" for tool in ("Edit", "Write")]  # (//: a path from the root)
                if "--allowedTools" in extra:  # (one: the chat's own allowed_tools with them)
                    at = extra.index("--allowedTools") + 1
                    extra[at:at] = rules
                else:
                    extra += ["--allowedTools", *rules]
        if s.mesh_tools() and run["program"] in ("claude", "pi"):  # (the mesh's tools: baudelib.meshtools, here)
            servers = {meshtools.NAME: {"command": sys.executable, "args": [agentproxy.HEADLESS, "mesh-tools",
                                                                            "--socket",
                                                                            os.path.abspath(self.socket_path)],
                                        "env": {"BAUDE_SESSION": s.id}}}
            if s.meta.get("bot"):  # (a Bot's browser too: its own Emission profile, on any host)
                servers[emissiontools.NAME] = {"command": sys.executable,
                                               "args": [agentproxy.HEADLESS, "emission-tools", "--socket",
                                                        os.path.abspath(self.socket_path)],
                                               "env": {"BAUDE_SESSION": s.id}}
            extra += ["--mcp-config", json.dumps({"mcpServers": servers})]
            if run["program"] == "claude":  # (a Bot's, never asked about; another chat's, its looking only: what it
                rules = [f"mcp__{meshtools.NAME}__{t}"  # starts or says, as the chat's mode has it)
                         for t in (meshtools.SCHEMAS if s.meta.get("bot") else meshtools.READ_ONLY)]
                if s.meta.get("bot"):
                    rules += [f"mcp__{emissiontools.NAME}__{t}" for t in emissiontools.SCHEMAS]
                if "--allowedTools" in extra:
                    at = extra.index("--allowedTools") + 1
                    extra[at:at] = rules
                else:
                    extra += ["--allowedTools", *rules]
        if s.app_tools is not None and run["program"] == "claude":  # (a head's own tools: relays back to it, never
            tools = s.app_tools.config()                              # asked about: the head runs them itself)
            if "--mcp-config" in extra:  # (the mesh's with them, in one)
                at = extra.index("--mcp-config") + 1
                extra[at] = json.dumps({"mcpServers": {**json.loads(extra[at])["mcpServers"], **tools}})
            else:
                extra += ["--mcp-config", json.dumps({"mcpServers": tools})]
            rules = [f"mcp__{n}__*" for n in tools]
            if "--allowedTools" in extra:
                at = extra.index("--allowedTools") + 1
                extra[at:at] = rules
            else:
                extra += ["--allowedTools", *rules]
        made = []  # (the claude its exit is about: claude_exited)
        kind = agentproxy.ProxiedProc if models.proxied(s.meta.get("model")) else claude.ClaudeProc
        if kind is claude.ClaudeProc and run["program"] == "pi":  # (pi here writes its session itself: a run of it
            agentproxy.PiSession.release(agentproxy.pi_sessions(), s.meta.get("cli_id") or s.id)  # elsewhere no more)
        if run["program"] == "codex":
            # The server keeps the display history. Codex resumes its own thread using its native ID.
            s.proc = codex.CodexProc(
                lambda p, m: self.from_claude(s, p, m), lambda code: self.claude_exited(s, made, code),
                s.meta["cwd"], s.meta["model"],
                resume=None if s.meta.get("ephemeral") else s.meta.get("cli_id"),
                effort=run["effort"], exe=run["exe"], ephemeral=s.meta.get("ephemeral", False),
                env={**s.env, **(self.env or {})}, catalog=self.codex_models(), mode=run["mode"],
                recover_empty=bool(s.meta.get("cli_id")) and self.codex_empty_thread(s))
        else:
            s.proc = kind(
                lambda p, m: self.from_claude(s, p, m), lambda code: self.claude_exited(s, made, code), s.meta["cwd"],
                s.meta.get("model"), run["mode"], run["resume"], run["effort"], extra=extra,
                env={**s.env, **(self.env or {})} or None,  # (a head's, as it gave it; the host's own extras over it)
                exe=run["exe"] if run["program"] != "claude" else self.exe,
                **({"path": self.socket_path} if kind is agentproxy.ProxiedProc else {}))
        made.append(s.proc)
        s.used, s.streamed, s.acting = time.time(), False, False  # (a new agent, its model's perhaps: how it says
        # what it writes learned again)
        self.changed()  # (running: listed so, and the watch waits for it to go idle)
        return s.proc

    def send_user(self, s, content, sender=None):
        """(lock held) A session's first message (start's prompt), from sender if it isn't its user's (take_input)."""
        said = str(uuid.uuid4())  # (its program keeps it: who it's from kept by it, stamp)
        if wire.clean_sender(sender) is not None:
            content = wire.said_by(sender, content)
            self.stamp(s, said, sender)
        s.proc.user(content, uuid=said)
        self.keep(s, "user", {"role": "user", "content": content}, said)
        s.busy, s.used = True, time.time()
        s.meta["said"] = time.time()
        self.save(s)

    def take_input(self, conn, s, msg, sender=None, shown=None):
        """(lock held) What a client sends its claude. A user message is given a uuid if it has none, which its
        program keeps; sender: who it's from, if not the session's user (sent_by): said so in it, for its agent
        (wire.said_by), and kept by that uuid (stamp), with what heads show of it if not its text (shown: a held
        answer's). One whose uuid it's taken already (a head sending it again, its connection gone as it first sent
        it) taken once: the last TAKEN kept in its meta."""
        kind = msg.get("type")
        if kind == "user":
            if wire.is_id(msg.get("uuid") or ""):
                taken = s.meta.setdefault("taken", [])
                if msg["uuid"] in taken:
                    return
                taken.append(msg["uuid"])
                del taken[:-TAKEN]
            msg = {**msg, "uuid": msg["uuid"] if wire.is_id(msg.get("uuid") or "") else str(uuid.uuid4())}
        if kind == "user" and wire.clean_sender(sender) is not None:
            message = msg.get("message") or {}
            msg = {**msg, "message": {**message, "content": wire.said_by(sender, message.get("content"))}}
            self.stamp(s, msg["uuid"], sender, shown)
        if kind == "user":
            is_codex = models.model_program(s.meta.get("model")) == "codex"
            if is_codex:
                codex.user_input((msg.get("message") or {}).get("content", ""))
                if s.busy:
                    s.codex_queue.append(msg)
                    return
            s.meta.pop("cut", None)  # (carried on, or something else said: not cut any more)
            if s.meta.pop("draft", None) and not s.meta.get("prompt"):  # (written in: a session of its own from now on,
                s.meta["prompt"] = _said(msg)                           # listed and kept, titled by what was said)
            at_turn = s.busy
            self.ensure_proc(s).send(msg)
            if at_turn and s.kept():  # (taken after the turn it's sent in: kept after it too)
                s.queued.append((msg.get("message") or {}, msg.get("uuid")))
            else:
                self.keep(s, "user", msg.get("message") or {}, msg.get("uuid"))
            s.busy, s.used, s.meta["said"] = True, time.time(), time.time()
            self.save(s)
            self.changed()
            return
        if kind == "control_response":  # (an answer to what claude asked)
            s.asks.pop((msg.get("response") or {}).get("request_id"), None)
            if s.proc is not None:
                s.proc.send(msg)
            s.used = time.time()
            self.changed()
            return
        if kind != "control_request":
            if s.proc is not None:
                s.proc.send(msg)
            return
        rid, request = msg.get("request_id"), msg.get("request") or {}
        sub, undo = request.get("subtype"), None
        answer = lambda response: conn.send({"op": "claude", "session": s.id, "msg": {  # noqa: E731
            "type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": response}}})
        if sub == "get_context_usage" and models.model_program(s.meta.get("model")) == "codex":
            return answer(s.meta.get("context_usage") or {"showWithoutLimit": True})
        if sub == "initialize":
            if s.proc is None and not os.path.isdir(s.meta["cwd"]):  # (its folder gone: nothing started for this, a
                return answer(self.init({**(s.meta.get("init") or {}),  # message makes it again: restore_folder)
                                         "current_permission_mode": s.meta.get("mode") or "default"}))
            if s.meta.get("init") is not None and (s.proc is None or s.proc.init_rid is None):
                return answer(self.init({**s.meta["init"], "current_permission_mode": s.meta.get("mode") or "default"}))
            s.waiting_init.append((conn, rid))
            self.ensure_proc(s)
            return
        if sub == "set_model" and request.get("model") and \
                models.model_program(request["model"]) != models.model_program(s.meta.get("model")):
            return conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                "subtype": "error", "request_id": rid,
                "error": wording.say("host_errors.other_model")}}})
        if sub == "set_model" and s.proc is not None and request.get("model") != s.meta.get("model") and \
                (models.model_program(request.get("model")) == "pi" or
                 models.runs_on(request.get("model")) != models.runs_on(s.meta.get("model"))):
            s.proc.stopping = s.proc.restarting = True  # (pi-claude runs one model, and one on another host runs
            s.proc.kill()  # there: the next message starts it on this one, pi's session going on: nothing's gone)
        if sub == "set_permission_mode" and models.model_program(s.meta.get("model")) == "codex" and (
            models.codex_mode(request.get("mode")) != request.get("mode")
            or (s.meta.get("ephemeral") and request.get("mode") != "default")
        ):
            return conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": "Unsupported Codex permission mode"}}})
        if sub in SETTINGS:
            key, field = SETTINGS[sub]
            undo = (key, s.meta.get(key), request.get(field) or s.meta.get(key))  # (put back if claude says no)
            s.meta[key] = undo[2]
        elif sub == "apply_flag_settings" and "effortLevel" in (request.get("settings") or {}):
            s.meta["effort"] = request["settings"]["effortLevel"]
        if models.model_program(s.meta.get("model")) == "codex":
            s.meta["effort"] = models.codex_effort(self.codex_models(), s.meta["model"], s.meta.get("effort"))
        self.save(s)
        if (s.proc is None or getattr(s.proc, "stopping", False)) and sub != "rewind_conversation":
            self.changed()  # (stopped: answered from what's kept, for when it runs; only a message starts it, and a
            # revert, which its claude makes)
            if sub == "get_settings":
                return answer({"applied": {"model": s.meta.get("model"), "effort": s.meta.get("effort")}})
            if sub == "interrupt":  # (nothing here to stop: no result's coming, so a head waiting on one ends its turn)
                return answer({"running": False})
            return answer({})
        try:
            proc = self.ensure_proc(s)
        except OSError as e:  # (its claude couldn't be started: a revert's said so, not left waiting)
            return conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": str(e)}}})
        on = f"srv_{next(self.rids)}"
        if sub == "rewind_conversation" and s.kept():  # (pi's and Apple's: the model's cut, by its program; the
            # transcript's, here, once it's made)
            path = os.path.join(self.state_dir, "transcripts", f"{s.id}.jsonl")
            found = transcripts.rewind_target(path, request.get("target_message_uuid"))
            if found is None:
                return conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                    "subtype": "success", "request_id": rid,
                    "response": {"rewound": False, "error": wording.say("host_errors.rewind_missing")}}}})
            s.rewinds[on], index, count, before = found
            msg = {**msg, "request": {**request, "baude_prompt_index": index, "baude_prompt_count": count,
                                      "baude_kept": before}}  # (Apple's, its session made again from them)
        if sub == "rewind_conversation":  # (once it's made, who what it drops was from goes too: unstamp)
            s.reverting[on] = request.get("target_message_uuid")
        s.routes[on] = (conn, rid, undo)
        proc.send({**msg, "request_id": on})
        self.changed()

    def from_claude(self, s, proc, msg):
        """What a session's claude says (on its reading thread)."""
        with self.lock:
            if proc is not s.proc:
                return
            kind = msg.get("type")
            if kind == "control_response":
                r = msg.get("response") or {}
                rid = r.get("request_id")
                if rid == proc.init_rid:
                    proc.init_rid = None  # (answered: what it said is kept for whoever asks next)
                    if r.get("subtype") == "success":
                        s.meta["init"] = r.get("response") or {}
                        self.save(s)
                        if models.model_program(s.meta.get("model")) == "claude":  # (the latest: info's, for a head's
                            self.me["init"] = {k: v for k, v in s.meta["init"].items()  # pickers before its own)
                                               if k in ("commands", "models", "output_style", "available_output_styles")}
                            _write_json(os.path.join(self.state_dir, "server.json"), self.me)
                        r = {**r, "response": self.init(s.meta["init"], fresh=True)}
                    for conn, theirs in s.waiting_init:
                        conn.send({"op": "claude", "session": s.id,
                                   "msg": {**msg, "response": {**r, "request_id": theirs}}})
                    s.waiting_init = []
                elif rid in s.routes:
                    conn, theirs, undo = s.routes.pop(rid)
                    rewound = r.get("subtype") == "success" and (r.get("response") or {}).get("rewound")
                    if rid in s.rewinds:  # (a revert its program made: marked in the transcript kept here)
                        last = s.rewinds.pop(rid)
                        if rewound:
                            self.keep_rewind(s, last)
                            s.queued = []  # (sent after it, at the turn it stopped: gone with it)
                    if (target := s.reverting.pop(rid, None)) and rewound:
                        self.unstamp(s, target)
                    applied = (r.get("response") or {}).get("applied") if r.get("subtype") == "success" else None
                    if isinstance(applied, dict):  # (get_settings: what claude runs with, its own defaults too: kept,
                        for key in ("model", "effort"):  # for when it's asked again and isn't running)
                            if applied.get(key) and not s.meta.get(key):
                                s.meta[key] = applied[key]
                                self.save(s)
                    if undo and r.get("subtype") == "error" and s.meta.get(undo[0]) == undo[2]:
                        s.meta[undo[0]] = undo[1]  # (refused, a mode it can't switch to say: kept as it still is)
                        self.save(s)
                        self.changed()
                    if callable(theirs):  # (the server's own request: rename)
                        theirs(r.get("subtype") == "success")
                    else:
                        conn.send({"op": "claude", "session": s.id,
                                   "msg": {**msg, "response": {**r, "request_id": theirs}}})
                return
            if kind == "control_request":
                request = msg.get("request") or {}
                if request.get("subtype") != "can_use_tool":
                    proc.send({"type": "control_response", "response": {
                        "subtype": "error", "request_id": msg.get("request_id"),
                        "error": f"baude-server: unsupported {request.get('subtype')}"}})
                    return
                if request.get("tool_name") in s.always_allow and models.model_program(s.meta.get("model")) == "claude":
                    proc.respond(msg.get("request_id"), {"behavior": "allow",  # (always allowed, a head said:
                                                         "updatedInput": request.get("input") or {}})  # whoever's here)
                    return
                if self.edits_memory(s, request):  # (a Bot's memory is its own to keep: pi's and others' too)
                    proc.respond(msg.get("request_id"), {"behavior": "allow",
                                                         "updatedInput": request.get("input") or {}})
                    return
                s.asks[msg.get("request_id")] = msg
                self.cutting.discard(s.id)  # (asking as it stops: nobody can answer, so it stays cut, however it ends)
                if s.meta.get("ask_timeout"):
                    self.ask_timer(s, proc, msg.get("request_id"))
                for conn in s.viewers:
                    conn.send({"op": "claude", "session": s.id, "msg": msg})
                self.changed()
                return
            if kind == "system" and msg.get("subtype") == "codex_context":
                s.meta["context_usage"] = msg["usage"]
            if kind == "system" and msg.get("subtype") == "status" and msg.get("permissionMode") \
                    and msg["permissionMode"] != s.meta.get("mode") \
                    and not any(u and u[0] == "mode" for _c, _r, u in s.routes.values()):
                # (claude's own: out of plan, say: kept so; not a change a head asked for before the one it's making)
                s.meta["mode"] = msg["permissionMode"]
                self.save(s)
                self.changed()
            subtype = msg.get("subtype")
            if kind == "system" and subtype == "codex_discard_empty_thread":
                s.meta.pop("cli_id", None)
                self.save(s)
                return
            if kind == "system" and subtype == "codex_turn_started":
                if not s.meta.get("ephemeral") and s.meta.get("cli_id") != msg["session_id"]:
                    s.meta["cli_id"] = msg["session_id"]
                    s.meta["codex_started"] = True
                    self.save(s)
            if kind == "system" and subtype == "init" and not isinstance(proc, codex.CodexProc) \
                    and msg.get("session_id") and msg["session_id"] != (s.meta.get("cli_id") or s.id):
                s.meta["cli_id"] = msg["session_id"]  # (claude went on in a transcript of another id)
                s.meta.pop("path", None)
                self.save(s)
            if kind in ("stream_event", "assistant") and not msg.get("parent_tool_use_id") and not s.busy:
                s.busy = True  # (a turn claude started itself: a background task's end, say)
                if self.stopped.is_set():  # (one more, started as it's stopped: cut short)
                    self.cut(s)
                    self.cutting.add(s.id)
                self.changed()
            if kind in ("assistant", "user") and not msg.get("parent_tool_use_id"):
                self.keep(s, kind, msg.get("message") or {})
            if kind == "stream_event" and not msg.get("parent_tool_use_id"):
                s.streamed = True  # (its text said as it's written: the same text come whole after is late)
            if kind in ("stream_event", "assistant") and not msg.get("parent_tool_use_id") and \
                    (acting := turn_acting(msg, s.acting, s.streamed)) != s.acting:
                s.acting = acting
                self.changed()
            if kind in ("assistant", "user", "system", "result") and self.track(s, msg):
                self.changed()
            if kind == "result" and s.id in self.cutting and self.stopped.is_set() and msg.get("subtype") == "success":
                self.cutting.discard(s.id)  # (it ended as it was stopped: not cut after all)
                s.meta.pop("cut", None)
                self.save(s)
            if kind == "result" and s.renew and proc is s.proc and not s.queued:  # (a Bot changed at its turn)
                self.restart_agent(s)
            if kind == "result" and (not self.stopped.is_set() or msg.get("subtype") == "success"):
                self.drop_waits(s)
            if kind == "result":
                for message, said in s.queued:
                    self.keep(s, "user", message, said)
                s.queued = []
                s.busy, s.used, s.acting = False, time.time(), False
                s.routines_out.clear()
                s.asks.clear()
                if s.meta.get("context_usage") is not None:
                    self.save(s)
                self.changed()
                ended = {"op": "turn_ended", "session": s.id, "result": msg.get("result"),
                         "is_error": bool(msg.get("is_error")), "cost": msg.get("total_cost_usd"),
                         "turns": msg.get("num_turns"), "duration_ms": msg.get("duration_ms")}
                for conn in self.conns:  # (as it happens: a turn too short for a sessions listing to see isn't missed;
                    # an archived session's to those who see archived ones)
                    if conn.subscribed and (conn.archived or not s.meta.get("archived")):
                        conn.send(ended)
            for conn in s.live:
                conn.send({"op": "claude", "session": s.id, "msg": msg})
            if kind == "result" and s.codex_queue:
                if self.stopped.is_set() or s.deleting or getattr(proc, "stopping", False):
                    s.codex_queue.clear()
                else:
                    self.take_input(None, s, s.codex_queue.pop(0))

    def track(self, s, msg):
        """(lock held) What a session's claude runs (Session.commands), from what it says: a Bash or PowerShell call
        (its subagents' too) from its tool_use to its tool_result; a background task (not a subagent's) from
        task_started to task_notification. A turn's result ends any call still unanswered, but one in a subagent
        running in the background (is_backgrounded, run_in_background, or in one), which goes on past it. True if
        that's changed."""
        kind, changed = msg.get("type"), False
        content = (msg.get("message") or {}).get("content")
        blocks = [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []
        if kind == "assistant":
            for c in blocks:
                if c.get("type") != "tool_use" or not isinstance(c.get("id"), str):
                    continue
                inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                if c.get("name") in AGENT_TOOLS:
                    s.agent_calls[c["id"]] = (msg.get("parent_tool_use_id"), inp.get("run_in_background") is True)
                elif c.get("name") in COMMAND_TOOLS and c["id"] not in s.calls:
                    s.calls[c["id"]] = {"id": c["id"], "what": _what(inp.get("description"), inp.get("command")),
                                        "since": time.time(), "background": False,
                                        "parent": msg.get("parent_tool_use_id")}
                    changed = True
        elif kind == "user":
            for c in blocks:
                if c.get("type") == "tool_result" and s.calls.pop(c.get("tool_use_id"), None) is not None:
                    changed = True
        elif kind == "system" and isinstance(tid := msg.get("task_id"), str):
            if msg.get("subtype") == "task_started" and msg.get("task_type") == "local_agent":
                tool = msg.get("tool_use_id")
                s.agent_tasks[tid] = tool
                parent, background = s.agent_calls.get(tool) or (None, False)
                if tool is not None and (msg.get("is_backgrounded") or background or s.in_background_agent(parent)):
                    s.background_agents.add(tool)  # (its calls go on past the turn's result)
            elif msg.get("subtype") == "task_started" and tid not in s.tasks:
                call = s.calls.get(msg.get("tool_use_id")) or {}  # (a Bash run in the background: its call's)
                s.tasks[tid] = {"id": tid, "what": _what(msg.get("description")) or call.get("what") or "a task",
                                "since": call.get("since") or time.time(), "background": True}
                changed = True
            elif msg.get("subtype") == "task_notification":
                changed = s.tasks.pop(tid, None) is not None
                if (agent := s.agent_tasks.pop(tid, None)) is not None:  # (a subagent's done: nothing in it runs)
                    s.background_agents.discard(agent)
                    inside = [k for k, c in s.calls.items() if c["parent"] == agent]
                    for k in inside:
                        del s.calls[k]
                    changed |= bool(inside)
        elif kind == "result":  # (a subagent in the background goes on: what it runs, too)
            left = [k for k, c in s.calls.items() if not s.in_background_agent(c["parent"])]
            for k in left:
                del s.calls[k]
            changed = bool(left)
        return changed

    def ask_timer(self, s, proc, rid):
        """(lock held) A permission asked, answered as the session says (ask_default) once it's waited ask_timeout with
        nobody attached to answer it (someone's there: they're given as long again)."""
        def due():
            with self.lock:
                if rid not in s.asks or proc is not s.proc:
                    return
                if any(v.answers for v in s.viewers):  # (someone's there who answers: given as long again)
                    return self.ask_timer(s, proc, rid)
                ask = s.asks.pop(rid)
                allow = s.meta.get("ask_default") == "allow"
                proc.respond(rid, {"behavior": "allow", "updatedInput": (ask.get("request") or {}).get("input") or {}}
                             if allow else {"behavior": "deny",
                                            "message": f"Nobody answered within {s.meta['ask_timeout']:g} s."})
                self.log(f"{s.id}: {'allowed' if allow else 'denied'} {(ask.get('request') or {}).get('tool_name')}, "
                         "nobody there to answer")
                self.changed()
        timer = threading.Timer(s.meta["ask_timeout"], due)
        timer.daemon = True
        timer.start()

    def keep(self, s, kind, message, said=None):
        """(lock held) A message of a session whose program keeps no transcript, kept in the server's, as Claude Code
        keeps one (what's listed, and what clients mirror, come from it). said: a message of yours's uuid, as the head
        named it (what a revert names)."""
        if not s.kept() or s.deleted or s.deleting:
            return
        path = os.path.join(self.state_dir, "transcripts", f"{s.id}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = {"parentUuid": None, "isSidechain": False, "type": kind, "uuid": said or str(uuid.uuid4()),
               "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "sessionId": s.id,
               "cwd": s.meta.get("cwd"), "entrypoint": "baude-server", "message": message}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")

    # ---- who a session's messages are from, if not its user (wire's Senders): kept by their uuids beside it
    def chat_sender(self, sid):
        """(lock held) One of this host's chats as a message from it is from: a Bot by its name, else by its title, on
        this host; None if there's no such chat here."""
        s = self.sessions.get(sid) if wire.is_id(sid or "") else None
        if s is None:
            return None
        listed = s.listing()
        bot = (listed.get("bot") or {}).get("name")
        return wire.clean_sender({"kind": "bot" if bot else "chat", "name": bot or listed.get("title"),
                                  "host": self.me["id"], "host_name": self.me["name"], "session": sid})

    def sent_by(self, conn, msg):
        """(lock held) Who a request passing a message in (input, start) is from, as this host knows it: through a
        tunnel, the chat its relay named (else the one its as names) on the member it came from; on this host, the
        chat of this host's its as names; None for the session's user."""
        if conn.origin is not None:
            said = conn.sender or ({"kind": "chat", "session": msg["as"]} if wire.is_id(msg.get("as") or "") else None)
            if said is None:
                return None
            name = self.mesh.name_of(conn.origin)
            return wire.clean_sender({**said, "host": conn.origin,
                                      "host_name": said.get("host_name") if name == conn.origin[:8] else name})
        return self.chat_sender(msg.get("as"))

    def senders_path(self, sid):
        return os.path.join(self.state_dir, "senders", f"{sid}.json")

    def senders(self, s):
        """(lock held) Its sender store, {uuid: sender} in the order kept (read the first time it's wanted); None if
        it's there and can't be read (said in the log; read again the next time, never written over meanwhile)."""
        if s.senders is None:
            try:
                with open(self.senders_path(s.id), encoding="utf-8") as f:
                    got = json.load(f)
            except FileNotFoundError:
                got = {}
            except (OSError, ValueError) as e:
                self.log(f"{s.id}: its sender store couldn't be read: {e}")
                return None
            if not isinstance(got, dict):
                self.log(f"{s.id}: its sender store isn't one")
                return None
            s.senders = {k: v for k, v in got.items() if wire.is_id(k) and isinstance(v, dict)}
        return s.senders

    def write_senders(self, s, senders):
        """(lock held) Its sender store as it is now, written whole, and listed as changed (its revision)."""
        s.senders = senders
        if s.meta.get("ephemeral") or s.deleted or s.deleting:
            return
        if senders or os.path.exists(self.senders_path(s.id)):
            _write_json(self.senders_path(s.id), senders)
        s.meta["senders"] = (s.meta.get("senders") or 0) + 1
        self.save(s)
        self.changed()

    def stamp(self, s, said, sender, shown=None):
        """(lock held) A message passed to a session from another than its user: who it's from kept by its uuid, and
        what heads show of it in place of its text, if that's given (shown: a held answer's, its agent's told)."""
        if (sender := wire.clean_sender(sender)) is not None and wire.is_id(said or "") and \
                (have := self.senders(s)) is not None:
            self.write_senders(s, {**have, said: {**sender, **({"shown": shown} if isinstance(shown, str) else {})}})

    def unstamp(self, s, target):
        """(lock held) A revert made: the message it took back and those after it gone from its sender store. Its own
        order says which, if it's one of the store's (they're kept as they're sent); else its transcript does, read
        on a thread of its own, not under the lock (what's stamped meanwhile, after the revert, stays)."""
        if not (have := self.senders(s)):
            return
        if target in have:
            keys = list(have)
            self.write_senders(s, {k: have[k] for k in keys[:keys.index(target)]})
            return
        path, among = s.transcript(), set(have)
        if not path:
            return

        def read():
            gone = transcripts.from_record(path, target, among)
            with self.lock:
                if gone and not s.deleted and self.sessions.get(s.id) is s and (now := self.senders(s)) is not None:
                    self.write_senders(s, {k: v for k, v in now.items() if k not in gone})
        threading.Thread(target=read, daemon=True, name="unstamp").start()

    def copy_senders(self, src, s):
        """(lock held) A fork's sender store: its source's, as the copy of its conversation has the same messages."""
        if (have := self.senders(src)):  # (None: unreadable, nothing copied)
            self.write_senders(s, dict(have))

    def keep_rewind(self, s, last):
        """(lock held) A revert its program made, in a session whose transcript is the server's: marked there as claude
        marks its own (rewind_conversation), so what comes after the record it keeps last is read as gone."""
        path = os.path.join(self.state_dir, "transcripts", f"{s.id}.jsonl")
        if not s.kept() or s.deleted or s.deleting or not os.path.exists(path):
            return
        rec = {"type": "last-prompt", "leafUuid": last, "rewound": True, "sessionId": s.id,
               "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")

    def claude_exited(self, s, made, code):
        with self.lock:
            if not made or s.proc is not made[0]:
                return  # (one let go of already: ensure_proc)
            s.proc, s.busy, s.acting, s.streamed = None, False, False, False
            s.codex_queue.clear()
            s.asks.clear()
            s.rewinds.clear()  # (asked of the program gone: never answered)
            s.forget_commands()
            s.agents = None  # (what its subagents wrote as it went: looked at again)
            s.note_written()  # (exited: all it's written is there)
            self.save(s)
            for conn, theirs in s.waiting_init:
                conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                    "subtype": "error", "request_id": theirs,
                    "error": wording.say("host_errors.claude_exited", code=code)}}})
            s.waiting_init = []
            self.drop_routes(s)
            if not self.stopped.is_set():
                self.drop_waits(s)
            if not getattr(made[0], "restarting", False) and not self.stopped.is_set():  # (the host stopping: said
                # as that, said_quitting)
                stopped = {"stopped": True} if getattr(made[0], "stopping", False) else {}  # (told to: op stop)
                for conn in s.live:
                    conn.send({"op": "exited", "session": s.id, "code": code, **stopped})
            self.changed()

    def drop_routes(self, s):
        """(lock held) What was asked of a session's claude that's gone: no answer's coming (the server's own requests
        are done without one)."""
        routes, s.routes = s.routes, {}
        s.reverting = {}
        for _conn, theirs, _undo in routes.values():
            if callable(theirs):
                theirs(False)

    def rename(self, s, title, done):
        """(lock held) A session's title, as Claude Code keeps it: its claude's to write in its transcript if it's
        running (rename_session); else the record claude writes for a rename, put in its transcript here; the server's
        own for a transcript it keeps (or before there's any). done() once it is."""
        title = " ".join(str(title or "").split())[:200]
        if not title:
            raise ValueError(wording.say("host_errors.empty_title"))

        def write(by_claude):
            path = None if s.kept() else s.transcript()
            if path and not by_claude:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": "custom-title", "customTitle": title,
                                        "sessionId": s.meta.get("cli_id") or s.id},
                                       separators=(",", ":"), ensure_ascii=False) + "\n")
                if s.proc is None:
                    s.note_written()  # (this host's own line, not another writer's)
            if path:
                s.meta.pop("title", None)  # (the transcript says it now, and whatever a /rename in it says next)
            else:
                s.meta["title"] = title
            self.save(s)
            self.rescan(s)
            self.changed()
            done()
        if not s.kept() and s.proc is not None and not getattr(s.proc, "stopping", False):
            on = f"srv_{next(self.rids)}"
            s.routes[on] = (None, write, None)  # (one it can't do, or it's gone before it answers: written here)
            s.proc.send({"type": "control_request", "request_id": on,
                         "request": {"subtype": "rename_session", "title": title}})

            def unanswered():
                with self.lock:
                    if s.routes.pop(on, None) is not None:
                        write(False)
            threading.Timer(RENAME_S, unanswered).start()
            return
        write(False)

    def changed(self):
        """(lock held) Every subscriber gets the sessions as they are now (soon: a burst of changes is one), and
        on_change hears, whether anyone's attached or not."""
        self.dirty = True
        self.woken.set()

    def _watch(self):
        """While someone's attached, twice a second: transcripts that grew are read (what's listed comes from them),
        their subagents' files looked at, and subscribers hear what changed, or a ping. Idle claudes are stopped. While
        nobody is, it waits, and wakes only when someone comes, a claude starts or stops, the next idle claude's due to
        stop, (nothing running, for exit_s) the host process is to go, or every CHECK_SOCKET_S, to see its socket's
        still there (still_bound)."""
        pinged, quiet, checked = 0.0, time.monotonic(), time.monotonic()
        while not self.stopped.is_set():
            with self.lock:
                attached, running = bool(self.conns), [s for s in self.sessions.values() if s.proc is not None]
            quiet = max(quiet, self.accepted)  # (a connection, however short, since: not quiet)
            if attached:
                quiet = time.monotonic()
                self.stopped.wait(0.5)
            else:
                waits = [s.used + self.idle_s - time.time() for s in running if self.stoppable(s)]
                waits.append(checked + CHECK_SOCKET_S - time.monotonic())
                if (soon := self.routines_due()) is not None:
                    waits.append(min(soon, 60))  # (looked at again each minute: a machine asleep meanwhile isn't
                    # counted by the clock it waits on)
                if not running and self.exit_s:
                    waits.append(quiet + self.exit_s - time.monotonic())
                elif running:
                    quiet = time.monotonic()  # (something's running: not quiet yet)
                self.woken.wait(max(1.0, min(waits)))
                self.woken.clear()
                quiet = max(quiet, self.accepted)
                with self.lock:
                    now_running = any(s.proc is not None for s in self.sessions.values())
                if not now_running and self.exit_s and time.monotonic() - quiet >= self.exit_s and not self.conns \
                        and not self.mesh.links and not self.mesh.outs and self.routines_due() is None:  # (linked,
                    # or making links: the mesh's members may want it; a routine to run: it's to be here for it)
                    self.log(f"nothing running and nobody here for {self.exit_s / 3600:g} h: going")
                    threading.Thread(target=self.shutdown, daemon=True, name="exit").start()
                    return
            if self.stopped.is_set():
                return
            if attached:
                self.routines_due()
            if time.monotonic() - checked > (10 if attached else CHECK_SOCKET_S - 1):
                checked = time.monotonic()
                self.still_bound()
            try:
                pinged = self._look(pinged)
            except Exception as e:  # (whatever it was, it's looked at again next time)
                self.log(f"watching: {type(e).__name__}: {e}")

    def stoppable(self, s):
        """(lock held) Would an idle look stop a session's claude, once it's idle long enough (_look)? Not one at a
        turn, asking, watched live, stopping already, or whose program forgets what it's said (apple-claude, and an
        ephemeral session's, kept nowhere)."""
        return s.proc is not None and not s.busy and not s.asks and not s.live and not s.meta.get("ephemeral") and \
            not getattr(s.proc, "stopping", False) and models.program(s.meta.get("model"))["resumes"]

    def _notes(self):
        """The notes ops, one at a time in the order they came (a change after another applied after it, a listing
        after the changes asked for before it)."""
        while True:
            msg, reply = self.notes_ops.get()
            try:
                reply("notes", **(notes.write(msg.get("changes")) if msg.get("action") == "write"
                                  else notes.listing(msg.get("have"))))
            except Exception as e:  # noqa: BLE001 (said to the Record that asked)
                reply("error", error=f"{type(e).__name__}: {e}")

    def _look(self, pinged):
        """One look (_watch): (when subscribers were last pinged). Record's notebooks here written since the last:
        subscribers told too (info's notes), so a Record elsewhere showing them lists them again."""
        if (written := notes.file_key()) != self.notes_file:  # (read again only once it's written)
            self.notes_file = written
            if (seen := notes.stamp()) != self.notes_seen:
                with self.lock:
                    self.notes_seen, self.dirty = seen, True
        with self.lock:
            for s in list(self.sessions.values()):
                grew = self.rescan(s)
                self.dirty |= grew
                if s.agents is None or s.proc is not None or grew:  # (only a running claude's subagents work)
                    if (agents := s.subagent_files(s.meta.get("path"))) != s.agents:
                        s.agents, self.dirty = agents, True
                if self.stoppable(s) and time.time() - s.used > self.idle_s:  # (one that'd forget it all, not)
                    self.log(f"{s.id} idle: its claude stopped")
                    s.proc.stopping = True
                    s.proc.close_stdin()
                    threading.Timer(10, s.proc.kill).start()
                    s.used = time.time()
            send, self.dirty = self.dirty, False
            conns = list(self.conns)
        if send:
            every, info = self.listing(archived=True), self.info()
            shown = [x for x in every if not x["archived"]]
            for c in conns:
                if c.subscribed:
                    c.send({"op": "sessions", "sessions": every if c.archived else shown, "server": info})
            self.on_change()
        if time.monotonic() - pinged >= wire.PING_S:  # (every connection: a client takes silence for gone)
            pinged = time.monotonic()
            for c in conns:
                c.send({"op": "ping"})
        return pinged

    def rescan(self, s):
        """(lock held) A session's transcript read on, if it's changed since it was last read (what's listed comes from
        it): True if it had."""
        path = s.transcript()
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None
        stat = (st.st_size, st.st_mtime) if st else None
        if stat == s.stat:
            return False
        if st and s.stat and st.st_size < s.stat[0]:
            s.scan = None  # (rewritten: read again)
        s.stat = stat
        if path and st:
            s.scan, s.info = transcripts.scan_session(path, s.scan)
        return True

    # ---- reading
    def read_transcript(self, s, offset, limit=None):
        """Whole lines of the transcript from offset: {offset, next, size, data}."""
        return self.read_lines(s.transcript(), offset, limit)

    def read_subagents(self, s, have, limit=None):
        """Its subagents' files, each from where a client's copy of it is (have: {name: [size, mtime_ns]}): a
        transcript's whole lines after that (read_lines), a meta.json whole if it's not as the client has it; limit,
        all of them together, at most: {files: [{name, size, mtime, offset, next, data}], more}. more: there's more to
        fetch (those limit didn't reach aren't listed)."""
        have = have if isinstance(have, dict) else {}
        room = max(4096, min(int(limit or TRANSCRIPT_CHUNK), 16 << 20))
        files, folder, more = [], s.subagents_dir(), False
        if (found := s.subagent_files()) != s.agents:  # (listed as they're served: a client's checked against it)
            s.agents = found
            self.changed()
        for name, (size, mtime) in sorted(found.items()):
            had = have.get(name)
            at, when = had if isinstance(had, list) and len(had) == 2 and isinstance(had[0], int) else (0, None)
            whole = name.endswith(".jsonl")
            new = (at != size) if whole else (at, when) != (size, mtime)
            if new and room <= 0:
                more = True
                break
            f = {"name": name, "size": size, "mtime": mtime, "offset": at, "next": at, "data": ""}
            if new and whole:
                got = self.read_lines(os.path.join(folder, name), at if at < size else 0, room)
                f.update(offset=got["offset"], next=got["next"], size=max(size, got["size"]), data=got["data"])
                room -= got["next"] - got["offset"]
                more |= bool(got["data"]) and got["next"] < got["size"]  # (cut short by limit: the rest next time)
            elif new:
                try:
                    with open(os.path.join(folder, name), "rb") as fh:
                        data = fh.read(FILE_LIMIT)
                except OSError:
                    continue  # (gone meanwhile)
                f.update(offset=0, next=len(data), data=base64.b64encode(data).decode("ascii"))
                room -= len(data)
            files.append(f)
        return {"files": files, "more": more}

    def read_lines(self, path, offset, limit=None):
        """Whole lines of a file from offset: {offset, next, size, data}."""
        limit = max(4096, min(int(limit or TRANSCRIPT_CHUNK), 16 << 20))
        try:
            with open(path, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                offset = 0 if offset > size else int(offset)
                f.seek(offset)
                data = more = f.read(limit)
                while more and b"\n" not in more and len(data) < (64 << 20):
                    more = f.read(limit)  # (a line longer than a chunk: all of it)
                    data += more
        except (OSError, TypeError):
            return {"offset": 0, "next": 0, "size": 0, "data": ""}
        whole = data[:data.rfind(b"\n") + 1]
        return {"offset": offset, "next": offset + len(whole), "size": size,
                "data": base64.b64encode(whole).decode("ascii")}  # (as it is, byte for byte: the mirror's size is where it's up to)

    def read_file(self, path, limit=None):
        """A file in one of its sessions' folders (what a session's links name), not anywhere: there's no sign-in."""
        path = os.path.expanduser(path or "")
        if not os.path.isabs(path):
            return {"path": path, "error": wording.say("host_errors.not_absolute")}
        if not self.in_sessions(os.path.realpath(path)):
            return {"path": path, "error": wording.say("host_errors.not_in_session_folder")}
        try:
            with open(path, "rb") as f:
                data = f.read(min(int(limit or FILE_LIMIT), FILE_LIMIT) + 1)
            size = os.path.getsize(path)
        except OSError as e:
            return {"path": path, "error": e.strerror or str(e)}
        return {"path": path, "size": size, "truncated": len(data) > FILE_LIMIT,
                "data": base64.b64encode(data[:FILE_LIMIT]).decode("ascii")}

    def task_output(self, sid, path, limit=None):
        """The last part of a background task's output file: one its session's transcript names, not any file."""
        with self.lock:
            s = self.sessions.get(sid)
        if s is None or path not in s.output_files():
            return {"path": path, "error": wording.say("host_errors.not_task_output")}
        try:
            n = min(int(limit or 4000), FILE_LIMIT)
            with open(path, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                f.seek(max(0, size - n))
                data = f.read()
        except (OSError, ValueError) as e:
            return {"path": path, "error": getattr(e, "strerror", None) or str(e)}
        return {"path": path, "size": size, "truncated": size > n, "data": base64.b64encode(data).decode("ascii")}

    def in_sessions(self, path):
        """Is path (real) one of its sessions' folders, or in one?"""
        with self.lock:
            folders = {os.path.realpath(s.meta["cwd"]) for s in self.sessions.values() if s.meta.get("cwd")}
        return any(path == f or path.startswith(f.rstrip(os.sep) + os.sep) for f in folders)

    def catalogue(self, cwd, wait=False):
        """What @ lists for a session's folder, as Baude lists its own chat's (lookup.items: its files, branches,
        Kubernetes objects, what's running on this machine, its pull requests and issues), made here. What there is is
        sent at once; with nothing made yet, or wait, it's waited for while it's made (and GitHub's first read of it),
        up to CATALOGUE_WAIT_S. busy: there's more to come. At most CATALOGUE_LIMIT files, and every other name."""
        if not isinstance(cwd, str) or not os.path.isabs(os.path.expanduser(cwd)):
            return {"cwd": cwd, "items": [], "error": wording.say("host_errors.not_absolute")}
        real = os.path.realpath(os.path.expanduser(cwd))
        if not self.in_sessions(real) or not os.path.isdir(real):
            return {"cwd": cwd, "items": [], "error": wording.say("host_errors.not_session_folder")}
        root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        import lookup
        with self.catalogue_lock:  # (two heads asking at once: one Folder, made once)
            folder = lookup.Folder.for_folder(real)
            folder.catalogue_soon()
            first = self.catalogue_asked.setdefault(folder.root, time.monotonic())
        lookup.GITHUB.look([folder.root])

        def busy():
            github = lookup.gh_path() is not None and lookup.GITHUB.listed(folder.root) is lookup.GITHUB.NOTHING and \
                time.monotonic() - first < CATALOGUE_WAIT_S  # (its first read: a folder that's on GitHub's, soon)
            return folder.catalogue_busy or github
        if wait or not folder.catalogue:
            until = time.monotonic() + CATALOGUE_WAIT_S
            while busy() and time.monotonic() < until:
                time.sleep(0.1)
        items, files = [], 0
        for it in lookup.items(folder):
            if it["kind"] == "file":
                files += 1
                if files > CATALOGUE_LIMIT:
                    continue
            entry = it.get("entry")
            if entry is not None:  # (what a list shows of it: its first source, and GitHub's)
                entry = {k: entry[k] for k in ("kind", "hint", "github", "at") if k in entry} | \
                    {"sources": list(entry.get("sources") or ())[:1]}
            item = {**it, "entry": entry}
            try:
                wire.encode(item)
            except (TypeError, ValueError):  # (a file name that isn't UTF-8, say: that one left out)
                continue
            items.append(item)
        return {"cwd": cwd, "items": items, "busy": busy()}
