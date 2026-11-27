"""windowdrafts — what each of a Baude window's chats keeps (WindowDrafts): its draft, its model, the inference
providers, the stream-json protocol, compaction, its title, background tasks and artifacts.
"""
import base64
import json
import os
import time
import urllib.parse
import uuid
from baudelib.claude import EFFORT_LABELS, MODELS, MODES
from baudelib.transcripts import history_item, session_file, transcript_model
from baudelib import wire
import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import models as modelslib  # noqa: E402  (what runs a model: claude, apple-claude or pi-claude)
from baudelib import providers as providerslib  # noqa: E402  (where the models besides Claude's run: their providers)
from baudelib import toolnames, wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from baudelib.models import model_program  # noqa: E402,F401
from lookup import signal_place

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

import meta  # noqa: E402  (Meta, the helper in the side panel: Record's too; see meta.py)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import telemetryview  # noqa: E402  (its chats' activity, told to this machine's host)
from chatparts import QueuedTurn, SenderTurn, UserTurn  # noqa: E402
from widgets import discard, load_settings, save_settings  # noqa: E402,F401
from markdownview import ARTIFACT_TITLES, GIT_SCHEME, MarkdownView  # noqa: E402,F401
from codeview import CodePage  # noqa: E402,F401
from startup import LAUNCHER  # noqa: E402
from signalrefs import TOOL_NAMES  # noqa: E402
from toolcards import ARTIFACT_URL, STOP_SAID_RE, ThinkingView, ToolCard, api_error_label, tool_label  # noqa: E402
from baudelib.hostlogin import login_error  # noqa: E402
from chatinput import Attachment, attachment_widget  # noqa: E402
from sessionlist import HERE_HOST, THIS_MACHINE, local_host, main_folder, network, on_main, remote_name  # noqa: E402
from sidequestion import SideQuestion  # noqa: E402
from composerview import ComposerMarkdown  # noqa: E402


class WindowDrafts:
    """What each chat keeps: its draft, its model, providers, the protocol, compaction, its title,
    background tasks, artifacts."""

    # ---- each chat's draft: what's written in the box, its caret and what's attached, and its queue (queue_state),
    # kept for the chat as it's left for another (clear_transcript) and put back as it's opened again (take_draft);
    # every window's, kept over a restart (BaudeApp.restart). A new chat (nothing sent in it, nor anything of it listed
    # or saved) has the one draft, "": the next new chat opens with it, as its host drops a draft nobody's on and its id
    # goes with it. A queue kept for a chat not open is sent as its turn ends, as the open chat's is (send_left_queues)
    DRAFTS = {}  # draft_key -> {"text", "caret", "attachments", "queue"?: queue_state's}
    MAX_DRAFTS = 200
    QUEUE_SEEN = {}  # the id of a chat whose queue's kept here -> its state as last listed (send_left_queues)

    GONE = set()  # the ids of chats deleted or trashed: forget_draft

    @classmethod
    def forget_draft(cls, sid):
        """A chat deleted or trashed (here, in another window, or on its host: forget_gone): its draft gone with it, and
        nothing kept under its id from now on (draft_key)."""
        cls.GONE.add(sid)
        cls.DRAFTS.pop(sid, None)
        cls.QUEUE_SEEN.pop(sid, None)

    LISTED = None  # {host's key: its sessions' ids}, as last listed while it was there: forget_gone

    @classmethod
    def forget_gone(cls):
        """The network changed: a chat a host that's there no longer lists (deleted there, by any head) forgotten."""
        net = network()
        with net.lock:
            now = {r.key: set(r.sessions) for r in net.servers.values() if r.state == "alive"}
        for key, ids in (cls.LISTED or {}).items():
            for sid in ids - now.get(key, ids):
                cls.forget_draft(sid)
        cls.LISTED = {**(cls.LISTED or {}), **now}
        cls.GONE.difference_update(*now.values())  # (listed again: a host that had lost it has it back)

    def carry_on(self, sid):
        """The open chat (sid) deleted or trashed: a new chat in its place, what's typed in the box carried into it."""
        if sid == self.session_id:
            self.new_session(carry=True)
        self.forget_draft(sid)

    def draft_key(self):
        """What the open chat's draft is kept under: its id, or "" while it's a new chat (or one that's gone)."""
        sid = self.session_id
        if not sid or sid in self.GONE:
            return ""
        if getattr(self, "chat_sent", False):
            return sid
        r = self.remote or network().find(sid)  # (a host's, let go of as a chat's left: its host's still)
        listed = r.sessions.get(sid) if r is not None else None
        if listed is not None:
            return "" if listed.get("draft") else sid
        return sid if self.shows_anything() or session_file(sid) else ""

    def set_box(self, text, caret=None, atts=None):
        """The box's text (its caret, what's attached: as given, if given), the sidebar not filled again for it."""
        self.swapping_draft = True
        try:
            self.input.get_buffer().set_text(text)
            widgets.put_caret(self.input, caret)
            if atts is not None:
                self.attachments = list(atts)
                self.refresh_attach_strip()
        finally:
            self.swapping_draft = False

    def put_ahead(self, texts, atts):
        """texts and atts put in the box ahead of what's there, the caret after them, as a queued message's revert does
        (queue_edit)."""
        ahead = "\n\n".join(t for t in texts if t)
        draft = widgets.typed_text(self.input).strip()
        self.set_box("\n\n".join(p for p in (ahead, draft) if p), [len(ahead)] * 2, list(atts) + self.attachments)
        self.draft_typed()  # (put there by hand: dirty, saved to its host)

    @staticmethod
    def join_drafts(first, then):
        """Two drafts for one chat (another window's, and this one's) as one: first's text, then then's (the same text
        once), and what's attached to either; the caret where then's was."""
        a, b = first["text"], then["text"]
        if not a.strip() or a.strip() == b.strip():
            text, caret = b, then.get("caret")
        elif not b.strip():
            text, caret = a, first.get("caret")
        else:
            text, caret = f"{a}\n\n{b}", [c + len(a) + 2 for c in then.get("caret") or [len(b)] * 2]
        seen = [(x.name, x.path, x.data) for x in first["attachments"]]
        atts = first["attachments"] + [x for x in then["attachments"] if (x.name, x.path, x.data) not in seen]
        joined = {"text": text, "caret": caret, "attachments": atts}
        if first.get("queue") or then.get("queue"):  # (both queues, first's first; its ↑ and stop then's, if it has any)
            a, b = first.get("queue") or {"turns": []}, then.get("queue") or {"turns": []}
            joined["queue"] = {"turns": a["turns"] + b["turns"],
                               "hold": bool(b.get("hold") if b["turns"] else a.get("hold")),
                               "upto": len(a["turns"]) + b["upto"] if b.get("upto") is not None else a.get("upto")}
        return joined

    def keep_draft(self, key, queue=None):
        """The box's draft kept as key's, its queue with it (queue: queue_state's), with one another window left there
        (join_drafts), and the box emptied for the next chat's. An empty box and no queue keep nothing and drop
        nothing: a draft this window took is gone already (take_draft), and one there is another window's. A host's
        chat's typing not yet saved goes to its host first (flush_host_draft); dirty: its host hadn't taken it yet."""
        text, atts = widgets.typed_text(self.input), list(self.attachments)
        dirty = self.flush_host_draft()
        epoch, base = (self.hd.get("typed_in", 0), self.hd.get("base", "")) if self.hd_open() else (0, "")
        self.hd = {"sid": None}  # (its host's draft no longer this box's)
        if text.strip() or atts or queue:
            draft = {"text": text, "caret": widgets.caret_of(self.input), "attachments": atts, "dirty": dirty,
                     "epoch": epoch, "base": base, **({"queue": queue} if queue else {})}
            if (other := self.DRAFTS.pop(key, None)) is not None:
                draft = {**self.join_drafts(other, draft), "epoch": epoch, "base": base}
                draft["dirty"] = dirty or bool(other.get("dirty"))
            self.DRAFTS[key] = draft
            while len(self.DRAFTS) > self.MAX_DRAFTS:  # (the longest left go)
                del self.DRAFTS[next(iter(self.DRAFTS))]
        self.set_box("", None, [])

    def take_draft(self):
        """The chat just opened: its draft (keep_draft) in the box, and its queue under the conversation, if it was left
        with them; a host's chat's text as its host keeps it (open_host_draft)."""
        if draft := self.DRAFTS.pop(self.draft_key(), None):
            self.QUEUE_SEEN.pop(self.draft_key(), None)  # (its queue this window's again: sent as its turn ends here)
            self.set_box(draft["text"], draft["caret"], draft["attachments"])
            self.put_queue(draft.get("queue"))
            self.completer.hide()
        self.open_host_draft(draft)

    # ---- a host's chat's draft: its text kept by its host (draft_save), the same in every head that opens the chat,
    # on any machine. What's written here and its host hasn't taken yet is dirty: typed, pasted or put back in the
    # box by hand, till its host answers a save of that very text. Saved DRAFT_SAVE_MS after typing stops, at once
    # as the box is emptied (a message sent or queued) and as the chat's left; another head's save comes as
    # "_draft" (h_draft). The last write wins: one that comes while this box is dirty or has a save on its way is
    # held (hd's held), and put in once it's neither, if its host had it after this box's. Typing its host never took
    # is never replaced by another's write: a save it refused as too long (DRAFT_CHARS_MAX) stays this box's, not
    # saved again, another's write held as before; one it couldn't be given is saved again DRAFT_RETRY_MS later, twice
    # as long each time, DRAFT_RETRIES times, another's write held meanwhile, and as the chat's attached again.
    # Each Enter that empties the box is a send of its own (enter_id): its save empties its host's draft and starts
    # the draft's next epoch. Nothing written before a send comes back: its host refuses a save of an earlier epoch
    # (stale_draft), and every box drops a write of one; typing here from before another head's send keeps only
    # what was typed here, what its host had then (and has sent) taken out of it (rebase).
    # Only a dirty copy kept here is ever saved over its host's as the chat's opened. What's attached stays this app's (files on this
    # machine: DRAFTS)
    DRAFT_SAVE_MS, DRAFT_RETRY_MS, DRAFT_RETRIES = 400, 1000, 3
    hd = {"sid": None}  # (the open chat's: {sid, rev (the last its host's said), text (the markdown it has), dirty,
    # flight (saves on their way), held (another's, held), failed (its last save not given to its host, nothing typed
    # since: saved again), tries (saves made again since one was last taken), epoch (the last its host's said, or
    # this box's own send's, to come), base (what its host had in it as this box's typing began), typed_in (the epoch
    # what's in the box was written in), sent ((id, epoch before it): this box's send, its save not taken yet)})
    draft_timer = draft_retry = None

    @property
    def draft_head(self):
        """This window's box, as its saves say it (draft_save's head)."""
        if getattr(self, "_draft_head", None) is None:
            self._draft_head = str(uuid.uuid4())
        return self._draft_head

    def draft_host(self):
        """The host that keeps the open chat's draft (a host's chat, listed, there now, this head let write it), or
        None."""
        r = self.remote
        if r is None or r.state != "alive" or getattr(self, "desktop_chat", False) or not self.draft_key():
            return None
        return None if getattr(self.proc, "writes", True) is False else r

    def box_markdown(self):
        return ComposerMarkdown.to_markdown(widgets.typed_text(self.input))

    def hd_open(self):
        """Is hd the open chat's?"""
        return self.hd.get("sid") is not None and self.hd["sid"] == self.session_id

    def open_host_draft(self, local):
        """(the chat just opened, its local draft local, or None) Its host's draft in the box, unless what's kept here
        is dirty (typed while its host couldn't take it): that's saved to it instead. Its host with none, what's
        here is saved to it."""
        self.drop_draft_timer()
        dirty = bool(local and local.get("dirty"))
        self.hd = {"sid": self.session_id, "rev": 0, "text": None, "flight": 0, "held": None, "dirty": dirty,
                   "failed": False, "tries": 0, "epoch": 0, "base": "", "typed_in": 0, "sent": None}
        if self.draft_host() is None:
            return
        d = getattr(self.proc, "draft", None) if isinstance(self.proc, baudelib.client.RemoteProc) else None
        if not isinstance(d, dict):
            if self.box_markdown().strip():
                self.hd["dirty"] = True
                self.save_host_draft()
            return
        self.host_queue_in(getattr(self.proc, "queue", None))  # (its queue, its host's)
        epoch = d.get("epoch") or 0
        self.hd.update(rev=d.get("rev") or 0, text=d.get("text") or "", epoch=epoch, base=d.get("text") or "",
                       typed_in=epoch)
        if dirty and (local.get("epoch") or 0) < epoch:  # (typed before a send since: only what was typed here)
            self.set_box(self.after_send(local.get("base") or "", self.box_markdown(), self.hd["text"]))
        if dirty and self.box_markdown() != self.hd["text"]:
            return self.save_host_draft()
        self.hd["dirty"] = False
        self.put_host_text(self.hd["text"])

    def reattached_draft(self):
        """(the open chat attached again: its host back, say) What's been typed since its host last took it saved to
        it; else what it has, if it's had a write since. Its queue as its host has it."""
        if isinstance(self.proc, baudelib.client.RemoteProc):
            self.host_queue_in(self.proc.queue)
        if not self.hd_open() or not isinstance(self.proc, baudelib.client.RemoteProc) or self.draft_host() is None:
            return
        if self.hd["dirty"]:
            return self.save_host_draft()
        self.host_draft_in(self.proc.draft)

    @staticmethod
    def moved_offset(old, new, at):
        """Where offset at in old is in new, the change between them the part neither's start nor end shares."""
        start, most = 0, min(len(old), len(new))
        while start < most and old[start] == new[start]:
            start += 1
        end = 0
        while end < most - start and old[-1 - end] == new[-1 - end]:
            end += 1
        if at <= start:
            return at
        if at >= len(old) - end:
            return at + len(new) - len(old)
        return len(new) - end

    def put_host_text(self, text):
        """text in the box (what's attached as it is), the caret and selection carried over the change, unless that's
        what's there."""
        self.hd["text"] = text
        if self.box_markdown() != text:
            old = widgets.typed_text(self.input)
            self.set_box(text, [self.moved_offset(old, text, c) for c in widgets.caret_of(self.input)])

    def drop_draft_timer(self):
        """The save on its way after typing stops, and one to be made again, not made."""
        if self.draft_timer is not None:
            GLib.source_remove(self.draft_timer)
            self.draft_timer = None
        self.drop_draft_retry()

    def drop_draft_retry(self):
        """A save to be made again, not made (also as the window's closed)."""
        if self.draft_retry is not None:
            GLib.source_remove(self.draft_retry)
            self.draft_retry = None

    def draft_typed(self):
        """(the box's text changed by hand: draft_changed, put_ahead) Dirty, and saved to its host DRAFT_SAVE_MS after
        typing stops, at once as it's emptied; written back to what its host has, clean."""
        self.drop_draft_timer()
        if self.reverting is not None or not self.hd_open():
            return
        text, hd = self.box_markdown(), self.hd
        hd.update(failed=False, tries=0)
        if text.strip():
            hd.update(sent=None, typed_in=hd["epoch"])
        elif getattr(self, "enter_id", None) and (hd["sent"] or (None,))[0] != self.enter_id:  # (emptied by a
            hd.update(sent=(self.enter_id, hd["epoch"]), epoch=hd["epoch"] + 1, base="")  # send: the next epoch's,
            hd["typed_in"] = hd["epoch"]  # and nothing from before it written back)
        else:
            hd["typed_in"] = hd["epoch"]
        if text == hd["text"] and not hd["sent"]:
            hd["dirty"] = False
            return self.release_held()
        self.hd["dirty"] = True
        if self.draft_host() is None:
            return
        self.draft_timer = GLib.timeout_add(0 if not text.strip() else self.DRAFT_SAVE_MS, self.draft_due)  # (emptied:
        # on the main loop's next turn, so text put in its place at once, as set_text does, goes instead)

    def draft_due(self):
        self.draft_timer = None
        self.save_host_draft()
        return False

    def flush_host_draft(self):
        """(the chat being left, or the app restarting) Dirty typing saved to its host now; whether it's still dirty
        (its host's answer not come yet, or it can't be told: saved again as it's opened)."""
        self.drop_draft_timer()
        if not self.hd_open() or self.reverting is not None:
            return False
        if self.hd["dirty"] and self.draft_host() is not None:
            self.save_host_draft()
        return bool(self.hd["dirty"])

    def save_host_draft(self):
        """The box's text saved to the open chat's host (draft_save): clean once its host's taken that very text and
        nothing's been typed since."""
        self.drop_draft_timer()
        r = self.draft_host()
        if r is None:
            return
        hd, sid, text = self.hd, self.session_id, self.box_markdown()
        hd["text"], hd["failed"] = text, False
        hd["flight"] += 1
        sent = hd["sent"] if not text.strip() else None
        msg = {"op": "draft_save", "session": sid, "text": text, "head": self.draft_head,
               "epoch": sent[1] if sent else hd["typed_in"], **({"sent": sent[0]} if sent else {})}

        def done(reply):
            hd["flight"] -= 1
            took = reply.get("op") == "draft_saved"
            stale = reply.get("code") == wire.STALE and isinstance(reply.get("draft"), dict)
            again = took and sent and reply["draft"].get("head") != self.draft_head  # (that send's own, made again:
            if took and not again:  # its host's draft now, which another head may have written: that's come in below)
                d = reply["draft"]
                hd["rev"] = max(hd["rev"], d["rev"])
                if (d.get("epoch") or 0) >= hd.get("epoch", 0):
                    hd.update(epoch=d.get("epoch") or 0, base=d.get("text") or "")
            if (took or stale) and sent and hd.get("sent") == sent:
                hd["sent"] = None
            if self.hd is not hd:  # (left: what was kept of it clean, if this was what it had)
                if took and (d := self.DRAFTS.get(sid)) is not None and \
                        ComposerMarkdown.to_markdown(d["text"]) == text:
                    d["dirty"] = False
                return
            if again:
                if not hd["flight"] and self.draft_timer is None and self.box_markdown() == text:
                    hd["dirty"] = False
                return self.host_draft_in(reply["draft"])
            if stale:  # (written before a send since: its host's draft in, and only what was typed here: rebase)
                if (reply["draft"].get("epoch") or 0) >= hd["epoch"] and (reply["draft"].get("rev") or 0) >= hd["rev"]:
                    self.rebase(reply["draft"])
                return
            if not took:  # (not kept there: still dirty, this box's, and held against others'. Too long: stays so, as
                hd["text"] = None  # its host will never take it. Else saved again soon, and as it's opened or attached)
                if not hd["flight"] and self.draft_timer is None and self.box_markdown() == text and \
                        reply.get("code") != wire.INVALID:
                    hd["failed"] = True
                    self.retry_host_draft(text)
                return
            hd["tries"] = 0
            if not hd["flight"] and self.draft_timer is None and self.box_markdown() == text:
                hd["dirty"] = False
                self.release_held()
        r.request(msg, done)

    def retry_host_draft(self, text):
        """(a save of text its host didn't take) Saved again in a while, unless it's been tried DRAFT_RETRIES times, or
        the box has changed, another's write come or the chat's been left meanwhile."""
        hd = self.hd
        if hd["tries"] >= self.DRAFT_RETRIES or self.draft_retry is not None:
            return

        def again():
            self.draft_retry = None
            if self.hd is hd and hd["failed"] and not hd["flight"] and self.draft_timer is None and \
                    self.box_markdown() == text:
                self.save_host_draft()
            return False
        self.draft_retry = GLib.timeout_add(self.DRAFT_RETRY_MS << hd["tries"], again)
        hd["tries"] += 1

    def release_held(self):
        """Another head's write held while this box was dirty or saving, put in now it's neither."""
        hd = self.hd
        if (held := hd.get("held")) is not None and not hd["flight"] and not hd["dirty"] and \
                self.draft_timer is None:
            hd["held"] = None
            self.host_draft_in(held)

    def h_draft(self, msg):
        """Another head's write of the open chat's draft, as its host tells it (draft_changed)."""
        self.host_draft_in(msg.get("draft"))

    def host_draft_in(self, d):
        """Its host's draft (another head's write, or as it's attached again) in the box, unless it's from before a
        send this box knows of. One of a later epoch than this box's typing: its own typing kept, and only that
        (rebase); else held while this box is dirty or saving."""
        hd = self.hd
        if not isinstance(d, dict) or not self.hd_open() or (d.get("epoch") or 0) < hd["epoch"]:
            return  # (from before a send: what was sent, never back)
        if (d.get("rev") or 0) <= hd["rev"]:
            return  # (one seen already, or older than one that has been)
        epoch = d.get("epoch") or 0
        if d.get("head") == self.draft_head:  # (this box's own save, back)
            hd.update(rev=d["rev"], epoch=epoch)
            return
        if epoch > hd["typed_in"] and (hd["dirty"] or self.draft_timer is not None) and self.reverting is None:
            return self.rebase(d)
        if hd["flight"] or hd["dirty"] or self.draft_timer is not None or \
                self.reverting is not None:
            if hd["held"] is None or d["rev"] > hd["held"]["rev"]:  # (this box's own goes after, unless its host had
                hd["held"] = d                                      # this after it)
            return
        hd.update(rev=max(hd["rev"], d["rev"]), epoch=epoch, base=d.get("text") or "", typed_in=epoch)
        self.put_host_text(d.get("text") or "")

    @staticmethod
    def after_send(base, typed, now):
        """(the box written in from base, its host's draft then, as typed; its draft emptied by a send since, now
        what it has) What the box keeps: now, and what was typed here into base (the part base's start and end
        don't share), not base's own text, sent."""
        start, most = 0, min(len(base), len(typed))
        while start < most and base[start] == typed[start]:
            start += 1
        end = 0
        while end < most - start and base[-1 - end] == typed[-1 - end]:
            end += 1
        mine = typed[start:len(typed) - end].strip() if base else typed
        return "\n\n".join(p for p in (now, mine) if p.strip())

    def rebase(self, d):
        """(d: its host's draft, of an epoch after this box's typing: a send emptied it since) The box: d's text, and
        what was typed here (after_send); saved a moment later if that's more than d's."""
        hd, text = self.hd, d.get("text") or ""
        keep = self.after_send(hd["base"], self.box_markdown(), text)
        self.drop_draft_timer()
        epoch = d.get("epoch") or 0
        hd.update(rev=max(hd["rev"], d["rev"]), epoch=epoch, base=text, typed_in=epoch, text=text, held=None,
                  failed=False, tries=0)
        if self.box_markdown() != keep:
            self.set_box(keep, [len(keep)] * 2)
        hd["dirty"] = keep != text
        if hd["dirty"] and self.draft_host() is not None:
            self.draft_timer = GLib.timeout_add(self.DRAFT_SAVE_MS, self.draft_due)

    # ---- a host's chat's queue: kept by its host (hostqueue), the same in every head that opens the chat, on any
    # machine. What's queued here is added there, and what's sent, edited or deleted here dropped there; its host
    # leads it, sending it as the turn ends, so every head shows it and it's sent once. Each change comes back as
    # its answer, another head's or its host's sending it as "_queue" (h_queue). One its host won't take (too big)
    # stays this window's, sent as its turn ends here, as before
    hq = {"sid": None, "rev": -1}  # (the open chat's queue: the last revision its host's said)

    def queue_host(self):
        """The open chat's connection, if its host keeps its queue (as its draft: draft_host), else None."""
        p = self.proc
        return p if self.draft_host() is not None and isinstance(p, baudelib.client.RemoteProc) else None

    def queue_to_host(self, turn):
        """A message just queued, added to its host's queue."""
        p = self.queue_host()
        if p is None:
            return
        turn.qid, turn.hosted = turn.said or str(uuid.uuid4()), "adding"
        sid = self.session_id

        def done(reply):
            if reply.get("op") == "queue" and self.session_id == sid:
                self.host_queue_in(reply.get("queue"))
                if turn.hosted == "adding":  # (its host's queue has no row for it: sent at once, as no turn was going)
                    self.unqueue([turn], host=False)
                return
            if turn.hosted == "adding":  # (not taken: this window's to send, as its turn ends)
                turn.hosted = False
        p.queue_op({"add": [{"id": turn.qid, "text": turn.text, "content": self.user_content(turn.text, turn.atts)}]},
                   done)

    def unqueue_on_host(self, turns):
        """Queued messages sent, edited or deleted here: dropped from its host's queue."""
        ids = [t.qid for t in turns if t.hosted and t in self.queued]
        if ids and (p := self.queue_host()) is not None:
            sid = self.session_id
            p.queue_op({"drop": ids}, lambda r: self.session_id == sid and r.get("op") == "queue" and
                       self.host_queue_in(r.get("queue")))

    def queue_upto_host(self):
        """(a send-now's interrupt about to be sent) The queued message it's for, its host's: named to its host, which
        sends it and those before it as the turn ends, not this window."""
        t = self.queue_upto
        if isinstance(t, QueuedTurn) and t.hosted and (p := self.queue_host()) is not None:
            self.queue_upto = None
            sid = self.session_id
            p.queue_op({"upto": t.qid}, lambda r: self.session_id == sid and r.get("op") == "queue" and
                       self.host_queue_in(r.get("queue")))

    def follow_host_queue(self):
        """(the open chat's agent gone mid-turn, stopped outright) Attached again if its host has what's queued here,
        which it sends now, so its turn's followed here as it would be had this window sent it: once what's sent is
        in its transcript as it's fetched (FOLLOW_WAIT_S at most), so it's drawn before the turn that answers it."""
        if self.remote is None or self.proc is not None or not any(t.hosted for t in self.queued):
            return
        sid, path, since = self.session_id, self.view_path, time.monotonic()
        size = os.path.getsize(path) if path and os.path.exists(path) else 0

        def due():
            if self.session_id != sid or self.proc is not None or not self.resume_pending:
                return False
            grown = path and os.path.exists(path) and os.path.getsize(path) > size
            if not grown and time.monotonic() - since < self.FOLLOW_WAIT_S:
                return True
            self.begin_resume()
            return False
        GLib.timeout_add(200, due)

    FOLLOW_WAIT_S = 5

    def h_queue(self, msg):
        """The open chat's queue changed on its host: another head's change, or its host sending it (queue_changed)."""
        self.host_queue_in(msg.get("queue"))

    @staticmethod
    def queued_atts(content):
        """What's attached to a queued message, from its content (pictures and documents; files named in its text)."""
        atts = []
        for b in content if isinstance(content, list) else ():
            src = b.get("source") or {} if isinstance(b, dict) else {}
            if src.get("type") != "base64" or b.get("type") not in ("image", "document"):
                continue
            try:
                data = base64.b64decode(src.get("data") or "")
            except ValueError:
                continue
            atts.append(Attachment(b.get("title") or "image", "image" if b["type"] == "image" else "pdf",
                                   src.get("media_type") or "application/pdf", data, None))
        return atts

    def host_queue_in(self, q):
        """The open chat's queue as its host has it, under the conversation: what's gone from it gone here, what's
        new in it drawn in its place, what this window's adding (and what its host won't take) left as it is."""
        if not isinstance(q, dict) or self.queue_host() is None:
            return
        if self.hq.get("sid") != self.session_id:
            self.hq = {"sid": self.session_id, "rev": -1}
        if (q.get("rev") or 0) < self.hq["rev"]:
            return  # (older than one seen)
        self.hq["rev"] = q.get("rev") or 0
        turns = [t for t in q.get("turns") or () if isinstance(t, dict) and isinstance(t.get("id"), str)]
        ids = {t["id"] for t in turns}
        self.unqueue([t for t in self.queued if t.hosted is True and t.qid not in ids], host=False)
        here = {t.qid: t for t in self.queued if t.qid}
        prev = None
        for t in turns:
            turn = here.get(t["id"])
            if turn is None:
                atts = self.queued_atts(t.get("content"))
                turn = QueuedTurn(self, self.user_bubble(t.get("text") or "", atts), t.get("text") or "", atts)
                turn.qid = t["id"]
                at = self.queued.index(prev) + 1 if prev is not None else 0
                self.queued.insert(at, turn)
                after = prev if prev is not None else (self.queued[1].get_prev_sibling() if len(self.queued) > 1
                                                       else self.queue_box.get_last_child())
                self.queue_box.insert_child_after(turn, after)
                self.stick_bottom, self.restore_scroll = True, None
            turn.hosted = True
            prev = turn
        self.queue_box.set_visible(bool(self.queued or any(t.get_visible() for t in
                                                            getattr(self, "held_turns", {}).values())))

    @classmethod
    def drafts_state(cls):
        """The drafts as a restart keeps them (BaudeApp.restart)."""
        return {k: {**d, "attachments": [a.state() for a in d["attachments"]],
                    **({"queue": cls.queue_saved(d["queue"])} if d.get("queue") else {})}
                for k, d in cls.DRAFTS.items()}

    @classmethod
    def restore_drafts(cls, state):
        for key, d in (state or {}).items():
            cls.DRAFTS.setdefault(key, {**d, "attachments": [Attachment.from_state(a) for a in d["attachments"]],
                                        **({"queue": cls.queue_loaded(d["queue"])} if d.get("queue") else {})})

    @staticmethod
    def queue_saved(queue):
        """A queue (queue_state's) as a restart keeps it: its attachments as theirs (Attachment.state)."""
        return {**queue, "turns": [{**t, "attachments": [a.state() for a in t["attachments"]]} for t in queue["turns"]]}

    @staticmethod
    def queue_loaded(queue):
        return {**queue, "turns": [{**t, "attachments": [Attachment.from_state(a) for a in t["attachments"]]}
                                   for t in queue["turns"]]}

    @classmethod
    def send_left_queues(cls):
        """(the network changed) A chat left with a queue, whose turn its host lists over now: what's queued sent to
        it, as the open chat's is as its turn ends (left_turn_ended), by its host, whatever's open. Only a turn's end
        seen here sends it: one over by the time it's first listed (a restart's) leaves it for its chat's ↑."""
        for key in [k for k, d in cls.DRAFTS.items() if d.get("queue")]:
            r = network().find(key)
            listed = r.sessions.get(key) if r is not None and r.state == "alive" else None
            if listed is None:
                continue
            state, was = listed.get("state") or "idle", cls.QUEUE_SEEN.get(key)
            cls.QUEUE_SEEN[key] = state
            if was in ("working", "waiting") and state not in ("working", "waiting"):
                cls.left_turn_ended(key, lambda content, said, done, r=r, key=key: r.request(
                    {"op": "input", "session": key, "msg": {"type": "user", "uuid": said,
                                                            "message": {"role": "user", "content": content}}}, done))

    @classmethod
    def left_turn_ended(cls, key, send):
        """The turn of a chat left with a queue (key's draft's) over: the queue sent as the open chat's would be
        (flush_queue), with send(content, uuid, done): all of it as one message, or up to the one whose ↑ stopped the
        turn. Sent by a uuid kept with it, so its host takes it once if it's sent
        again; put back at the queue's front if it isn't taken (in its window's queue, if it's open in one by then).
        True if something was sent."""
        draft = cls.DRAFTS.get(key) or {}
        queue = draft.get("queue")
        if not queue:
            return False
        upto = queue.get("upto")
        queue["upto"], queue["hold"] = None, False
        turns = queue["turns"][:upto + 1 if upto is not None else None]
        del queue["turns"][:len(turns)]
        if not queue["turns"]:
            del draft["queue"]
            if not draft["text"].strip() and not draft["attachments"]:
                del cls.DRAFTS[key]
            cls.QUEUE_SEEN.pop(key, None)
        text = "\n\n".join(t["text"] for t in turns if t["text"])
        atts = [a for t in turns for a in t["attachments"]]
        said = turns[0].get("said") if len(turns) == 1 and turns[0].get("said") else str(uuid.uuid4())

        def done(r):
            if r.get("op") != "error":
                return
            back = {"text": text, "attachments": atts, "said": said}
            app = Gio.Application.get_default()
            for w in app.windows() if app is not None and hasattr(app, "windows") else ():
                if w.draft_key() == key:
                    w.put_queue({"turns": [back], "upto": None, "hold": False})
                    return
            d = cls.DRAFTS.setdefault(key, {"text": "", "caret": None, "attachments": []})
            d.setdefault("queue", {"turns": [], "upto": None, "hold": False})["turns"].insert(0, back)
        send(cls.user_content(text, atts), said, done)
        return True

    def ask_side_question(self, question):
        sq = SideQuestion(self, question)
        self.panel.add_side_question(sq)
        self.show_panel(True, "btw")
        return sq

    @staticmethod
    def user_content(text, atts):
        """Plain text when there is nothing attached; otherwise blocks, media first."""
        if not atts:
            return text
        blocks = [b for a in atts if (b := a.block())]
        paths = [a.path for a in atts if a.kind == "file"]
        if paths:
            listing = "\n".join(f"- {p}" for p in paths)
            text = (text + "\n\n" if text else "") + f"Attached files (open them with your tools):\n{listing}"
        if text:
            blocks.append({"type": "text", "text": text})
        return blocks

    def user_bubble(self, text, atts):
        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.END)
        bubble.add_css_class("user-bubble")
        if atts:
            row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
            for a in atts:
                row.append(attachment_widget(a, 96))
            bubble.append(row)
        if text:
            bubble.append(MarkdownView(text))
        return bubble

    def chat_senders(self):
        """Who the open chat's messages from others than you are from, by their uuids, as its host's sender store
        had them when it was last fetched ({} for a chat of no host's)."""
        if self.remote is None or not self.session_id:
            return {}
        return self.remote.senders_of(self.session_id)

    def sync_senders(self):
        """The open chat's host's sender store fetched, if it lists a newer one than is here (senders_came)."""
        if self.remote is not None and self.session_id and self.remote.state == "alive":
            sid = self.session_id
            self.remote.sync_senders(sid, lambda: self.senders_came(sid))

    def senders_came(self, sid):
        """The chat's sender store, fetched: a message of yours drawn before it came that it says is another's
        drawn as that one's, hidden as it was (a revert not sent yet: in what it hides). The one a pending revert's of
        is left as it is: the revert goes on with it."""
        if sid != self.session_id or getattr(self, "let_go_done", False):
            return
        senders, rev = self.chat_senders(), self.reverting
        stack = [self.transcript]
        while stack:
            child = stack.pop().get_first_child()
            while child is not None:
                after = child.get_next_sibling()
                if isinstance(child, UserTurn) and not isinstance(child, SenderTurn) and child.uuid in senders and \
                        isinstance(child.get_parent(), Gtk.Box) and (rev is None or child is not rev["turn"]):
                    turn = self.sender_turn(senders[child.uuid], child.text, getattr(child, "atts", ()), child.uuid)
                    turn.set_visible(child.get_visible())
                    turn.zoom_hidden = getattr(child, "zoom_hidden", False)
                    if rev is not None and child in rev["hidden"]:
                        rev["hidden"][rev["hidden"].index(child)] = turn
                    for side in ("above", "below"):  # (one a zoom's hidden, to show as it settles: this in its place)
                        held = (self.zoom_job or {}).get(side) or []
                        if child in held:
                            held[held.index(child)] = turn
                    child.get_parent().insert_child_after(turn, child)
                    child.get_parent().remove(child)
                elif getattr(child, "is_history", False) or getattr(child, "span", None) is not None:
                    stack.append(child)  # (what's drawn of its history, and of its earlier chunks)
                child = after

    def sender_turn(self, sender, text, atts, said):
        """A message from sender, its host's sender store says (uuid said), as the chat draws it: SenderTurn, without
        the line its host put at its head for its agent (wire.said_by), and what the store says is shown of it in
        place of its text if it says (shown: a held answer sent, its agent told more). It reverts as yours do, its
        words left out of your box."""
        said_text = baudelib.wire.unsaid(text or "").strip()
        drawn = baudelib.wire.unsaid(sender["shown"]).strip() if isinstance(sender.get("shown"), str) else said_text
        return SenderTurn(self, self.sender_bubble(sender, drawn, atts, said), said_text, said)

    def sender_bubble(self, sender, text, atts=(), said=None):
        """A message from sender, not you (its host's sender store's): where yours are, grey (not your accent), as a
        queued one is, with who it's from in a small line at its top: a link to that chat (a task's: to its run, on
        its Bot's Tasks page), if it names one; a chat a task's run started, then its run (a link to it) and how its
        turn ended: "from subagent “Why” · Deploy site run · finished"; a task's ask (uuid said) whose run waits for
        its answer, then that it does, while it does (sender_waits)."""
        bubble = self.user_bubble(text, atts)
        bubble.add_css_class("sender-bubble")
        head = Gtk.Label(xalign=1, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, halign=Gtk.Align.END)
        head.sender, head.said, head.waits = sender, said, None
        if sender.get("session"):
            head.connect("activate-link", lambda _l, uri: self.open_sender(sender, uri == "run") or True)
        head.add_css_class("sender-from")
        self.sender_head(head)
        if said and sender.get("run"):
            head.chat = self.session_id
            self.sender_heads = [h for h in getattr(self, "sender_heads", ()) if h.chat == self.session_id] + [head]
        bubble.prepend(head)
        return bubble

    def task_asks(self):
        """The asks (their uuids) the open chat's been sent whose tasks' runs wait for its answer, as its host lists
        them (a Bot's task_asks)."""
        if self.remote is None or not self.session_id:
            return ()
        return tuple((self.remote.sessions.get(self.session_id) or {}).get("task_asks") or ())

    def sender_head(self, head):
        """A from line drawn as its sender says, and, for a task's ask its run waits on, that it does."""
        sender = head.sender
        waits = bool(head.said) and head.said in self.task_asks()
        if waits == head.waits:
            return
        head.waits = waits
        said = wording.say("common.from_who", author=baudelib.wire.sender_name(sender))
        run, about = baudelib.wire.sender_run(sender), baudelib.wire.sender_about(sender)
        after = [wording.say("senders.run_waiting")] if waits else []
        if not sender.get("session"):
            head.set_label(" · ".join([said, *about, *after]))
            return
        esc = GLib.markup_escape_text
        parts = [f'<a href="sender">{esc(said)}</a>']
        for i, part in enumerate(about):  # (its run: a link to it; how it ended, plain)
            parts.append(f'<a href="run">{esc(part)}</a>' if i == 0 and sender.get("task_name") and run
                         else esc(part))
        parts += [f'<a href="run">{esc(part)}</a>' for part in after]  # (the run that waits: a link to it)
        head.set_markup(" · ".join(parts))

    def sender_waits(self):
        """The open chat's from lines of tasks' asks drawn again as their runs wait for an answer or stop waiting."""
        self.sender_heads = [h for h in getattr(self, "sender_heads", ()) if h.chat == self.session_id]
        for head in self.sender_heads:
            self.sender_head(head)

    def open_sender(self, sender, to_run=False):
        """The chat a message is from, opened (a task's, or to_run, a chat a task's run started's run: its Bot's, on
        its Tasks page at that run, its task's page under it), on its host."""
        run = baudelib.wire.sender_run(sender)
        if run and (to_run or not sender.get("bot")):
            bot, task, rid = run
            return self.see_blocker(self.remote, {"kind": "task", "session": bot, "host": sender.get("host"),
                                                  "task": task, "run": rid, "name": sender.get("task_name")})
        self.see_blocker(self.remote, {"kind": "chat", "session": sender["session"], "host": sender.get("host")})

    def can_revert(self):
        """Whether a message of yours can be reverted here: every model's chat can (its program cuts the conversation:
        rewind_conversation, which claude has, and pi-claude and apple-claude answer as it does), but not one that's
        only read here (live_elsewhere)."""
        return not self.live_elsewhere()

    def login_ask(self, msg):
        """A message of claude's saying its sign-in ran out, or None: only a Claude Code chat's (another program's
        sign-in isn't `claude auth login`'s)."""
        return login_error(msg) if model_program(self.model) == "claude" else None

    def program_name(self):
        """What runs the chat's model, as a note names it: Claude, Pi, Codex or Apple's model."""
        program = model_program(self.model)
        return wording.say(f"programs.{program}" if program in ("pi", "apple", "codex") else "report.claude")

    def revert_to(self, turn):
        """A message of yours reverted (UserTurn's button), tentatively, as a model or a folder picked for a chat is till
        it's written in: it and everything after it hidden, and what it said put in the box (another's message: the box
        left as it is, and sending makes the revert with nothing in it too). Esc puts them back, and the
        draft that was there (cancel_revert); sending makes the revert (commit_revert), then sends. One already being
        reverted is put back first (the draft kept: the one from before either)."""
        if not self.can_revert() or turn.uuid is None:
            return
        draft = None
        if self.reverting is not None:
            if self.revert_asked():
                return
            draft = self.reverting["draft"]
            self.cancel_revert(restore_draft=False)
        buf = self.input.get_buffer()
        if draft is None:
            draft = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        self.reverting = {"turn": turn, "hidden": [], "draft": draft, "asked": None}
        w = turn
        while w is not None and w is not self.transcript:  # (it, what's after it, and after what it's in)
            after = w.get_next_sibling() if w is not turn else w
            while after is not None:
                self.hide_reverted(after)
                after = after.get_next_sibling()
            w = w.get_parent()
        if not isinstance(turn, SenderTurn):  # (another's words aren't put in your box: it's left as it is)
            buf.set_text(turn.text or "")
        buf.place_cursor(buf.get_end_iter())
        self.input.grab_focus()
        self.stick_bottom = True

    def hide_reverted(self, widget):
        """A widget hidden while a revert's not sent (revert_to), to be shown again if it's not (cancel_revert)."""
        if widget is not None and getattr(widget, "zoom_hidden", False):  # (hidden by a zoom, to be shown as it
            widget.zoom_hidden = False                                     # settles: not now, but with the rest)
            for side in ("above", "below"):
                if self.zoom_job is not None and widget in self.zoom_job.get(side, ()):
                    self.zoom_job[side].remove(widget)
            self.reverting["hidden"].append(widget)
        elif widget is not None and widget.get_visible():
            widget.set_visible(False)
            self.reverting["hidden"].append(widget)

    def revert_asked(self):
        """Whether the pending revert's been asked of claude, and it's still to answer (one gone meanwhile won't)."""
        asked = self.reverting["asked"] if self.reverting is not None else None
        return asked is not None and asked[0] is self.proc and asked[1] in self.pending

    def say_now(self, text):
        """A note shown while a revert's pending, below what's still shown (add hides what comes after the reverted
        message)."""
        rev, self.reverting = self.reverting, None
        try:
            return self.note(text)
        finally:
            self.reverting = rev

    def show_waiting(self, widget):
        """A permission asked while a revert's pending: what it's in shown again, to be answered (the turn waits on
        it)."""
        rev, w = self.reverting, widget
        while rev is not None and w is not None and w is not self.transcript:
            if w in rev["hidden"]:
                rev["hidden"].remove(w)
                w.set_visible(True)
            w = w.get_parent()

    def cancel_revert(self, restore_draft=True):
        """Esc on a revert not sent: what it hid shown again, and the box as it was before."""
        rev, self.reverting = self.reverting, None
        self.held_after_revert = None  # (a held message's ↑ that made it: not sent after it either)
        if rev is None:
            return
        for widget in rev["hidden"]:
            widget.set_visible(True)
        if restore_draft:
            buf = self.input.get_buffer()
            buf.set_text(rev["draft"])
            buf.place_cursor(buf.get_end_iter())

    def commit_revert(self):
        """A revert sent (on_send): claude takes the message and everything after it back (a turn at it stopped, what it
        started since stopped too), then what's in the box is sent (reverted). A chat only viewed is resumed first, as
        sending in it does (send_text)."""
        rev = self.reverting
        turn = rev["turn"]
        self.reverting = None  # (what's said meanwhile shown, not hidden with what's being reverted: add)
        try:
            if self.resume_pending:
                self.begin_resume()
            if self.proc is None and self.remote is None and not self.runs_here():
                took = self.take_up()
                if took is False:  # (This Mac's host process isn't there yet: started, and sent once it is)
                    self.note(f"Starting {THIS_MACHINE}'s host process…")
                    self.held_after_revert = None  # (what's in the box is what goes, once it is)
                    self.local_server(lambda _r: self.reverting is rev and not self.revert_asked() and
                                      self.commit_revert())
                    return
                if took is None:
                    self.held_after_revert = None
                    return
            if self.proc is None:
                self.start_proc()
            if self.proc is None:
                self.held_after_revert = None
                self.note(wording.say("chat_turns.couldnt_revert", why=wording.say(
                    "chat_turns.not_running", program=self.program_name())))
                return
        finally:
            if rev["turn"].is_ancestor(self.transcript):
                self.reverting = rev
        if self.busy:  # (the turn it stops ends as a stopped one: not an error, h_result)
            self.proc.stop_asked = True
        rid = self.proc.control({"subtype": "rewind_conversation", "target_message_uuid": turn.uuid,
                                 "interrupt_if_running": True})
        self.pending[rid] = (lambda r: self.reverted(rev, r), lambda error: self.revert_refused(rev, error))
        rev["asked"] = (self.proc, rid)

    def revert_refused(self, rev, why):
        """claude wouldn't revert: said, and the revert's still not sent (Esc puts it back; Ctrl/⌘+Enter asks again);
        nor a held message whose ↑ asked for it (held_after_revert: ↑ again)."""
        self.held_after_revert = None
        if rev["asked"] is not None:
            rev["asked"][0].stop_asked = False  # (its turn not stopped by it: an error that ends it is said, h_result)
        rev["asked"] = None
        if self.reverting is rev:
            self.say_now(wording.say("chat_turns.couldnt_revert", why=why))

    def reverted(self, rev, response):
        """claude's answer to commit_revert: rewound, the message and all after it gone from the chat, and what's in the
        box sent; else why not, said."""
        response = response or {}
        if not response.get("rewound"):
            why = next((v for k, v in response.items() if k != "rewound" and isinstance(v, str)), None)
            return self.revert_refused(rev, why or f"{self.program_name()} said no")
        if self.reverting is not rev or not rev["turn"].is_ancestor(self.transcript):  # (another chat opened meanwhile)
            return
        self.reverting = None
        self.drop_from(rev["turn"])
        self.blocks, self.streamed_ids = {}, set()  # (a turn it stopped: what was half drawn is gone)
        if self.busy:  # (a turn it stopped, its ending not said yet: what's in the box sent once it is, h_result; or
            self.send_after_result = GLib.timeout_add(3000, self.send_reverted)  # in a moment, if it never is)
        else:
            self.send_reverted(waited=False)

    def send_reverted(self, waited=True):
        """After a revert's made (reverted): what's in the box sent. waited: once its stopped turn's ended, or a moment
        after (whichever's first: once)."""
        if waited and self.send_after_result is None:
            return False
        if self.send_after_result is not None:
            GLib.source_remove(self.send_after_result) if GLib.MainContext.default().find_source_by_id(
                self.send_after_result) else None
            self.send_after_result = None
        self.set_busy(False)
        held, self.held_after_revert = getattr(self, "held_after_revert", None), None
        if held is not None and held.get_parent() is self.queue_box:  # (a held message's ↑ made it: that sent)
            self.held_send(held)
            return False
        self.on_send()
        return False

    def drop_from(self, widget):
        """widget and everything drawn after it, out of the chat (an earlier chunk it's in: the rest of that, and the
        chunks after it, not drawn later either)."""
        w = widget
        while w is not None and w is not self.transcript:
            parent, after = w.get_parent(), w.get_next_sibling()
            while after is not None:
                nxt = after.get_next_sibling()
                parent.remove(after)
                discard(after)
                after = nxt
            w = parent
        widget.get_parent().remove(widget)
        discard(widget)
        if self.chunk_slots:
            gone = [slot for slot in self.chunk_slots if slot.get_parent() is None]
            self.chunk_slots = [slot for slot in self.chunk_slots if slot.get_parent() is not None]
            for slot in gone:  # (the chunks after it not drawn yet: their items counted as drawn, never to be)
                self.history.take(*slot.span)
            if gone:
                self.update_earlier_btn()
        kept = [job for job in self.fill_jobs if job.slot.get_parent() is not None]
        self.fill_jobs.clear()
        self.fill_jobs.extend(kept)

    INTERRUPT_WAIT = 5000  # ms for an interrupted turn to end, before its claude's stopped outright (stop_stuck)

    def interrupt(self):
        """The turn interrupted (Esc, the stop button): asked of its claude once (pressed again meanwhile: nothing
        more). Not over within INTERRUPT_WAIT, whatever runs the model, its claude's stopped (stop_stuck), so the chat's
        usable again; at once if its host says nothing's running there to interrupt. Nothing said yet: the turn's end
        says it, once (h_result; stop_stuck)."""
        if not self.proc:
            return
        self.queue_upto_host()  # (a send-now's: its host sends them as the turn ends)
        if self.interrupting is not None and self.interrupting[0] is self.proc:
            return  # (asked already: stopped outright if it doesn't end soon)
        self.drop_interrupting()
        proc = self.proc
        proc.stop_asked = True
        self.pending[proc.control({"subtype": "interrupt"})] = (lambda r: self.interrupt_answered(proc, r), None)
        self.interrupting = (proc, GLib.timeout_add(self.INTERRUPT_WAIT, lambda: self.stop_stuck(proc)))

    def interrupt_answered(self, proc, response):
        """An interrupt's answer: a host's, that its claude isn't running (or is being stopped), ends the turn now,
        as no result's coming; claude's own, that it's stopping, waits for the turn's end (or INTERRUPT_WAIT)."""
        if response.get("running") is False:
            self.drop_interrupting()
            self.stop_stuck(proc, ran=False)

    def drop_interrupting(self):
        """An interrupt's wait (interrupt) let go of: its turn's over, or its claude's gone."""
        it, self.interrupting = getattr(self, "interrupting", None), None
        if it is not None:
            GLib.source_remove(it[1])

    def stop_stuck(self, proc, ran=True):
        """An interrupted turn not over in time (its claude still starting on a resume, stuck, or gone without a
        word), or with nothing running to end it (ran False: its host said so): its claude stopped, the turn shown
        over. A host's session's, there, as Stop does it (its next message starts it again); this process's own,
        killed and started again on the conversation."""
        self.interrupting = None
        if proc is not self.proc or not self.busy:
            return False
        proc.stop_asked = "said"  # (its end said here: not again if its result comes after all, h_result)
        self.note(wording.say("interrupt.not_stopped", program=self.program_name(),
                              seconds=self.INTERRUPT_WAIT // 1000) if ran else
                  wording.say("interrupt.not_running", program=self.program_name()))
        self.cut_short()
        if self.remote is not None:
            if ran:
                self.remote.request({"op": "stop", "session": self.session_id})
            self.blocks.clear()
            for waiting in list(self.waiting_cards):  # (what it asked: nothing to answer now. A copy: answered()
                if isinstance(waiting, ToolCard):  # takes the card out of waiting_cards as it goes)
                    waiting.answered()
            self.waiting_cards.clear()
        else:
            self.restart_after_turn = False  # (started again now, on whatever sign-in there is, or the account picked)
            self.take_account()
            self.restart_proc(resume=True)
        self.end_turn(proc)
        return False

    def model_said(self, value):
        """A model's name as a note says it: as the model picker lists it (Qwen3 4B)."""
        return getattr(self, "model_names", {}).get(value) or self.model_dd.label()

    def on_model_changed(self, value):
        dropped = getattr(self, "move_after_turn", None) is not None
        self.move_after_turn = None  # (a model picked mid-turn: that one, not one picked before it)
        was = self.model
        if value == was:  # (the one it's on: one waiting for the turn's end dropped)
            if dropped:
                self.sync_providers()
            return
        self.outgrown = False  # (another model's window: as its usage says)
        if self.remote is not None and model_program(value) != model_program(was) and self.bot_name() and \
                baudelib.wire.takes(self.remote.info, "rotate"):
            if getattr(self, "busy", False) and self.proc is not None:  # (rotated once the turn's over)
                self.move_after_turn = (self.proc, value)
                self.sync_providers()
                return
            self.sync_providers()  # (its own model till the host's moved it)
            self.remote_rotate_to(value)  # (a Bot: its conversation rotated, and it goes on afresh on the one picked)
            return
        if self.remote is not None and model_program(value) != model_program(was):
            self.sync_providers()  # (this one goes on with its own model: the one picked is the new session's)
            self.remote_new_conversation(value)  # (a network session: a new one, on its server)
            return
        if getattr(self, "busy", False) and self.proc is not None and \
                (model_program(value) != model_program(was) or model_program(value) == "pi" or
                 modelslib.runs_on(value) != modelslib.runs_on(was)):
            # (it'd restart pi or claude, here or in the host process, which would stop the reply: once the turn's
            # over, as a provider picked)
            self.move_after_turn = (self.proc, value)
            self.sync_providers()
            return
        self.model = value
        self.sync_providers()
        self.sync_effort_choices()
        self.sync_local()
        if self.remote is None and model_program(value) != model_program(was):  # (Claude's to Apple's: another program)
            self.note(wording.say("chat_header.model_note_new", name=self.model_said(value)))
            self.restart_proc(resume=False)
            return
        if self.remote is None and value != was and \
                (model_program(value) == "pi" or modelslib.runs_on(value) != modelslib.runs_on(was)):
            # (pi-claude serves one model, and one on another host runs there: pi's session goes on, kept on the chat's
            # host; a network session's server starts it again on the new one itself)
            self.note(wording.say("chat_header.model_note", name=self.model_said(value)))
            self.restart_proc(resume=True)
            return
        if self.proc:
            self.proc.control({"subtype": "set_model", "model": value})
            self.note(wording.say("chat_header.model_note", name=self.model_said(value)))
            self.refresh_effort()  # the CLI may change or drop the effort with the model
            self.refresh_context()  # different models have different windows
        else:
            self.exhausted.set(False)  # (no claude to ask: the one started next says)

    def on_effort_changed(self, value):
        if model_program(self.model) == "pi":
            self.local_effort = value
        else:
            self.effort = value
        if self.proc:
            self.proc.control({"subtype": "apply_flag_settings", "settings": {"effortLevel": value}})
            self.note(wording.say("chat_header.effort_note", label=self.effort_dd.label()))

    def sync_effort_choices(self):
        """Offer the current model's effort levels; grey the picker out if it has none."""
        info = self.models_info.get(self.model_dd.value() or "", {})
        levels = info.get("supportedEffortLevels") if info.get("supportsEffort") else None
        if levels:
            self.effort_dd.set_choices([(l, EFFORT_LABELS.get(l, l.title())) for l in levels])
            want = self.local_effort if model_program(self.model) == "pi" else self.effort
            if want in levels:
                self.effort_dd.select(want)
            elif info.get("defaultEffort") in levels:
                self.effort_dd.select(info["defaultEffort"])
            self.effort_dd.set_tooltip_text(wording.say("chat_header.effort_tip"))
        else:
            self.effort_dd.set_choices([("", wording.say("meta_settings.effort"))])
            self.effort_dd.select("")
            self.effort_dd.set_tooltip_text(wording.say("meta_settings.no_effort", model=self.model_dd.label() or
                                                        wording.say("chat_header.this_model")))
        self.effort_dd.set_sensitive(bool(levels))

    def sync_effort(self, effort):
        if effort and effort in self.effort_dd.values:
            if model_program(self.model) == "pi":
                self.local_effort = effort
            else:
                self.effort = effort
            self.effort_dd.select(effort)

    def on_mode_changed(self, value):
        self.mode = value
        if self.proc:
            self.request({"subtype": "set_permission_mode", "mode": value},
                         lambda _r: self.mode_taken(value), lambda error: self.mode_refused(value, error))

    def mode_taken(self, value):
        self.proc.mode = value  # (what it's in: where a mode it refuses next goes back to)
        self.note(wording.say("chat_header.mode_note", mode=dict(MODES).get(value, value)))

    def mode_refused(self, value, error):
        """claude said no to a mode picked (Bypass permissions, when it wasn't started able to bypass them): the
        picker and self.mode (what the chat's claude starts in next) back on the mode it's still in, unless another's
        been picked since, and why said."""
        was = getattr(self.proc, "mode", None)
        if self.mode == value and was:
            self.sync_mode(was)
        why = error.split(" because ", 1)[-1].rstrip(".") if error else wording.say("chat_header.claude_said_no")
        mode = dict(MODES).get(value, value)
        self.note(wording.say("chat_header.mode_refused_still", mode=mode, why=why, was=dict(MODES).get(was, was))
                  if was else wording.say("chat_header.mode_refused", mode=mode, why=why))

    # ---- each chat's own model: picked in one, it isn't the others'. Kept by session id as a turn ends (chat_models);
    # a chat opened is put back on it, or on its transcript's last reply's model (one Baude hasn't kept)
    CHAT_MODELS = 500

    def keep_chat_model(self):
        if not (self.session_id and self.model) or self.resume_pending or self.model == "default":
            return
        kept = load_settings().get("chat_models") or {}
        if kept.get(self.session_id) != self.model:
            chats = {k: v for k, v in kept.items() if k != self.session_id} | {self.session_id: self.model}
            save_settings(chat_models=dict(list(chats.items())[-self.CHAT_MODELS:]))
        if model_program(self.model) == "claude":
            self.claude_model = self.model  # (where a chat goes that has no model of its own: apply_chat_model)

    def apply_chat_model(self, info):
        """Put the picker on the model the chat info names was on. A Claude transcript with no model Baude knows goes
        on Claude's last used (or claude's own default): never Apple's or one on this Mac, which can't carry it on. A
        network session's is the one its server says it's on."""
        if info.get("remote") is not None and info.get("model"):
            self.model = info["model"]
            self.sync_providers()  # (named as its server lists it once it's said: sync_models)
            self.sync_effort_choices()
            self.sync_local()
            return
        kept = (load_settings().get("chat_models") or {}).get(info["id"])
        want = (kept if kept != "default" else None) or transcript_model(info["path"])
        models = {v: m for v, m in getattr(self, "models_info", {}).items() if v != "default"}  # (an alias, not a model)
        model = next((v for v, m in models.items() if want in (v, m.get("resolvedModel"))), None) or \
            (modelslib.claude_match(want, list(models.values())) or {}).get("value") if want else None
        if model is None and want and model_program(want) == "claude":  # ("claude-opus-5-5" for the picker's "opus")
            model = next((v for v in models if model_program(v) == "claude" and v in want), None)
        if model is None and model_program(self.model) != "claude":
            model = getattr(self, "claude_model", None)
        elif model is None:
            return
        self.model = model
        self.sync_providers()
        self.sync_effort_choices()
        self.sync_local()

    def sync_local(self):
        """Show the permission and effort controls available for this backend."""
        program = model_program(self.model)
        self.effort_dd.set_visible(program == "claude" or self.effort_dd.get_sensitive())
        self.mode_dd.set_visible(program in ("claude", "codex") or self.host_lists("set_permission_mode"))
        self.sync_mode(self.mode)

    def host_lists(self, op):
        """Does this chat's host take op as claude's own modes and always-allow have it, whatever its model's program,
        as it says (its hello's ops: a phone's agents)? Not one that lists no ops: that's said by its program."""
        ops = None if self.remote is None else self.remote.info.get("ops")
        return isinstance(ops, list) and op in ops

    def sync_models(self, models):
        """Fill the model picker from initialize's list, labelled with real names ("Opus 5.5"), and the models this
        machine runs besides (modelslib.runnable_models: Apple's, MLX's, Ollama's, an endpoint's), with every other
        host's in the mesh (sync_providers). apple-claude's and pi-claude's lists are their one model: Claude's, as claude last
        listed them, stay.

        The list's "default" entry is an alias, not a model: it is dropped, and when no model
        was chosen the entry it resolves to is selected instead.
        """
        models = [m for m in models or [] if m.get("value")]
        if self.remote is not None:  # (a network session: what its server can run, not this machine)
            if not any(model_program(m["value"]) == "claude" for m in models):
                models = [m for m in getattr(self, "claude_models", []) if model_program(m["value"]) == "claude"] + models
        elif models and all(model_program(m["value"]) != "claude" for m in models):
            models = getattr(self, "claude_models", [])
        else:
            self.claude_models = models
            meta.listed(models, seed=True)  # (what this machine's claude runs: Meta's Intelligence, till Meta's own lists)
        if self.remote is None:
            models = [m for m in models if model_program(m["value"]) == "claude"] + \
                modelslib.runnable_models(wait=self.PROVIDERS_WAIT)
        self.models_info = {m["value"]: m for m in models}
        self.merge_codex_models()
        default = next((m for m in models if m["value"] == "default"), {})
        models = [m for m in models if m["value"] != "default"]
        want = self.model or default.get("resolvedModel")
        hit = next((m["value"] for m in models if want in (m["value"], m.get("resolvedModel"))), None) or \
            (modelslib.claude_match(want, models) or {}).get("value", want) if want else None
        self.model_shown = hit  # (what the picker shows while claude's own default's the model: self.model None)
        self.sync_providers(hit)
        self.sync_effort_choices()
        self.sync_local()

    # ---- inference providers: where the models besides Claude's run (Ollama, MLX, Apple's, an OpenAI-compatible
    # server), by host: the chat's host's, and every other member's of the mesh (network().providers()). The model
    # picker lists every model there is: Claude's (claude's: not a provider's, bound to where it runs), then every
    # other, the chat's host's first, each once however many hosts (or runtimes) have it (providers.alike: the same
    # one). A model picked runs on the first provider that has it (the chat's host's, then the mesh's in its order),
    # unless the chat's on another of its copies already. Left of the model picker, the provider picker lists where the
    # chat's model runs: each provider that has it, by host, greyed with one; picking another moves the chat's model
    # there (mid-turn, once the turn's over). It's gone while the chat's on Claude's. Neither picker changes the
    # chat's host: the chat stays where it is, with its files, and another host's model runs for it through the mesh's
    # proxy where it serves an API, else as an agent there whose tools are the chat's host's (providers.elsewhere).
    # Where a chat lives is picked as it's made (New Session's host), or by Meta. A model's name is the same wherever
    # it runs: its provider and host are the provider picker's to say
    PROVIDERS_WAIT = 0.5  # (s a chat waits for this machine's providers the first time: the rest come as they're found)

    def chat_host(self):
        """The host this chat lives on, its files and its agent's tools, as New session's hosts key it: this machine (its
        own claude, or its host process), or the server's."""
        return HERE_HOST if self.remote is None or baudelib.client.is_here(self.remote.info) else self.remote.key

    def provider_choices(self):
        """[(key, host name, provider name, models)], a host's together: the providers of the chat's host (this
        machine's, as they're found here, whether its own claude or its host process runs the chat; another's, the
        models it lists: models_info), then every other host's in the mesh (this machine's too, for a chat on another),
        each model as the chat runs it where it is: through the mesh's proxy, or as an agent there whose tools are the
        chat's host's (providers.elsewhere). Another host's Apple model is one too; the chat's host's own is in
        models_info (providers.group leaves it out)."""
        own, out = self.chat_host(), []

        def add(host, name, models):
            for pid, pname, ms in providerslib.group(models):
                out.append((f"{host}|{pid}", name, pname, ms))
        if own == HERE_HOST:
            add(own, THIS_MACHINE, modelslib.runnable_models(wait=False))
            theirs = set()
        else:  # (its own, as it lists them: not those of other hosts' it runs, which are kept beside them)
            add(own, remote_name(self.remote), [m for m in self.models_info.values() if m["value"] != "default" and
                                                not modelslib.proxied(m["value"]) and
                                                not m["value"].startswith("openai/mesh-")])
            theirs = {self.remote.info.get("id"), own}
        if self.runs_here():  # (Baude's own claude runs it: no host process here to reach another host's from)
            return out
        for p in network().providers():
            host = p.get("host") or {}
            if (host.get("here") if own == HERE_HOST else host.get("id") in theirs) or not p.get("models") or \
                    not baudelib.wire.is_id(host.get("id")):
                continue
            hname, pname = THIS_MACHINE if host.get("here") else host.get("name") or str(host["id"])[:8], \
                p.get("name") or p.get("id")
            pid, models = providerslib.elsewhere(p, host["id"])
            out.append((f"{host['id']}|{p['id']}", hname, pname,
                        [m | {"providerName": f"{hname} / {pname}"} for m in models]))
        return out

    def merge_codex_models(self):
        """Use the chat host's current Codex model catalog, independent of Claude initialization."""
        host = self.remote or local_host()
        if host is None or "models" not in host.info:
            return False
        catalog = {m["value"]: m for m in host.info["models"] if model_program(m["value"]) == "codex"}
        old = {v: m for v, m in self.models_info.items() if model_program(v) == "codex"}
        if catalog == old:
            return False
        for value in old:
            del self.models_info[value]
        self.models_info.update(catalog)
        return True

    def providers_listed(self, found=None):
        """What the provider picker lists (found: provider_choices'), to tell whether it's changed."""
        return [(k, host, label, [m["value"] for m in ms]) for k, host, label, ms in
                (self.provider_choices() if found is None else found)]

    def sync_providers(self, shown=None):
        """The model picker and the provider picker as the chat's host and the mesh have them now, on shown (the chat's
        model, if not given). The model picker: Claude's models, then every other, one of each kind (providers.alike),
        the chat's host's first: shown where it's of a kind, else the first provider's copy (the one a pick runs on);
        shown last if it's none of these. The provider picker: where shown runs, each provider that has it by host
        (alike_keys: its copy there), on shown's (or the copy a move picked mid-turn waits to go to); greyed with one,
        hidden on Claude's."""
        found = self.provider_choices()
        self.providers_seen = self.providers_listed(found)
        self.provider_index, named = {}, {}  # (each model's provider, the first that lists it; each provider's names)
        for k, host, label, ms in found:
            named[k] = (host, label)
            for m in ms:
                self.provider_index.setdefault(m["value"], k)
                self.models_info.setdefault(m["value"], m)  # (as it's run: its effort levels, its name)
                if m.get("digest") and self.models_info[m["value"]].get("digest") != m["digest"]:  # (pulled again)
                    self.models_info[m["value"]] = {**self.models_info[m["value"]], "digest": m["digest"]}
        pending = getattr(self, "move_after_turn", None)  # (picked mid-turn: where the chat goes once it's over)
        shown = (pending[1] if pending and pending[0] is self.proc else None) or shown or self.model or \
            getattr(self, "model_shown", None)

        if shown == "codex/default":
            default = self.models_info.get(shown, {}).get("resolvedModel")
            shown = f"codex/{default}" if default else None

        def pid_of(m):
            return m.get("provider") or providerslib.provider_of(m["value"])

        def info(value):
            return self.models_info.get(value) or \
                {"value": value, "displayName": providerslib.model_name(value, list(self.models_info.values()))}
        pid = pid_of(info(shown)) if shown else None
        # (every model with a provider: the chat's host's Apple model, then the providers' in their order; the chat's
        # own last if its host's gone, the same one as another host's where it is)
        offered, seen = [], set()
        for m in [m for m in self.models_info.values() if m["value"] not in self.provider_index and
                  pid_of(m) == "apple"] + [m for *_x, ms in found for m in ms]:
            if m["value"] not in seen:
                seen.add(m["value"])
                offered.append(self.models_info.get(m["value"], m))
        if pid is not None and shown not in seen:
            offered.append(info(shown))
        kinds = providerslib.alike(offered, [shown] if shown else [])
        own = [m for m in self.models_info.values() if m["value"] not in ("default", "codex/default") and
               m["value"] not in seen and pid_of(m) is None]  # (Claude's as claude lists them, each its own row)
        same = modelslib.claude_match(shown, own) if shown and pid is None else None  # (the chat's model said
        if shown and pid is None and same is None:  # another way, claude-opus-5-5, its server's say: the row it is;
            own.append(info(shown))  # else its own row, last)
        pick = same["value"] if same else shown
        listed = own + [lead for lead, _ms in kinds]
        others = [m for m in listed if pid_of(m) is not None]  # (one of each kind: told apart where two share a name)
        names = [(m["value"], providerslib.told_apart(m, others) if pid_of(m) is not None else
                  m.get("displayName") or providerslib.model_name(m["value"])) for m in listed]
        self.model_names = dict(names) | ({shown: dict(names)[pick]} if same else {})
        known = list(self.models_info.values())
        self.model_dd.set_choices(names or MODELS, {v: providerslib.model_slug(v, known) for v, _n in names})
        if pick:
            self.model_dd.select(pick)

        here = THIS_MACHINE if self.chat_host() == HERE_HOST else remote_name(self.remote)
        copies = next((ms for _lead, ms in kinds if any(m["value"] == shown for m in ms)), []) if pid else []
        hosts, picked, self.alike_keys = {}, None, {}
        for m in copies:
            k = self.provider_index.get(m["value"])
            if k is None:  # (the chat's host's Apple model, or one whose host's gone: by what its value says)
                there, label = providerslib.where(pid_of(m))
                agent = modelslib.proxied(m["value"])
                k, host = f"{agent[0] if agent else there or self.chat_host()}|{pid_of(m)}", there or here
            else:
                host, label = named[k]
            if k in self.alike_keys:
                continue
            hosts.setdefault(k.split("|", 1)[0], (host, []))[1].append((k, label))
            self.alike_keys[k] = m["value"]
            if m["value"] == shown:
                picked = k
        one = len(self.alike_keys) == 1
        self.provider_dd.set_choices(list(hosts.values()))
        self.provider_dd.select(picked)
        self.provider_dd.set_sensitive(not one)  # (one provider has it: greyed, there's no other to pick)
        self.provider_dd.set_visible(bool(self.alike_keys))  # (Claude's: none)
        self.provider_dd.set_tooltip_text(wording.say("chat_header.provider_tip_only") if one else
                                          wording.say("chat_header.provider_tip"))
        if not self.alike_keys:
            self.sync_accounts(shown)
        self.ctx_box.window_changed()  # (no session's said what it's used yet: this model's window)

    # ---- a chat on Claude's or Codex's model whose host keeps a subscription account for it (with the CLI's own
    # sign-in, two subscriptions): each is a provider in the provider picker, by its name alone (an account is its
    # host's: never another's), the CLI's own sign-in first, the chat's own picked. Picking another goes on with the chat under it (accounts' use: its agent started again, resuming it,
    # once its turn's over; a Claude Desktop chat's own claude, here, started again under it)
    ACCOUNTS_S = 30  # (s a host's accounts, as it last listed them, are taken as they are)
    ACCOUNT = "account|"  # (a provider picker key that's an account: its id after it)
    ACCOUNT_GONE = "account-gone"  # (the provider picker's key for an account since removed that the chat's on)

    def host_accounts(self):
        """The subscription accounts of the chat's host, as it last listed them ([] till it has), listed again once
        they're ACCOUNTS_S old; the pickers synced as an answer changes them."""
        host = self.remote
        if host is None:
            from baudelib import accounts
            try:
                return accounts.listing()
            except OSError:
                return []
        said = getattr(host, "accounts_said", None)
        if (said is None or GLib.get_monotonic_time() / 1e6 - said[0] >= self.ACCOUNTS_S) and \
                not getattr(host, "accounts_asking", False):
            host.accounts_asking = True

            def answered(reply, host=host, was=said):
                host.accounts_asking = False  # (answered, refused, or the connection gone: asked again next time)
                if reply.get("op") != "accounts":
                    return
                listed = [a for a in reply.get("accounts") or [] if isinstance(a, dict) and a.get("id")]
                host.accounts_said = (GLib.get_monotonic_time() / 1e6, listed)
                if (was or (0, []))[1] != listed and self.remote is host and self.get_application() is not None:
                    self.sync_providers()
            host.request({"op": "accounts", "what": "list"}, answered)
        return said[1] if said else []

    def sync_accounts(self, shown):
        """The provider picker as the chat's subscriptions, the CLI's own sign-in and each account: shown (its model)
        Claude's or Codex's, and its host keeping an account for it; else left as sync_providers left it. Not for a Claude Desktop chat its
        host only lists (nothing of it runs there to move). An account since removed that the chat's on: listed as
        that, picked, till another's picked."""
        program = model_program(shown or self.model)
        if program not in ("claude", "codex") or not self.session_id:
            return
        if self.remote is not None and (self.remote.sessions.get(self.session_id) or {}).get("taken_up") is False:
            return
        mine = [a for a in self.host_accounts() if a.get("provider") == program]
        if not mine:  # (the CLI's own sign-in alone: nothing else to pick)
            return
        on = (self.chat_account_info() or {}).get("id") or ""
        choices = [(self.ACCOUNT, wording.say("subscriptions.cli_default"))] + \
            [(self.ACCOUNT + a["id"], a["name"]) for a in mine]
        if self.chat_account_gone():
            on = self.ACCOUNT_GONE
            choices.append((on, wording.say("subscriptions.removed_account")))
        self.provider_dd.set_choices([(None, choices)])
        self.provider_dd.select(on if on == self.ACCOUNT_GONE else self.ACCOUNT + on)
        self.provider_dd.set_sensitive(True)
        self.provider_dd.set_visible(True)
        self.provider_dd.set_tooltip_text(wording.say("subscriptions.account_tip"))

    def chat_account_gone(self):
        """Is the chat on a subscription account since removed from its host?"""
        if self.remote is not None:
            return bool((self.remote.sessions.get(self.session_id) or {}).get("account_gone"))
        import sqlite3
        from baudelib import accounts
        try:
            profile = accounts.claude_session_profile(self.session_id)
            return bool(profile) and accounts.account(profile) is None
        except (OSError, sqlite3.Error):
            return False

    def use_account(self, profile):
        """The chat goes on under another of its host's accounts (profile; "": the CLI's own sign-in)."""
        name = next((a["name"] for a in self.host_accounts() if a["id"] == profile), None) or \
            wording.say("subscriptions.cli_default")
        if self.remote is None:  # (a Claude Desktop chat's own claude, here: started again under it, resuming it,
            self.account_to = profile  # once its turn's over)
            if getattr(self, "busy", False) and self.proc is not None:
                self.restart_after_turn = True
                self.note(wording.say("subscriptions.account_note", name=name))
            elif self.take_account():
                self.note(wording.say("subscriptions.account_note", name=name))
                self.restart_proc(resume=True)
            self.sync_providers()
            return

        def answered(reply):
            if reply.get("error") or reply.get("op") == "error":
                self.note(reply.get("error") or wording.say("subscriptions.gone"))
            else:
                self.note(wording.say("subscriptions.account_note", name=name))
                listed = self.remote.sessions.get(self.session_id) if self.remote is not None else None
                if isinstance(listed, dict):  # (as its host lists it next: taken now)
                    listed["account"] = next((a for a in self.host_accounts() if a["id"] == profile), None)
                    listed["account_gone"] = False
            self.sync_providers()
        self.remote.request({"op": "accounts", "what": "use", "session": self.session_id, "id": profile}, answered)

    def take_account(self):
        """A Claude Desktop chat run here moved onto the account picked for it (account_to), with no claude running
        in it: its transcript carried to that account's projects folder where it isn't the one it's in
        (accounts.carry_chat), and the account kept as its own and as the one a new chat here takes. Whether it was (a
        copy that fails: said, the chat left on the one it's on)."""
        profile, self.account_to = getattr(self, "account_to", None), None
        if profile is None or not self.session_id:
            return False
        from baudelib import accounts, transcripts
        try:
            accounts.carry_chat("claude", self.session_id, transcripts.session_file(self.session_id),
                                accounts.claude_session_profile(self.session_id), profile)
            accounts.use_claude_session(self.session_id, profile)
            accounts.select("claude", profile)  # (as a host's use keeps it: its drafts take it as they're written in)
        except (OSError, ValueError) as e:
            self.note(str(e))
            return False
        return True

    def on_provider_changed(self, key):
        """Another provider of the chat's model picked (alike_keys): the chat's model is its copy there, as a model
        picked is; mid-turn, once the turn's over. Its own picked back: no move, one waiting dropped. An account
        picked (sync_accounts): the chat goes on under it."""
        if key == self.ACCOUNT_GONE:
            return
        if key.startswith(self.ACCOUNT):
            if key[len(self.ACCOUNT):] != ((self.chat_account_info() or {}).get("id") or "") or \
                    self.chat_account_gone():
                self.use_account(key[len(self.ACCOUNT):])
            return
        self.move_after_turn = None
        value = getattr(self, "alike_keys", {}).get(key)
        if value is not None and value != self.model:
            self.move_to(value)
        else:
            self.sync_providers()

    def move_to(self, value):
        """The chat's model to its copy on another host (a provider picked): now, or mid-turn once the turn's over
        (moving restarts pi or claude, which would stop the reply), if its claude's still the one running it."""
        if getattr(self, "busy", False) and self.proc is not None:
            self.move_after_turn = (self.proc, value)
            self.sync_providers()
        else:
            self.on_model_changed(value)

    def providers_found(self):
        """This machine's providers changed (found again: Ollama started, a model pulled): the pickers as they are now."""
        on_main(lambda: self.get_application() is not None and not getattr(self, "let_go_done", False) and
                self.resync_models())

    def resync_models(self):
        if self.remote is None and getattr(self, "claude_models", None) is not None:
            self.sync_models(self.claude_models)  # (an own claude's: the models this machine runs, again)
        else:
            self.sync_providers()

    def default_mode(self):
        """Pick the backend's starting mode without inheriting the previous chat's mode."""
        self.sync_mode("default" if model_program(self.model) == "codex" else "auto")

    def sync_mode(self, mode):
        program = model_program(self.model)
        choices = modelslib.CODEX_MODES if program == "codex" else MODES
        values = [value for value, _label in choices]
        if self.mode_dd.values != values:
            self.mode_dd.set_choices(choices)
        if mode and program == "codex":
            mode = modelslib.codex_mode(mode)
        elif mode and program != "claude" and not self.host_lists("set_permission_mode"):
            mode = "default"
        if mode:
            self.mode = mode
            self.mode_dd.select(mode)  # (quietly: not asked of claude again)
            if self.proc is not None:
                self.proc.mode = mode  # (what claude says it's in)

    def on_launcher_toggled(self, action, value):
        try:
            if value.get_boolean():
                widgets.toast(self, f"added to {LAUNCHER.where}: {LAUNCHER.install()}")
            else:
                removed = LAUNCHER.uninstall()
                widgets.toast(self, f"removed from {LAUNCHER.where}" if removed else "launcher was not installed")
        except (OSError, GLib.Error) as e:
            widgets.toast(self, f"launcher: {e}")
        action.set_state(GLib.Variant.new_boolean(LAUNCHER.installed()))

    @staticmethod
    def norm_dir(path):
        """The one spelling of a folder used everywhere: absolute, ~ expanded, symlinks resolved
        (claude reports its cwd resolved, so this is what it actually runs in)."""
        return os.path.realpath(os.path.expanduser(path))

    def set_cwd(self, path):
        """Make `path` the working folder: the next claude process runs in it (self.run_cwd), and so do relative
        paths. self.cwd is the folder it belongs to (main_folder: a worktree's is its repository's main checkout),
        which the status line, the sidebar's group, new sessions and Signal's glossary follow. Nothing else may
        assign either."""
        ran, self.run_cwd = self.run_cwd, self.norm_dir(path)
        self.cwd, tree = main_folder(self.run_cwd)
        MarkdownView.base_dir = self.run_cwd
        checkout = tree if tree and os.path.isdir(tree) else None  # (a worktree's chat works in it)
        moved = getattr(self, "signal", None) and \
            self.signal.place.key != signal_place(self.ref_checkout or checkout or self.cwd).key
        if moved or self.run_cwd != ran and checkout != self.ref_checkout:
            self.ref_checkout = checkout  # (another folder or checkout: its chat's checkout is looked at again)
            if getattr(self, "signal", None) and not getattr(self, "switching_chat", False):
                self.set_signal(save=False)  # (a chat being opened applies its own switches once it's in)
        if hasattr(self, "panel"):
            self.panel.git_page.chat_changed()
        if hasattr(self, "status"):
            self.update_status()
        if hasattr(self, "sidebar") and not getattr(self, "switching_chat", False):  # (a chat switched to: once it's in)
            self.sidebar.fill()  # this folder's group moves to the top

    def cwd_dialog(self):
        dlg = Gtk.FileDialog(title="Working directory", modal=True)
        dlg.set_initial_folder(Gio.File.new_for_path(self.cwd))  # open where we are
        return dlg

    def pick_folder(self):
        """Open folder…: a new session in the folder you pick."""
        dlg = self.cwd_dialog()
        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            self.switch_to(lambda: self.start_in_folder(f.get_path()))
        dlg.select_folder(self, None, done)

    def start_in_folder(self, path):
        """A new chat in a folder (Open folder…, a folder's +): from a new chat not written in yet that was started
        in a group, in that group still; else in the group New session would put it in (new_group)."""
        if not os.path.isdir(path):
            self.note(wording.say("chat_notes.not_a_folder", path=path))
            return
        draft = self.session_id is None or self.remote is not None and \
            (self.remote.sessions.get(self.session_id) or {}).get("draft")
        group = self.draft_group if draft and getattr(self, "draft_group", "") else self.sidebar.new_group(HERE_HOST)
        self.sidebar.put_in(group)
        self.new_session(path, group=group)

    def change_cwd(self, path):
        path = self.norm_dir(os.path.join(self.run_cwd, path))  # relative to the folder claude runs in
        if not os.path.isdir(path):
            self.note(wording.say("chat_notes.not_a_folder", path=path))
            return
        if self.busy:
            self.note(wording.say("slash_commands.cd_busy"))
            return
        if path == self.run_cwd and (self.remote is None or self.remote_folder() in (None, path)):
            return  # (a host's chat whose folder there isn't this window's, a worktree that's gone, say: moved still)
        if self.remote is not None and not self.remote.mine:  # (another machine's folders: not this one's to pick)
            self.note(wording.say("slash_commands.cd_elsewhere", host=remote_name(self.remote)))
            return
        if self.remote is not None:  # (this machine's host process runs it: there, from its next message on)
            reply = self.remote.call({"op": "set_cwd", "session": self.session_id, "cwd": path}, timeout=10)
            if reply.get("op") != "cwd_set":
                self.note(wording.say("chat_notes.folder_not_changed", why=baudelib.wire.said(reply)))
                return
            if self.session_id in self.remote.sessions:  # (as it's listed, before it next says so)
                self.remote.sessions[self.session_id] = {**self.remote.sessions[self.session_id], "cwd": reply["cwd"]}
        self.set_cwd(path)
        keep = self.session_id is not None
        self.note(wording.say("chat_notes.folder_kept" if keep else "chat_notes.folder", folder=self.short_cwd()))
        self.restart_proc(resume=True)

    # ---- protocol
    def on_msg(self, proc, msg):
        if proc is not self.proc:
            return False  # leftover output from a process we already replaced
        t = msg.get("type")
        if t in ("stream_event", "assistant", "result") and not msg.get("parent_tool_use_id"):
            self.show_retry(None)  # (the model's answering again, or the turn's over)
        if t in ("stream_event", "assistant") and not msg.get("parent_tool_use_id") and not self.busy:
            self.set_busy(True)  # the CLI started a turn by itself
        handler = getattr(self, "h_" + str(t).lstrip("_"), None)
        if handler:
            handler(msg)
        return False

    def h_note(self, msg):  # (the host process's word on the chat: its worktree made again, say)
        self.note(msg.get("text") or "")

    def h_said(self, msg):
        """A message the chat was sent from elsewhere, as its agent's given it (its host's word: claude never says one
        back), drawn as its transcript's record of it is: as who it's from if not you (sender), as its host says."""
        if self.remote is not None and self.session_id and msg.get("sender") is not None:
            self.remote.heard_sender(self.session_id, msg.get("uuid"), msg["sender"])
        rec = {"type": "user", "uuid": msg.get("uuid"), "message": msg.get("message") or {}}
        if (item := history_item(json.dumps(rec))) is not None:
            self.render_history([item], final=False)

    def h_stderr(self, msg):
        own = model_program(self.model) != "claude"  # (apple-claude and pi-claude say things there for the chat)
        self.note(msg["text"] if own else wording.say("chat_notes.stderr", text=msg["text"]))

    def h_system(self, msg):
        st = msg.get("subtype")
        if st == "init":
            self.init_info = msg  # (its model and tools, as claude says them: report)
            TOOL_NAMES.update(t for t in msg.get("tools") or [] if isinstance(t, str))  # (what Signal can name)
            toolnames.learn_servers(msg.get("mcp_servers"))  # (its MCP servers' names, as they're configured)
        if st == "init" and self.resume_pending:
            return  # the fresh helper process's own id: the session we show is the viewed one
        if st == "init" and self.remote is not None:  # (a network session is its server's id, whatever claude's is)
            self.update_status("")
            return
        if st == "init":
            new_id = msg.get("session_id") != self.session_id
            if new_id and self.session_id is None:  # (a new chat's notification, under its id now; what it's allowed)
                widgets.withdraw_notification(self.chat_key(None))
                self.allowed_in(msg.get("session_id")).update(self.always_allowed.pop(None, ()))
            self.session_id = msg.get("session_id")
            telemetryview.look(self)
            if new_id:
                if self.session_id and (self.signal_pending is not None or self.session_id not in
                                        (load_settings().get("signal_chats") or {})):
                    # (its switches as they are now, so the defaults changing later doesn't change them)
                    self.keep_chat_signal(self.session_id, *(self.signal_pending or (self.signal_run.get_active(),
                                                                                     self.signal_show.get_active())))
                    self.signal_pending = None
                self.sidebar.fill()
            if msg.get("cwd") and self.norm_dir(msg["cwd"]) != self.run_cwd:
                self.note(wording.say("chat_notes.running_in", folder=msg["cwd"]))
                self.set_cwd(msg["cwd"])  # what claude really uses wins
            self.update_status(f"{len(msg.get('tools', []))} tools")
        elif st == "codex_turn_started":
            if not self.busy:
                self.set_busy(True)
        elif st in ("codex_context", "baude_context"):  # (baude_context: pi-claude's, MLX's count of what it's sent)
            self.remember_context(self.model, msg["usage"])
            self.show_context(msg["usage"])
        elif st == "api_retry" and not msg.get("parent_tool_use_id"):  # (a subagent's: its own to wait out)
            self.show_retry(msg)
        elif st == "compact_boundary":
            self.on_compaction(msg)
            self.refresh_context()
        elif st == "status":
            self.sync_mode(msg.get("permissionMode"))  # e.g. leaving plan mode
            self.on_compaction(msg)
            if msg.get("status") == "requesting" and not self.busy:
                self.set_busy(True)  # a turn the CLI started itself (e.g. a background task finished)
        elif st == "session_title_changed":
            self.show_title(msg.get("title"))
            self.sidebar.refresh()  # the list shows the new title
        elif st in ("task_started", "task_progress", "task_updated", "task_notification"):
            self.on_task_event(st, msg)
        elif st == "background_tasks_changed":
            self.on_tasks_changed(msg.get("tasks") or [])
        elif st == "commands_changed":
            self.completer.set_commands(msg.get("commands") or [])
        elif st == "vcs_state_changed":
            self.repository_changed(msg)
        elif st == "code_change_published":
            self.panel.git_page.chat_changed()  # (its pull request, on the Git page)
        elif said := wording.say("claude_events.said", event=str(st), level=str(msg.get("level") or ""),
                                 text=next((t for t in (msg.get("content"), msg.get("text"))
                                            if isinstance(t, str) and t.strip()), "")):
            self.note(said)  # (claude_events: what's said of its other events, a warning or error's own message;
            # nothing, for the rest)

    def repository_changed(self, msg):
        """claude committed, pushed, merged or rebased (vcs_state_changed): said in the chat, with a link to the Git
        page (the branch, if it said one; the folder it was in, if not the chat's), which reads the history again."""
        branch = msg.get("branch") if isinstance(msg.get("branch"), str) else ""
        said = wording.say("claude_events.repository", kind=str(msg.get("kind")), branch=branch)
        link = GIT_SCHEME + urllib.parse.quote(branch)
        cwd = msg.get("cwd") if isinstance(msg.get("cwd"), str) and msg.get("cwd") else None
        if cwd and self.norm_dir(cwd) not in (self.run_cwd, self.cwd, self.ref_checkout):
            link += "?" + urllib.parse.quote(self.norm_dir(cwd), safe="")
        self.note(said, link=(link, wording.say("signal.show_in_git")))
        self.panel.git_page.chat_changed()

    # ---- compaction (CompactMarks): marked in the chat, at its end
    def compact_add(self, mark):
        return self.add(mark)

    def compact_here(self, mark):
        return mark.is_ancestor(self.transcript)

    def compact_started(self):
        if not self.busy:
            self.set_busy(True)

    # ---- session title (click it in the header to rename)
    def show_title(self, title):
        """The chat's title in the header and the window's title; a Bot's name instead, for a Bot (it has no other)."""
        self.session_title = title
        shown = self.bot_name() or title
        self.title_label.set_text(shown or wording.say("sidebar.new_session"))
        self.set_title(f"Baude — {shown}" if shown else "Baude")

    def bot_name(self):
        """The open chat's Bot's name, as its host lists it; None if it isn't a Bot."""
        r = getattr(self, "remote", None)
        listed = r.sessions.get(self.session_id) if r is not None else None
        return ((listed or {}).get("bot") or {}).get("name") or None
        # (not the sidebar: a chat being switched to fills it once it's in; a rename re-reads it)

    # ---- background tasks (commands and subagents the CLI runs after a turn ends)
    def on_task_event(self, kind, msg):
        tid = msg.get("task_id")
        if not tid:
            return
        task = self.tasks.setdefault(tid, {"task_id": tid, "status": "running"})
        if kind == "task_updated":
            task.update(msg.get("patch") or {})
        elif kind == "task_progress":  # its "description" is the current activity, not the title
            fields = {k: v for k, v in msg.items() if k not in ("type", "subtype", "uuid", "session_id")}
            fields["activity"] = fields.pop("description", None)
            task.update(fields)
        else:
            task.update({k: v for k, v in msg.items() if k not in ("type", "subtype", "uuid", "session_id")})
        if kind == "task_notification":
            task["status"] = msg.get("status") or "completed"
        card = self.tool_cards.get(task.get("tool_use_id"))
        if card is not None:
            if kind == "task_started":
                card.bind_task(task)
                if task.get("task_type") == "local_agent":
                    self.panel.link_card(card, task)
            elif kind == "task_notification" and (card.state in ("running", "background", "interrupted")
                                                  or card.task is None):
                # not on task_updated: it comes first, without the summary and output file. One settled as
                # interrupted (a chat opened as finished, settle_tasks) or never told of its task ended after all.
                card.finish_task(task)
        if kind == "task_notification" and not task.get("noted"):
            task["noted"] = True
            if (line := self.task_note(task)) is not None:  # (none while its card's in the chat: it says so)
                self.add(line)
        self.panel.update_task(task)
        self.update_tasks_indicator()

    def on_tasks_changed(self, running):
        """The CLI's current list of running tasks (endings arrive as task_notification)."""
        for t in running:
            if isinstance(t, dict) and t.get("task_id"):
                self.tasks.setdefault(t["task_id"], {"status": "running"}).update(t)
                self.panel.update_task(self.tasks[t["task_id"]])
        self.update_tasks_indicator()

    def running_tasks(self):
        return [t for t in self.tasks.values() if t.get("status") == "running"]

    def update_tasks_indicator(self):
        self.update_session_state()
        running = self.running_tasks()
        agents = sum(t.get("task_type") == "local_agent" for t in running)
        procs = len(running) - agents
        parts = ([wording.say("status_bar.processes", n=procs)] if procs else []) + \
                ([wording.say("status_bar.subagents", n=agents)] if agents else [])
        self.tasks_btn.set_visible(bool(running))
        self.tasks_btn.set_label(wording.say("status_bar.running", tasks=" · ".join(parts)) if running else "")
        self.tasks_btn.set_tooltip_text("\n".join(t.get("description") or t["task_id"] for t in running)
                                        + "\n" + wording.say("status_bar.open_panel"))

    def stop_task(self, task_id):
        self.request({"subtype": "stop_task", "task_id": task_id}, lambda _r: None)
        self.note(wording.say("side_panel.stop_requested", task=self.tasks.get(task_id, {}).get("description") or task_id))

    def offers_background(self):
        """Whether the open chat's agent moves a command or subagent it's running to the background when asked: only
        Claude Code does (background_tasks); Codex, pi and Apple's model have no way to."""
        return model_program(self.model) == "claude"

    def move_foreground(self):
        """Send now's: each command and subagent of the turn's own running in the foreground moved to the background,
        where it goes on (background_tasks by its call, as send_to_background does, unsaid if it can't be), each card
        taking its result as a task's; the step the turn waits on ends, so the turn takes in what was sent now. A
        subagent's own commands and a call waiting on a permission are left as they are."""
        if not self.offers_background() or not self.proc or not self.busy:
            return
        for card in [c for c in self.tool_cards.values() if getattr(c, "to_background", None) is not None and
                     c.state == "running" and c.asking is None and not c.moving and c.tool_id not in self.tool_parent]:
            self.send_to_background(card, said=False)

    def send_to_background(self, card, said=True):
        """A card's Background: its command or subagent moved to the background (Claude Code's background_tasks, by
        its call), where it goes on, its result saying so (task_from_result) and its end told as a task's; the turn
        goes on, so what's sent at it reaches the agent. Said in the chat if it can't be (said)."""
        def done(r):
            if not (r or {}).get("backgrounded", True):  # (it ended, or its task wasn't one yet: nothing to move)
                card.moved(False)
                if said:
                    self.note(wording.say("tool_calls.not_moved", what=tool_label(card.name)))

        def refused(why):
            card.moved(False)
            if said:
                self.note(wording.say("tool_calls.not_moved_why", why=why))
        if not self.proc or not self.busy:
            return
        card.moved(True)
        self.request({"subtype": "background_tasks", "tool_use_id": card.tool_id}, done, refused)

    def place_split(self):
        """Give the panel the width it was dragged to when it opens, else about a third of the window."""
        width = self.split.get_width() or self.get_width()
        if width:
            want = self.panel_width or max(320, min(460, int(width * 0.36)))
            chat = self.split.get_start_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0]  # (what it can't do without)
            self.split.set_position(width - max(320, min(want, width - chat)))

    def panel_closed_by_drag(self, width):
        """Dragged on past its least and closed: what's kept is the width it was, not that."""
        self.panel_width = width
        save_settings(panel_width=width)

    def split_dragged(self):
        """The rule between the chat and the panel moved while it's held: the panel's width is yours from now on
        (saved once the dragging stops)."""
        if not self.panel_slide.following() or not (self.panel.get_visible() and self.split.get_width()):
            return
        self.panel_width = self.split.get_width() - self.split.get_position()
        if self.split_save is not None:
            GLib.source_remove(self.split_save)
        self.split_save = GLib.timeout_add(400, lambda: setattr(self, "split_save", None) or
                                           save_settings(panel_width=self.panel_width) or False)

    def show_panel(self, show=True, page=None):
        self.panel_slide.set_shown(show)
        if show:
            self.panel.show_page(page or ("agents" if any(t.get("task_type") == "local_agent"
                                                          for t in self.running_tasks()) else "processes"))

    def toggle_meta(self):
        """Ctrl+M (widgets.META_KEY), from anywhere in the window: Meta shown (show_meta), or closed if it's open
        already (close_meta)."""
        if self.panel_slide.target and self.panel.stack.get_visible_child_name() == "meta" \
                and self.capture_cover() is None:
            self.close_meta()
        else:
            self.show_meta()

    def show_meta(self):
        """Meta's page of the side panel, the panel shown if it was hidden, and the focus in Meta's box."""
        cover = self.capture_cover()  # (the large image, the Kubernetes or the database view: over Meta too)
        if cover is not None:
            cover.close()
        if self.completer.visible():
            self.completer.hide()  # (/'s or @'s list, not left over the chat's box)
        self.refs.forget()
        self.show_panel(True, "meta")
        self.panel.meta.composer.grab_focus()

    def _focus_moved(self, _pspec):
        focus = self.get_focus()
        if focus is not None and not focus.is_ancestor(self.panel):
            self.meta_from = focus

    def close_meta(self):
        """The side panel hidden, Meta's @ list with it, and the focus, if it was in the panel, back where it was last
        outside it if that's still on screen, else in what the tab shown has to type in or scroll: the chat's box,
        the transcript (the box hidden: read-only), a file's code."""
        meta_chat, focus = self.panel.meta, self.get_focus()
        if meta_chat.refs.visible():
            meta_chat.refs.hide()
        self.show_panel(False)
        if focus is not None and not focus.is_ancestor(self.panel):
            return
        page = self.tabs.current()
        if not (widgets.focus_shown(self.meta_from) or widgets.focus_shown(self.input)
                or widgets.focus_shown(self.scroller if page is self.chat_column else
                                       page.code.view if isinstance(page, CodePage) else None)
                or (page is not None and page.child_focus(Gtk.DirectionType.TAB_FORWARD))):
            self.set_focus(None)  # (not left in Meta's box as it slides shut)

    def h_stream_event(self, msg):
        ev, parent = msg.get("event", {}), msg.get("parent_tool_use_id")
        et = ev.get("type")
        key = lambda i: (parent, i)  # noqa: E731
        if et == "message_start":
            self.streamed_ids.add(ev["message"]["id"])
        elif et == "content_block_start":
            cb = ev["content_block"]
            if cb["type"] == "text":
                self.blocks[key(ev["index"])] = self.add(self.reply_view(), parent)
            elif cb["type"] == "thinking":  # shown once it has words (most blocks never do)
                view = ThinkingView()
                view.parent_tool = parent
                self.reply_views.append(view.md)  # (its names link as a reply's do)
                self.blocks[key(ev["index"])] = view
            elif cb["type"] in ("tool_use", "server_tool_use"):
                self.blocks[key(ev["index"])] = self.add(self.tool_card(cb["name"], cb["id"]), parent)
        elif et == "content_block_delta":
            w, d = self.blocks.get(key(ev["index"])), ev["delta"]
            if w is None:
                return
            if d["type"] == "text_delta":
                w.append(d["text"])
            elif d["type"] == "thinking_delta":
                w.append(d["thinking"])
                if w.get_parent() is None and w.has_text():
                    self.add(w, w.parent_tool)
            elif d["type"] == "input_json_delta":
                w.add_partial_json(d["partial_json"])

    def h_assistant(self, msg):
        m, parent = msg["message"], msg.get("parent_tool_use_id")
        if parent is None and self.compact_mark is not None and self.compact_mark.state == "done":
            self.compact_mark = None  # (the conversation's gone on: a summary only follows its boundary at once)
        if parent is None:  # (what a turn ending in an error last said: the error, said once, not again by h_result)
            said = "".join(c.get("text", "") for c in m.get("content", []) if c.get("type") == "text").strip()
            self.said_last = said.removeprefix("⚠").strip() or self.said_last
        for w in self.blocks.values():  # its streamed text is complete: no frame's delay
            if isinstance(w, (MarkdownView, ThinkingView)):
                w.finish()
        if (why := self.login_ask(msg)) is not None:  # (its sign-in ran out, say: a card to sign in again, not the error)
            if not getattr(self, "login_asked", False):
                self.login_prompt(why)
            return
        if msg.get("is_api_error_message") or m.get("model") == "<synthetic>":
            stopped = parent is None and getattr(self.proc, "stop_asked", False)
            for c in m.get("content", []):
                if c.get("type") == "text" and not (stopped and STOP_SAID_RE.fullmatch(c["text"])):  # (a stop's
                    # words, aborted: said once, as the turn ends, h_result)
                    self.add(api_error_label(c["text"]), parent)
            return
        self.render_assistant(m.get("content", []), parent, streamed=m.get("id") in self.streamed_ids)
        if parent is None:
            self.refresh_context_soon()

    def render_assistant(self, content, parent=None, streamed=False, written_at=None):
        """Assistant content blocks. Streamed messages already drew their text and thinking."""
        for c in content if isinstance(content, list) else [{"type": "text", "text": str(content)}]:
            if c.get("type") in ("tool_use", "server_tool_use"):
                card = self.tool_cards.get(c["id"])
                if card is None:
                    card = self.add(self.tool_card(c["name"], c["id"]), parent)
                card.set_input(c.get("input", {}))
                if c["id"] in self.orphan_results:  # its result was drawn before it (older history)
                    self.render_tool_results([self.orphan_results.pop(c["id"])])
            elif str(c.get("type")).endswith("_tool_result") and c.get("tool_use_id") in self.tool_cards:
                # (a server tool's result, a web search's: in the reply itself; an error's a dict of type …_error)
                failed = isinstance(c.get("content"), dict) and str(c["content"].get("type")).endswith("_error")
                self.tool_cards[c["tool_use_id"]].set_state("error" if failed else "done")
            elif c.get("type") == "text" and c.get("text", "").strip():
                self.queue_refs(c["text"], written_at)  # first: new names are drawn pending, not redrawn
                if not streamed:
                    self.add(self.reply_view(c["text"]), parent)
                self.add_artifact_chips(c["text"], parent)
            elif c.get("type") == "thinking" and c.get("thinking", "").strip():
                self.queue_refs(c["thinking"], written_at)  # (its names link as a reply's do)
                if not streamed:
                    view = ThinkingView(c["thinking"])
                    self.reply_views.append(view.md)
                    self.add(view, parent)

    def render_tool_results(self, content, from_file=False):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") not in self.tool_cards:
                self.orphan_results[c.get("tool_use_id")] = c
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in self.tool_cards:
                card = self.tool_cards[c["tool_use_id"]]
                if from_file or card.moving:  # (one moved to the background as it ran: its result says it's a task)
                    self.task_from_result(card, c.get("content"), from_file)  # before: the card waits on it
                card.set_result(c.get("content"), c.get("is_error", False))
                if card.task is None and self.task_lines:  # (its task's ending drawn before it: a later part)
                    self.card_task(card)
                if card.artifact:
                    self.register_artifact(card.artifact)

    # ---- artifacts
    def register_artifact(self, info):
        """Remember an artifact (newer info wins, but a known title is never lost)."""
        old = self.artifacts.get(info["url"], {})
        merged = {**old, **{k: v for k, v in info.items() if v}}
        if old.get("title") and merged.get("title") == wording.say("references.claude_artifact"):
            merged["title"] = old["title"]
        self.artifacts[info["url"]] = merged
        if merged.get("title") and merged["title"] != wording.say("references.claude_artifact"):
            ARTIFACT_TITLES[merged["url"]] = merged["title"]  # links in replies read as the title
        self.panel.update_artifact(merged)
        return merged

    def add_artifact_chips(self, text, parent=None):
        """Artifact links in a reply are drawn inline (see link_markup); here they're only
        recorded, so the side panel lists them."""
        for url in dict.fromkeys(ARTIFACT_URL.findall(text or "")):
            if url not in self.artifacts:
                self.register_artifact({"url": url, "title": wording.say("references.claude_artifact")})
