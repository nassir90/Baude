"""The host process running (Server's ServerRunning): serving its socket, shutting down and restarting onto new code,
a head's requests (handle), feedback, tasks and Bots.
"""
import base64
import copy
import json
import os
import re
import select
import socket
import threading
import time
import uuid

from baudelib import (
    access, accounts, agentproxy, autofix, deciders, deps, desktopchats, emissionlink, emissiontools, hostaccounts, hostdb, hostdocker, hostfiles, hostfixes, hostkube,
    hostlogin, hostqueue, mesh, meshtools, models, hostterminal, multiplexlink, notes, selfupdate, telemetry, themes,
    wire, wording, worktree,
)
from baudelib.serverstate import (
    APPS, BOT_PICTURE_MAX, Conn, FRAME_KEYS, Session, _mtime, _write_json, _write_text, bot_dir, bot_folder_name, clean_env,
)


def catalogued_kube(item):
    """Is a catalogue's item of a cluster's (a Kubernetes object, a context, a workload)?"""
    return item.get("kind") == "k8s" or item.get("icon") == "k8s"


class ServerRunning:
    """Server's running: serving its socket, shutting down, a head's requests, feedback, tasks and Bots."""

    # ---- running
    def serve_forever(self):
        """Take connections till it's shut down."""
        threading.Thread(target=self._watch, daemon=True, name="watch").start()
        threading.Thread(target=self._emissions, daemon=True, name="emissions").start()
        threading.Thread(target=self._multiplexes, daemon=True, name="multiplexes").start()
        self.mesh.start()
        self.telemetry.start()
        with self.lock:  # (waits its agents were at as it last stopped, which stopped them: answered for them)
            waits = [(s, w) for s in self.sessions.values() for w in s.meta.get("waits_on") or []]
        for s, w in waits:
            threading.Thread(target=self.answer_wait, args=(s, w), daemon=True, name="wait").start()
        with self.lock:  # (follow-ups a restart cut short: sent now, keep_follow_ups)
            self.send_follow_ups()
            hostqueue.send_left(self)  # (queues a restart cut short: sent as at a turn's end)
            self.tasks.resume_told()  # (what chats a task's run started had still to tell their Bots: told now)
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

    def halt(self, if_idle=False, restart=None, pending=False):
        """(shutdown's first half, all at once, so nothing starts between the look and the stop) Nothing more taken,
        unless (if_idle) a command's running, or (pending: its kept restart going through) a head's paused it or
        something blocks it since it was looked at; every session whose claude is at a turn or asking marked cut, and
        kept so, and its follow-ups kept for after a restart (keep_follow_ups): (busy, the claudes to stop). Those:
        None if it's not stopping (busy) or stopping already."""
        with self.lock:
            if self.stopped.is_set():
                if not if_idle and restart:  # (stopping already, and told to restart now: it starts again)
                    self.restarting = self.restarting or restart
                    self.keep_follow_ups()
                    if self.quitting is not None and not self.quitting["restart"]:
                        self.quitting = {**self.quitting, "restart": True}
                        self.said_quitting()
                elif not if_idle:  # (stopping for an update, and told to stop: it goes, and doesn't start again)
                    self.restarting = None
                    if self.quitting is not None and self.quitting["restart"]:
                        self.quitting = {**self.quitting, "restart": False}
                        self.said_quitting()
                return None, None
            if if_idle and (busy := self.running()):
                return busy, None
            if pending and (self.pending_restart is None or self.restart_holds or self.blockers()):
                return [], None
            self.stopped.set()
            self.restarting = restart
            if restart:
                self.keep_follow_ups()
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

    def keep_follow_ups(self):
        """(lock held) Each session's follow-ups sent at a turn a restart cuts short (queued): kept in its
        meta (follow_ups), and sent once it's started again (send_follow_ups), not lost with its claude."""
        for s in self.sessions.values():
            held = [{"message": m, "uuid": u} for m, u, _by in s.queued]
            if held:
                s.meta["follow_ups"] = (s.meta.get("follow_ups") or []) + held
                s.queued = []
                self.save(s)

    def send_follow_ups(self):
        """(lock held) Follow-ups a restart cut short (keep_follow_ups): sent, in order, as they were first."""
        for s in list(self.sessions.values()):
            held = s.meta.pop("follow_ups", None)
            if not held:
                continue
            self.save(s)
            for f in held:
                msg = {"type": "user", "message": f["message"],
                       **({"uuid": f["uuid"]} if wire.is_id(f.get("uuid") or "") else {})}
                try:
                    self.take_input(None, s, msg, again=wire.is_id(f.get("uuid") or ""))
                except Exception as e:  # (one that can't be sent: said in the log, the rest sent)
                    self.log(f"{s.id}: a follow-up kept over a restart not sent: {type(e).__name__}: {e}")
            self.log(f"{s.id}: {len(held)} follow-up{'s' if len(held) != 1 else ''} kept over a restart sent")

    # ---- a restart kept till nothing blocks it (info's restart): a head's Restart Daemon, or its own updater's
    def blockers(self):
        """(lock held) What a restart waits for (as every head lists it: RestartBlocker): each chat whose agent's
        running a command (its oldest), each task's run, each agent run here for a chat on another host (an Apple or
        pi model's), and each terminal here someone's attached to."""
        chats = [{"kind": "chat", "session": s.id, "name": s.listing()["title"] or "", "what": cmds[0]["what"]}
                 for s in self.sessions.values() if (cmds := s.commands())]
        agents = []
        for run in self.agent_runs.values():
            if not isinstance(run, agentproxy.Run) or run.gone:
                continue
            host = getattr(run.conn, "member", None)
            agents.append({"kind": "agent", "name": run.model, **({"session": run.chat} if run.chat else {}),
                           **({"host": host} if isinstance(host, str) else {}),
                           "what": wording.say("hosts.restart_agent", model=run.model,
                                               host=self.mesh.name_of(host) if isinstance(host, str) else "?")})
        return chats + self.tasks.blocking() + agents + hostterminal.blocking()

    def restart_state(self):
        """(lock held) info's restart: the restart kept (RestartState), or None. by: a head's, while one's asked for
        it, else its updater's."""
        p = self.pending_restart
        if p is None:
            return None
        return {"by": "head" if "head" in p["owners"] else "update", **({"to": p["to"]} if p.get("to") else {}),
                "paused": bool(self.restart_holds), "blockers": self.blockers()}

    def restart_when_free(self, by, to=None):
        """A restart kept (by: head, update, each kept for apart: drop_restart; to: the commit an update's onto), and
        gone through once nothing's running and no head's holding it (_restart_watch), heads told as what it waits for
        changes."""
        with self.lock:
            p = self.pending_restart
            self.pending_restart = {"owners": (p["owners"] if p else set()) | {by},
                                    "to": to or (p or {}).get("to")}
            if p is None:
                threading.Thread(target=self._restart_watch, daemon=True, name="restart").start()
            self.changed()
            return self.restart_state()

    def drop_restart(self, by):
        """by's restart let go of (its updater's: updates turned off, or the checkout moved on): one a head asked for
        stays. Gone once nothing's asking for it, the heads' pauses with it (a pause outlives nothing)."""
        with self.lock:
            p = self.pending_restart
            if p is None or by not in p["owners"]:
                return
            owners = p["owners"] - {by}
            if owners:
                self.pending_restart = {"owners": owners, "to": None if by == "update" else p.get("to")}
            else:
                self.pending_restart = None
                self.restart_holds.clear()
            self.changed()

    def hold_restart(self, conn, on):
        """(lock held) A head showing what the restart waits for holds it (on), or lets go of it."""
        was = bool(self.restart_holds)
        (self.restart_holds.add if on else self.restart_holds.discard)(conn)
        if was != bool(self.restart_holds):
            self.changed()

    def _restart_watch(self):
        said = None
        while not self.stopped.wait(0.5):
            with self.lock:
                if self.pending_restart is None:
                    return
                state = self.restart_state()
                if state != said:
                    said = state
                    self.changed()
                if state["paused"] or state["blockers"]:
                    continue
            if self.restart_now(force=False):
                return

    def restart_now(self, force=True):
        """The host process stopped to start again on the code checked out (baude_headless.py's serve: restarting);
        not force: only if nothing's running and no head's paused it (halt looks again, as it stops). True once it's
        stopping."""
        with self.lock:
            to = (self.pending_restart or {}).get("to")
        to = to or selfupdate.git(selfupdate.HERE, "rev-parse", "HEAD", timeout=5) or self.commit or "here"
        busy, procs = self.halt(if_idle=not force, restart=to, pending=not force)
        if busy is not None:
            return False
        if procs is not None:
            self.stop_procs(procs)
        self.log(f"restart{' forced' if force else ''}: stopping to start again")
        return True

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
        self.telemetry.stop()
        self.tasks.stop_all()  # (its tasks' runs: stopped, and kept so)
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

    def serve(self, sock, origin=None, sender=None, verified=True):
        """A connection served (a tunnel's through the mesh: origin, the member it came from; sender, who what's sent
        through it is from, as that member said; verified, whether its open was signed by origin's key: Conn's)."""
        sock.settimeout(wire.SILENT_S)  # (a client that's said nothing for this long is gone: they ping)
        ch = wire.Channel.over(sock)
        ch.serving = True
        conn = Conn(ch, origin, sender, verified)
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
                    if msg["op"] == "relay" and conn.task is not None:  # (a task's script's: from its run's Bot)
                        msg = {**msg, "as": conn.task["bot"]["id"]}
                        msg.pop("as_meta", None), msg.pop("as_agent", None)
                    if conn.member is not None or msg["op"] == "relay" and self.started_by(msg.get("as")):  # (a
                        # member's, or a chat a member started: what's relayed from here is this host's, and a join is
                        # a host's own; neither borrowed)
                        conn.send(wire.refused(wire.DENIED, msg.get("req"), wording.say("access.not_passed_on")))
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
                except deps.Missing as e:  # (a program a chat runs isn't here: what to install, said as it is)
                    conn.send({"op": "error", "req": msg.get("req"), "error": str(e), "plain": True, "missing": e.dep})
                except accounts.Disabled as e:  # (a chat on a disabled subscription: said as it is)
                    conn.send({"op": "error", "req": msg.get("req"), "error": str(e), "plain": True})
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
            hostkube.stop(conn)  # (its pod logs and watches: kubectl ended with it)
            hostdocker.stop(conn)  # (its containers' logs: docker ended with it)
            hostterminal.gone(conn)  # (its terminal: left running a while, to be attached to again)
            hostlogin.stop(conn)  # (a sign-in it ran: stopped)
            hostaccounts.stop(conn)  # (a Codex sign-in it ran: stopped)
            if getattr(conn, "emission_watch", None) is not None:  # (its pane gone: Emission's watcher with it)
                try:
                    conn.emission_watch.shutdown(socket.SHUT_RDWR)  # (its recv woken, Emission told)
                    conn.emission_watch.close()
                except OSError:
                    pass
            with self.lock:
                self.conns.discard(conn)
                self.hold_restart(conn, False)  # (a head gone: its pause with it)
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
        """A Bot's Emission here (started if it isn't; bot None: Emission's own window on its Default profile)
        watched for conn: its frames (as JPEG) and state, a message each (emission_frame), till either end goes
        (emission_watch_end)."""
        size = {k: int(msg[k]) for k in ("max_w", "max_h") if isinstance(msg.get(k), int)}
        try:
            if bot is None:  # (Emission's own window here, on its Default profile: ☰ → Browser elsewhere)
                path = emissionlink.default_window(emissiontools.HOST_WAIT_S)
            else:
                emissionlink.call_bot(bot["id"], bot.get("name"), {"op": "info"}, timeout=emissiontools.HOST_WAIT_S)
                path = emissionlink.profile_socket(emissionlink.bot_profile(bot["id"], None))
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(path)
            sock.sendall((json.dumps({"op": "watch", "from_pane": bot is None, **size}) + "\n").encode())
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
                           f'"baude-note-{note}" in:body', "--json", "number,url,body", feature="fixes.send_feedback")
        if not ok:
            return reply("error", error=out or wording.say("host_errors.gh_list_failed"))
        try:
            made = next((i for i in json.loads(out or "[]") if (i.get("body") or "").rstrip().endswith(token)), None)
        except ValueError:
            made = None
        if made is None:
            ok, out = fixes.gh("issue", "create", "--repo", repo, "--title", title, "--body-file", "-", body=body,
                               feature="fixes.send_feedback")
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

    def deciders_op(self, msg, reply):
        """deciders: this host's (with their URLs), then the other members' as their records say; one added, removed
        or tested first."""
        what, tested, error = msg.get("what") or "list", None, None
        try:
            if what == "add":
                tested = self.deciders.add(msg.get("name"), msg.get("url"))
            elif what == "remove":
                self.deciders.remove(msg.get("id"))
            elif what == "test":
                tested = self.deciders.test(msg.get("id"))
        except (ValueError, OSError) as e:
            error = str(e)
        own = self.deciders.listing(fresh=what == "list")
        if what != "list" or not error:
            self.mesh.deciders_changed(own)
        listed = [{**d, "host": None, "host_name": self.me["name"]} for d in own]
        for member, name, decs in self.mesh.member_deciders():
            listed += [{**d, "value": f"{name}/{d['id']}", "host": member, "host_name": name, "url": None}
                       for d in decs]
        reply("deciders", what=what, deciders=listed, tested=tested, **({"error": error} if error else {}))

    def decide(self, msg, reply):
        """decide: asked of one of this host's deciders, or carried to the member that has it; decided either way
        (unavailable: why it couldn't be asked)."""
        name = msg.get("decider") if isinstance(msg.get("decider"), str) and msg["decider"].strip() else None
        here = self.me["name"]
        host, _, which = (name or "").rpartition("/")
        member = None
        if host and host.lower() not in (here.lower(), self.me["id"]):
            member = self.mesh.member_named(host)
            if member is None:
                return reply("decided", decider=which, host=host, unavailable=wording.say("deciders.host_gone", host=host))
        if member is not None:
            ask = {"op": "decide", "decider": which, "state": msg.get("state", ""), "questions": msg.get("questions"),
                   **({"images": msg["images"]} if msg.get("images") else {})}
            try:
                ch, _info = self.mesh.channel(member)
            except OSError as e:
                return reply("decided", decider=which, host=host, unavailable=str(e) or
                             wording.say("deciders.host_gone", host=host))
            try:
                ch.sock.settimeout(wire.SILENT_S)
                ch.keep_alive()
                ch.send({**ask, "req": 1})
                while (got := ch.reply()) is not None:
                    if got.get("req") != 1:
                        continue
                    if got.get("op") == "decided":
                        return reply("decided", **{k: v for k, v in got.items() if k not in ("op", "req")})
                    return reply("error", error=wire.said(got))
                return reply("decided", decider=which, host=host, unavailable=wording.say("deciders.host_gone", host=host))
            except OSError as e:
                return reply("decided", decider=which, host=host, unavailable=str(e))
            finally:
                ch.close()
        entry = self.deciders.find(which) if which else next(iter(self.deciders.entries()), None)
        if entry is None:
            return reply("decided", decider=which or "", host=here, unavailable=wording.say(
                "deciders.no_such", host=here, name=which) if which else wording.say("deciders.none_here", host=here))
        try:
            got = self.deciders.decide(entry, msg)
        except deciders.Unavailable as e:
            self.mesh.deciders_changed(self.deciders.listing())
            return reply("decided", decider=entry["name"], host=here, unavailable=wording.say(
                "deciders.unavailable", name=entry["name"], host=here, why=str(e)))
        except ValueError as e:
            return reply("error", error=wording.say("deciders.said", name=entry["name"], host=here, why=str(e)))
        reply("decided", decider=entry["name"], host=here, **got)

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
        if op == "hello" and conn.origin is None and conn.task is None and isinstance(msg.get("task"), dict):
            if (bot := self.tasks.bot_of(msg["task"])) is None:  # (a task's script: its run's Bot's from now on)
                return conn.send(wire.refused(wire.DENIED, req, wording.say("host_errors.no_task_token")))
            conn.task, conn.acting = {"run": msg["task"]["run"], "bot": bot}, bot["id"]
            if (by := self.started_by(bot["id"])) is not None:  # (judged as the Bot's own tools are)
                conn.member, conn.verified = by
        elif op == "hello" and conn.origin is None and conn.task is None and wire.is_id(msg.get("as") or ""):  # (a
            conn.acting = msg["as"]                          # chat's own tools: judged as the member that started it)
            if (by := self.started_by(msg["as"])) is not None:
                conn.member, conn.verified = by
        if conn.task is not None and op in ("emission", "multiplex", "emission_watch"):  # (a task's: its Bot's own
            if msg.get("default"):                                     # browser and terminals, never another's)
                return conn.send(wire.refused(wire.DENIED, req, wording.say("host_errors.task_default_browser")))
            msg = {**msg, "bot": dict(conn.task["bot"])}
        if (cap := access.rule_of(msg)) is not None and op not in access.FOUND and not self.access.allowed(conn, cap):
            return conn.send(self.access.refused(req, cap))  # (a chat's: as it's found, its own rules first)
        if (op in ("start", "task_output", "autofix", "git", "signal_names") or op == "accounts" and
                msg.get("what") == "use") and (cap := self.manage_refused(conn, msg)):  # (a chat named: forked,
            return conn.send(self.access.refused(req, cap))  # adopted, read, its Auto-fix, moved to another subscription)
        if op in ("read_file", "git", "signal_names", "catalogue") and conn.member is not None and \
                not self.managed_folder(conn, msg.get("path") if op == "read_file" else msg.get("cwd")):
            return conn.send(self.access.refused(req, "manage_read"))  # (a member's: in the folder of a chat it reads)
        if op == "start":
            with self.lock:
                src = self.sessions.get(msg.get("fork_from")) if isinstance(msg.get("fork_from"), str) else None
            if (cap := self.access.needs(conn, msg, src)) is not None:
                return conn.send(self.access.refused(req, cap))
        if op in ("access", "access_set"):
            return self.access_op(conn, msg, reply)
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
                self.desktop.look(force=True)
                listing = self.shown_to(conn, self.heads_listing(conn.archived))
            return reply("sessions", sessions=listing, server=self.info())
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
        if op == "restart":  # (kept till nothing blocks it, held while a head shows what that is, or now)
            if msg["do"] == "now":
                reply("quitting")
                threading.Thread(target=self.restart_now, daemon=True, name="restart").start()
                return None
            if msg["do"] == "pause":
                with self.lock:
                    if self.pending_restart is not None:
                        self.hold_restart(conn, True)
                    return reply("restart_pending", restart=self.restart_state())
            with self.lock:
                self.hold_restart(conn, False)
            return reply("restart_pending", restart=self.restart_when_free("head"))
        if op == "start":
            return self.start(msg, conn, req)
        if op == "read_file":
            return reply("file", **self.read_file(msg.get("path"), msg.get("limit")))
        if op == "task_output":
            return reply("file", **self.task_output(msg.get("session"), msg.get("path"), msg.get("limit")))
        if op == "notes" or op in notes.EDIT_OPS:  # (Record's notebooks here, for another machine's Record: listed, or
            self.notes_ops.put((msg, reply))  # changed; their pages' edits; one at a time, in the order asked, on a
            # thread of their own: _notes)
            return None
        if op == "catalogue":  # (on a thread of its own: it waits for the catalogue to be made)
            def catalogue():
                try:
                    got = self.catalogue(msg.get("cwd"), bool(msg.get("wait")))
                    kube, dbs = self.access.allowed(conn, "kube_read"), self.access.allowed(conn, "databases")
                    got["items"] = [it for it in got["items"] if (kube or not catalogued_kube(it)) and
                                    (dbs or it.get("icon") != "db")]  # (its clusters' and databases' parts: theirs)
                    reply("catalogue", **got)
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
                    else:  # (a member's git: the repository its folder's, not one named, read with this host's gh)
                        asked = {k: v for k, v in msg.items() if k != "repo"} if conn.member is not None else msg
                        reply(op, **(hostfiles.git if op == "git" else hostfiles.signal_names)(self, asked))
                except (ValueError, OSError) as e:
                    reply("error", error=str(e))
                except Exception as e:  # noqa: BLE001 (anything else gone wrong: still answered)
                    reply("error", error=f"{type(e).__name__}: {e}")
            threading.Thread(target=read, daemon=True, name=op).start()
            return None
        if op == "autofix":  # (a chat's Auto-fix turned on or off, or as it is: on a thread of its own, as git and
            def turn():      # gh take a while)
                try:
                    reply("autofix", session=msg.get("session"), autofix=autofix.request(self, msg))
                except deps.Missing as e:
                    reply("error", error=str(e), plain=True, missing=e.dep)
                except (ValueError, OSError) as e:
                    reply("error", error=str(e), plain=True)
                except Exception as e:  # noqa: BLE001 (anything else gone wrong: still answered)
                    reply("error", error=f"{type(e).__name__}: {e}")
            threading.Thread(target=turn, daemon=True, name="autofix").start()
            return None
        if op == "deps":  # (what this host and its machine's apps need, each there or not: Settings → Dependencies;
            def look():   # on a thread of its own, as Ollama's asked and gh run)
                try:
                    reply("deps", deps=deps.report())
                except Exception as e:  # noqa: BLE001 (anything gone wrong: still answered)
                    reply("error", error=f"{type(e).__name__}: {e}")
            threading.Thread(target=look, daemon=True, name="deps").start()
            return None
        if op == "themes":  # (the themes kept here, each worked out: Settings → Theme, on every app)
            return reply("themes", themes=themes.listing())
        if op in ("theme_save", "theme_remove"):  # (Meta's create_theme and install_theme, and their undo)
            try:
                if op == "theme_remove":
                    return reply(op, before=themes.remove(themes.check_name(msg.get("name"))))
                before = themes.save(msg.get("name"), msg.get("css"), msg.get("tokens"))
            except (OSError, ValueError) as e:
                return reply("error", error=str(e))
            return reply(op, theme=themes.theme(msg["name"]), before=before)
        if op == "telemetry":  # (an app of this machine's: what it saw done in chats, and what it profiled, kept here)
            try:
                self.telemetry.keep(msg)
            except OSError as e:
                return reply("error", error=str(e))
            return reply("telemetry_kept")
        if op in ("telemetry_read", "telemetry_set"):  # (any head's, from anywhere in the mesh: on a thread of its own)
            def tell():
                try:
                    if op == "telemetry_set":
                        reply(op, state=self.telemetry.set(msg))
                    else:
                        reply(op, **self.telemetry.read(msg, self.me["id"]))
                except Exception as e:  # noqa: BLE001 (anything gone wrong: still answered)
                    reply("error", error=f"{type(e).__name__}: {e}")
            threading.Thread(target=tell, daemon=True, name="telemetry").start()
            return None
        if op in ("kube", "databases", "docker"):  # (the clusters, databases and Docker here, for a head anywhere:
            def read():                            # hostkube, hostdb, hostdocker)
                try:
                    reply(op, **{"kube": hostkube, "databases": hostdb, "docker": hostdocker}[op].answer(self, msg))
                except Exception as e:  # noqa: BLE001 (anything gone wrong: still answered)
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
            own = msg.get("default") is True  # (Emission's own window on its Default profile, from a Browser pane
            if not own and (not isinstance(bot.get("id"), str) or not bot["id"]) or \
                    not isinstance(msg.get("call"), dict):  # elsewhere: what a pane does, nothing else)
                return reply("error", error="emission needs a Bot (its id) and a call")
            if msg["call"].get("op") not in (emissiontools.PANE_OPS if own else emissiontools.HOST_OPS):
                return reply("error", error=f"emission takes the tools' requests, not {msg['call'].get('op')!r}")

            def browse():
                try:
                    if own:
                        return reply("emission", result=emissionlink.call(
                            emissionlink.profile_id(emissionlink.DEFAULT), {**msg["call"], "from_pane": True},
                            timeout=emissiontools.HOST_WAIT_S))
                    if self.phones.phone(bot["id"]) is not None:  # (its browser on a phone now: asked there)
                        return reply("emission", result=self.phones.call(bot, msg["call"]))
                    reply("emission", result=emissionlink.call_bot(bot["id"], bot.get("name"), msg["call"],
                                                                   timeout=emissiontools.HOST_WAIT_S))
                except (OSError, ValueError) as e:
                    reply("error", error=str(e))
            threading.Thread(target=browse, daemon=True, name="emission").start()
            return None
        if op == "browser_open":  # (☰ → Browser: Emission's window here, brought to the front or started; on a thread)
            def browser():
                try:
                    reply("browser_opened", **emissionlink.open_window())
                except (OSError, ValueError) as e:
                    reply("error", error=str(e))
            threading.Thread(target=browser, daemon=True, name="browser-open").start()
            return None
        if op in ("terminal", "terminal_in", "terminal_resize", "terminal_close", "terminals"):  # (a terminal here:
            if (bad := hostterminal.take(conn, msg)) is not None:  # hostterminal, its bytes carried on this connection)
                return reply("error", error=bad)
            return None
        if op == "multiplex":  # (a Bot's terminal tools: Multiplex's terminals here; on a thread of its own)
            bot = msg.get("bot") if isinstance(msg.get("bot"), dict) else {}
            if not isinstance(bot.get("id"), str) or not bot["id"] or not isinstance(msg.get("call"), dict):
                return reply("error", error="multiplex needs a Bot (its id) and a call")

            def terminal():
                try:
                    reply("multiplex", result=multiplexlink.call(msg["call"], {"id": bot["id"],
                                                                               "name": bot.get("name")}))
                except (OSError, ValueError) as e:
                    reply("error", error=str(e))
            threading.Thread(target=terminal, daemon=True, name="multiplex").start()
            return None
        if op == "accounts":  # (this host's subscription accounts, for a head anywhere: hostaccounts)
            return hostaccounts.answer(self, conn, msg, reply)
        if op == "login":  # (claude auth login run here for a head elsewhere, on this connection: hostlogin)
            return hostlogin.start(self, conn, msg)
        if op == "login_code":
            return hostlogin.code(conn, msg)
        if op == "docker_follow":  # (a container's or a Compose project's log followed: on this connection, ended
            threading.Thread(target=hostdocker.follow, args=(self, conn, msg), daemon=True,  # with it)
                             name="docker-follow").start()
            return None
        if op == "kube_follow":  # (a pod's log followed, or a kind's list watched, for a head elsewhere: on this
            threading.Thread(target=hostkube.follow, args=(self, conn, msg), daemon=True,  # connection, ended with it)
                             name="kube-follow").start()
            return None
        if op == "emission_watch":  # (a Browser pane elsewhere: a Bot's Emission here watched, its frames carried on
            bot = msg.get("bot") if isinstance(msg.get("bot"), dict) else {}  # this connection; one a connection.
            if msg.get("default") is True:  # default: Emission's own window instead, ☰ → Browser's)
                bot = None
            elif not isinstance(bot.get("id"), str) or not bot["id"]:
                return reply("error", error="emission_watch needs a Bot (its id), one on a connection")
            if getattr(conn, "emission_watch", None):
                return reply("error", error="emission_watch needs a Bot (its id), one on a connection")
            if bot is not None and self.phones.phone(bot["id"]) is not None:
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
        if op in ("task_report", "task_ask"):  # (a task's script, running here: on this machine's socket, its run's
            if conn.origin is not None or conn.member is not None:  # token its own)
                return conn.send(self.access.refused(req, "shell"))
            if op == "task_report":
                try:
                    self.tasks.report(msg)
                except ValueError as e:
                    return reply("error", error=str(e))
                return reply("task_reported")

            def asked():  # (on a thread of its own: its Bot's turn waited for)
                try:
                    said, values = self.tasks.script_ask(msg)
                    reply("task_answer", text=said, values=values)
                except ValueError as e:
                    reply("error", error=str(e))
            threading.Thread(target=asked, daemon=True, name="task-ask").start()
            return None
        if op == "deciders":  # (the host's decision models' servers: listed, added, removed, tested; on a thread)
            threading.Thread(target=self.deciders_op, args=(msg, reply), daemon=True, name="deciders").start()
            return None
        if op == "decide":  # (a task's script's, on this machine's socket with its run's token; a member's, through
            if msg.get("run") is not None and conn.origin is None and conn.member is None and \
                    self.tasks.held(msg) is None:  # the mesh: as it may use providers)
                return reply("error", error=wording.say("host_errors.no_run"))
            threading.Thread(target=self.decide, args=(msg, reply), daemon=True, name="decide").start()
            return None
        if op in ("deactivate", "activate"):  # (a member, by its id or where a link from here goes: mesh.deactivate)
            where = msg.get("where") if wire.is_where(msg.get("where")) else None
            done = (self.mesh.deactivate if op == "deactivate" else self.mesh.activate)(msg.get("id"), where)
            return reply(op + "d", any=done)
        if cap := self.manage_refused(conn, msg):  # (every op from here on is a chat's: refused before it's touched)
            return conn.send(self.access.refused(req, cap))
        with self.lock:  # (a Claude Desktop chat only listed, written in: taken up first, its folder made again below;
            # not while Desktop's writing it)
            if msg.get("session") in self.desktop.chats and desktopchats.takes_up(msg):
                desktopchats.restat(d := self.desktop.chats[msg["session"]])
                if desktopchats.elsewhere(d):
                    return reply("error", error=wording.say("host_errors.active_elsewhere"), plain=True)
                try:
                    self.desktop.take_up(msg["session"])
                except (OSError, ValueError) as e:
                    return reply("error", error=str(e))
        if op == "input" and self.makes_folder(msg.get("msg") or {}) or op == "held" and msg.get("send"):
            self.restore_folder(conn, msg.get("session"))  # (its worktree removed: made again first, not under the
            # lock, which git would hold up)
        with self.lock:
            if self.stopped.is_set():  # (it stopped as this waited for the lock: halt's)
                return reply("error", error="the host process is stopping")
            # (or a Claude Desktop chat only listed, read as it is: desktopchats.READS)
            s = self.sessions.get(msg.get("session")) or self.desktop.chats.get(msg.get("session"))
            if s is None:
                return reply("error", error=f"no session {msg.get('session')}")
            if s.deleting:
                return reply("error", error=wording.say("host_errors.being_deleted", session=s.id))
            if (cap := self.access.needs(conn, msg, s)) is not None:  # (commands run, or a Bot driven, through it)
                return conn.send(self.access.refused(req, cap))
            if op == "waits_on" and conn.member is not None and conn.acting != s.id:  # (its own agent's tools' only)
                return conn.send(self.access.refused(req, "manage_write"))
            if op == "attach":
                writes = self.access.allowed(conn, "manage_write", s)  # (one that only reads: answers nothing)
                if writes and msg.get("answers", True) is not False:  # (false: it won't answer what's asked)
                    conn.mute.discard(s.id)
                else:
                    conn.mute.add(s.id)
                s.attached_once = True
                if s.drop_timer is not None:  # (a head back on it: not dropped after all)
                    s.drop_timer.cancel()
                    s.drop_timer = None
                if isinstance(msg.get("always_allow"), list) and writes:  # (tools a head always allows: added to
                    s.always_allow |= {t for t in msg["always_allow"] if isinstance(t, str)}  # those allowed here,
                    self.changed()  # kept in memory)
                if isinstance(msg.get("env"), dict) and writes:  # (a head here: its claude's environment, from its
                    s.env = clean_env(msg["env"])  # next start)
                s.viewers.add(conn)
                autofix.held(self, s, conn)  # (Auto-fix stopped while nobody was here: said now)
                if msg.get("live"):
                    s.live.add(conn)
                if msg.get("app_tools") and writes and s.app_tools is not None:  # (its head back: its tools carried to
                    # it again)
                    s.app_tools.bind(conn)
                return reply("attached", session=s.listing(), asks=list(s.asks.values()), writes=writes,
                             login=hostlogin.attached(self, conn, s), draft=s.meta.get("composer") if writes else None,
                             queue=hostqueue.given(s, writes))
            if op == "detach":  # (meant: an ephemeral session its head's done with dropped at once, not after its grace;
                s.viewers.discard(conn)  # one that only reads it isn't its head: its grace kept)
                s.live.discard(conn)
                conn.mute.discard(s.id)
                self.drop_ephemeral(s, meant=self.access.allowed(conn, "manage_write", s))
                return None
            if op == "input":
                try:
                    self.take_input(conn, s, msg.get("msg") or {}, self.sent_by(conn, msg))
                except (desktopchats.Elsewhere, accounts.Disabled) as e:
                    return reply("error", error=str(e), plain=True)
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
            if op == "chat_size":  # (worked out from its transcript as it is, on a thread of its own: never kept)
                path = s.transcript()

                def size():
                    try:
                        reply("chat_size", session=s.id, **telemetry.chat_size(path))
                    except OSError as e:
                        reply("error", error=str(e))
                threading.Thread(target=size, daemon=True, name="chat-size").start()
                return None
            if op == "transcript":
                return reply("transcript", session=s.id, **self.read_transcript(s, msg.get("offset") or 0,
                                                                               msg.get("limit")))
            if op == "subagents":
                return reply("subagents", session=s.id, **self.read_subagents(s, msg.get("have"), msg.get("limit")))
            if op == "seen":  # (a head shows it on screen, up to upto: its last turn's end seen once that reaches it)
                upto = msg.get("upto")
                if self.saw(s, upto if isinstance(upto, int) and not isinstance(upto, bool) else 0):
                    self.changed()
                return reply("seen_set", session=s.id, seen=s.meta.get("seen"))
            if op == "rename":
                return self.rename(s, msg.get("title"), lambda: reply("renamed", session=s.listing()))
            if op == "draft_save":  # (what's written in its box and not sent: kept here for every head, the last
                kind, fields = self.save_draft(conn, s, msg)  # write's, told the others attached)
                return reply(kind, **fields)
            if op == "queue":  # (what's written while its turn runs: kept here for every head, sent by this host
                kind, fields = hostqueue.change(self, conn, s, msg)  # as the turn ends, hostqueue)
                return reply(kind, **fields)
            if op == "bot":  # (made a Bot, renamed or its picture changed; or, bot None, a chat again)
                return reply("bot_set", session=self.set_bot(s, msg.get("bot")))
            if op == "rotate":  # (a Bot's conversation archived as a past thread of its own; the Bot goes on afresh,
                try:              # on model if it's given)
                    thread = self.rotate(s) if msg.get("model") is None else \
                        self.rotate_to(s, msg["model"], msg.get("effort"))
                except (OSError, ValueError) as e:
                    return reply("error", error=str(e), plain=True)
                return reply("rotated", session=s.listing(), thread=thread.listing() if thread is not None else None)
            if op == "held":  # (a message held for it: dropped, or sent as its user's input would be, and dropped then,
                # at once: kept if it can't be)
                held = s.meta.get("held") or []
                h = next((h for h in held if h.get("id") == msg.get("id")), None)
                if h is None:
                    return reply("error", error=wording.say("host_errors.no_held"))
                if msg.get("send"):
                    try:
                        self.take_input(conn, s, {"type": "user", "message": {"role": "user", "content": h["text"]}},
                                        h.get("sender"), h["shown"] if isinstance(h.get("shown"), str) else
                                        meshtools.shown_of_told(h.get("text") or ""))
                    except (desktopchats.Elsewhere, accounts.Disabled) as e:  # (kept: sent once it's quiet)
                        return reply("error", error=str(e), plain=True)
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
            if op == "bot_picture":
                return reply("bot_picture", session=s.id, picture=self.bot_picture(s))
            if op in ("task_bind", "task_choices"):  # (a task's variables: on a thread of its own, as what's here of
                def variables(s=s):                     # their type is looked at: kubectl, docker, the mesh)
                    try:
                        kind, fields = self.tasks.bind(s, msg) if op == "task_bind" else \
                            self.tasks.choices(s, msg.get("type"), lambda cap: self.access.allowed(conn, cap))
                    except (ValueError, TypeError, OSError) as e:
                        return reply("error", error=str(e))
                    return reply(kind, **fields)
                threading.Thread(target=variables, daemon=True, name=op).start()
                return None
            if op in ("tasks", "task", "run_task", "task_run", "task_stop"):  # (a Bot's tasks: baudelib.tasks)
                try:
                    kind, fields = self.tasks.op(s, msg, by={"id": conn.member, "verified": conn.verified}
                                                 if conn.member is not None else None)
                except (ValueError, TypeError, OSError) as e:
                    return reply("error", error=str(e))
                return reply(kind, **fields)
            if op == "answer_task":  # (an ask of one of a Bot's tasks' runs, waiting on it: answered, its run going on)
                if not s.meta.get("bot"):
                    return reply("error", error=wording.say("host_errors.tasks_bot_only"))
                try:
                    kind, fields = self.tasks.answer(s, msg)
                except ValueError as e:
                    return reply("error", error=str(e))
                return reply(kind, **fields)
            if op == "memory":  # (a Bot's memory: read, or, text given, written; refused if the Bot's written it since
                # base_mtime, with what it wrote)
                mem = self.bot_memory(s, msg.get("text"), msg.get("seen"),
                                      **({"base_mtime": msg["base_mtime"]} if "base_mtime" in msg else {}))
                if "refused" in mem:
                    return reply("error", error=mem.pop("refused"), session=s.id, **mem)
                return reply("memory", session=s.id, **mem)
        self.log(f"refused: no op {op!r} here")  # (one the schema has that no host takes here: a reply's, say)
        return conn.send(wire.refused(wire.UNSUPPORTED, req, f"no op {op!r} here"))

    def save_draft(self, conn, s, msg):
        """(lock held) draft_save: s's draft kept as msg's text, its revision one more than the last, and told every
        other head attached that may write it (draft_changed); the reply's op and fields."""
        text = msg.get("text")
        if s.id not in self.sessions:  # (a Claude Desktop chat only listed: Baude's own, on this machine, keeps its)
            return "error", {"error": f"no session {s.id}"}
        if not isinstance(text, str) or len(text) > wire.CONSTANTS["DRAFT_CHARS_MAX"]:
            return "error", {"error": wire.SAID[wire.INVALID], "code": wire.INVALID}
        draft = {"text": text, "rev": int((s.meta.get("composer") or {}).get("rev") or 0) + 1,
                 "head": msg["head"][:64] if isinstance(msg.get("head"), str) else None, "at": time.time()}
        s.meta["composer"] = draft
        self.save(s)
        for v in list(s.viewers):
            if v is not conn and self.access.allowed(v, "manage_write", s):
                v.send({"op": "draft_changed", "session": s.id, "draft": draft})
        return "draft_saved", {"session": s.id, "draft": draft}

    def started_by(self, sid):
        """(member, verified) that started the chat sid here (its meta's started_by), or None: a head here did."""
        with self.lock:
            s = self.sessions.get(sid) if isinstance(sid, str) else None
            by = s.meta.get("started_by") if s is not None else None
        return (by["id"], by.get("verified") is not False) if isinstance(by, dict) and wire.is_id(by.get("id") or "") \
            else None

    def managed_folder(self, conn, path):
        """Is path (absolute) in the folder of a chat here conn may manage?"""
        if not isinstance(path, str) or not os.path.isabs(os.path.expanduser(path)):
            return False
        real = os.path.realpath(os.path.expanduser(path))
        with self.lock:
            chats = list(self.sessions.values()) + list(self.desktop.chats.values())
        folders = {os.path.realpath(c.meta["cwd"]) for c in chats
                   if isinstance(c.meta.get("cwd"), str) and self.access.allowed(conn, "manage_read", c)}
        return any(real == f or real.startswith(f.rstrip(os.sep) + os.sep) for f in folders)

    def manage_refused(self, conn, msg):
        """The rule, if any, by which conn may not do msg to the chat it names (session; a start's fork_from, read, or
        adopt, written; a chat moved to another subscription, written), as access has it: msg's own side of
        Agent Management (access.OPS) for the rest."""
        if conn.member is None:
            return None
        for key in ("session", "fork_from", "adopt"):
            if isinstance(sid := msg.get(key), str) and sid:
                with self.lock:
                    s = self.sessions.get(sid) or self.desktop.chats.get(sid)
                side = access.rule_of(msg, s)
                side = "manage_write" if msg.get("op") == "accounts" or key == "adopt" else side \
                    if access.capability(side or "") == "manage" else "manage_read"
                if not self.access.allowed(conn, side, s):
                    return side
        return None

    def access_op(self, conn, msg, reply):
        """access and access_set: the host's rules, a chat's own (session) too; set from where access allows it."""
        op, sid = msg["op"], msg.get("session")
        with self.lock:
            s = self.sessions.get(sid) if isinstance(sid, str) else None
            listed = sid in self.desktop.chats
        if sid is not None and listed and s is None:  # (a Claude Desktop chat only listed: its rule's the host's)
            return reply("error", error=wording.say("access.desktop_listed"), plain=True)
        if sid is not None and (s is None or not self.access.allowed(conn, "manage_read", s)):
            return reply("error", error=f"no session {sid}")
        if op == "access_set":
            if not self.access.allowed(conn, "access_write"):
                return conn.send(self.access.refused(msg.get("req"), "access_write"))
            try:
                if s is not None:
                    with self.lock:
                        self.access.set_own(s.meta, msg.get("capability"), msg.get("rule"))
                        self.save(s)
                elif msg.get("capability") is not None and msg.get("rule") is not None:
                    self.access.set(msg["capability"], msg["rule"])
                else:
                    raise ValueError(wording.say("access.bad_rule"))
            except (OSError, ValueError) as e:
                return reply("error", error=str(e))
            self.log(f"access: {sid or msg['capability']} set by {conn.member or 'a head here'}")
        own = access.own_rules(s.meta) if s is not None else {}
        return reply(op, state=self.access.state(conn, [m for r in own.values() for m in r.get("members") or ()]),
                     session=sid if s is not None else None, chat=own or None)

    def set_bot(self, s, bot):
        """(under the lock) A chat made a Bot (bot: {name, picture: base64 or None to keep it, or "" to drop it}), its
        name or picture changed, or (bot None) a chat again, its memory and picture kept in case it's made one again.
        Its folder named after it (name_bot_folder). A chat made one has its agent given a Bot's tools as it runs
        (grow_agent), where its program takes them so; another change to what it's told, or one that can't, has its
        agent started again (renew_agent)."""
        was = s.meta.get("bot")
        if bot is None:
            s.meta.pop("bot", None)
        else:
            if not isinstance(bot, dict) or not str(bot.get("name") or "").strip():
                raise ValueError(wording.say("host_errors.bot_needs_name"))
            name = " ".join(str(bot["name"]).split())[:80]
            data = base64.b64decode(bot["picture"]) if bot.get("picture") else None
            if data is not None and len(data) > BOT_PICTURE_MAX:
                raise ValueError(wording.say("host_errors.picture_too_big"))
            if was and name != was.get("name") and getattr(s, "proc", None) is not None and s.busy:
                s.bot_rename = True  # (its agent goes on writing its memory where it began the turn: moved as it ends)
                folder = bot_dir(self.state_dir, s.meta)
            else:
                folder = self.name_bot_folder(s, name)
            s.meta["bot"] = {"name": name, "since": (was or {}).get("since") or time.time()}
            if not os.path.exists(os.path.join(folder, "memory.md")):  # (and nothing in it accepted yet: all the Bot
                for n in ("memory.md", "memory.seen.md"):                 # writes is shown as its changes)
                    with open(os.path.join(folder, n), "w") as f:
                        f.write("")
            pic = os.path.join(folder, "picture")
            if bot.get("picture") == "":
                if os.path.exists(pic):
                    os.remove(pic)
            elif data is not None:
                with open(pic + ".new", "wb") as f:
                    f.write(data)
                os.replace(pic + ".new", pic)
        self.save(s)
        now = s.meta.get("bot")
        if (was or {}).get("name") != (now or {}).get("name"):
            s.bot_note = None  # (what it was to be told, with the name and folder it had)
            if not (was is None and self.grow_agent(s)):
                self.renew_agent(s)  # (its picture alone: nothing its agent's told)
        self.changed()
        return s.listing()

    def name_bot_folder(self, s, name):
        """(under the lock) A Bot's folder (bot_dir) named after it (serverstate.bot_folder_name), as it's named:
        the one it has moved there, or made; a number after the name if another Bot's has it. Left where it is if it
        can't be moved. Its tasks' store goes on in it where it is now."""
        old, base = bot_dir(self.state_dir, s.meta), bot_folder_name(name)
        mine = (s.meta.get("bot_folder") or "").casefold()
        taken = {(o.meta.get("bot_folder") or "").casefold() for o in self.sessions.values() if o is not s}
        bots = os.path.join(self.state_dir, "bots")
        for n in range(1, 100):
            want = base if n == 1 else f"{base} {n}"
            if want.casefold() == mine and os.path.isdir(old):
                return old  # (named so already)
            if want.casefold() not in taken and not os.path.lexists(os.path.join(bots, want)):
                break
        else:
            want = wire.safe_name(s.id)
        new = os.path.join(bots, want)
        before = {k: s.meta.get(k) for k in ("bot_folder", "bot_moving")}
        try:
            os.makedirs(bots, mode=0o700, exist_ok=True)
            if os.path.isdir(old):
                s.meta["bot_folder"], s.meta["bot_moving"] = want, os.path.basename(old)
                self.save(s)  # (the name it's to have kept first: a crash between this and the move is mended as the host starts)
                os.rename(old, new)
            else:
                os.makedirs(new, mode=0o700)
        except OSError as e:
            self.log(f"{s.id}: its Bot's folder not named {want}: {e}")
            for k, v in before.items():
                s.meta.pop(k, None) if v is None else s.meta.__setitem__(k, v)
            try:
                self.save(s)
                os.makedirs(old, mode=0o700, exist_ok=True)
            except OSError:
                pass
            return old
        s.meta["bot_folder"] = want
        s.meta.pop("bot_moving", None)
        self.tasks.moved(s, os.path.join(new, "tasks"))
        return new

    def move_bot_folder(self, s):
        """(under the lock) A Bot's folder moved to its name's, now its agent's turn is over (set_bot)."""
        if s.bot_rename and s.meta.get("bot"):
            s.bot_rename = False
            self.name_bot_folder(s, s.meta["bot"]["name"])
            try:
                self.save(s)
            except OSError as e:
                self.log(f"{s.id}: {e}")

    def settle_bot_folders(self):
        """(as the host starts) Each Bot's folder named after it, a Bot's kept by its session's id till now too; one a
        crash left between saving its folder's name and moving it, moved. Never stops the host."""
        bots = os.path.join(self.state_dir, "bots")
        for s in list(self.sessions.values()):
            if not s.meta.get("bot"):
                continue
            try:
                if s.meta.get("bot_moving"):
                    new, old = (os.path.join(bots, s.meta.get(k) or "") for k in ("bot_folder", "bot_moving"))
                    if os.path.isdir(old) and not os.path.lexists(new):
                        os.rename(old, new)
                    s.meta.pop("bot_moving", None)
                    self.save(s)
                if not s.meta.get("bot_folder"):
                    self.name_bot_folder(s, s.meta["bot"].get("name") or "Bot")
                    self.save(s)
            except OSError as e:
                self.log(f"{s.id}: its Bot's folder not settled: {e}")

    # (what a Bot's conversation was, its thread's; what the Bot is, kept: the rest of its meta)
    THREAD_KEYS = ("path", "cli_id", "title", "prompt", "said", "written", "cut", "context_usage", "codex_started",
                   "fork_from", "adopted", "desktop", "ended", "seen")

    def rotate(self, s):
        """(under the lock) A Bot's conversation archived as a past thread (a session of its own, read-only, named
        "<Bot> / <its title>"), and the Bot carried on in a new, empty one with a new agent: its id, name, picture,
        memory, tasks, browser, rules and place all as they were. Refused (ValueError) while it's at a turn, asking,
        running commands or agents in the background, or a task's ask waits on it. Kept in an order a crash can't
        spoil: the thread's settings first (rotating, till the Bot's are), then its files moved, then the Bot's;
        one left half done is undone or finished as the host next starts (settle_rotations), or at once if a step
        fails here. The thread: the new session, as it's kept."""
        bot = s.meta.get("bot")
        self.can_rotate(s)
        if s.transcript() is None:
            raise ValueError(wording.say("host_errors.nothing_to_rotate"))
        self.rescan(s)  # (its title as its transcript has it now)
        listed = s.listing()
        meta = copy.deepcopy(s.meta)
        for key in ("bot", "autofix", "held", "waits_on", "follow_ups", "routines", "task_run", "spawned_by",
                    "start_key", "rotated", "last_thread", "composer", "bot_folder", "bot_moving", "queue"):  # (the Bot's draft, and its
            # folder, stay the Bot's)
            meta.pop(key, None)
        title = listed.get("title") or time.strftime("%-d %b %Y", time.localtime(listed.get("created") or time.time()))
        meta.update(id=str(uuid.uuid4()), archived=True, thread_of=s.id, title=f"{bot['name']} / {title}",
                    rotating=True, cli_id=s.meta.get("cli_id") or (
                        None if models.model_program(s.meta.get("model")) == "codex" else s.id))
        if not meta["cli_id"]:
            meta.pop("cli_id")
        if meta.get("ended"):  # (its last turn's end, seen: rotated by hand, and read-only from now on)
            meta["seen"] = dict(meta["ended"])
        thread = Session(self, meta)
        was = copy.deepcopy(s.meta)
        try:
            self.save(thread)  # (1: the thread, rotating: undone as the host starts, unless the Bot's kept below)
            for src, dst in self.rotated_files(s.id, thread.id):  # (2: the host's own transcript, its senders)
                if os.path.exists(src):
                    os.replace(src, dst)
            ended = s.meta.get("ended")
            for key in self.THREAD_KEYS:
                s.meta.pop(key, None)
            if ended:  # (the Bot's last turn's end, said seen: every head's notification of it taken back)
                s.meta["seen"] = dict(ended)
            if models.model_program(s.meta.get("model")) != "codex":  # (claude, pi: a conversation of a new id;
                s.meta["cli_id"] = str(uuid.uuid4())                  # Codex names its own thread as it starts)
            s.meta.update(said=listed.get("said"), rotated=time.time(), senders=(s.meta.get("senders") or 0) + 1,
                          last_thread=thread.id)  # (its place in the list kept: what's next said in it moves it up)
            self.save(s)  # (3: the Bot afresh: from here the thread's its own)
        except Exception:
            s.meta = was
            self.undo_rotation(s.id, thread.id)
            raise
        if s.proc is not None:  # (its agent between turns: let go of, as ensure_proc lets go of one that's stopping)
            old, s.proc = s.proc, None
            old.stopping = True
            s.forget_commands()
            old.kill()
            self.drop_routes(s)
        thread.meta.pop("rotating")
        try:
            self.save(thread)  # (4: settled; if it isn't kept, settle_rotations settles it next start)
        except OSError as e:
            self.log(f"rotating {s.id}: {thread.id} not marked done yet: {e}")
        self.sessions[thread.id] = thread
        self.rescan(thread)
        thread.agents = thread.subagent_files()
        s.scan = s.info = s.stat = s.agents = None
        s.senders, s.outputs, s.outputs_at, s.context_asked = {}, set(), 0, 0.0
        s.rewinds.clear()
        s.reverting.clear()
        self.rescan(s)
        self.log(f"rotated {s.id}: its conversation archived as {thread.id}")
        self.changed()
        return thread

    def can_rotate(self, s):
        """(under the lock) Refused (ValueError) if a session can't be rotated now: it isn't a Bot, or it's at a turn,
        asking, running commands or agents in the background, or a task's ask waits on it."""
        if not s.meta.get("bot"):
            raise ValueError(wording.say("host_errors.not_a_bot"))
        if s.busy or s.asks or s.queued:
            raise ValueError(wording.say("host_errors.at_a_turn"))
        if s.commands() or s.background_agents:
            raise ValueError(wording.say("host_errors.background_running"))
        if self.tasks.asking(s.id):
            raise ValueError(wording.say("host_errors.task_asking"))

    def rotate_to(self, s, model, effort=None):
        """(under the lock) A Bot moved onto any model, of whatever program: its conversation rotated (rotate) if
        anything's been said in it, and the Bot carried on afresh on model, at effort if its program has one, its
        memory telling it who it is. Refused (ValueError) as rotate is, or for a model this host can't run. The
        thread: its past one, None if there was nothing to rotate."""
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model is a model's name")
        if not self.can_run(model):
            raise ValueError(wording.say("host_errors.cant_run", host=self.me["name"], model=model))
        self.can_rotate(s)
        thread = self.rotate(s) if s.transcript() is not None else None
        was = models.model_program(s.meta.get("model"))
        run = models.launch(model, s.meta.get("mode"), effort if isinstance(effort, str) and effort else
                            s.meta.get("effort"))
        if run["program"] == "codex":
            run["effort"] = models.codex_effort(self.codex_models(s.meta.get("profiles", {}).get("codex", "")),
                                                model, run["effort"])
        s.meta.update(model=model, mode=run["mode"], effort=run["effort"])
        if run["program"] != was:  # (a conversation of the new program's: Codex names its own thread as it starts)
            for key in ("cli_id", "path", "codex_started", "context_usage", "init"):
                s.meta.pop(key, None)
            if run["program"] != "codex":
                s.meta["cli_id"] = str(uuid.uuid4())
        self.save(s)
        if s.proc is not None:  # (its agent between turns, the old model's: let go of, as rotate does)
            old, s.proc = s.proc, None
            old.stopping = True
            s.forget_commands()
            old.kill()
            self.drop_routes(s)
        self.log(f"moved {s.id} onto {model}")
        self.changed()
        return thread

    def rotated_files(self, bot, thread):
        """(from, to) of what a rotation moves from the Bot to its thread: the host's own transcript, its senders."""
        return [(os.path.join(self.state_dir, "transcripts", f"{bot}.jsonl"),
                 os.path.join(self.state_dir, "transcripts", f"{thread}.jsonl")),
                (self.senders_path(bot), self.senders_path(thread))]

    def undo_rotation(self, bot, thread):
        """A rotation not kept to its end (the Bot's settings never written): its files moved back to the Bot, and
        the thread gone."""
        for src, dst in self.rotated_files(bot, thread):
            if os.path.exists(dst) and not os.path.exists(src):
                os.replace(dst, src)
        try:
            os.remove(os.path.join(self.state_dir, "sessions", f"{thread}.json"))
        except FileNotFoundError:
            pass
        self.sessions.pop(thread, None)

    def settle_rotations(self):
        """(as the host starts) Rotations a crash left half done: one whose Bot's settings were kept, finished; any
        other undone, its Bot as it was."""
        for t in [x for x in self.sessions.values() if x.meta.get("rotating")]:
            b = self.sessions.get(t.meta.get("thread_of"))
            if b is not None and b.meta.get("last_thread") == t.id:
                t.meta.pop("rotating")
                self.save(t)
            else:
                self.log(f"rotating {t.meta.get('thread_of')}: undone, the host stopped before it was")
                self.undo_rotation(t.meta.get("thread_of"), t.id)

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
        """Is what a Bot's agent asks to do a read or an edit of a file in its own folder (its memory's)?"""
        path = (request.get("input") or {}).get("file_path")
        if not s.meta.get("bot") or request.get("tool_name") not in ("Read", "Edit", "MultiEdit", "Write") or \
                not isinstance(path, str) or not path:
            return False
        folder = os.path.realpath(bot_dir(self.state_dir, s.meta))
        path = os.path.realpath(os.path.join(s.meta.get("cwd") or "/", os.path.expanduser(path)))
        return path.startswith(folder + os.sep)

    def bot_picture(self, s):
        pic = os.path.join(bot_dir(self.state_dir, s.meta), "picture")
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
        folder = bot_dir(self.state_dir, s.meta)
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
