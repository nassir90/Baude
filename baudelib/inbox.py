"""The Inbox (schema/ui/Inbox.page.json): what a Bot's task, or a chat's model, asks of a person, kept by its host. A
running script raises an ask (baudetask.inbox: inbox_raise with its run's token), or a chat does with its own tools
(baudelib.chattools' ask_user: inbox_raise with its session, on its own tools' connection): Markdown, its pictures,
its buttons and a form. Every head that may manage the asking Bot (or chat) is told it, and told again as it changes (inbox_changed, as a draft's change is told), so an
ask answered on one device leaves every Inbox. A person answers by pressing one of its buttons (inbox_press, the
form's values with it), has a model of the host answer it for them to confirm (inbox_route model: its proposal), sends
it to a Bot to answer (inbox_route bot: the Bot's answer_ask tool), or writes to the asking task's Bot (inbox_say),
which may propose an answer (its answer_ask: for the person to confirm) or put a new version of it (revise_ask). The
script waits for the answer, or polls it (inbox_wait), and may put a new version of it (inbox_revise: one that's been
pressed is open again, waiting) or withdraw it (inbox_withdraw). A chat's ask is answered back into that chat: the
answer is sent it as a message from the Inbox once it's idle (deliver), as its next turn, unless its model waits for
it (inbox_wait) and is given it so.

Kept in the host's state folder, inbox/: asks.json (the asks), pictures/<sha256's first 16 hex> (each picture's bytes,
once however many versions show it). Asks persist across the host's restarts: one waiting as the host stopped (or as
its run ended) is stopped, shown struck through till it's removed (inbox_remove); a chat's goes on waiting, as its
chat's still there, and is stopped once the chat's archived or deleted (chat_gone). No GTK."""
import base64
import hashlib
import json
import os
import re
import struct
import threading
import time
import uuid

from baudelib import providers, wire, wording
from baudelib.tasks import FIELDS_MAX, _field_said, answered, clean_field

STYLES = tuple(wire.DEFS["InboxButton"]["properties"]["style"]["enum"])
BODY_MAX = 200_000  # (characters of an ask's body)
TITLE_MAX = 200
BUTTONS_MAX = 30
LABEL_MAX = 200
PICTURES_MAX = 24
PICTURE_MAX = wire.CONSTANTS["IMAGE_BYTES_MAX"]  # (one picture's bytes, at most)
FRESH_S = 120  # (seconds a picture just kept is safe from save() while its ask is made)
VERSIONS_MAX = 50  # (an ask's versions kept: the oldest go, its first kept)
TEXT_MAX = 20_000  # (what a person writes to the asking Bot)
WAIT_MAX_S = 600  # (a chat's model waiting on its ask in one call, at most)
DONE_KEEP_S = 7 * 86400  # (a chat's ask answered or withdrawn: kept this long for its model to read, across restarts)
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")
_KINDS = (  # (a picture's kind, by how its bytes begin)
    (b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"))
SCRIPT_OPS = ("inbox_raise", "inbox_withdraw", "inbox_wait")  # (a task's script's, on the host's own socket)
OPS = ("inbox", "inbox_press", "inbox_route", "inbox_say", "inbox_remove", "inbox_picture", "inbox_revise",
       *SCRIPT_OPS)


def mime_of(data):
    for head, mime in _KINDS:
        if data.startswith(head):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def size_of(data, mime):
    """(width, height) a picture's header says, or (None, None)."""
    try:
        if mime == "image/png" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if mime == "image/gif":
            return struct.unpack("<HH", data[6:10])
        if mime == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
        if mime == "image/webp":
            if data[12:16] == b"VP8X":
                return (int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1)
            if data[12:16] == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if data[12:16] == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
    except (struct.error, IndexError):
        pass
    return None, None


def shown(ask):
    """An ask as the wire has it: its pictures without where they're kept."""
    return {**{k: v for k, v in ask.items() if not k.startswith("_")},
            "versions": [{**v, "pictures": [{k: p[k] for k in p if k != "sha"} for p in v["pictures"]]}
                         for v in ask["versions"]]}


def title_of(version):
    """What an ask's called in the list: its title, else its body's first line that says something."""
    if version.get("title"):
        return version["title"]
    for line in version.get("body", "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith(("![", "|")):
            return line[:TITLE_MAX]
    return ""


def is_chats(ask):
    """Is it a chat's own ask (its model's, raised with the chat's tools), not a task's run's?"""
    return not ask.get("run")


def chat_of(s):
    """What a chat's called, as an ask from it says: its Bot's name, else its title."""
    if s is None:
        return None
    return (s.meta.get("bot") or {}).get("name") or s.listing().get("title") or None


def answer_said(ask):
    """A chat's ask's answer, as its chat's told it: the button, the form's values, who answered it if not the
    person."""
    a, v = ask["answer"], ask["versions"][-1]
    lines = [wording.say("inbox.answered_said", title=title_of(v), ask=ask["id"], button=a["label"])]
    if a.get("by"):
        lines.append(wording.say("inbox.answered_by", bot=a["by"]))
    if a.get("values"):
        lines += ["", wording.say("inbox.values_said"), json.dumps(a["values"], ensure_ascii=False, indent=1)]
    return "\n".join(lines)


def said(ask, version=None):
    """An ask as a model or a Bot is told it: its title and body, its buttons by index, its form's fields."""
    v = version or ask["versions"][-1]
    lines = [f"## {title_of(v)}", "", v["body"].strip(), "", wording.say("inbox.buttons_said")]
    lines += [f"- {i}: {b['label']}" for i, b in enumerate(v["buttons"])]
    if v["form"]:
        lines += ["", wording.say("inbox.form_said"), *(_field_said(f) for f in v["form"])]
    return "\n".join(lines)


class Inbox:
    """A host's asks: kept, told every head that may see them as they change, answered, routed and waited for."""

    def __init__(self, server):
        from baudelib.serverstate import _read_json
        self.server = server
        self.lock = threading.RLock()  # (taken after the server's lock, never before)
        self.cv = threading.Condition(self.lock)  # (an ask changed: inbox_wait's waits look again)
        self.folder = os.path.join(server.state_dir, "inbox")
        self.fresh = {}  # (pictures just kept and not yet in an ask: sha: when, so save() leaves them)
        self.proposing = {}  # (the start_key of a model's session asked to answer an ask: that ask's id)
        self.waiters = {}  # (a chat's ask's id: how many of its model's inbox_waits wait on it, which it's told by)
        kept = _read_json(os.path.join(self.folder, "asks.json")) or {}
        self.asks = {}
        for a in kept.get("asks") or []:
            if not isinstance(a, dict) or not wire.is_id(a.get("id") or "") or not a.get("versions"):
                continue
            chat = server.sessions.get(a.get("session") or "") if is_chats(a) else None
            if a.get("status") in ("answered", "withdrawn") and not (  # (its run's gone with the host: nobody to
                    chat is not None and (a.get("_tell") or time.time() - (a.get("_done") or 0) < DONE_KEEP_S)):
                continue  # collect it; a chat's, its model may yet read, or be told)
            if a.get("status") == "waiting" and (not is_chats(a) or chat is None or chat.meta.get("archived")):
                a["status"] = "stopped"  # (its run ended as the host stopped; its chat's gone meanwhile)
            if (a.get("proposal") or {}).get("status") == "answering":
                a["proposal"] = None
            a["sent_to"] = a["sent_to_name"] = None
            self.asks[a["id"]] = a

    # ---- kept
    def save(self):
        from baudelib.serverstate import _write_json
        with self.lock:
            _write_json(os.path.join(self.folder, "asks.json"), {"asks": list(self.asks.values())})
            now = time.monotonic()
            self.fresh = {k: t for k, t in self.fresh.items() if now - t < FRESH_S}
            used = {p["sha"] for a in self.asks.values() for v in a["versions"] for p in v["pictures"]} | set(self.fresh)
            folder = os.path.join(self.folder, "pictures")
            for n in os.listdir(folder) if os.path.isdir(folder) else ():
                if n not in used:
                    try:
                        os.unlink(os.path.join(folder, n))
                    except OSError:
                        pass

    def _picture(self, p, at=None):
        """A picture given ({name, mime?, data}): its bytes kept, its listing ({name, mime, size, width, height,
        sha}); ValueError if it isn't one this can show."""
        name = p.get("name") if isinstance(p, dict) else None
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(wording.say("inbox.bad_name", name=str(name)))
        try:
            data = base64.b64decode(p.get("data") or "", validate=True)
        except (ValueError, TypeError):
            raise ValueError(wording.say("inbox.bad_picture", name=name)) from None
        if len(data) > PICTURE_MAX:
            raise ValueError(wording.say("inbox.too_big", name=name, max=PICTURE_MAX))
        mime = mime_of(data)
        if mime is None:
            raise ValueError(wording.say("inbox.bad_picture", name=name))
        sha = hashlib.sha256(data).hexdigest()[:16]
        path = os.path.join(self.folder, "pictures", sha)
        with self.lock:
            self.fresh[sha] = time.monotonic()
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
                with open(path + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(path + ".tmp", path)
        w, h = size_of(data, mime)
        return {"name": name, "mime": mime, "size": len(data), "width": w, "height": h, "sha": sha}

    def version(self, msg, last=None, by=None):
        """An ask's version from what a request gives (title, body, pictures, buttons, form), each left out taken
        from last (the version before); ValueError for what isn't as an ask takes."""
        last = last or {}
        body = msg.get("body") if msg.get("body") is not None else last.get("body")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("an ask needs a body")
        title = msg.get("title") if "title" in msg and msg.get("title") is not None else last.get("title")
        pictures = last.get("pictures") or []
        if msg.get("pictures") is not None:
            if not isinstance(msg["pictures"], list) or len(msg["pictures"]) > PICTURES_MAX:
                raise ValueError(f"an ask has {PICTURES_MAX} pictures at most")
            pictures = {}
            for p in msg["pictures"]:
                got = self._picture(p)
                pictures[got["name"]] = got
            pictures = list(pictures.values())
        names = {p["name"] for p in pictures}
        buttons = msg.get("buttons") if msg.get("buttons") is not None else last.get("buttons")
        if not isinstance(buttons, list) or not buttons:
            raise ValueError(wording.say("inbox.no_buttons"))
        if len(buttons) > BUTTONS_MAX:
            raise ValueError(f"an ask has {BUTTONS_MAX} buttons at most")
        clean = []
        for b in buttons:
            b = {"label": b} if isinstance(b, str) else b
            if not isinstance(b, dict) or not isinstance(b.get("label"), str) or not b["label"].strip():
                raise ValueError("a button needs a label")
            out = {"label": " ".join(b["label"].split())[:LABEL_MAX],
                   "style": b.get("style") if b.get("style") in STYLES else "secondary"}
            if b.get("picture") is not None:
                if b["picture"] not in names:
                    raise ValueError(f"{out['label']}: no picture named {b['picture']}")
                out["picture"] = b["picture"]
            clean.append(out)
        form = msg.get("form") if msg.get("form") is not None else last.get("form") or []
        if not isinstance(form, list) or len(form) > FIELDS_MAX:
            raise ValueError(f"a form has {FIELDS_MAX} fields at most")
        form = [clean_field(f) for f in form]
        return {"n": (last.get("n") or 0) + 1 if last else 1, "at": time.time(), "by": by, "title": " ".join(title.split())[:TITLE_MAX] if isinstance(title, str)
                and title.strip() else None, "body": body[:BODY_MAX], "pictures": pictures, "buttons": clean,
                "form": form}

    # ---- told
    def tell(self, ask, removed=False):
        """Every subscribed head that may read the asking Bot told the ask as it is (inbox_changed); waits woken."""
        with self.lock:
            self.cv.notify_all()
            out = {"op": "inbox_changed", "ask": shown(ask), **({"removed": True} if removed else {})}
        srv = self.server
        with srv.lock:
            s = srv.sessions.get(ask["session"])
            conns = [c for c in srv.conns if c.subscribed and srv.access.allowed(c, "manage_read", s)]
        for c in conns:
            c.send(out)
        srv.on_change()

    def listing(self, conn):
        """inbox's asks: those of the Bots conn may read, oldest first."""
        srv = self.server
        with self.lock:
            asks = [shown(a) for a in sorted(self.asks.values(), key=lambda a: a["at"])
                    if a["status"] in ("waiting", "stopped")]
        with srv.lock:
            return [a for a in asks if srv.access.allowed(conn, "manage_read", srv.sessions.get(a["session"]))]

    def waiting(self):
        """How many asks wait (the menu bar's, and a restart's: what it would stop)."""
        with self.lock:
            return sum(a["status"] == "waiting" for a in self.asks.values())

    # ---- a script's
    def _run_ask(self, msg):
        """(the ask a script's request names, the run as it's held, its store): ValueError if the run isn't going
        with that token, or the ask isn't its own."""
        got = self.server.tasks.held(msg)
        if got is None:
            raise ValueError(wording.say("host_errors.no_run"))
        with self.lock:
            ask = self.asks.get(msg.get("ask"))
        if ask is None:
            raise ValueError(wording.say("inbox.no_ask", ask=str(msg.get("ask"))))
        if ask["run"] != msg.get("run"):
            raise ValueError(wording.say("inbox.not_yours"))
        return ask, got[0], got[1]

    def raise_(self, conn, msg):
        if msg.get("run") is None:
            return self._chat_raise(conn, msg)
        got = self.server.tasks.held(msg)
        if got is None:
            raise ValueError(wording.say("host_errors.no_run"))
        held, st = got
        version = self.version(msg)
        with self.server.lock:
            s = self.server.sessions.get(held["sid"])
            bot = ((s.meta.get("bot") or {}).get("name") if s is not None else None) or None
        ask = {"id": uuid.uuid4().hex[:16], "session": held["sid"], "bot": bot, "task": held["task"],
               "task_name": held.get("name") or "", "run": msg["run"], "at": version["at"], "status": "waiting",
               "versions": [version], "proposal": None, "sent_to": None, "sent_to_name": None, "answer": None}
        with self.lock:
            self.asks[ask["id"]] = ask
        self.save()
        if st is not None:
            st.log(msg["run"], "ask", title_of(version))
        self.tell(ask)
        return {"ask": ask["id"], "version": 1}

    def withdraw(self, conn, msg):
        ask = self._chat_ask(conn, msg) if msg.get("run") is None else self._run_ask(msg)[0]
        with self.lock:
            if ask["status"] == "waiting":
                ask.update(status="withdrawn", proposal=None, _done=time.time())
        self.save()
        self.tell(ask)
        return {"ask": ask["id"]}

    def wait(self, conn, msg):
        """inbox_wait: the ask as it is once it isn't waiting, or timeout's up (the run waiting for input meanwhile,
        its clock stopped), or its run's stopped. A chat's: as its model asks for it (_chat_wait)."""
        if msg.get("run") is None:
            return self._chat_wait(conn, msg)
        ask, held, st = self._run_ask(msg)
        timeout = msg.get("timeout")
        end = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        tasks = self.server.tasks
        waits = ask["status"] == "waiting" and (end is None or end > time.monotonic())
        if waits:
            tasks._waiting(st, msg["run"], held, True)
        try:
            with self.lock:
                while ask["status"] == "waiting" and not held.get("stop") and \
                        (end is None or time.monotonic() < end):
                    self.cv.wait(0.5 if end is None else max(0.0, min(0.5, end - time.monotonic())))
                return {"ask": ask["id"], "status": ask["status"], "answer": ask.get("answer")}
        finally:
            if waits:
                tasks._waiting(st, msg["run"], held, False)

    # ---- a chat's (its model's, with its own tools: baudelib.chattools)
    def _own(self, conn, sid):
        """(server's lock held) The chat sid, as its own tools ask of it: ValueError if conn isn't its own tools', or
        may not write to it, or it's gone or archived."""
        srv = self.server
        s = srv.sessions.get(sid) if isinstance(sid, str) else None
        if s is None or s.deleting or conn.acting != sid or conn.task is not None or \
                not srv.access.allowed(conn, "manage_write", s):  # (a task's script's: its run's, with its token)
            raise ValueError(wording.say("inbox.not_own"))
        if s.meta.get("archived"):
            raise ValueError(wording.say("inbox.chat_archived"))
        return s

    def _chat_ask(self, conn, msg):
        """The ask a chat's request names: ValueError if it isn't that chat's own, or conn isn't its tools'."""
        with self.lock:
            ask = self.asks.get(msg.get("ask"))
        if ask is None:
            raise ValueError(wording.say("inbox.no_ask", ask=str(msg.get("ask"))))
        if not is_chats(ask) or ask["session"] != msg.get("session"):
            raise ValueError(wording.say("inbox.not_own"))
        with self.server.lock:
            s = self.server.sessions.get(ask["session"])
            if s is None or conn.acting != s.id or conn.task is not None or \
                    not self.server.access.allowed(conn, "manage_read", s):
                raise ValueError(wording.say("inbox.not_own"))
        return ask

    def _chat_raise(self, conn, msg):
        version = self.version(msg)
        with self.server.lock:
            s = self._own(conn, msg.get("session"))
            bot, chat = (s.meta.get("bot") or {}).get("name") or None, chat_of(s)
        ask = {"id": uuid.uuid4().hex[:16], "session": s.id, "bot": bot, "chat": chat, "task": None,
               "task_name": None, "run": None, "at": version["at"], "status": "waiting", "versions": [version],
               "proposal": None, "sent_to": None, "sent_to_name": None, "answer": None}
        with self.lock:
            self.asks[ask["id"]] = ask
        self.save()
        self.tell(ask)
        return {"ask": ask["id"], "version": 1}

    def _chat_wait(self, conn, msg):
        """A chat's ask as it is once it isn't waiting, or timeout's up (WAIT_MAX_S at most; 0: as it is now). Its
        answer given so is its model's: it isn't sent the chat as well."""
        ask = self._chat_ask(conn, msg)
        try:
            timeout = min(WAIT_MAX_S, max(0.0, float(msg.get("timeout") if msg.get("timeout") is not None
                                                     else WAIT_MAX_S)))
        except (TypeError, ValueError):
            timeout = 0.0
        end, told = time.monotonic() + timeout, False
        with self.lock:
            self.waiters[ask["id"]] = self.waiters.get(ask["id"], 0) + 1
            try:
                while ask["status"] == "waiting" and time.monotonic() < end:
                    self.cv.wait(max(0.0, min(0.5, end - time.monotonic())))
                told = bool(ask.pop("_tell", None))
                out = {"ask": ask["id"], "status": ask["status"], "answer": ask.get("answer"),
                       "version": len(ask["versions"])}
            finally:
                self.waiters[ask["id"]] -= 1
                if not self.waiters[ask["id"]]:
                    del self.waiters[ask["id"]]
        if told:
            self.save()
        return out

    def chat_gone(self, sid):
        """A chat archived or deleted: its asks still waiting, stopped; those it's done with, let go of."""
        changed, dropped = [], []
        with self.lock:
            for a in list(self.asks.values()):
                if a["session"] != sid or not is_chats(a):
                    continue
                if a["status"] == "waiting":
                    a.update(status="stopped", proposal=None, sent_to=None, sent_to_name=None)
                    changed.append(a)
                elif a["status"] in ("answered", "withdrawn"):
                    del self.asks[a["id"]]
                    dropped.append(a)
        if changed or dropped:
            self.save()
        for a in changed:
            self.tell(a)
        for a in dropped:
            self.tell(a, removed=True)

    def deliver(self):
        """Each chat's asks answered and not yet told it (_tell), sent it, one at a time, once it's idle (the host's
        watch comes by again for the rest), as a message from the Inbox: its model's next turn. One its model waits on
        (inbox_wait) is its wait's to give."""
        srv, sent = self.server, False
        with srv.lock:
            with self.lock:
                due = [a for a in self.asks.values() if a.get("_tell") and not self.waiters.get(a["id"])]
            for a in sorted(due, key=lambda a: (a.get("answer") or {}).get("at") or 0):
                s = srv.sessions.get(a["session"])
                if s is not None and not s.deleting and not s.meta.get("archived") and s.busy:
                    continue
                with self.lock:
                    if not a.pop("_tell", None):
                        continue
                sent = True
                if s is None or s.deleting or s.meta.get("archived"):
                    continue
                try:
                    srv.take_input(None, s, {"type": "user", "uuid": str(uuid.uuid4()),
                                             "message": {"role": "user", "content": answer_said(a)}},
                                   {"kind": "host", "name": wording.say("inbox.title"), "host": srv.me["id"],
                                    "host_name": srv.me["name"], "session": s.id})
                except Exception as e:  # noqa: BLE001 (Elsewhere, its subscription off, or its agent not started)
                    srv.log(f"telling {s.id} its ask {a['id']} was answered: {e}")
        if sent:
            self.save()

    def run_over(self, rid):
        """A run ended: its asks still waiting, stopped; those it's done with (answered, withdrawn), let go of."""
        changed, dropped = [], False
        with self.lock:
            for a in list(self.asks.values()):
                if a["run"] != rid:
                    continue
                if a["status"] == "waiting":
                    a.update(status="stopped", proposal=None, sent_to=None, sent_to_name=None)
                    changed.append(a)
                elif a["status"] in ("answered", "withdrawn"):
                    del self.asks[a["id"]]
                    dropped = True
        if changed or dropped:
            self.save()
        for a in changed:
            self.tell(a)

    # ---- a head's (or a Bot's)
    def _find(self, conn, msg, write=True):
        """(the ask msg names, its Bot's Session or None): ValueError if there's none, or conn may not manage the
        asking Bot (or, a Bot it's been sent to, that Bot)."""
        with self.lock:
            ask = self.asks.get(msg.get("ask"))
        srv = self.server
        if ask is None:
            raise ValueError(wording.say("inbox.no_ask", ask=str(msg.get("ask"))))
        with srv.lock:
            s = srv.sessions.get(ask["session"])
            sent = srv.sessions.get(ask.get("sent_to") or "")
            rule = "manage_write" if write else "manage_read"
            if not srv.access.allowed(conn, rule, s) and not (sent is not None and conn.acting == sent.id):
                raise ValueError(wording.say("inbox.no_ask", ask=str(msg.get("ask"))))
        return ask, s

    def picture(self, conn, msg):
        ask, _s = self._find(conn, msg, write=False)
        n = msg.get("version")
        if not isinstance(n, int) or not 1 <= n <= len(ask["versions"]):
            raise ValueError(f"no version {n}")
        p = next((p for p in ask["versions"][n - 1]["pictures"] if p["name"] == msg.get("name")), None)
        if p is None:
            raise ValueError(f"no picture {msg.get('name')}")
        with open(os.path.join(self.folder, "pictures", p["sha"]), "rb") as f:
            return {"mime": p["mime"], "data": base64.b64encode(f.read()).decode()}

    def press(self, conn, msg):
        ask, _s = self._find(conn, msg)
        by = None
        if msg.get("as") is not None:  # (a Bot's answer_ask: the asking task's Bot, or one it's been sent to)
            if msg["as"] not in (ask["session"], ask.get("sent_to")) or \
                    conn.acting is not None and conn.acting != msg["as"]:
                raise ValueError(wording.say("inbox.no_ask", ask=ask["id"]))
            with self.server.lock:
                b = self.server.sessions.get(msg["as"])
                by = ((b.meta.get("bot") or {}).get("name") if b is not None else None) or "Bot"
        with self.lock:
            if ask["status"] != "waiting":
                raise ValueError(wording.say("inbox.not_waiting"))
            v = ask["versions"][-1]
            if msg.get("version") is not None and msg["version"] != v.get("n", len(ask["versions"])):
                raise ValueError(wording.say("inbox.changed"))
            n = msg.get("button")
            if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < len(v["buttons"]):
                raise ValueError(wording.say("inbox.no_button", n=str(n)))
            values = {}
            if v["form"]:
                given = {**{f["name"]: f["default"] for f in v["form"] if "default" in f}, **(msg.get("values") or {})}
                values, wrong = answered(given, v["form"])
                if wrong:
                    raise ValueError(wrong)
            if by is not None and msg["as"] != ask.get("sent_to"):  # (the asking Bot's, after a person wrote to it:
                ask["proposal"] = {"model": by, "name": by, "status": "ready", "button": n,  # a proposal, for the
                                   "values": values, "text": None}  # person to confirm, as a model's is)
                done = None
            else:
                done = ask["answer"] = {"button": n, "label": v["buttons"][n]["label"], "values": values, "by": by,
                                        "at": time.time()}
                ask.update(status="answered", proposal=None, sent_to=None, sent_to_name=None)
                if is_chats(ask):  # (told its chat: its model's wait, or its next turn, deliver)
                    ask.update(_tell=True, _done=done["at"])
        self.save()
        if done is not None and is_chats(ask):
            self.server.woken.set()
        st = self.server.tasks.stores.get(ask["session"])
        if done is not None and st is not None and ask["run"] in st.runs:
            st.log(ask["run"], "answer", done["label"] + (" " + json.dumps(values) if values else ""),
                   values=values or None)
        self.tell(ask)
        return {"ask": ask["id"]}

    def revise(self, conn, msg):
        """A new version: the asking run's script's (run, token), or the asking Bot's own (its revise_ask tool), or
        anyone's who may manage the Bot."""
        if msg.get("run") is not None:
            ask, _held, _st = self._run_ask(msg)
            by = None
        else:
            ask, s = self._find(conn, msg)
            by = ((s.meta.get("bot") or {}).get("name") if s is not None else None) or None
        own = msg.get("run") is not None or is_chats(ask) and conn.acting == ask["session"] and conn.task is None
        with self.lock:
            reopens = own and ask["status"] == "answered"  # (its run, or its chat, asks again: one ask)
            if ask["status"] != "waiting" and not reopens:
                raise ValueError(wording.say("inbox.not_waiting"))
            version = self.version(msg, ask["versions"][-1], by)
            ask["versions"] = [*ask["versions"], version]
            if len(ask["versions"]) > VERSIONS_MAX:
                ask["versions"] = ask["versions"][:1] + ask["versions"][-(VERSIONS_MAX - 1):]
            ask.update(status="waiting", answer=None, proposal=None)  # (answered for the version before: asked again,
            ask.pop("_tell", None)  # if it's wanted)
            n = len(ask["versions"])
        self.save()
        self.tell(ask)
        return {"ask": ask["id"], "version": n}

    def remove(self, conn, msg):
        ask, _s = self._find(conn, msg)
        with self.lock:
            if ask["status"] != "stopped":
                raise ValueError(wording.say("inbox.not_stopped"))
            del self.asks[ask["id"]]
        self.save()
        self.tell(ask, removed=True)
        return {"ask": ask["id"]}

    def say(self, conn, msg):
        """What a person wrote in an ask's box: sent the asking task's Bot as their message, the ask under it."""
        ask, s = self._find(conn, msg)
        text = msg.get("text") if isinstance(msg.get("text"), str) else ""
        if not text.strip():
            raise ValueError("nothing to send")
        if is_chats(ask):
            told = "\n\n".join((text[:TEXT_MAX], wording.say("inbox.person_wrote_chat", ask=ask["id"]), said(ask),
                                wording.say("inbox.chat_may")))
        else:
            told = "\n\n".join((text[:TEXT_MAX], wording.say("inbox.person_wrote", task=ask["task_name"],
                                                                ask=ask["id"]), said(ask), wording.say("inbox.bot_may")))
        srv = self.server
        with srv.lock:
            s = srv.sessions.get(ask["session"])
            if s is None or s.deleting:
                raise ValueError(wording.say("host_errors.no_task"))
            srv.take_input(conn, s, {"type": "user", "uuid": str(uuid.uuid4()),
                                     "message": {"role": "user", "content": told}})
        return {"ask": ask["id"]}

    def route(self, conn, msg):
        """A model asked to answer (its answer a proposal, for the person to confirm), a Bot sent the ask to answer,
        or (neither) the model's answer let go of."""
        ask, _s = self._find(conn, msg)
        with self.lock:
            if ask["status"] != "waiting":
                raise ValueError(wording.say("inbox.not_waiting"))
        if msg.get("bot"):
            return self._to_bot(conn, ask, msg["bot"])
        if msg.get("model"):
            return self._to_model(ask, msg["model"], msg.get("effort"))
        with self.lock:
            ask["proposal"] = None
        self.save()
        self.tell(ask)
        return {"ask": ask["id"]}

    def _to_bot(self, conn, ask, sid):
        srv = self.server
        with srv.lock:
            b = srv.sessions.get(sid)
            if b is None or b.deleting or not b.meta.get("bot") or not srv.access.allowed(conn, "manage_write", b):
                raise ValueError(f"no Bot {sid} here")
            name = (b.meta.get("bot") or {}).get("name") or "Bot"
            if is_chats(ask):
                text = "\n\n".join((wording.say("inbox.chat_sent", chat=ask.get("chat") or "", ask=ask["id"]),
                                     said(ask)))
                sender = {"kind": "bot" if ask.get("bot") else "chat", "name": ask.get("chat") or "",
                          "host": srv.me["id"], "host_name": srv.me["name"], "session": ask["session"]}
            else:
                text = "\n\n".join((wording.say("inbox.bot_sent", task=ask["task_name"], ask=ask["id"],
                                                bot=ask.get("bot") or "Bot"), said(ask)))
                sender = {"kind": "host", "name": wording.say("tasks.sender", name=ask["task_name"]),
                          "host": srv.me["id"], "host_name": srv.me["name"], "session": ask["session"],
                          "task": ask["task"], "run": ask["run"]}
            srv.take_input(None, b, {"type": "user", "uuid": str(uuid.uuid4()),
                                     "message": {"role": "user", "content": text}}, sender)
        with self.lock:
            ask.update(sent_to=sid, sent_to_name=name)
        self.save()
        self.tell(ask)
        return {"ask": ask["id"]}

    def _to_model(self, ask, model, effort=None):
        srv = self.server
        if not srv.can_run(model):
            raise ValueError(wording.say("host_errors.cant_run", host=srv.me["name"], model=model))
        listed = (srv.info() or {}).get("models") or ()
        name = providers.model_name(model, listed)
        with self.lock:
            ask["proposal"] = {"model": model, "name": name, "status": "answering", "button": None, "values": None,
                               "text": None}
        self.save()
        self.tell(ask)
        prompt = wording.say("inbox.model_prompt") + "\n\n" + said(ask)

        key = uuid.uuid4().hex  # (its session known by it as soon as it's made: its turn may end before start's back)
        with self.lock:
            self.proposing[key] = ask["id"]

        def start():
            try:
                srv.start({"prompt": prompt, "model": model, "effort": effort, "ephemeral": True, "mode": "default",
                           "ask_timeout": 1, "ask_default": "deny", "start_key": key,
                           "title": title_of(ask["versions"][-1])[:60]})
            except Exception as e:  # noqa: BLE001 (said on the ask)
                with self.lock:
                    self.proposing.pop(key, None)
                self._proposed(ask["id"], None, f"{type(e).__name__}: {e}")
        threading.Thread(target=start, daemon=True, name="inbox-model").start()
        return {"ask": ask["id"]}

    def _proposed(self, aid, text, failed=None):
        """A model's answer to the ask aid (what it said), or why it failed: its proposal."""
        with self.lock:
            ask = self.asks.get(aid)
            p = (ask or {}).get("proposal")
            if ask is None or p is None or p["status"] != "answering" or ask["status"] != "waiting":
                return
            v = ask["versions"][-1]
            got = None
            if failed is None:
                start, end = (text or "").find("{"), (text or "").rfind("}")
                try:
                    got = json.loads(text[start:end + 1]) if 0 <= start < end else None
                except ValueError:
                    got = None
            button = got.get("button") if isinstance(got, dict) else None
            if isinstance(button, str):  # (a label for an index)
                button = next((i for i, b in enumerate(v["buttons"]) if b["label"] == button), None)
            if not isinstance(button, int) or isinstance(button, bool) or not 0 <= button < len(v["buttons"]):
                p.update(status="failed", text=failed or (text or "").strip()[:500] or "no answer")
            else:
                values, _wrong = answered((got or {}).get("values") or {}, v["form"]) if v["form"] else ({}, None)
                if values is None:  # (what fits of what it filled)
                    values = {}
                    for f in v["form"]:
                        try:
                            values[f["name"]] = answered({f["name"]: got["values"][f["name"]]}, [f])[0][f["name"]]
                        except (KeyError, TypeError, IndexError):
                            pass
                why = got.get("why") if isinstance(got.get("why"), str) else None
                p.update(status="ready", button=button, values=values, text=(why or "").strip()[:1000] or None)
        self.save()
        self.tell(ask)

    def turn_over(self, s, msg):
        """(server's lock held) A session's turn ended: one asked to answer an ask, its answer taken, and let go of; a
        chat with an answer to be told, told it now it's idle (the watch woken: deliver)."""
        with self.lock:
            aid = self.proposing.pop(s.meta.get("start_key") or "", None)
            if any(a.get("_tell") and a["session"] == s.id for a in self.asks.values()):
                self.server.woken.set()  # (an answer to tell it, now it's idle: deliver)
        if aid is None:
            return
        failed = str(msg.get("result") or "error") if msg.get("is_error") else None
        threading.Thread(target=self._proposed, args=(aid, str(msg.get("result") or ""), failed), daemon=True,
                         name="inbox-proposed").start()
        self.server.drop_ephemeral(s)

    def agent_gone(self, s):
        """(server's lock held) A session asked to answer an ask ended before it answered: failed."""
        with self.lock:
            aid = self.proposing.pop(s.meta.get("start_key") or "", None)
        if aid is not None:
            threading.Thread(target=self._proposed, args=(aid, None, wording.say("tasks.agent_gone")), daemon=True,
                             name="inbox-proposed").start()

    # ---- the wire
    def op(self, conn, msg, reply):
        """One of the Inbox's ops (OPS): answered with reply; the ones that wait, on a thread of their own."""
        op = msg["op"]

        def run(fn, kind):
            try:
                reply(kind, **fn())
            except Exception as e:  # noqa: BLE001 (what's wrong said to whoever asked: Elsewhere, a disabled
                reply("error", error=str(e) or type(e).__name__)  # subscription, a bad field)

        if op in SCRIPT_OPS and msg.get("run") is not None or op == "inbox_revise" and msg.get("run") is not None:
            if conn.origin is not None or conn.member is not None:  # (a task's script: this machine's socket only)
                return conn.send(self.server.access.refused(msg.get("req"), "shell"))
        if op in SCRIPT_OPS and msg.get("run") is None and conn.origin is not None:  # (a chat's own tools: on its
            return conn.send(self.server.access.refused(msg.get("req"), "manage_write"))  # host's own socket)
        if op == "inbox":
            return reply("inbox_reply", asks=self.listing(conn))
        table = {"inbox_raise": (lambda: self.raise_(conn, msg), "inbox_raised"),
                 "inbox_revise": (lambda: self.revise(conn, msg), "inbox_raised"),
                 "inbox_withdraw": (lambda: self.withdraw(conn, msg), "inbox_done"),
                 "inbox_wait": (lambda: self.wait(conn, msg), "inbox_answered"),
                 "inbox_press": (lambda: self.press(conn, msg), "inbox_done"),
                 "inbox_route": (lambda: self.route(conn, msg), "inbox_done"),
                 "inbox_say": (lambda: self.say(conn, msg), "inbox_done"),
                 "inbox_remove": (lambda: self.remove(conn, msg), "inbox_done"),
                 "inbox_picture": (lambda: self.picture(conn, msg), "inbox_picture_data")}
        fn, kind = table[op]
        threading.Thread(target=run, args=(fn, kind), daemon=True, name=op).start()
        return None
