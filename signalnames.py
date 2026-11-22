"""signalnames — Signal: a chat's names looked up, on a worker thread, and shown where a location is now.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import queue

import lookupgit
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from lookup import (
    CATALOGUE_TTL, Names, REF_KINDS, REF_LOC_FIELDS, Ref, catalogue_hits, catalogue_ref, ref_key,
    signal_glossary_exists, signal_place, signal_store,
)
from lookupgit import GITHUB, _git, github_data

from gi.repository import GLib  # noqa: E402

from widgets import load_settings, pretty_path, save_settings  # noqa: E402,F401
from refkinds import ref_kind  # noqa: E402,F401
from gitview import github_repo  # noqa: E402,F401

from signalrefs import (  # noqa: E402
    REF_BRANCHES, REF_BRANCH_KINDS, REF_CODE_PATHS, REF_LOCAL_KINDS, REF_SENSITIVE, REF_TOOL_KINDS, blob_sha,
    ref_candidates, ref_def_role, ref_find_snippet, ref_is_test, ref_map_line, ref_money,
)
from signaltools import SignalTools  # noqa: E402
from signalresolve import SignalResolve  # noqa: E402
from signalcode import SignalCode  # noqa: E402
from signalreader import SignalChat, SignalReader  # noqa: E402


class Signal(SignalTools, SignalResolve, SignalCode, Names):
    """One folder's glossary (<folder>/.baude/signal.db) and the thread that fills it. The main loop
    owns `refs` (surface -> Ref) and reads it while drawing; the worker thread owns the database, git and gh."""

    _instances = {}
    GITHUB_TTL = 600              # PR states change: GitHub answers are kept this long
    STILL_S = 0.4                 # how long the chats are still before the workers go on (wait_still)
    moved_at = 0.0                # when a chat last moved, any window's (time.monotonic(); main loop: moved)

    @classmethod
    def moved(cls):
        """A chat moved: scrolled, gliding, its scrollbar dragged (its view's value changed; main loop)."""
        cls.moved_at = time.monotonic()

    def wait_still(self):
        """(Worker.) Hold on while a chat moves, and STILL_S after (each git it starts too: git_wait). A lookup's own
        Python and its gits kept the window waiting for Python's lock: a long chat flung up as it opened drew 14
        frames a second, 39 with Signal off."""
        while (left := Signal.moved_at + self.STILL_S - time.monotonic()) > 0:
            time.sleep(left)

    @classmethod
    def for_folder(cls, folder, home=None, halted=False):
        """The Signal that searches folder: one for each package of each checkout of a repository (signal_place),
        whichever of its folders a chat is in, its glossary the repository's. home: the folder the chat started in
        (a worktree's chat: its main checkout), whose glossary from before is folded in. halted: one made now starts
        off (only its catalogue wanted, for the glossary's search: nothing is looked up, and no glossary is made
        where there's none)."""
        folder = os.path.realpath(folder)
        home = os.path.realpath(home or folder)
        key = signal_place(folder).key
        if key not in cls._instances:
            cls._instances[key] = cls(folder, home, halted)
        else:
            cls._instances[key].adopt(folder, home)
        return cls._instances[key]

    def adopt(self, folder, home):
        """Another folder of this Signal's package (another chat's): its glossary from before is folded in too."""
        old = signal_store(folder, home)
        if old not in self.olds and signal_glossary_exists(folder, home) and \
                os.path.realpath(old) != os.path.realpath(self.dir):
            self.olds.add(old)
            self.jobs.put(("load", None, None))  # (_open folds it in, then the names are read again)

    def __init__(self, folder, home=None, halted=False):
        Names.__init__(self, folder, home)
        self.refs, self.strong, self.listeners = {}, set(), []
        self.jobs = queue.SimpleQueue()
        # what it's doing, for the Signal page: now (None: idle), a log, replies waiting and read, and the
        # backticked names it doesn't look up (not a kind it knows yet)
        self.activity, self.log, self.waiting, self.read = None, [], 0, 0
        self.skipped, self.status_listeners = {}, []
        self.reader_chat = []  # what the reader said and did, for the Signal page: [(when, kind, text)] (reader_said)
        self.phrases, self.phrase_re = set(), None  # names Sonnet found in plain prose ("the Relay"), linked there too
        # the name catalogue (catalogue_hits; made in the worker, swapped in whole here), when it was made, and
        # whether one is on its way
        self.catalogue, self.catalogue_at, self.catalogue_busy = {}, 0.0, False
        self.shown_texts = {}  # (the replies drawn while it isn't running: linked again when the catalogue is in)
        self.agent = SignalReader(self) if load_settings().get("signal_reader", True) else None  # the reader
        self.transcript = None  # the chat it reads, and what's in it (main loop; follow)
        self._chat, self._checkouts, self._evidence_soon = None, None, None  # (_chat: a SignalChat)
        self._chats = {}  # (the chats read here lately, by transcript, the last followed last: follow)
        self._shown = {}  # (window -> the transcript it last followed: shown)
        self._evidence_from = None  # (the first reply whose evidence may have changed since it went to the worker)
        self.chat_events, self.chat_texts, self.chat_replies, self.chat_evidence = [], [], [], []
        # per reply: what a name in it points at ((message, surface) -> decision; main loop), and the replies
        # the reader has decided
        self.decisions, self.decided_upto, self._phrase_res = {}, set(), {}
        self._by_surface, self._order, self._order_for = {}, {}, None  # (decisions by name; the replies' order)
        self._looked, self._sock_path, self._schemas = set(), None, {}  # (worker / the tools' socket)
        self.tools_said, self.question_listeners = set(), []  # (tool schemas: fetched / asked; who asks you)
        self.texts = {}  # the replies read (hash -> text, session, written_at): looked at again when tools are known
        self.halted = halted  # turned off (stop): the worker drops what it's doing
        # the names looked up here alone while it isn't running (look_locally), and the branches the chat names, in the
        # order it named them (where a name this checkout hasn't may be: _branch_revs)
        self._local_asked, self.chat_branches = set(), {}
        self.runners = set()  # the windows whose chat has it activated: it stops when none has (main loop)
        # the language servers (RefCode, worker thread), what's been said about them, and who asks you to fetch them
        self.code, self.lsp_said, self.ask_listeners = None, set(), []
        self.github = github_repo(self.top) if self.top else None
        self._repos = {}  # the other repositories each chat worked in (chat_repos; worker thread)
        self._git_asked = set()  # names looked up in git while it isn't running (main loop)
        threading.Thread(target=self._run, daemon=True, name="baude-signal").start()
        self.jobs.put(("load", None, None))
        self.jobs.put(("tools", None, None))

    # ---- main loop side
    def queue(self, text, session=None, written_at=None, touched=(), look=True):
        """Look up the names in a reply. Returns their surfaces; new ones show as pending at once. With look False (a
        chat that doesn't have Signal activated, in a window beside one that does), only what's known is drawn."""
        hints = {}  # (the kind a reply gives a name: "the `x` Deployment")
        cands = ref_candidates(text or "", self.skipped, hints)
        for s, c in catalogue_hits(text, self.catalogue, hints).items():  # (and the names known to be there)
            cands.setdefault(s, c)
        message = hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:16]
        if text and text.strip() and look and not self.halted:  # (looked at again when tools are known)
            self.texts[message] = (text, session, written_at)
            self.catalogue_soon()
        if not cands:
            self.status_changed()
            return []
        for s, (kind, _strong) in cands.items():  # (a branch it names: where a name this checkout hasn't may be)
            if kind == "branch" or any((self.catalogue.get(b) or {}).get("kind") == "branch"
                                       for b in (s, "origin/" + s)):
                self.chat_branches.pop(s, None)
                self.chat_branches[s] = True
        if self.halted or not look:  # (not running: what the glossary knows is drawn, and nothing new is kept;
            # a name the catalogue has is linked from what it says, the catalogue made if it's old, and the rest are
            # looked up here alone: look_locally)
            self.strong.update(s for s, (_kind, strong) in cands.items() if strong)
            if text and text.strip():
                self.shown_texts[message] = text
                while len(self.shown_texts) > 2000:
                    self.shown_texts.pop(next(iter(self.shown_texts)))
                self.catalogue_soon()
            self.link_catalogued(cands)
            self.look_in_git(cands)
            self.look_locally(cands, touched, hints)
            self.status_changed()
            return list(cands)
        new = []
        for s, (kind, strong) in cands.items():
            if strong:
                self.strong.add(s)
            if s not in self.refs or self.refs[s].data.get("catalogue") or self.refs[s].data.get("local"):
                # (linked from the catalogue, or looked up while it wasn't running: looked up now, and kept)
                self.refs[s] = Ref(s, kind)
                new.append(s)
        meta = {"session": session, "written_at": written_at, "touched": list(touched), "message": message,
                "hints": hints}
        self.jobs.put(("resolve", cands, meta))
        self.waiting += 1
        if new:
            self.notify(new)
        self.status_changed()
        return list(cands)

    def github_changed(self):
        """GitHub's lists are new: the pull requests and issues named here say where they stand now (nothing asked for:
        it's what the lists say), as a closed issue's # goes grey and a merged pull request purple. The labels showing
        one that's changed are drawn again."""
        changed = []
        for ref in list(self.refs.values()):
            listed = GITHUB.known(ref.data.get("repo"), ref.data.get("number")) if ref.kind == "pr" else None
            data = github_data(listed) if listed is not None else {}
            if any(ref.data.get(k) != v for k, v in data.items()):
                ref.data.update(data)
                changed.append(ref.surface)
        if changed:
            self.notify(changed)

    def link_catalogued(self, names):
        """Link the names the catalogue has that the glossary doesn't (Signal not running): from what the catalogue
        says (catalogue_ref). The labels showing them are drawn again."""
        new = []
        for s in names:
            if s not in self.refs and (entry := self.catalogue.get(s)) is not None:
                self.refs[s] = catalogue_ref(s, entry)
                new.append(s)
        if new:
            self.notify(new)

    def look_locally(self, cands, touched=(), hints=None):
        """Look up the names in a reply Signal isn't running for (Show links alone), as it would but with nothing but
        what's here: the repository's files, a search of its code, the language servers if LSP is on and they're
        installed, the branches the chat names (_on_branches). No model, no network, nothing kept: one found is
        linked for now, and looked up again (and kept) once Signal runs."""
        names = {s: kind for s, (kind, _strong) in cands.items() if kind in REF_LOCAL_KINDS and s not in self.refs
                 and s not in self._local_asked}
        if names and load_settings().get("signal_local", True):
            self._local_asked.update(names)
            self.jobs.put(("local", names, {"touched": list(touched), "hints": dict(hints or {})}))

    def look_in_git(self, cands):
        """Look up the commits and pull requests that names written in replies point at, in git alone (Signal not
        running: no model and no network, nothing kept). One found is linked; one not found stays plain."""
        names = {s: kind for s, (kind, _strong) in cands.items()
                 if kind in ("commit", "pr") and s not in self.refs and s not in self._git_asked}
        if names:
            self._git_asked.update(names)
            self.jobs.put(("git", names, None))

    FOLLOW_MS = 8  # how long a chat just opened is read at a time, between frames (_follow_some)
    CHATS = 4  # the most chats kept read (_chats): windows of one repository share its Signal, each following its own

    def follow(self, transcript, win=None):
        """The chat shown (its transcript; win: the window showing it): what's new in it is read (what Claude saw, as
        evidence), and the reader reads it, from the start or on from where it was (main loop). Not running, it's only read, so the reader's
        decisions carry from reply to reply as they're drawn. This runs for each line another program appends to the
        chat: only what was appended is read (SignalChat). A chat just opened is read FOLLOW_MS at a time between
        frames, once it's still (_follow_some), to the same lists: read at once as it opened, an 85 MB chat and its
        subagents' (96 MB more) held its first frame up 0.9 s. The chats its windows show are kept (shown; CHATS at
        most), each read on from where it was: windows sharing this Signal each follow theirs as it grows, and one
        being read isn't started again when another's grows. One no window shows any more goes, its events, texts and
        evidence with it (some MB of a long one)."""
        if transcript:
            self.transcript = transcript
            if win is not None:
                self._shown[win] = transcript
        if not self.transcript or not os.path.exists(self.transcript):
            return
        if self._chat is None or self._chat.path != self.transcript:
            chat = self._chats.pop(self.transcript, None)
            if chat is None:
                chat = SignalChat(self.transcript, self.root, self.other_checkouts())
                chat.behind = True
                GLib.idle_add(self._follow_some, chat, None, priority=GLib.PRIORITY_LOW)
            self._chats[self.transcript] = chat
            shown = self.shown()
            for path in [p for p in self._chats if p != self.transcript and p not in shown]:
                self._chats.pop(path)  # (its reading stops: _follow_some)
            while len(self._chats) > self.CHATS:
                self._chats.pop(next(iter(self._chats)))
            self._chat, self._evidence_from = chat, None
            if chat.behind:  # (another chat's lists go: this one's as far as it's read, all of them once it is)
                self._chat_read(chat, None, True)
            else:  # (read before: on from there, its evidence looked at again from the start, as if read afresh)
                chat.read()
                self._chat_read(chat, 1, True)
        elif not self._chat.behind:  # (else what's new is read with the rest)
            self._chat_read(self._chat, self._chat.read())

    def shown(self):
        """The transcripts of the chats its windows show now: what each last followed (follow's win), of the windows
        that still listen to it."""
        wins = {getattr(listener, "__self__", None) for listener in self.listeners}
        self._shown = {w: path for w, path in self._shown.items() if w in wins}
        return set(self._shown.values())

    def _follow_some(self, chat, first):
        """FOLLOW_MS of reading a chat just opened (follow), then on in the next idle moment; or on once the chats
        have been still a moment (STILL_S). first: the first reply its reads so far say changed. Once it's all read,
        the replies drawn meanwhile are linked again where the reader's decisions say: a name means what the nearest
        reply it decided says (decision), and which is nearest wasn't known. A chat another window's has taken the place
        of goes on being read (it's kept: _chats), and shows what's read of it when it's followed again."""
        if self._chats.get(chat.path) is not chat:
            return False  # (not kept any more: other chats since, or Signal's read them afresh)
        if (wait := Signal.moved_at + self.STILL_S - time.monotonic()) > 0:
            GLib.timeout_add(int(wait * 1000) + 1, self._follow_some, chat, first, priority=GLib.PRIORITY_LOW)
            return False
        got = chat.read(time.monotonic() + self.FOLLOW_MS / 1000)
        first = got if first is None else first if got is None else min(first, got)
        if chat.behind:
            GLib.idle_add(self._follow_some, chat, first, priority=GLib.PRIORITY_LOW)
            return False
        if chat is not self._chat:
            return False  # (read: its lists go out when it's followed again)
        self._chat_read(chat, first, True)
        self._phrase_res.clear()
        if carried := [s for s, ds in self._by_surface.items() if any(d["by"] == "reader" for _k, d in ds)]:
            self.notify(carried)
        return False

    def _chat_read(self, chat, first, new=False):
        """What a read of the chat found (first: SignalChat.read's, None if nothing's new; new: it's just been read,
        or is about to be): its lists, the evidence looked at again from first, and the reader on."""
        if first is not None or new:  # (another chat's lists go, even for one with nothing in it yet)
            first = first or len(chat.replies) + 1
            self.chat_events, self.chat_texts, self.chat_replies, self.chat_evidence = \
                chat.events, chat.texts, chat.replies, chat.evidence
            if first <= len(chat.replies):  # (a reply that's new, or after new evidence: the worker looks again)
                self._evidence_from = min(first, self._evidence_from or first)
                if self._evidence_soon is None and not self.halted:
                    self._evidence_soon = GLib.timeout_add(300, self._evidence_when_ready)
        if self.agent is not None and not self.halted:
            self.agent.follow(self.transcript)

    def forget_chat(self):
        """No chat to read (a new one, with no transcript yet): the reader stops reading the one before (main loop)."""
        self.transcript, self._chat, self._evidence_from = None, None, None
        self.chat_events, self.chat_texts, self.chat_replies, self.chat_evidence = [], [], [], []
        if self.agent is not None:
            self.agent.restart_chat(None)

    def steer(self, transcript, win=None):
        """The chat a window that runs it shows: read that one (or none, for a chat with no transcript yet)."""
        if transcript:
            self.follow(transcript, win)
        else:
            self.forget_chat()

    def _evidence_when_ready(self):
        """The evidence goes to the worker once the rules have looked at the replies (it builds on their answers), for
        the replies it may have changed for: the new ones, and those after evidence that's new (a reply's evidence is
        what came before it). Lines streaming in come here once per 300 ms or so, not once a line."""
        if self.waiting and not self.halted:
            return True
        self._evidence_soon = None
        first, self._evidence_from = self._evidence_from or 1, None
        self.jobs.put(("evidence", {"replies": list(zip(self.chat_texts, self.chat_replies))[first - 1:],
                                    "first": first, "evidence": self.chat_evidence}, None))
        return False

    def other_checkouts(self):
        """The repository's other checkouts (its main one, its worktrees): paths Claude used there map here."""
        if self._checkouts is None:
            self._checkouts = []
            for line in (_git(self.root, "worktree", "list", "--porcelain") or "").splitlines():
                if line.startswith("worktree ") and os.path.realpath(line[9:]) != self.root:
                    self._checkouts.append(os.path.realpath(line[9:]))
        return self._checkouts

    def reading(self):
        """Whether the reader will decide this chat's names (on, and under its cap): till then, a plain word the
        rules placed isn't drawn as a link."""
        ai = self.agent
        return ai is not None and not ai.stopped and ai.cost < ai.budget

    def decision(self, message, surface):
        """What surface points at in a reply: its own decision, else the nearest earlier reply's in the chat (a name
        means the same until the reader says otherwise), else a later one's."""
        if not message:
            return None
        if (dec := self.decisions.get((message, surface))) is not None:
            return dec
        order = self._order if self._order_for is self.chat_replies else self._reorder()
        here, best = order.get(message), None
        if here is None:
            return None
        for (m, s_), d in self._by_surface.get(surface, ()):
            i = order.get(m)
            if i is None or d["by"] != "reader":
                continue
            rank = (0, here - i) if i < here else (1, i - here)  # (earlier first, then nearest)
            if best is None or rank < best[0]:
                best = (rank, d)
        return best[1] if best else None

    def _reorder(self):
        self._order_for, self._order = self.chat_replies, {h: i for i, h in enumerate(self.chat_replies)}
        return self._order

    def message_phrases(self, message):
        """A regex of the names decided in one reply as links or as not in the code (they're drawn in its prose
        too), or None."""
        if not message:
            return None
        if message not in self._phrase_res:
            names = sorted({s for s in self._by_surface if len(s) >= 3 and (d := self.decision(message, s)) is not None
                            and d["verdict"] != "plain"}, key=len, reverse=True)
            self._phrase_res[message] = re.compile(r"(?<![\w`-])(?:" + "|".join(map(re.escape, names)) +
                                                   r")(?![\w`-])") if names else None
        return self._phrase_res[message]

    def _apply_decisions(self, rows):
        for d in rows:
            key = (d["message"], d["surface"])
            if key not in self.decisions:
                self._by_surface.setdefault(d["surface"], []).append((key, d))
            else:
                self._by_surface[d["surface"]] = [(k, d if k == key else v) for k, v in self._by_surface[d["surface"]]]
            self.decisions[key] = d
            self._phrase_res.clear()  # (a decision may be carried into other replies)
            if d["by"] == "reader":
                self.decided_upto.add(d["message"])
        self.notify(sorted({d["surface"] for d in rows}))
        return False

    def catalogue_soon(self):
        """Catalogue the names afresh if it's been a while (main loop): the worker makes it, then catalogue_ready."""
        if not self.catalogue_busy and time.time() - self.catalogue_at > CATALOGUE_TTL:
            self.catalogue_busy = True
            self.jobs.put(("catalogue", None, None))

    def catalogue_ready(self, catalogue):
        """A new catalogue (None: it couldn't be made, tried again later): the replies read so far are matched against
        it, and the names new to it looked up."""
        self.catalogue_at, self.catalogue_busy = time.time(), False
        if catalogue is None:
            return False
        self.catalogue = catalogue
        if self.halted:  # (the replies shown, linked again from it)
            names = set()
            for text in list(self.shown_texts.values()):
                names.update(catalogue_hits(text, catalogue))
                names.update(s for s in ref_candidates(text, self.skipped, {}) if s in catalogue)
            self.link_catalogued(names)
            return False
        new = []
        for message, (text, session, written_at) in list(self.texts.items()):
            hints = {}
            cands = {s: c for s, c in catalogue_hits(text, catalogue, hints).items() if s not in self.refs}
            if not cands:
                continue
            for s, (kind, _strong) in cands.items():
                self.strong.add(s)
                self.refs[s] = Ref(s, kind)
                new.append(s)
            self.jobs.put(("resolve", cands, {"session": session, "written_at": written_at, "message": message,
                                              "hints": hints, "again": True}))
            self.waiting += 1
        if new:
            self.notify(new)
        self.status_changed()
        return False

    def known_from(self, surface):
        """Where the catalogue knows a name from: [(what, where)] (a Deployment and its manifest, a branch, a table and
        its database…), or []."""
        entry = self.catalogue.get(surface) or self.catalogue.get(ref_key(surface))
        return list(entry["sources"]) if entry else []

    def rescan_tools(self):
        """The tools, subagent types and skills known changed (the catalogue loaded, schemas fetched, the folder's
        definitions read): the replies read so far are looked at again for them."""
        batch, new = [], []
        for message, (text, session, written_at) in self.texts.items():
            cands = {s: c for s, c in ref_candidates(text).items()
                     if c[0] in REF_TOOL_KINDS and (s not in self.refs or self.refs[s].kind != c[0])}
            if cands:
                batch.append((cands, {"session": session, "written_at": written_at, "message": message, "again": True}))
                for s, (kind, strong) in cands.items():
                    if strong:
                        self.strong.add(s)
                    if s not in new:
                        self.refs[s] = Ref(s, kind)
                        new.append(s)
        for cands, meta in batch:
            self.jobs.put(("resolve", cands, meta))
        if new:
            self.notify(new)

    def refresh(self, surfaces, touched=()):
        """Search again, whatever was found (or given up on) before."""
        cands = {}
        for s in surfaces:
            if s in self.refs:
                kind = self.refs[s].kind  # (a tool's name may look like code: it stays a tool)
                cands[s] = (kind if kind in REF_TOOL_KINDS else ref_kind(s) or kind, s in self.strong)
                old = self.refs[s]
                self.refs[s] = Ref(s, old.kind)
        if cands:
            self.jobs.put(("resolve", cands, {"force": True, "touched": list(touched)}))
            self.notify(list(cands))

    def reanchor(self, surface, index, line):
        """A location was found at a new line: store that (and a fresh anchor)."""
        self.jobs.put(("reanchor", (surface, index, line), None))

    def mark_stale(self, surface):
        self.jobs.put(("stale", surface, None))

    def notify(self, surfaces):
        for fn in list(self.listeners):
            fn(surfaces)

    def status_changed(self):
        for fn in list(self.status_listeners):
            fn()

    def status_text(self):
        """One line on what it's doing: the Signal page's, and the header button's tooltip."""
        if self.halted:
            return wording.say("signal.not_running", n=len(self.refs))
        part = self.agent.part if self.agent is not None else None
        now = self.activity or ("Waiting to start" if self.waiting else
                                f"{self.agent.model.capitalize()}: reading replies {part['a']} to {part['b']}" if part
                                else "Idle")
        more = f" · {self.waiting} {'reply' if self.waiting == 1 else 'replies'} waiting" if self.waiting else ""
        ai = self.agent
        read_to = (ai.part["a"] - 1 if ai.part else sum(1 for e in ai.events[:ai.pos] if e[0] == "reply")) if ai else 0
        model = (f" · {ai.model.capitalize()}: {len(ai.replies) - read_to} replies to read, {ref_money(ai.cost)}"
                 if ai.busy() else f" · {ai.model.capitalize()} {ref_money(ai.cost)}") if ai is not None and ai.parts \
            else ""
        return f"{now}{more} · {self.read} {'reply' if self.read == 1 else 'replies'} read{model}"

    def say(self, activity):
        """What it's doing now (main loop; None: nothing)."""
        self._told(activity, None, 0, time.time())

    def note(self, entry):
        """A line for the log of what Signal did (main loop)."""
        self._told(False, entry, 0, time.time())

    def reader_said(self, kind, text):
        """An entry of the reader's chat on the Signal page (main loop): part (the replies it's given), assistant (its
        message's content blocks, drawn as a subagent's are), results (its tool calls' results), glossary (what the
        checks kept of its decisions), done (a part read), refused."""
        self.reader_chat.append((time.time(), kind, text))
        del self.reader_chat[:-400]
        self.status_changed()
        return False

    def stop(self):
        """Turned off: drop the replies waiting, stop Sonnet and Haiku, the language servers and the lookup in
        progress, at once. What's been found stays in the glossary. Returns what was stopped (for a toast)."""
        self.halted = True
        while True:
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                break
        ai, code = self.agent, self.code
        stopped = [f"{self.waiting} {'reply' if self.waiting == 1 else 'replies'} waiting"] if self.waiting else []
        if ai is not None and ai.busy():
            stopped.append(f"{ai.model.capitalize()}'s reading" + (f" (replies {ai.part['a']} to {ai.part['b']})"
                                                                     if ai.part else ""))
        if code is not None and (running := code.running()):
            stopped.append(f"{len(running)} language server{'s' * (len(running) != 1)}")
        if ai is not None:
            ai.stop()  # (what it spent still counts against the cap)
        if code is not None:
            code.stop()  # (started again when a code name next needs one)
        self.waiting, self.activity = 0, None
        self.note("Turned off" + (": stopped " + ", ".join(stopped) if stopped else ""))
        return stopped

    def checking(self):
        """Whether the model pass will check the rules' plain words and several-match names (Sonnet on, under its cap)."""
        ai = self.agent
        return ai is not None and ai.cost < ai.budget

    def links_only(self):
        """Signal shows its links but isn't running: whatever it's doing stops (stop), and the glossary is read again
        for drawing. Returns what was stopped."""
        stopped = self.stop() if not self.halted else []
        self.jobs.put(("load", None, None))
        return stopped

    def start(self):
        """Turned on again after stop: the glossary and the tools are read again; the window queues the replies."""
        if self.halted:
            self.halted, self._chat = False, None  # (the chat is read afresh: its evidence, the reader)
            self._chats.clear()
            self.jobs.put(("load", None, None))
            self.jobs.put(("tools", None, None))
            if self.agent is not None:
                self.agent.start()

    def set_agent(self, on):
        if on and self.agent is None:
            self.agent = SignalReader(self)
            self.follow(None)
        elif not on and self.agent is not None:
            self.agent.stop()
            self.agent = None
        self.status_changed()

    def set_lsp(self, on):
        """Your answer about language servers (the question, or the Signal page's switch): fetch them if they
        aren't there and look at the code names again, or stop them and find code names by search alone."""
        save_settings(signal_lsp=on)
        self.lsp_said.discard("fetch")
        self.jobs.put(("lsp", on, None))
        self.status_changed()

    def _ask_lsp(self):
        for fn in list(self.ask_listeners)[:1]:  # (one window asks)
            fn()
        return False

    def _lsp_ready(self):
        """The servers have just been fetched (or turned on): the code names in this run's replies are looked up
        again, with them."""
        self.refresh([s for s in self.strong if (r := self.refs.get(s)) is not None and r.kind == "symbol"
                      and r.status != "pending"])
        return False

    def _tell(self, activity=False, entry=None, read=0):
        """From the worker: what it's doing now (None: nothing), a line for the log, replies done."""
        GLib.idle_add(self._told, activity, entry, read, time.time())

    def _told(self, activity, entry, read, when):
        if activity is not False and not (self.halted and activity):  # (a lookup that was running when stopped)
            self.activity = activity
        if entry:
            self.log.append((when, entry))
            del self.log[:-400]
        self.waiting, self.read = max(0, self.waiting - read), self.read + read
        self.status_changed()
        return False

    def _apply(self, refs):
        for r in refs:
            self.refs[r.surface] = r
            if r.data.get("phrase") and r.surface not in self.phrases:
                self.phrases.add(r.surface)
                self.phrase_re = re.compile(r"(?<![\w`-])(?:" + "|".join(
                    re.escape(p) for p in sorted(self.phrases, key=len, reverse=True)) + r")(?![\w`-])")
        self.notify([r.surface for r in refs])
        return False

    def _post(self, refs):
        if refs:
            GLib.idle_add(self._apply, refs)

    # ---- either thread: where a location is now
    def old_text(self, blob):
        """The version of a file a location was read from: kept in .baude/blobs, or git's own if committed."""
        if not blob:
            return None
        import zlib
        try:
            with open(os.path.join(self.dir, "blobs", blob), "rb") as f:
                return zlib.decompress(f.read()).decode("utf-8", "replace")
        except (OSError, zlib.error):
            pass
        out = _git(self.top, "cat-file", "blob", blob, raw=True) if self.top else None
        return out.decode("utf-8", "replace") if out is not None else None

    def locate(self, loc):
        """Where a location is now: (absolute path, line, how), how being 'same', 'moved' or 'lost'. One on another
        branch (its rev): the same while that branch has its file (text_on shows it)."""
        full, line = os.path.join(self.root, loc["path"]), loc.get("line")
        if loc.get("rev"):
            there = self.top and _git(self.top, "cat-file", "-e", f"{loc['rev']}:{loc['path']}") is not None
            return full, line, "same" if there else "lost"
        if not os.path.exists(full):
            return full, line, "lost"
        if not line or not loc.get("blob") or os.path.isdir(full):
            return full, line, "same"
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return full, line, "lost"
        if blob_sha(data) == loc["blob"]:
            return full, line, "same"
        new = data.decode("utf-8", "replace").splitlines()
        old = self.old_text(loc["blob"])
        found = ref_map_line(old.splitlines(), new, line) if old is not None else None
        found = found or ref_find_snippet(loc, new, bool(REF_SENSITIVE.search(loc["path"])))
        return (full, found, "moved") if found else (full, line, "lost")

    def text_on(self, loc):
        """A location's file as its branch (rev) has it; None if it hasn't it."""
        out = _git(self.top, "show", f"{loc['rev']}:{loc['path']}", raw=True) if self.top and loc.get("rev") else None
        return out.decode("utf-8", "replace") if out is not None else None

    # ---- worker thread
    def _run(self):
        lookupgit.git_wait(self.wait_still)
        while True:
            jobs = [self.jobs.get()]
            self.wait_still()  # (and what's queued meanwhile goes with it)
            if jobs[0][0] in ("resolve", "local"):  # every reply queued meanwhile, in one go: one git status for all
                while len(jobs) < 200:
                    try:
                        jobs.append(self.jobs.get_nowait())
                    except queue.Empty:
                        break
            batch = [(arg, meta) for job, arg, meta in jobs if job == "resolve"]
            self._local_st = None  # (the local lookups among them share one state of the repository, _job_local's:
            # one a reply was 3 gits, 150 of them as a long chat opened)
            for job, arg, meta in [("batch", batch, None)] * bool(batch) + [j for j in jobs if j[0] != "resolve"]:
                self.wait_still()
                try:
                    getattr(self, "_job_" + job)(arg, meta)
                except Exception as e:  # noqa: BLE001 (one bad name mustn't stop the rest)
                    print(f"baude: signal {job} failed: {e!r}", file=sys.stderr)
                    self._tell(entry=f"Error ({job}): {e!r}")
                    self.db = None  # opened afresh next time
                    if job == "batch":  # never leave names pending forever
                        self._post([Ref(s, kind, "not_found", reason=f"the lookup failed ({e})")
                                    for cands, _m in arg for s, (kind, _strong) in cands.items()])
                        self._tell(read=sum(1 for _c, m in arg if m and m.get("message")))
            if self.jobs.empty():
                self._tell(activity=None)

    def _job_catalogue(self, _arg, _meta):
        self._tell(activity="Cataloguing the names that are there…")
        t0, catalogue = time.monotonic(), None
        try:
            catalogue = self._build_catalogue(self._repo_state())
            self._tell(entry=f"Catalogued {len(catalogue)} names (files, branches, Kubernetes, databases) in "
                             f"{time.monotonic() - t0:.1f} s")
        finally:
            GLib.idle_add(self.catalogue_ready, catalogue)

    def _job_load(self, _arg, _meta):
        if self.halted and not signal_glossary_exists(self.folder, self.home):
            return  # (only showing links, and there's no glossary here: none is made)
        self._tell(activity="Opening the glossary…")
        db = self._open()
        refs = [self._load(s) for (s,) in db.execute("SELECT surface FROM alias WHERE checkout = ? AND scope = ?",
                                                    (self.checkout, self.scope))]
        self._post([r for r in refs if r is not None])
        self._job_decisions_load()
        where = f"{self.root} ({'git' if self.top else 'no git'}{', GitHub ' + self.github if self.github else ''})"
        scope = f", {self.scope}'s names" if self.scope else ""
        self._tell(entry=f"Glossary {pretty_path(self.dir)}{scope}: {len(refs)} names known. Searching {where}.")

    def _save(self, ref, fp):
        db, now = self._open(), time.time()
        row = db.execute("SELECT id, attempts FROM entity WHERE checkout = ? AND scope = ? AND kind = ? AND "
                         "canonical = ?", (self.checkout, self.scope, ref.kind, ref.canonical)).fetchone()
        missed = ref.attempted and ref.status == "not_found"
        attempts = (row[1] if row else 0) + (1 if missed else 0)
        values = (ref.status, ref.reason, json.dumps(ref.data), fp if ref.attempted else ref.fingerprint, attempts,
                  now if missed else None, now)
        if row:
            eid = row[0]
            db.execute("UPDATE entity SET status = ?, reason = ?, data = ?, fingerprint = ?, attempts = ?, "
                       "gave_up_at = COALESCE(?, gave_up_at), updated_at = ? WHERE id = ?", values + (eid,))
        else:
            eid = db.execute("INSERT INTO entity (status, reason, data, fingerprint, attempts, gave_up_at, updated_at, "
                             "kind, canonical, checkout, scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             values + (ref.kind, ref.canonical, self.checkout, self.scope)).lastrowid
        db.execute("DELETE FROM location WHERE entity_id = ?", (eid,))
        for loc in ref.locations:
            cols = [f for f in REF_LOC_FIELDS if f != "id"]
            loc["id"] = db.execute(f"INSERT INTO location (entity_id, {', '.join(cols)}) VALUES (?{', ?' * len(cols)})",
                                   (eid, *(loc.get(f) for f in cols))).lastrowid
        db.execute("INSERT OR REPLACE INTO alias (checkout, scope, surface, entity_id) VALUES (?, ?, ?, ?)",
                   (self.checkout, self.scope, ref.surface, eid))
        ref.id = eid
        if ref.attempted:
            ref.fingerprint = fp

    def _rank(self, ref, texts=()):
        """Several matches: the ones nearest to where the same replies' other names are come first (a field in the
        model.py the reply's other fields are in, a system.yaml in the app folder the reply names). The replies:
        the ones it was looked up from, else texts (a name Sonnet listed). True if the order changed."""
        if ref.status != "ambiguous" or len(ref.locations) < 2:
            return False
        db = self._open()
        msgs = [m for (m,) in db.execute("SELECT DISTINCT message FROM mention WHERE entity_id = ?", (ref.id,))] \
            if ref.id is not None else []
        msgs = msgs or [hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()[:16] for t in texts]
        if not msgs:
            return False
        ctx = {p for (p,) in db.execute(
            f"SELECT (SELECT path FROM location WHERE entity_id = e.id ORDER BY id LIMIT 1) FROM entity e "
            f"WHERE e.status = 'resolved' AND e.id != ? AND e.checkout = ? AND e.scope = ? AND e.id IN "
            f"(SELECT entity_id FROM mention WHERE message IN ({','.join('?' * len(msgs))}))",
            (ref.id or -1, self.checkout, self.scope, *msgs)) if p and not os.path.isabs(p)}

        def near(loc):  # strong evidence only (else the rules' order stands: a data model's field first...)
            if loc["path"] in ctx:
                return 1000
            if any(loc["path"].startswith(c + "/") for c in ctx):
                return 500  # (inside a folder the reply names)
            parts = loc["path"].split("/")
            shared = max((next((i for i, (a, b) in enumerate(zip(parts, c.split("/"))) if a != b),
                               min(len(parts), len(c.split("/")))) for c in ctx), default=0)
            return shared if shared >= 3 else 0
        order = sorted(range(len(ref.locations)), key=lambda i: (-near(ref.locations[i]), i))
        if order == list(range(len(order))):
            return False
        ref.locations = [ref.locations[i] for i in order]
        return True

    def _mention(self, ref, meta, st):
        if not meta or not meta.get("message") or ref.id is None:
            return
        self._open().execute("INSERT OR IGNORE INTO mention VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (ref.id, meta.get("session") or "", meta["message"], ref.surface, st["head"],
                              int(bool(st["status"])), meta.get("written_at"), time.time()))
        if meta.get("written_at") and not (ref.written_at and ref.written_at < meta["written_at"]):
            ref.written_at = meta["written_at"]

    def _job_batch(self, batch, _meta):
        """Look up the names in a batch of replies (and Refreshes): each name once, every mention recorded."""
        replies = sum(1 for _c, m in batch if m and m.get("message") and not m.get("again"))
        names, mentions, touched, hints = {}, [], set(), {}
        for cands, meta in batch:
            force = bool(meta and meta.get("force"))
            touched.update((meta or {}).get("touched") or ())
            hints.update((meta or {}).get("hints") or {})
            for s, (kind, _strong) in cands.items():
                names[s] = (names.get(s, (kind, False))[0], names.get(s, (kind, False))[1] or force)
                mentions.append((s, meta))
        what = f"{replies} {'reply' if replies == 1 else 'replies'}" if replies else \
            ("Earlier replies, for tools" if any(m and m.get("again") for _c, m in batch) else "Refresh")
        self._tell(activity=f"{what}: {len(names)} names. Reading the repository's state…")
        t0 = time.monotonic()
        st = self._repo_state()
        if (dt := time.monotonic() - t0) > 0.5:
            self._tell(entry=f"git status took {dt:.1f} s ({len(st['status'])} changed files)")
        meta = {"touched": sorted(touched), "hints": hints}
        out, enrich, known = {}, [], 0
        for i, (surface, (kind, force)) in enumerate(names.items(), 1):
            if self.halted:  # turned off: the names not looked up yet stay as they were
                break
            ref = None if force or kind in REF_TOOL_KINDS else self._load(surface)  # (the catalogue is newer)
            if ref is not None:
                if ref.status in ("resolved", "ambiguous", "stale"):
                    ref = self._validate(ref, st, meta)
                elif ref.status != "not_found" or ref.fingerprint != st["fp"]:
                    ref = None  # given up on, but the code has changed since: the cheap tiers again
            if ref is None:
                self._tell(activity=f"{what}: looking up {surface} ({REF_KINDS[kind][0].lower()}), {i} of {len(names)}")
                t0 = time.monotonic()
                ref = self._resolve(surface, kind, st, meta)
                ms = (time.monotonic() - t0) * 1000
                verdict = {"resolved": "Found", "ambiguous": "Several matches", "not_found": "Not found"}.get(
                    ref.status, ref.status.capitalize())
                self._tell(entry=f"{verdict}: {surface} ({REF_KINDS[ref.kind][0].lower()}): "
                                 f"{ref.summary().removeprefix('Not found: ')} [{ms:.0f} ms]")
            elif ref.attempted:  # validated, and found again by name
                self._tell(entry=f"Found again: {surface}: {ref.summary()}")
            else:
                known += 1
            self._save(ref, st["fp"])
            out[surface] = ref
            if self._needs_github(ref):
                enrich.append(ref)
        misses = {s: r.kind for s, r in out.items() if r.status == "not_found" and r.attempted and
                  r.kind in REF_BRANCH_KINDS}
        if misses and not self.halted:  # (not in this checkout: perhaps on a branch the chat names)
            for surface, ref in self._on_branches(misses, st).items():
                for loc in ref.locations:
                    self._anchor(loc, st)
                ref.attempted = True
                self._tell(entry=f"Found on {ref.locations[0]['rev']}: {surface}: {ref.summary()}")
                self._save(ref, st["fp"])
                out[surface] = ref
        for surface, m in mentions:
            if surface in out:
                self._mention(out[surface], m, st)
        for ref in out.values():
            if self._rank(ref):
                self._save(ref, st["fp"])
        self._open().commit()
        self._post(list(out.values()))
        if known:
            self._tell(entry=f"{what}: {known} of {len(names)} names already known")
        self._tell(read=replies)
        for ref in enrich:  # slow (the network): after everything local is shown
            if self.halted:
                break
            self._tell(activity=f"Asking GitHub about {ref.surface}…")
            self._tell(entry=f"GitHub: {self._enrich(ref)}")
            self._save(ref, st["fp"])
            self._open().commit()
            self._post([ref])

    def _job_local(self, names, meta):
        """Names in replies Signal isn't running for, looked up here alone (look_locally): those found are posted,
        marked local; nothing goes in the glossary, nothing is copied into .baude, nothing is fetched or asked."""
        self._tell(activity=f"Looking up {len(names)} names here alone (Signal isn't running)…")
        st = self._local_st = self._local_st or self._repo_state()  # (the replies drained with it share it: _run)
        found, misses = [], {}
        for surface, kind in names.items():
            try:
                ref = self._resolve(surface, kind, st, meta, keep=False)
            except Exception as e:  # noqa: BLE001 (one bad name mustn't stop the rest)
                print(f"baude: signal: looking up {surface} here failed: {e!r}", file=sys.stderr)
                continue
            if ref.status in ("resolved", "ambiguous"):
                found.append(ref)
            elif kind in REF_BRANCH_KINDS:
                misses[surface] = kind
        for ref in self._on_branches(misses, st).values():
            for loc in ref.locations:
                self._anchor(loc, st, keep=False)
            found.append(ref)
        for ref in found:
            ref.data["local"] = True
        self._post(found)
        self._tell(entry=f"Here alone: {len(found)} of {len(names)} names found (nothing kept)")

    def _branch_revs(self, st):
        """The branches a name this checkout hasn't may be on, in the order they're looked at: those the chat names
        (the latest first) and the heads of the pull requests it names, then the REF_BRANCHES most recently changed;
        one a commit, and not the one checked out."""
        if not self.top:
            return []
        shas, order = {}, []
        for row in (_git(self.top, "for-each-ref", "--sort=-committerdate", "--format=%(objectname) %(refname) "
                         "%(refname:short)", "refs/heads", "refs/remotes") or "").splitlines():
            sha, full, short = (row.split(" ", 2) + ["", ""])[:3]
            if short and not full.endswith("/HEAD") and sha != st["head"]:
                shas[short] = sha
                order.append(short)
        heads = [r.data["head"] for r in list(self.refs.values()) if r.kind == "pr" and r.data.get("head")]
        named = [c for b in list(self.chat_branches)[::-1] + heads[::-1]
                 if (c := next((c for c in (b, "origin/" + b) if c in shas), None))]
        revs, seen = [], set()
        for short in named + order[:REF_BRANCHES]:
            if shas[short] not in seen:
                seen.add(shas[short])
                revs.append(short)
        return revs

    def _on_branches(self, names, st):
        """Names this checkout hasn't ({surface: kind}: code names and files) looked for on other branches
        (_branch_revs), the code names in one search: each found there is that branch's (its locations' rev), at a
        definition of the name (else a module of that name) or a file of that name, on the first branch in that
        order that has one. {surface: Ref}."""
        revs = self._branch_revs(st) if names else []
        if not revs:
            return {}
        out, order = {}, {r: i for i, r in enumerate(revs)}
        code = {s: re.split(r"[.:]", re.sub(r"\([^()]*\)$", "", s))[-1] for s, k in names.items() if k == "symbol"}
        code = {s: n for s, n in code.items() if re.fullmatch(r"[A-Za-z_]\w{2,}", n)}
        if code:
            args = [a for n in sorted(set(code.values())) for a in ("-e", n)]
            raw = _git(self.top, "grep", "-n", "-I", "-w", "-F", *args, *revs, "--", *REF_CODE_PATHS, ok=(0, 1),
                       timeout=60) or ""
            defs = {}
            for line in raw.splitlines()[:50000]:
                parts = line.split(":", 3)
                if len(parts) < 4 or not parts[2].isdigit():
                    continue
                rev, path, n, text = parts
                for name in set(code.values()):
                    if name in text and (role := ref_def_role(path, text, name)):
                        defs.setdefault(name, []).append((order.get(rev, len(revs)), rev, path, int(n), role))
            for surface, name in code.items():
                hits = sorted(defs.get(name, ()))
                hits = [h for h in hits if h[1] == hits[0][1]] if hits else []
                hits = [h for h in hits if not ref_is_test(h[2])] or hits
                if not hits:
                    continue
                rev = hits[0][1]
                locs = [{"role": role, "path": path, "line": n, "resolver": "branch", "rev": rev}
                        for _o, _r, path, n, role in hits[:30]]
                out[surface] = Ref(surface, "symbol", "resolved" if len(hits) == 1 else "ambiguous",
                                   f"{hits[0][2]}::{name}" if len(hits) == 1 else name,
                                   reason=f"not in this checkout: on {rev}" + (f", {len(hits)} definitions of {name}"
                                                                               if len(hits) > 1 else ""),
                                   locations=locs)
        def tree(rev):  # (a branch's files, once a commit)
            if (files := self._cache.get(("tree", rev))) is None:
                files = self._cache[("tree", rev)] = (_git(self.top, "ls-tree", "-r", "--name-only", rev, timeout=30)
                                                      or "").splitlines()
            return files
        exts = tuple(p[1:] for p in REF_CODE_PATHS)
        for surface, name in code.items():  # (no definition: a module of that name, as _symbol finds one here)
            if surface in out:
                continue
            for rev in revs:
                if hits := sorted((f for f in tree(rev) if f.endswith(exts) and
                                   os.path.splitext(os.path.basename(f))[0] == name), key=lambda h: (len(h), h)):
                    out[surface] = Ref(surface, "symbol", "resolved" if len(hits) == 1 else "ambiguous", hits[0] if
                                       len(hits) == 1 else name, reason=f"not in this checkout: on {rev}",
                                       locations=[{"role": "module", "path": h, "line": None, "resolver": "branch",
                                                   "rev": rev} for h in hits[:30]])
                    break
        for surface, kind in names.items():
            if kind != "file":
                continue
            m = re.fullmatch(r"(.*?)(?::(\d+)(?::\d+)?)?", surface)
            want, line = m.group(1).removeprefix("./").rstrip("/"), int(m.group(2)) if m.group(2) else None
            for rev in revs:
                files = tree(rev)
                hits = [f for f in files if f == want or f.endswith("/" + want)]
                if hits:
                    hits.sort(key=lambda h: (not self._in_scope(h), len(h), h))
                    locs = [{"role": "file", "path": h, "line": line, "resolver": "branch", "rev": rev}
                            for h in hits[:40]]
                    out[surface] = Ref(surface, "file", "resolved" if len(hits) == 1 else "ambiguous",
                                       hits[0] + (f":{line}" if line else "") if len(hits) == 1 else want,
                                       reason=f"not in this checkout: on {rev}" + (f", {len(hits)} files end with "
                                                                                   f"{want}" if len(hits) > 1 else ""),
                                       locations=locs)
                    break
        return out

    def _job_git(self, names, _meta):
        """Commits and pull requests looked up in git alone (look_in_git): the ones found are linked."""
        known = self.transcript is not None  # (till the chat is known, neither are the repositories it worked in)
        found, missed = [], []
        for surface, kind in names.items():
            ref = self._commit(surface) if kind == "commit" else self._pr(surface)
            (found if ref.status == "resolved" else missed).append(ref)
        self._post(found)
        if missed and not known:
            GLib.idle_add(self._git_asked.difference_update, [r.surface for r in missed])

    def chat_repos(self):
        """The other git repositories the chat worked in, in the order it went to them (worker thread): where the
        paths in its tool calls are (a file it read or edited, a cd, a git -C) and the folders it ran in. A commit
        it names may be one of theirs. Read on from where it was, as the chat grows."""
        path = self.transcript
        if not path:
            return []
        seen = self._repos.setdefault(path, {"offset": 0, "tops": [], "dirs": {}})
        lines, offset = [], seen["offset"]
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                for raw in f:  # (a line at a time: a long chat decoded and split whole held Python's lock 0.2 s)
                    if not raw.endswith(b"\n"):
                        break  # (whole lines only: the rest is read next time)
                    offset += len(raw)
                    lines += raw.decode("utf-8", "replace").splitlines()
        except OSError:
            return seen["tops"]
        seen["offset"] = offset
        home = os.path.expanduser("~")
        mine = {os.path.realpath(t) for t in (self.top, *self.other_checkouts()) if t}
        for line in lines:
            paths = re.findall(r'"cwd":\s*"([^"]+)"', line[:3000])
            if re.search(r'"type":\s*"tool_use"', line) and len(line) < 500_000:
                paths += re.findall(r'(?<![\w/.~:-])((?:~|/[\w.-]+)/[^\s"\'\\:;|&)<>`,]+)', line)
            for p in paths:
                p = os.path.join(home, p[2:]) if p.startswith("~/") else p
                d = asked = p if os.path.isdir(p) else os.path.dirname(p)
                if d in seen["dirs"] or any(d == t or d.startswith(t + os.sep) for t in mine | set(seen["tops"])):
                    continue
                while d and d not in (home, "/") and not os.path.isdir(d):
                    d = os.path.dirname(d)
                if d not in seen["dirs"]:
                    seen["dirs"][d] = (_git(d, "rev-parse", "--show-toplevel") or "").strip() \
                        if d not in (home, "/", "") else ""
                top = seen["dirs"][asked] = seen["dirs"][d]
                top = os.path.realpath(top) if top else ""
                if top and top not in mine and top not in seen["tops"]:
                    seen["tops"].append(top)
        return seen["tops"]
