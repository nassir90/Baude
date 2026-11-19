"""A Bot's browser on a phone: the Android app, connected to this host process as a head is, says it's showing a Bot
of this host's browser (emission_serve), and from then on that Bot's browser tools' requests here (wire's emission
op) are carried to it on its connection (emission_call), its answer back (emission_answer), instead of to this host's
Emission. Only while the phone says so: once it says it's stopped, or its connection goes, they're Emission's again.

What it answers is what Emission would (the same ops, the same answers: the app runs PAGE_JS too)."""
import itertools
import threading

from baudelib import emissiontools

GONE = "the phone's browser closed before it answered"


class Phones:
    """The Bots whose browser is on a phone now (by the Bot's id: the connection it's on, and the Bot's name), and the
    requests carried to one, waiting on its answer."""

    def __init__(self, server):
        self.server = server
        self.lock = threading.Lock()
        self.serving = {}  # (a Bot's id: the phone's connection)
        self.waiting = {}  # (a request's id: [its connection, an event set once it's answered, the answer])
        self.ids = itertools.count(1)

    def serve(self, conn, msg):
        """emission_serve {bot: its id, on}: a phone showing that Bot's browser (on), or not now. The Bot's a Bot of
        this host's: its chat here. What it's answered: (op, its fields)."""
        bot = msg.get("bot")
        if not isinstance(bot, str) or not bot:
            return "error", {"error": "emission_serve needs a Bot's id"}
        if not msg.get("on"):
            with self.lock:
                if self.serving.get(bot) is conn:
                    del self.serving[bot]
            self.fail(conn, bot)
            return "emission_serving", {"bot": bot, "on": False}
        with self.server.lock:
            s = self.server.sessions.get(bot)
            is_bot = s is not None and bool(s.meta.get("bot"))
        if not is_bot:
            return "error", {"error": "that's no Bot of this host's"}
        with self.lock:
            was = self.serving.get(bot)
            self.serving[bot] = conn
        if was is not None and was is not conn:  # (another phone had it: its requests not answered there now)
            self.fail(was, bot)
        return "emission_serving", {"bot": bot, "on": True}

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
            rid = next(self.ids)
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
            for bot in [b for b, c in self.serving.items() if c is conn]:
                del self.serving[bot]
        self.fail(conn)
