#!/usr/bin/env python3
"""Tests for record.py and lookup.py (⌘K's engine, as Record uses it). Run through tests/run.sh, or on their own:

    python3 tests/test_record.py            # all of them
    python3 tests/test_record.py -k diary   # those whose name has "diary" in it
    python3 tests/test_record.py --shots DIR

Drives a real Record window against tests/fake_record_claude.py, in a throwaway data folder (and Baude settings of
its own), reading a throwaway chat whose folder is a throwaway git repository (files, a branch, a
Kubernetes manifest, a Signal glossary). Nothing touches your Records, your settings, your chats, your clusters or
your containers.
"""
import argparse
import gc
import json
import math
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import weakref

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TMP = os.path.realpath(tempfile.mkdtemp(prefix="record-test-"))
os.environ["RECORD_DATA"] = os.path.join(TMP, "record-data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")  # (Baude's settings.json)
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(TMP, "claude-config")
os.environ["KUBECONFIG"] = os.path.join(TMP, "no-kubeconfig")
os.environ["DOCKER_HOST"] = "unix://" + os.path.join(TMP, "no-docker.sock")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg-data")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "xdg-cache")  # (what's kept of GitHub's lists: test_github_lists)
os.environ["FAKE_RECORD_LOG"] = os.path.join(TMP, "fake.log")
os.environ["RECORD_NOUNS"] = "off"  # (no NLTK fetched by uv for the chats read here: test_unexplained_nouns)
os.environ["BAUDE_GH"] = ""  # (no issue filed on GitHub for the feedback saved here)
os.environ["BAUDE_KEEP_FILING"] = "0"  # (nor a note going up by itself, to a stand-in gh, mid-test)
os.environ["BAUDE_UPDATE"] = "0"  # (no git fetched, no restart onto a new version)
FAKE = os.path.join(TMP, "fake-claude")
with open(FAKE, "w") as f:
    f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(HERE, "fake_record_claude.py"))} "$@"\n')
os.chmod(FAKE, 0o755)
os.environ["RECORD_CLAUDE"] = FAKE

# the chat's folder: a repository with files, a branch and a manifest, and the glossary Baude's Signal keeps
REPO = os.path.join(TMP, "shop")
FILES = {"src/relay_jobs.py": "class RelayRunner:\n    def run(self):\n        pass\n",
         "deploy/orders-api.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: orders-api\n",
         "README.md": "# shop\n", "Dockerfile": "FROM scratch\n",
         **{f"svc/{s}/compose.yaml": "services: {}\n" for s in ("cart", "pay", "ship", "mail")}}
for rel, text in FILES.items():
    os.makedirs(os.path.dirname(os.path.join(REPO, rel)), exist_ok=True)
    with open(os.path.join(REPO, rel), "w") as f:
        f.write(text)
GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "init.defaultBranch=main"]
for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "shop"], ["branch", "feature/fast-lists"]):
    subprocess.run(GIT + args, cwd=REPO, check=True, capture_output=True)
os.makedirs(os.path.join(REPO, ".baude"))
GLOSSARY = os.path.join(REPO, ".baude", "signal.db")
db = sqlite3.connect(GLOSSARY)
import lookup  # noqa: E402
db.executescript(lookup.REF_SCHEMA)
db.execute("INSERT INTO entity (id, kind, canonical, status, data) VALUES (1, 'symbol', 'RelayRunner', 'resolved', "
           "'{}')")
db.execute("INSERT INTO alias (surface, entity_id) VALUES ('RelayRunner', 1)")
db.execute("INSERT INTO location (entity_id, role, path, line) VALUES (1, 'defined', 'src/relay_jobs.py', 1)")
db.commit()
db.close()

# the chat: a transcript as Claude Code writes them
CHATS = os.path.join(TMP, "claude-config", "projects", "-shop")
os.makedirs(CHATS)


def transcript(name, replies):
    path = os.path.join(CHATS, f"{name}.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps({"type": "user", "cwd": REPO, "uuid": "u1",
                            "message": {"role": "user", "content": "How do the jobs run?"}}) + "\n")
        for i, text in enumerate(replies):
            f.write(json.dumps({"type": "assistant", "cwd": REPO, "uuid": f"a{i}",
                                "message": {"id": f"m{i}", "role": "assistant",
                                            "content": [{"type": "text", "text": text}]}}) + "\n")
    return path


CHAT = transcript("chat-1", [
    "`RelayRunner` in `relay_jobs.py` runs each job; the golden files sit beside it. See "
    "[the docs](https://example.com/docs).",
    "Merge feature/fast-lists once orders-api is green."])
OTHER = transcript("chat-2", ["Nothing to see."])

import record  # noqa: E402
import widgets  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

widgets.SLIDE_MS = 0  # (a side bar opens and closes at once here: test_side_bars_slide has it slide)
widgets.Lightbox.FLY_MS = 0  # (the large view opens and closes at once here: test_lightbox_flies has it fly)

SHOTS = None


def pump(seconds=0.05):
    end = time.monotonic() + seconds
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.002)


def walk(widget):
    """widget and all its descendants."""
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def wait_for(cond, what, timeout=10):
    end = time.monotonic() + timeout
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what() if callable(what) else what}")


def shot(widget, name):
    if not SHOTS:
        return
    pump(0.2)
    p = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    p.snapshot(snap, widget.get_width(), widget.get_height())
    node = snap.to_node()
    if node is not None:
        widget.get_native().get_renderer().render_texture(node, None).save_to_png(os.path.join(SHOTS, name + ".png"))


def asked():
    """What the fake claude was asked, oldest first."""
    if not os.path.exists(os.environ["FAKE_RECORD_LOG"]):
        return []
    return [json.loads(line) for line in open(os.environ["FAKE_RECORD_LOG"])]


def catalogued(win):
    space = win.space()
    assert space is not None, "a space for the chat's folder"
    space.refresh()
    wait_for(lambda: not space.busy and space.find("relay_jobs.py") is not None, "the folder catalogued")
    pump(0.6)  # (the reader's links follow, on a timer)
    return space


class App:
    """One Record for the whole run, its data in TMP: saved as they were before diaries (a glossary)."""

    def __init__(self):
        os.makedirs(os.environ["RECORD_DATA"], exist_ok=True)
        with open(os.path.join(os.environ["RECORD_DATA"], "records.json"), "w") as f:
            json.dump({"records": [
                {"id": "a", "name": "Systems for me", "created": 0, "chat": {"path": CHAT, "title": "Jobs", "cwd": REPO},
                 "glossary": [{"term": "function", "meaning": "a recipe"}, {"term": "JSON", "meaning": ""}],
                 "messages": [{"role": "user", "text": "one"}, {"role": "record", "text": "two"},
                              {"role": "user", "text": "three"}, {"role": "record", "text": "four"}]},
                {"id": "b", "name": "Biology reading", "created": 0, "glossary": [], "chat": None, "messages": []}]}, f)
        self.app = record.App()
        self.app.register(None)
        self.app.activate()
        wait_for(lambda: self.app.get_active_window() is not None, "the window")

    @property
    def win(self):
        return self.app.win  # (made again on a restart)


def by_id(win, tid):
    return next(t for t in win.store.notebooks if t["id"] == tid)


def select(win, tid):
    i = 0
    while (row := win.notebook_list.get_row_at_index(i)) is not None and row.notebook["id"] != tid:
        i += 1
    win.notebook_list.select_row(row)
    pump(0.1)


# ---------------------------------------------------------------- lookup, on its own

def test_lookup_folder():
    """lookup.Folder: the catalogue (files, branches, a manifest's objects) made in a thread of its own, and
    Signal's glossary read without being written to; SearchSpace ranks them as ⌘K does and finds them in prose."""
    before = os.stat(GLOSSARY).st_mtime_ns
    space = lookup.SearchSpace(lookup.Folder(REPO), GLib.timeout_add)
    done = []
    space.refresh(lambda: done.append(True))
    assert space.busy, "catalogued off the main loop"
    wait_for(lambda: done, "the catalogue")
    assert space.search("relay")[0][0]["surface"] in ("relay_jobs.py", "RelayRunner"), space.search("relay")[0][:3]
    assert space.find("relay_jobs.py")["path"] == os.path.join(REPO, "src/relay_jobs.py")
    # every file: one whose name four share, or with no extension, by its path (a name few share: not twice)
    found = [it["surface"] for it in space.search("compose.yaml")[0]]
    assert sorted(found) == [f"svc/{s}/compose.yaml" for s in ("cart", "mail", "pay", "ship")], found
    assert space.find("svc/pay/compose.yaml")["path"] == os.path.join(REPO, "svc/pay/compose.yaml")
    assert space.find("Dockerfile")["path"] == os.path.join(REPO, "Dockerfile")
    assert space.find("src/relay_jobs.py") is None, "relay_jobs.py is listed by its name"
    assert space.find("feature/fast-lists")["kind"] == "branch"
    assert space.find("orders-api")["kind"] == "k8s", "the manifest's Deployment"
    runner = space.find("RelayRunner")
    assert runner and runner["entry"] is None and runner["path"] == os.path.join(REPO, "src/relay_jobs.py") and \
        runner["line"] == 1, "a name Signal looked up, from its glossary"
    assert space.search("zzz nothing") == ([], 0)
    said = [it["surface"] for it in space.mentions("`RelayRunner` and relay_jobs.py, not README or a run;\n"
                                                   "```\nfeature/fast-lists\n```")]
    assert said == ["RelayRunner", "relay_jobs.py"], ("in order; code blocks left out", said)
    assert os.stat(GLOSSARY).st_mtime_ns == before and not os.path.exists(os.path.join(REPO, ".baude", ".gitignore")), \
        "the glossary only read"
    assert lookup.Folder.for_folder(REPO) is lookup.Folder.for_folder(REPO + "/"), "one per folder"


def test_baude_uses_lookup():
    """Baude's Signal is a lookup.Names, and its ⌘K a lookup.SearchSpace: the engine is one, in lookup.py."""
    import baude
    assert issubclass(baude.Signal, lookup.Names) and issubclass(baude.SearchSpace, lookup.SearchSpace)
    assert baude.catalogue_hits is lookup.catalogue_hits and baude.ref_palette_rank is lookup.ref_palette_rank


# ---------------------------------------------------------------- the window

def test_glossary_becomes_diary(app):
    """One from before diaries: its glossary is Home's list, a term with no meaning under To learn; its name
    as it was typed."""
    win = app.win
    t = by_id(win, "a")
    assert "glossary" not in t and t["pages"]["Home"].startswith("- **function**: a recipe") and \
        "## To learn\n- JSON" in t["pages"]["Home"], t["pages"]
    assert [record.full_name(x) for x in win.store.notebooks] == ["Systems for me", "Biology reading"]


def test_reader_links_names(app):
    """The chat's names that its folder has are links in the reader (in backticks and in prose); one clicked opens
    where it is, and the pointer over one says what it is, with To notes, which writes it into the notes as
    "- @name: "."""
    win = app.win
    select(win, "a")
    catalogued(win)
    markup = [r.prose.get_label() for r in win.reader.rows if r.item["role"] == "assistant"]
    assert 'href="ref:RelayRunner"' in markup[0] and 'href="ref:relay_jobs.py"' in markup[0], markup[0]
    assert 'href="https://example.com/docs"' in markup[0] and "<b>" not in markup[0], "a web link stays one"
    assert 'href="ref:feature%2Ffast-lists"' in markup[1] and 'href="ref:orders-api"' in markup[1], markup[1]
    shot(win, "record-1-reader")
    assert win.follow("ref:relay_jobs.py", from_chat=True)
    assert win.tabs.current() is win.code_page and win.card is None, "clicked: a code tab, no card"
    assert not win.follow("https://example.com/docs"), "a web link opens as links do"
    win.hover_ref("relay_jobs.py", from_chat=True)
    assert "relay_jobs.py: File · src/relay_jobs.py" in win.card.get_title() and \
        win.card.get_button_label() == "To notes"
    win.card.emit("button-clicked")
    assert isinstance(win.tabs.current(), record.DiaryEditor), "To notes: the page of notes shown"
    assert win.diary.text().endswith("- [[@relay_jobs.py]]: "), win.diary.text()
    assert by_id(win, "a")["pages"]["Home"].endswith("- [[@relay_jobs.py]]: "), "saved"


class Press:
    """A press on the reader (its Gtk.GestureDrag's, as Reader._pick_begin sees it), at (x, y), with these keys."""

    def __init__(self, x, y, state=0, button=Gdk.BUTTON_PRIMARY):
        self.x, self.y, self.state, self.button, self.got = x, y, state, button, None

    def get_current_button(self):
        return self.button

    def get_current_event_state(self):
        return self.state

    def set_state(self, state):
        self.got = state

    def get_start_point(self):
        return True, self.x, self.y


def test_notes_code_block_is_one_card(app):
    """A code block in the notes is one card, its fences' lines its top and bottom: drawn the width of the text, the code
    set into it in monospace. Its ``` shows only with the caret on it (Settings → Markup visibility), and a fence not yet closed
    takes the card to the end of the page."""
    win = app.win
    select(win, "b")
    d = win.open_note("Code")
    text = "Before\n\n```yaml\napps:\n  - name: base\n```\n\nAfter\n\n```\nnot closed yet"
    tags = d.buf.get_tag_table()
    at = lambda name, line, column=0: d.buf.get_iter_at_line_offset(line, column)[1].has_tag(tags.lookup(name))  # noqa: E731
    try:
        d.buf.set_text(text)
        d.buf.place_cursor(d.buf.get_start_iter())
        d._highlight()
        assert d.view.blocks == [[2, 5], [9, 10]], d.view.blocks
        for line in (2, 3, 4, 5):
            assert at("pre", line), f"line {line} is in the block, its fences too"
        assert not at("pre", 0) and not at("pre", 7), "what's outside isn't"
        assert at("hidden", 2) and at("hidden", 5) and not at("hidden", 3), "the fences unseen, the code not"
        assert not at("verbatim", 2) and not at("verbatim", 5), "a fence isn't drawn as inline code"
        d.buf.place_cursor(d.buf.get_iter_at_line(5)[1])
        d._highlight()
        assert not at("hidden", 5) and at("mark", 5) and at("hidden", 2), "the ``` shown with the caret on it"
        d.view.queue_draw()
        pump(0.2)  # (its card drawn: nothing to go wrong in the layer)
        fill = d.view.get_color()
        assert fill.alpha > 0
        d.buf.set_text("```\nonly")  # (still being written)
        d._highlight()
        assert d.view.blocks == [[0, 1]]
        d.buf.set_text("no code")
        d._highlight()
        assert d.view.blocks == []
    finally:
        win.tabs.close(("note", "Code"))
        by_id(win, "b")["pages"].pop("Code", None)


def test_read_code_block_height(app):
    """Read draws a reply in Baude's chat's scroller (widgets.markdown_scroller): a code block in a chat taller than
    the window is its own height, not squashed to a line (the viewport at its child's natural height). The
    notebooks' column is the pages' width, a notebook a line as tall as a page's, no subtitle."""
    win = app.win
    select(win, "a")
    r = win.reader
    assert r.scroll.get_child().get_vscroll_policy() == Gtk.ScrollablePolicy.NATURAL
    assert win.chat.scroll.get_child().get_vscroll_policy() == Gtk.ScrollablePolicy.NATURAL
    assert win.left_slide.fixed.width == record.DiaryEditor.PAGES_WIDTH, "till it's dragged: test_notebooks_column_drags"
    bar = next(w for w in walk(win.books) if isinstance(w, record.Adw.HeaderBar))
    assert not bar.get_show_title(), "no title: it didn't fit"
    assert not win.books.get_visible() or win.books.get_width() == record.DiaryEditor.PAGES_WIDTH, \
        ("held at it, its bar's title and a long name notwithstanding", win.books.get_width())
    row = win.notebook_list.get_row_at_index(0)
    assert not hasattr(row, "sub") and isinstance(row.name, Gtk.Label) and not row.name.has_css_class("heading")


NOTES_OUTLINE = """intro
# A
a text

## A1
## A2
a2 text
### A2x
## A3

# B
b text
```
# not a heading
```"""


def test_heading_swap_stays_among_siblings():
    """A heading swaps with its sibling, all under it going along: never out of its parent, never into a sibling's family,
    and the blank lines between them stay put. A # in a code fence isn't a heading."""
    lines = NOTES_OUTLINE.split("\n")
    levels = record.heading_levels(lines)
    assert levels == [0, 1, 0, 0, 2, 2, 0, 3, 2, 0, 1, 0, 0, 0, 0], levels
    at = {name: lines.index(("#" * n) + " " + name) for name, n in (("A", 1), ("A1", 2), ("A2", 2), ("A2x", 3), ("A3", 2), ("B", 1))}

    def swap(name, by):
        done = record.swap_with_sibling(lines, at[name], by)
        if done is None:
            return None
        new, one, three, moved = done
        whole = lines[:one] + new + lines[three:]
        assert whole[moved].endswith(" " + name), (whole, moved)
        return whole
    assert swap("A1", -1) is None, "not above its parent"
    assert swap("A3", 1) is None, "not out into the next parent"
    assert swap("A2x", -1) is None and swap("A2x", 1) is None, "an only child"
    assert swap("B", 1) is None and swap("A", -1) is None, "the first and the last"
    assert swap("A2", -1) == "intro\n# A\na text\n\n## A2\na2 text\n### A2x\n## A1\n## A3\n\n# B\nb text\n```\n# not a heading\n```".split("\n")
    assert swap("A1", 1) == swap("A2", -1), "the same swap, from the other side: past A2 with its child, not into it"
    assert swap("A2", 1) == "intro\n# A\na text\n\n## A1\n## A3\n## A2\na2 text\n### A2x\n\n# B\nb text\n```\n# not a heading\n```".split("\n")
    assert swap("A", 1) == "intro\n# B\nb text\n```\n# not a heading\n```\n\n# A\na text\n\n## A1\n## A2\na2 text\n### A2x\n## A3".split("\n")
    assert swap("B", -1) == swap("A", 1)
    skipped = ["# P", "### deep", "## mid"]  # (a level skipped: not the same level, so no sibling)
    assert record.swap_with_sibling(skipped, 1, 1) is None and record.swap_with_sibling(skipped, 2, -1) is None


def test_enter_in_the_title_goes_to_the_text(app):
    """Enter in a page's title: it's renamed, and the caret's in its text, from the top. A name that's taken stays in
    the title, to try another. (#53)"""
    win = app.win
    select(win, "a")
    d = win.open_note("Enter here")
    d.buf.set_text("The first line\nThe second line")
    d.buf.place_cursor(d.buf.get_end_iter())
    d.title.grab_focus()
    d.title.set_text("Entered")
    pump(0.1)
    d.title.emit("activate")
    pump(0.1)
    assert win.get_focus() is d.view, win.get_focus()
    assert record.widgets.caret_of(d.view) == [0, 0], record.widgets.caret_of(d.view)
    assert d.page == "Entered" and d.title.get_text() == "Entered" and d.text() == "The first line\nThe second line"
    d.title.grab_focus()
    pump(0.1)
    d.title.emit("activate")
    pump(0.1)
    assert win.get_focus() is d.view, "no change to the name: on to the text all the same"
    other = win.open_note("Taken")
    other.title.grab_focus()
    other.title.set_text("Entered")
    pump(0.1)
    other.title.emit("activate")
    pump(0.1)
    assert other.title.get_text() == "Taken" and win.get_focus().is_ancestor(other.title), "the name's taken: still in the title"
    home = win.open_note(record.HOME)
    home.title.grab_focus()
    pump(0.1)
    home.title.emit("activate")
    pump(0.1)
    assert win.get_focus() is home.view, "Home, whose name can't change: on to its text too"


def test_word_steps_reach_what_ends_a_line():
    """Option and an arrow stops after each word, and after each mark that follows one where nothing else does: a closing
    }, a full stop at a line's end. Marks inside a word or leading one aren't stops, and what's unseen isn't there. (#61)"""
    def stops(text, at=0, forward=True, hidden=()):
        found = []
        while True:
            to = widgets.word_stop(text, at, forward, lambda i: i in hidden)
            if to == at:
                return found
            found.append(text[to:] if not forward else text[:to])
            at = to

    line = "Relevant thread is @{This AND that}.\n\nThe next"
    assert stops(line) == ["Relevant", "Relevant thread", "Relevant thread is", "Relevant thread is @{This",
                           "Relevant thread is @{This AND", "Relevant thread is @{This AND that",
                           "Relevant thread is @{This AND that}", "Relevant thread is @{This AND that}.",
                           line[:-5], line], stops(line)
    back = stops("is @{a b}.\n\nNext", 16, False)
    assert back == ["Next", ".\n\nNext", "}.\n\nNext", "b}.\n\nNext", "a b}.\n\nNext", "@{a b}.\n\nNext",
                    "is @{a b}.\n\nNext"], back
    assert stops("orders-api a.b don't") == ["orders", "orders-api", "orders-api a", "orders-api a.b",
                                             "orders-api a.b don't"], stops("orders-api a.b don't")
    assert stops("wait... **now**") == ["wait", "wait...", "wait... **now", "wait... **now**"], stops("wait... **now**")
    assert stops("no marks") == ["no", "no marks"] and stops("") == [] and stops("  \n ") == ["  \n "]

    # {, } and ]] unseen: the stops as if they weren't there, each on the far side of them
    text = "see @{a b}. And"
    unseen = {text.index("{"), text.index("}")}
    assert stops(text, hidden=unseen) == ["see", "see @{a", "see @{a b}", "see @{a b}.", "see @{a b}. And"], stops(text, hidden=unseen)
    back = stops(text, len(text), False, unseen)
    assert back[:3] == ["And", ". And", "b}. And"] and back[-1] == text, back


def test_word_steps_in_the_notes_and_the_box(app):
    """Real keys: Shift+Option+Right across a line selects to after its }, then after its full stop, and no further till
    pressed again; Option+Left comes back. The Record's box steps the same way. (#61)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    d = win.open_note("Word steps")
    line = "Relevant thread is @{This AND that}."
    d.buf.set_text(line + "\n\nThe next paragraph")
    d.buf.place_cursor(d.buf.get_start_iter())
    d.view.grab_focus()
    pump(0.2)
    hand = mouse.Mouse(win)

    def selected(buf):
        return buf.get_text(*buf.get_selection_bounds(), True) if buf.get_has_selection() else ""

    def right(n, flags=mouse.SHIFT | mouse.OPTION):
        for _ in range(n):
            hand.key(*mouse.RIGHT_ARROW, flags=flags)
        pump(0.05)

    right(6)
    assert selected(d.buf) == "Relevant thread is @{This AND that", selected(d.buf)
    right(1)
    assert selected(d.buf) == line[:-1], selected(d.buf)
    right(1)
    assert selected(d.buf) == line, selected(d.buf)
    right(1)
    assert selected(d.buf) == line + "\n\nThe", "and only then on to the next line's first word"
    d.buf.place_cursor(d.buf.get_iter_at_offset(len(line)))
    pump(0.05)
    hand.key(*mouse.LEFT_ARROW, flags=mouse.OPTION)
    pump(0.05)
    caret = d.buf.get_iter_at_mark(d.buf.get_insert()).get_offset()
    assert caret == len(line) - 1 and not d.buf.get_has_selection(), caret

    box = win.chat.composer
    box.get_buffer().set_text("One @{two}.")
    box.get_buffer().place_cursor(box.get_buffer().get_start_iter())
    box.grab_focus()
    pump(0.1)
    right(3)
    assert selected(box.get_buffer()) == "One @{two}", selected(box.get_buffer())
    box.get_buffer().set_text("")
    d.buf.set_text("")
    win.tabs.close(("note", "Word steps"))


def test_enter_after_a_sub_page_link_goes_on_with_the_list(app):
    """Real keys: +Ideas typed in a list item, then Enter: it becomes the link, and Enter is the Enter it would have been,
    the list's next item (a number one more, a box not ticked), not a line outside the list. Off a list, a new line; a
    space after one is still just a space, and Enter then goes on with the list as ever. (#68)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    d = win.open_note("Plus links")
    hand = mouse.Mouse(win)
    enter = (36, "\r")
    try:
        for typed, wanted in (("- item +Ideas", "- item [[+Ideas]]\n- "),
                              ("- +Ideas", "- [[+Ideas]]\n- "),
                              ("1. one +More", "1. one [[+More]]\n2. "),
                              ("- [ ] do +Task", "- [ ] do [[+Task]]\n- [ ] "),
                              ("plain +Other", "plain [[+Other]]\n")):
            d.buf.set_text(typed)
            d.buf.place_cursor(d.buf.get_end_iter())
            d.view.grab_focus()
            pump(0.1)
            hand.key(*enter)
            pump(0.1)
            assert d.text() == wanted, (typed, d.text())
            assert d.buf.get_iter_at_mark(d.buf.get_insert()).is_end(), "the caret where the next's typed"
        d.buf.set_text("- item +Ideas")
        d.buf.place_cursor(d.buf.get_end_iter())
        pump(0.1)
        hand.key(49, " ")
        pump(0.1)
        assert d.text() == "- item [[+Ideas]] ", d.text()
        hand.key(*enter)
        pump(0.1)
        assert d.text() == "- item [[+Ideas]] \n- ", d.text()
    finally:
        d.buf.set_text("")
        win.tabs.close(("note", "Plus links"))


def test_selections_dont_split_links():
    """What's selected takes a link's unseen marks with what it takes of its name, never leaving [[ or ]] behind; one that
    runs out of it part way takes it all; a part of a name alone, or text beside a link, is left as it is. (#75)"""
    text = "see [[Some page]] and more"
    a, name, name_end, b = text.index("[["), text.index("Some"), text.index("]]"), text.index("]]") + 2

    def picks(start, end, source=text):
        s, e = record.whole_links(source, start, end)
        return source[s:e]

    assert picks(name, name_end) == "[[Some page]]", "its name: its marks too"
    assert picks(0, name_end) == "see [[Some page]]", "ending where the name does: ]] with it"
    assert picks(name, len(text)) == "[[Some page]] and more", "starting where the name does: [[ with it"
    assert picks(0, name) == "see ", "ending where the name starts: no [[ alone"
    assert picks(0, a + 1) == "see "
    assert picks(name_end, len(text)) == " and more", "starting where the name ends: no ]] alone"
    assert picks(name_end + 1, len(text)) == " and more"
    assert picks(name + 2, name + 6) == "me p", "a part of a name alone: as it is"
    assert picks(name + 2, len(text)) == "[[Some page]] and more", "out of it part way: all of it"
    assert picks(0, name + 2) == "see [[Some page]]"
    assert picks(0, 3) == "see" and picks(b, len(text)) == " and more", "beside it"
    items = "- one\n- [[Some page]]\n- three"
    assert record.whole_links(items, items.index("\n"), items.index("]]")) == (items.index("\n"), items.index("]]") + 2)
    assert picks(0, 8, "[[+Sub]] tail") == "[[+Sub]]" and picks(2, 8, "[[+Sub]] tail") == "[[+Sub]]"
    label = "x [[@o/r#12|#12]] y"
    assert picks(label.index("#12]"), label.index("]]"), label) == "[[@o/r#12|#12]]", "a label's the name"
    md = "a [text](https://x.y/z) b"
    assert picks(md.index("text"), md.index("]"), md) == "[text](https://x.y/z)", "a link's address with it"
    ref = "at @{a name} here"
    assert picks(ref.index("a name"), ref.index("}"), ref) == "@{a name}"
    assert picks(0, 0) == "" and picks(5, 5) == ""


def test_cutting_lines_keeps_links_whole(app):
    """Real keys on a selection as a drag leaves it, from the end of a line (before its link's unseen ]]) up to the line
    before: nothing's left of the link, and the line before is as it was. Typing over one, the same. (#75)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    d = win.open_note("Whole links")
    hand = mouse.Mouse(win)
    backspace = (51, "\x7f")
    try:
        d.view.grab_focus()
        for text, start, end, wanted in (
                ("- one\n- [[Some page]]\n- three", 5, "]]", "- one\n- three"),
                ("- [[Only link]]\n- two", 0, "]]", "\n- two"),
                ("[[A]] and [[B]]", "A", "B", ""),):
            d.buf.set_text(text)
            s = start if isinstance(start, int) else text.index(start)
            e = text.rindex(end) if end == "]]" else text.index(end)
            if start == "A":  # (from inside the first's name to inside the second's: both whole)
                e += 1
            d.buf.select_range(d.buf.get_iter_at_offset(e), d.buf.get_iter_at_offset(s))
            pump(0.1)
            hand.key(*backspace)
            pump(0.1)
            assert d.text() == wanted, (text, d.text())
            assert "[[" not in d.text() and "]]" not in d.text(), d.text()
        d.buf.set_text("before [[Some page]] after")
        d.buf.select_range(d.buf.get_iter_at_offset(0), d.buf.get_iter_at_offset(d.text().index("]]")))
        pump(0.1)
        hand.type("x")
        pump(0.1)
        assert d.text() == "x after", d.text()
    finally:
        d.buf.set_text("")
        win.tabs.close(("note", "Whole links"))


def test_list_item_swaps_with_its_sibling():
    """A list item goes past its sibling with all under it, never out of its list or into a deeper level; numbers stay in
    order; a code block's lines aren't items. (#74)"""
    def swap(text, at, by):
        lines = text.split("\n")
        swapped = widgets.swap_list_item(lines, at, by)
        return None if swapped is None else ("\n".join(lines[:swapped[1]] + swapped[0] + lines[swapped[2]:]), swapped[3])

    assert swap("- a\n- b\n- c", 1, -1) == ("- b\n- a\n- c", 0)
    assert swap("- a\n- b\n- c", 1, 1) == ("- a\n- c\n- b", 2)
    assert swap("- a\n- b", 0, -1) is None and swap("- a\n- b", 1, 1) is None, "nothing that way"
    assert swap("- a\n  - a1\n  more\n- b", 0, 1) == ("- b\n- a\n  - a1\n  more", 1), "all under it goes along"
    assert swap("- a\n  - a1\n- b\n  - b1", 2, -1) == ("- b\n  - b1\n- a\n  - a1", 0)
    assert swap("- a\n  - a1\n  - a2\n- b", 1, 1) == ("- a\n  - a2\n  - a1\n- b", 2), "among its own level"
    assert swap("- a\n  - a1\n- b", 1, 1) is None and swap("- a\n- b\n  - b1", 2, -1) is None, "never out of its parent"
    assert swap("- a\n  more of a\n- b", 1, 1) == ("- b\n- a\n  more of a", 2), "from a line going on with it"
    assert swap("1. a\n2. b\n3. c", 0, 1) == ("1. b\n2. a\n3. c", 1), "numbers in order"
    assert swap("- [ ] a\n- [x] b", 0, 1) == ("- [x] b\n- [ ] a", 1), "its box with it"
    assert swap("- a\n\n- b", 0, 1) is None and swap("- a\ntext\n- b", 2, -1) is None, "not past what isn't the list"
    assert swap("plain\nlines", 0, 1) is None
    assert swap("```\n- a\n- b\n```", 1, 1) is None, "a code block's lines"


def test_list_items_move_with_real_keys(app):
    """Real keys: Option and Up or Down on a list item moves it, the caret going along; undone in one; elsewhere it's what
    it was, and a heading still swaps. The Record's box does it too. (#74)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    d = win.open_note("Moving items")
    hand = mouse.Mouse(win)

    def press(arrow):
        hand.key(*arrow, flags=mouse.OPTION)
        pump(0.1)

    try:
        d.buf.set_text("- one\n- two\n  - under two\n- three\n\nprose\n\n# First\n\n# Second")
        d.buf.place_cursor(d.buf.get_iter_at_line_offset(1, 3)[1])
        d.view.grab_focus()
        pump(0.2)
        press(mouse.DOWN_ARROW)
        assert d.text().startswith("- one\n- three\n- two\n  - under two\n\nprose"), d.text()
        caret = d.buf.get_iter_at_mark(d.buf.get_insert())
        assert (caret.get_line(), caret.get_line_offset()) == (2, 3), "the caret goes along"
        press(mouse.DOWN_ARROW)
        assert d.text().startswith("- one\n- three\n- two\n  - under two\n\nprose"), "the end of the list: nothing"
        press(mouse.UP_ARROW)
        press(mouse.UP_ARROW)
        assert d.text().startswith("- two\n  - under two\n- one\n- three\n\nprose"), d.text()
        d.buf.undo()
        pump(0.1)
        assert d.text().startswith("- one\n- two\n  - under two\n- three\n\nprose"), "one undo a move: " + d.text()
        d.buf.place_cursor(d.buf.get_iter_at_line(5)[1])
        was = d.text()
        pump(0.1)
        press(mouse.DOWN_ARROW)
        assert d.text() == was, "off a list: not this"
        d.buf.place_cursor(d.buf.get_iter_at_line(7)[1])
        pump(0.1)
        press(mouse.DOWN_ARROW)
        assert d.text().endswith("# Second\n\n# First"), "a heading still swaps: " + d.text()

        box = win.chat.composer
        box.get_buffer().set_text("- a\n- b")
        box.get_buffer().place_cursor(box.get_buffer().get_start_iter())
        box.grab_focus()
        pump(0.2)
        press(mouse.DOWN_ARROW)
        typed = widgets.typed_text(box)
        assert typed.split("\n")[0].endswith("b") and typed.split("\n")[1].endswith("a"), typed
        box.get_buffer().set_text("")
    finally:
        d.buf.set_text("")
        win.tabs.close(("note", "Moving items"))


def test_side_bars_drag_from_the_edge_thats_seen(app):
    """The arrows and the press are either side of the inside's edge, not a gap short of it: the right bar's rule, and the
    notebooks' column, whose own grip goes on past it. A real press there drags it. Hidden, none of it's there. (#126)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    win.open_note(record.HOME)
    pump(0.3)

    def box(w):
        ok, b = w.compute_bounds(win)
        assert ok, w
        return b.get_x(), b.get_x() + b.get_width()
    inside = win.tabs
    left, right = box(inside)
    y = win.get_height() / 2
    rule = next(w for w in walk(win.paned) if w.get_css_name() == "separator" and w.get_parent() is win.paned)
    assert abs(box(rule)[0] - right) <= 1, (box(rule), right)
    for dx in (-5, -1, 1, 5):
        assert win.pick(right + dx, y, Gtk.PickFlags.DEFAULT) is rule, dx
    assert win.pick(right - 9, y, Gtk.PickFlags.DEFAULT) is not rule
    column = win.left_slide.fixed
    for dx in (-5, -1, 1, 5):
        at = win.pick(left + dx, y, Gtk.PickFlags.DEFAULT)
        assert at is column.past or at is column.grip, (dx, at)
    assert win.pick(left + 9, y, Gtk.PickFlags.DEFAULT) not in (column.past, column.grip)
    assert column.past.get_cursor().get_name() == "ew-resize"
    hand, was = mouse.Mouse(win), column.width
    try:
        for _ in range(4):  # (GDK's own motions, from where the real pointer is: not a real hand's)
            hand.down(left + 3, y)
            pump(0.05)
            moved = column.dragged_from is not None
            hand.up(left + 3, y)
            pump(0.05)
            if moved:
                break
        assert moved, "a press just inside the edge takes it"
        slid = column.width
        win.left_slide.set_shown(False)
        wait_for(lambda: not column.child.get_visible(), "closed", 3)
        pump(0.1)
        assert not column.past.get_visible(), "hidden: nothing's there"
        win.left_slide.set_shown(True)
        wait_for(lambda: column.child.get_visible() and column.showing is None, "open", 3)
        pump(0.1)
        assert column.past.get_visible() and column.width == slid
    finally:
        column.set_width(was)
        pump(0.1)


def test_notebooks_column_follows_the_pointer_from_past_its_edge(app):
    """Dragged from the part of its edge over what's beside it, which moves with it, the column's where the pointer is, every
    step, not shaking either side of it. A real drag, laid out between its steps. (#156)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    win.open_note(record.HOME)
    pump(0.3)
    column, hand = win.left_slide.fixed, mouse.Mouse(win)
    was = column.width
    y = win.get_height() / 2
    ok, past = column.past.compute_bounds(win)
    x = past.get_x() + past.get_width() - 3
    try:
        hand.down(x, y)
        at = []
        for by in (20, 40, 60, 80):
            hand.drag(x + by, y)  # (and laid out, what the grip's moved to before the next)
            at.append(column.width)
        hand.up(x + 80, y)
        pump(0.1)
        # (GDK puts in motions of its own, from where the real pointer is, at any step, each put right by the next real one: so
        # most of them. Measured from the grip, these were was + 20, 20, 40, 40: one)
        assert sum(got == was + by for got, by in zip(at, (20, 40, 60, 80))) >= 2, (was, at)
    finally:
        column.set_width(was)
        win.store.books_width = None
        pump(0.1)


def test_nothing_open_is_blank(app):
    """Everything closed, the page is blank: nothing pointing anywhere, no words. (#157)"""
    win = app.win
    select(win, "a")
    pump(0.2)
    for _ in range(40):
        if not win.tabs.children():
            break
        win.tabs.close_current()
        pump(0.05)
    pump(0.2)
    try:
        assert win.content_stack.get_visible_child_name() == "empty", win.content_stack.get_visible_child_name()
        page = win.content_stack.get_visible_child()
        assert not [w for w in walk(page) if isinstance(w, (Gtk.Label, Gtk.Button, Gtk.Image, Adw.StatusPage))], list(walk(page))
    finally:
        win.open_note(record.HOME)
        pump(0.2)


def test_settings_rows_take_the_width(app):
    """Settings' rows are as wide as the dialog has room for, as the feedback box is. (#155)"""
    win = app.win
    select(win, "a")
    pump(0.2)
    settings = settings_opened(win)
    pump(0.3)
    try:
        lists = [w for w in walk(settings) if isinstance(w, Gtk.ListBox) and w.get_mapped() and w.get_width() > 0]
        assert lists
        for w in lists:
            assert settings.get_content_width() - 2 * 16 <= w.get_width() <= settings.get_content_width(), w.get_width()
    finally:
        settings.close()
        wait_for(lambda: win.get_visible_dialog() is None, "closed")


def test_settings_version_and_changelog(app):
    """Settings has the version running and View Changelog, the commits that version has, newest first, as Baude's."""
    win = app.win
    select(win, "a")
    pump(0.2)
    settings = settings_opened(win)
    pump(0.3)
    try:
        running = record.update.git(record.update.HERE, "log", "-1", "--format=%h", record.update.STARTED)
        has = record.update.git(record.update.HERE, "log", "--no-merges", "--format=%h", record.update.STARTED).split()
        assert running and running in settings.version_row.get_title() and has, settings.version_row.get_title()
        settings.version_row.changelog_button.emit("clicked")
        wait_for(lambda: [w for w in walk(settings) if hasattr(w, "commit") and w.get_mapped()], "the changelog", 3)
        assert [w.commit for w in walk(settings) if hasattr(w, "commit")] == has
    finally:
        settings.close()
        wait_for(lambda: win.get_visible_dialog() is None, "closed")


def test_right_bar_closed_at_launch_opens(app):
    """The right bar closed when the window opened has had no position (the paned's asked for more than it has): opening it slides
    it out to as wide as it can be at the least, not to nothing. (#150)"""
    win = app.win
    select(win, "a")
    slide, paned = win.right_slide, win.paned
    saved, widgets.SLIDE_MS = widgets.SLIDE_MS, 0
    slide.set_shown(False)
    pump(0.1)
    paned.set_position(100_000)
    pump(0.1)
    widgets.SLIDE_MS = 150
    try:
        slide.set_shown(True)
        wait_for(lambda: slide.anim is None, "opened", 3)
        pump(0.2)
        least = slide.pane.get_size_request()[0]
        assert slide.open_size >= least and slide.pane.get_width() >= least, (slide.open_size, least)
        assert 0 < paned.get_width() - paned.get_position() < paned.get_width() / 2, paned.get_position()
    finally:
        widgets.SLIDE_MS = saved
        slide.set_shown(True)
        pump(0.1)


def test_notes_go_narrow(app):
    """With the right bar open, its rule dragged as far as it goes leaves the notes next to nothing, their start still seen
    (cut off on the right): no least of theirs holds the right bar back. Narrow, they're laid out narrow first: the find
    bar and Feedback's Capture bar, each been and gone, take no width (shut, they held the notes at 530px)."""
    win = app.win
    select(win, "a")
    d = win.open_note("Narrow notes")
    win.right_slide.set_shown(True)
    pump(0.3)
    was = win.paned.get_position()
    try:
        d.open_search()
        win.capture_bar.set_reveal_child(True)
        pump(0.4)
        d.close_search()
        win.capture_bar.set_reveal_child(False)
        wait_for(lambda: not (win.capture_bar.get_child_revealed() or d.search_bar.get_first_child().get_child_revealed()),
                 "both bars gone", 3)
        shown, notes = win.paned.get_start_child(), win.content_stack.get_ancestor(Adw.ToolbarView)
        win.paned.set_position(220)  # (narrow, and wider than all they have at their least)
        pump(0.3)
        at, inside = (w.compute_bounds(win)[1] for w in (shown, notes))
        assert inside.get_x() + inside.get_width() <= at.get_x() + at.get_width(), "all of them seen"
        win.paned.set_position(0)  # (as far as it goes)
        pump(0.3)
        at, inside = (w.compute_bounds(win)[1] for w in (shown, notes))
        assert at.get_width() < 20 and inside.get_x() == at.get_x(), (at.get_width(), inside.get_x(), at.get_x())
    finally:
        win.paned.set_position(was)
        pump(0.1)


def test_sub_page_from_a_pages_menu(app):
    """Right-click a page in the list, the menu's new page item, a name typed and Enter: that page is made under the one
    clicked, open and shown, and listed under it. Home's menu has only that item; the list never folded first. (#163)"""
    win = app.win
    select(win, "a")
    win.open_note("Plans")
    pump(0.2)

    def row_of(name):
        i = 0
        while (row := win.pages.index.get_row_at_index(i)) is not None and row.page != name:
            i += 1
        return row

    def menu_of(name):
        """A right-click on name's row: its menu's items, (action, target) each, and the row."""
        row = row_of(name)
        click = next(c for c in row.observe_controllers()
                     if isinstance(c, Gtk.GestureClick) and c.get_button() == Gdk.BUTTON_SECONDARY)
        # (not popped up: late in a run this window can be on no monitor, offscreen, and macOS aborts on where it would go)
        popup, Gtk.Popover.popup = Gtk.Popover.popup, lambda _pop: None
        try:
            click.emit("pressed", 1, 10, 5)
        finally:
            Gtk.Popover.popup = popup
        pop = next(w for w in walk(row) if isinstance(w, Gtk.PopoverMenu))
        model, items = pop.get_menu_model(), []
        for i in range(model.get_n_items()):
            section = model.get_item_link(i, Gio.MENU_LINK_SECTION)
            items += [(section.get_item_attribute_value(j, "action").get_string(),
                       section.get_item_attribute_value(j, "target").get_string()) for j in range(section.get_n_items())]
        pop.unparent()
        return items, row

    (home_item,), _ = menu_of(record.HOME)
    made = []
    try:
        for parent in ("Plans", record.HOME):
            items, row = menu_of(parent)
            action = next(a for a, target in items if a == home_item[0] and target == parent)
            assert row.activate_action(action, GLib.Variant.new_string(parent)), "the menu's action is there"
            pump(0.3)  # (the dialog come up)
            dialog = win.get_visible_dialog()
            entry = next(w for w in walk(dialog) if isinstance(w, Gtk.Entry))
            entry.set_text("Ideas")
            entry.get_delegate().emit("activate")  # (Enter: the entry's text, which activates the default, Add)
            wait_for(lambda: win.get_visible_dialog() is None, "the dialog gone", 3)
            shown = win.diary_shown().page
            made.append(shown)
            assert shown.rpartition(":")[::2] == (parent, "Ideas"), shown
            assert row_of(shown).tree_parent is row_of(parent), "listed under it"
    finally:
        for name in made + ["Plans"]:
            win.tabs.close(("note", name))
        pump(0.1)


def test_notebooks_column_stops_where_the_rest_still_fits(app):
    """In a window too narrow for half of it to be the column and the rest still fit, the column dragged as far as it goes
    stops where the notes and the right bar are at their least: the window as wide as it was, all of it inside. (#166)"""
    win = app.win
    select(win, "a")
    column, H = win.left_slide.fixed, Gtk.Orientation.HORIZONTAL
    was, kept, size = column.width, win.store.books_width, (win.get_width(), win.get_height())

    class Drag:
        def set_state(self, _state):
            pass
    try:
        win.set_default_size(1000, size[1])
        win.right_slide.set_shown(True)
        pump(0.5)
        assert win.get_width() == 1000, win.get_width()
        column._drag_begin(Drag(), column.width, 0)
        column._drag_update(None, 700 - was, 0)  # (on past half the window)
        column._drag_end(None, 0, 0)
        pump(0.3)
        right = win.paned.get_end_child()
        ok, at = right.compute_bounds(win)
        assert win.get_width() == 1000 and win.measure(H, -1)[0] <= 1000, (win.get_width(), win.measure(H, -1)[0])
        assert column.width > was and at.get_x() + at.get_width() <= 1000, (column.width, at.get_x() + at.get_width())
    finally:
        column.set_width(was)
        win.store.books_width = kept
        win.set_default_size(*size)
        pump(0.3)


def test_notebooks_column_closes_only_when_dragged_nearly_shut(app):
    """The notebooks' column stops at its least however far on it's dragged, until that would leave it next to nothing: then it
    slides shut, but it's settled by letting go. Dragged out again first, it's open where the pointer is, and that's kept; let go
    shut, it's closed, and opens again at the width it was, and that's what's kept. As Baude's bars do, and Record's right one.
    (#127, #147, #150)"""
    win = app.win
    select(win, "a")
    pump(0.2)
    column, slide = win.left_slide.fixed, win.left_slide
    was, kept = column.width, win.store.books_width

    class Drag:
        def set_state(self, _state):
            pass
    try:
        column._drag_begin(Drag(), column.width, 0)
        column._drag_update(None, column.CLOSE_WITHIN + 2 - was, 0)
        assert column.width == column.LEAST and slide.target, "nearly shut: at its least, and not yet"
        column._drag_update(None, column.CLOSE_WITHIN - was, 0)
        assert slide.target and column.dragged_from == was and column.child.get_visible(), "next to nothing: not settled"
        pump(0.1)
        assert column.showing == 0 and column.child.get_visible() and slide.target, column.showing
        column._drag_update(None, 200 - was, 0)
        pump(0.1)
        assert slide.target and column.child.get_visible() and column.showing is None and column.width == 200, column.width
        column._drag_update(None, 210 - was, 0)
        assert column.width == 210, "the pointer's again"
        column._drag_end(None, 0, 0)
        assert win.store.books_width == 210 and slide.target and column.child.get_visible(), "out again and let go: that's kept"
        column.set_width(was)
        win.store.books_width = kept
        column._drag_begin(Drag(), column.width, 0)
        column._drag_update(None, column.CLOSE_WITHIN - was, 0)
        column._drag_update(None, -500, 0)
        assert slide.target
        column._drag_end(None, 0, 0)
        assert not slide.target and column.dragged_from is None, "let go shut: closed"
        wait_for(lambda: not column.child.get_visible(), "closed", 3)
        assert column.width == was and win.store.books_width == kept, "the width it was, kept"
        slide.set_shown(True)
        wait_for(lambda: column.child.get_visible() and column.showing is None, "open", 3)
        pump(0.1)
        assert column.get_width() == was, column.get_width()
    finally:
        slide.set_shown(True)
        column.set_width(was)
        pump(0.1)


def test_notebooks_column_drags(app):
    """A real mouse on the right edge of the pages and notebooks' column: it goes wider or narrower as far as it's dragged,
    within limits, the tabs beside it following; it's kept, and it's what opening again goes back to. A press further in
    is the list's. (#72)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    win.open_note(record.HOME)
    pump(0.3)
    column, slide = win.left_slide.fixed, win.left_slide
    hand, was = mouse.Mouse(win), column.width

    def edge(dx=-2):
        ok, p = column.compute_point(win, widgets.Graphene.Point().init(column.width + dx, column.get_height() / 2))
        return p.x, p.y

    def tab_x():
        tabs = [w for w in walk(win.tabs.bar) if w.get_css_name() == "tab" and w.get_mapped()]
        return min(w.compute_bounds(win)[1].get_x() for w in tabs)

    def drag(by, dx=-2):
        """A real press and release, for where a drag can begin; how far it goes, told it directly (GDK puts in motions
        from where the real pointer is, which a drag here would go to)."""
        x, y = edge(dx)
        hand.down(x, y)
        began = column.dragged_from is not None
        hand.up(x, y)
        pump(0.05)
        if began:
            column.dragged_from = column.width
            column._drag_update(None, by, 0)
            column._drag_end(None, by, 0)
        pump(0.2)

    try:
        assert column.grip.get_cursor().get_name() == "ew-resize" and column.grip.get_mapped()
        before = tab_x()
        drag(120)
        assert column.width == was + 120, column.width
        assert win.books.get_width() == was + 120, "what's in it too"
        assert abs(tab_x() - before - 120) <= 1, (tab_x(), before)
        assert win.store.books_width == was + 120, "kept"
        win.store.save_now()
        with open(win.store.path) as f:
            assert json.load(f)["books_width"] == was + 120
        shot(win, "record-notebooks-column-wide")
        drag(-60)
        assert column.width == was + 60, column.width
        drag(record.FixedWidth.LEAST - 40 - column.width)
        assert column.width == record.FixedWidth.LEAST, column.width
        shot(win, "record-notebooks-column-narrow")
        drag(5000)
        assert column.width == min(record.FixedWidth.MOST, win.get_width() // 2), column.width
        drag(was - column.width)
        assert column.width == was, column.width
        drag(50, dx=-30)
        assert column.width == was, "further in: the list's press, not the column's"
        column.set_width(was + 40)
        slide.set_shown(False)
        wait_for(lambda: slide.anim is None and not win.books.get_visible(), "closed")
        assert not column.grip.get_child_visible(), "closed: nothing to drag"
        slide.set_shown(True)
        wait_for(lambda: slide.anim is None and win.books.get_visible(), "open")
        pump(0.2)
        assert win.books.get_width() == was + 40 and column.grip.get_child_visible(), "open again, as wide as it was"
    finally:
        column.set_width(was)
        win.store.books_width = None
        win.store.save_now()
        pump(0.1)


def test_unmatched_fence_keeps_to_its_lines():
    """A fence nothing closes has the lines right after it, up to a blank one, not the rest of the page; one that's closed
    goes to its close, blank lines and all. One left over, it's the one being typed on, or else the one leaving least in
    code, so blocks that were there stay as they were; ```python can't close anything. (#70)"""
    spans = lambda text, **kw: widgets.fence_spans(text.split("\n"), **kw)  # noqa: E731
    assert spans("Above\n```\ncode\nmore\n\nBelow\n\nAnd below") == [(1, 3, False)]
    assert spans("```") == [(0, 0, False)] and spans("```\n") == [(0, 0, False)] and spans("no code") == []
    assert spans("```\na\n\nb\n```\nafter") == [(0, 4, True)], "closed: blank lines and all"
    above = "```\nnew\n\nprose here\nand more\n\n```\nold\n\nold\n```\nafter"
    assert spans(above) == [(0, 1, False), (6, 10, True)], spans(above)
    assert spans(above, typing=(0,)) == [(0, 1, False), (6, 10, True)]
    below = "```\nold\n```\n\nprose\n\nmore\n\n```\nnew"
    assert spans(below) == [(0, 2, True), (8, 9, False)], spans(below)
    long = "```\n" + "code\n" * 40 + "```\nprose\n```"
    assert spans(long, typing=(43,)) == [(0, 41, True), (43, 43, False)], "being typed on: it's the new one"
    assert spans("```\nx\n\n```python\ny\n```") == [(0, 1, False), (3, 5, True)], "```python opens, it can't close"
    assert spans("~~~\nx\n~~~") == [(0, 2, True)] and spans("~~~\nx\n~~~", tildes=False) == []
    assert record.heading_levels("```\ncode\n\n# Heading\n```py\n# comment\n```".split("\n")) == [0, 0, 0, 1, 0, 0, 0]
    assert record.heading_levels(["```", "# comment", "", "# Heading"]) == [0, 0, 0, 1]


def test_unmatched_fence_in_the_notes(app):
    """A ``` typed in a page shades only its own lines: what's below it stays as it was, headings and all, and # there
    isn't taken for code. Closing it takes in what it should. (#70)"""
    win = app.win
    select(win, "a")
    d = win.open_note("Fences")
    below = "\n\n# A heading below\n\nSome **bold** prose below, and a list:\n\n- one\n- two"
    try:
        d.buf.set_text("Above the fence\n```\ncode line\nsecond code line" + below)
        d.buf.place_cursor(d.buf.get_iter_at_line(2)[1])
        d.view.grab_focus()
        pump(0.3)
        shot(win, "record-unmatched-fence")
        pre = d.buf.get_tag_table().lookup("pre")
        in_pre = lambda n: d.buf.get_iter_at_line(n)[1].has_tag(pre)  # noqa: E731
        assert [n for n in range(d.buf.get_line_count()) if in_pre(n)] == [1, 2, 3], "only its own lines"
        assert d.view.blocks == [[1, 3]], d.view.blocks
        assert d.buf.get_iter_at_line(5)[1].has_tag(d.buf.get_tag_table().lookup("h1")), "the heading below is one"
        assert not d._in_code(d.text().index("Some")), "and what's below isn't code"
        assert d._in_code(d.text().index("second"))
        d.buf.insert(d.buf.get_end_iter(), "\n```")
        pump(0.2)
        assert all(in_pre(n) for n in range(1, d.buf.get_line_count())), "closed further down: all of it, as asked"
    finally:
        d.buf.set_text("")
        win.tabs.close(("note", "Fences"))


def test_heading_keys(app):
    """Alt+Up and Alt+Down swap a heading with its sibling; Shift+Alt+Left and Right change its level and its children's;
    Alt+Left and Right its own, or all in the selection. Each is one undo and keeps the caret where it was on the line.
    Off a heading the keys do what they did; a level past 1 to 6 changes nothing, for any of them."""
    win = app.win
    select(win, "b")
    d = win.open_note("Outline")
    alt = Gdk.ModifierType.ALT_MASK
    shift_alt = alt | Gdk.ModifierType.SHIFT_MASK

    def start(line, column=0):
        d.buf.set_text(NOTES_OUTLINE)
        at = d.buf.get_iter_at_line(NOTES_OUTLINE.split("\n").index(line))[1]
        at.set_line_offset(column)
        d.buf.place_cursor(at)

    def line_of_caret():
        ins = d.buf.get_iter_at_mark(d.buf.get_insert())
        return d.text().split("\n")[ins.get_line()], ins.get_line_offset()
    try:
        start("## A2", 4)
        assert d._key(None, Gdk.KEY_Up, 0, alt) is True
        assert d.text().index("## A2") < d.text().index("## A1") < d.text().index("## A3")
        assert d.text().index("### A2x") < d.text().index("## A1"), "its child went with it"
        assert line_of_caret() == ("## A2", 4), "the caret came with it"
        assert by_id(win, "b")["pages"]["Outline"] == d.text(), "kept"
        d.buf.undo()
        assert d.text() == NOTES_OUTLINE, "one undo"
        start("## A1")
        assert d._key(None, Gdk.KEY_Up, 0, alt) is True and d.text() == NOTES_OUTLINE, "no sibling: nothing, and it's taken"
        start("a text", 2)
        assert d._key(None, Gdk.KEY_Up, 0, alt) is False and d._key(None, Gdk.KEY_Right, 0, alt) is False
        assert d._key(None, Gdk.KEY_Left, 0, shift_alt) is False and d.text() == NOTES_OUTLINE, "not a heading: left be"
        cmd = alt | Gdk.ModifierType.META_MASK  # (⌥⌘: back and forward, the window's)
        start("## A2")
        assert not d._key(None, Gdk.KEY_Right, 0, cmd) and d.text() == NOTES_OUTLINE

        start("## A2", 3)
        assert d._key(None, Gdk.KEY_Right, 0, shift_alt) is True  # (with its children)
        got = d.text().split("\n")
        assert "### A2" in got and "#### A2x" in got and "## A1" in got and "## A3" in got, got
        assert line_of_caret() == ("### A2", 4), "the caret stays on its letter"
        d.buf.undo()
        assert d.text() == NOTES_OUTLINE
        assert d._key(None, Gdk.KEY_Left, 0, shift_alt) is True
        got = d.text().split("\n")
        assert "# A2" in got and "## A2x" in got, got

        start("## A2")  # (on its own: the child stays)
        assert d._key(None, Gdk.KEY_Right, 0, alt) is True
        got = d.text().split("\n")
        assert "### A2" in got and "### A2x" in got, got

        d.buf.set_text(NOTES_OUTLINE)  # (a selection: every heading in it, not its text)
        lines = NOTES_OUTLINE.split("\n")
        d.buf.select_range(d.buf.get_iter_at_line(lines.index("## A1"))[1], d.buf.get_iter_at_line(lines.index("## A3"))[1])
        assert d._key(None, Gdk.KEY_Left, 0, alt) is True
        got = d.text().split("\n")
        assert "# A1" in got and "# A2" in got and "## A2x" in got and "a2 text" in got, got
        assert "## A3" in got, "a selection ending at a line's start doesn't take that line"

        start("# A")  # (past 1 to 6: nothing)
        assert d._key(None, Gdk.KEY_Left, 0, alt) is True and d.text() == NOTES_OUTLINE
        assert d._key(None, Gdk.KEY_Left, 0, shift_alt) is True and d.text() == NOTES_OUTLINE
        d.buf.set_text("## P\n###### deep\n")
        d.buf.place_cursor(d.buf.get_start_iter())
        assert d._key(None, Gdk.KEY_Right, 0, shift_alt) is True and d.text() == "## P\n###### deep\n", "all of it or none"
    finally:
        win.tabs.close(("note", "Outline"))
        by_id(win, "b")["pages"].pop("Outline", None)


def test_no_second_line_beside_the_right_bar(app):
    """Between the page and the right bar there's the page's own edge and nothing more: the pixel the two panes leave
    between them is the frame's colour, not a second line."""
    import io
    from PIL import Image
    win = app.win
    select(win, "a")
    win.right_slide.set_shown(True)
    pump(0.3)
    handle = next(c for c in walk(win.paned) if c.get_css_name() == "separator" and c.get_parent() is win.paned)
    ok, at = handle.compute_bounds(win)
    assert ok and at.get_width() == 1, at.get_width()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, win.get_width(), win.get_height())
    tex = win.get_native().get_renderer().render_texture(snap.to_node(), None)
    im = Image.open(io.BytesIO(tex.save_to_png_bytes().get_data())).convert("RGB")
    x, y = round(at.get_x()), win.get_height() // 2  # (the rule's on the page's edge (#126), the gap after it, and where it was:
    gap = [im.getpixel((x + d, y)) for d in range(widgets.INSIDE_GAP + 2)]  # nothing left showing there, #148)
    assert len(set(gap)) == 1, gap


def test_left_column(app):
    """The left: the pages first (gone to more often), then the notebooks, each under a header as Baude's days have
    (its arrow folds the list away; its + makes a page, a notebook), kept folded or not."""
    win = app.win
    select(win, "a")
    left = win.pages.get_parent()
    kids = [c for c in walk(left) if c.get_parent() is left]
    assert kids.index(win.pages) < kids.index(win.books_head) < kids.index(win.books_reveal)
    for head in (win.pages.head, win.books_head):
        assert head.has_css_class("sidebar-header") and head.arrow.has_css_class("folder-arrow")
    try:
        win.pages.head.arrow.emit("clicked")
        assert win.pages.folded and record.Store().pages_folded and win.books_scroll.get_vexpand()
        win.books_head.arrow.emit("clicked")
        assert not win.books_reveal.get_reveal_child() and record.Store().books_folded
    finally:
        win.pages.set_folded(False)
        win.fold_books(False)


def test_ongoing_feedback_tab(app):
    """☰ → Ongoing Feedback is in Record as in Baude: its tab, read from GitHub (a stand-in), what's ready shown with what
    can be done with it; closed, freed, and back through the history; what's selected and typed through a restart."""
    feedback, fixes = record.feedback, record.fixview.fixes
    fake = os.path.join(TMP, "fake-github")
    with open(fake, "w") as f:
        f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(os.path.dirname(__file__), "fake_gh.py"))} "$@"\n')
    os.chmod(fake, 0o755)
    state = os.path.join(TMP, "fake-github.json")
    ready = fixes.round_body("o/r", "It's fixed.", state="ready", round=1, machine="another-mac")
    with open(state, "w") as f:
        json.dump({"viewer": "ADMIN", "issues": [
            {"number": 1, "title": "Ready one", "body": "A note.", "state": "OPEN", "stateReason": "", "url": "u/1",
             "author": {"login": "someone"}, "createdAt": "2026-11-01T10:00:00Z", "updatedAt": "2026-11-01T10:00:00Z",
             "comments": [{"author": {"login": "someone"}, "authorAssociation": "OWNER", "createdAt": "2026-11-01T10:00:00Z",
                           "body": ready}]},
            {"number": 2, "title": "Open one", "body": "Another.", "state": "OPEN", "stateReason": "", "url": "u/2",
             "author": {"login": "someone"}, "createdAt": "2026-11-01T09:00:00Z", "updatedAt": "2026-11-01T09:00:00Z",
             "comments": []}],
            "prs": [{"number": 9, "title": "t", "state": "OPEN", "isDraft": False, "headRefName": "fix/1", "headRefOid": "sha9",
                     "url": "p/9", "mergedAt": None, "updatedAt": "2026-11-01T10:00:00Z",
                     "closingIssuesReferences": [{"number": 1}]}]}, f)
    if feedback.issue_repo() is None:
        return  # (a checkout not from GitHub: nowhere to read it from)
    os.environ.update(BAUDE_GH=fake, FAKE_GH_STATE=state, FAKE_GH_SIGNED_IN="1")
    feedback._gh_seen.update(at=None, repo=None)
    fixes._can_write.clear()
    win = app.win
    select(win, "b")
    try:
        def labels(menu):
            for i in range(menu.get_n_items()):
                if (label := menu.get_item_attribute_value(i, "label")) is not None:
                    yield label.get_string()
                for link in ("section", "submenu"):
                    if (inner := menu.get_item_link(i, link)) is not None:
                        yield from labels(inner)
        menus = [w.get_menu_model() for w in walk(win) if isinstance(w, Gtk.MenuButton) and w.get_menu_model() is not None]
        listed = [label for m in menus for label in labels(m)]
        assert "Ongoing Feedback" in listed and "Feedback Items…" not in listed, listed
        assert win.lookup_action("feedbacklist") is None
        win.activate_action("win.fixes")
        page = win.tabs.get(("fixes",))
        assert isinstance(page, record.fixview.FixesPage) and win.tabs.current() is page
        assert win.content_stack.get_visible_child_name() != "none"
        wait_for(lambda: page.read_at, "the feedback read")
        assert [i["number"] for i in page.listed()] == [1, 2] and page.selected == 1
        assert page.merge_btn.get_visible() and not page.fix_btn.get_visible()
        assert win.tabs.pages[("fixes",)].get_title().endswith("(1)")
        assert page.stack.get_visible_child_name() == "main"
        assert isinstance(page.refresh_btn, widgets.RefreshButton)  # (it turns as it reads: test_refresh_turns, Baude's)
        page.send_btn.emit("clicked")  # (+: Send Feedback…, in Record's window)
        pump(0.1)
        assert isinstance(win.get_visible_dialog(), feedback.FeedbackDialog)
        win.get_visible_dialog().force_close()
        win.feedback_draft = None
        pump(0.1)
        page.reply_entry.set_text("Half a reply")
        page.merge_btn.emit("clicked")  # (asked first, over Record's window)
        dialog = win.get_visible_dialog()
        assert dialog is not None
        dialog.force_close()
        pump(0.1)

        saved = json.loads(json.dumps(win.state()))  # (a restart: the tab, what's selected, what's typed)
        assert saved["tab"] == ["fixes"] and saved["fixes"]["reply"] == "Half a reply"
        win.let_go()
        app.app.win = new = record.Window(app.app)
        new.restore(saved)
        new.present()
        win.destroy()
        page = new.tabs.get(("fixes",))
        assert page is not None and new.tabs.current() is page
        wait_for(lambda: page.read_at, "the feedback read again")
        assert page.selected == 1 and page.reply_entry.get_text() == "Half a reply"

        new.tabs.close(("fixes",))  # (closed, then back through the history)
        pump(0.1)
        assert new.tabs.get(("fixes",)) is None
        assert new.reopen_tab(("fixes",), None) and new.tabs.get(("fixes",)) is not None
        new.tabs.close(("fixes",))
        pump(0.1)
    finally:
        os.environ["BAUDE_GH"] = ""
        feedback._gh_seen.update(at=None, repo=None)
        fixes._can_write.clear()


def test_one_look(app):
    """Baude's look (widgets.CSS): the notebooks and pages, the bars and the right bar (the Record, Files, Git) the
    frame; the tabs (a page, a chat, code) the inside, a box set into it; no rule under the Record's title."""
    win = app.win
    select(win, "a")
    for w in (win.books, win.content_stack.get_ancestor(record.Adw.ToolbarView), win.right):
        assert "baude-frame" in w.get_css_classes(), w
    assert "baude-inside" in win.tabs.get_css_classes() and win.tabs.get_margin_bottom() == widgets.INSIDE_GAP
    assert "baude-inside" not in win.files_page.get_css_classes(), "Files: the right bar's, the frame"
    assert not [w for w in walk(win.chat) if isinstance(w, Gtk.Separator)], "no rule under the Record's title"


def test_views_look_as_the_window(app):
    """Kubernetes… and Databases… (☰) cover the window and look as it does, light and dark: the same bar and left
    column (the frame, no line under the bar), and what's read in them the inside, its edge the same rule, set into the
    frame as the tabs are."""
    import io
    from PIL import Image

    def look(inner):
        """What's drawn: the bar, the left column's edge, under the bar, the inside's edge, the inside, and what's below it."""
        pump(0.3)
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(win).snapshot(snap, win.get_width(), win.get_height())
        tex = win.get_native().get_renderer().render_texture(snap.to_node(), None)
        im = Image.open(io.BytesIO(tex.save_to_png_bytes().get_data())).convert("RGB")
        ok, b = inner.compute_bounds(win)
        x, top, y = round(b.get_x()), round(b.get_y()), round(b.get_y() + b.get_height() / 2)
        fill = [im.getpixel((x + 3 + i, y + j)) for i in range(8) for j in range(8)]  # (most of it: a diagram has dots)
        return {"bar": im.getpixel((win.get_width() // 2, 3)), "column": im.getpixel((3, y)),
                "under the bar": im.getpixel((x + 50, top - 1)), "edge": im.getpixel((x, y)),
                "inside": max(set(fill), key=fill.count),
                "below": im.getpixel((x + 50, round(b.get_y() + b.get_height()) + widgets.INSIDE_GAP // 2))}

    win = app.win
    select(win, "a")
    kv, sv = win.kube, win.schema
    for view, inner in ((kv, kv.lower), (sv, sv.split)):
        assert "baude-frame" in view.get_css_classes(), view
        assert "baude-inside" in inner.get_css_classes(), inner
    style = Adw.StyleManager.get_default()
    scheme = style.get_color_scheme()
    try:
        for force in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
            style.set_color_scheme(force)
            main = look(win.tabs)
            assert main["bar"] == main["column"] == main["under the bar"] == main["below"] != main["inside"], main
            assert main["edge"] != main["inside"], main
            for view, inner in ((kv, kv.lower), (sv, sv.split)):
                view.set_visible(True)
                found = look(inner)
                view.set_visible(False)
                assert found == main, f"{force.value_nick}: {view.__class__.__name__} {found}, the window {main}"
    finally:
        style.set_color_scheme(scheme)
        kv.set_visible(False)
        sv.set_visible(False)


def test_reader_formats_what_you_wrote(app):
    """In a chat read, your messages are drawn as Markdown, as its replies are and as Baude's chat draws them; a very long
    one still ends in …."""
    path = os.path.join(CHATS, "formatted.jsonl")
    long_one = "Word " * 400
    with open(path, "w") as f:
        for i, text in enumerate(("Make **this** bold and `this` code\n\n- one\n- two", long_one)):
            f.write(json.dumps({"type": "user", "cwd": REPO, "uuid": f"u{i}", "message": {"role": "user", "content": text}}) + "\n")
    win = app.win
    reader = win.open_chat(path)
    try:
        wait_for(lambda: len(reader.rows) == 2, "both read")
        row = reader.rows[0]
        assert isinstance(row.prose, record.widgets.MarkdownView) and row.has_css_class("reader-user")
        said = " ".join(w.get_label() for w in walk(row.prose) if isinstance(w, Gtk.Label))
        assert "<b>this</b>" in said and "**" not in said and "- one" not in said, said
        assert any(w.get_selectable() for w in walk(row.prose) if isinstance(w, Gtk.Label)), "still selectable"
        assert reader.rows[1].prose.text.endswith("…") and len(reader.rows[1].prose.text) == 1201
    finally:
        win.tabs.close(("chat", os.path.realpath(path)))
        os.remove(path)


def test_reader_picks(app):
    """Read: no Explain this. A selection is the passage quoted; Ctrl (or ⌘) and a drag picks another, the
    press the reader's (a label's menu on macOS, else), each quoted with its number; Ctrl and a click on one lets it
    go; ⌘C copies them all; a press without Ctrl lets them all go, the other messages' selections too."""
    win = app.win
    select(win, "a")
    r = win.reader
    assert not [w for w in walk(r.box) if isinstance(w, Gtk.Button) and w.get_label() == "Explain this"]
    first = next(w for w in r._labels() if "golden" in w.get_text())
    second = next(w for w in r._labels() if "Merge" in w.get_text())

    def at(label, i):
        rect = widgets.passage_rects(label, i, i + 1, r.over)[0]
        return rect.get_x() + 1, rect.get_y() + rect.get_height() / 2

    def drag(label, words, state=Gdk.ModifierType.CONTROL_MASK):
        a = label.get_text().index(words)
        (x0, y0), (x1, y1) = at(label, a), at(label, a + len(words))
        press = Press(x0, y0, state)
        r._pick_begin(press, x0, y0)
        r._pick_update(press, x1 - x0, y1 - y0)
        r._pick_end(press, x1 - x0, y1 - y0)
        return press

    a = first.get_text().index("golden files")
    first.select_region(a, a + len("golden files"))
    r._took_selection(first)
    assert win.chat.quote == "golden files"
    assert drag(second, "orders-api").got == Gtk.EventSequenceState.CLAIMED
    drag(first, "each job", Gdk.ModifierType.META_MASK)
    assert [p["text"] for p in r.picks] == ["golden files", "orders-api", "each job"], r.picks
    assert win.chat.quote == "[1] golden files\n\n[2] orders-api\n\n[3] each job", win.chat.quote
    assert win.chat.quote_label.get_text() == "1 “golden files”  2 “orders-api”  3 “each job”"
    assert not first.get_selection_bounds()[0], "all three drawn alike, by the reader"
    shot(win, "record-1-picks")
    copied = []
    clipboard = r.get_clipboard
    r.get_clipboard = lambda: type("Clip", (), {"set": lambda self, v: copied.append(v)})()
    try:
        assert r._copy_picks(None, Gdk.KEY_c, 0, Gdk.ModifierType.META_MASK)
    finally:
        r.get_clipboard = clipboard
    assert copied == ["golden files\n\norders-api\n\neach job"], copied
    x, y = at(second, second.get_text().index("orders-api") + 2)
    press = Press(x, y, Gdk.ModifierType.CONTROL_MASK)
    r._pick_begin(press, x, y)
    r._pick_end(press, 0, 0)
    assert win.chat.quote == "[1] golden files\n\n[2] each job", "Ctrl and a click on one: let go"
    second.select_region(0, 5)
    x, y = at(first, 2)
    press = Press(x, y)
    r._pick_begin(press, x, y)
    assert press.got == Gtk.EventSequenceState.DENIED and r.picks == [] and not second.get_selection_bounds()[0]
    assert win.chat.quote == "[1] golden files\n\n[2] each job", "the quote kept till another's picked, or ×"
    win.chat.set_quote("")


def test_typing_keeps_the_caret_in_view(app):
    """Typing at the bottom of a page, or moving the caret past an edge with the keys, scrolls it back into view
    smoothly; nothing's scrolled by a change or a caret that isn't yours (the focus elsewhere), nor while you've just
    read further on without typing."""
    win = app.win
    select(win, "b")
    d = win.open_note("Long page")
    d.buf.set_text("\n".join(f"Line {n} of a long page" for n in range(200)))
    d.view.grab_focus()
    pump(0.4)
    adj = d.scroll.get_vadjustment()

    def caret():
        r = d.view.char_rect(d.buf.get_iter_at_mark(d.buf.get_insert()))
        y = d.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, r.x, r.y)[1]
        return d.view.compute_point(d.scroll, record.Graphene.Point().init(0, y))[1].y, r.height

    def seen():
        top, height = caret()
        return top >= 0 and top + height <= d.scroll.get_height()

    try:
        d.buf.place_cursor(d.buf.get_end_iter())
        assert not seen(), "the page is taller than what's shown, and the caret at its end"
        values = []
        changed = adj.connect("value-changed", lambda a: values.append(a.get_value()))
        d.buf.insert_at_cursor("\nTyped at the end")
        wait_for(seen, "the caret back in view")
        wait_for(lambda: not d.follow_anim or d.follow_anim.get_state() != record.Adw.AnimationState.PLAYING, "it settled")
        pump(0.1)
        adj.disconnect(changed)
        assert seen() and adj.get_value() > 0
        assert len(values) > 2, ("in steps, not at a jump", values)
        assert values == sorted(values), "only ever down"
        top, height = caret()
        assert top + height <= d.scroll.get_height() - d.FOLLOW_MARGIN + 1, "kept off the edge"

        for n in range(5):  # (going on typing, line after line: still seen)
            d.buf.insert_at_cursor(f"\nAnd line {n}")
            pump(0.08)
        wait_for(seen, "the caret still in view")

        d.buf.place_cursor(d.buf.get_start_iter())  # (a key that moves it up, past the top)
        d.follow_soon()
        wait_for(lambda: adj.get_value() == 0, "scrolled back up")
        assert seen()

        at = adj.get_value()  # (nothing typed, a caret out of view after you've scrolled away: left where you read)
        d.buf.place_cursor(d.buf.get_end_iter())
        pump(0.3)
        assert adj.get_value() == at

        win.chat.composer.grab_focus()  # (written to by something else, the focus elsewhere: not scrolled)
        pump(0.05)
        d.buf.insert_at_cursor("\nFrom elsewhere")
        pump(0.3)
        assert adj.get_value() == at
    finally:
        win.tabs.close(("note", "Long page"))
        by_id(win, "b")["pages"].pop("Long page", None)


def test_diary_at_lists_the_folder(app):
    """@ in the diary lists what ⌘K finds in the chat's folder as you type; Enter puts in [[@name]], a link's way
    (its marks hidden, what's typed after it beside it, the caret back at its end no search again); a question says
    what each @name is."""
    win = app.win
    select(win, "a")
    catalogued(win)
    d = win.open_note(record.HOME)
    d.buf.place_cursor(d.buf.get_end_iter())
    d.view.grab_focus()
    for ch in "runs in @relay":  # (typed, a key at a time: @'s search starts only from an @ typed)
        d.buf.insert_at_cursor(ch)
    wait_for(lambda: d.pop.get_visible() and d.pop_shown, "@'s list")
    shown = [it["surface"] for it in d.pop_shown]
    assert set(shown[:2]) == {"relay_jobs.py", "RelayRunner"}, shown
    assert d.pop_model.get_n_items() == len(shown) == win.space().search("relay")[1], "every match, not the first few"
    pump(0.2)
    icons = [w.icon for w in walk(d.pop_list) if isinstance(w, record.widgets.RefIcon)]
    assert "file" in icons, ("each with its logo", icons)
    shot(d.pop, "record-2-at-list")
    d.pop_sel.set_selected(0)
    assert d._key(None, record.Gdk.KEY_Down, 0, 0) and d.pop_sel.get_selected() == 1, "↓ moves down the list"
    assert d._key(None, record.Gdk.KEY_Page_Down, 0, 0) and d.pop_sel.get_selected() == len(shown) - 1, \
        "and stops at its end"
    d.pop_sel.set_selected(shown.index("relay_jobs.py"))
    assert d._key(None, record.Gdk.KEY_Return, 0, 0), "Enter takes it"
    assert d.text().endswith("runs in [[@relay_jobs.py]] ") and not d.pop.get_visible()
    at = d.text().rindex("@relay")
    has = lambda name, off: d.buf.get_iter_at_offset(off).has_tag(d.buf.get_tag_table().lookup(name))  # noqa: E731
    assert has("hidden", at - 1) and has("ref", at + 1) and has("hidden", at + len("@relay_jobs.py"))
    assert "@relay_jobs.py" not in win.pages.names()[0], "a reference, not a page"
    d.buf.place_cursor(d.buf.get_iter_at_offset(at + len("@relay_jobs.py")))
    pump(0.1)
    assert d.token is None and not d.pop.get_visible(), "the caret back at its end: no search"
    assert d._key(None, record.Gdk.KEY_x, 0, 0) is False and d.buf.get_property("cursor-position") == at + len(
        "@relay_jobs.py]]"), "what's typed there goes after it"
    prompt = record.build_prompt(by_id(win, "a"), [], win.space())
    assert "@relay_jobs.py (File, src/relay_jobs.py)" in prompt, prompt
    assert "[[@" not in prompt, "the Record reads @names"


def rest_item(number, title, pr=False, state="open", merged=False, draft=False, ago=0):
    """An issue or a pull request as GitHub's REST API says it (tests/fake_gh.py's rest)."""
    item = {"number": number, "title": title, "state": state, "body": "What it's about.",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))}
    if pr:
        item.update(draft=draft, pull_request={"merged_at": "2026-11-01T10:00:00Z" if merged else None})
    return item


def checkout(path, url=None):
    """A git checkout at path, its origin url."""
    os.makedirs(path, exist_ok=True)
    subprocess.run(GIT + ["init", "-q"], cwd=path, check=True, capture_output=True)
    if url:
        subprocess.run(GIT + ["remote", "add", "origin", url], cwd=path, check=True, capture_output=True)
    return path


class Github:
    """tests/fake_gh.py as gh, with these repositories on it, lookup.GITHUB from nothing; no gh again after."""

    def __init__(self, rest, **state):
        self.state, self.path = {"viewer": "ADMIN", "issues": [], "prs": [], "rest": rest, **state}, \
            os.path.join(TMP, "fake-github.json")

    def __enter__(self):
        fake = os.path.join(TMP, "fake-github")
        with open(fake, "w") as f:
            f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(HERE, "fake_gh.py"))} "$@"\n')
        os.chmod(fake, 0o755)
        self.write()
        os.environ.update(BAUDE_GH=fake, FAKE_GH_STATE=self.path, FAKE_GH_SIGNED_IN="1")
        os.environ.pop("FAKE_GH_OFFLINE", None)
        shutil.rmtree(os.path.join(os.environ["XDG_CACHE_HOME"], "baude", "github"), ignore_errors=True)
        lookup.GITHUB.forget()
        return self

    def __exit__(self, *_):
        os.environ["BAUDE_GH"] = ""
        os.environ.pop("FAKE_GH_OFFLINE", None)
        lookup.GITHUB.forget()

    def write(self):
        with open(self.path, "w") as f:
            json.dump(self.state, f)

    def now(self):
        with open(self.path) as f:
            self.state = json.load(f)
        return self.state

    def calls(self):
        """What's been asked since the last time: each call's last word, its path."""
        calls, self.state["calls"] = self.now().get("calls", []), []
        self.write()
        return [c[-1] for c in calls]

    def due(self, gh=None):
        """It's time to look again."""
        for r in (gh or lookup.GITHUB)._repos.values():
            r["looked"] = 0.0


def test_github_lists(app):
    """A folder's GitHub repositories (its checkout's, those of the checkouts under it, not far): their latest and open
    pull requests and issues are names, with a logo for where each stands, in ⌘K's search and #'s; read with GET only;
    a look at one that hasn't changed is one request GitHub doesn't count; a change, two. Kept on disk for the next start;
    nothing in the way of anything else while GitHub can't be asked."""
    work = os.path.join(TMP, "gh-work")
    checkout(os.path.join(work, "one"), "https://github.com/acme/one.git")
    checkout(os.path.join(work, "deep", "er", "two"), "git@github.com:acme/two.git")
    checkout(os.path.join(work, "three"), "https://gitlab.com/acme/three.git")
    checkout(os.path.join(work, "node_modules", "dep"), "https://github.com/acme/dep.git")
    checkout(os.path.join(work, "a", "b", "c", "d"), "https://github.com/acme/far.git")
    fork = checkout(os.path.join(work, "fork"), "https://github.com/me/fork.git")
    subprocess.run(GIT + ["remote", "add", "upstream", "https://github.com/acme/parent"], cwd=fork, check=True)
    os.makedirs(os.path.join(work, "plain"), exist_ok=True)
    rest = {"acme/one": [rest_item(1, "Old open issue", ago=9e6),
                         rest_item(5, "Open pull request", pr=True, ago=50),
                         rest_item(6, "Merged pull request", pr=True, state="closed", merged=True, ago=40),
                         rest_item(7, "Closed pull request", pr=True, state="closed", ago=30),
                         rest_item(8, "Draft pull request", pr=True, draft=True, ago=20),
                         rest_item(9, "Closed issue", state="closed", ago=10),
                         rest_item(10, "New issue wraps badly", ago=5),
                         *(rest_item(n, f"Closed long ago {n}", state="closed", ago=9e5) for n in range(100, 400))],
            "acme/two": [rest_item(7, "The other seven")], "me/fork": [], "acme/parent": [rest_item(70, "In the parent")],
            "acme/three": [rest_item(1, "Not on GitHub")], "acme/dep": [rest_item(1, "Not looked for")],
            "acme/far": [rest_item(1, "Too far down")]}
    gh = lookup.GITHUB
    with Github(rest) as hub:
        gh.round([work])
        asked = hub.calls()
        looked_at = {c.split("/issues")[0][len("repos/"):] for c in asked}
        assert looked_at == {"acme/one", "acme/two", "me/fork", "acme/parent"}, asked
        assert len(asked) == 7, ("the latest and the open ones of each (me/fork has no latest: no open ones)", asked)
        assert not hub.now().get("wrote")
        assert any("per_page=50" in c and "state=all" in c for c in asked) and \
            any("per_page=100" in c and "state=open" in c for c in asked), asked
        listing = gh.listed(work)
        one = {it["surface"]: it for it in listing if it["surface"].startswith("acme/one#")}
        assert len(one) == 51, ("the latest 50, and the open ones however old", len(one))
        assert "acme/one#1" in one and "acme/one#399" not in one
        assert {n: one[f"acme/one#{n}"]["icon"] for n in (1, 5, 6, 7, 8, 9, 10)} == {
            1: "issue", 5: "pr_open", 6: "pr_merged", 7: "pr_closed", 8: "pr_open", 9: "issue_closed", 10: "issue"}
        assert one["acme/one#8"]["what"] == "Draft pull request" and one["acme/one#6"]["what"] == "Merged pull request"
        assert one["acme/one#10"]["summary"] == "New issue wraps badly · acme/one" and one["acme/one#10"]["label"] == "10"
        for name in ("pr_open", "pr_merged", "pr_closed", "issue", "issue_closed"):  # (each can be drawn)
            assert widgets.ref_icon_ops(name), name

        # the same, in ⌘K's search and #'s: by number or by a word of its title
        space = lookup.SearchSpace(lookup.Folder.for_folder(work), GLib.timeout_add)
        assert space.find("acme/two#7")["summary"].startswith("The other seven")
        assert space.find("#10") is None, "a bare number isn't a name: which repository's would it be?"
        assert space.search("wraps")[0][0]["surface"] == "acme/one#10", "a word of its title"
        assert space.search("10")[0][0]["surface"] == "acme/one#10", "its number"
        assert not [it for it in space.search("acmn")[0]], "not its repository's letters in order: all of them would"
        issues = [it["surface"] for it in space.issues()[0]]
        assert issues.index("acme/one#10") < issues.index("acme/one#1") < issues.index("acme/one#9") < \
            issues.index("acme/one#5"), ("issues first, the open ones first, the latest first", issues[:9])
        assert [it["surface"] for it in space.issues("7")[0]][:2] in (["acme/one#7", "acme/two#7"],
                                                                      ["acme/two#7", "acme/one#7"]), "its number first"
        assert space.issues("70")[0][0]["surface"] == "acme/parent#70", "and either kind"
        assert [it["surface"] for it in space.issues("the other")[0]] == ["acme/two#7"], "or a word of its title"
        assert not space.issues("no such thing")[0]
        assert not space.issues("acme")[0] and not space.issues("two")[0], "not its repository's name"
        assert not space.issues("ther")[0], "a word's start, not the middle of one"
        said = [it["surface"] for it in space.mentions("See acme/two#7, and `acme/one#5` and acme/one#99999, `#10`, #10.")]
        assert said == ["acme/one#5", "acme/two#7"] or said == ["acme/two#7", "acme/one#5"], said
        ref = lookup.catalogue_ref("acme/one#6", one["acme/one#6"]["entry"])
        assert ref.kind == "pr" and ref.status == "resolved" and ref.data["url"] == "https://github.com/acme/one/pull/6"
        assert lookup.ref_icon(ref.kind, ref.data) == "pr_merged" and "Merged pull request" in ref.summary()
        assert lookup.github_url("acme/one#6") == "https://github.com/acme/one/issues/6"
        assert lookup.github_url("feature/login#12") is None, "a repository it has read, not whatever's spelt so"

        # nothing's changed: not looked at again before it's time; then one request each, not counted, nothing new
        gh.round([work])
        assert hub.calls() == [], "not time yet"
        left, generation = hub.now()["left"], gh.generation
        hub.due()
        gh.round([work])
        asked = hub.calls()
        assert len(asked) == 4 and all("state=all" in c for c in asked), ("one request a repository", asked)
        assert hub.now()["left"] == left, "a 304 isn't counted"
        assert gh.generation == generation and gh.listed(work) is listing, "the same list"
        assert space.items() is space._items

        # the folders looked through again, and nothing's changed: nothing's new, so nothing's drawn again
        for known in gh._folders.values():
            known["at"] = 0.0
        made = space.items()
        hub.due()
        gh.round([work])
        assert gh._folders[work]["at"] > 0 and gh.generation == generation and gh.listed(work) is listing and \
            space.items() is made, "a rescan with nothing changed publishes nothing"
        hub.calls()

        # a pull request merged, a new issue: two requests (that one's), and the list is a new one
        rest_one = hub.now()["rest"]["acme/one"]
        rest_one[1] = rest_item(5, "Open pull request", pr=True, state="closed", merged=True)
        rest_one.append(rest_item(11, "Another"))
        hub.write()
        hub.due()
        gh.round([work])
        asked = hub.calls()
        assert len(asked) == 5 and sum("acme/one" in c for c in asked) == 2, asked
        assert hub.now()["left"] == left - 2 and gh.generation != generation and gh.listed(work) is not listing
        assert space.find("acme/one#5")["icon"] == "pr_merged" and space.find("acme/one#11")["what"] == "Issue", \
            "the items made again"
        assert gh.known("acme/one", 5)["state"] == "merged" and gh.known("acme/one", 12345) is None

        # the next start: what's kept is listed at once, whatever GitHub says, and costs nothing to check
        os.environ["FAKE_GH_OFFLINE"] = "1"
        left = hub.now()["left"]
        fresh = lookup.GitHub()
        fresh.round([work])
        assert {it["surface"] for it in fresh.listed(work)} >= {"acme/one#5", "acme/one#11", "acme/two#7"}, "kept on disk"
        assert next(it for it in fresh.listed(work) if it["surface"] == "acme/one#5")["icon"] == "pr_merged"
        wait = fresh._paused - time.time()
        assert 100 < wait <= 120, ("offline: it waits, and asks one repository to find out", wait)
        assert len(hub.calls()) == 1
        fresh.round([work])
        assert hub.calls() == [], "not while it waits"
        fresh._paused = 0.0
        fresh.due = hub.due(fresh)
        fresh.round([work])
        assert fresh._paused - time.time() > 200, "longer each time"
        assert fresh.listed(work), "still listed"
        del os.environ["FAKE_GH_OFFLINE"]
        fresh._paused = 0.0
        hub.due(fresh)
        hub.calls()
        fresh.round([work])
        assert len(hub.calls()) == 4 and hub.now()["left"] == left, "back: the ETags on disk are good"
        assert fresh._fails == 0

        # the rate limit: run out, it waits till it's back, and leaves some for your other work
        reset = time.time() + 1500
        hub.state.update(rest_status={"acme/one": 403}, rest_headers={"X-Ratelimit-Remaining": "0",
                                                                      "X-Ratelimit-Reset": str(int(reset))})
        hub.write()
        hub.due(fresh)
        fresh.round([work])
        assert abs(fresh._paused - reset) < 2 and len(hub.calls()) == 1 and fresh.listed(work), "the rest wait too"
        hub.state.update(rest_status={}, rest_headers={"X-Ratelimit-Remaining": "12",
                                                       "X-Ratelimit-Reset": str(int(reset))})
        hub.write()
        fresh._paused = 0.0
        hub.due(fresh)
        fresh.round([work])
        assert abs(fresh._paused - reset) < 2, "nearly out: it stops for now"
        hub.state.update(rest_headers={})
        hub.write()

        # a repository GitHub won't show (404): that one's left for a long while, and forgotten; the rest go on
        hub.state.update(rest_status={"acme/two": 404})
        hub.write()
        fresh._paused = 0.0
        hub.due(fresh)
        hub.calls()
        fresh.round([work])
        assert len(hub.calls()) == 4 and not [it for it in fresh.listed(work) if it["surface"].startswith("acme/two#")]
        assert fresh._repos["acme/two"]["paused"] > time.time() + 3000 and fresh._fails == 0
        hub.due(fresh)
        fresh.round([work])
        assert not [c for c in hub.calls() if "acme/two" in c]
        hub.state.update(rest_status={})
        hub.write()

        # issues switched off (410): its pull requests are what's read, there and then, with no wait
        hub.state.update(issues_off=["acme/one"])
        hub.write()
        hub.due(fresh)
        fresh._repos["acme/one"].update(recent=[], open=[], etags={})
        fresh.round([work])
        listed = {it["surface"]: it for it in fresh.listed(work)}
        assert listed["acme/one#5"]["icon"] == "pr_merged" and listed["acme/one#7"]["icon"] == "pr_closed" and \
            "acme/one#10" not in listed and fresh._repos["acme/one"]["pulls"]
        assert fresh._repos["acme/one"]["paused"] == 0.0 and any("acme/one/pulls?" in c for c in hub.calls())
        hub.state.update(issues_off=[])
        hub.write()
        shutil.rmtree(os.path.join(os.environ["XDG_CACHE_HOME"], "baude", "github"), ignore_errors=True)

        # no ETag, no free lunch: every 5 minutes, not every minute
        lookup.GITHUB.forget()
        hub.state.update(no_etag=True)
        hub.write()
        hub.calls()
        gh.round([os.path.join(work, "one")])
        assert gh._repos["acme/one"]["etags"].get("recent") == ""
        hub.calls()
        gh._repos["acme/one"]["looked"] = time.time() - 120
        gh.round([os.path.join(work, "one")])
        assert hub.calls() == [], "not yet"
        gh._repos["acme/one"]["looked"] = time.time() - 301
        gh.round([os.path.join(work, "one")])
        assert hub.calls(), "5 minutes"
        hub.state.update(no_etag=False)
        hub.write()

        # signed out: nothing listed, what's kept or not, and gh asks no one (so says the real one: exit 4)
        os.environ["FAKE_GH_SIGNED_IN"] = "0"
        gh.forget()
        gh.round([work])
        assert gh.listed(work) == () and gh._out and gh._paused > time.time() + 500, "nothing, for a while"
        assert len(hub.calls()) == 1
        os.environ["FAKE_GH_SIGNED_IN"] = "1"
        gh._paused = 0.0
        hub.due()
        gh.round([work])
        assert gh.listed(work) and not gh._out, "signed in again"

        # many repositories in use: only so many, each looked at less often, the folder's own first
        many = os.path.join(TMP, "gh-many")
        checkout(many, "https://github.com/acme/own.git")
        for n in range(40):
            checkout(os.path.join(many, f"r{n}"), f"https://github.com/acme/r{n}.git")
            hub.state["rest"][f"acme/r{n}"] = []
        hub.state["rest"]["acme/own"] = []
        hub.write()
        gh.forget()
        hub.calls()
        gh.round([many])
        asked = hub.calls()
        assert len({c.split("/issues")[0] for c in asked}) == lookup.GITHUB_REPOS, len(asked)
        assert asked[0].startswith("repos/acme/own/issues"), asked[0]
        for r in gh._repos.values():
            r["looked"] = time.time() - 120
        gh.round([many])
        assert hub.calls() == [], "24 of them: every 4 minutes"
        for r in gh._repos.values():
            r["looked"] = time.time() - 241
        gh.round([many])
        assert len(hub.calls()) == lookup.GITHUB_REPOS

        # shared out: one folder's many checkouts don't crowd out another's, or the chat's own
        small = os.path.join(TMP, "gh-small")
        checkout(small, "https://github.com/acme/small.git")
        checkout(os.path.join(small, "sub"), "https://github.com/acme/small-sub.git")
        chat = os.path.join(TMP, "gh-chat")
        checkout(chat, "https://github.com/acme/chat.git")
        checkout(os.path.join(chat, "vendor-ish", "dep"), "https://github.com/acme/vendored.git")
        hub.state["rest"].update({"acme/small": [], "acme/small-sub": [], "acme/chat": [], "acme/vendored": []})
        hub.write()
        gh.forget()
        hub.calls()
        gh.round([many, small], [chat])
        asked = [c.split("/issues")[0][len("repos/"):] for c in hub.calls() if "state=all" in c]
        assert asked[:3] == ["acme/own", "acme/small", "acme/chat"], ("each folder's own, then the chat's", asked[:4])
        assert "acme/small-sub" in asked and len(asked) == lookup.GITHUB_REPOS, ("and in turn", asked)
        assert asked.index("acme/small-sub") == 4, ("in the first turn of the rest", asked)

        # the chat's folder, which no one attached: the repository it's in, not the checkouts under it
        assert "acme/vendored" not in asked, asked
        gh.forget()
        gh.round([], [chat])
        assert [c.split("/issues")[0][len("repos/"):] for c in hub.calls() if "state=all" in c] == ["acme/chat"]
        assert gh.own_repository(chat) == "acme/chat" and gh.own_repository(os.path.join(TMP, "gh-small")) is None
        gh.round([chat])  # (attached, it's another matter)
        assert "acme/vendored" in {c.split("/issues")[0][len("repos/"):] for c in hub.calls()}
        assert not gh._repos["acme/chat"]["paused"]

        # off the main loop: look returns at once, and a folder with no remote on GitHub asks nothing at all
        gh.forget()
        gh.round([os.path.join(work, "plain")])
        gh.round([os.path.join(work, "three")])
        assert hub.calls() == [], "no remote on GitHub: nothing"
        assert gh.listed(os.path.join(work, "plain")) == ()
        gh.look([work])
        wait_for(lambda: gh.listed(work), "read on its thread")
        assert gh._thread.name == "lookup-github" and gh._thread.is_alive()
        wait_for(lambda: gh._todo.empty() and not gh._one.locked(), "done")
        hub.calls()
        gh.look([work])
        pump(0.2)
        wait_for(lambda: gh._todo.empty() and not gh._one.locked(), "done")
        assert hub.calls() == [], "not due again yet"
        assert not hub.now().get("wrote")

    # no gh (as these tests run but for this): no thread woken, nothing read, nothing kept
    assert os.environ["BAUDE_GH"] == "" and lookup.gh_path() is None
    cached = os.path.join(os.environ["XDG_CACHE_HOME"], "baude", "github")
    shutil.rmtree(cached, ignore_errors=True)
    size = gh._todo.qsize()
    gh.forget()
    gh.look([work])
    gh.round([work])
    assert gh._todo.qsize() == size and not gh._folders and not gh._repos and gh.listed(work) == ()
    assert not os.path.exists(cached), "nothing at all"


def test_diary_hash_lists_issues(app):
    """# in the notes lists GitHub's issues and pull requests, as @ lists the rest: Enter puts in the reference, seen as
    #12, its logo the #; a pull request's the branch's logo where the # was, green and then orange; a click opens it.
    A # that's a heading's or code's or finds nothing does nothing. @ lists them too; the questions say what they are."""
    win = app.win
    select(win, "b")
    notebook = win.current
    folder = os.path.join(TMP, "gh-shop")
    checkout(folder, "https://github.com/acme/shop.git")
    checkout(os.path.join(folder, "tools"), "git@github.com:acme/tools.git")
    with open(os.path.join(folder, "README.md"), "w") as f:
        f.write("# shop\n")
    for args in (["add", "README.md"], ["commit", "-qm", "shop"], ["branch", "-f", "feature/login#12"]):
        subprocess.run(GIT + args, cwd=folder, check=True, capture_output=True)
    rest = {"acme/shop": [rest_item(12, "Lists scroll past the end", ago=30),
                          rest_item(13, "Wrapped titles overlap", state="closed", ago=20),
                          rest_item(61, "Fix the wrap", pr=True, ago=10),
                          rest_item(60, "Faster lists", pr=True, state="closed", merged=True, ago=5),
                          rest_item(59, "Tried and dropped", pr=True, state="closed", ago=8)],
            "acme/tools": [rest_item(12, "A twelve of its own", ago=40), rest_item(77, "Only here", pr=True, ago=41)]}
    opened = []
    real_web, record.fixview.web = record.fixview.web, lambda _win, url: opened.append(url)
    page = "On GitHub"
    with Github(rest) as hub:
        try:
            notebook["folders"] = [folder]
            win.attached_changed(notebook)
            space = win.space()
            space.refresh()
            wait_for(lambda: not space.busy, "the folder catalogued")
            lookup.GITHUB.round(*win.github_folders())
            assert space.find("acme/shop#12") is not None
            d = win.open_note(page)
            d.view.grab_focus()

            def typed(text):
                for ch in text:
                    d.buf.insert_at_cursor(ch)
                pump(0.05)

            def has(name, off):
                return d.buf.get_iter_at_offset(off).has_tag(d.buf.get_tag_table().lookup(name))

            # a heading's #, one in code, one that finds nothing: no list, nothing said
            typed("#")
            pump(0.1)
            assert not d.pop.get_visible() and d.token is None, "a line's first #: a heading, as like as not"
            typed(" Heading\n")
            assert not d.pop.get_visible() and d.text() == "# Heading\n"
            typed("## ")
            assert not d.pop.get_visible()
            typed("Sub\n")
            typed("in `#")
            assert not d.pop.get_visible(), "code"
            typed("1` and C#")
            assert not d.pop.get_visible(), "C#"
            typed("\n```\n#")
            assert not d.pop.get_visible(), "a code block's first #"
            typed(" ")
            typed("#1")
            assert not d.pop.get_visible(), "a code block"
            typed("\n```\n")
            typed("see #zzz")
            assert not d.pop.get_visible() and d.token is None, "found nothing"
            typed(" and on")
            assert not d.pop.get_visible()
            assert d.text().endswith("see #zzz and on")

            # the list: issues first, the open ones first, each with its logo; digits find numbers
            typed(" #")
            wait_for(lambda: d.pop.get_visible() and d.pop_shown, "#'s list")
            shown = [it["surface"] for it in d.pop_shown]
            assert shown == ["acme/shop#12", "acme/tools#12", "acme/shop#13", "acme/shop#61", "acme/shop#60",
                             "acme/shop#59"][:len(shown)] or shown[:2] == ["acme/shop#12", "acme/tools#12"], shown
            assert d.pop_note.get_text() == "7 found"
            assert d.pop_selected() is None, "nothing picked till it's asked for"
            pump(0.2)
            icons = [w.icon for w in walk(d.pop_list) if isinstance(w, widgets.RefIcon)]
            assert {"issue", "issue_closed", "pr_open", "pr_merged", "pr_closed"} <= set(icons), icons
            labels = [w.get_text() for w in walk(d.pop_list) if isinstance(w, Gtk.Label)]
            assert "61" in labels and any("Fix the wrap" in t and "Pull request" in t for t in labels), labels
            shot(d.pop, "github-hash-list")
            typed("6")
            wait_for(lambda: [it["surface"] for it in d.pop_shown][0] == "acme/shop#61", "numbers starting with it")
            assert {it["surface"] for it in d.pop_shown} == {"acme/shop#59", "acme/shop#60", "acme/shop#61"} - \
                {"acme/shop#59"}, "59 doesn't start with 6"
            assert d._key(None, Gdk.KEY_Down, 0, 0) and d.pop_selected()["surface"] == "acme/shop#61"
            assert d._key(None, Gdk.KEY_Return, 0, 0), "Enter takes the one moved to"
            assert d.text().endswith("on [[@acme/shop#61|#61]] ") and not d.pop.get_visible(), d.text()[-40:]
            at = d.text().rindex("|#61") + 1  # (where it's shown: its #)
            assert has("hidden", at - 1) and has("ref", at) and has("logo-at", at) and has("hidden", at + 3)
            assert (at, "pr_open") in d.view.logos, d.view.logos
            assert d.text() == d.buf.get_text(*d.buf.get_bounds(), True) and "[[" not in win.pages.names()[0]
            assert "acme" not in " ".join(win.pages.names()[0]), "a reference, not a page"

            # words find its title; Esc leaves what's typed; an issue's logo is its own #
            typed("#scroll past")
            wait_for(lambda: d.pop_shown and d.pop_shown[0]["surface"] == "acme/shop#12", "words of its title")
            assert d._key(None, Gdk.KEY_Down, 0, 0) and d._key(None, Gdk.KEY_Tab, 0, 0), "Tab takes it too"
            assert d.text().endswith("[[@acme/shop#12|#12]] ")
            at12 = d.text().rindex("|#12") + 1
            assert (at12, "issue") in d.view.logos and has("logo-at", at12)
            typed("#w")
            wait_for(lambda: d.pop.get_visible(), "#'s list again")
            assert d._key(None, Gdk.KEY_Escape, 0, 0) and not d.pop.get_visible() and d.text().endswith(" #w")
            typed("r")
            assert not d.pop.get_visible(), "left: not till the next # typed"
            typed("\n")
            assert d.token is None
            typed("#")
            assert not d.pop.get_visible()
            typed("6")
            wait_for(lambda: d.pop.get_visible() and d.pop_shown, "a line's first #, once what follows says what it is")
            assert d._key(None, Gdk.KEY_Escape, 0, 0)
            typed("\n")
            typed("see #")  # (a click always takes it, moved to or not: the list's own activate)
            wait_for(lambda: d.pop.get_visible() and d.pop_shown, "#'s list")
            d.pop_list.emit("activate", 3)
            wait_for(lambda: not d.pop.get_visible(), "taken")
            assert d.text().endswith("see [[@acme/shop#61|#61]] "), d.text()[-40:]
            typed("\n")

            # what's clicked opens it (the real mouse, in the real gesture); the pointer over it says what it is
            import mouse
            shot(win, "github-note")
            if mouse.available():
                hand = mouse.Mouse(win)

                def spot(offset):
                    r = d.view.char_rect(d.buf.get_iter_at_offset(offset))
                    x, y = d.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, r.x + 2, r.y + r.height // 2)
                    ok, p = d.view.compute_point(win, Graphene.Point().init(x, y))
                    assert ok
                    return p.x, p.y
                wait_for(lambda: d.view.get_mapped(), "the page")
                pump(0.3)
                def click(offset, count):
                    for _ in range(6):  # (GDK puts in motions of its own, from where the real pointer is: one can
                        hand.down(*spot(offset))  # stop a click, here)
                        hand.up(*spot(offset))
                        pump(0.7)
                        if len(opened) == count:
                            return
                    raise AssertionError(f"nothing opened, clicking at {offset}: {opened}")
                click(at, 1)
                assert opened == ["https://github.com/acme/shop/pull/61"], opened
                caret = d.buf.get_property("cursor-position")
                click(at12, 2)
                assert opened[1] == "https://github.com/acme/shop/issues/12", opened
                assert d.buf.get_property("cursor-position") == caret, "the caret left where it was"
            win.open_ref("acme/shop#60")
            assert opened[-1] == "https://github.com/acme/shop/pull/60"
            win.open_ref("acme/shop#99999")
            assert opened[-1] == "https://github.com/acme/shop/issues/99999", "not listed now: its page is still there"
            said, real_toast, win.toast = [], win.toast, lambda text: said.append(text)
            try:
                win.open_ref("acme/gone#3")  # (a repository it's never read: only spelt as one)
                assert opened[-1] != "https://github.com/acme/gone/issues/3" and "not found" in said[-1], said
            finally:
                win.toast = real_toast

            # a branch that's spelt as one is a branch
            branch = space.find("feature/login#12")
            assert branch is not None and branch["kind"] == "branch" and not branch.get("label"), branch
            assert record.ref_text(branch["surface"], branch.get("label")) == "[[@feature/login#12]]"
            typed("@login")
            wait_for(lambda: d.pop_shown and d.pop_shown[0]["surface"] == "feature/login#12", "@'s list")
            assert d._key(None, Gdk.KEY_Return, 0, 0) and d.text().endswith("[[@feature/login#12]] ")
            d.buf.delete(d.buf.get_iter_at_offset(d.text().rindex("[[@feature")), d.buf.get_end_iter())
            win.hide_card()
            win.hover_ref("acme/shop#12")
            assert "Lists scroll past the end" in win.card.get_title() and "Issue" in win.card.get_title()
            win.hide_card()

            # @ lists them too, by their numbers, with the rest
            typed("@61")
            wait_for(lambda: d.pop_shown and d.pop_shown[0]["surface"] == "acme/shop#61", "@'s list")
            assert d._key(None, Gdk.KEY_Return, 0, 0)
            assert d.text().endswith("[[@acme/shop#61|#61]] ")
            typed("\n")

            # merged since: the list is new, and what's open shows it
            hub.now()["rest"]["acme/shop"][2] = rest_item(61, "Fix the wrap", pr=True, state="closed", merged=True)
            hub.write()
            hub.due()
            lookup.GITHUB.round(*win.github_folders())
            record.Window._github_changed(win)
            assert (at, "pr_merged") in d.view.logos, d.view.logos
            wait_for(lambda: space.find("acme/shop#61")["icon"] == "pr_merged", "the items")

            # a name in a reply, as Baude's markdown asks (the backticked #, the full name, PR #)
            names = record.ReaderNames(space, folder)
            assert names.refs["acme/shop#60"].kind == "pr" and names.refs["PR #60"] is names.refs["acme/shop#60"]
            assert "PR #77" not in names.refs and "acme/tools#77" in names.refs, "the chat's own repository's, not a " \
                "vendored checkout's"
            assert "PR #60" not in record.ReaderNames(space).refs, "no chat's: no repository a bare number means"
            was, widgets.SIGNAL = widgets.SIGNAL, names
            try:
                assert "baude-icon-pr_merged" in widgets.ref_link("acme/shop#60", "acme/shop#60", False)
                assert "ref:acme%2Fshop%2360" in widgets.ref_link("PR #60", "PR #60", False)
                assert "baude-icon-pr_merged" in widgets.ref_link("acme/shop#60", "acme/shop#60", True)
                # (a bare number in code is no link, whatever's listed: a colour's #333, a comment's)
                names.refs["#12"] = names.refs["acme/shop#12"]
                assert widgets.ref_link("#12", "#12", True) is None, "backticked"
                assert "ref:" not in (widgets.ref_code_markup("color: #12;\n# or #12", None) or ""), "in a code block"
                assert "ref:" in widgets.ref_code_markup("see acme/shop#12 for it", None), "its full name is fine"
                assert "ref:" not in widgets._inline_markup("See #12 and #60, and `#60`."), "nor in prose, on its own"
            finally:
                widgets.SIGNAL = was
            assert "acme/shop#60" in win.names_in("That's acme/shop#60, merged.")
            assert 'href="ref:acme%2Fshop%2360"' in record.md_markup("That's acme/shop#60.", {"acme/shop#60"})

            # the questions say what each is, as they do of any @name
            prompt = record.build_prompt(notebook, [], space)
            assert "@{acme/shop#12} (Issue, Lists scroll past the end · acme/shop)" in prompt, prompt
            assert "[[@" not in prompt

            # another repository with a #12 of its own: each stays its own, and what's written stays what it was
            assert space.find("acme/tools#12")["summary"].startswith("A twelve of its own")
            assert space.issues("12")[0][0]["surface"] == "acme/shop#12", "the first repository's, for the number typed"
            second = checkout(os.path.join(TMP, "gh-shop-again"), "https://github.com/acme/shop.git")  # (and two checkouts
            notebook["folders"] = [folder, second]  # of one repository: it's listed once)
            win.attached_changed(notebook)
            lookup.GITHUB.round(*win.github_folders())
            both = win.space()
            both.refresh()
            wait_for(lambda: not both.busy and both.find("acme/shop#12") is not None, "both folders")
            assert [it["surface"] for it in both.items()].count("acme/shop#12") == 1
            assert both.find("acme/shop#12")["summary"] == "Lists scroll past the end · acme/shop", \
                "it says its repository, not a folder"
            notebook["folders"] = [folder]
            win.attached_changed(notebook)

            # backspacing over one takes it whole; what's typed at its edge goes beside it
            d.buf.place_cursor(d.buf.get_end_iter())
            typed("x [[@acme/shop#12|#12]]")
            d.buf.place_cursor(d.buf.get_end_iter())
            pump(0.05)
            assert d._key(None, Gdk.KEY_BackSpace, 0, 0) and d.text().endswith("x "), d.text()[-30:]
        finally:
            record.fixview.web = real_web
            win.tabs.close(("note", page))
            notebook["pages"].pop(page, None)
            notebook["folders"] = []
            win.attached_changed(notebook)
            win.pages.fill()


def test_github_shots(app):
    """The lists as they look, light and dark: the branch's logo green, orange and grey, an issue's #."""
    if not SHOTS:
        return
    win = app.win
    select(win, "b")
    notebook = win.current
    folder = os.path.join(TMP, "gh-shots")
    checkout(folder, "https://github.com/nassir90/Baude.git")
    subprocess.run(GIT + ["add", "-A"], cwd=folder, capture_output=True)
    rest = {"nassir90/Baude": [
        rest_item(62, 'When the "gh" CLI is available, integrate with the git repos on attached folders', ago=60),
        rest_item(63, "Option and an arrow stops after a closing } and a line's full stop", pr=True, ago=120),
        rest_item(60, "Keep the tab strip's fade while a tab is closing", pr=True, state="closed", merged=True, ago=7200),
        rest_item(58, "Try a second renderer for the code view", pr=True, state="closed", ago=9000),
        rest_item(52, "The feedback tab loses its place on refresh", ago=10000),
        rest_item(49, "Selecting across two replies copies only one", state="closed", ago=86000),
        rest_item(47, "Draft: smaller tab close targets", pr=True, draft=True, ago=99000)]}
    page = "On GitHub"
    manager = Adw.StyleManager.get_default()
    with Github(rest):
        try:
            notebook["folders"] = [folder]
            win.attached_changed(notebook)
            space = win.space()
            space.refresh()
            wait_for(lambda: not space.busy, "the folder catalogued")
            lookup.GITHUB.round(*win.github_folders())
            for scheme, name in ((Adw.ColorScheme.FORCE_LIGHT, "light"), (Adw.ColorScheme.FORCE_DARK, "dark")):
                manager.set_color_scheme(scheme)
                pump(0.3)
                d = win.open_note(page)
                d.view.grab_focus()
                d.buf.set_text("# Where things stand\n\n- [[@nassir90/Baude#62|#62]] asks for this.\n"
                               "- [[@nassir90/Baude#63|#63]] is open, [[@nassir90/Baude#60|#60]] is in, "
                               "[[@nassir90/Baude#58|#58]] was dropped, [[@nassir90/Baude#49|#49]] is closed.\n"
                               "- [[feature/fast-lists]] and a page link, and #62 as plain text.\n\nSee ")
                d.buf.place_cursor(d.buf.get_end_iter())
                pump(0.2)
                for ch in "#":
                    d.buf.insert_at_cursor(ch)
                wait_for(lambda: d.pop.get_visible() and d.pop_shown, "#'s list")
                pump(0.4)
                shot(d.pop, f"github-hash-{name}")
                shot(win, f"github-window-hash-{name}")
                d._hide_pop()
                for ch in " @":
                    d.buf.insert_at_cursor(ch)
                wait_for(lambda: d.pop.get_visible() and d.pop_shown, "@'s list")
                for ch in "ab":
                    d.buf.insert_at_cursor(ch)
                pump(0.4)
                shot(d.pop, f"github-at-{name}")
                d._hide_pop()
                d.buf.place_cursor(d.buf.get_iter_at_offset(3))
                pump(0.3)
                shot(win, f"github-note-{name}")
                pal = widgets.RefPalette(win, space, lambda item: None)
                pal.present(win)
                wait_for(lambda: win.get_visible_dialog() is pal, "⌘K")
                pal.entry.set_text("the")
                pump(0.5)
                shot(win, f"github-palette-{name}")
                pal.force_close()
                pump(0.2)
                d.buf.set_text("")
                win.tabs.close(("note", page))
                notebook["pages"].pop(page, None)
                pump(0.1)
        finally:
            manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
            win.tabs.close(("note", page))
            notebook["pages"].pop(page, None)
            notebook["folders"] = []
            win.attached_changed(notebook)
            win.pages.fill()


def test_watch_github(app):
    """While a window's shown, and only then, GitHub's lists are looked at (once a minute); once they've changed, what's
    on show is told. It holds nothing of the window."""
    asked, told = [], []
    real_look, tick = lookup.GITHUB.look, widgets.GITHUB_TICK_S
    # (only what's asked of "a folder": the app's own window has a watch of its own)
    lookup.GITHUB.look, widgets.GITHUB_TICK_S = (lambda a, b: "a folder" in b and asked.append(b)), 1
    in_use = ["a folder"]
    try:
        win = Adw.Window(default_width=200, default_height=100)
        widgets.watch_github(win, lambda w: ([], list(in_use)), told.append)
        pump(1.3)
        assert not asked and not widgets.window_shown(win), "not shown yet"
        win.present()
        wait_for(lambda: asked == [["a folder"]], "a look, once it's shown")
        assert widgets.window_shown(win) and not told
        pump(1.2)
        assert len(asked) == 1, "not again for a minute"
        in_use.append("a folder too")
        wait_for(lambda: len(asked) == 2 and asked[1] == in_use, "at once, for folders that are new")
        lookup.GITHUB.generation += 1
        wait_for(lambda: len(told) == 1, "told it's changed")
        win.set_visible(False)
        lookup.GITHUB.generation += 1
        pump(1.2)
        assert len(told) == 1 and not widgets.window_shown(win), "not while it's hidden"
        win.set_visible(True)
        wait_for(lambda: len(told) == 2, "told, shown again")
        gone = weakref.ref(win)
        win.destroy()
        told.clear()
        del win
        gc.collect()
        pump(1.2)
        assert gone() is None, "let go of"
    finally:
        lookup.GITHUB.look, widgets.GITHUB_TICK_S = real_look, tick


def test_hash_key_never_rewrites_what_is_typed(app):
    """Real keys, on a plain line, in a list item and in a heading: Enter and Tab take only what they were asked to
    (a row moved to with the arrows, or the number exact); a hashtag or a heading never has a list open over it; a space
    after a number ends its search, and a new @ or # always starts its own list; Backspace anywhere in a #12 takes it
    whole."""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "b")
    notebook = win.current
    folder = os.path.join(TMP, "gh-keys")
    checkout(folder, "https://github.com/acme/shop.git")
    rest = {"acme/shop": [rest_item(1042, "Lists scroll past the end", ago=15), rest_item(12, "Wrapped titles overlap", ago=20),
                          rest_item(13, "Add a folder", ago=25), rest_item(61, "Fix the wrap", pr=True, ago=10),
                          rest_item(60, "Faster lists", pr=True, state="closed", merged=True, ago=5)]}
    page = "On GitHub, by key"
    with Github(rest):
        try:
            notebook["folders"] = [folder]
            win.attached_changed(notebook)
            space = win.space()
            space.refresh()
            wait_for(lambda: not space.busy, "the folder catalogued")
            lookup.GITHUB.round(*win.github_folders())
            d = win.open_note(page)
            d.view.grab_focus()
            pump(0.2)
            hand = mouse.Mouse(win)

            def start(text=""):
                if d.pop.get_visible():
                    d._hide_pop()
                d.buf.set_text(text)
                d.buf.place_cursor(d.buf.get_end_iter())
                pump(0.05)
                assert d.text() == text

            press, shown = hand.type, d.pop.get_visible

            def key(which):
                hand.key(*which)

            # (real keys reach it: the notes type as they do under your hand)
            start()
            press("Plain")
            assert d.text() == "Plain", d.text()

            for lead, after in (("", "\n"), ("- ", "\n- ")):
                # the list open, nothing asked of it: Enter's a new line, what's typed as it was typed
                start(lead)
                press("Priority #1")
                assert shown() and d.pop_selected() is None and d.pop_shown[0]["surface"] == "acme/shop#1042"
                key(mouse.RETURN)
                assert not shown() and d.text() == f"{lead}Priority #1{after}", repr(d.text())
                start(lead)
                press("Priority #1")
                key(mouse.TAB)
                assert not shown() and d.text() == f"{lead}Priority #1", ("Tab leaves it too", d.text())
                assert win.get_focus() is not None and win.get_focus().is_ancestor(d.view) or win.get_focus() is d.view
                # moved to with the arrows: that one
                start(lead)
                press("see #1")
                key(mouse.DOWN_ARROW)
                assert d.pop_selected()["surface"] == "acme/shop#1042"
                key(mouse.RETURN)
                assert d.text() == f"{lead}see [[@acme/shop#1042|#1042]] " and not shown(), repr(d.text())
                # typed more, what was moved to is left behind
                start(lead)
                press("see #1")
                key(mouse.DOWN_ARROW)
                press("3")
                assert shown() and d.pop_selected()["surface"] == "acme/shop#13", "the number exact"
                key(mouse.TAB)
                assert d.text() == f"{lead}see [[@acme/shop#13|#13]] ", repr(d.text())
                # the number exact: that one, and the Enter's taken
                start(lead)
                press("#12")
                assert shown() and d.pop_selected()["surface"] == "acme/shop#12"
                key(mouse.RETURN)
                assert d.text() == f"{lead}[[@acme/shop#12|#12]] " and not shown(), repr(d.text())

                # a hashtag's words: only of a title
                for word in ("#acme", "#shop", "#issue", "#pull", "#merged", "#closed"):
                    start(lead)
                    press(word)
                    assert not shown() and d.text() == lead + word and d.token is None, word
                start(lead)
                press("#lists")
                assert shown() and {it["surface"] for it in d.pop_shown} == {"acme/shop#1042", "acme/shop#60"}
                d._hide_pop()

                # a hex colour, C#, a web address's, &#39;: nothing open once they're typed
                for text in ("#ffffff", "#fff", "#abcdef", "C#", "http://x.y/#a", "&#39;"):
                    start(lead)
                    press(text)
                    assert not shown() and d.text() == lead + text, text

                # a # that's a number and a space, after Backspace too
                start(lead + "x ")
                press("#1")
                assert shown()
                key(mouse.BACKSPACE)
                assert shown() and d.text() == lead + "x #"
                press(" ")
                assert not shown() and d.text() == lead + "x # ", repr(d.text())
                start(lead)  # (and first on the line: back to a heading's #, as far as anyone can tell)
                press("#1")
                assert shown()
                key(mouse.BACKSPACE)
                assert not shown() and d.text() == lead + "#"
                press(" ")
                assert not shown() and d.text() == lead + "# ", repr(d.text())

                # a space after a number ends its list, and a new @ or # starts its own
                start(lead)
                press("#12 ")
                assert not shown() and d.token is None and d.text() == lead + "#12 "
                press("@")
                assert shown() and d.token is not None and d.text()[d.token[0]] == "@" and d.typed_at == d.token[0]
                press("ab")
                assert d.token[1] == "ab" and d.text()[d.token[0]] == "@", "its own"
                key(mouse.ESCAPE)
                start(lead)
                press("#12 #1")
                assert shown() and d.text()[d.token[0]:] == "#1" and d.token[1] == "1", d.token
                press("3")
                key(mouse.RETURN)
                assert d.text() == f"{lead}#12 [[@acme/shop#13|#13]] ", repr(d.text())
                start(lead)
                press("#add @")  # (a search of words, and a new @: it's @'s)
                assert shown() and d.text()[d.token[0]] == "@" and d.typed_at == d.token[0], d.token
                key(mouse.ESCAPE)
                start(lead + "x ")
                press("@ab #1")
                assert shown() and d.text()[d.token[0]:] == "#1", ("and a new #", d.token)
                key(mouse.ESCAPE)

                # Backspace anywhere in one takes it whole; what's typed in one goes after it
                for into in (0, 1, 2, 3):
                    start(f"{lead}a [[@acme/shop#12|#12]] b")
                    shown_at = d.text().index("|#12") + 1
                    d.buf.place_cursor(d.buf.get_iter_at_offset(shown_at + into))
                    pump(0.05)
                    if into == 0:  # (before it: what's before it, as any link's)
                        key(mouse.BACKSPACE)
                        assert d.text() == f"{lead}a[[@acme/shop#12|#12]] b", repr(d.text())
                        continue
                    key(mouse.BACKSPACE)
                    assert d.text() == f"{lead}a  b", (into, repr(d.text()))
                start(f"{lead}a [[@acme/shop#12|#12]] b")
                d.buf.place_cursor(d.buf.get_iter_at_offset(d.text().index("|#12") + 2))
                pump(0.05)
                press("9")
                assert "[[@acme/shop#12|#12]]9 b" in d.text(), repr(d.text())

            # headings: never a list over one, as they're typed key by key
            for text in ("# Add", "## Title ##", "# Title #12"):
                start()
                for ch in text:
                    press(ch)
                    assert not shown(), (text, d.text())
                assert d.text() == text
            for lead in ("> ", "- ", "1. ", "- [ ] "):  # (one in a quote or an item: and in a line's first #)
                start()
                for ch in lead + "## Add #1":
                    press(ch)
                    assert not shown(), (lead, d.text())
                assert d.text().endswith("## Add #1"), repr(d.text())
            # (but what's typed after a line's first # may ask for it, in an item too)
            start("- ")
            press("#")
            assert not shown()
            press("6")
            assert shown()
            key(mouse.ESCAPE)
            start()
            press("(#6")
            assert shown(), "after a bracket"
            key(mouse.ESCAPE)

            # a list open, moved in, and GitHub's list new: what's picked stays picked
            start("see ")
            press("#")
            key(mouse.DOWN_ARROW)
            key(mouse.DOWN_ARROW)
            picked = d.pop_selected()["surface"]
            d.github_changed()
            assert d.pop_selected()["surface"] == picked, "picked, it stays"
            # and the folders looked through again with nothing changed: nothing's told of it at all
            generation, listing = lookup.GITHUB.generation, lookup.GITHUB.listed(folder)
            for known in lookup.GITHUB._folders.values():
                known["at"] = 0.0
            for r in lookup.GITHUB._repos.values():
                r["looked"] = 0.0
            lookup.GITHUB.round(*win.github_folders())
            assert lookup.GITHUB.generation == generation and lookup.GITHUB.listed(folder) is listing
            assert d.pop_selected()["surface"] == picked
            key(mouse.ESCAPE)
        finally:
            win.tabs.close(("note", page))
            notebook["pages"].pop(page, None)
            notebook["folders"] = []
            win.attached_changed(notebook)
            win.pages.fill()


def test_ask_says_what_project_names_are(app):
    """A question about a passage: the passage goes with what its project names are, and the Record sees three
    messages, its diary as the glossary; what it couldn't place comes back as + chips for To learn."""
    win = app.win
    select(win, "a")
    catalogued(win)
    win.open_chat(CHAT)
    n = len(asked())
    win.chat.set_quote("RelayRunner reads relay_jobs.py and diffs against the golden files.")
    win.chat.composer.get_buffer().set_text("What's this saying?")
    win.chat.submit()
    wait_for(lambda: win.chat.ask is None and len(asked()) > n, "the answer")
    q = asked()[-1]
    assert "--model" in q["argv"] and q["argv"][q["argv"].index("--model") + 1] == "haiku"
    sysprompt = q["argv"][q["argv"].index("--system-prompt") + 1]
    assert "golden files" in sysprompt and "C++, CLI, HTTP" in sysprompt, "the computer science it takes as known"
    assert sysprompt.startswith("You are Record.")
    p = q["prompt"]
    assert "[project names in it]\n- RelayRunner: Code symbol, src/relay_jobs.py\n- relay_jobs.py: File, " \
           "src/relay_jobs.py\n[/project names]" in p, p
    assert p.count("\nUser: ") + p.count("\nRecord: ") + p.startswith("User: ") == 3, "three messages"
    assert "--- page: Home ---" in p and "- **function**: a recipe" in p
    last = by_id(win, "a")["messages"][-1]
    assert last["role"] == "record" and last["unknown"] == ["stream protocol", "idempotency"], last
    shot(win, "record-3-answered")


def test_a_name_is_shown_by_where_the_pointer_is(app):
    """A reply beginning with a name, not clicked in: the name's card comes with the pointer resting on the name, and not on
    the words after it (Gtk.Label.get_current_uri says the first link for all of it). Selecting, none comes up under the
    drag: a press calls off the one on its way. (#92)"""
    label = Gtk.Label(use_markup=True, selectable=True, xalign=0,
                      label='<a href="ref:Runner">Runner</a> and a good many plain words after it')
    window = Gtk.Window(child=label, default_width=500)
    shown = []

    class Host:
        card = None

        def hover_ref(self, name, **_kw):
            shown.append(name)

        def hover_left(self):
            shown.append(None)
    wait = record.NAME_CARD_WAIT / 1000 + 0.15
    try:
        window.present()
        pump(0.3)
        its_own = list(label.observe_controllers())
        record.on_name_hover(label, Host())
        moved = next(c for c in list(label.observe_controllers())
                     if c not in its_own and isinstance(c, Gtk.EventControllerMotion))
        y = label.get_height() / 2
        moved.emit("motion", label.get_width() - 30, y)
        pump(wait)
        assert shown == [], f"on plain words: {shown}"
        moved.emit("motion", 8, y)
        assert shown == [], "not at once"
        pump(wait)
        assert shown == ["Runner"], shown
        moved.emit("motion", label.get_width() - 30, y)
        assert shown == ["Runner", None], shown
        # gone off it before it's up: it doesn't come up
        moved.emit("motion", 8, y)
        moved.emit("leave")
        pump(wait)
        assert shown == ["Runner", None, None], shown
    finally:
        window.destroy()


def test_pointers(app):
    """#92, a real mouse, its moves posted as macOS delivers them, GDK having lost track of the pointer as it does on a
    real Mac (Mouse.lost). Record has the magnifying glass as Baude has (GTK's zoom-in is the arrow
    without it). In the Ongoing Feedback tab's issue a link is the hand, Issue too, the images the magnifying glass; the
    Reader's links, the Record's replies' and the notes' the hand, and the text beside them the I-beam."""
    import macos
    import mouse
    if not mouse.available():
        return
    assert macos.objc_send(b"gdkCoreCursor", b"gdkZoomInCursor") == macos.make_zoom_cursor.cursor, "the magnifying glass"
    feedback, fixes = record.feedback, record.fixview.fixes
    win = app.win
    select(win, "a")
    read = win.open_chat(CHAT)
    pump(1.1)
    hand = mouse.Mouse(win)
    hand.lost()
    reader = next(w for w in read._labels() if "the docs" in w.get_text())
    hand.hover(reader, *mouse.label_spot(reader, "the docs"), "pointer")
    hand.hover(reader, *mouse.label_spot(reader, "runs"), "text")

    bubble = win.chat._bubble({"role": "record", "text": "See [the answer](https://example.com/a) then go on."})
    win.chat.box.append(bubble)
    pump(0.3)
    win.chat.scroll.get_vadjustment().set_value(win.chat.scroll.get_vadjustment().get_upper())
    pump(0.3)
    reply = next(w for w in walk(bubble) if isinstance(w, Gtk.Label) and "the answer" in w.get_text())
    assert reply.get_mapped(), "the Record's reply shown"
    hand.hover(reply, *mouse.label_spot(reply, "the answer"), "pointer")
    hand.hover(reply, *mouse.label_spot(reply, "then"), "text")
    win.chat.box.remove(bubble)

    d = win.open_note("Pointers")
    d.buf.set_text("Read [the site](https://example.com/site) and more words\n")
    wait_for(lambda: d.view.get_mapped(), "the page")
    pump(0.3)

    def spot(words):
        r = d.view.char_rect(d.buf.get_iter_at_offset(d.text().index(words)))
        return d.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, r.x + 2, r.y + r.height // 2)
    hand.hover(d.view, *spot("the site"), "pointer")
    hand.hover(d.view, *spot("more"), "text")

    if feedback.issue_repo() is None:
        return  # (a checkout not from GitHub: nowhere to read it from)
    fake = os.path.join(TMP, "fake-github-92")
    with open(fake, "w") as f:
        f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(os.path.dirname(__file__), "fake_gh.py"))} "$@"\n')
    os.chmod(fake, 0o755)
    png = Gdk.MemoryTexture.new(40, 20, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(40 * 20 * 4)), 160) \
        .save_to_png_bytes().get_data()
    for name in ("1-before", "1-after"):
        kept = os.path.join(fixes.fixes_home(), "shots", "902", "1", f"{name}.png")
        os.makedirs(os.path.dirname(kept), exist_ok=True)
        with open(kept, "wb") as f:
            f.write(png)
    shots = [{"label": "The chat", "before": "902/1/1-before.png", "after": "902/1/1-after.png"}]
    ready = fixes.round_body("o/r", "It's fixed.", state="ready", round=1, machine="another-mac", shots=shots)
    state = os.path.join(TMP, "fake-github-92.json")
    with open(state, "w") as f:
        json.dump({"viewer": "ADMIN", "issues": [
            {"number": 1, "title": "Ready one", "body": "See [the page](https://example.com/page) about it.", "state": "OPEN",
             "stateReason": "", "url": "u/1", "author": {"login": "someone"}, "createdAt": "2026-11-01T10:00:00Z",
             "updatedAt": "2026-11-01T10:00:00Z",
             "comments": [{"author": {"login": "someone"}, "authorAssociation": "OWNER", "createdAt": "2026-11-01T10:00:00Z",
                           "body": ready}]}], "prs": []}, f)
    os.environ.update(BAUDE_GH=fake, FAKE_GH_STATE=state, FAKE_GH_SIGNED_IN="1")
    feedback._gh_seen.update(at=None, repo=None)
    fixes._can_write.clear()
    if os.path.exists(fixes.list_path()):  # (nothing kept from another world: show_last_read)
        os.remove(fixes.list_path())
    saved = feedback.feedback_items
    feedback.feedback_items = lambda: []
    try:
        win.activate_action("win.fixes")
        page = win.tabs.get(("fixes",))
        wait_for(lambda: page.read_at, "the feedback read")
        pictures = lambda: [w for w in walk(page.clamp) if isinstance(w, Gtk.Picture) and w.has_css_class("fix-shot")]  # noqa: E731
        wait_for(lambda: len(pictures()) == 2, "the images")
        pump(0.3)
        said = next(w for w in walk(page.clamp) if isinstance(w, Gtk.Label) and "the page" in w.get_text())
        hand.hover(said, *mouse.label_spot(said, "the page"), "pointer")
        hand.hover(said, *mouse.label_spot(said, "about"), "text")
        issue = next(w for w in walk(page.clamp) if isinstance(w, Gtk.Button) and w.get_label() == "Issue")
        hand.hover_middle(issue, "pointer")
        hand.hover_middle(pictures()[0], "zoom-in")
        hand.hover(said, *mouse.label_spot(said, "about"), "text")
        hand.hover_middle(pictures()[1], "zoom-in")
        win.tabs.close(("fixes",))
        pump(0.1)
    finally:
        feedback.feedback_items = saved
        os.environ["BAUDE_GH"] = ""
        feedback._gh_seen.update(at=None, repo=None)
        fixes._can_write.clear()


def test_ci_events_arent_shown(app):
    """The Reader leaves out a CI event the desktop app sent, as it does a compaction's summary: nobody said it. (#108)"""
    event = {"type": "user", "uuid": "u9", "message": {"role": "user", "content": "<ci-monitor-event>2 checks failed</ci-monitor-event>"}}
    said = {"type": "user", "uuid": "u8", "message": {"role": "user", "content": "What failed?"}}
    items = []
    record.parse_records([json.dumps(event), json.dumps(said)], items)
    assert [i["text"] for i in items] == ["What failed?"], items


def test_open_chat(app):
    """A notebook has no chat: one opened (record.py --chat from Baude's Open in Record, an @chat, ⌘K's) is read in a
    tab of its own, whichever notebook's open, kept open for the next start; one open already, shown; no notebook's
    made or attached for it. One from when a notebook had its chat: that chat open, its folder the notebook's."""
    win = app.win
    assert "chat" not in by_id(win, "a") and by_id(win, "a")["folders"][0] == REPO, "a's chat's folder: a's"
    assert win.store.reading["path"] == CHAT
    select(win, "b")
    count, b = len(win.store.notebooks), win.current
    assert win.reader.path == CHAT, "the chat open, whichever notebook's"
    win.open_chat(OTHER)
    assert len(win.store.notebooks) == count and win.current is b and "chat" not in b and not b["folders"]
    assert isinstance(win.tabs.current(), record.Reader) and win.reader.path == os.path.realpath(OTHER)
    assert [c["path"] for c in record.Store().chats] == [os.path.realpath(CHAT), os.path.realpath(OTHER)], "kept open"
    assert record.Store().reading["path"] == os.path.realpath(OTHER), "kept"
    win.open_item({"kind": "chat", "path": CHAT})
    assert len(win.store.notebooks) == count and win.current is b and win.reader.path == os.path.realpath(CHAT)
    assert len([c for c in win.tabs.children() if isinstance(c, record.Reader)]) == 2, "open already: shown"
    select(win, "a")
    assert win.reader.path == os.path.realpath(CHAT), "another notebook: the same chats"
    win.tabs.close(("chat", os.path.realpath(OTHER)))
    assert [c["path"] for c in win.store.chats] == [os.path.realpath(CHAT)], "closed: not kept open"


def test_attach_to_folder(app):
    """Attach… lists Choose a folder… and the folders the chats ran in; Open a Chat… the chats. A notebook attached
    to a folder: its @ knows the folder's names, the chat open is as it was; Detach lets it go."""
    win = app.win
    picked = []
    sessions = [{"path": CHAT, "title": "Jobs", "cwd": REPO, "mtime": time.time() - 60},
                {"path": OTHER, "title": "Other", "cwd": REPO, "mtime": time.time() - 999},
                {"path": "/x/3.jsonl", "title": "Gone", "cwd": "/no/such/folder", "mtime": time.time()}]
    rows = lambda lb: [r.get_title() for i in range(20) if (r := lb.get_row_at_index(i))]  # noqa: E731
    chats = record.AttachDialog(picked.append, chats=True)
    chats._loaded(sessions)
    assert rows(chats.list) == ["Jobs", "Other", "Gone"], rows(chats.list)
    assert chats.folders.get_parent() is None, "no folders"
    dlg = record.AttachDialog(picked.append)
    dlg._loaded(sessions)
    assert "shop" in rows(dlg.folders) and dlg.list.get_parent() is None, rows(dlg.folders)
    dlg.search.set_text("nothing like it")
    dlg.fill()
    assert "shop" not in rows(dlg.folders)
    dlg.search.set_text("")
    dlg.fill()
    picked.append(dlg.folders.get_row_at_index(1).session)
    assert picked == [{"path": None, "title": "shop", "cwd": REPO}], picked
    select(win, "b")
    notebook, reading = win.current, win.reader.path
    try:
        notebook["folders"].append(picked[0]["cwd"])  # (a folder picked: one more of the Record's folders)
        win.attached_changed(notebook)
        assert win.reader.path == reading, "the chat open as it was"
        assert win.right_stack.get_child_by_name("folders") is win.folders_page, "in the right bar"
        row = win.folders_page.rows.get_row_at_index(0)
        assert row.get_title() == "shop" and not win.folders_page.none.get_visible()
        space = catalogued(win)
        assert space.root == REPO and space.find("relay_jobs.py") is not None, "@ knows the folder's names"
        win.detach()
        assert notebook["folders"] == [] and win.folders_page.rows.get_row_at_index(0) is None
        assert win.folders_page.none.get_visible(), "says what they're for"
        win.folders_page.add_btn.emit("clicked")  # (+: Attach…)
        pump(0.1)
        dialog = win.get_visible_dialog()
        assert isinstance(dialog, record.AttachDialog)
        dialog.on_pick(record.folder_attachment(REPO))
        dialog.force_close()
        pump(0.1)
        assert notebook["folders"] == [REPO] and win.folders_page.rows.get_row_at_index(0).get_title() == "shop"
    finally:
        notebook["folders"] = []


def test_many_folders(app):
    """A Record attached to several folders: @ and the questions know all their names, each said with its folder's,
    a name two have the first folder's; the Folders page lists what's attached, each with ×; Detach all lets them all go."""
    other = os.path.join(TMP, "api")
    os.makedirs(os.path.join(other, "api"), exist_ok=True)
    with open(os.path.join(other, "api", "routes.py"), "w") as f:
        f.write("ROUTES = []\n")
    with open(os.path.join(other, "README.md"), "w") as f:
        f.write("# api\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "api"]):
        subprocess.run(GIT + args, cwd=other, check=True, capture_output=True)
    win = app.win
    select(win, "b")
    notebook = win.current
    try:
        notebook["folders"] = [REPO, other]
        win.attached_changed(notebook)
        space = win.space()
        assert isinstance(space, record.Spaces)
        space.refresh()
        wait_for(lambda: not space.busy and space.find("routes.py") is not None, "both folders catalogued")
        assert space.find("relay_jobs.py")["root"] == REPO and space.find("routes.py")["root"] == other
        assert space.find("README.md")["root"] == REPO, "a name both have: the first folder's"
        assert space.find("routes.py")["summary"].startswith("api · ")
        rows = win.folders_page.rows
        assert rows.get_row_at_index(1) is not None and rows.get_row_at_index(2) is None, "each listed"
        win.detach_folder(other)  # (its ×)
        assert notebook["folders"] == [REPO] and rows.get_row_at_index(1) is None
        notebook["folders"] = [REPO, other]
        win.attached_changed(notebook)
        win.detach()
        assert notebook["folders"] == [] and win.space().find("routes.py") is None
    finally:
        notebook["folders"] = []


def test_images_open_in_the_large_view(app):
    """An image in Record opens in the same large view as Baude's, not the system's viewer: in the Feedback tab, going
    through that fix's images, and zooming, panning and closing just as there."""
    win = app.win
    select(win, "a")
    assert isinstance(win.lightbox, widgets.Lightbox)
    png = Gdk.MemoryTexture.new(800, 400, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes([9, 90, 200, 255]) * (800 * 400)),
                                3200).save_to_png_bytes().get_data()
    page = win.open_fixes()
    page.draw_detail = lambda: None  # (what's in it is this test's: not what was read, or read last time)
    pictures = []
    for name in ("a.png", "b.png"):
        pic = Gtk.Picture(paintable=Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)), css_classes=["fix-shot"])
        widgets.make_image_interactive(pic, png, name=name)
        pictures.append(pic)
    holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.clamp.set_child(holder)
    for pic in pictures:
        holder.append(pic)
    page.stack.set_visible_child_name("main")
    pump(0.2)
    opened, saved = [], widgets.ImageActions.open
    widgets.ImageActions.open = lambda self: opened.append(self)
    box = win.lightbox
    try:
        pictures[0].image_actions.view()
        assert not opened, "not the system's viewer"
        wait_for(lambda: box.picture.get_width() > 0, "laid out")
        assert box.get_visible() and len(box.items) == 2 and box.index == 0
        box.on_key(None, Gdk.KEY_Right, 0, Gdk.ModifierType(0))
        assert box.index == 1, "through that tab's images"
        box.zoom_to(3.0, animate=False)
        assert box.zoomed() and box.picture.zoom == 3.0 and not box.next_btn.get_visible()
        box.on_key(None, Gdk.KEY_Right, 0, Gdk.ModifierType(0))
        assert box.index == 1, "zoomed in, the arrows go around it"
        box.on_key(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
        assert not box.get_visible() and box.picture.get_paintable() is None
    finally:
        widgets.ImageActions.open = saved
        win.tabs.close(("fixes",))
        pump(0.1)


def test_viewers(app):
    """Baude's viewers in Record's window, without Baude: Files and Git in the right bar after the Record, on the
    notebook's folder, code in a tab of its own, and Kubernetes… and Databases… (☰) over the window; an @name opened
    goes where it is (a file in a code tab, a branch on the Git page). A window of their own is there when wanted
    (viewers())."""
    win = app.win
    select(win, "a")
    space = catalogued(win)
    assert "baude" not in sys.modules, "the viewers without Baude"
    import viewers
    win.open_item(space.find("relay_jobs.py"))
    wait_for(lambda: win.tabs.current() is win.code_page and
             win.code_page.path == os.path.join(REPO, "src/relay_jobs.py"), "the file in a code tab")
    assert not viewers.ViewerWindow._windows, "in Record's window, not one of their own"
    win.open_item(space.find("feature/fast-lists"))
    wait_for(lambda: win.right.get_visible() and win.right_stack.get_visible_child_name() == "git",
             "the branch on the Git page")
    shot(win, "record-3-viewers")
    win.open_viewer("kube")
    wait_for(lambda: win.kube.get_visible(), "the Kubernetes view")
    win.kube.set_visible(False)
    win.open_viewer("db")
    wait_for(lambda: win.schema.get_visible(), "the database view")
    win.schema.set_visible(False)
    apart = win.viewers()
    try:
        assert isinstance(apart, viewers.ViewerWindow) and apart.viewer_folder() == win.viewer_folder()
    finally:
        apart.close()


def test_git_avatars(app):
    """The Git page in Record's right bar has its authors' pictures, without Baude: a noreply email's, one found through
    the repository's commits, and initials for who has none. (All the ways that can go: tests/test_baude.py.)"""
    import base64
    import gitview
    picture = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
    repo = os.path.join(TMP, "authors")
    os.makedirs(repo)
    for name, email, subject in (("Naza", "12345+naza@users.noreply.github.com", "By Naza"),
                                 ("Ada", "ada@example.com", "By Ada"), ("Grace", "grace@example.com", "By Grace")):
        with open(os.path.join(repo, "a.txt"), "a") as f:
            f.write(subject)
        git = [*GIT, "-c", f"user.name={name}", "-c", f"user.email={email}"]
        if not os.path.isdir(os.path.join(repo, ".git")):
            subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
            subprocess.run([*git, "remote", "add", "origin", "https://github.com/acme/authors.git"], cwd=repo, check=True)
        subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
        subprocess.run([*git, "commit", "-qm", subject], cwd=repo, check=True)
    urls = {"https://avatars.githubusercontent.com/u/12345?s=40", "https://avatars.githubusercontent.com/u/7?v=4&s=40"}
    listing = [{"commit": {"author": {"email": "ada@example.com"}},
                "author": {"avatar_url": "https://avatars.githubusercontent.com/u/7?v=4"}}]
    read = []
    saved = gitview.AVATARS, gitview._avatar_download, gitview.gh_state, gitview._gh_json
    gitview.AVATARS = gitview.GitAvatars()
    gitview._avatar_download = lambda url: (read.append(url), picture if url in urls else None)[1]
    gitview.gh_state = lambda refresh=False: ("signed_in", "you")
    gitview._gh_json = lambda *a, timeout=20: listing if a[0] == "api" and "/commits?" in a[1] else None
    os.environ["BAUDE_GH"] = "/bin/true"  # (there's a gh: this test's own)
    win = app.win
    try:
        select(win, "a")
        win.show_git(folder=repo)
        page = win.git_page
        wait_for(lambda: page.top and os.path.realpath(page.top) == os.path.realpath(repo) and page.commits, "the log", 20)
        rows = {r.commit["subject"]: r.avatar for r in walk(page.list) if hasattr(r, "commit")}
        wait_for(lambda: rows["By Naza"].get_custom_image() is not None and rows["By Ada"].get_custom_image() is not None,
                 "their pictures")
        wait_for(lambda: math.inf not in dict(gitview.AVATARS.asked).values(), "nothing more being read")
        assert rows["By Grace"].get_custom_image() is None and rows["By Grace"].get_text() == "Grace"
        assert sorted(read) == sorted(urls), read
    finally:
        wait_for(lambda: math.inf not in dict(gitview.AVATARS.asked).values(), "nothing more being read")
        os.environ["BAUDE_GH"] = ""
        gitview.AVATARS, gitview._avatar_download, gitview.gh_state, gitview._gh_json = saved
        shutil.rmtree(gitview.avatar_folder(), ignore_errors=True)
        win.git_page.focus()


def test_sub_pages(app):
    """As Zim's: +Ideas then a space (or Enter) is [[+Ideas]], a link to the page's sub-page Home:Ideas, made and
    listed under it, grey and italic until something's written in it. Backspace after a link doesn't break it: its
    name loses a letter. A page renamed takes its sub-pages, and the links to them, along."""
    win = app.win
    select(win, "b")
    notebook = win.current
    saved = dict(notebook["pages"])
    rows = lambda: [(r.page, r.tree_anchor.has_css_class("page-placeholder"))  # noqa: E731
                    for i in range(50) if (r := win.pages.index.get_row_at_index(i))]
    try:
        d = win.open_note(record.HOME)
        d.buf.set_text("")
        d.buf.insert_at_cursor("Notes on +Ideas")
        assert d._key(None, record.Gdk.KEY_space, 0, 0) is False, "the space goes in as typed"
        d.buf.insert_at_cursor(" ")
        assert d.text() == "Notes on [[+Ideas]] " and "Home:Ideas" not in notebook["pages"], "listed for its link"
        assert ("Home:Ideas", True) in rows(), rows()
        end = d.buf.get_end_iter()
        start = end.copy()
        start.backward_char()
        d.buf.delete(start, end)  # (the space: GTK's Backspace)
        assert d._key(None, record.Gdk.KEY_BackSpace, 0, 0) is True
        assert d.text() == "Notes on [[+Idea]]", "the link kept, its name a letter shorter"
        d.buf.insert_at_cursor(" +Plans")
        d._key(None, record.Gdk.KEY_Return, 0, 0)
        d.buf.insert_at_cursor("\n")
        d = d.open_page("Home:Plans")  # (in a tab of its own)
        assert d.title.get_text() == "Plans" and win.tabs.current() is d
        d.buf.insert_at_cursor("Ship it. +Q1")
        d._key(None, record.Gdk.KEY_space, 0, 0)
        assert ("Home:Plans", False) in rows() and ("Home:Plans:Q1", True) in rows(), rows()
        shot(win, "record-4-sub-pages")
        d.title.set_text("Goals")
        d.rename_page()
        assert "Home:Goals" in notebook["pages"] and ("Home:Goals:Q1", True) in rows() and \
            "[[+Goals]]" in notebook["pages"]["Home"]
        assert win.tabs.get(("note", "Home:Goals")) is d, "its tab with it"
    finally:
        for key in [k for k in list(win.tabs.pages) if k[0] == "note" and k[1] != record.HOME]:
            win.tabs.close(key)
        notebook["pages"].clear()
        notebook["pages"].update(saved)
        win.open_note(record.HOME).reload()


def test_saves_links_and_blank_notebooks(app):
    """Every change is saved as it's made, and at once on leaving a page, with backups; a link
    shows only its name, what's typed at its edges goes beside it; a page only a link has is listed while the link
    is there; New Notebook doesn't pile up blank ones, and one left blank goes."""
    win = app.win
    select(win, "b")
    notebook, store = win.current, win.store
    saved = dict(notebook["pages"])
    on_disk = lambda: next(t for t in json.load(open(store.path))["records"] if t["id"] == notebook["id"])  # noqa: E731
    index = win.pages.index
    listed = lambda: [index.get_row_at_index(i).page for i in range(30) if index.get_row_at_index(i)]  # noqa: E731
    try:
        d = win.open_note(record.HOME)
        d.buf.set_text("")
        d.buf.insert_at_cursor("Jobs run nightly. ")
        wait_for(lambda: "nightly" in on_disk()["pages"]["Home"], "saved as typed")
        d.buf.insert_at_cursor("+Plans")
        d._key(None, record.Gdk.KEY_space, 0, 0)
        d.buf.insert_at_cursor(" today")
        d.open_page("Home:Plans")
        assert "[[+Plans]] today" in on_disk()["pages"]["Home"], "on the disk as the page was left"
        assert os.listdir(os.path.join(os.path.dirname(store.path), "backups")), "a backup"
        win.tabs.close(("note", "Home:Plans"))  # (left, its tab closed: empty, it's listed for its link only)
        d = win.open_note(record.HOME)
        hidden = d.buf.get_tag_table().lookup("hidden")
        shown = "".join(c for i, c in enumerate(d.text()) if not d.buf.get_iter_at_offset(i).has_tag(hidden))
        assert shown == "Jobs run nightly. Plans today", repr(shown)
        assert "Home:Plans" in listed() and "Home:Plans" not in notebook["pages"], "listed for its link, empty"
        m = next(record.LINK_RE.finditer(d.text()))
        d.buf.place_cursor(d.buf.get_iter_at_offset(record.link_name(m)[1]))
        d._key(None, record.Gdk.KEY_x, 0, 0)
        d.buf.insert_at_cursor("!")
        assert "[[+Plans]]! today" in d.text(), "typed at its last letter: beside it"
        d.buf.set_text("Jobs run nightly.")
        assert "Home:Plans" not in listed(), "the link gone: its page too"
    finally:
        notebook["pages"].clear()
        notebook["pages"].update(saved)
        win.open_note(record.HOME).reload()
    record.Window.rename, rename = (lambda self, *_how: None), record.Window.rename  # (its dialog)
    try:
        before = len(store.notebooks)
        win.new_notebook()
        win.new_notebook()
        assert len(store.notebooks) == before + 1, "a blank one, not two"
        blank = win.current
        select(win, "a")
        wait_for(lambda: blank not in store.notebooks, "the blank one let go")
    finally:
        record.Window.rename = rename


def test_baude_niceties(app):
    """As Baude's: Ctrl+B the notebooks, Ctrl/⌘ + − 0 the zoom, ⌘, Settings (sizes, Show in Applications, feedback),
    Send Feedback… saved with Record's context, ⌘K over the notebook's names (opened where they are), a reply drawn
    as Baude draws one (its code, its names linked with their logos), and an @name's @ its logo."""
    win = app.win
    select(win, "a")
    space = catalogued(win)
    keys = {sc.get_trigger().to_string(): sc for c in win.observe_controllers()
            if isinstance(c, record.Gtk.ShortcutController) for sc in c}
    press = lambda k: keys[k].get_action().activate(record.Gtk.ShortcutActionFlags(0), win, None)  # noqa: E731
    press("<Control>b")
    assert not win.books.get_visible()
    press("<Control>b")
    assert win.books.get_visible()
    mod = "<Meta>" if sys.platform == "darwin" else "<Control>"
    press(mod + "plus")
    assert abs(win.store.zoom - 1.1) < 1e-9
    wait_for(lambda: "zoom-110" in win.chat.box.get_css_classes(), "the zoom applied, as the next frame begins")
    press(mod + "0")
    assert win.store.zoom == 1.0
    wait_for(lambda: "zoom-100" in win.chat.box.get_css_classes(), "back to the usual")
    dlg = win.open_feedback()
    dlg.text.get_buffer().set_text("Too tall.")
    dlg.save()
    assert record.feedback.feedback_items()[0]["text"] == "Too tall."
    pal = record.widgets.RefPalette(win, space, win.open_item)
    pal.entry.set_text("relay")
    pal.fill()
    assert "relay_jobs.py" in pal.shown()
    pal.close()
    row = next(r for r in win.reader.rows if r.item["role"] == "assistant")
    assert isinstance(row.prose, record.widgets.MarkdownView)
    markup = " ".join(w.get_label() for w in walk(row.prose) if isinstance(w, record.Gtk.Label))
    assert "baude-ref:relay_jobs.py" in markup and "baude-icon-file" in markup, markup[:300]
    d = win.open_note(record.HOME)
    saved = d.text()
    try:
        d.buf.insert_at_cursor(" @relay_jobs.py")
        assert any(icon == "file" for _at, icon in d.view.logos), d.view.logos
    finally:
        d.buf.set_text(saved)


def test_zoom_and_following_the_chat_keep_out_of_each_other(app):
    """A chat being read: at its end, a zoom leaves it at its end and still following; scrolled up, a zoom leaves it where
    it was and not following, though what's hidden through the zoom takes the view to the end for a moment. A selection
    across messages and a citation's shading stay through it too."""
    win = app.win
    select(win, "a")
    path = transcript("zoom-follow", [f"Reply {n} of a chat, words enough to wrap across the column as it's read. " * 3
                                      for n in range(200)])
    reader = win.open_chat(path)
    adj, zoom = reader.scroll.get_vadjustment(), win.store.zoom

    def zoomed(size):
        win.set_zoom(size)
        pump(0.3)  # (its size changes as the next frame begins)
        wait_for(lambda: not reader.place.job, "the zoom done", 20)
        pump(0.2)
    try:
        wait_for(lambda: adj.get_upper() > adj.get_page_size() * 3 and reader.follow.stick, "the chat, at its end")
        pump(0.5)
        zoomed(zoom + 0.2)
        assert reader.follow.stick and adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1, "still at the end"
        assert not getattr(reader.scroll, "held", False)
        live_reply(path, "One more, as it's read.", "follow-more")
        wait_for(lambda: any("One more" in w.get_text() for w in reader._labels()), "the new reply", 3)
        pump(0.5)
        assert adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1, "and goes on following"

        adj.set_value(adj.get_upper() / 2)
        pump(0.2)
        where = adj.get_value()
        assert not reader.follow.stick
        labels = list(reader._labels())
        here = next(w for w in labels if w.get_mapped() and w.compute_bounds(reader.box)[1].get_y() > where)
        at = labels.index(here)
        reader.selection.anchor = (labels[at], 2)
        reader.selection._select_to((labels[at + 1], 3))
        chosen = reader.selection.text()
        zoomed(zoom)
        assert not reader.follow.stick, "not following: it's only been to the end for a moment"
        ok, seen = here.compute_point(reader.scroll, widgets.Graphene.Point().init(0, 0))
        assert ok and 0 <= seen.y < reader.scroll.get_height(), f"where it was: {seen.y} of {reader.scroll.get_height()}"
        assert reader.selection.text() == chosen, "what was selected still is"

        def in_view():
            ok, at = here.compute_point(reader.scroll, widgets.Graphene.Point().init(0, 0))
            assert ok and -here.get_height() <= at.y < reader.scroll.get_height(), f"{at.y} of {reader.scroll.get_height()}"
            assert not reader.follow.stick, "and not thrown to the end"

        # another press in the middle of a zoom, a few frames in
        for frames in (0.03, 0.1, 0.2):
            win.set_zoom(zoom + 0.2)
            pump(frames)
            win.set_zoom(zoom + 0.4)
            pump(0.3)
            wait_for(lambda: not reader.place.job, "the zoom done", 20)
            pump(0.2)
            in_view()
            zoomed(zoom)
            in_view()
        # from much bigger to where it was: what stays laid out below the view is less than a page
        zoomed(zoom + 1.3)
        in_view()
        zoomed(zoom)
        in_view()
        # its tab left in the middle of one: a restart has where it was, and it's there as it's shown again
        place = lambda: dict((tuple(k), p) for k, p in win.state()["places"])[("chat", os.path.realpath(path))]  # noqa: E731
        was = adj.get_value()
        win.set_zoom(zoom + 0.4)
        pump(0.1)
        note = win.open_note("Left for a moment")
        pump(0.3)
        assert reader.place.left, "messages still to come back"
        assert abs(place()["value"] - was * (zoom + 0.4) / zoom) < adj.get_page_size(), (place(), was)
        win.open_chat(path)
        pump(0.3)
        wait_for(lambda: not reader.place.job, "the zoom done", 20)
        pump(0.2)
        in_view()
        assert not reader.place.left
        win.tabs.close(("note", note.page))
        zoomed(zoom)
        in_view()
        live_reply(path, "Yet another.", "follow-more-2")
        wait_for(lambda: any("Yet another" in w.get_text() for w in reader._labels()), "the new reply", 3)
        pump(0.3)
        in_view()
        reader.selection.clear()
    finally:
        win.set_zoom(zoom)
        pump(0.3)
        win.tabs.close(("chat", os.path.realpath(path)))
        pump(0.2)


def test_zoom_does_only_what_shows(app):
    """A zoom's work, counted (issue #51; as Baude's test_zoom_keeps_place and test_zoom_draws_once): a burst of key
    presses is one change of size, no style sheet loaded anew (every widget of every tab styled afresh) and nothing
    written till the keys have stopped; of the chat shown, only what's near the view in the first frame, the rest coming
    back a few messages a frame, and no frame showing a message at the old size, a logo at the old size or the place
    you were reading moved; a tab not shown left as it is till it's shown, then at the new size in its first frame, its
    place kept. Settings → Text Scale: what's shown keeps its place, and the chats not shown wait too. And a label's
    width is had with one pass over its text (widgets.WrapLabel): not laid out at no width, its natural width still
    right."""
    win = app.win
    select(win, "a")
    catalogued(win)
    reply = "`relay_jobs.py` runs each job, and here are words that wrap across the width of the column " * 4
    hidden = win.open_chat(transcript("zoom-hidden", [f"Reply {n}: {reply}" for n in range(120)]), present=False)
    note = win.open_note("Zoomed")
    note.buf.set_text("\n\n".join(f"Line {n} of a page, and @relay_jobs.py" for n in range(200)))
    reader = win.open_chat(transcript("zoom-long", [f"Reply {n}: {reply}" for n in range(240)]), present=False)
    # (a label's width, had without Pango laying it out at no width: the same as a plain label's, its height too)
    wrapped = widgets.WrapLabel(label=reply, wrap=True, wrap_mode=record.Pango.WrapMode.WORD_CHAR)
    plain = Gtk.Label(label=reply, wrap=True, wrap_mode=record.Pango.WrapMode.WORD_CHAR)
    assert wrapped.measure(Gtk.Orientation.HORIZONTAL, -1)[1] == plain.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
    assert wrapped.measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= widgets.WrapLabel.NARROWEST
    assert wrapped.measure(Gtk.Orientation.VERTICAL, 400)[0] == plain.measure(Gtk.Orientation.VERTICAL, 400)[0]
    pump(1.2)  # (past a chat's going to its end as it fills)

    def label_of(row):
        return next(w for w in walk(row) if isinstance(w, Gtk.Label) and w.get_selectable())
    assert isinstance(label_of(reader.rows[0]), widgets.WrapLabel), "a reply's labels are"

    def font(widget):
        return widget.get_pango_context().get_font_description().get_size()

    def place(scroller, extent, what):  # (the top of the view: how far through what's there)
        y, height = extent(what)
        return what, (scroller.get_vadjustment().get_value() - y) / height

    def on_screen(scroller, extent, at):
        y, height = extent(at[0])
        return y + at[1] * height - scroller.get_vadjustment().get_value()

    def row_extent(tab):
        def extent(row):
            b = row.compute_bounds(tab.clamp)[1]
            return b.get_y(), b.get_height()
        return extent

    def read_on(tab, value):
        tab.scroll.get_vadjustment().set_value(value)
        pump(0.3)
        extent = row_extent(tab)
        return place(tab.scroll, extent, next(r for r in tab.rows if sum(extent(r)) > value))

    def each_frame(fn):
        clock = win.get_frame_clock()
        handler = clock.connect("after-paint", lambda _c: fn())
        return lambda: clock.disconnect(handler)

    done = lambda tab: tab.place.job is None and win.zoom_wait is None  # noqa: E731
    win.tabs.show(hidden.tab_key)
    pump(0.5)
    hidden_at = read_on(hidden, 3000)
    hidden_y = on_screen(hidden.scroll, row_extent(hidden), hidden_at)
    hidden_font, hidden_class = font(label_of(hidden_at[0])), hidden.box.zoomed
    win.tabs.show(reader.tab_key)
    pump(0.5)
    at = read_on(reader, 6000)
    extent = row_extent(reader)
    y0, base = on_screen(reader.scroll, extent, at), font(label_of(at[0]))

    # (counted: the zoom's applied, a style sheet's loaded, what's written, which labels' logos are placed again)
    applied, loaded, saved, placed = [], [], [], set()
    apply_zoom, save_now, place_icons = win.apply_zoom, win.store.save_now, widgets.LinkLabel.place_icons
    win.apply_zoom = lambda: (applied.append(win.store.zoom), apply_zoom())[1]
    win.zoom_css.load_from_string = lambda css: loaded.append(css)
    win.store.save_now = lambda: (saved.append(True), save_now())[1]
    widgets.LinkLabel.place_icons = lambda label: (placed.add(label), place_icons(label))[1]
    win.ZOOM_SAVE_MS = 60_000  # (a burst's save not due, however slowly this is going)
    try:
        frames = []

        def frame():
            if not applied:
                return
            shown = [r for r in reader.rows if r.get_visible()]
            frames.append((len(shown), on_screen(reader.scroll, extent, at),
                           [font(label_of(r)) / base for r in shown],
                           [(lb.icon_px, lb.text_px()[2]) for r in shown for lb in walk(r) if getattr(lb, "icons", None)]))
        stop = each_frame(frame)
        for z in (1.1, 1.2, 1.3):  # (a burst of key presses)
            win.set_zoom(z)
        assert not applied, "as the next frame begins"
        wait_for(lambda: frames and done(reader), "the zoom done", 30)
        assert applied == [1.3], f"one change of size for the burst: {applied}"
        assert not loaded, "no style sheet loaded anew: that styles every widget of every window and tab"
        assert not saved and win.zoom_saving is not None, "a save writes every notebook: not while the keys are going"
        pump(0.2)
        stop()
        assert frames[0][0] < len(reader.rows) / 3, f"the first frame's messages: {frames[0][0]} of {len(reader.rows)}"
        assert frames[-1][0] == len(reader.rows), "all back"
        back = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
        assert len(frames) > 3 and max(back) <= record.ZoomPlace.BACK + 1, f"a few a frame: {back}"
        assert not any(getattr(r, "zoom_hidden", False) for r in reader.rows)
        stale = [s for f in frames for s in f[2] if abs(s - 1.3) > 0.03]
        assert not stale, f"{len(stale)} times a message was drawn at another size: {stale[:3]}"
        late = [lg for f in frames for lg in f[3] if lg[0] != lg[1]]
        assert not late, f"a logo at {late[:1]}, not the text's size"
        worst = max((abs(f[1] - y0) for f in frames), default=0)
        assert worst <= 3, f"the place you were reading moved by {worst:.0f}px on a frame"

        # the tabs not shown: nothing of theirs touched
        assert hidden.box.zoomed == hidden_class and font(label_of(hidden_at[0])) == hidden_font, "the chat not shown"
        assert not placed & {lb for lb in walk(hidden.box) if isinstance(lb, widgets.LinkLabel)}, "its logos neither"
        assert note.zoomed != record.zoom_class(1.3), "nor the page"

        # shown: at the new size in its first frame, its place kept, the rest coming back as the chat's did
        frames.clear()
        hidden_extent = row_extent(hidden)

        def frame_there():
            shown = [r for r in hidden.rows if r.get_visible()]
            frames.append((len(shown), on_screen(hidden.scroll, hidden_extent, hidden_at),
                           font(label_of(hidden_at[0])) / hidden_font))
        stop = each_frame(frame_there)
        win.tabs.show(hidden.tab_key)
        assert hidden.box.zoomed == record.zoom_class(1.3), "as it's shown, before its first frame"
        wait_for(lambda: frames and done(hidden), "it's all back", 30)
        pump(0.2)
        stop()
        assert abs(frames[0][2] - 1.3) < 0.03, f"its first frame's at {frames[0][2]}"
        assert frames[0][0] < len(hidden.rows) * 3 / 4 and frames[-1][0] == len(hidden.rows), (frames[0][0], frames[-1][0])
        worst = max(abs(f[1] - hidden_y) for f in frames)
        assert worst <= 3, f"switching to it moved the place by {worst:.0f}px"

        # the same for a page of notes: what's at the top of the view stays there
        win.tabs.show(note.tab_key)
        pump(0.5)
        assert note.zoomed == record.zoom_class(1.3)
        value = (note.scroll.get_vadjustment().get_upper() - note.scroll.get_vadjustment().get_page_size()) / 2
        note.scroll.get_vadjustment().set_value(value)
        pump(0.3)
        lines = note.place.extent
        note_at = place(note.scroll, lines, note.place.split()[0])
        note_y = on_screen(note.scroll, lines, note_at)
        drawn = []
        stop = each_frame(lambda: drawn.append(on_screen(note.scroll, lines, note_at)))
        win.ZOOM_SAVE_MS = 200
        win.set_zoom(1.0)
        wait_for(lambda: drawn and done(note), "the page's zoom done", 30)
        pump(0.2)
        stop()
        assert note.zoomed == record.zoom_class(1.0)
        assert max(abs(y - note_y) for y in drawn) <= 3, f"the page's place moved: {note_y:.0f} -> {drawn[:4]}"

        # let go of the keys: written, once
        wait_for(lambda: saved and win.zoom_saving is None and win.store._pending is None, "the zoom written", 5)
        assert len(saved) == 1, saved
        with open(os.path.join(os.environ["RECORD_DATA"], "records.json")) as f:
            assert json.load(f)["zoom"] == 1.0

        # Settings → Text Scale: every widget's, so the chats not shown are put away till they're shown, place and all
        win.tabs.show(reader.tab_key)
        wait_for(lambda: done(reader), "the chat at the zoom", 30)
        pump(0.3)
        win.tabs.show(hidden.tab_key)
        wait_for(lambda: done(hidden), "the other chat too", 30)
        pump(0.3)
        hidden_at = read_on(hidden, 2000)
        hidden_y = on_screen(hidden.scroll, row_extent(hidden), hidden_at)
        win.tabs.show(reader.tab_key)
        pump(0.5)
        at = read_on(reader, 5000)
        y0 = on_screen(reader.scroll, extent, at)
        drawn = []
        stop = each_frame(lambda: drawn.append(on_screen(reader.scroll, extent, at)))
        win.set_scale("text_scale", 1.2)
        win.set_scale("text_scale", 1.3)
        assert widgets.scale_setting("text_scale") == 1.3 and win.scale_wait is not None, "once for a few quick clicks"
        wait_for(lambda: win.scale_wait is None and drawn and done(reader), "the text's size changed", 30)
        pump(0.2)
        stop()
        assert max(abs(y - y0) for y in drawn) <= 3, f"the place moved: {y0:.0f} -> {drawn[:4]}"
        assert not any(r.get_visible() for r in hidden.rows), "the chat not shown waits"
        drawn = []
        stop = each_frame(lambda: drawn.append(on_screen(hidden.scroll, row_extent(hidden), hidden_at)))
        win.tabs.show(hidden.tab_key)
        wait_for(lambda: drawn and done(hidden), "it's back", 30)
        pump(0.2)
        stop()
        assert all(r.get_visible() for r in hidden.rows)
        assert max(abs(y - hidden_y) for y in drawn) <= 3, f"its place moved: {hidden_y:.0f} -> {drawn[:4]}"
    finally:
        widgets.LinkLabel.place_icons = place_icons
        del win.apply_zoom, win.zoom_css.load_from_string, win.store.save_now, win.ZOOM_SAVE_MS
        widgets.save_settings(text_scale=1.0)
        widgets.apply_scales(win.get_display())
        win.set_zoom(1.0)
        pump(0.2)
        for key in (hidden.tab_key, reader.tab_key, note.tab_key):
            win.tabs.close(key)
        win.store.flush()
        pump(0.2)


def live_reply(path, text, mid):
    with open(path, "a") as f:
        f.write(json.dumps({"type": "assistant", "cwd": REPO, "uuid": mid,
                            "message": {"id": mid, "role": "assistant", "content": [{"type": "text", "text": text}]}})
                + "\n")


def test_reader_is_live(app):
    """A chat read follows its transcript as Baude's chat does (livefeed.Tail): what's written shows at once, with no
    tick to wait for, half a line waits for its rest, a reply written in several records stays one, a chat rewritten
    starts again, and with its tab closed nothing's left reading it."""
    win = app.win
    path = transcript("live", ["First reply"])
    real = os.path.realpath(path)
    reader = win.open_chat(path)
    said = lambda: [row.item["text"] for row in reader.rows]  # noqa: E731
    try:
        assert said() == ["How do the jobs run?", "First reply"], said()
        assert reader.tail.monitor is not None and reader.tail.ticking is None, "file events: nothing polls"
        for n in range(3):  # (each well inside the second and a bit a poll took: one of them would have been late)
            live_reply(path, f"Written {n}", f"live{n}")
            wait_for(lambda: f"Written {n}" in said(), f"written {n} shown at once", timeout=0.5)
        line = json.dumps({"type": "assistant", "cwd": REPO, "uuid": "half", "message": {
            "id": "half", "role": "assistant", "content": [{"type": "text", "text": "Half a line"}]}}) + "\n"
        with open(path, "a") as f:
            f.write(line[:30])
        pump(0.2)
        assert "Half a line" not in said(), "not till the rest of it's written"
        with open(path, "a") as f:
            f.write(line[30:])
        wait_for(lambda: "Half a line" in said(), "the rest of the line", timeout=0.5)
        count = len(reader.rows)
        live_reply(path, "and more of it", "half")
        wait_for(lambda: reader.rows[-1].item["text"] == "Half a line\n\nand more of it", "the same reply, grown", 0.5)
        assert len(reader.rows) == count and reader.rows[-1].prose.text.endswith("and more of it")

        with open(path, "w") as f:
            f.write("")
        live_reply(path, "Started over", "again")
        wait_for(lambda: said() == ["Started over"], lambda: f"a chat rewritten starts again: {said()}", timeout=1)
        assert [w for w in walk(reader.box) if isinstance(w, Gtk.Label) and w.get_text() == "First reply"] == []
    finally:
        win.tabs.close(("chat", real))
    assert (reader.tail.monitor, reader.tail.idle, reader.tail.ticking, reader.follow.layout) == (None,) * 4, \
        "its tab closed: nothing's left reading it"
    count = len(reader.rows)
    live_reply(path, "Too late", "late")
    pump(0.3)
    assert len(reader.rows) == count
    os.remove(path)


def test_reader_keeps_to_the_end(app):
    """As Baude's chat does (livefeed.FollowEnd): read to its end, a chat stays there as it's written; scrolled up, even
    right after a write, it stays where it was put; back all the way down, it follows again."""
    win = app.win
    path = transcript("following", [f"Reply {n}\n\n" + "Words " * 80 for n in range(40)])
    reader = win.open_chat(path)
    adj = reader.scroll.get_vadjustment()
    at_end = lambda: adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1  # noqa: E731
    long_reply = "\n\n".join("More words " * 40 for _ in range(6))
    try:
        wait_for(lambda: adj.get_page_size() > 0 and adj.get_upper() > adj.get_page_size() + 500 and at_end(),
                 "opened at its end")
        assert reader.follow.stick
        upper = adj.get_upper()
        live_reply(path, long_reply, "more")
        wait_for(lambda: adj.get_upper() > upper and at_end(), "a long reply followed")

        upper = adj.get_upper()
        live_reply(path, long_reply, "yank")  # (and scrolled up before it's settled: its 900 ms timer took it back)
        wait_for(lambda: reader.rows[-1].item["id"] == "yank", "written")
        adj.set_value(adj.get_value() - 300)
        place = adj.get_value()
        pump(1.2)
        assert adj.get_upper() > upper and adj.get_value() == place, (adj.get_value(), place)
        assert not reader.follow.stick
        live_reply(path, "One more", "stays")
        wait_for(lambda: reader.rows[-1].item["id"] == "stays", "written")
        pump(0.3)
        assert adj.get_value() == place, "still where it was put"

        adj.set_value(adj.get_upper())
        pump(0.1)
        assert reader.follow.stick, "all the way down: following again"
        upper = adj.get_upper()
        live_reply(path, long_reply, "again")
        wait_for(lambda: adj.get_upper() > upper and at_end(), "followed again")
    finally:
        win.tabs.close(("chat", os.path.realpath(path)))
        os.remove(path)


def test_restart_for_update(app):
    """A new version found (update.Updater): Record restarts once idle (no question being answered, no dialog, you
    not at it), leaving the app you were in and what it's updated by; Settings → Auto restart on update, Baude's own."""
    win = app.win
    select(win, "a")
    win.last_input = time.monotonic() - record.update.QUIET_S - 1  # (the real keys and presses of the tests before)
    assert win.idle()
    win.chat.ask = object()
    assert not win.idle(), "a question being answered"
    win.chat.ask = None
    win.is_active = lambda: True
    try:
        win.last_input = time.monotonic()
        assert not win.idle(), "you at it"
        win.last_input -= record.update.QUIET_S + 1
        assert win.idle()
    finally:
        del win.is_active
    rows = [w for w in walk(record.Settings(win).get_visible_page()) if isinstance(w, record.Adw.SwitchRow)]
    assert len(rows) == 1
    calls, saved = [], (os.execv, record.update.front_app)
    os.execv, record.update.front_app = (lambda exe, argv: calls.append(argv)), (lambda: 4242)
    try:
        win.restart({"from": "a", "to": "b", "commits": 2})
    finally:
        os.execv, record.update.front_app = saved
    state = json.load(open(record.restart_path()))
    os.remove(record.restart_path())
    assert calls and state["front"] == 4242 and state["updated"]["commits"] == 2 and state["record"] == "a"


def test_restart_waits_for_keys_and_presses(app):
    """Real presses and keys count as you being at it (update.watch_input), and still go where they were going; the pointer
    only moving doesn't count."""
    import mouse
    if not mouse.available():
        return
    win, update = app.win, record.update
    select(win, "a")
    box, page = win.chat.composer, win.right_stack.get_visible_child_name()
    win.is_active = lambda: True
    try:
        win.right_slide.set_shown(True)
        win.right_stack.set_visible_child_name("record")  # (another test's may be up: the box isn't, then)
        box.get_buffer().set_text("")
        box.grab_focus()
        pump(0.3)
        ok, bounds = box.compute_bounds(win)
        assert ok
        x, y = bounds.get_x() + bounds.get_width() / 2, bounds.get_y() + bounds.get_height() / 2
        hand = mouse.Mouse(win)
        stale = time.monotonic() - update.QUIET_S - 1
        win.last_input = stale
        assert update.quiet(win)
        hand.post(mouse.MOVED, x, y)
        hand.settle()
        assert win.last_input == stale, "the pointer only moving"
        hand.down(x, y)
        hand.up(x, y)
        assert win.last_input > stale and not update.quiet(win), "a press"
        win.last_input = stale
        hand.type("a")
        assert win.last_input > stale and not update.quiet(win), "a key"
        buf = box.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        assert text == "a", text
        buf.set_text("")
    finally:
        del win.is_active
        win.right_stack.set_visible_child_name(page)


def test_restart_keeps_what_is_typed(app):
    """What's being written comes back from a restart: the question, what its markup hides too, with its quote and its
    caret; on a page of notes, the caret, a new title not yet taken, and where you were typing."""
    win = app.win
    select(win, "a")
    d = win.open_note(record.HOME)
    words = "Is **this** right?"
    win.chat.composer.get_buffer().set_text(words)
    win.chat.composer.get_buffer().place_cursor(win.chat.composer.get_buffer().get_iter_at_offset(7))
    pump(0.1)
    d.buf.set_text("Some notes\nand more")
    d.buf.select_range(d.buf.get_iter_at_offset(5), d.buf.get_iter_at_offset(9))
    d.title.set_text("A new name")
    d.view.grab_focus()
    pump(0.1)
    state = json.loads(json.dumps(win.state()))
    assert state["composer"] == words and state["composer_caret"] == [7, 7], state
    assert state["note_caret"] == [5, 9] and state["note_title"] == "A new name" and state["focus"] == "note"
    win.chat.composer.get_buffer().set_text("")
    d.buf.place_cursor(d.buf.get_start_iter())
    d.title.set_text(record.short_page(record.HOME))
    win.restore(state)
    pump(0.1)
    d = win.tabs.current()
    assert widgets_text(win.chat.composer) == words and record.widgets.caret_of(win.chat.composer) == [7, 7]
    assert record.widgets.caret_of(d.view) == [5, 9] and d.title.get_text() == "A new name" and win.get_focus() is d.view
    d.title.set_text(record.short_page(record.HOME))
    win.chat.composer.get_buffer().set_text("")
    d.buf.set_text("")


def widgets_text(view):
    return record.widgets.typed_text(view)


def test_restart_keeps_scroll_positions(app):
    """Where you'd read to comes back from a restart: the page of notes shown and one behind it, the chat read (which
    otherwise goes to its end as it fills), and the Record's conversation. Scrolled by hand as it settles, it stays
    where you put it."""
    win = app.win
    select(win, "a")
    long_note = "\n\n".join(f"Line {n} of a long page" for n in range(300))
    win.open_note("Behind").buf.set_text(long_note)
    d = win.open_note(record.HOME)
    d.buf.set_text(long_note)
    chat = transcript("scrolled", [f"Reply {n}\n\n" + "Words " * 80 for n in range(40)])
    reader = win.open_chat(chat)
    win.right.set_visible(True)
    pump(1.2)  # (past a chat's going to its end as it fills)

    def settle(scroller, value):
        adj = scroller.get_vadjustment()
        wait_for(lambda: adj.get_upper() > adj.get_page_size() + value, "it's laid out")
        adj.set_value(value)
        return adj.get_value()
    read = settle(reader.scroll, 500)
    win.tabs.show(("note", "Behind"))
    pump(0.3)
    behind = settle(win.tabs.get(("note", "Behind")).scroll, 700)
    win.tabs.show(("note", record.HOME))
    pump(0.3)
    shown = settle(d.scroll, 900)
    state = json.loads(json.dumps(win.state()))
    places = {tuple(k): p for k, p in state["places"]}
    assert places[("note", record.HOME)]["value"] == shown and not places[("note", record.HOME)]["at_end"]
    assert places[("chat", chat)]["value"] == read, (places, read)
    assert places[("note", "Behind")]["value"] == behind, (places, behind)

    win.let_go()  # (a window made again from that, as a restart's is)
    app.app.win = new = record.Window(app.app)
    new.restore(state)
    new.present()
    win.destroy()
    try:
        page = new.tabs.get(("note", record.HOME))
        assert new.tabs.current() is page and page.scroll.held
        wait_for(lambda: page.scroll.get_vadjustment().get_value() == shown, "the page back where it was")
        pump(1.2)  # (past what would have taken a chat to its end)
        assert page.scroll.get_vadjustment().get_value() == shown
        assert new.tabs.get(("chat", chat)) is not None, "the chats open come back with the window"
        new.tabs.show(("chat", chat))
        tab = new.tabs.get(("chat", chat))
        wait_for(lambda: abs(tab.scroll.get_vadjustment().get_value() - read) <= 1, "the chat back where it was")
        pump(1.2)
        assert abs(tab.scroll.get_vadjustment().get_value() - read) <= 1, "not taken to its end as it fills"
        new.tabs.show(("note", "Behind"))
        tab = new.tabs.get(("note", "Behind"))
        wait_for(lambda: tab.scroll.get_vadjustment().get_value() == behind, "the page behind back where it was")
        wait_for(lambda: not page.scroll.held, "let go of", 4)
    finally:
        new.tabs.close(("chat", chat))
        os.remove(chat)

    fresh = Gtk.ScrolledWindow(child=Gtk.Label(label="x"))  # (never shown: nothing to put back)
    assert record.widgets.scroll_place(fresh) is None
    record.widgets.hold_place(fresh, None)
    assert not getattr(fresh, "held", False)


def test_left_or_right_leaves_what_was_just_formatted(app):
    """In the notes and the Record's box: a selection formatted, then Right or Left, and what's typed next is plain, the
    formatting staying round what was selected."""
    win = app.win
    select(win, "b")
    d = win.open_note("Formatting")
    none = Gdk.ModifierType(0)
    star, right, left = Gdk.KEY_asterisk, Gdk.KEY_Right, Gdk.KEY_Left
    try:
        for view, key, text in ((d.view, d._key, None), (win.chat.composer, win.chat._key, None)):
            buf = view.get_buffer()
            buf.set_text("make this bold")
            buf.select_range(buf.get_iter_at_offset(5), buf.get_iter_at_offset(9))
            assert key(None, star, 0, none) is True
            assert key(None, right, 0, none) is True and not buf.get_has_selection()
            buf.insert_at_cursor("!")
            assert record.widgets.typed_text(view) == "make **this**! bold"
            buf.set_text("make this bold")
            buf.select_range(buf.get_iter_at_offset(5), buf.get_iter_at_offset(9))
            key(None, star, 0, none)
            assert key(None, left, 0, none) is True
            buf.insert_at_cursor("!")
            assert record.widgets.typed_text(view) == "make !**this** bold"
            buf.set_text("")
    finally:
        win.tabs.close(("note", "Formatting"))
        by_id(win, "b")["pages"].pop("Formatting", None)


def test_images_fly_into_the_large_view(app):
    """Record's large view opens out of the image clicked and closes back into it, in steps, as Baude's does (the one
    widget: test_lightbox_flies there has the rest), ending exactly as it does at once."""
    win = app.win
    select(win, "a")
    png = Gdk.MemoryTexture.new(800, 400, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes([9, 90, 200, 255]) * (800 * 400)),
                                3200).save_to_png_bytes().get_data()
    page = win.open_fixes()
    page.draw_detail = lambda: None  # (what's in it is this test's: not what was read, or read last time)
    pic = Gtk.Picture(paintable=Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)), css_classes=["fix-shot"])
    widgets.make_image_interactive(pic, png, name="a.png")
    holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.clamp.set_child(holder)
    holder.append(pic)
    pump(0.3)
    page.stack.set_visible_child_name("main")  # (after what it read when it opened has said what to show)
    pump(0.3)
    assert pic.get_mapped() and pic.get_width() > 0
    box, big = win.lightbox, win.lightbox.picture
    saved, widgets.Lightbox.FLY_MS = widgets.Lightbox.FLY_MS, 240

    def frames(go):
        got, clock = [], win.get_frame_clock()
        watch = clock.connect("after-paint", lambda _c: big.flight and got.append((box.shown, big.flown()[0][2])))
        go()
        wait_for(lambda: box.flying is None, "it flew")
        clock.disconnect(watch)
        return [f for f in got if 0 < f[0] < 1]

    try:
        ok, bounds = pic.compute_bounds(win)
        assert ok
        opening = frames(pic.image_actions.view)
        assert len(opening) > 2 and opening == sorted(opening), opening
        final = box.image_rect()
        assert box.get_visible() and box.shown == 1.0 and big.flight is None and box.get_can_target()
        assert box.stage.get_overflow() == Gtk.Overflow.HIDDEN and pic.get_opacity() == 1.0
        assert all(0 < w < final[2] for _t, w in opening), (opening, final)

        def closing():
            box.on_key(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
            assert box.get_visible() and box.closing and not box.get_can_target(), "going, and clicks go through"
            assert box.source is not None and pic.get_opacity() == 0.0
            image = box.source[0]  # (it's letterboxed in its widget)
            assert abs(image[2] / image[3] - 2) < 0.01 and bounds.get_x() - 0.01 <= image[0], image
            assert image[2] <= bounds.get_width() + 0.01 and image[3] <= bounds.get_height() + 0.01, image
        back = frames(closing)
        assert len(back) > 2 and back == sorted(back, reverse=True), back
        assert not box.get_visible() and box.shown == 0.0 and big.get_paintable() is None and not box.items
        assert box.get_can_target() and pic.get_opacity() == 1.0 and big.flight is None
    finally:
        widgets.Lightbox.FLY_MS = saved
        win.tabs.close(("fixes",))
        pump(0.1)


def test_side_bars_slide(app):
    """The notebooks and the right bar open and close smoothly, in steps; the notebooks' toggles stay one switch."""
    saved, widgets.SLIDE_MS = widgets.SLIDE_MS, 180
    win = app.win
    slide, column = win.left_slide, win.left_slide.fixed
    try:
        pump(0.2)
        toggles = slide.toggles
        assert len(toggles) == 2 and column.showing is None
        widths = []
        toggles[0].set_active(False)
        assert slide.pane.get_visible(), "there until it's closed"
        while slide.anim is not None:
            widths.append(column.showing)
            pump(0.02)
        assert not slide.pane.get_visible() and not toggles[1].get_active() and column.showing is None
        steps = [w for w in widths if w is not None]
        assert len(steps) > 2 and steps == sorted(steps, reverse=True), widths
        toggles[1].set_active(True)  # (the other toggle: the one switch)
        wait_for(lambda: slide.anim is None, "it opened")
        assert slide.pane.get_visible() and toggles[0].get_active() and column.showing is None

        right = win.right_slide  # (the right bar)
        at = win.paned.get_position()
        moved = []
        watch = win.paned.connect("notify::position", lambda p, _: moved.append(p.get_position()))
        win.right_toggle.set_active(False)
        assert win.right.get_visible()
        wait_for(lambda: not win.right.get_visible(), "it closed")
        assert len(moved) > 2 and all(b - a >= -1 for a, b in zip(moved, moved[1:-1])), moved  # (a pixel's rounding)
        win.right_toggle.set_active(True)
        wait_for(lambda: right.anim is None, "it opened")
        win.paned.disconnect(watch)
        assert win.right.get_visible() and abs(win.paned.get_position() - at) <= 1
    finally:
        widgets.SLIDE_MS = saved


def test_nothing_jumps_as_the_notebooks_slide(app):
    """Frame by frame, in the window, as it's painted (the frame clock's after-paint: it runs offscreen too, some 30 frames
    a second; the window's native buttons reserve their room offscreen as on the screen): as the notebooks close and open
    the window's buttons' room and their toggle stay put (the notebooks' bar's toggle, and the tabs' bar's where the
    column's edge has cut that off), and the first tab moves one way only, never over them, no further in a frame than the
    column did, from where it sits steady to where it sits steady. The right bar shown or hidden; its own slide, the
    notebooks shown or hidden, leaves all that where it is."""
    saved, widgets.SLIDE_MS = widgets.SLIDE_MS, 180
    win = app.win
    select(win, "a")
    win.open_note(record.HOME)
    for key in [k for k in win.tabs.pages if k != ("note", record.HOME)]:
        win.tabs.close(key)  # (the tabs to themselves: too many for the bar and the first is scrolled, as it narrows)
    slide, column, right = win.left_slide, win.left_slide.fixed, win.right_slide
    heads = (next(w for w in walk(win.books) if isinstance(w, record.Adw.HeaderBar)),
             win.tabs.bar.get_ancestor(record.Adw.HeaderBar))
    rooms = [next(w for w in walk(h) if isinstance(w, Gtk.WindowControls) and w.get_side() == Gtk.PackType.START)
             for h in heads]

    def place(widget):
        ok, bounds = widget.compute_bounds(win)
        assert ok
        return bounds.get_x(), bounds.get_width()

    def frame():
        steady = win.books.get_width() if slide.pane.get_visible() else 0
        return {"column": steady if column.showing is None else column.showing,
                "rooms": [place(r) for r in rooms if r.get_mapped()],
                "toggles": [place(t) for t in slide.toggles if t.get_mapped()], "cut": not slide.toggles[0].get_mapped(),
                "hidden": slide.toggles[1].get_mapped(), "right": place(win.right_toggle)[0],
                # (no further left than the bar: with many open, the first is scrolled off under its edge)
                "tab": max(place(win.tabs.bar)[0], min(place(w)[0] for w in walk(win.tabs.bar)
                                                       if w.get_css_name() == "tab" and w.get_mapped()))}

    def frames(moving, go):
        got, clock = [], win.get_frame_clock()
        watch = clock.connect("after-paint", lambda _c: got.append(frame()))
        go()
        wait_for(lambda: moving.anim is None, "it moved")
        pump(0.15)
        clock.disconnect(watch)
        assert len(got) > 2, ("frames came", got)
        return got

    def nothing_jumps(got, start, end):
        trail = [start["tab"]] + [f["tab"] for f in got]
        assert trail[-1] == end["tab"], ("ends where it sits steady", trail, end["tab"])
        assert trail == sorted(trail, reverse=start["tab"] > end["tab"]), ("one way only", trail)
        columns = [start["column"]] + [f["column"] for f in got]
        for tabs, widths in zip(zip(trail, trail[1:]), zip(columns, columns[1:])):
            assert abs(tabs[1] - tabs[0]) <= abs(widths[1] - widths[0]) + 1, ("no step", trail, columns)
        at, wide = start["toggles"][0]
        room = (start["rooms"] or [(0, 0)])[0]  # (none, where the window has no buttons at its start)
        for f in got:
            assert f["right"] == start["right"], "the right bar's toggle's not in this"
            assert bool(f["rooms"]) == bool(start["rooms"]) and all(r == room for r in f["rooms"]), ("the room stays put", f, room)
            assert f["toggles"] and all(t == (at, wide) for t in f["toggles"]), ("the toggle stays put", f, at)
            assert f["hidden"] or f["cut"] or f["column"] >= at + wide, ("the toggle's in sight, all of it", f, at)
            assert f["tab"] >= at + wide > at >= room[0] + room[1], ("nothing over the buttons", f, room)
        if sys.platform == "darwin":  # (the traffic lights are native: they take room only if it's asked for)
            assert room[1] > 40, room

    try:
        pump(0.3)
        for bar_shown in (True, False):  # (the right bar)
            right.set_shown(bar_shown)
            wait_for(lambda: right.anim is None, "the right bar settled")
            pump(0.15)
            shown = frame()
            closing = frames(slide, lambda: slide.set_shown(False))
            hidden = frame()
            assert not slide.pane.get_visible() and hidden["hidden"] and hidden["cut"]
            nothing_jumps(closing, shown, hidden)
            assert hidden["tab"] < shown["tab"], "the tabs have what the notebooks had, past the buttons and the toggle"
            nothing_jumps(frames(slide, lambda: slide.set_shown(True)), hidden, shown)
            assert frame() == shown and slide.pane.get_visible() and not shown["hidden"]

        slide.set_shown(False)  # (turned back part way: from where it's got to, in both directions)
        wait_for(lambda: column.showing is not None and column.showing < 60, "it closed some")
        back = frames(slide, lambda: slide.set_shown(True))
        assert [f for f in back if f["tab"] != shown["tab"]] and back[-1]["tab"] == shown["tab"]
        trail = [f["tab"] for f in back]
        assert trail == sorted(trail), ("back the way it came", trail)

        right.set_shown(True)  # (its slide, on its own: nothing on the left moves, or on the left's account)
        wait_for(lambda: right.anim is None, "the right bar settled")
        for side_shown in (True, False):
            slide.set_shown(side_shown)
            wait_for(lambda: slide.anim is None, "the notebooks settled")
            pump(0.15)
            steady = frame()
            at = win.paned.get_position()
            for shown_now in (False, True):
                moved = frames(right, lambda: right.set_shown(shown_now))
                assert win.right.get_visible() == shown_now
                assert all((f["tab"], f["toggles"], f["rooms"]) == (steady["tab"], steady["toggles"], steady["rooms"])
                           for f in moved), moved
            assert abs(win.paned.get_position() - at) <= 1 and frame()["right"] == steady["right"]
    finally:
        slide.set_shown(True)
        right.set_shown(True)
        wait_for(lambda: slide.anim is None and right.anim is None, "all settled")
        widgets.SLIDE_MS = saved


def test_tabs_fill_the_top_bar(app):
    """The tabs run the whole width of the bar above the page, from the left toggle's place to the right toggle, with no gap
    left where a button or a centred title would have been."""
    win = app.win
    select(win, "a")
    pump(0.2)
    bar, head = win.tabs.bar, win.tabs.bar.get_ancestor(record.Adw.HeaderBar)
    ok, tabs = bar.compute_bounds(head)
    ok2, toggle = win.right_toggle.compute_bounds(head)
    assert ok and ok2 and tabs.get_x() < 24, ("from the bar's left, the notebooks showing", tabs.get_x())
    assert tabs.get_width() > head.get_width() - 140, (tabs.get_width(), head.get_width())
    assert toggle.get_x() - (tabs.get_x() + tabs.get_width()) < 24, ("to the right toggle", tabs.get_x(), tabs.get_width(), toggle.get_x())
    win.left_slide.set_shown(False)  # (the notebooks hidden: just after their toggle, here now)
    pump(0.2)
    ok, tabs = bar.compute_bounds(head)
    ok2, left = win.left_slide.toggles[1].compute_bounds(head)
    buttons = next(w for w in walk(head) if isinstance(w, Gtk.WindowControls) and w.get_side() == Gtk.PackType.START)
    ok3, room = buttons.compute_bounds(head)
    assert ok3 and left.get_x() >= room.get_x() + room.get_width(), "the toggle after the window's buttons, not under them"
    if sys.platform == "darwin":  # (the traffic lights are native: they take room only if it's asked for)
        assert buttons.get_use_native_controls() and room.get_width() > 40, room.get_width()
    assert tabs.get_x() - (left.get_x() + left.get_width()) < 24 and tabs.get_x() >= left.get_x() + left.get_width() - 1, tabs.get_x()
    win.left_slide.set_shown(True)
    pump(0.1)


def test_selection_runs_across_paragraphs(app):
    """A real mouse: a drag from one paragraph of the chat read into others selects what it passes, down or up, however
    many labels that is; Copy takes it all and the Record is quoted it, whole. In one paragraph it's still GTK's, and the
    next press lets all of it go. (#47)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    r = win.open_chat(CHAT)
    pump(0.3)
    assert r.get_mapped(), "the chat's on show"
    first, second = next(w for w in r._labels() if "golden" in w.get_text()), \
        next(w for w in r._labels() if "Merge" in w.get_text())
    question = next(w for w in r._labels() if "How do the jobs" in w.get_text())
    hand = mouse.Mouse(win)

    def at(label, i):
        rect = widgets.passage_rects(label, i, i + 1, win)[0]
        return rect.get_x() + 1, rect.get_y() + rect.get_height() / 2

    def drag(*points):
        hand.down(*points[0])
        pump(0.05)
        for p in points[1:]:
            hand.drag(*p)
            pump(0.05)
        hand.up(*points[-1])
        pump(0.05)

    drag(at(first, 3), at(first, 9))
    assert first.get_selection_bounds()[1:] == (3, 9), first.get_selection_bounds()  # (in one paragraph: GTK's)
    assert [p["text"] for p in r.picks] == [first.get_text()[3:9]], "and it's picked, and quoted, as the drag ends"
    assert win.chat.quote == first.get_text()[3:9].strip(), win.chat.quote
    assert not r.selection.shown
    drag(at(question, 4), at(first, 5), at(second, 5))
    assert not first.get_selection_bounds()[0], "GTK's let go: only one label can hold one"
    assert [p[1:] for p in r.selection.parts()] == [(4, len(question.get_text())), (0, len(first.get_text())), (0, 5)]
    whole = "\n\n".join((question.get_text()[4:], first.get_text().replace("\u2063", ""), second.get_text()[:5]))
    assert r.selection.text() == whole, r.selection.text()
    assert win.chat.quote == whole.strip(), win.chat.quote
    assert [p["text"] for p in r.picks] == [whole], "one passage, not numbered"
    shot(win, "record-selection-across")
    copied = []
    clipboard = r.box.get_clipboard
    r.box.get_clipboard = lambda: type("Clip", (), {"set": lambda self, v: copied.append(v)})()
    try:
        assert r.selection._key(None, Gdk.KEY_c, 0, Gdk.ModifierType.META_MASK) is True
    finally:
        del r.box.get_clipboard
    assert copied == [whole], copied
    drag(at(second, 4), at(first, 2))
    assert [p[1:] for p in r.selection.parts()] == [(2, len(first.get_text())), (0, 4)], "up, the last one let go"
    plain = first.get_text().index("each job")  # (not on a link: a click there would open it)
    hand.down(*at(first, plain))
    pump(0.05)
    hand.up(*at(first, plain))
    pump(0.05)
    assert not r.selection.parts(), "a click lets it all go"
    hand.down(*at(second, 3), flags=mouse.SHIFT)
    pump(0.05)
    hand.up(*at(second, 3), flags=mouse.SHIFT)
    pump(0.05)
    assert [p[1:] for p in r.selection.parts()] == [(plain, len(first.get_text())), (0, 3)], "Shift and a click: on to there"
    assert not first.get_selection_bounds()[0] and not second.get_selection_bounds()[0]
    hand.right_click(*at(second, 1))
    pump(0.1)
    menus = [w for w in walk(r.box) if isinstance(w, Gtk.PopoverMenu)]
    assert len(menus) == 1 and r.selection.parts(), "a right click on it: its menu, and it stays"
    copied.clear()
    r.get_clipboard = lambda: type("Clip", (), {"set": lambda self, v: copied.append(v)})()
    try:
        r.box.activate_action("chat-text.item0", None)
    finally:
        del r.get_clipboard
    assert copied == [r.selection.text().strip()], copied
    where = r.scroll.get_vadjustment().get_value()
    menus[0].popdown()
    pump(0.3)
    assert r.scroll.get_vadjustment().get_value() == where, "the focus coming back doesn't move the chat"
    assert not first.get_selection_bounds()[0] and not second.get_selection_bounds()[0], "nor selects a label"
    assert r.selection.parts(), "and it's still selected"
    assert r.selection._key(None, Gdk.KEY_a, 0, Gdk.ModifierType.META_MASK) is True
    labels = list(r._labels())
    assert len(r.selection.parts()) == len(labels), "⌘A: the whole chat"
    r.selection.select_all()
    assert len(r.picks) == 1 and r.selection.shown
    rect = widgets.passage_rects(first, plain, plain + 1, r.over)[0]
    x, y = rect.get_x() + 1, rect.get_y() + rect.get_height() / 2
    press = Press(x, y, Gdk.ModifierType.CONTROL_MASK)
    r._pick_begin(press, x, y)
    r._pick_end(press, 0, 0)
    assert not r.picks and not r.selection.shown, "Ctrl and a click on it lets it go, drawn by whichever"
    hand.down(*at(first, plain))
    pump(0.05)
    hand.up(*at(first, plain))
    pump(0.05)
    win.chat.set_quote("")


def test_citation_link_says_where_a_passage_is():
    """A citation link holds the chat, the message and the passage's words; a long passage only its ends. The words before
    it tell it from the same words said elsewhere, and its text survives what a link can't hold. (#48)"""
    link = record.citation_link("abc-1", "msg_1", "said before it", "a (small) - passage, with [marks]")
    shown, url = record.MD_LINK_RE.fullmatch(link).groups()
    assert shown == "“a (small) - passage, with marks”", shown
    assert record.parse_citation(url) == {"session": "abc-1", "message": "msg_1", "prefix": "said before it", "length": 33,
                                          "start": "a (small) - passage, with [marks]", "end": ""}, url
    assert "@" not in record.MD_LINK_RE.fullmatch(record.citation_link("s", "m", "", "ask @orders-api why")).group(1)
    weird = "100% of it, 50-50: “odd” & é ☃ a=b#c?d"
    assert record.passage_in([weird], record.parse_citation(record.MD_LINK_RE.fullmatch(
        record.citation_link("s", "m", "", weird)).group(2))) == [(0, 0, len(weird))], "what a link can't hold as it is"
    long = " ".join(f"w{n}" for n in range(40))
    url = record.MD_LINK_RE.fullmatch(record.citation_link("s", None, "", long)).group(2)
    cite = record.parse_citation(url)
    assert (cite["start"], cite["end"], cite["prefix"]) == ("w0 w1 w2 w3 w4 w5 w6 w7", " ".join(f"w{n}" for n in range(32, 40)),
                                                            ""), cite
    assert record.parse_citation("https://example.com") is None
    assert record.parse_citation("baude-chat:../../etc?message=#:~:text=x") is None, "not a chat's id"

    texts = ["the job ran", "and \u2063the  job\nran again", "the job ran at last"]
    assert record.passage_in(texts, {"start": "the job ran", "end": "", "prefix": ""}) == [(0, 0, 11)]
    assert record.passage_in(texts, {"start": "the job ran", "end": "", "prefix": "again"}) == [(2, 0, 11)]
    assert record.passage_in(texts, {"start": "the job ran", "end": "", "prefix": "and"}) == [(1, 5, 17)], "its logo, its spaces"
    assert record.passage_in(texts, {"start": "job ran again", "end": "at last", "prefix": ""}) == [(1, 10, 23), (2, 0, 19)]
    assert record.passage_in(texts, {"start": "the job ran", "end": "", "prefix": "gone"}) == [(0, 0, 11)], "its first place"
    assert record.passage_in(texts, {"start": "never said", "end": "", "prefix": ""}) is None
    assert record.passage_in(texts, {"start": "the job", "end": "never said", "prefix": ""}) is None
    again = ["one two three four, more words, one two three four"]
    assert record.passage_in(again, {"start": "one", "end": "three four", "prefix": "", "length": 50}) == [(0, 0, 50)], \
        "its last words said twice: as long as it was"
    assert record.passage_in(again, {"start": "one", "end": "three four", "prefix": "", "length": 18}) == [(0, 0, 18)]


def find_all(root, kind):
    """Every widget of a kind under root."""
    out, kid = [], root.get_first_child()
    while kid is not None:
        out += ([kid] if isinstance(kid, kind) else []) + find_all(kid, kind)
        kid = kid.get_next_sibling()
    return out


def test_record_pane_has_no_name_over_it(app):
    """The conversation's pane doesn't say the notebook's name again: only its clear button, at the top right. (#101)"""
    win = app.win
    select(win, "a")
    pump(0.2)
    chat, name = win.chat, record.full_name(win.current)
    head = chat.get_first_child()
    kids = []
    kid = head.get_first_child()
    while kid is not None:
        kids.append(kid)
        kid = kid.get_next_sibling()
    assert len(kids) == 1, kids
    assert not any(isinstance(w, Gtk.Label) and w.get_text() == name for w in find_all(head, Gtk.Label)), "no name"
    ok, at = kids[0].compute_point(chat, widgets.Graphene.Point().init(kids[0].get_width(), 0))
    assert chat.get_width() - at.x <= 16, ("at the right", chat.get_width(), at.x)
    # renaming the notebook, with nothing there to set
    win.rename()
    pump(0.2)
    d = win.get_visible_dialog()
    d.get_extra_child().get_first_child().set_text(name)
    d.emit("response", "ok")
    d.close()
    pump(0.2)
    assert win.current["name"] == name and win.get_visible_dialog() is None


def test_citation_links(app):
    """A real mouse: select in the chat read, right-click, Copy citation link, and in the notes that's a link which opens
    the chat at the passage, shaded till the next click, across paragraphs too. A chat that isn't here, or whose words
    are gone, says so. (#48)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    long = transcript("cited-chat", [f"Reply number {n} says something.\n\nIts second part, number {n}." for n in range(50)])
    r = win.open_chat(long)
    pump(1.1)
    labels = list(r._labels())
    near = next(w for w in labels if "Reply number 44 " in w.get_text())
    after = next(w for w in labels if "Its second part, number 44." in w.get_text())
    hand = mouse.Mouse(win)
    r.scroll.get_vadjustment().set_value(r.scroll.get_vadjustment().get_upper())
    pump(0.1)

    def at(label, i):
        rect = widgets.passage_rects(label, i, i + 1, win)[0]
        return rect.get_x() + 1, rect.get_y() + rect.get_height() / 2

    hand.down(*at(near, 6))
    pump(0.05)
    hand.drag(*at(near, 12))
    pump(0.05)
    hand.up(*at(near, 12))
    pump(0.1)
    assert [p["text"] for p in r.picks] == [near.get_text()[6:12]], "within a paragraph: picked, as ever"
    copied = []
    r.get_clipboard = lambda: type("Clip", (), {"set": lambda self, v: copied.append(v)})()
    hand.right_click(*at(near, 8))
    pump(0.1)
    menus = [w for w in walk(r.box) if isinstance(w, Gtk.PopoverMenu)]
    assert len(menus) == 1, "a right click on a passage: its menu"
    r.box.activate_action("chat-text.item1", None)
    menus[0].popdown()
    pump(0.6)  # (long enough that the next press isn't a double click)
    assert len(copied) == 1 and record.CITE_SCHEME in copied[0], copied

    hand.down(*at(near, 6))
    pump(0.05)
    hand.drag(*at(after, 10))
    pump(0.05)
    hand.up(*at(after, 10))
    pump(0.1)
    copied.clear()
    assert r._copy_picks(None, Gdk.KEY_C, 0, Gdk.ModifierType.META_MASK | Gdk.ModifierType.SHIFT_MASK) is True
    link = copied[0]
    del r.get_clipboard
    assert link.startswith("[“number 44 says something. Its second”](baude-chat:cited-chat?message=m44&length=36#:~:text="), link
    win.chat.set_quote("")

    other = win.open_note("Cited")
    other.buf.set_text(f"See {link} for why.\n\nAnd this.")
    other.buf.place_cursor(other.buf.get_end_iter())
    pump(0.2)
    shot(win, "record-citation-in-the-notes")
    hit = record.MD_LINK_RE.search(other.text())
    rect = other.view.get_iter_location(other.buf.get_iter_at_offset(hit.start() + 3))
    x, y = other.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x + 1, rect.y + rect.height // 2)
    assert other._at(x, y) == ("url", hit.group(2)), other._at(x, y)
    ok, p = other.view.compute_point(win, widgets.Graphene.Point().init(x, y))
    r.scroll.get_vadjustment().set_value(0)
    r.selection.clear()
    for _ in range(6):  # (GDK puts in motions of its own, from where the real pointer is: one can stop a click, here)
        hand.down(p.x, p.y)
        hand.up(p.x, p.y)
        pump(0.7)
        if r.cited:
            break
    wait_for(lambda: r.cited, "the passage shown", 1)
    assert win.reader is r and r.get_mapped(), "its chat in front"
    assert [(q["label"], q["start"], q["end"]) for q in r.cited] == [(near, 6, len(near.get_text())), (after, 0, 10)], r.cited
    pump(0.2)
    ok, seen = near.compute_point(r.scroll, widgets.Graphene.Point().init(0, 0))
    assert 0 <= seen.y < r.scroll.get_height(), f"in view: {seen.y} of {r.scroll.get_height()}"
    shot(win, "record-citation-followed")
    pump(0.5)
    plain = near.get_text().index("says")
    hand.down(*at(near, plain))
    pump(0.05)
    hand.up(*at(near, plain))
    pump(0.1)
    assert not r.cited and r.citing is None, "the next click lets it go"

    # a chat a few pages long, just opened: at the passage, not gone on to its end as it opens
    short = transcript("cited-short", [f"Short reply {n} of the chat.\n\nWith a second part {n}." for n in range(24)])
    win.open_citation(record.MD_LINK_RE.fullmatch(record.citation_link("cited-short", "m2", "", "Short reply 2 of the chat.")).group(2))
    s = win.open_chat(short)
    wait_for(lambda: s.cited, "the passage shown", 5)
    pump(0.8)
    assert s.scroll.get_vadjustment().get_upper() > s.scroll.get_vadjustment().get_page_size() * 1.3, "long enough"
    ok, seen = s.cited[0]["label"].compute_point(s.scroll, widgets.Graphene.Point().init(0, 0))
    assert 0 <= seen.y < s.scroll.get_height(), f"in view: {seen.y} of {s.scroll.get_height()}"
    wait_for(lambda: not getattr(s.scroll, "held", False), "the hold over", 3)
    stays = s.scroll.get_vadjustment().get_value()
    with open(short, "a") as f:
        f.write(json.dumps({"type": "assistant", "cwd": REPO, "uuid": "more", "message": {
            "id": "more", "role": "assistant", "content": [{"type": "text", "text": "One more reply, come in as it's read."}]}}) + "\n")
    wait_for(lambda: any("One more reply" in w.get_text() for w in s._labels()), "the new reply", 3)
    pump(0.6)
    assert s.scroll.get_vadjustment().get_value() == stays, "a chat being written leaves the view at the passage"

    # in a long code block: the block's own view goes to it too
    code = "\n".join(f"line_{n} = {n}" for n in range(400))
    coded = transcript("cited-code", ["Some code:\n\n```python\n" + code + "\n```\n\nThat's it."])
    win.open_citation(record.MD_LINK_RE.fullmatch(record.citation_link("cited-code", "m0", "", "line_300 = 300")).group(2))
    c = win.open_chat(coded)
    wait_for(lambda: c.cited, "the passage shown", 5)
    pump(0.3)
    q = c.cited[0]
    inner = widgets.inner_scroller(q["label"], c.box)
    assert inner is not None and inner.get_vadjustment().get_value() > 100, "the block's own view moved"
    rect = widgets.passage_rects(q["label"], q["start"], q["end"], inner)[0]
    assert 0 <= rect.get_y() < inner.get_height(), f"in the block's view: {rect.get_y()} of {inner.get_height()}"
    win.open_chat(long)
    pump(0.1)

    toasts = []
    toast = win.toast
    win.toast = toasts.append
    try:
        win.open_citation("baude-chat:no-such-chat?message=m#:~:text=x")
        assert toasts == ["That chat isn't on this computer"], toasts
        toasts.clear()
        r.show_passage({"session": "cited-chat", "message": "", "prefix": "", "start": "never said here", "end": ""})
        wait_for(lambda: toasts, "what it says", 3)
        assert toasts == ["That passage isn't in the chat any more"], toasts
    finally:
        win.toast = toast
    win.open_chat(CHAT)
    for path in (long, short, coded):
        win.tabs.close(("chat", os.path.realpath(path)))
    win.tabs.close(("note", other.page))


def test_selection_scrolls_at_the_edges(app):
    """A drag across held at the bottom of the view: it scrolls, and the selection goes on with it (#47)."""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    long = transcript("long-chat", [f"Paragraph number {n} of a long reply.\n\nAnd a second part to it, {n}."
                                    for n in range(60)])
    reader = win.open_chat(long)
    try:
        pump(1.1)  # (it's gone to the end of the chat, as it opens)
        adj = reader.scroll.get_vadjustment()
        adj.set_value(0)
        pump(0.1)
        assert adj.get_upper() > adj.get_page_size() + 400, "long enough to scroll"
        labels = list(reader._labels())
        hand, start = mouse.Mouse(win), widgets.passage_rects(labels[0], 0, 1, win)[0]
        hand.down(start.get_x() + 1, start.get_y() + 3)
        pump(0.05)
        ok, bottom = reader.scroll.compute_point(win, widgets.Graphene.Point().init(40, reader.scroll.get_height() + 30))
        reached = 0
        for _ in range(200):  # (over and over: GDK puts in motions of its own, from where the real pointer is)
            hand.drag(bottom.x, bottom.y)
            pump(0.02)
            reached = max(reached, len(reader.selection.parts()))
            if adj.get_value() > 200:
                break
        assert adj.get_value() > 200, f"scrolled to {adj.get_value()}"
        hand.up(bottom.x, bottom.y)
        pump(0.05)
        assert reached > 10, f"{reached} labels selected"
        assert reader.selection.text().startswith("How do the jobs") or "Paragraph number 0" in reader.selection.text()

        # begun beside the text, the focus elsewhere: a little movement's a click, a drag doesn't take the chat anywhere
        win.chat.composer.grab_focus()
        adj.set_value(adj.get_upper() / 2)
        pump(0.1)
        where = adj.get_value()
        ok, beside = reader.scroll.compute_point(win, widgets.Graphene.Point().init(8, reader.scroll.get_height() / 2))
        for _ in range(6):  # (GDK's own motions, from where the real pointer is, can land in a press: here only)
            reader.selection.clear()
            win.chat.composer.grab_focus()
            pump(0.5)  # (not a double click)
            hand.down(beside.x, beside.y)
            hand.drag(beside.x + 2, beside.y + 3)
            hand.up(beside.x + 2, beside.y + 3)
            pump(0.05)
            if not reader.selection.shown and win.get_focus() is win.chat.composer:
                break
        assert not reader.selection.shown and win.get_focus() is win.chat.composer, "a click beside the text is nothing"
        adj.set_value(where)
        pump(0.1)
        pump(0.5)
        hand.down(beside.x, beside.y)
        hand.drag(beside.x + 4, beside.y + 90)
        hand.up(beside.x + 4, beside.y + 90)
        pump(0.05)
        assert len(reader.selection.parts()) >= 2, reader.selection.parts()
        assert abs(adj.get_value() - where) < 1, f"from {where} to {adj.get_value()}"
        focus = win.get_focus()
        assert focus is reader.box or focus.is_ancestor(reader.box), "Copy reaches it"
    finally:
        win.open_chat(CHAT)
        win.tabs.close(("chat", os.path.realpath(long)))


def test_markdown_notes(app):
    """The notes in Markdown, drawn as Self's editor draws it: a heading's # and a bold's ** shown only on the
    caret's line; a bullet and a box drawn; Enter goes on with a list and ends it on an empty item. Zim's markup from
    before is made Markdown (zim_to_markdown)."""
    assert record.zim_to_markdown("====== Jobs ======\n* one\n[*] done\n//slant//") == "# Jobs\n- one\n- [x] done\n*slant*"
    win = app.win
    select(win, "b")
    d = win.open_note(record.HOME)
    saved = d.text()
    try:
        d.buf.set_text("# Title\n- one\n- [ ] two\nsome **bold**")
        d.buf.place_cursor(d.buf.get_start_iter())
        d._highlight()
        tags = d.buf.get_tag_table()
        at = lambda name, off: d.buf.get_iter_at_offset(off).has_tag(tags.lookup(name))  # noqa: E731
        text = d.text()
        assert at("mark", 0) and at("hidden", text.index("**bold**")), "markup on the caret's line only"
        assert [kind for _o, kind in d.view.marks] == ["bullet", "bullet", "box"]
        d.buf.set_text("- top\n  - in\n    - deeper\n1. first\nnot a list")  # (as Baude's chat lays a list out)
        d.buf.place_cursor(d.buf.get_end_iter())
        d._highlight()
        assert [kind for _o, kind in d.view.marks] == ["bullet", "hollow", "bullet"], "solid, hollow a level in"
        x = lambda word: d.view.char_rect(d.buf.get_iter_at_offset(d.text().index(word))).x  # noqa: E731
        col = d.view.marker_col() + d.MARKER_GAP
        assert x("not a list") == d.MARGIN and abs(x("top") - (d.MARGIN + d.LIST_PAD + col)) <= 1
        assert abs(x("in") - x("top") - d.INDENT) <= 1 and abs(x("deeper") - x("in") - d.INDENT) <= 1
        assert abs(x("first") - x("top")) <= 1, "a number's text where a bullet's is"
        assert tags.lookup("bullet").get_property("foreground-rgba").alpha == record.NotesView.MARKER_ALPHA
        d.buf.set_text("- " + "wrapping words " * 40 + "\n- \nnot a list")  # (a long item, and a new one)
        d.buf.place_cursor(d.buf.get_iter_at_offset(d.text().index("\n- \n") + 3))
        d._highlight()
        pump(0.1)
        second = d.buf.get_iter_at_offset(2)
        assert d.view.forward_display_line(second) and abs(d.view.char_rect(second).x - x("wrapping")) <= 1, \
            "a long item wraps under its text, as a Shift+Enter line, not back at the marker"
        caret, _weak = d.view.get_cursor_locations(d.buf.get_iter_at_offset(d.text().index("\n- \n") + 3))
        assert abs(caret.x - x("wrapping")) <= 1, "a new item's caret where its text will go: the space not lost"
        d.buf.set_text("say hi")  # (a selection, and * ` or /: bold, code, italic)
        d.buf.select_range(d.buf.get_iter_at_offset(4), d.buf.get_iter_at_offset(6))
        assert d._key(None, record.Gdk.KEY_asterisk, 0, record.Gdk.ModifierType.SHIFT_MASK) and d.text() == "say **hi**"
        assert d._key(None, record.Gdk.KEY_slash, 0, 0) and d.text() == "say ***hi***"
        assert d._key(None, record.Gdk.KEY_asterisk, 0, record.Gdk.ModifierType.SHIFT_MASK) and d.text() == "say *hi*"
        assert d._key(None, record.Gdk.KEY_grave, 0, 0) and d.text() == "say *`hi`*"
        d.buf.set_text("- one")
        d.buf.place_cursor(d.buf.get_end_iter())
        assert d._key(None, record.Gdk.KEY_Return, 0, 0) and d.text() == "- one\n- "
        assert d._key(None, record.Gdk.KEY_Return, 0, 0) and d.text() == "- one\n"
        d.buf.set_text("- one")  # (Tab and Shift+Tab, Backspace after the bullet: as an editor's)
        d.buf.place_cursor(d.buf.get_end_iter())
        assert d._key(None, record.Gdk.KEY_Tab, 0, 0) and d.text() == "  - one"
        assert d._key(None, record.Gdk.KEY_ISO_Left_Tab, 0, record.Gdk.ModifierType.SHIFT_MASK) and d.text() == "- one"
        d.buf.place_cursor(d.buf.get_iter_at_offset(2))
        assert d._key(None, record.Gdk.KEY_BackSpace, 0, 0) and d.text() == "one"
        shift = record.Gdk.ModifierType.SHIFT_MASK  # (Shift+Enter: a line of the item's text, under it, as Markdown)
        for item, spaces in (("- one", "  "), ("1. one", "   "), ("  - one", "    ")):
            d.buf.set_text(item)
            d.buf.place_cursor(d.buf.get_end_iter())
            assert d._key(None, record.Gdk.KEY_Return, 0, shift) and d.text() == f"{item}\n{spaces}", d.text()
        d.buf.set_text("- one\n  more")
        d.buf.place_cursor(d.buf.get_end_iter())
        assert d._key(None, record.Gdk.KEY_Return, 0, 0) and d.text() == "- one\n  more\n- ", "Enter: the next item"
        d.buf.set_text("- one\n  more")
        d.buf.place_cursor(d.buf.get_iter_at_offset(8))
        assert d._key(None, record.Gdk.KEY_BackSpace, 0, 0) and d.text() == "- onemore", "back onto the item"
        assert tags.lookup("drawn").get_property("foreground-rgba").alpha > 0, \
            "all but clear: GTK draws a clear colour as none set (the - black)"
        d.buf.set_text("say **hello** there\nnext [[Page]]")  # (a whole span selected, typed over: formatted still)
        d.buf.place_cursor(d.buf.get_end_iter())
        d._highlight()
        s = d.text().index("**")
        d.buf.select_range(d.buf.get_iter_at_offset(s), d.buf.get_iter_at_offset(s + len("**hello**")))
        d._key(None, record.Gdk.KEY_x, 0, 0)
        d.buf.delete_selection(True, True)
        d.buf.insert_at_cursor("x")
        assert d.text().startswith("say **x** there"), d.text()
        d._highlight()
        link = d.text().index("[[")
        try:  # (Settings → Markup visibility: the caret's line's markup shown; none's; all's, but a link's)
            win.set_markup("none")
            assert at("hidden", link) and at("hidden", s), "none: hidden, the caret's line's too"
            win.set_markup("all")
            assert at("mark", s) and at("hidden", link), "all: shown, a link's [[ ]] not"
        finally:
            win.set_markup("line")
    finally:
        d.buf.set_text(saved)


def test_click_after_find_stays_where_it_is(app):
    """Found something, then a real click in the text, away from the match: the view stays where it is, the bar and the
    shading go, and the caret's where it was clicked, nothing selected. (#79)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    d = win.open_note("Finding and clicking")
    d.buf.set_text("\n".join(f"Line {n} of a long page" + (" with needle" if n == 150 else "") for n in range(300)))
    hand, adj = mouse.Mouse(win), d.scroll.get_vadjustment()
    try:
        pump(0.3)
        d.view.grab_focus()
        d.open_search()
        d.search_entry.set_text("needle")
        wait_for(lambda: d.matches and adj.get_value() > 500, lambda: f"the match shown: at {adj.get_value()}", 3)
        pump(0.5)
        def on_screen():
            return d.view.compute_point(win, widgets.Graphene.Point().init(0, 0))[1].y
        was = on_screen()
        ok, p = d.scroll.compute_point(win, widgets.Graphene.Point().init(200, d.scroll.get_height() - 80))
        ok, at = win.compute_point(d.view, widgets.Graphene.Point().init(p.x, p.y))
        line = d.view.get_line_at_y(d.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, 0, int(at.y))[1])[0].get_line()
        hand.down(p.x, p.y)
        hand.up(p.x, p.y)
        pump(0.5)
        assert abs(on_screen() - was) < 2, f"the text moved from {was} to {on_screen()}"
        assert not d.search_bar.get_search_mode() and not d.matches, "the bar and its shading gone"
        caret = d.buf.get_iter_at_mark(d.buf.get_insert())
        assert caret.get_line() == line and not d.buf.get_has_selection(), (caret.get_line(), line)
        assert win.get_focus() is d.view, win.get_focus()
    finally:
        d.search_bar.set_search_mode(False)
        d.buf.set_text("")
        win.tabs.close(("note", "Finding and clicking"))


def test_name_from_the_chat_goes_to_the_end_of_a_long_page(app):
    """A name from the chat, written at the end of a long page of notes: the page goes there, the caret in sight to write
    on. (Focus coming to the text doesn't scroll it, since #79.)"""
    win = app.win
    select(win, "a")
    d = win.open_note("Names at the end")
    d.buf.set_text("\n".join(f"Line {n}" for n in range(300)))
    try:
        pump(0.3)
        d.scroll.get_vadjustment().set_value(0)
        win.diary_ref("the-surface", "A name")
        at = d.buf.get_iter_at_mark(d.buf.get_insert())

        def in_sight():
            y = d.view.get_iter_location(at).y
            ok, p = d.view.compute_point(d.scroll, widgets.Graphene.Point().init(0, y))
            return 0 <= p.y <= d.scroll.get_height() - 10
        wait_for(in_sight, lambda: f"the caret out of sight: at {d.scroll.get_vadjustment().get_value()}", 3)
    finally:
        d.buf.set_text("")
        win.tabs.close(("note", "Names at the end"))


def test_find_takes_in_the_title(app):
    """The find bar's over the whole page, and the title's in what it finds, before the text: shaded, counted, gone to and
    from with Enter and Shift+Enter; only in the title, it still says so. Esc on it is back in the text, nothing selected:
    what's typed next isn't to rename the page. A selection in the title is what it looks for. (#80)"""
    win = app.win
    select(win, "a")
    d = win.open_note("Needle page")
    try:
        d.buf.set_text("A needle here.\n\nAnother needle there.\n\nNothing else.")
        d.view.grab_focus()
        pump(0.2)
        d.open_search()
        pump(0.2)
        assert d.get_first_child() is d.search_bar, "over the title"
        ok, bar = d.search_bar.compute_point(d, widgets.Graphene.Point().init(0, 0))
        ok, title = d.title.compute_point(d, widgets.Graphene.Point().init(0, 0))
        assert bar.y < title.y, (bar.y, title.y)
        d.search_entry.set_text("needle")
        wait_for(lambda: d.search_count.get_label() == "1 of 3", lambda: d.search_count.get_label(), 3)
        assert d.title_hits == [(0, 6)] and d.on_title == 0 and d.match_index == -1, (d.title_hits, d.on_title, d.match_index)
        assert d.title.get_attributes().get_attributes(), "shaded"
        shot(win, "record-find-in-the-title")
        d.find_next(1)
        assert d.on_title is None and d.match_index == 0 and d.search_count.get_label() == "2 of 3"
        d.find_next(1)
        assert d.match_index == 1 and d.search_count.get_label() == "3 of 3"
        d.find_next(1)
        assert d.on_title == 0 and d.match_index == -1 and d.search_count.get_label() == "1 of 3", "round to the title"
        assert d.title.get_position() == 6, "shown, should the title be too long for its box"
        d.find_next(-1)
        assert d.match_index == 1 and d.on_title is None, "and back"
        d.find_next(1)
        d._find_again()
        assert d.on_title == 0, "drawn again, still on it"
        d.close_search()
        pump(0.1)
        assert not d.buf.get_has_selection() and win.get_focus() is d.view, "in the text, the title left alone"
        d.title.grab_focus()
        d.title.select_region(0, 6)
        d.open_search()
        assert d.search_entry.get_text() == "Needle", "the title's selection is what it looks for"
        d.MATCH_LIMIT = 2
        d.search_entry.set_text("needle")
        wait_for(lambda: d.search_count.get_label() == "1 of 3+", lambda: d.search_count.get_label(), 3)
        del d.MATCH_LIMIT
        d.search_bar.set_search_mode(False)
        d.open_search()
        d.search_entry.set_text("page")
        wait_for(lambda: d.search_count.get_label() == "1 of 1", lambda: d.search_count.get_label(), 3)
        assert d.on_title == 0 and not d.matches, "only in the title"
        d.find_next(1)
        assert d.on_title == 0
        d.search_entry.set_text("nowhere at all")
        wait_for(lambda: d.search_count.get_label() == "No matches", lambda: d.search_count.get_label(), 3)
        assert not d.title_hits and not d.title.get_attributes().get_attributes()
        d.search_entry.set_text("")
        pump(0.1)
    finally:
        d.search_bar.set_search_mode(False)
        d.buf.set_text("")
        win.tabs.close(("note", "Needle page"))


def test_notes_find(app):
    """⌘F on a page of notes: a code tab's find bar (every match shaded, the current one more, "2 of 8", Enter and
    Shift+Enter, Esc). It finds what's seen, across the markup hidden in a line, never in it or in what's drawn over,
    the caret's line's markup as the caret moves. The matches outlast the page being drawn again as it's written in, and
    none of it is the note's: nothing kept, nothing to undo. Esc leaves the match selected; Esc in the text just closes
    the bar."""
    win = app.win
    select(win, "b")
    d = win.open_note("Finding")
    pages = by_id(win, "b")["pages"]
    keys = {sc.get_trigger().to_string(): sc for c in win.observe_controllers()
            if isinstance(c, Gtk.ShortcutController) for sc in c}
    mod = "<Meta>" if sys.platform == "darwin" else "<Control>"

    def find_key():
        keys[mod + "f"].get_action().activate(Gtk.ShortcutActionFlags(0), win, None)

    def seen(a, b):
        return d.buf.get_text(d.buf.get_iter_at_offset(a), d.buf.get_iter_at_offset(b), False)

    searched = []
    d.search_entry.connect("search-changed", lambda e: searched.append(e.get_text()))

    def found(text):
        """What's typed in the bar, once it's searched for it (a moment later): the matches, as they're seen."""
        searched.clear()
        d.search_entry.set_text(text)
        wait_for(lambda: text in searched, f"{text} searched for")
        return [seen(a, b) for a, b in d.matches]

    def shaded(tag):
        return [i for i in range(d.buf.get_char_count()) if d.buf.get_iter_at_offset(i).has_tag(tag)]

    def caret_to(line):
        d.buf.place_cursor(d.buf.get_iter_at_line(line)[1])

    text = ("# Finding words\n\nThe caret starts here.\n- some **bold** words in a hyphen-ated list\n"
            "- a [[Words page]] link\n`words` in code, ==words== marked, and a [link](https://example.com/words)\n\n"
            "```\nwords in a block\n```\n- [ ] a box of words\n---\nplain words at the end")
    n = 8  # ("words" as it's seen: the heading, bold's, the link's, code, marked, the block, the box, the end)
    try:
        d.buf.set_text(text)
        caret_to(2)
        d._highlight()
        kept = pages["Finding"]
        d.buf.set_modified(False)
        assert not d.buf.get_can_undo() and not d.search_bar.get_search_mode(), "closed till asked for"
        # ⌘F, what's typed, Enter, Shift+Enter, Ctrl+G
        find_key()
        assert d.search_bar.get_search_mode() and win.get_focus().is_ancestor(d.search_entry), "the bar, to type in"
        assert [word.lower() for word in found("WORDS")] == ["words"] * n, d.matches
        assert d.search_count.get_label() == f"{d.match_index + 1} of {n}", d.search_count.get_label()
        first = d.match_index
        start = d.buf.get_iter_at_offset(d.matches[first][0])
        assert start.has_tag(d.current_tag) and start.has_tag(d.match_tag)
        d.search_entry.emit("activate")
        assert d.match_index == (first + 1) % n and d.search_count.get_label() == f"{(first + 1) % n + 1} of {n}"
        assert not start.has_tag(d.current_tag) and start.has_tag(d.match_tag), "the old current: shaded only"
        shift_enter = next(c for c in d.search_entry.observe_controllers() if isinstance(c, Gtk.EventControllerKey) and
                           c.get_propagation_phase() == Gtk.PropagationPhase.BUBBLE)
        shift_enter.emit("key-pressed", Gdk.KEY_Return, 0, Gdk.ModifierType.SHIFT_MASK)
        assert d.match_index == first, "Shift+Enter: the one before"
        d.search_entry.emit("next-match")
        d.search_entry.emit("previous-match")
        assert d.match_index == first
        shot(win, "record-notes-find")
        # none of that is the note's
        assert pages["Finding"] == d.text() == kept and not d.buf.get_can_undo() and not d.buf.get_modified(), \
            "finding changes nothing"
        # drawn again: still shaded. Written in above: the matches move with it, the current the one it was on
        at = d.matches[first][0]
        d._highlight()
        assert len(d.matches) == n and d.match_index == first and d.buf.get_iter_at_offset(at).has_tag(d.current_tag)
        d.buf.insert(d.buf.get_start_iter(), "words first\n")
        moved = at + len("words first\n")
        assert len(d.matches) == n + 1 and d.matches[d.match_index][0] == moved and \
            d.buf.get_iter_at_offset(moved).has_tag(d.current_tag), (d.match_index, d.matches)
        assert d.search_count.get_label() == f"{d.match_index + 1} of {n + 1}"
        assert pages["Finding"] == d.text() == "words first\n" + kept, "kept as written, no more"
        d.buf.delete(d.buf.get_iter_at_offset(moved), d.buf.get_iter_at_offset(moved + 1))  # (the match written away)
        assert len(d.matches) == n and d.matches[d.match_index][0] > moved, "the next one's the current one"
        d.buf.insert(d.buf.get_iter_at_offset(moved), "w")
        d.buf.delete(d.buf.get_start_iter(), d.buf.get_iter_at_offset(len("words first\n")))
        assert d.text() == kept and len(d.matches) == n
        # undone: what was written, and nothing of the finding's
        d.buf.begin_user_action()
        d.buf.insert(d.buf.get_end_iter(), " more words")
        d.buf.end_user_action()
        assert len(d.matches) == n + 1
        d.buf.undo()
        assert d.text() == kept and pages["Finding"] == kept and len(d.matches) == n, "one undo, of what was written"
        # what's hidden or drawn over isn't found; what reads across it is
        caret_to(2)
        assert d.buf.get_iter_at_offset(text.index("**bold**")).has_tag(d.buf.get_tag_table().lookup("hidden"))
        for missing in ("**", "[[", "example.com", "[ ]", "---", "bold** words"):
            assert found(missing) == [] and not shaded(d.match_tag) and d.search_count.get_label() == "No matches", \
                f"{missing}: nothing to show for it"
        assert found("-") == ["-"] and d.matches[0][0] == text.index("-ated"), "not the bullets' or the rule's"
        assert found("bold words") == ["bold words"] and d.search_count.get_label() == "1 of 1", "as it reads"
        a, b = d.matches[0]
        assert d.buf.get_text(d.buf.get_iter_at_offset(a), d.buf.get_iter_at_offset(b), True) == "bold** words", \
            "from what's seen of it, not the ** before it"
        shot(win, "record-notes-find-across-markup")
        # the caret's line's markup is seen, so found there, shaded; gone again with the caret
        assert found("**") == []
        caret_to(3)
        assert [seen(a, b) for a, b in d.matches] == ["**", "**"] and d.search_count.get_label().endswith("of 2")
        assert len(shaded(d.match_tag)) == 4
        caret_to(2)
        assert d.matches == [] and not shaded(d.match_tag) and d.search_count.get_label() == "No matches"
        # Esc: closed, nothing shaded, back in the text with the match selected; the page's text as it was
        found("bold")
        d.search_entry.emit("stop-search")
        assert not d.search_bar.get_search_mode() and not d.matches and d.search_entry.get_text() == ""
        assert not shaded(d.match_tag) and not shaded(d.current_tag), "nothing left shaded"
        a, b = d.buf.get_selection_bounds()
        assert d.buf.get_text(a, b, True) == "bold" and win.get_focus() is d.view, "the match selected"
        assert d.text() == kept and pages["Finding"] == kept
        # selected text is what's found; written with the bar open, Esc in the text closes it, the caret left there
        find_key()
        assert d.search_entry.get_text() == "bold" and len(d.matches) == 1, "the selection is what's found"
        caret_to(2)
        d.view.grab_focus()
        d.buf.insert_at_cursor("bold ")
        assert d.search_bar.get_search_mode() and len(d.matches) == 2 and win.get_focus() is d.view
        assert d.text().count("bold") == 2
        at = d.buf.get_property("cursor-position")
        d.pop.popup()
        assert d._key(None, Gdk.KEY_Escape, 0, 0) and d.search_bar.get_search_mode() and not d.pop.get_visible(), \
            "@'s list goes first"
        assert d._key(None, Gdk.KEY_Escape, 0, 0) and not d.search_bar.get_search_mode() and not d.matches
        assert d.buf.get_property("cursor-position") == at and not d.buf.get_has_selection()
        assert win.get_focus() is d.view
        # its close button: back to writing too
        find_key()
        d.search_bar.set_search_mode(False)
        assert win.get_focus() is d.view and not d.matches
        # far down a long page: scrolled to, by the page's scroller, and found from what's shown
        d.buf.set_text("\n".join(f"line {i}" for i in range(200)) + "\nthe **far** end\n" +
                       "\n".join(f"line {i}" for i in range(200, 300)))
        caret_to(0)
        pump(0.2)
        adj = d.scroll.get_vadjustment()
        adj.set_value(0)
        find_key()
        assert found("far end") == ["far end"]
        wait_for(lambda: adj.get_value() > 0 and d.follow_anim.get_state() != record.Adw.AnimationState.PLAYING,
                 "scrolled to it")
        pump(0.1)
        rect = d.view.char_rect(d.buf.get_iter_at_offset(d.matches[0][0]))
        y = d.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x, rect.y)[1]
        ok, there = d.view.compute_point(d.scroll, record.Graphene.Point().init(0, y))
        assert ok and 0 <= there.y <= d.scroll.get_height() - rect.height, (there.y, d.scroll.get_height())
        found("line 28")  # (line 28, and line 280 to 289: from what's shown, the far end's far past the first)
        assert seen(*d.matches[d.match_index]) == "line 28" and d.matches[d.match_index][0] > d.matches[0][0], \
            (d.match_index, d.matches[:2])
        d.close_search()
        # ⌘F with the Kubernetes view over the window: its filter's, not a bar under it
        win.kube.set_visible(True)
        try:
            find_key()
            assert not d.search_bar.get_search_mode()
        finally:
            win.kube.set_visible(False)
    finally:
        win.tabs.close(("note", "Finding"))
        pages.pop("Finding", None)


def test_full_width(app):
    """☰ → Full-width text, as Baude's: the chat read and the diary across the whole pane, kept for next time."""
    win = app.win
    win.open_note(record.HOME)
    assert win.reader.clamp.get_maximum_size() == record.Reader.WIDTH
    win.activate_action("win.fullwidth", None)
    try:
        assert win.lookup_action("fullwidth").get_state().get_boolean()
        assert win.reader.clamp.get_maximum_size() == win.diary.clamp.get_maximum_size() == 100_000
        assert record.Store().full_width, "kept in records.json"
    finally:
        win.activate_action("win.fullwidth", None)
    assert win.diary.clamp.get_maximum_size() == record.DiaryEditor.WIDTH and not record.Store().full_width


def test_one_record_running(app):
    """A second start hands its chat to the Record that's running (two would save over each other's)."""
    win = app.win
    select(win, "b")
    assert app.app.server is not None and os.path.exists(record.socket_path())
    assert record.hand_over({"chat": CHAT}), "the running one answered"
    wait_for(lambda: win.current["id"] == "a", "opened there")


def test_new_ones_named(app):
    """+ in Record: Untitled (renamed at once), Untitled 2 for the next."""
    win = app.win
    count = len(win.store.notebooks)
    for want in ("Untitled", "Untitled 2"):
        win.new_notebook()
        pump(0.2)
        win.get_visible_dialog().force_close()
        assert record.full_name(win.current) == want, record.full_name(win.current)
    assert len(win.store.notebooks) == count + 2
    for t in [t for t in win.store.notebooks if t["name"].startswith("Untitled")]:
        win.store.delete(t)
    win.fill_sidebar()


def test_renamed_then_another_page_clicked(app):
    """A new page, nothing written in it, its title typed over and another page clicked in the list with real clicks: the
    page takes its new name, the page clicked is shown, and Record is still there. (Going from the title renames it, and
    that lists the pages again, under the click that's still being handled: it took the row away from under GTK.) (#83)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    home = win.open_note(record.HOME)
    d = win.open_note("Brand new")
    try:
        pump(0.3)
        hand = mouse.Mouse(win)
        row = next(r for r in listed_rows(win.pages.index) if r.page == "Brand new")
        ok, p = row.compute_point(win, widgets.Graphene.Point().init(30, 10))
        hand.down(p.x, p.y)
        hand.up(p.x, p.y)
        pump(0.2)
        ok, p = d.title.compute_point(win, widgets.Graphene.Point().init(40, 12))
        hand.down(p.x, p.y)
        hand.up(p.x, p.y)
        pump(0.1)
        d.title.grab_focus()
        d.title.set_text("Renamed page")
        pump(0.1)
        row = next(r for r in listed_rows(win.pages.index) if r.page == record.HOME)
        ok, p = row.compute_point(win, widgets.Graphene.Point().init(30, 10))
        hand.down(p.x, p.y)
        hand.up(p.x, p.y)
        pump(0.5)
        assert win.tabs.current() is home, win.tabs.current()
        assert win.tabs.get(("note", "Renamed page")) is d and win.tabs.get(("note", "Brand new")) is None
        assert d.page == "Renamed page" and "Renamed page" in win.current["pages"] and "Brand new" not in win.current["pages"]
        assert [r.page for r in listed_rows(win.pages.index)] == list(win.pages.names()[0])
        assert win.pages.index.get_selected_row().page == record.HOME, "the one clicked, selected"
    finally:
        win.tabs.close(("note", d.page))


def listed_rows(box):
    rows, row = [], box.get_row_at_index(0)
    while row is not None:
        rows.append(row)
        row = box.get_row_at_index(len(rows))
    return rows


def test_menu_has_no_rename_or_delete(app):
    """A notebook's Rename and Delete are on its right-click only, not in ☰ as well. (#115)"""
    win = app.win
    select(win, "a")

    def labels(model):
        out = []
        for i in range(model.get_n_items()):
            if (said := model.get_item_attribute_value(i, "label", None)) is not None:
                out.append(said.get_string())
            for kind in ("section", "submenu"):
                if (inner := model.get_item_link(i, kind)) is not None:
                    out += labels(inner)
        return out

    def under(widget):
        yield widget
        kid = widget.get_first_child()
        while kid is not None:
            yield from under(kid)
            kid = kid.get_next_sibling()
    button = next(w for w in under(win) if isinstance(w, Gtk.MenuButton) and w.get_icon_name() == "open-menu-symbolic")
    said = labels(button.get_menu_model())
    assert said and not [s for s in said if s.startswith(("Rename", "Delete"))], said
    assert win.lookup_action("rename") is not None and win.lookup_action("delete") is not None, "the right-click's still work"


def test_rename(app):
    """A Record's name is what's typed; no suffix beside it."""
    win = app.win
    select(win, "b")
    win.rename()
    pump(0.2)
    d = win.get_visible_dialog()
    entry = d.get_extra_child().get_first_child()
    assert entry.get_text() == "Biology reading" and entry.get_next_sibling() is None, "nothing after it"
    entry.set_text("  Monorepo   notes ")
    d.emit("response", "ok")
    assert win.current["name"] == "Monorepo notes" and record.full_name(win.current) == "Monorepo notes"


def test_copy_link_is_a_file_an_agent_can_read(app):
    """☰ → Copy Link to This Page, or a tab's right-click: a path that's there to read, whichever the tab. A page of notes'
    is its own read-only file, kept as the page changes, under its new name once renamed, gone once it's deleted; no name
    takes it out of its folder. A chat's is its transcript, a file's code's the file at its line. (#84)"""
    win = app.win
    select(win, "a")
    copied = []
    win.tabs.get_clipboard = lambda: type("Clip", (), {"set": lambda self, v: copied.append(v)})()
    d = win.open_note("Plans:Next week")
    d.buf.set_text("Do the [[Other]] thing")
    pages = os.path.join(os.environ["RECORD_DATA"], "pages")
    try:
        key = win.get_application().get_accels_for_action("win.copylink")
        assert key == ["<Shift><Meta>l" if sys.platform == "darwin" else "<Shift><Control>l"], key
        win.activate_action("win.copylink", None)
        link = copied[-1]
        assert os.path.isabs(link) and link == os.path.join(pages, "Systems for me", "Plans", "Next week.md"), link
        with open(link) as f:
            assert f.read() == "Do the [[Other]] thing"
        assert not os.stat(link).st_mode & 0o222, "read-only: what's written there wouldn't reach the notes"
        d.buf.set_text("Do it now")
        win.store.save_now()
        with open(link) as f:
            assert f.read() == "Do it now", "kept up with the page"
        d.title.set_text("Later")
        d.rename_page()
        renamed = os.path.join(pages, "Systems for me", "Plans", "Later.md")
        assert win.tabs.copy_link() == renamed and os.path.exists(renamed) and not os.path.exists(link)
        # a name that's no file's
        book = os.path.join(pages, "Systems for me")
        for name, where in (("../../escape", "-..-escape.md"), ("..:out", os.path.join("Untitled", "out.md")),
                            ("Same", "Same.md"), ("same", "same (2).md")):
            win.current["pages"][name] = "words"
            win.store.save_now()
            path = win.store.page_files[win.current["id"], name]
            assert path == os.path.join(book, where) and os.path.exists(path), path
        for name in ("../../escape", "..:out", "Same", "same"):
            del win.current["pages"][name]
        win.store.save_now()
        assert not os.path.exists(os.path.join(book, "Same.md")), "gone with its page"
        assert not os.path.exists(os.path.join(book, "Untitled")), "and its folder with it"
        # two notebooks of one name, whichever comes first: neither's path comes to hold the other's page
        one, two = {"id": "a1", "name": "Notes", "pages": {"Home": "x"}}, {"id": "b2", "name": "notes", "pages": {"Home": "y"}}
        for notebooks in ([one, two], [two, one]):
            paths = record.page_paths(notebooks)
            assert paths["a1", "Home"] == os.path.join("Notes (a1)", "Home.md"), paths
            assert paths["b2", "Home"] == os.path.join("notes (b2)", "Home.md"), paths
        # what isn't Record's stays; one file that can't be written stops neither the rest nor the notes being saved
        stray = os.path.join(book, "Left behind.md")
        with open(stray, "w") as f:
            f.write("not Record's")
        win.store.mirrored = {}
        win.store.save_now()
        assert os.path.exists(stray) and os.path.exists(renamed)
        win.current["pages"].update({"A": "first", "A.md:B": "no room: A.md is a file", "C": "after it"})
        win.store.save_now()
        assert win.store.page_file(win.current, "A.md:B") is None, "no file: no link"
        for name, text in (("A", "first"), ("C", "after it")):
            with open(win.store.page_file(win.current, name)) as f:
                assert f.read() == text
        for name in ("A", "A.md:B", "C"):
            del win.current["pages"][name]
        win.store.save_now()
        os.remove(stray)
        aside = pages + ".aside"
        os.rename(pages, aside)
        with open(pages, "w") as f:
            f.write("not a folder")
        try:
            win.store.mirrored = {}
            d.buf.set_text("Still saved")
            win.store.save_now()
            with open(win.store.path) as f:
                assert "Still saved" in f.read()
            assert win.tabs.copy_link() is None, "no file: no link"
        finally:
            os.remove(pages)
            os.rename(aside, pages)
            win.store.mirrored = {}
            win.store.save_now()
        # a tab's own, right-clicked, and another shown
        assert win.tabs.view.get_menu_model() is not None, "Record's tabs have the menu (Baude's don't)"
        win.open_note(record.HOME)
        page = win.tabs.pages[("note", "Plans:Later")]
        win.tabs.view.emit("setup-menu", page)
        win.tabs.view.emit("setup-menu", None)
        win.tabs.bar.activate_action("tab.copy-link", None)
        assert copied[-1] == renamed, copied[-1]
        # a chat's, a file's code's; one with none says so
        reader = win.open_chat(CHAT)
        assert win.tabs.current() is reader and win.tabs.copy_link() == os.path.realpath(CHAT)
        source = os.path.join(TMP, "linked.py")
        with open(source, "w") as f:
            f.write("a = 1\nb = 2\nc = 3\n")
        win.show_code(source, 2)
        assert win.tabs.copy_link() == f"{source}:2"
        before = len(copied)
        win.open_fixes()
        assert win.tabs.copy_link() is None and len(copied) == before, "the Feedback tab has none: nothing's copied"
        win.tabs.close(("fixes",))
    finally:
        del win.tabs.get_clipboard
        win.tabs.close(("note", d.page))


def settings_opened(win):
    """Settings, opened as ☰ does: nothing in Python has hold of it till it's asked for here."""
    win.activate_action("win.settings", None)
    wait_for(lambda: isinstance(win.get_visible_dialog(), record.Settings), "Settings")
    return win.get_visible_dialog()


def export_chosen(win, i):
    """Settings opened, if it isn't, and its i-th export clicked: what's over the window then."""
    open_now = win.get_visible_dialog()
    row = (open_now if isinstance(open_now, record.Settings) else settings_opened(win)).export_rows[i]
    pump(0.2)
    row.get_parent().emit("row-activated", row)
    pump(0.2)
    return win.get_visible_dialog()


def export_sizes_known(rows):
    wait_for(lambda: all(r.get_subtitle() != "Working out size…" for r in rows), "the sizes")


def forget_zip_sizes():
    with record._ZIP_SIZES:
        record._ZIPPED.clear()


def folders_back(win, folders):
    """A notebook's attached folders as they were, kept: None, it had none."""
    if folders is None:
        win.current.pop("folders", None)
    else:
        win.current["folders"] = folders
    win.store.save_now()


def test_export_sizes_are_the_zips(app):
    """What an export's said to come to, both ways, is what export_zip makes, to the byte, with nothing written. A chat's read
    once, whichever notebooks have it, and again only when its transcript has changed. (#124)"""
    out = os.path.join(TMP, "sizes")
    os.makedirs(out, exist_ok=True)
    named = transcript("chat-sizes", ["Ünï wörds " * 300])
    with open(named, "a") as f:
        f.write(json.dumps({"type": "custom-title", "customTitle": "Ça va, a/b"}) + "\n")
    book = {"id": "x1", "name": "Shöp notes", "folders": [REPO], "pages": {record.HOME: "Home words " * 500, "Plans:Nëxt": "Next", "Empty": " "},
            "messages": [{"role": "user", "text": "What is it?", "quote": "a passage"}, {"role": "record", "text": "A thing."}]}
    other = {"id": "x2", "name": "Elsewhere", "folders": [REPO], "pages": {record.HOME: "Other words"}, "messages": []}
    nothing = {"id": "x3", "name": "Nothing", "folders": [], "pages": {}, "messages": []}

    def made(notebooks, extras):
        path = os.path.join(out, "made.zip")
        record.export_zip(path, notebooks, extras)
        return os.path.getsize(path)
    forget_zip_sizes()
    reads, read = [], record.chat_text

    def counted(*a):
        reads.append(a[0])
        return read(*a)
    record.chat_text = counted
    try:
        def data():
            return sorted((os.path.join(d, f), os.stat(os.path.join(d, f)).st_mtime_ns)
                          for d, _, files in os.walk(os.environ["RECORD_DATA"]) for f in files)
        before, zipping = data(), record.zipfile.ZipFile
        record.zipfile.ZipFile = None  # (it makes no zip to see how big it is)
        try:
            both = record.zip_sizes([book, other])
        finally:
            record.zipfile.ZipFile = zipping
        assert data() == before and not os.listdir(out), "nothing written"
        stopped = threading.Event()
        stopped.set()
        assert record.zip_sizes([book, other], stopped) is None, "not wanted any more"
        assert named in reads and CHAT in reads and len(reads) == len(set(reads)), reads
        first = list(reads)
        assert record.zip_sizes([book, other]) == both and record.zip_sizes([other]) and reads == first, "none read again"
        with open(named, "a") as f:
            f.write(json.dumps({"type": "assistant", "cwd": REPO, "uuid": "a9", "message": {
                "id": "m9", "role": "assistant", "content": [{"type": "text", "text": "More, and more words."}]}}) + "\n")
        del reads[:]
        changed = record.zip_sizes([book, other])
        assert reads == [named], reads
        assert changed[0] == both[0] and changed[1] > both[1], (both, changed)
        record.chat_text = read
        for notebooks in ([book, other], [book], [other], [nothing], []):
            assert record.zip_sizes(notebooks) == (made(notebooks, False), made(notebooks, True)), notebooks
    finally:
        record.chat_text = read
        os.remove(named)
    assert [record.size_said(n) for n in (412, 3_400, 12_340, 999_600, 3_400_000, 9_960_000, 177_000_000, 1_500_000_000)] == [
        "412 bytes", "3.4 KB", "12 KB", "1.0 MB", "3.4 MB", "10 MB", "177 MB", "1.5 GB"]


def test_export_in_settings(app):
    """Export Notebook… and Export All… have left ☰ for Settings: each says what its zip comes to both ways, once that's
    known. Chosen, Settings goes first, so what's asked, where it goes and the toast are over the window. (#124)"""
    import zipfile
    win = app.win
    select(win, "a")

    def labels(menu):
        for i in range(menu.get_n_items()):
            if (label := menu.get_item_attribute_value(i, "label")) is not None:
                yield label.get_string()
            for link in ("section", "submenu"):
                if (inner := menu.get_item_link(i, link)) is not None:
                    yield from labels(inner)
    button = next(w for w in walk(win) if isinstance(w, Gtk.MenuButton) and w.get_icon_name() == "open-menu-symbolic")
    listed = list(labels(button.get_menu_model()))
    assert "Settings…" in listed and not [s for s in listed if s.startswith("Export")], listed
    out = os.path.join(TMP, "settings-exports")
    os.makedirs(out, exist_ok=True)
    folders = win.current.get("folders")
    win.current["folders"] = [REPO]

    def said(notebooks):
        path = os.path.join(out, "size.zip")
        sizes = []
        for extras in (False, True):
            record.export_zip(path, notebooks, extras)
            sizes.append(record.size_said(os.path.getsize(path)))
        os.remove(path)
        return f"{sizes[0]}, or {sizes[1]} with glossary and chats"
    was = record.Gtk.FileDialog
    saves, path = [], os.path.join(out, "chosen.zip")

    class Saving:
        def __init__(self, **props):
            self.name = props["initial_name"]

        def save(self, parent, _cancel, chosen):
            saves.append((self, parent, chosen, win.get_visible_dialog()))

        def save_finish(self, _result):
            if cancelled:
                raise record.GLib.Error("Dismissed by user")
            return record.Gio.File.new_for_path(path)
    cancelled, toasts, win.toast = [], [], lambda text: toasts.append(text)
    record.Gtk.FileDialog = Saving
    try:
        rows = record.Settings(win).export_rows
        assert all(r.get_activatable() for r in rows)
        settings = settings_opened(win)
        rows = settings.export_rows
        export_sizes_known(rows)
        assert rows[0].get_subtitle() == said([win.current]), rows[0].get_subtitle()
        assert rows[1].get_subtitle() == said(win.store.notebooks), rows[1].get_subtitle()
        both = re.fullmatch(r"([\d.]+ (?:bytes|KB)), or ([\d.]+ (?:bytes|KB)) with glossary and chats", rows[0].get_subtitle())
        assert both and both[1] != both[2], rows[0].get_subtitle()
        settings.close()
        wait_for(lambda: win.get_visible_dialog() is None, "Settings closed")
        # one notebook: what's asked, over Settings, which is still there if that's cancelled
        d = export_chosen(win, 0)
        assert isinstance(d, Adw.AlertDialog) and win.current['name'] in d.get_heading(), d
        assert not d.get_body()
        d.emit("response", "cancel")
        d.close()
        pump(0.2)
        settings = win.get_visible_dialog()
        assert isinstance(settings, record.Settings) and not saves, (settings, saves)
        d = export_chosen(win, 0)
        d.close()  # (Esc: the same)
        pump(0.2)
        assert win.get_visible_dialog() is settings and not saves
        # where it goes: cancelled, Settings again and nothing made; chosen, Settings gone, then the toast
        d = export_chosen(win, 0)
        d.get_extra_child().set_active(True)
        d.emit("response", "ok")
        d.close()
        pump(0.2)
        saver, parent, chosen, _over = saves[-1]
        assert parent is win and saver.name == f"{win.current['name']}.zip" and win.get_visible_dialog() is settings, saves
        cancelled.append(True)
        chosen(saver, None)
        pump(0.2)
        assert win.get_visible_dialog() is settings and not toasts and not os.path.exists(path)
        del cancelled[:]
        chosen(saver, None)
        wait_for(lambda: toasts and win.get_visible_dialog() is None, "said, Settings gone", 5)
        assert toasts == [f"Exported to {path}"], toasts
        with zipfile.ZipFile(path) as z:
            assert any(n.startswith("chats/") for n in z.namelist()) and "glossary/Systems for me.md" in z.namelist(), z.namelist()
        # them all
        d = export_chosen(win, 1)
        assert isinstance(d, Adw.AlertDialog), d
        d.close()
        wait_for(lambda: isinstance(win.get_visible_dialog(), record.Settings), "asked no more")
        win.get_visible_dialog().close()
        wait_for(lambda: win.get_visible_dialog() is None, "Settings closed")
        # no notebook open: no row for it
        current, win.current = win.current, None
        try:
            assert len(record.Settings(win).export_rows) == 1
        finally:
            win.current = current
    finally:
        record.Gtk.FileDialog = was
        del win.toast
        folders_back(win, folders)


def test_export_sizes_off_the_main_loop(app):
    """A large chat doesn't hold Settings up: it says it's working the size out meanwhile. Closed before they're known, it's
    left alone; opened again, no chat's read twice or two at once. Its window gone, the same. One that can't be worked out says
    so. (#124)"""
    win = app.win
    select(win, "a")
    folders = win.current.get("folders")
    win.current["folders"] = [REPO]
    gate, reading, most, reads, read = threading.Event(), [], [0], [], record.chat_text

    def held(*a):
        reads.append(a[0])
        reading.append(a[0])
        most[0] = max(most[0], len(reading))
        gate.wait(20)
        reading.remove(a[0])
        return read(*a)

    def working(settings):
        return [r.get_subtitle() for r in settings.export_rows] == ["Working out size…"] * 2
    forget_zip_sizes()
    record.chat_text = held
    try:
        begun = time.monotonic()
        first = settings_opened(win)
        wait_for(lambda: reads, "a chat being read")
        pump(0.3)
        assert time.monotonic() - begun < 10, "the main loop went on"
        assert reading and working(first), [r.get_subtitle() for r in first.export_rows]
        first.close()
        wait_for(lambda: not first.get_realized(), "closed")
        second = settings_opened(win)
        pump(0.3)
        assert working(second) and working(first), "still being read"
        gate.set()
        export_sizes_known(second.export_rows)
        pump(0.2)
        assert working(first), [r.get_subtitle() for r in first.export_rows]
        assert most[0] == 1 and len(reads) == len(set(reads)), (most, reads)
        once = list(reads)
        second.close()
        wait_for(lambda: win.get_visible_dialog() is None, "closed")
        third = settings_opened(win)
        export_sizes_known(third.export_rows)
        assert reads == once, "known, and not changed"
        assert third.export_rows[0].get_subtitle() == second.export_rows[0].get_subtitle()
        third.close()
        wait_for(lambda: win.get_visible_dialog() is None, "closed")
        # closed: no more chats are read for it
        forget_zip_sizes()
        gate.clear()
        del reads[:]
        record.chat_text = held
        closing = settings_opened(win)
        wait_for(lambda: reads, "a chat being read")
        closing.close()
        wait_for(lambda: win.get_visible_dialog() is None, "closed")
        gate.set()
        wait_for(lambda: not reading, "that one read")
        pump(0.3)
        assert len(reads) == 1, reads
        # a chat gone as it's read: what it comes to without them is still known
        forget_zip_sizes()
        record.chat_text = lambda *_a: (_ for _ in ()).throw(OSError("gone"))
        failed = settings_opened(win)
        export_sizes_known(failed.export_rows)
        assert all(r.get_subtitle().endswith("; with glossary and chats, not known") for r in failed.export_rows), \
            [r.get_subtitle() for r in failed.export_rows]
        failed.close()
        wait_for(lambda: win.get_visible_dialog() is None, "closed")
        # nothing can be worked out
        deflating, record.deflated_size = record.deflated_size, lambda _t: 1 / 0
        try:
            failed = settings_opened(win)
            export_sizes_known(failed.export_rows)
            assert [r.get_subtitle() for r in failed.export_rows] == ["Size not known"] * 2
            failed.close()
            wait_for(lambda: win.get_visible_dialog() is None, "closed")
        finally:
            record.deflated_size = deflating
    finally:
        gate.set()
        record.chat_text = read
        forget_zip_sizes()
        select(app.win, "a")
        folders_back(app.win, folders)


def test_feedback_over_settings_and_notes_lines(app):
    """#140, as Baude's: on Settings, ⌘. (a real key, where there's a way to post one) brings Feedback over it, and + captures
    Settings at once, then marks its regions, then the note. On a long page of notes a capture has the lines on screen,
    wrapped as they are, none scrolled away, no markup that's hidden, each with its own place: a small region is the
    lines it runs through, not the page, in feedback.md and the issue's body."""
    import mouse
    feedback = record.feedback
    win = app.win
    select(win, "a")
    d = win.open_note("Long notes")
    word = "word " * 60
    d.buf.set_text("\n\n".join(f"**Para{n:03}** {word}" for n in range(120)))
    d.buf.place_cursor(d.buf.get_end_iter())
    pump(0.5)
    adj = d.scroll.get_vadjustment()
    adj.set_value(adj.get_upper() / 2)
    pump(0.5)
    win.feedback_draft = None

    def press():
        if mouse.available():
            mouse.Mouse(win).key(47, ".", flags=mouse.COMMAND)
        else:
            sc = next(sc for c in win.observe_controllers() if isinstance(c, Gtk.ShortcutController) for sc in c
                      if sc.get_trigger().to_string() == widgets.FEEDBACK_KEY)
            sc.get_action().activate(Gtk.ShortcutActionFlags(0), win, None)
        pump(0.3)

    d.view.grab_focus()
    pump(0.1)
    press()
    dlg = win.get_visible_dialog()
    assert isinstance(dlg, feedback.FeedbackDialog) and win.get_focus() is dlg.text, (dlg, win.get_focus())
    press()
    assert list(win.get_dialogs()) == [dlg], "the key again leaves it"
    # nothing open: the bar. What it captures is the page as it stands
    dlg.add_btn.emit("clicked")
    pump(0.3)
    assert win.capture_bar.get_reveal_child() and win.capture_btn.get_mapped() and not list(win.get_dialogs())
    press()  # (with the bar up the key's Capture)
    wait_for(lambda: win.feedback_draft.captures, "captured")
    pump(0.3)
    cap = win.feedback_draft.captures[0]
    editor = win.get_visible_dialog()
    assert isinstance(editor, feedback.RegionEditor) and editor.capture is cap
    notes = [i for i in cap.items if i["area"] == "notes: Long notes"]
    assert 3 < len(notes) < 200, len(notes)
    numbers = sorted(set(re.findall(r"Para(\d+)", " ".join(i["text"] for i in notes))))
    assert numbers and numbers[0] != "000" and numbers[-1] != "119", ("in sight, not scrolled away", numbers)
    assert all("\n" not in i["text"] and len(i["text"]) < 400 for i in notes), "a line each"
    assert all(a["y"] < b["y"] for a, b in zip(notes, notes[1:])), "each with its place, in order"
    assert not any("**" in i["text"] for i in notes if "Para" in i["text"]), "the markup, hidden, isn't on screen"
    ok, at = d.view.compute_point(win.get_child(), Graphene.Point().init(0, 0))
    wanted = next(i for i in notes if "Para" in i["text"])
    k = round(cap.width / win.get_child().get_width())
    assert wanted["x"] >= at.x * k, "where it's drawn"
    first, second, third = wanted["words"][:3]
    # (the words where they're drawn, though what's in the page before them is hidden: from the caret's places, NotesView)
    buf = d.buf
    plain = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
    place = buf.get_iter_at_offset(plain.index(first[0].strip()) + len(first[0].strip()) + 3)  # (past its **, and a space)
    x = d.view.get_cursor_locations(place)[0].x
    x = d.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, x, 0)[0]
    ok, spot = d.view.compute_point(win.get_child(), Graphene.Point().init(x, 0))
    assert abs(second[1] - spot.x * k) <= 2 * k, (second, spot.x * k)
    region = (second[1] + 2, wanted["y"] + 2, second[2] - second[1] - 4, wanted["h"] - 4)
    cap.regions.append(region)
    assert [t["text"] for t in cap.text_in(region)] == [second[0].strip()], cap.text_in(region)
    whole = (wanted["x"], wanted["y"] + 2, wanted["w"], wanted["h"] - 4)
    assert [t["text"] for t in cap.text_in(whole)] == [wanted["text"]]
    page = cap.page_text()
    assert "## notes: Long notes" in page and page.index(notes[0]["text"]) < page.index(notes[-1]["text"])
    editor.done_btn.emit("clicked")
    pump(0.3)
    note = win.get_visible_dialog()
    assert isinstance(note, feedback.FeedbackDialog), "the note after it"
    note.text.get_buffer().set_text("This note line")
    folder = note.save()
    pump(0.3)
    md = open(os.path.join(folder, "feedback.md"), encoding="utf-8").read()
    body = feedback.issue_body(md, folder)
    said = second[0].strip()
    assert md.index("## Context") < md.index(f"> {said}\n") < md.index("## Capture 1") and md.count("> ") == 1, md
    assert body.index("## Context") < body.index(f"```\n{said}\n```") < body.index("## Capture 1"), body
    for text in (md, body):
        assert "Para000" not in text and "Para119" not in text and wanted["text"] not in text, text
    assert len(body) < 2000, "not the page"

    # the chat read: its lines too
    win.open_chat(CHAT)
    pump(0.5)
    read = [i for i in feedback.capture_window(win).items if i["area"] == "chat"]
    assert read and all("\n" not in i["text"] for i in read), read
    win.open_note("Long notes")
    pump(0.2)

    # Settings open: Feedback over it, + captures it at once
    Settings = win.lookup_action("settings")
    Settings.activate(None)
    pump(0.4)
    settings = win.get_visible_dialog()
    assert isinstance(settings, record.Settings)
    press()
    dlg = win.get_visible_dialog()
    assert isinstance(dlg, feedback.FeedbackDialog) and list(win.get_dialogs()) == [settings, dlg]
    dlg.add_btn.emit("clicked")
    assert not win.capture_bar.get_reveal_child()
    wait_for(lambda: isinstance(win.get_visible_dialog(), feedback.RegionEditor), "captured, and on to its regions")
    pump(0.3)
    cap = win.feedback_draft.captures[0]
    assert list(win.get_dialogs()) == [settings, win.get_visible_dialog()], "Settings still under it"
    shown = [i["text"] for i in cap.items]
    assert "Full-width text" in shown and not any("Para" in t for t in shown), shown
    assert {i["area"] for i in cap.items} == {f"dialog: {settings.get_title()}"}, {i["area"] for i in cap.items}
    win.get_visible_dialog().close()
    pump(0.3)
    win.feedback_dialog.discard()
    pump(0.2)
    settings.close()
    pump(0.3)
    menu = next(b for b in walk(win) if isinstance(b, Gtk.MenuButton) and b.get_icon_name() == "open-menu-symbolic")
    sections = [menu.get_menu_model().get_item_link(i, "section") for i in range(menu.get_menu_model().get_n_items())]
    accel = next(s.get_item_attribute_value(i, "accel") for s in sections if s for i in range(s.get_n_items())
                 if s.get_item_attribute_value(i, "action").get_string() == "win.feedback")
    assert accel.get_string() == widgets.FEEDBACK_KEY
    d.buf.set_text("")
    win.tabs.close(("note", "Long notes"))
    pump(0.1)


def test_feedback_note_box(app):
    """#152, #153, as Baude's: Send Feedback is its header and the note's box, as wide as there's room for, the box Record
    asks in, and no words under the header. The + in its corner, a real click, starts a capture; a capture shows over the
    box, only then, and its × removes it, ⌘↩ still saving after. A long note scrolls in the box, no character under the +,
    in light and dark and at every size."""
    try:
        _feedback_note_box(app)
    finally:  # (what it had open, if it failed part way, would be the next test's)
        for _ in range(4):
            if (left := app.win.get_visible_dialog()) is None:
                break
            left.close()
            pump(0.3)
        app.win.feedback_draft = None


def _feedback_note_box(app):
    import io
    import mouse
    from PIL import Image
    feedback = record.feedback
    win = app.win
    win.set_zoom(1.0)  # (as a test before may have left it: the chat's box follows it, the note's doesn't)
    widgets.save_settings(text_scale=None, icon_scale=None)
    widgets.apply_scales(win.get_display())
    win.right_slide.set_shown(True)  # (and the chat's box there to measure against: a test before may have closed it,
    win.right_stack.set_visible_child_name("record")  # or left another page showing)
    select(win, "a")
    win.feedback_draft = None
    style = Adw.StyleManager.get_default()
    scheme = style.get_color_scheme()

    def click(widget):
        """A real click, its press and release posted together: GDK puts in moves of its own, from where the real pointer
        is, and one between them would cancel it."""
        if not mouse.available():
            widget.emit("clicked")
        else:
            ok, at = widget.compute_point(win, Graphene.Point().init(widget.get_width() / 2, widget.get_height() / 2))
            tx, ty = win.get_surface_transform()
            hand = mouse.Mouse(win)
            hand.post(mouse.MOVED, at.x + tx, at.y + ty)
            hand.settle()
            mouse.Mouse.held = 1
            hand.post(mouse.LEFT_DOWN, at.x + tx, at.y + ty)
            hand.post(mouse.LEFT_UP, at.x + tx, at.y + ty)
            hand.settle()
            mouse.Mouse.held = 0
        pump(0.4)

    def corner(box, btn):
        ok, b = btn.compute_bounds(box)
        return (round(box.get_width() - b.get_x() - b.get_width()), round(box.get_height() - b.get_y() - b.get_height()),
                btn.get_width(), btn.get_height())

    def top_corner(widget):
        """What's drawn in its top right corner: its rounding and its fill."""
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(widget).snapshot(snap, widget.get_width(), widget.get_height())
        at = Graphene.Rect().init(widget.get_width() - 16, 0, 16, 12)
        tex = win.get_native().get_renderer().render_texture(snap.to_node(), at)
        return Image.open(io.BytesIO(tex.save_to_png_bytes().get_data())).convert("RGBA").tobytes()

    def fits(dlg, note):
        buf = dlg.text.get_buffer()
        buf.set_text("\n".join(note for _ in range(12)))
        pump(0.3)
        tall = dlg.get_child().get_height()
        buf.set_text("\n".join(note for _ in range(60)))
        pump(0.3)
        adj = dlg.text.get_parent().get_vadjustment()
        assert adj.get_upper() > 3 * adj.get_page_size(), (adj.get_upper(), adj.get_page_size())
        assert dlg.box.get_height() <= 280 and dlg.get_child().get_height() == tall, (dlg.box.get_height(), tall)
        adj.set_value(adj.get_upper())
        pump(0.2)
        ok, plus = dlg.add_btn.compute_point(dlg.text, Graphene.Point().init(0, 0))
        it, right = buf.get_start_iter(), 0
        while True:
            r = dlg.text.get_iter_location(it)
            right = max(right, dlg.text.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, r.x + r.width, r.y)[0])
            if not it.forward_char():
                break
        assert 0 < right <= plus.x, (right, plus.x)
        buf.set_text("")
        pump(0.1)

    def buttons(dlg, picture):
        """The strip's thumbnails' buttons (the picture in them), or their ×s."""
        return [w for w in walk(dlg.shots) if isinstance(w, Gtk.Button)
                and any(isinstance(p, widgets.FixedPicture) for p in walk(w)) == picture]

    dlg = win.open_feedback()
    pump(0.3)
    body = dlg.box.get_parent()
    dlg.found_repo("o/r")  # (gh signed in: it says no more)
    pump(0.1)
    assert not [w for w in walk(body) if isinstance(w, Gtk.Label) and w.get_mapped()], "no words under the header"
    assert win.get_focus() is dlg.text, win.get_focus()
    assert not dlg.strip.get_mapped() and dlg.shots.get_first_child() is None, "no captures: no strip"
    assert dlg.box.get_width() == body.get_width() > 0.9 * dlg.get_child().get_width(), dlg.box.get_width()
    assert corner(dlg.box, dlg.add_btn) == corner(win.chat.writing, win.chat.send), \
        (corner(dlg.box, dlg.add_btn), corner(win.chat.writing, win.chat.send))
    dlg.text.get_buffer().set_text("Too tall.")
    pump(0.1)
    click(dlg.add_btn)
    assert win.capture_bar.get_reveal_child() and not list(win.get_dialogs()), "nothing open: the bar"
    win.capture_btn.emit("clicked")
    wait_for(lambda: win.feedback_draft.captures, "captured")
    pump(0.3)
    win.get_visible_dialog().done_btn.emit("clicked")
    pump(0.4)
    dlg = win.feedback_dialog
    assert win.get_focus() is dlg.text and dlg.draft.text == "Too tall.", win.get_focus()
    assert dlg.strip.get_mapped(), "a capture: the strip"
    ok, strip = dlg.strip.compute_bounds(dlg.box)
    assert strip.get_y() + strip.get_height() < 0, "over the box"
    click(buttons(dlg, True)[0])
    assert isinstance(win.get_visible_dialog(), feedback.RegionEditor), win.get_visible_dialog()
    win.get_visible_dialog().done_btn.emit("clicked")
    pump(0.4)
    click(buttons(dlg, False)[0])
    assert not win.feedback_draft.captures and not dlg.strip.get_mapped(), "the last gone: no strip"
    assert win.get_focus() is dlg.text, win.get_focus()
    if mouse.available():
        mouse.Mouse(win).key(*mouse.RETURN, flags=mouse.COMMAND)
        pump(0.4)
    else:
        dlg.save()
    assert dlg.get_parent() is None and win.feedback_draft is None, "⌘↩ saves"
    shutil.rmtree(feedback.feedback_items()[0]["folder"])

    # a long note, in light and dark and at each size (the sizes last: as the window restyles a click can be lost)
    dlg = win.open_feedback()
    pump(0.3)
    note = "A line of a note that runs on past where the plus sits in the corner of the box, and wraps there. " * 2
    try:
        for force in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
            style.set_color_scheme(force)
            pump(0.3)
            assert top_corner(dlg.box) == top_corner(win.chat.writing), force
            fits(dlg, note)
        style.set_color_scheme(scheme)
        for text, icons in ((2.0, 2.0), (0.7, 0.7)):
            widgets.save_settings(text_scale=text, icon_scale=icons)
            widgets.apply_scales(win.get_display())
            pump(0.3)
            assert corner(dlg.box, dlg.add_btn) == (10, 10, 34, 34), (text, icons)
            fits(dlg, note)
        widgets.save_settings(text_scale=None, icon_scale=None)
        widgets.apply_scales(win.get_display())
        win.set_zoom(2.0)  # (Settings → Global Scale goes to it)
        pump(0.3)
        assert corner(dlg.box, dlg.add_btn) == (10, 10, 34, 34)
        fits(dlg, note)
    finally:
        style.set_color_scheme(scheme)
        widgets.save_settings(text_scale=None, icon_scale=None)
        widgets.apply_scales(win.get_display())
        win.set_zoom(1.0)
        dlg.discard()
        pump(0.5)


def test_export_to_a_zip(app):
    """Settings → Export Notebook… and Export All…: the notes and the conversation in a zip, as Markdown; with the option, the
    glossary and what's shown of the chats a notebook has to do with, and none it hasn't. Every 10 minutes all of them go to the
    one zip in a .local folder if they've changed, in place of the last. Nothing's left half-made. (#86)"""
    import zipfile
    win = app.win
    select(win, "a")
    out = os.path.join(TMP, "exports")
    os.makedirs(out, exist_ok=True)
    book = {"id": "x1", "name": "Shop notes", "folders": [REPO], "pages": {record.HOME: "Home words", "Plans:Next": "Next words", "Empty": " "},
            "messages": [{"role": "user", "text": "What is it?", "quote": "a passage"}, {"role": "record", "text": "A thing."}]}
    other = {"id": "x2", "name": "Elsewhere", "folders": [], "pages": {record.HOME: "Other words"}, "messages": []}
    with open(os.path.join(CHATS, "tools-1.jsonl"), "w") as f:  # (what's not shown of a chat stays out)
        for d in ({"type": "user", "cwd": REPO, "uuid": "u1", "message": {"role": "user", "content": "Run it"}},
                  {"type": "assistant", "cwd": REPO, "uuid": "a1", "message": {"id": "m1", "role": "assistant", "content": [
                      {"type": "tool_use", "name": "Bash", "input": {"command": "secret-command"}}, {"type": "text", "text": "It ran."}]}},
                  {"type": "user", "cwd": REPO, "uuid": "u2", "toolUseResult": {}, "message": {"role": "user", "content": [
                      {"type": "tool_result", "content": "secret-output"}]}}):
            f.write(json.dumps(d) + "\n")
    try:
        # the notes alone
        path = os.path.join(out, "plain.zip")
        record.export_zip(path, [book, other])
        with zipfile.ZipFile(path) as z:
            names = sorted(z.namelist())
            assert names == ["conversations/Shop notes.md", "notes/Elsewhere/Home.md", "notes/Shop notes/Home.md",
                             "notes/Shop notes/Plans/Next.md"], names
            assert z.read("notes/Shop notes/Plans/Next.md").decode() == "Next words"
            said = z.read("conversations/Shop notes.md").decode()
            assert "What is it?" in said and "> a passage\n" in said and "A thing." in said
        two = {**book, "messages": [{"role": "user", "text": "So?", "quote": "one\ntwo"}]}
        assert "> one\n> two\n" in record.conversation_text(two), record.conversation_text(two)
        # no two notebooks in one folder, even one named as the other's would be
        same = [{"id": "a1", "name": "Foo", "pages": {}}, {"id": "b1", "name": "Foo", "pages": {}}, {"id": "c1", "name": "Foo (a1)", "pages": {}}]
        folders = record.book_folders(same)
        assert len(set(folders.values())) == 3 and folders["b1"] == "Foo (b1)", folders
        # with the glossary and chats: those in its folder, not in the other's
        path = os.path.join(out, "all.zip")
        record.export_zip(path, [book, other], extras=True)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            assert "glossary/Shop notes.md" in names and "glossary/Elsewhere.md" not in names, names
            assert "RelayRunner" in z.read("glossary/Shop notes.md").decode()
            chats = [n for n in names if n.startswith("chats/")]
            assert chats and all(n.startswith("chats/Shop notes/") for n in chats), names
            text = "".join(z.read(n).decode() for n in chats)
            assert "How do the jobs run?" in text and "It ran." in text
            assert "secret-command" not in text and "secret-output" not in text, "only what's shown"
        # a chat cited or named in the notes comes too, wherever it is
        session = os.path.basename(CHAT)[:-len(".jsonl")]
        for words in (f"[“a”]({record.CITE_SCHEME}{session}?message=#:~:text=a)", f"[[@{record.session_info(CHAT)[0]}]]"):
            cited = {**other, "pages": {record.HOME: words}}
            found = record.chats_of(cited, [(CHAT, *record.session_info(CHAT))])
            assert [c[0] for c in found] == [CHAT], words
        assert not record.chats_of(other, [(CHAT, *record.session_info(CHAT))]), "nothing to do with it"
        # one that can't be made leaves what was there, and nothing beside it
        def broken(*_a):
            raise OSError("no")
        was, record.conversation_text = record.conversation_text, broken
        try:
            with open(path, "rb") as f:
                before = f.read()
            try:
                record.export_zip(path, [book])
                assert False, "it should have failed"
            except OSError:
                pass
            with open(path, "rb") as f:
                assert f.read() == before and not os.path.exists(path + ".tmp")
        finally:
            record.conversation_text = was
        # Settings: what's asked, then where it goes
        asked_for = []
        win.choose_zip = lambda *a: asked_for.append(a)
        try:
            d = export_chosen(win, 0)
            d.get_extra_child().set_active(True)
            d.emit("response", "ok")
            d.close()
            assert asked_for[-1] == ([win.current], win.current["name"], True), asked_for
            pump(0.2)
            d = export_chosen(win, 1)
            d.emit("response", "ok")
            d.close()
            assert asked_for[-1] == (win.store.notebooks, "Notebooks", False) and asked_for[-1][0] is win.store.notebooks, asked_for
            pump(0.2)
        finally:
            del win.choose_zip
            if isinstance(win.get_visible_dialog(), record.Settings):
                win.get_visible_dialog().close()
            wait_for(lambda: win.get_visible_dialog() is None, "Settings closed")
        # one that fails says so, whatever it was: it isn't taken for done
        said = []
        made, record.export_zip = record.export_zip, lambda *_a: (_ for _ in ()).throw(MemoryError())
        try:
            win.export([win.current], os.path.join(out, "never.zip"), False, said.append)
            wait_for(lambda: said, "said", 5)
        finally:
            record.export_zip = made
        assert said == ["MemoryError()"], said
        path = os.path.join(out, "window & co.zip")
        toasts, win.toast = [], lambda text: toasts.append(text)
        try:
            win.export([win.current], path, False)
            wait_for(lambda: toasts, "said", 5)
        finally:
            del win.toast
        assert toasts == [f"Exported to {path.replace('&', '&amp;')}"], toasts
        with zipfile.ZipFile(path) as z:
            assert any(n.startswith(f"notes/{win.current['name']}/") for n in z.namelist()), z.namelist()
        # rolling: in a .local folder (here, this run's), only when something's changed
        rolling = record.backup_zip()
        assert rolling.startswith(os.environ["XDG_DATA_HOME"]) and rolling.endswith(".zip")
        win.store.save_now()
        assert win.roll_backup() is True, "and again in 10 minutes"
        wait_for(lambda: not win.zipping and os.path.exists(rolling), "the backup", 5)
        made = os.stat(rolling).st_mtime_ns
        with zipfile.ZipFile(rolling) as z:
            assert f"notes/{win.current['name']}/Home.md" in z.namelist() and not any(n.startswith("chats/") for n in z.namelist())
        win.roll_backup()
        pump(0.5)
        assert os.stat(rolling).st_mtime_ns == made and not win.zipping, "nothing's changed: left as it is"
        win.store.save()
        win.store.save_now()
        win.roll_backup()
        wait_for(lambda: not win.zipping and os.stat(rolling).st_mtime_ns != made, "made again", 5)
        # notes that couldn't be read aren't a reason to replace the last good one with nothing
        kept = os.stat(rolling).st_mtime_ns
        real, win.store.notebooks = win.store.notebooks, [{"id": "n", "name": "New", "pages": {record.HOME: ""}, "messages": []}]
        try:
            win.store.unzipped = True
            win.roll_backup()
            pump(0.3)
            assert os.stat(rolling).st_mtime_ns == kept and not win.zipping, "nothing written anywhere: left as it is"
        finally:
            win.store.notebooks = real
        # with the glossary and chats, as Settings says, whether or not the notebooks have changed; kept
        select(win, "a")
        win.current.setdefault("folders", []).append(REPO)
        win.activate_action("win.backupall", None)
        assert win.store.backup_all is True
        with open(win.store.path) as f:
            assert json.load(f)["backup_all"] is True
        win.store.save_now()
        win.roll_backup()
        wait_for(lambda: not win.zipping, "the backup", 5)
        made = os.stat(rolling).st_mtime_ns
        win.roll_backup()
        wait_for(lambda: not win.zipping and os.stat(rolling).st_mtime_ns != made, "made again", 5)
        with zipfile.ZipFile(rolling) as z:
            assert any(n.startswith("chats/") for n in z.namelist()) and any(n.startswith("glossary/") for n in z.namelist())
    finally:
        os.remove(os.path.join(CHATS, "tools-1.jsonl"))
        if win.store.backup_all:
            win.activate_action("win.backupall", None)
        if REPO in win.current.get("folders", []):
            win.current["folders"].remove(REPO)


def test_no_focus_ring(app):
    """As in Baude: no ring round what's clicked, what the keys move to or a box being typed in (focus_rings). (#100)"""
    import focus_rings
    focus_rings.check(app.app, pump)
    focus_rings.check_window(app.win, pump)


def test_chats_arent_searched_by_when(app):
    """How long ago a chat was is shown beside it, not searched: a date filter would be another thing. "ago" doesn't find
    every chat, and its folder and title still find it. (#94)"""
    chats = record.ChatNames()
    chats.chats = [{"path": CHAT, "title": "How do the jobs run?", "cwd": REPO, "mtime": time.time() - 3 * 7 * 86400}]
    item = chats.make_items()[0]
    assert "ago" in item["summary"], item["summary"]
    for typed in ("ago", "3w", "week"):
        assert lookup.ref_palette_rank(item, typed) is None, typed
    folder = os.path.basename(REPO)
    assert lookup.ref_palette_rank(item, folder) is not None and lookup.ref_palette_rank(item, "jobs") is not None
    assert lookup.ref_palette_rank({**item, "summary": "a thing"}, "chat") is not None, "and what it is"
    # (what isn't a chat still goes by its summary)
    other = {"surface": "x", "canonical": "", "summary": "a helpful summary", "what": "File"}
    assert lookup.ref_palette_rank(other, "helpful") is not None


def test_times_say_when(app):
    """How long ago a chat was says when, with the pointer on it and only there (#94): Open a Chat…'s and Attach…'s rows
    (a real pointer on the row's own label), @'s list, ⌘K's and a name's card."""
    import mouse
    win = app.win
    select(win, "a")
    catalogued(win)
    at = time.time() - 7200
    full = widgets.full_time(at)

    def on(label, words):
        layout = label.get_layout()
        text, (left, top) = layout.get_text(), label.get_layout_offsets()
        spot = layout.index_to_pos(len(text[:text.rfind(words)].encode()))
        return left + spot.x / record.Pango.SCALE + 1, top + (spot.y + spot.height / 2) / record.Pango.SCALE

    def says(label):
        assert widgets.time_tip_at(label, *on(label, "2h ago")) == full, label.get_text()
        assert widgets.time_tip_at(label, *on(label, label.get_text()[:2])) is None, label.get_text()

    recent = record.recent_sessions
    record.recent_sessions = lambda: [{"path": CHAT, "title": "Jobs", "cwd": REPO, "mtime": at}]
    try:
        chats = record.AttachDialog(lambda _s: None, chats=True)
        chats.present(win)
        wait_for(lambda: chats.list.get_row_at_index(0) is not None, "the chats")
        pump(0.5)
        row = chats.list.get_row_at_index(0)
        assert row.get_subtitle() == f"{record.short_dir(REPO)} · 2h ago", row.get_subtitle()
        label = next(w for w in walk(row) if isinstance(w, Gtk.Label) and w.get_text() == row.get_subtitle())
        says(label)
        if mouse.available():
            asked, ask = [], widgets.time_tip_at
            widgets.time_tip_at = lambda w, x, y: (lambda said: (asked.append(said), said)[1])(ask(w, x, y))
            hand = mouse.Mouse(win)
            try:
                x, y = on(label, "2h ago")
                ok, p = label.compute_point(win, Graphene.Point().init(x + 1, y))
                hand.post(mouse.MOVED, p.x - 1, p.y)
                hand.settle()
                hand.post(mouse.MOVED, p.x, p.y)
                wait_for(lambda: asked, "GTK asking", 4)
                assert asked[-1] == full, asked
            finally:
                widgets.time_tip_at = ask
        chats.force_close()
        folders = record.AttachDialog(lambda _s: None)
        folders.present(win)
        wait_for(lambda: folders.folders.get_row_at_index(1) is not None, "the folders")
        pump(0.5)
        row = folders.folders.get_row_at_index(1)
        says(next(w for w in walk(row) if isinstance(w, Gtk.Label) and w.get_text() == row.get_subtitle()))
        folders.force_close()
    finally:
        record.recent_sessions = recent
    pump(0.2)

    names = record.chat_names()
    kept = names.chats, names.at
    names.chats, names.at = [{"path": CHAT, "title": "Zebra planning", "cwd": REPO, "mtime": at}], time.time()
    d = win.open_note(record.HOME)
    saved = d.text()
    try:
        item = next(it for it in names.items() if it["surface"] == "Zebra planning")
        assert item["summary"] == f"{record.short_dir(REPO)} · 2h ago" and item["at"] == at, item
        assert record.what_it_is(item, None) == f"Chat, {item['summary']}", "what's asked says it as it did"
        d.buf.place_cursor(d.buf.get_end_iter())
        d.view.grab_focus()
        for ch in " @zebra":
            d.buf.insert_at_cursor(ch)
        wait_for(lambda: d.pop.get_visible() and d.pop_shown and d.pop_shown[0]["surface"] == "Zebra planning", "@'s list")
        pump(0.3)
        says(next(w for w in walk(d.pop_list) if isinstance(w, Gtk.Label) and w.get_mapped() and "2h ago" in w.get_text()))
        d._hide_pop()
        pal = widgets.RefPalette(win, win.space(), lambda item: None)
        pal.present(win)
        wait_for(lambda: win.get_visible_dialog() is pal, "⌘K")
        pal.entry.set_text("zebra")
        wait_for(lambda: any(isinstance(w, Gtk.Label) and w.get_mapped() and "2h ago" in w.get_text() for w in walk(pal)), "⌘K's list")
        pump(0.3)
        says(next(w for w in walk(pal) if isinstance(w, Gtk.Label) and w.get_mapped() and "2h ago" in w.get_text()))
        pal.force_close()
        pump(0.2)
        # (not popped up: late in a run this window can be on no monitor, offscreen, and macOS aborts on where it would go)
        popup, Gtk.Popover.popup = Gtk.Popover.popup, lambda _pop: None
        try:
            win.ref_card(item, at=(d.view, 40, 40))
        finally:
            Gtk.Popover.popup = popup
        pump(0.1)
        says(next(w for w in walk(win.card) if isinstance(w, Gtk.Label) and "2h ago" in w.get_text()))
        win.hide_card()
    finally:
        names.chats, names.at = kept
        d.buf.set_text(saved)


def test_unexplained_nouns():
    """Read's red: nouns.py reads a reply's prose (no code blocks, no markup, a block at a time), your notes' To learn
    list isn't what you know, and a reply's phrases the detector flagged are drawn red, but not in code (a code span,
    a link: set aside first). (NLTK itself isn't run here: it's fetched by uv.)"""
    import nouns
    md = ("## The platform\n\nThe **platform** runs [the job](https://x.y/z) and `build_app`.\n\n"
          "```\nthe code isn't prose\n```\n- an item\n- [ ] a box\n\n| col | the cell |\n|---|---|\n> a quote")
    assert nouns.prose_blocks(md) == ["The platform", "The platform runs the job and build_app.", "an item", "a box",
                                      "col", "the cell", "a quote"], nouns.prose_blocks(md)
    assert record.nouns_command() is None, "RECORD_NOUNS=off: not run"
    page = "- platform: where apps run\n\n## To learn\n- backend\n\n## Later\n- more"
    assert record.without_to_learn(page) == "- platform: where apps run\n\n## Later\n- more"
    names = record.ReaderNames(type("NoNames", (), {"items": lambda self: []})())
    reply = "Then the platform's backend restarts `the platform`, as the platform does."
    names.said = {widgets.message_key(reply): re.compile(r"(?<![\w-])(?:the platform's backend|the platform)(?![\w-])")}
    was = widgets.SIGNAL
    widgets.SIGNAL = names
    try:
        markup = widgets.markdown_blocks(reply)[0][1]
    finally:
        widgets.SIGNAL = was
    red = re.findall(r'<span foreground="#[0-9a-f]{6}">([^<]*)</span>', markup)
    assert red == ["the platform's backend", "the platform"], markup
    assert 'bgalpha="22%">the platform</span>' in markup, "the code span: code, not red"


def test_chats_once():
    """A chat the desktop app restarted, forked or resumed is one chat in @, ⌘K and Open a Chat…: the transcripts it
    left behind (its priorCliSessionIds) aren't listed, though they're still in Claude Code's folder with its title."""
    folder = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "-once")
    os.makedirs(folder)
    ids = {"old": "0ld00000-0000-0000-0000-000000000000", "new": "4e700000-0000-0000-0000-000000000000",
           "other": "07e00000-0000-0000-0000-000000000000"}
    for i, (which, sid) in enumerate(ids.items()):
        title = "Other chat" if which == "other" else "Pages planning"
        with open(os.path.join(folder, sid + ".jsonl"), "w") as f:
            f.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": sid}) + "\n")
            f.write(json.dumps({"type": "user", "cwd": REPO, "message": {"content": "plan the pages"}}) + "\n")
        os.utime(f.name, (time.time() - 3600 + i * 600,) * 2)
    desk = os.path.join(TMP, "desktop-sessions")
    os.makedirs(os.path.join(desk, "account", "org"))
    with open(os.path.join(desk, "account", "org", "local_x.json"), "w") as f:
        json.dump({"cliSessionId": ids["new"], "priorCliSessionIds": [ids["old"]], "title": "Pages planning"}, f)
    real = record.desktop_sessions_dir
    record.desktop_sessions_dir = lambda: desk
    try:
        names = [os.path.basename(p) for p in record.chat_transcripts() if os.path.dirname(p) == folder]
        assert sorted(names) == sorted([ids["new"] + ".jsonl", ids["other"] + ".jsonl"]), names
        listed = [s["title"] for s in record.recent_sessions() if os.path.dirname(s["path"]) == folder]
        assert listed == ["Other chat", "Pages planning"], listed  # (newest first)
        chats = record.ChatNames()
        chats._read()
        mine = [c for c in chats.chats if os.path.dirname(c["path"]) == folder]
        assert [os.path.basename(c["path"]) for c in mine] == [ids["other"] + ".jsonl", ids["new"] + ".jsonl"], mine
        with open(os.path.join(desk, "account", "org", "local_x.json"), "w") as f:
            f.write("{not json")  # (one the app's halfway through writing: nothing left behind, for now)
        assert ids["old"] + ".jsonl" in [os.path.basename(p) for p in record.chat_transcripts()]
    finally:
        record.desktop_sessions_dir = real
        shutil.rmtree(folder)


def main():
    global SHOTS
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="only run tests whose name contains this")
    ap.add_argument("--shots", help="directory to save window snapshots into")
    args = ap.parse_args()
    if args.shots:
        SHOTS = os.path.abspath(args.shots)
        os.makedirs(SHOTS, exist_ok=True)
    app = App()
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and args.k in n]
    failed = 0
    for name, fn in tests:
        t0 = time.monotonic()
        try:
            fn(app) if fn.__code__.co_argcount else fn()
            print(f"PASS  {name}  ({time.monotonic() - t0:.1f}s)")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    app.app.quit()
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
