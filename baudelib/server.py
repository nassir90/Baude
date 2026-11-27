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
Code's, and the server keeps one in the same form for a session whose program keeps none (apple-claude, pi-claude).

What it's made of (its settings, a head's connection, a session) is in serverstate.py; its running (serving, shutting
down, a head's requests) in serverrun.py (ServerRunning), and its sessions in serversessions.py (ServerSessions)."""
import fcntl
import itertools
import json
import os
import queue
import socket
import sys
import threading
import time
import uuid

from baudelib import (
    access, accounts, codex, deciders, desktopchats, emissionlink, inbox, mesh, multiplexlink, phonebrowser, models, notes, providers,
    selfupdate, tasks, telemetry, toolnames, wire,
    wording,
)
from baudelib.serverstate import (
    AlreadyRunning, DRAFT_S, EMISSIONS_S, MULTIPLEX_S, EPHEMERAL_S, EXIT_S, IDLE_S, Session, _read_json, _write_json, bot_dir,
    kept_sessions, lock_path, state_home, trash,
)
from baudelib.serverrun import ServerRunning
from baudelib.serversessions import ServerSessions


class Server(ServerRunning, ServerSessions):
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
        self.codex_catalogs = {}
        self.codex_refresh_at = {}
        self.codex_refreshing = set()
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
        self.settle_rotations()  # (a Bot's rotation the host stopped in the middle of: finished, or undone)
        self.desktop = desktopchats.DesktopChats(self)  # (this machine's Claude Desktop chats, listed to heads too)
        self.conns = set()
        self.dirty = False  # (subscribers have yet to hear of a change: _watch)
        self.notes_file, self.notes_seen = notes.file_key(), notes.stamp()  # (records.json here as last looked at:
        # info's notes, _look)
        self.edits_seen = notes.edits_stamp()  # (edits.json, as this wrote it last: info's edits)
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
        # (a restart kept till nothing blocks it: {owners: what asked for it, head or update, to}; the heads holding it
        # while they show what it waits for)
        self.pending_restart, self.restart_holds = None, set()
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
        self.autofix_reading = set()  # (chats whose pull request Auto-fix is reading now: autofix.due)
        self.following = set()  # ((session, the one it waits on): waits of its agent's answered for it, answer_wait)
        self.deciders = deciders.Deciders(self.state_dir)  # (its decision models' servers: baudelib/deciders.py)
        self.mesh = mesh.Mesh(self)  # (the hosts it's linked to, and those they reach: mesh.py)
        self.multiplex_pids = set()  # (the processes of this machine's Multiplex windows, likewise)
        self.emission_pids = set()  # (the processes of this machine's Emissions, as they last said: sampled too)
        self.telemetry = telemetry.Telemetry(self.state_dir, self.me["id"], changed=self.changed, log=self.log,
                                             found=self.found_procs)  # (its machine's chat events, samples and line
        # profiles: baudelib/telemetry.py)
        self.access = access.Access(self)  # (who in the mesh may use what of its: baudelib/access.py)
        self.tasks = tasks.Tasks(self)  # (its Bots' tasks: baudelib/tasks.py)
        self.inbox = inbox.Inbox(self)  # (what its Bots' tasks ask of a person: baudelib/inbox.py)
        self.settle_bot_folders()  # (each Bot's folder by its name)

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

    def codex_models(self, profile=None):
        exe = models.codex_exe()
        if not exe:
            return []
        profile = accounts.selected("codex") if profile is None else profile
        with self.lock:
            if profile not in self.codex_refreshing and time.monotonic() >= self.codex_refresh_at.get(profile, 0):
                self.codex_refreshing.add(profile)
                threading.Thread(target=self.refresh_codex_models, args=(exe, profile), daemon=True).start()
            return self.codex_catalogs.get(profile) or [models.CODEX_DEFAULT]

    def refresh_codex_models(self, exe, profile):
        choices, retry = None, 60
        try:
            choices = models.codex_choices(codex.list_models(exe, self.env, profile=profile))
            retry = 300
        except (OSError, RuntimeError) as exc:
            self.log(f"Codex model discovery: {exc}")
        finally:
            with self.lock:
                self.codex_refresh_at[profile] = time.monotonic() + retry
                self.codex_refreshing.discard(profile)
                if choices is not None and choices != self.codex_catalogs.get(profile):
                    self.codex_catalogs[profile] = choices
                    for session in self.sessions.values():
                        if isinstance(session.proc, codex.CodexProc) and session.proc.profile == profile:
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
                **self.mesh.here_behind(), "update": self.update, "restart": self.restart_state(),
                "providers": self.mesh.providers_here(), "mesh": self.mesh.view(),
                "notes": self.notes_seen,  # (Record's notebooks here, as written last: notes.stamp)
                "edits": self.edits_seen,  # (the edits proposed to their pages, as written last: notes.edits_stamp)
                "telemetry": self.telemetry.state(),  # (on or off, and what each process here profiles: its apps follow it)
                "front": self.telemetry.front(),  # (the chats in front on this machine: no head anywhere notifies of them)
                "tool_names": toolnames.table(self.me.get("mcp_servers") or ())}  # (how its MCP tools are named)

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
        """This machine's Emissions, and the code each runs, asked at once and then every EMISSIONS_S (they're no
        heads: the host process reaches them, emissionlink)."""
        while True:
            infos = emissionlink.running()
            self.emission_pids = {i["pid"] for i in infos if isinstance(i.get("pid"), int) and i["pid"] > 0}
            found = [{"client": "emission", "commit": i["commit"] if isinstance(i.get("commit"), str) else None}
                     for i in infos]
            if found != self.emissions:
                self.emissions = found
                self.heads_look()
            if self.stopped.wait(EMISSIONS_S):
                return

    def _multiplexes(self):
        """The processes of this machine's Multiplex windows, asked at once and then every MULTIPLEX_S, each
        briefly (those of Baude's and Record's own, already sampled, aren't asked)."""
        while True:
            try:
                skip = set(self.telemetry.pids)
                infos = [i for _path, i in multiplexlink.processes(timeout=1, skip=skip) if isinstance(i, dict)]
                self.multiplex_pids = {i["pid"] for i in infos if i.get("app") == "multiplex"
                                       and isinstance(i.get("pid"), int) and i["pid"] > 0}
            except Exception as e:  # noqa: BLE001 (what couldn't be found: the next time's)
                self.log(f"multiplex: {type(e).__name__}: {e}")
            if self.stopped.wait(MULTIPLEX_S):
                return

    def found_procs(self):
        """{pid: app} of this machine's processes that tell it no telemetry of their own, for its samples: its
        Emissions' and Multiplex's windows' (as they last said)."""
        return {**dict.fromkeys(self.multiplex_pids, "multiplex"), **dict.fromkeys(self.emission_pids, "emission")}

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
        return [{"session": s.id, "what": c["what"]} for s in self.sessions.values() for c in s.commands()] + \
            self.tasks.busy()  # (and its tasks' runs)

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
            names = {s.meta.get("cli_id") or s.id, s.id} - \
                {o.meta.get("cli_id") for o in self.sessions.values() if o is not s}  # (a Bot's past thread's: its own)
        paths = [path, path[:-len(".jsonl")] if path and path.endswith(".jsonl") else None,
                 bot_dir(self.state_dir, s.meta),  # (a Bot's, or a chat's that was one: its memory and its picture)
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
        # (one deleted, a side question's, a new chat's not written in yet, or a Desktop chat only listed: not kept)
        if not s.deleted and not s.meta.get("ephemeral") and not s.meta.get("draft") and not s.meta.get("listed_only"):
            _write_json(os.path.join(self.state_dir, "sessions", f"{s.id}.json"), s.meta)

    def listing(self, archived=False):
        """Its sessions, newest first; those archived only if asked for (archived); never a side question's
        (ephemeral). A new chat not written in yet is, as draft (a head leaves it out of its lists)."""
        with self.lock:
            return sorted((s.listing() for s in self.sessions.values()
                           if (archived or not s.meta.get("archived")) and not s.meta.get("ephemeral")),
                          key=lambda i: -(i["said"] or 0))

    def heads_listing(self, archived=False):
        """What a head is told the host has (sessions): its sessions (listing), then this machine's Claude Desktop chats
        it hasn't taken up (desktopchats), as Desktop's list was last read."""
        with self.lock:
            return self.listing(archived) + sorted(self.desktop.listings(archived), key=lambda i: -(i["said"] or 0))

    def shown_to(self, conn, listing):
        """(lock held) Of a listing, the chats conn may read (access manage_read): those it's told of."""
        if conn.member is None:
            return listing
        return [x for x in listing if self.access.allowed(
            conn, "manage_read", self.sessions.get(x["id"]) or self.desktop.chats.get(x["id"]))]
