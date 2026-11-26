"""What the host process (server.py's Server) is made of: its settings (IDLE_S, EXIT_S and the rest), where it keeps
what it keeps (state_home, kept_sessions, running_pid), a head's connection (Conn), a session (Session), what a
Bot's agent is told (bot_dir, bot_system), and AlreadyRunning.
"""
import fcntl
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

from baudelib import accounts, autofix, desktopchats, models, transcripts, wire


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
CONTEXT_EVERY_S = 1.0  # (through a turn, its agent's asked how much of its window it's used at most this often)
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

    def __init__(self, channel, origin=None, sender=None, verified=True):
        self.channel, self.out = channel, queue.Queue()
        self.subscribed, self.mute = False, set()  # (mute: the chats it's attached to whose asks it won't answer)
        self.origin, self.sender = origin, sender  # (a tunnel's: the member it came from; the chat there its relay
        # named, as that member has it: what's sent through it is from)
        self.member, self.verified = origin, verified  # (who the access rules judge it as: a tunnel's origin, or the
        # member that started the chat whose own tools it is (hello's as); verified: by its key, mesh.Mesh.opener)
        self.acting = None  # (the chat here whose own tools it is, as its hello said)
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
        self.turn_began = None    # (when its turn going on began: autofix.turn_over's)
        self.acting = False       # at a turn's tools: a tool call begun, no text begun since (turn_acting)
        self.streamed = False     # (its agent's said what it writes as it writes it: stream_event)
        self.waiting_init = []    # (conn, its request id): initialize asked before claude answered its own
        self.queued = []          # (message, uuid, conn that sent it) sent at a turn, kept once it's over (Server.keep)
        self.codex_queue = []     # (msg, conn that sent it): Codex runs each follow-up as a separate turn.
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
        self.account_to = None    # (an account picked for it, not yet taken: switch_account; "": the CLI's own sign-in)
        self.context_asked = 0.0  # (when its agent was last asked its context's usage: ask_context)
        self.context_soon = False  # (asked again a moment from now, through a turn)
        self.account_after_turn = False  # (that one taken once its turn and the follow-ups sent at it are answered)
        self.sent_at_turn = False  # (its claude sent a follow-up at the turn going on: one more turn of its own to come)
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
        """Has its agent the mesh's tools (baudelib.meshtools): a Bot's, and a head's Meta's (its chat run with the
        head's own tools: start's app_tools); no other chat's."""
        return bool(self.meta.get("bot")) or self.app_tools is not None

    def bot_listing(self):
        """A Bot's name, and when its picture last changed (None: no picture): what a head draws it with."""
        bot = self.meta.get("bot")
        if not bot:
            return None
        pic = os.path.join(bot_dir(self.server.state_dir, self.id), "picture")
        return {"name": bot.get("name") or "Bot", "since": bot.get("since"),
                "picture": os.path.getmtime(pic) if os.path.exists(pic) else None}

    def made(self):
        """When it was made: the earlier of when Baude took it up and its transcript's first record (a chat Baude adopted
        was made when it was begun, not when it was adopted)."""
        born = transcripts.ref_epoch((self.info or {}).get("born"))
        return min((t for t in (self.meta.get("created"), born) if t), default=None)

    def account(self):
        """The subscription account its agent runs under here (Account's fields), or None: the CLI's own sign-in, a
        model that isn't Claude's or Codex's, or an account since removed."""
        return accounts.account(self.profile())

    def profile(self):
        """The id of the account its agent runs under ("": the CLI's own sign-in, or a model that isn't Claude's or
        Codex's): one picked for it, as it waits for its turn's end (account_to), else its own."""
        program = models.model_program(self.meta.get("model"))
        if program not in accounts.PROVIDERS:
            return ""
        return self.account_to if self.account_to is not None else (self.meta.get("profiles") or {}).get(program, "")

    def listing(self):
        info = self.info or {}
        size, mtime = self.stat or (0, None)
        prompt = " ".join((self.meta.get("prompt") or "").split())
        return {"id": self.id, "title": self.meta.get("title") or info.get("title") or prompt[:90] or None,
                "cwd": self.meta.get("cwd"), "model": self.meta.get("model"), "effort": self.meta.get("effort"),
                "mode": self.meta.get("mode"), "group": self.meta.get("group"), "created": self.made(),
                "said": max(info.get("said") or 0, self.meta.get("said") or 0) or self.meta.get("created"),
                "updated": mtime or self.meta.get("created"), "size": size, "state": self.state(),
                "prompts": info.get("prompts") or 0, "cli_id": self.meta.get("cli_id"),
                "transcript": self.found_transcript(),
                "archived": bool(self.meta.get("archived")), "draft": bool(self.meta.get("draft")),
                "written": self.meta.get("written"),
                "subagents": max((m for _size, m in (self.agents or {}).values()), default=0),
                "commands": self.commands(), "cut": self.meta.get("cut"), "bot": self.bot_listing(),
                "autofix": autofix.listing(self.meta.get("autofix")),
                "acting": self.acting and self.busy and not self.asks,
                "context": context_listing(self.meta.get("context_usage")),
                "on_phone": self.server.phones.phone(self.id) is not None,
                "fork_from": self.meta.get("fork_from"), "spawned_by": self.meta.get("spawned_by"),
                "desktop": bool(self.meta.get("desktop")), "elsewhere": desktopchats.elsewhere(self),
                "start_key": self.meta.get("start_key"), "account": self.account(),
                "account_gone": bool(self.profile()) and self.account() is None,
                "held": [{"id": h["id"], "text": h["text"], "shown": h.get("shown"), "sender": h.get("sender"),
                          "at": h.get("at")} for h in self.meta.get("held") or []],
                "senders": self.meta.get("senders") or 0,
                "always_allow": sorted(self.always_allow),
                "asks": [{"request_id": rid, "tool": (m.get("request") or {}).get("tool_name"),
                          "input": _shown((m.get("request") or {}).get("input"))} for rid, m in self.asks.items()]}


def context_listing(usage):
    """What every head's told of a session's context (ContextUsage, as context_kept has it): its count and window,
    without its categories (those are said to the chat that's open, on get_context_usage)."""
    kept = context_kept(usage)
    return None if kept is None else {k: v for k, v in kept.items() if k != "categories"}


def context_kept(usage):
    """What an agent said of its context (get_context_usage's answer, Codex's or pi-claude's count) as it's kept
    (ContextUsage); None for one with neither a window nor a wish to be shown without one (a stopped one's {})."""
    if not isinstance(usage, dict):
        return None
    used, total = usage.get("totalTokens"), usage.get("maxTokens")
    total = total if isinstance(total, int) and not isinstance(total, bool) and total > 0 else None
    if total is None and not usage.get("showWithoutLimit"):
        return None
    return {"totalTokens": used if isinstance(used, int) and not isinstance(used, bool) else None, "maxTokens": total,
            "showWithoutLimit": bool(usage.get("showWithoutLimit")),
            "categories": [{"name": c["name"], "tokens": int(c.get("tokens") or 0),
                            "kind": c.get("kind") if isinstance(c.get("kind"), str) else None}
                           for c in usage.get("categories") or [] if isinstance(c, dict) and
                           isinstance(c.get("name"), str) and isinstance(c.get("tokens") or 0, (int, float))]}


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
