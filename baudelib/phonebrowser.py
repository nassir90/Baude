"""A Bot's browser on a phone: the Android app, connected to this host process as a head is (straight to it, or
through the mesh), says it's showing a Bot of this host's browser (emission_serve), and from then on that Bot's browser
tools' requests here (wire's emission op) are carried to it on its connection (emission_call), its answer back
(emission_answer), instead of to this host's Emission. Only while the phone says so:
once it says it's stopped, another phone takes it (emission_unserved, to the one it was on), or its connection goes,
they're Emission's again.

What it answers is what Emission would (the same ops, the same answers: the app runs PAGE_JS too). Once it's
Emission's again, the Bot's next request there is told so (emissionlink.pending_note, as a browser that's restarted
tells it), with what the phone had open and anything it hadn't told the Bot yet."""
import itertools
import os
import queue
import sys
import threading

from baudelib import emissionlink, emissiontools, wording

GONE = "the phone's browser closed before it answered"


class Phones:
    """The Bots whose browser is on a phone now (by the Bot's id: the phone's connection), and the requests carried to
    one, waiting on its answer."""

    def __init__(self, server):
        self.server = server
        self.lock = threading.Lock()
        self.serving = {}  # (a Bot's id: the phone's connection)
        self.waiting = {}  # (a request's id: [its connection, an event set once it's answered, the answer, the Bot])
        self.prefix = os.urandom(4).hex()  # (requests' ids this process's own: not one before it restarted)
        self.ids = itertools.count(1)
        self.notes = queue.Queue()  # (what's to be told to the Bots' Emissions here: in the order it comes, on a
        threading.Thread(target=self.telling, daemon=True, name="phone-notes").start()  # thread of its own)

    def serve(self, conn, msg, reply):
        """emission_serve {bot: its id, on}: a phone showing that Bot's browser (on), or not now (urls: the pages it
        had open; note: what it hadn't told the Bot yet). The Bot's a Bot of this host's: its chat here. Answered
        before the Bot's requests are carried to it."""
        bot = msg.get("bot")
        if not isinstance(bot, str) or not bot:
            return reply("error", error="emission_serve needs a Bot's id")
        if not msg.get("on"):
            with self.lock:
                had = self.serving.get(bot) is conn
                if had:
                    del self.serving[bot]
            self.fail(conn, bot)
            if had:
                urls = [u for u in msg.get("urls") or [] if isinstance(u, str)]
                note = msg.get("note") if isinstance(msg.get("note"), str) else ""
                self.notes.put((bot, "the phone closed it", urls, note))
                self.listed()
            return reply("emission_serving", bot=bot, on=False)
        with self.server.lock:
            s = self.server.sessions.get(bot)
            is_bot = s is not None and bool(s.meta.get("bot"))
        if not is_bot:
            return reply("error", error=wording.say("host_errors.no_such_bot"))
        reply("emission_serving", bot=bot, on=True)
        self.notes.put((bot, None, (), ""))  # (what this host's Emission had for the Bot about a phone: not told now)
        with self.lock:
            was = self.serving.get(bot)
            self.serving[bot] = conn
        if was is not None and was is not conn:  # (another phone had it: told, and its requests not answered there)
            self.fail(was, bot)
            was.send({"op": "emission_unserved", "bot": bot, "why": wording.say("host_errors.another_phone")})
        self.listed()

    def phone(self, bot_id):
        with self.lock:
            return self.serving.get(bot_id)

    def call(self, bot, call):
        """call (a Bot's tools' request) carried to the phone showing bot's browser: its answer, or ValueError for
        what went wrong there (OSError: it went first)."""
        with self.lock:
            conn = self.serving.get(bot["id"])
            if conn is None:
                raise OSError(GONE)
            rid = f"{self.prefix}-{next(self.ids)}"
            box = [conn, threading.Event(), None, bot["id"]]
            self.waiting[rid] = box
        conn.send({"op": "emission_call", "id": rid, "bot": {"id": bot["id"], "name": bot.get("name")}, "call": call})
        try:
            if not box[1].wait(emissiontools.HOST_WAIT_S):
                raise OSError("the phone's browser didn't answer in time")
        finally:
            with self.lock:
                self.waiting.pop(rid, None)
        got = box[2]
        if not isinstance(got, dict):
            raise OSError(GONE)
        if got.get("error"):
            raise ValueError(str(got["error"]))
        return got.get("result") if isinstance(got.get("result"), dict) else {}

    def answer(self, conn, msg):
        """emission_answer {id, result | error}: a request's answer, from the phone it went to."""
        with self.lock:
            box = self.waiting.get(msg.get("id"))
        if box is None or box[0] is not conn:
            return
        box[2] = {"result": msg.get("result")} if "result" in msg else {"error": msg.get("error") or "no answer"}
        box[1].set()

    def fail(self, conn, bot=None):
        """conn's requests (bot's only, if it's given) answered as gone."""
        with self.lock:
            boxes = [b for b in self.waiting.values() if b[0] is conn and (bot is None or b[3] == bot)]
        for b in boxes:
            b[1].set()

    def gone(self, conn):
        """A connection gone: the Bots it was showing, Emission's again."""
        with self.lock:
            bots = [b for b, c in self.serving.items() if c is conn]
            for bot in bots:
                del self.serving[bot]
        self.fail(conn)
        for bot in bots:
            self.notes.put((bot, "the phone's connection went", (), ""))
        if bots:
            self.listed()

    def listed(self):
        """Heads told the sessions again: a Bot's on_phone changed (Baude's Browser page says where its browser is)."""
        with self.server.lock:
            self.server.changed()

    def telling(self):
        while True:
            try:
                self.noted(*self.notes.get())
            except Exception:  # (one that couldn't be told: said, and the next told)
                sys.excepthook(*sys.exc_info())

    @staticmethod
    def noted(bot, why, urls=(), note=""):
        """What the Bot's next request to this host's Emission is told (why None: nothing, now it's a phone's again):
        its browser's that again, not the phone's. Told to the Emission that has its profile, if one's running (in what
        it'll tell, in place of what it had about a phone before); else written down for the next one
        (emissionlink.pending_note, the last of these in place of one before it)."""
        pid = emissionlink.bot_profile_id(bot)
        said = "" if why is None else (
            f"Your browser was on a phone till now ({why}): this is this host's browser again, with its own tabs "
            "(their ids and refs aren't the phone's)" +
            (", and the pages the phone had open are closed: " + ", ".join(list(urls)[:10]) if urls else "") +
            "." + (f" {note}" if note else ""))
        try:
            emissionlink.call(pid, {"op": "phone_note", "note": said}, timeout=5)
            return
        except (OSError, ValueError):
            pass
        emissionlink.phone_note(pid, said)
