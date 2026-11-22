"""Spelling and grammar as you write, for a Gtk.TextView of Markdown (Record's pages): a word misspelled underlined
with a red squiggle, a grammar error with a blue one; a right-click on one offers the checker's suggestions, Ignore and
Learn. Code (``` blocks, `code`), links, URLs, @names and code-like words (paths, snake_case, camelCase) aren't checked.

What checks it, whichever is there:
- macOS: NSSpellChecker, through the Objective-C runtime with ctypes, spelling and grammar, in the languages System
  Settings has, with the words learnt in any Mac app.
- elsewhere: Enchant 2 (libenchant-2, with the system's Hunspell or Nuspell dictionaries) for spelling, and harper-ls
  (Harper's language server, over LSP on stdio) for grammar, each when it's installed.
With none, nothing's underlined and nothing else changes (backend is None).

The checking is on a thread of its own (one for every view: _Worker), a line at a time, and what it finds is kept for
each line's text (_found), so a page is checked once, and then only the lines that change, after a pause in typing; a
right-click's guesses go ahead of any check waiting (Worker.FIRST)."""
import ctypes
import ctypes.util
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gsk, Gtk  # noqa: E402

import markdownview  # noqa: E402
from baudelib import wording  # noqa: E402

SPELLING, GRAMMAR = "spelling", "grammar"
COLORS = {SPELLING: "#e01b24", GRAMMAR: "#3584e4"}  # (GNOME's red 3 and blue 3: a squiggle reads on light and dark)
WAVE = (1.6, 4.0, 1.2)  # (a squiggle's height, its wavelength and its line's width, in pixels)
WAIT_MS = 400  # (a pause in typing this long: what changed is checked)
GUESSES = 6  # (suggestions a right-click offers, at most)
KEEP = 4000  # (lines' results kept, at most: _found)

# What isn't prose, blanked (spaces, so offsets stay) before a line's checked
_SKIP = re.compile("|".join((
    r"`[^`\n]*`?",  # `code` (and one being typed)
    r"\[\[[^\]\n]*\]\]",  # [[a page]]
    r"\]\([^)\s]*\)?",  # a [link](url)'s url
    r"<?\b(?:https?://|www\.)[^\s<>\"]*>?",  # a URL
    r"[\w.+-]+@[\w-]+\.[\w.-]+",  # an email address
    r"@\{[^}\n]*\}?|[@#][\w./:-]+",  # @name, @{a name}, #123
    r"\S*[/\\]\S*",  # paths
    r"\w*[^\W_]_[^\W_]\w*",  # snake_case (an underscore inside a word: _italic_'s are outside it)
    r"\b[a-z]+[A-Z]\w*|\b\w*\d\w*",  # camelCase, words with digits in
)))


_ANCHOR, _JOINER = "\ufffc", "\u2060"  # (an edit's controls in a page: its child anchor, and the joiner before it)


def masked_lines(text):
    """text's lines, what isn't to be checked in each blanked (_SKIP), a code block's lines all blank. An edit's
    controls (their anchor and word joiner, in the buffer's slice) are spaces, so offsets stay the buffer's."""
    lines = text.replace(_ANCHOR, " ").replace(_JOINER, " ").split("\n")
    bare = text.replace(_ANCHOR, "").replace(_JOINER, "").split("\n")
    code = {n for first, last, _closed in markdownview.fence_spans(bare) for n in range(first, last + 1)}
    return ["" if n in code else _SKIP.sub(lambda m: " " * len(m.group()), line) for n, line in enumerate(lines)]


# ---------------------------------------------------------------- what checks it

class _Mac:
    """NSSpellChecker's spelling and grammar. Called on the worker's thread only."""
    grammar = True

    def __init__(self):
        rt = self.rt = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        rt.objc_getClass.restype = rt.sel_registerName.restype = ctypes.c_void_p
        rt.objc_getClass.argtypes = rt.sel_registerName.argtypes = [ctypes.c_char_p]
        rt.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        rt.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]

        class Range(ctypes.Structure):  # (NSRange: in UTF-16 code units)
            _fields_ = [("loc", ctypes.c_ulong), ("len", ctypes.c_ulong)]
        self.Range, self.checker, self.tag = Range, None, 0
        if not rt.objc_getClass(b"NSSpellChecker"):
            raise OSError("no NSSpellChecker")

    def send(self, obj, sel, *args, types=(), ret=ctypes.c_void_p):
        rt = self.rt
        if isinstance(obj, bytes):
            obj = rt.objc_getClass(obj)
        f = ctypes.CFUNCTYPE(ret, ctypes.c_void_p, ctypes.c_void_p, *types)(
            ctypes.cast(rt.objc_msgSend, ctypes.c_void_p).value)
        return f(obj, rt.sel_registerName(sel), *args)

    def ns(self, text):
        return self.send(b"NSString", b"stringWithUTF8String:", text.encode(), types=(ctypes.c_char_p,))

    def py(self, string):
        return ctypes.string_at(self.send(string, b"UTF8String")).decode() if string else ""

    def items(self, array):
        n = self.send(array, b"count", ret=ctypes.c_ulong) if array else 0
        return [self.send(array, b"objectAtIndex:", i, types=(ctypes.c_ulong,)) for i in range(n)]

    def _ready(self):
        if self.checker is None:
            self.checker = self.send(b"NSSpellChecker", b"sharedSpellChecker")
            self.tag = self.send(b"NSSpellChecker", b"uniqueSpellDocumentTag", ret=ctypes.c_long)
        return self.checker

    def check(self, line):
        """[(start, end, kind, fixes, what's wrong)] in line, in characters; a misspelled word's fixes None: its guesses
        are asked for when it's right-clicked (guesses)."""
        pool = self.rt.objc_autoreleasePoolPush()
        try:
            checker, R = self._ready(), self.Range
            string, chars = self.ns(line), _chars(line)
            found = self.send(checker, b"checkString:range:types:options:inSpellDocumentWithTag:orthography:wordCount:",
                              string, R(0, len(line.encode("utf-16-le")) // 2), 2 | 4, None, self.tag, None, None,
                              types=(ctypes.c_void_p, R, ctypes.c_ulonglong, ctypes.c_void_p, ctypes.c_long,
                                     ctypes.c_void_p, ctypes.c_void_p))  # (2 | 4: spelling and grammar)
            out = []
            for result in self.items(found):
                kind = self.send(result, b"resultType", ret=ctypes.c_ulonglong)
                at = self.send(result, b"range", ret=R)
                if kind == 2:
                    out.append((chars(at.loc), chars(at.loc + at.len), SPELLING, None, ""))
                elif kind == 4:  # (each detail: a part of the sentence, from its start)
                    for detail in self.items(self.send(result, b"grammarDetails")):
                        def get(key):
                            return self.send(detail, b"objectForKey:", self.ns(key), types=(ctypes.c_void_p,))
                        part = self.send(get("NSGrammarRange"), b"rangeValue", ret=R)
                        start = at.loc + part.loc
                        out.append((chars(start), chars(start + max(part.len, 1)), GRAMMAR,
                                    [self.py(c) for c in self.items(get("NSGrammarCorrections"))][:GUESSES],
                                    self.py(get("NSGrammarUserDescription"))))
            return out
        finally:
            self.rt.objc_autoreleasePoolPop(pool)

    def guesses(self, word):
        pool = self.rt.objc_autoreleasePoolPush()
        try:
            found = self.send(self._ready(), b"guessesForWordRange:inString:language:inSpellDocumentWithTag:",
                              self.Range(0, len(word.encode("utf-16-le")) // 2), self.ns(word), None, self.tag,
                              types=(self.Range, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long))
            return [self.py(g) for g in self.items(found)][:GUESSES]
        finally:
            self.rt.objc_autoreleasePoolPop(pool)

    def learn(self, word):
        """Learnt as Learn Spelling in any Mac app learns it: the system's, for every app."""
        pool = self.rt.objc_autoreleasePoolPush()
        try:
            self.send(self._ready(), b"learnWord:", self.ns(word), types=(ctypes.c_void_p,))
        finally:
            self.rt.objc_autoreleasePoolPop(pool)


def _chars(text):
    """A function from a UTF-16 offset in text (NSString's) to its offset in characters (a text buffer's)."""
    if all(ord(c) < 0x10000 for c in text):
        return lambda at: at
    where = []  # (each UTF-16 unit's character: two for one outside the BMP)
    for i, c in enumerate(text):
        where += [i, i] if ord(c) >= 0x10000 else [i]
    where.append(len(text))
    return lambda at: where[min(at, len(where) - 1)]


class _Enchant:
    """Enchant 2's spelling, in the locale's language (else English). Called on the worker's thread only."""
    grammar = False
    WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")

    def __init__(self):
        name = ctypes.util.find_library("enchant-2") or "libenchant-2.so.2"
        lib = self.lib = ctypes.CDLL(name)  # (OSError if it isn't installed)
        lib.enchant_broker_init.restype = ctypes.c_void_p
        lib.enchant_broker_dict_exists.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.enchant_broker_request_dict.restype = ctypes.c_void_p
        lib.enchant_broker_request_dict.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.enchant_dict_check.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ssize_t]
        lib.enchant_dict_suggest.restype = ctypes.POINTER(ctypes.c_char_p)
        lib.enchant_dict_suggest.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ssize_t,
                                             ctypes.POINTER(ctypes.c_size_t)]
        lib.enchant_dict_free_string_list.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
        lib.enchant_dict_add.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ssize_t]
        broker = lib.enchant_broker_init()
        locale = next((os.environ[k] for k in ("LC_ALL", "LC_MESSAGES", "LANG") if os.environ.get(k)), "")
        locale = locale.split(".")[0].split("@")[0]
        for tag in (locale, locale.split("_")[0], "en_US", "en_GB", "en"):
            if tag and tag not in ("C", "POSIX") and lib.enchant_broker_dict_exists(broker, tag.encode()):
                self.dict = lib.enchant_broker_request_dict(broker, tag.encode())
                if self.dict:
                    return
        raise OSError("no Enchant dictionary")

    def check(self, line):
        out = []
        for m in self.WORD.finditer(line):
            word = m.group()
            if len(word) < 2 or word.isupper():  # (an initial, an acronym)
                continue
            raw = word.replace("’", "'").encode()
            if self.lib.enchant_dict_check(self.dict, raw, len(raw)) > 0:
                out.append((m.start(), m.end(), SPELLING, None, ""))
        return out

    def guesses(self, word):
        raw, n = word.replace("’", "'").encode(), ctypes.c_size_t(0)
        found = self.lib.enchant_dict_suggest(self.dict, raw, len(raw), ctypes.byref(n))
        if not found:
            return []
        said = [found[i].decode(errors="replace") for i in range(min(n.value, GUESSES))]
        self.lib.enchant_dict_free_string_list(self.dict, found)
        return said

    def learn(self, word):
        raw = word.replace("’", "'").encode()
        self.lib.enchant_dict_add(self.dict, raw, len(raw))  # (the personal word list, every Enchant app's)


class _Harper:
    """harper-ls's grammar, over LSP on stdio: each line opened as a document of its own, its diagnostics what's wrong,
    their code actions' edits the fixes. Its own spell check is off (Enchant's are the spelling's squiggles), and what
    it says of a word Enchant has already underlined is left out (_Linux.check)."""
    SETTINGS = {"harper-ls": {"linters": {"SpellCheck": False, "spell_check": False}}}
    WAIT = 3  # (seconds for a line's diagnostics: past that, Harper's taken as gone, and the grammar with it)

    def __init__(self, exe):
        self.proc = subprocess.Popen([exe, "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        self.incoming, self.ids, self.docs, self.ok = queue.Queue(), 0, 0, True
        threading.Thread(target=self._read, daemon=True, name="harper").start()
        caps = {"general": {"positionEncodings": ["utf-32", "utf-16"]},
                "textDocument": {"publishDiagnostics": {}, "codeAction": {"codeActionLiteralSupport": {
                    "codeActionKind": {"valueSet": ["quickfix"]}}}},
                "workspace": {"configuration": True}}
        init = self.request("initialize", {"processId": os.getpid(), "rootUri": None, "capabilities": caps,
                                           "initializationOptions": self.SETTINGS})
        if init is None:
            raise OSError("harper-ls didn't start")
        self.utf32 = (init.get("capabilities") or {}).get("positionEncoding") == "utf-32"
        self.notify("initialized", {})
        self.notify("workspace/didChangeConfiguration", {"settings": self.SETTINGS})

    def _send(self, message):
        body = json.dumps({"jsonrpc": "2.0", **message}).encode()
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()

    def _read(self):
        """Harper's messages, as they come (its own thread), onto incoming; None once it's gone."""
        out = self.proc.stdout
        try:
            while True:
                length = None
                while (line := out.readline()) not in (b"\r\n", b"\n"):
                    if not line:
                        raise EOFError
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                self.incoming.put(json.loads(out.read(length)))
        except (EOFError, OSError, ValueError, TypeError):
            self.incoming.put(None)

    def notify(self, method, params):
        self._send({"method": method, "params": params})

    def request(self, method, params):
        self.ids += 1
        self._send({"id": self.ids, "method": method, "params": params})
        return self._wait(lambda m: m.get("id") == self.ids and "method" not in m, lambda m: m.get("result"))

    def _wait(self, wanted, value):
        """What wanted is, of what Harper sends in the next WAIT seconds (value of it); what it asks of the client meanwhile
        answered. None if it doesn't come (and Harper's not asked again)."""
        while True:
            try:
                m = self.incoming.get(timeout=self.WAIT)
            except queue.Empty:
                m = None
            if m is None:
                self.ok = False
                return None
            if "method" in m and "id" in m:  # (a request of Harper's: its settings, or nothing to say)
                items = (m.get("params") or {}).get("items") or []
                result = [self.SETTINGS["harper-ls"] for _ in items] if m["method"] == "workspace/configuration" else None
                self._send({"id": m["id"], "result": result})
                continue
            if wanted(m):
                return value(m)

    def check(self, line):
        self.docs += 1
        uri = "file://" + os.path.join(tempfile.gettempdir(), f"record-spelling-{os.getpid()}-{self.docs}.md")
        self.notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "markdown", "version": 1,
                                                              "text": line}})
        try:
            diagnostics = self._wait(lambda m: m.get("method") == "textDocument/publishDiagnostics" and
                                     (m.get("params") or {}).get("uri") == uri,
                                     lambda m: m["params"].get("diagnostics") or [])
            chars = (lambda at: at) if self.utf32 else _chars(line)
            out = []
            for d in diagnostics or ():
                a, b = (chars(d["range"][end]["character"]) for end in ("start", "end"))
                actions = self.request("textDocument/codeAction", {"textDocument": {"uri": uri}, "range": d["range"],
                                                                   "context": {"diagnostics": [d]}}) or []
                fixes = [e["newText"] for act in actions if isinstance(act, dict)
                         for e in ((act.get("edit") or {}).get("changes") or {}).get(uri, [])
                         if e.get("range") == d["range"]]
                out.append((a, max(b, a + 1), GRAMMAR, list(dict.fromkeys(fixes))[:GUESSES], d.get("message", "")))
            return out
        finally:
            self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})


class _Linux:
    """Enchant's spelling and Harper's grammar, whichever of them is installed (OSError: neither)."""

    def __init__(self):
        self.spelling = self.harper = None
        try:
            self.spelling = _Enchant()
        except (OSError, AttributeError) as e:
            print(f"spelling: no spelling ({e})", file=sys.stderr)
        if exe := shutil.which("harper-ls"):
            try:
                self.harper = _Harper(exe)
            except (OSError, ValueError) as e:
                print(f"spelling: no grammar ({e})", file=sys.stderr)
        if self.spelling is None and self.harper is None:
            raise OSError("neither Enchant 2 nor harper-ls")

    @property
    def grammar(self):
        return self.harper is not None and self.harper.ok

    def check(self, line):
        out = self.spelling.check(line) if self.spelling is not None else []
        if self.grammar:
            words = {(a, b) for a, b, *_ in out}
            out += [g for g in self.harper.check(line) if (g[0], g[1]) not in words]
        return out

    def guesses(self, word):
        return self.spelling.guesses(word) if self.spelling is not None else []

    def learn(self, word):
        if self.spelling is not None:
            self.spelling.learn(word)


class _Worker:
    """The checker's thread: jobs (a function, then what's told its result on the main loop) one at a time, FIRST's
    before CHECK's. The checker's made on it, the first time it's asked for (backend: None, if there's none)."""
    FIRST, CHECK = 0, 1  # (a right-click's guesses, a word learnt; a page's lines)

    def __init__(self):
        self.jobs, self.checker, self.made, self.order = queue.PriorityQueue(), None, threading.Event(), 0
        threading.Thread(target=self._run, daemon=True, name="spelling").start()

    def _run(self):
        try:
            self.checker = _Mac() if sys.platform == "darwin" else _Linux()
        except (OSError, AttributeError) as e:
            print(f"spelling: not checked ({e})", file=sys.stderr)
        self.made.set()
        while True:
            _first, _order, fn, done = self.jobs.get()
            try:
                result = fn(self.checker) if self.checker is not None else None
            except Exception as e:  # (a thread: a line that can't be checked stays unchecked)
                print(f"spelling: {e!r}", file=sys.stderr)
                result = None
            if done is not None:
                GLib.idle_add(lambda done=done, result=result: done(result) and False)

    def submit(self, fn, done=None, first=False):
        self.order += 1  # (in its order, among its own lane's)
        self.jobs.put((self.FIRST if first else self.CHECK, self.order, fn, done))


_worker = None
_found = {}  # (a line's text, blanked: what's wrong in it, as check says)
_ignored = set()  # (Ignore: words, and grammar's (word, what's wrong), till Record's closed)
_learnt = set()  # (Learn: not underlined again, as it's learnt)
_guessed = {}  # (a word: its guesses, asked for once)


def worker():
    global _worker
    if _worker is None:
        _worker = _Worker()
    return _worker


def backend():
    """What checks, once it's made (None: there's none, or not yet)."""
    w = worker()
    return w.checker if w.made.is_set() else None


def about():
    """What checks, said for Settings ("Starting…" till it's made: when_made says when)."""
    w = worker()
    checker = w.checker
    if not w.made.is_set():
        return "Starting…"
    if sys.platform == "darwin":
        return "Spelling and grammar, as macOS checks them" if checker else "macOS's spell checker couldn't be loaded"
    if checker is None:
        return "Needs Enchant 2 and a dictionary (libenchant-2, hunspell-en-us) for spelling, harper-ls for grammar"
    if checker.spelling is None:
        return "Grammar with Harper; spelling needs Enchant 2 and a dictionary (libenchant-2, hunspell-en-us)"
    return "Spelling with Enchant, grammar with Harper" if checker.grammar else \
        "Spelling with Enchant; grammar needs harper-ls"


def when_made(fn):
    """fn() on the main loop once what checks is made (or there's none): at once if it is."""
    if worker().made.is_set():
        fn()
    else:
        worker().submit(lambda _checker: None, lambda _r: fn(), first=True)  # (it runs once it's made)


# ---------------------------------------------------------------- on a text view

class SpellCheck:
    """view's text checked as it's written (enabled(): whether to). The view draws the squiggles, in a layer over its
    text, by calling snapshot; rect_of(iter) is that character's box in the view."""

    def __init__(self, view, rect_of, enabled=lambda: True, skip=()):
        self.view, self.buf, self.enabled, self.rect_of = view, view.get_buffer(), enabled, rect_of
        self.skip = skip  # (the names of tags whose text isn't checked: a proposed edit's old text, struck out)
        self.colors = {}
        for kind, color in COLORS.items():
            rgba = self.colors[kind] = Gdk.RGBA()
            rgba.parse(color)
        self.waiting, self.asked = None, False  # (the pause before a check; a check out)
        self.shown = []  # (what's underlined: (start, end, kind, word, fixes, what's wrong))
        self.was = []  # (each line's text and results the last time it was drawn: kept on it as it's typed in)
        self.typing = None  # (the caret's offset after a word's letter typed: that word's not underlined yet)
        self.buf.connect_after("changed", lambda *_: (self.draw(), self.soon()))
        self.buf.connect_after("insert-text", self._typed)
        self.buf.connect("notify::cursor-position", self._caret)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._pressed)
        view.add_controller(click)
        acts = Gio.SimpleActionGroup()
        for name, fn in (("fix", self._fix), ("ignore", self._ignore), ("learn", self._learn)):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, v, fn=fn: fn(*json.loads(v.get_string())))
            acts.add_action(a)
        view.insert_action_group("spell", acts)
        self.menu = None
        view.connect("destroy", lambda *_: self.menu is not None and self.menu.unparent())
        worker()
        self.soon(0)

    def soon(self, ms=WAIT_MS):
        if self.waiting is not None:
            GLib.source_remove(self.waiting)
        self.waiting = GLib.timeout_add(ms, self._check)

    def refresh(self):
        """Checked or not, as enabled() says now."""
        self.draw()
        self.soon(0)

    def _check(self):
        """The lines not checked yet, sent to the checker; drawn again when it's done (or now, if there are none)."""
        self.waiting = None
        if not self.enabled() or self.asked:
            if self.asked:
                self.soon()
            return False
        text = self._text()
        todo = sorted({line for line in masked_lines(text) if line.strip() and line not in _found})
        if not todo:
            self.draw()
            return False
        self.asked = True

        def job(checker):
            return {line: checker.check(line) for line in todo}

        def done(found):
            self.asked = False
            if found:
                if len(_found) + len(found) > KEEP:
                    _found.clear()
                _found.update(found)
            if self.view.get_root() is not None:
                self.draw()
        worker().submit(job, done)
        return False

    def _text(self):
        """The buffer's text as its offsets count it (an edit's controls' anchors in it), the text of the tags in skip
        blanked to spaces."""
        text = self.buf.get_slice(*self.buf.get_bounds(), True)
        table = self.buf.get_tag_table()
        for name in self.skip:
            tag = table.lookup(name)
            if tag is None:
                continue
            it = self.buf.get_start_iter()
            while True:
                if it.starts_tag(tag):
                    a = it.get_offset()
                    it.forward_to_tag_toggle(tag)
                    b = it.get_offset()
                    text = text[:a] + "".join(c if c == "\n" else " " for c in text[a:b]) + text[b:]
                elif not it.forward_to_tag_toggle(tag):
                    break
        return text

    def draw(self):
        """What's underlined (shown), from what's known of each line (a line typed in since: what it had that's still
        there), and the view drawn again."""
        self.shown = []
        self.view.queue_draw()
        if not self.enabled() or backend() is None:
            self.was = []
            return
        text = self._text()
        lines, masked = text.split("\n"), masked_lines(text)
        was = self.was if len(self.was) == len(lines) else []
        now, pos = [], 0
        for n, (line, blank) in enumerate(zip(lines, masked)):
            found = _found.get(blank)
            if found is None and was and was[n][0] is not None:  # (not checked as it is: what's still there of before)
                old, results = was[n]
                shift = len(line) - len(old)
                found = []
                for a, b, *rest in results:  # (typed after it: where it was; before it: as far on as what's typed)
                    d = 0 if line[a:b] == old[a:b] else shift
                    if line[a + d:b + d] == old[a:b]:
                        found.append((a + d, b + d, *rest))
            now.append((line, found) if found is not None else (None, None))
            for a, b, kind, fixes, said in found or ():
                if kind == SPELLING:  # (the word itself, not the _ or * of its emphasis round it)
                    while a < b and not line[a].isalnum():
                        a += 1
                    while b > a and not line[b - 1].isalnum():
                        b -= 1
                word = line[a:b]
                if not word:
                    continue
                if word in _learnt or (word if kind == SPELLING else (word, said)) in _ignored:
                    continue
                if kind == SPELLING and self.typing == pos + b:  # (being typed: not yet)
                    continue
                self.shown.append((pos + a, pos + b, kind, word, fixes, said))
            pos += len(line) + 1
        self.was = now

    def snapshot(self, snapshot):
        """The squiggles in view (the view's ABOVE_TEXT layer): a wave under each, along each of the lines it's on,
        wrapped."""
        if not self.shown:
            return
        rect = self.view.get_visible_rect()
        first = self.view.get_line_at_y(rect.y)[0].get_offset()
        last = self.view.get_line_at_y(rect.y + rect.height)[0]
        last.forward_to_line_end()
        last = last.get_offset()
        height, length, width = WAVE
        for a, b, kind, *_ in self.shown:
            if b < first or a > last:
                continue
            it, end = self.buf.get_iter_at_offset(a), self.buf.get_iter_at_offset(b)
            if any(tag.get_property("invisible") for tag in it.get_tags()):
                continue  # (in markup not shown)
            path = Gsk.PathBuilder.new()
            while it.compare(end) < 0:
                stop = it.copy()
                if not self.view.forward_display_line_end(stop) or stop.compare(end) > 0:
                    stop = end.copy()
                if stop.compare(it) <= 0:
                    break
                back = stop.copy()
                back.backward_char()
                r0, r1 = self.rect_of(it), self.rect_of(back)
                x0, x1, y = r0.x, r1.x + r1.width, r0.y + r0.height - 1  # (just under the descenders)
                if x1 > x0:
                    path.move_to(x0, y)
                    x, up = x0, True  # (half a wave at a time, up then down: a smooth curve through each half)
                    while x < x1:
                        step = min(length / 2, x1 - x)
                        path.quad_to(x + step / 2, y + (-height if up else height), x + step, y)
                        x, up = x + step, not up
                if stop.compare(end) >= 0 or not self.view.forward_display_line(stop):
                    break
                it = stop
            snapshot.append_stroke(path.to_path(), Gsk.Stroke.new(width), self.colors[kind])

    def _typed(self, _buf, at, text, _n):
        was = self.typing
        self.typing = at.get_offset() if len(text) == 1 and (text.isalnum() or text in "'’") else None
        if was is not None and self.typing is None:  # (a space or a stop after a word: it's underlined if it's wrong)
            GLib.idle_add(self._drawn_unless_typing)

    def _caret(self, *_):
        if self.typing is not None and self.buf.get_property("cursor-position") != self.typing:
            self.typing = None
            GLib.idle_add(self._drawn_unless_typing)  # (left the word: it's underlined if it's wrong)

    def _drawn_unless_typing(self):
        if self.typing is None and self.view.get_root() is not None:  # (not a letter typed after it: its next)
            self.draw()
        return False

    # ---- right-click on a squiggle
    def _pressed(self, gesture, _n, x, y):
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.view.get_iter_at_location(bx, by)
        at = it.get_offset() if ok else -1
        hit = next((s for s in self.shown if s[0] <= at < s[1]), None)
        if hit is None:
            return  # (the text view's own menu)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if hit[4] is not None:
            return self._menu(hit, hit[4], x, y)
        word = hit[3]
        if word in _guessed:
            return self._menu(hit, _guessed[word], x, y)

        def done(guesses):  # (a word's guesses: the spell server's, a moment)
            _guessed[word] = guesses or []
            if self.view.get_root() is not None:
                self._menu(hit, _guessed[word], x, y)
        worker().submit(lambda checker: checker.guesses(word), done, first=True)

    def _menu(self, hit, fixes, x, y):
        """The menu a right-click on a squiggle shows: the fixes, Ignore, Learn (a word), the clipboard's."""
        a, b, kind, word, _fixes, said = hit
        menu, first = Gio.Menu(), Gio.Menu()
        for fix in fixes:
            first.append_item(self._item(fix, "spell.fix", [a, b, word, fix]))
        if fixes:
            menu.append_section(said or None, first)  # (what's wrong, grammar's: its heading)
        keep = Gio.Menu()
        keep.append_item(self._item("Ignore Spelling" if kind == SPELLING else "Ignore", "spell.ignore", [word, kind, said]))
        if kind == SPELLING:
            keep.append_item(self._item("Learn Spelling", "spell.learn", [word]))
        menu.append_section(None if fixes else said or "No Guesses Found", keep)
        edit = Gio.Menu()
        for label, action in (("Cut", "clipboard.cut"), (wording.say("common.copy"), "clipboard.copy"),
                              ("Paste", "clipboard.paste")):
            edit.append(label, action)  # (the text view's own)
        menu.append_section(None, edit)
        if self.menu is not None:
            self.menu.unparent()
        self.menu = Gtk.PopoverMenu(menu_model=menu, has_arrow=False, halign=Gtk.Align.START)
        self.menu.set_parent(self.view)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.menu.set_pointing_to(rect)
        self.menu.popup()

    @staticmethod
    def _item(label, action, args):
        item = Gio.MenuItem.new(label, None)
        item.set_action_and_target_value(action, GLib.Variant.new_string(json.dumps(args)))
        return item

    def _fix(self, a, b, word, fix):
        """word (still there, from a to b) replaced with fix, as one edit, to undo."""
        start, end = self.buf.get_iter_at_offset(a), self.buf.get_iter_at_offset(b)
        if self.buf.get_slice(start, end, True) != word:
            return
        self.buf.begin_user_action()
        if self.buf.delete_interactive(start, end, self.view.get_editable()):
            self.buf.insert_interactive(start, fix, -1, self.view.get_editable())
        self.buf.end_user_action()

    def _ignore(self, word, kind, said):
        _ignored.add(word if kind == SPELLING else (word, said))
        self.draw()

    def _learn(self, word):
        _learnt.add(word)
        worker().submit(lambda checker: checker.learn(word), first=True)
        self.draw()
