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
os.environ["BAUDE_LAUNCHERS"] = os.path.join(TMP, "Applications")  # (nor ~/Applications: Record's launcher)
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "xdg-cache")  # (what's kept of GitHub's lists: test_github_lists)
os.environ["FAKE_RECORD_LOG"] = os.path.join(TMP, "fake.log")
os.environ["RECORD_NOUNS"] = "off"  # (no NLTK fetched by uv for the chats read here: test_unexplained_nouns)
os.environ["BAUDE_GH"] = ""  # (no issue filed on GitHub for the feedback saved here)
os.environ["BAUDE_KEEP_FILING"] = "0"  # (nor a note going up by itself, to a stand-in gh, mid-test)
os.environ["BAUDE_UPDATE"] = "0"  # (no git fetched, no restart onto a new version)
# nor this machine's host process, nor ssh's shared connections (and through them the mesh): a folder of the run's own
# (short: a socket's path is), as test_baude's
os.environ["BAUDE_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="bd-", dir="/tmp")
os.environ["BAUDE_SSH"] = "false"  # (nor anywhere over ssh: a host added by hand is never reached)
os.environ["BAUDE_MENU_BAR"] = "0"  # (nor an item in the real menu bar, from a host process a test starts)
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
import lookupgit  # noqa: E402
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


def transcript(name, replies, cwd=REPO):
    path = os.path.join(CHATS, f"{name}.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps({"type": "user", "cwd": cwd, "uuid": "u1",
                            "message": {"role": "user", "content": "How do the jobs run?"}}) + "\n")
        for i, text in enumerate(replies):
            f.write(json.dumps({"type": "assistant", "cwd": cwd, "uuid": f"a{i}",
                                "message": {"id": f"m{i}", "role": "assistant",
                                            "content": [{"type": "text", "text": text}]}}) + "\n")
    return path


CHAT = transcript("chat-1", [
    "`RelayRunner` in `relay_jobs.py` runs each job; the golden files sit beside it. See "
    "[the docs](https://example.com/docs).",
    "Merge feature/fast-lists once orders-api is green."])
OTHER = transcript("chat-2", ["Nothing to see."])

import record  # noqa: E402
import recordstore  # noqa: E402
import recordui  # noqa: E402
import diaryeditor  # noqa: E402
import recordreader  # noqa: E402
import recordwindow  # noqa: E402
import widgets  # noqa: E402
import imageviews  # noqa: E402
import markdownview  # noqa: E402
import composerview  # noqa: E402
import panels  # noqa: E402
import lightbox  # noqa: E402
import chatfind  # noqa: E402
import pickers  # noqa: E402
import meshui  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

panels.SLIDE_MS = 0  # (a side bar opens and closes at once here: test_side_bars_slide has it slide)
lightbox.Lightbox.FLY_MS = 0  # (the large view opens and closes at once here: test_lightbox_flies has it fly)

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
    import signaltools
    import signalnames
    import baudemeta
    assert issubclass(signalnames.Signal, lookup.Names) and issubclass(baudemeta.SearchSpace, lookup.SearchSpace)
    assert signaltools.catalogue_hits is lookup.catalogue_hits


# ---------------------------------------------------------------- the window

def test_glossary_becomes_diary(app):
    """One from before diaries: its glossary is Home's list, a term with no meaning under To learn; its name
    as it was typed."""
    win = app.win
    t = by_id(win, "a")
    assert "glossary" not in t and t["pages"]["Home"].startswith("- **function**: a recipe") and \
        "## To learn\n- JSON" in t["pages"]["Home"], t["pages"]
    assert [recordstore.full_name(x) for x in win.store.notebooks] == ["Systems for me", "Biology reading"]


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
    """Read draws a reply in Baude's chat's scroller (panels.markdown_scroller): a code block in a chat taller than
    the window is its own height, not squashed to a line (the viewport at its child's natural height). The
    notebooks' column is the pages' width, a notebook a line as tall as a page's, no subtitle."""
    win = app.win
    select(win, "a")
    r = win.reader
    assert r.scroll.get_child().get_vscroll_policy() == Gtk.ScrollablePolicy.NATURAL
    assert win.chat.scroll.get_child().get_vscroll_policy() == Gtk.ScrollablePolicy.NATURAL
    assert win.left_slide.fixed.width == diaryeditor.DiaryEditor.PAGES_WIDTH, "till it's dragged: test_notebooks_column_drags"
    assert not win.books.get_visible() or win.books.get_width() == diaryeditor.DiaryEditor.PAGES_WIDTH, \
        ("held at it, a long name notwithstanding", win.books.get_width())
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
    levels = recordstore.heading_levels(lines)
    assert levels == [0, 1, 0, 0, 2, 2, 0, 3, 2, 0, 1, 0, 0, 0, 0], levels
    at = {name: lines.index(("#" * n) + " " + name) for name, n in (("A", 1), ("A1", 2), ("A2", 2), ("A2x", 3), ("A3", 2), ("B", 1))}

    def swap(name, by):
        done = recordstore.swap_with_sibling(lines, at[name], by)
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
    assert recordstore.swap_with_sibling(skipped, 1, 1) is None and \
        recordstore.swap_with_sibling(skipped, 2, -1) is None


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
    home = win.open_note(recordstore.HOME)
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
            to = composerview.word_stop(text, at, forward, lambda i: i in hidden)
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
        s, e = recordstore.whole_links(source, start, end)
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
    assert recordstore.whole_links(items, items.index("\n"), items.index("]]")) == \
        (items.index("\n"), items.index("]]") + 2)
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
        swapped = composerview.swap_list_item(lines, at, by)
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


def test_side_bars_drag_from_the_edge_thats_seen(app):
    """The arrows and the press are either side of the inside's edge, not a gap short of it: the right bar's rule, and the
    notebooks' column, whose own grip goes on past it. A real press there drags it. Hidden, none of it's there. (#126)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    win.open_note(recordstore.HOME)
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
        win.open_note(recordstore.HOME)
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
    """Settings has the version running and ☰ → Changelog, the commits that version has, newest first, as Baude's."""
    win = app.win
    select(win, "a")
    pump(0.2)
    settings = settings_opened(win)
    pump(0.3)
    try:
        running = record.update.git(record.update.HERE, "log", "-1", "--format=%h", record.update.STARTED)
        has = record.update.git(record.update.HERE, "log", "--no-merges", "--format=%h", record.update.STARTED).split()
        assert running and running in settings.version_row.get_title() and has, settings.version_row.get_title()
        win.activate_action("win.changelog", None)
        log = win.get_visible_dialog()
        wait_for(lambda: [w for w in walk(log) if hasattr(w, "commit") and w.get_mapped()], "the changelog", 3)
        assert [w.commit for w in walk(log) if hasattr(w, "commit")] == has[:50]
        log.close()
    finally:
        settings.close()
        wait_for(lambda: win.get_visible_dialog() is None, "closed")


def test_right_bar_closed_at_launch_opens(app):
    """The right bar closed when the window opened has had no position (the paned's asked for more than it has): opening it slides
    it out to as wide as it can be at the least, not to nothing. (#150)"""
    win = app.win
    select(win, "a")
    slide, paned = win.right_slide, win.paned
    saved, panels.SLIDE_MS = panels.SLIDE_MS, 0
    slide.set_shown(False)
    pump(0.1)
    paned.set_position(100_000)
    pump(0.1)
    panels.SLIDE_MS = 150
    try:
        slide.set_shown(True)
        wait_for(lambda: slide.anim is None, "opened", 3)
        pump(0.2)
        least = slide.pane.get_size_request()[0]
        assert slide.open_size >= least and slide.pane.get_width() >= least, (slide.open_size, least)
        assert 0 < paned.get_width() - paned.get_position() < paned.get_width() / 2, paned.get_position()
    finally:
        panels.SLIDE_MS = saved
        slide.set_shown(True)
        pump(0.1)


def test_notes_go_narrow(app):
    """With the right bar open, its rule dragged as far as it goes stops where the notes are at their least: narrow, and
    none of them under the bar. The find bar and Feedback's Capture bar, each been and gone, take no width (shut, they
    held the notes at 530px)."""
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
        win.paned.set_position(0)  # (as far as it goes: the notes at their least)
        pump(0.3)
        notes, bar = (w.compute_bounds(win)[1] for w in (win.paned.get_start_child(), win.paned.get_end_child()))
        assert win.paned.get_position() > 0 and notes.get_width() <= 220, notes.get_width()
        assert notes.get_x() + notes.get_width() <= bar.get_x(), (notes.get_x() + notes.get_width(), bar.get_x())
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

    (home_item,), _ = menu_of(recordstore.HOME)
    made = []
    try:
        for parent in ("Plans", recordstore.HOME):
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
    win.open_note(recordstore.HOME)
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
        drag(recordreader.FixedWidth.LEAST - 40 - column.width)
        assert column.width == recordreader.FixedWidth.LEAST, column.width
        shot(win, "record-notebooks-column-narrow")
        drag(5000)
        assert column.width == min(recordreader.FixedWidth.MOST, win.get_width() // 2), column.width
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
    spans = lambda text, **kw: markdownview.fence_spans(text.split("\n"), **kw)  # noqa: E731
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
    assert recordstore.heading_levels("```\ncode\n\n# Heading\n```py\n# comment\n```".split("\n")) == \
        [0, 0, 0, 1, 0, 0, 0]
    assert recordstore.heading_levels(["```", "# comment", "", "# Heading"]) == [0, 0, 0, 1]


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


def test_ongoing_feedback_tab(app):
    """☰ → Ongoing Feedback is in Record as in Baude: its tab, read from GitHub (a stand-in), what's ready shown with what
    can be done with it; closed, freed, and back through the history; what's selected and typed through a restart."""
    feedback, fixes = recordwindow.feedback, recordwindow.fixview.fixes
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
    if feedback.code_repo() is None:
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
        assert isinstance(page, recordwindow.fixview.FixesPage) and win.tabs.current() is page
        assert win.content_stack.get_visible_child_name() != "none"
        wait_for(lambda: page.read_at, "the feedback read")
        assert [i["number"] for i in page.listed()] == [1, 2] and page.selected == 1
        assert page.merge_btn.get_visible() and not page.fix_btn.get_visible()
        assert win.tabs.pages[("fixes",)].get_title().endswith("(1)")
        assert page.stack.get_visible_child_name() == "main"
        assert isinstance(page.refresh_btn, imageviews.RefreshButton)  # (it turns as it reads: test_refresh_turns, Baude's)
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
        app.app.win = new = recordwindow.Window(app.app)
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
    """Baude's look (theming.CSS): the notebooks and pages, the bars and the right bar (the Record, Files, Git) the
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
        assert isinstance(row.prose, markdownview.MarkdownView) and row.has_css_class("reader-user")
        said = " ".join(w.get_label() for w in walk(row.prose) if isinstance(w, Gtk.Label))
        assert "<b>this</b>" in said and "**" not in said and "- one" not in said, said
        assert any(w.get_selectable() for w in walk(row.prose) if isinstance(w, Gtk.Label)), "still selectable"
        assert reader.rows[1].prose.text.endswith("…") and len(reader.rows[1].prose.text) == 1201
    finally:
        win.tabs.close(("chat", os.path.realpath(path)))
        os.remove(path)


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
        return d.view.compute_point(d.scroll, recordui.Graphene.Point().init(0, y))[1].y, r.height

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
    """tests/fake_gh.py as gh, with these repositories on it, lookupgit.GITHUB from nothing; no gh again after."""

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
        lookupgit.GITHUB.forget()
        return self

    def __exit__(self, *_):
        os.environ["BAUDE_GH"] = ""
        os.environ.pop("FAKE_GH_OFFLINE", None)
        lookupgit.GITHUB.forget()

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
        for r in (gh or lookupgit.GITHUB)._repos.values():
            r["looked"] = 0.0


def test_github_lists(app):
    """A folder's GitHub repositories (its checkout's origin and upstream, not checkouts under it): their latest and
    open pull requests and issues are names, with a logo for where each stands, in ⌘K's search and #'s; read with GET only;
    a look at one that hasn't changed is one request GitHub doesn't count; a change, two. Kept on disk for the next start;
    nothing in the way of anything else while GitHub can't be asked."""
    work = checkout(os.path.join(TMP, "gh-work"), "https://github.com/acme/one.git")
    subprocess.run(GIT + ["remote", "add", "upstream", "git@github.com:acme/two.git"], cwd=work, check=True)
    checkout(os.path.join(work, "under"), "https://github.com/acme/under.git")
    three = checkout(os.path.join(TMP, "gh-three"), "https://gitlab.com/acme/three.git")
    plain = os.path.join(TMP, "gh-plain")
    os.makedirs(plain, exist_ok=True)
    rest = {"acme/one": [rest_item(1, "Old open issue", ago=9e6),
                         rest_item(5, "Open pull request", pr=True, ago=50),
                         rest_item(6, "Merged pull request", pr=True, state="closed", merged=True, ago=40),
                         rest_item(7, "Closed pull request", pr=True, state="closed", ago=30),
                         rest_item(8, "Draft pull request", pr=True, draft=True, ago=20),
                         rest_item(9, "Closed issue", state="closed", ago=10),
                         rest_item(10, "New issue wraps badly", ago=5),
                         *(rest_item(n, f"Closed long ago {n}", state="closed", ago=9e5) for n in range(100, 400))],
            "acme/two": [rest_item(7, "The other seven")], "acme/three": [rest_item(1, "Not on GitHub")],
            "acme/under": [rest_item(1, "A checkout under it: not looked for")]}
    gh = lookupgit.GITHUB
    with Github(rest) as hub:
        gh.round([work])
        asked = hub.calls()
        looked_at = {c.split("/issues")[0][len("repos/"):] for c in asked}
        assert looked_at == {"acme/one", "acme/two"}, asked
        assert len(asked) == 4, ("the latest and the open ones of each", asked)
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
        assert [it["surface"] for it in space.issues("the other")[0]] == ["acme/two#7"], "or a word of its title"
        assert not space.issues("no such thing")[0]
        assert not space.issues("acme")[0] and not space.issues("two")[0], "not its repository's name"
        assert not space.issues("ther")[0], "a word's start, not the middle of one"
        said = [it["surface"] for it in space.mentions("See acme/two#7, and `acme/one#5` and acme/one#99999, `#10`, #10.")]
        assert said == ["acme/one#5", "acme/two#7"] or said == ["acme/two#7", "acme/one#5"], said
        ref = lookup.catalogue_ref("acme/one#6", one["acme/one#6"]["entry"])
        assert ref.kind == "pr" and ref.status == "resolved" and ref.data["url"] == "https://github.com/acme/one/pull/6"
        assert lookup.ref_icon(ref.kind, ref.data) == "pr_merged" and "Merged pull request" in ref.summary()
        assert lookupgit.github_url("acme/one#6") == "https://github.com/acme/one/issues/6"
        assert lookupgit.github_url("feature/login#12") is None, "a repository it has read, not whatever's spelt so"

        # nothing's changed: not looked at again before it's time; then one request each, not counted, nothing new
        gh.round([work])
        assert hub.calls() == [], "not time yet"
        left, generation = hub.now()["left"], gh.generation
        hub.due()
        gh.round([work])
        asked = hub.calls()
        assert len(asked) == 2 and all("state=all" in c for c in asked), ("one request a repository", asked)
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
        assert len(asked) == 3 and sum("acme/one" in c for c in asked) == 2, asked
        assert hub.now()["left"] == left - 2 and gh.generation != generation and gh.listed(work) is not listing
        assert space.find("acme/one#5")["icon"] == "pr_merged" and space.find("acme/one#11")["what"] == "Issue", \
            "the items made again"
        assert gh.known("acme/one", 5)["state"] == "merged" and gh.known("acme/one", 12345) is None

        # the next start: what's kept is listed at once, whatever GitHub says, and costs nothing to check
        os.environ["FAKE_GH_OFFLINE"] = "1"
        left = hub.now()["left"]
        fresh = lookupgit.GitHub()
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
        assert len(hub.calls()) == 2 and hub.now()["left"] == left, "back: the ETags on disk are good"
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
        assert len(hub.calls()) == 2 and not [it for it in fresh.listed(work) if it["surface"].startswith("acme/two#")]
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
        lookupgit.GITHUB.forget()
        hub.state.update(no_etag=True)
        hub.write()
        hub.calls()
        gh.round([work])
        assert gh._repos["acme/one"]["etags"].get("recent") == ""
        hub.calls()
        gh._repos["acme/one"]["looked"] = time.time() - 120
        gh.round([work])
        assert hub.calls() == [], "not yet"
        gh._repos["acme/one"]["looked"] = time.time() - 301
        gh.round([work])
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

        # many repositories in use: only so many, each looked at less often, the first folder's first
        many = [checkout(os.path.join(TMP, "gh-many"), "https://github.com/acme/own.git")]
        for n in range(40):
            many.append(checkout(os.path.join(TMP, "gh-many-r", f"r{n}"), f"https://github.com/acme/r{n}.git"))
            hub.state["rest"][f"acme/r{n}"] = []
        hub.state["rest"]["acme/own"] = []
        hub.write()
        gh.forget()
        hub.calls()
        gh.round(many)
        asked = hub.calls()
        assert len({c.split("/issues")[0] for c in asked}) == lookupgit.GITHUB_REPOS, len(asked)
        assert asked[0].startswith("repos/acme/own/issues"), asked[0]
        for r in gh._repos.values():
            r["looked"] = time.time() - 120
        gh.round(many)
        assert hub.calls() == [], "24 of them: every 4 minutes"
        for r in gh._repos.values():
            r["looked"] = time.time() - 241
        gh.round(many)
        assert len(hub.calls()) == lookupgit.GITHUB_REPOS

        # shared out: each folder's own first, then an upstream, in turn
        small = checkout(os.path.join(TMP, "gh-small"), "https://github.com/acme/small.git")
        subprocess.run(GIT + ["remote", "add", "upstream", "https://github.com/acme/small-up.git"], cwd=small, check=True)
        chat = checkout(os.path.join(TMP, "gh-chat"), "https://github.com/acme/chat.git")
        checkout(os.path.join(chat, "vendor-ish", "dep"), "https://github.com/acme/vendored.git")
        hub.state["rest"].update({"acme/small": [], "acme/small-up": [], "acme/chat": [], "acme/vendored": []})
        hub.write()
        gh.forget()
        hub.calls()
        gh.round([small, chat])
        asked = [c.split("/issues")[0][len("repos/"):] for c in hub.calls() if "state=all" in c]
        assert asked == ["acme/small", "acme/chat", "acme/small-up"], ("each folder's own, then the rest", asked)

        # a chat's folder: the repository it's in, not the checkouts under it
        gh.forget()
        gh.round([chat])
        assert [c.split("/issues")[0][len("repos/"):] for c in hub.calls() if "state=all" in c] == ["acme/chat"]
        assert gh.own_repository(chat) == "acme/chat" and gh.own_repository(os.path.join(TMP, "gh-plain")) is None
        assert not gh._repos["acme/chat"]["paused"]

        # off the main loop: look returns at once, and a folder with no remote on GitHub asks nothing at all
        gh.forget()
        gh.round([plain])
        gh.round([three])
        assert hub.calls() == [], "no remote on GitHub: nothing"
        assert gh.listed(plain) == ()
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
    assert os.environ["BAUDE_GH"] == "" and lookupgit.gh_path() is None
    cached = os.path.join(os.environ["XDG_CACHE_HOME"], "baude", "github")
    shutil.rmtree(cached, ignore_errors=True)
    size = gh._todo.qsize()
    gh.forget()
    gh.look([work])
    gh.round([work])
    assert gh._todo.qsize() == size and not gh._folders and not gh._repos and gh.listed(work) == ()
    assert not os.path.exists(cached), "nothing at all"


def read_in(win, folder, name):
    """A chat in folder (a transcript of its own) opened and read: its folder's what @, ⌘K, the viewers and GitHub's
    lists know. Its path, for read_back."""
    path = os.path.realpath(transcript(name, ["Nothing to see."], cwd=folder))
    win.open_chat(path)
    pump(0.1)
    return path


def read_back(win, path):
    """The chat read_in opened, closed and gone: the one read before it read again."""
    if path is not None:
        win.tabs.close(("chat", path))
        os.remove(path)
        pump(0.1)


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
        rest_item(62, 'When the "gh" CLI is available, integrate with the git repo of the chat read', ago=60),
        rest_item(63, "Option and an arrow stops after a closing } and a line's full stop", pr=True, ago=120),
        rest_item(60, "Keep the tab strip's fade while a tab is closing", pr=True, state="closed", merged=True, ago=7200),
        rest_item(58, "Try a second renderer for the code view", pr=True, state="closed", ago=9000),
        rest_item(52, "The feedback tab loses its place on refresh", ago=10000),
        rest_item(49, "Selecting across two replies copies only one", state="closed", ago=86000),
        rest_item(47, "Draft: smaller tab close targets", pr=True, draft=True, ago=99000)]}
    page, chat = "On GitHub", None
    manager = Adw.StyleManager.get_default()
    with Github(rest):
        try:
            chat = read_in(win, folder, "gh-shots-chat")
            space = win.space()
            space.refresh()
            wait_for(lambda: not space.busy, "the folder catalogued")
            lookupgit.GITHUB.round(win.github_folders())
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
                pal = pickers.RefPalette(win, space, lambda item: None)
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
            read_back(win, chat)
            win.pages.fill()


def test_watch_github(app):
    """While a window's shown, and only then, GitHub's lists are looked at (once a minute); once they've changed, what's
    on show is told. It holds nothing of the window."""
    asked, told = [], []
    real_look, tick = lookupgit.GITHUB.look, meshui.GITHUB_TICK_S
    # (only what's asked of "a folder": the app's own window has a watch of its own)
    lookupgit.GITHUB.look, meshui.GITHUB_TICK_S = (lambda folders: "a folder" in folders and asked.append(folders)), 1
    in_use = ["a folder"]
    try:
        win = Adw.Window(default_width=200, default_height=100)
        meshui.watch_github(win, lambda w: list(in_use), told.append)
        pump(1.3)
        assert not asked and not meshui.window_shown(win), "not shown yet"
        win.present()
        wait_for(lambda: asked == [["a folder"]], "a look, once it's shown")
        assert meshui.window_shown(win) and not told
        pump(1.2)
        assert len(asked) == 1, "not again for a minute"
        in_use.append("a folder too")
        wait_for(lambda: len(asked) == 2 and asked[1] == in_use, "at once, for folders that are new")
        lookupgit.GITHUB.generation += 1
        wait_for(lambda: len(told) == 1, "told it's changed")
        win.set_visible(False)
        lookupgit.GITHUB.generation += 1
        pump(1.2)
        assert len(told) == 1 and not meshui.window_shown(win), "not while it's hidden"
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
        lookupgit.GITHUB.look, meshui.GITHUB_TICK_S = real_look, tick


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
    page, chat = "On GitHub, by key", None
    with Github(rest):
        try:
            chat = read_in(win, folder, "gh-keys-chat")
            space = win.space()
            space.refresh()
            wait_for(lambda: not space.busy, "the folder catalogued")
            lookupgit.GITHUB.round(win.github_folders())
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
            generation, listing = lookupgit.GITHUB.generation, lookupgit.GITHUB.listed(folder)
            for known in lookupgit.GITHUB._folders.values():
                known["at"] = 0.0
            for r in lookupgit.GITHUB._repos.values():
                r["looked"] = 0.0
            lookupgit.GITHUB.round(win.github_folders())
            assert lookupgit.GITHUB.generation == generation and lookupgit.GITHUB.listed(folder) is listing
            assert d.pop_selected()["surface"] == picked
            key(mouse.ESCAPE)
        finally:
            win.tabs.close(("note", page))
            notebook["pages"].pop(page, None)
            read_back(win, chat)
            win.pages.fill()


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
    wait = recordreader.NAME_CARD_WAIT / 1000 + 0.15
    try:
        window.present()
        pump(0.3)
        its_own = list(label.observe_controllers())
        recordreader.on_name_hover(label, Host())
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
    feedback, fixes = recordwindow.feedback, recordwindow.fixview.fixes
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

    if feedback.code_repo() is None:
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
    recordstore.parse_records([json.dumps(event), json.dumps(said)], items)
    assert [i["text"] for i in items] == ["What failed?"], items


def test_open_chat(app):
    """A notebook has no chat: one opened (record.py --chat from Baude's Open in Record, an @chat, ⌘K's) is read in a
    tab of its own, whichever notebook's open, kept open for the next start; one open already, shown; no notebook's
    made for it. One from when a notebook had its chat: that chat open, and nothing of it kept in the notebook."""
    win = app.win
    assert "chat" not in by_id(win, "a") and "folders" not in by_id(win, "a"), "a's chat: open, not a's"
    assert win.store.reading["path"] == CHAT
    select(win, "b")
    count, b = len(win.store.notebooks), win.current
    assert win.reader.path == CHAT, "the chat open, whichever notebook's"
    win.open_chat(OTHER)
    assert len(win.store.notebooks) == count and win.current is b and "chat" not in b and "folders" not in b
    assert isinstance(win.tabs.current(), recordreader.Reader) and win.reader.path == os.path.realpath(OTHER)
    assert [c["path"] for c in recordstore.Store().chats] == [os.path.realpath(CHAT), os.path.realpath(OTHER)], \
        "kept open"
    assert recordstore.Store().reading["path"] == os.path.realpath(OTHER), "kept"
    win.open_item({"kind": "chat", "path": CHAT})
    assert len(win.store.notebooks) == count and win.current is b and win.reader.path == os.path.realpath(CHAT)
    assert len([c for c in win.tabs.children() if isinstance(c, recordreader.Reader)]) == 2, "open already: shown"
    select(win, "a")
    assert win.reader.path == os.path.realpath(CHAT), "another notebook: the same chats"
    win.tabs.close(("chat", os.path.realpath(OTHER)))
    assert [c["path"] for c in win.store.chats] == [os.path.realpath(CHAT)], "closed: not kept open"


def test_images_open_in_the_large_view(app):
    """An image in Record opens in the same large view as Baude's, not the system's viewer: in the Feedback tab, going
    through that fix's images, and zooming, panning and closing just as there."""
    win = app.win
    select(win, "a")
    assert isinstance(win.lightbox, lightbox.Lightbox)
    png = Gdk.MemoryTexture.new(800, 400, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes([9, 90, 200, 255]) * (800 * 400)),
                                3200).save_to_png_bytes().get_data()
    page = win.open_fixes()
    page.draw_detail = lambda: None  # (what's in it is this test's: not what was read, or read last time)
    pictures = []
    for name in ("a.png", "b.png"):
        pic = Gtk.Picture(paintable=Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)), css_classes=["fix-shot"])
        imageviews.make_image_interactive(pic, png, name=name)
        pictures.append(pic)
    holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.clamp.set_child(holder)
    for pic in pictures:
        holder.append(pic)
    page.stack.set_visible_child_name("main")
    pump(0.2)
    opened, saved = [], imageviews.ImageActions.open
    imageviews.ImageActions.open = lambda self: opened.append(self)
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
        imageviews.ImageActions.open = saved
        win.tabs.close(("fixes",))
        pump(0.1)


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
        d = win.open_note(recordstore.HOME)
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
        for key in [k for k in list(win.tabs.pages) if k[0] == "note" and k[1] != recordstore.HOME]:
            win.tabs.close(key)
        notebook["pages"].clear()
        notebook["pages"].update(saved)
        win.open_note(recordstore.HOME).reload()


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
        d = win.open_note(recordstore.HOME)
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
        d = win.open_note(recordstore.HOME)
        hidden = d.buf.get_tag_table().lookup("hidden")
        shown = "".join(c for i, c in enumerate(d.text()) if not d.buf.get_iter_at_offset(i).has_tag(hidden))
        assert shown == "Jobs run nightly. Plans today", repr(shown)
        assert "Home:Plans" in listed() and "Home:Plans" not in notebook["pages"], "listed for its link, empty"
        m = next(recordstore.LINK_RE.finditer(d.text()))
        d.buf.place_cursor(d.buf.get_iter_at_offset(recordstore.link_name(m)[1]))
        d._key(None, record.Gdk.KEY_x, 0, 0)
        d.buf.insert_at_cursor("!")
        assert "[[+Plans]]! today" in d.text(), "typed at its last letter: beside it"
        d.buf.set_text("Jobs run nightly.")
        assert "Home:Plans" not in listed(), "the link gone: its page too"
    finally:
        notebook["pages"].clear()
        notebook["pages"].update(saved)
        win.open_note(recordstore.HOME).reload()
    recordwindow.Window.rename, rename = (lambda self, *_how: None), recordwindow.Window.rename  # (its dialog)
    try:
        before = len(store.notebooks)
        win.new_notebook()
        win.new_notebook()
        assert len(store.notebooks) == before + 1, "a blank one, not two"
        blank = win.current
        select(win, "a")
        wait_for(lambda: blank not in store.notebooks, "the blank one let go")
    finally:
        recordwindow.Window.rename = rename


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
    assert recordwindow.feedback.feedback_items()[0]["text"] == "Too tall."
    pal = pickers.RefPalette(win, space, win.open_item)
    pal.entry.set_text("relay")
    pal.fill()
    assert "relay_jobs.py" in pal.shown()
    pal.close()
    row = next(r for r in win.reader.rows if r.item["role"] == "assistant")
    assert isinstance(row.prose, markdownview.MarkdownView)
    markup = " ".join(w.get_label() for w in walk(row.prose) if isinstance(w, record.Gtk.Label))
    assert "baude-ref:relay_jobs.py" in markup and "baude-icon-file" in markup, markup[:300]
    d = win.open_note(recordstore.HOME)
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
        win.right_stack.set_visible_child_name("meta")  # (another test's may be up: the box isn't, then)
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
    d = win.open_note(recordstore.HOME)
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
    d.title.set_text(recordstore.short_page(recordstore.HOME))
    win.restore(state)
    pump(0.1)
    d = win.tabs.current()
    assert widgets_text(win.chat.composer) == words and record.widgets.caret_of(win.chat.composer) == [7, 7]
    assert record.widgets.caret_of(d.view) == [5, 9] and d.title.get_text() == "A new name" and win.get_focus() is d.view
    d.title.set_text(recordstore.short_page(recordstore.HOME))
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
    d = win.open_note(recordstore.HOME)
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
    win.tabs.show(("note", recordstore.HOME))
    pump(0.3)
    shown = settle(d.scroll, 900)
    state = json.loads(json.dumps(win.state()))
    places = {tuple(k): p for k, p in state["places"]}
    assert places[("note", recordstore.HOME)]["value"] == shown and not places[("note", recordstore.HOME)]["at_end"]
    assert places[("chat", chat)]["value"] == read, (places, read)
    assert places[("note", "Behind")]["value"] == behind, (places, behind)

    win.let_go()  # (a window made again from that, as a restart's is)
    app.app.win = new = recordwindow.Window(app.app)
    new.restore(state)
    new.present()
    win.destroy()
    try:
        page = new.tabs.get(("note", recordstore.HOME))
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
    assert panels.scroll_place(fresh) is None
    panels.hold_place(fresh, None)
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
    imageviews.make_image_interactive(pic, png, name="a.png")
    holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.clamp.set_child(holder)
    holder.append(pic)
    pump(0.3)
    page.stack.set_visible_child_name("main")  # (after what it read when it opened has said what to show)
    pump(0.3)
    assert pic.get_mapped() and pic.get_width() > 0
    box, big = win.lightbox, win.lightbox.picture
    saved, lightbox.Lightbox.FLY_MS = lightbox.Lightbox.FLY_MS, 240

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
        lightbox.Lightbox.FLY_MS = saved
        win.tabs.close(("fixes",))
        pump(0.1)


def test_nothing_jumps_as_the_notebooks_slide(app):
    """Frame by frame, in the window, as it's painted (the frame clock's after-paint: it runs offscreen too, some 30 frames
    a second): as the notebooks close and open, their toggle, the right bar's and ☰ stay put in the window's bar, and the
    first tab moves one way only, no further in a frame than the column did, from where it sits steady to where it sits
    steady. The right bar shown or hidden; its own slide, the notebooks shown or hidden, leaves all that where it is."""
    saved, panels.SLIDE_MS = panels.SLIDE_MS, 180
    win = app.win
    select(win, "a")
    win.open_note(recordstore.HOME)
    for key in [k for k in win.tabs.pages if k != ("note", recordstore.HOME)]:
        win.tabs.close(key)  # (the tabs to themselves: too many for the bar and the first is scrolled, as it narrows)
    slide, column, right = win.left_slide, win.left_slide.fixed, win.right_slide

    def place(widget):
        ok, bounds = widget.compute_bounds(win)
        assert ok
        return bounds.get_x(), bounds.get_y(), bounds.get_width()

    def frame():
        steady = win.books.get_width() if slide.pane.get_visible() else 0
        return {"column": steady if column.showing is None else column.showing,
                "buttons": [place(b) for b in (slide.toggles[0], win.right_toggle, win.menu_button)],
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
        for f in got:
            assert f["buttons"] == start["buttons"], ("the window's bar stays put", f, start)
    try:
        pump(0.3)
        for bar_shown in (True, False):  # (the right bar)
            right.set_shown(bar_shown)
            wait_for(lambda: right.anim is None, "the right bar settled")
            pump(0.15)
            shown = frame()
            closing = frames(slide, lambda: slide.set_shown(False))
            hidden = frame()
            assert not slide.pane.get_visible()
            nothing_jumps(closing, shown, hidden)
            nothing_jumps(frames(slide, lambda: slide.set_shown(True)), hidden, shown)
            assert frame() == shown and slide.pane.get_visible()

        slide.set_shown(False)  # (turned back part way: from where it's got to, in both directions)
        wait_for(lambda: column.showing is not None and column.showing < 60, "it closed some")
        back = frames(slide, lambda: slide.set_shown(True))
        assert back[-1]["tab"] == shown["tab"]
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
                assert all((f["tab"], f["buttons"]) == (steady["tab"], steady["buttons"]) for f in moved), moved
            assert abs(win.paned.get_position() - at) <= 1
    finally:
        slide.set_shown(True)
        right.set_shown(True)
        wait_for(lambda: slide.anim is None and right.anim is None, "all settled")
        panels.SLIDE_MS = saved


def test_window_bar_stays_in_sight(app):
    """The notebooks' toggle, the right bar's and ☰ are in the window's bar, over all three, as Baude's are: the right bar
    dragged as far as it goes, or the notebooks' column as wide as it goes, each is still what's under the pointer at its
    middle, where it was."""
    win = app.win
    select(win, "a")
    win.right_slide.set_shown(True)
    wait_for(lambda: win.right_slide.anim is None, "the right bar settled")
    pump(0.3)
    column = win.left_slide.fixed
    was_at, was_wide = win.paned.get_position(), column.width

    def in_sight(button):
        ok, at = button.compute_bounds(win)
        hit = win.pick(at.get_x() + at.get_width() / 2, at.get_y() + at.get_height() / 2, Gtk.PickFlags.DEFAULT)
        assert ok and button.get_mapped() and (hit is button or hit.is_ancestor(button)), (button, hit)
        return at.get_x(), at.get_y(), at.get_width()

    def places():
        return [in_sight(b) for b in (win.left_slide.toggles[0], win.right_toggle, win.menu_button)]
    try:
        was = places()
        win.paned.set_position(0)  # (the right bar as wide as it goes)
        pump(0.3)
        assert places() == was
        column.set_width(10_000)  # (the notebooks' column as wide as it goes)
        pump(0.3)
        assert places() == was
    finally:
        column.set_width(was_wide)
        win.paned.set_position(was_at)
        pump(0.2)


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
        rect = chatfind.passage_rects(label, i, i + 1, win)[0]
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
    rect = chatfind.passage_rects(first, plain, plain + 1, r.over)[0]
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
    link = recordstore.citation_link("abc-1", "msg_1", "said before it", "a (small) - passage, with [marks]")
    shown, url = recordstore.MD_LINK_RE.fullmatch(link).groups()
    assert shown == "“a (small) - passage, with marks”", shown
    assert recordstore.parse_citation(url) == {"session": "abc-1", "message": "msg_1", "prefix": "said before it", "length": 33,
                                          "start": "a (small) - passage, with [marks]", "end": ""}, url
    assert "@" not in recordstore.MD_LINK_RE.fullmatch(
        recordstore.citation_link("s", "m", "", "ask @orders-api why")).group(1)
    weird = "100% of it, 50-50: “odd” & é ☃ a=b#c?d"
    assert recordstore.passage_in([weird], recordstore.parse_citation(recordstore.MD_LINK_RE.fullmatch(
        recordstore.citation_link("s", "m", "", weird)).group(2))) == [(0, 0, len(weird))], \
        "what a link can't hold as it is"
    long = " ".join(f"w{n}" for n in range(40))
    url = recordstore.MD_LINK_RE.fullmatch(recordstore.citation_link("s", None, "", long)).group(2)
    cite = recordstore.parse_citation(url)
    assert (cite["start"], cite["end"], cite["prefix"]) == ("w0 w1 w2 w3 w4 w5 w6 w7", " ".join(f"w{n}" for n in range(32, 40)),
                                                            ""), cite
    assert recordstore.parse_citation("https://example.com") is None
    assert recordstore.parse_citation("baude-chat:../../etc?message=#:~:text=x") is None, "not a chat's id"

    texts = ["the job ran", "and \u2063the  job\nran again", "the job ran at last"]
    assert recordstore.passage_in(texts, {"start": "the job ran", "end": "", "prefix": ""}) == [(0, 0, 11)]
    assert recordstore.passage_in(texts, {"start": "the job ran", "end": "", "prefix": "again"}) == [(2, 0, 11)]
    assert recordstore.passage_in(texts, {"start": "the job ran", "end": "", "prefix": "and"}) == [(1, 5, 17)], "its logo, its spaces"
    assert recordstore.passage_in(texts, {"start": "job ran again", "end": "at last", "prefix": ""}) == [(1, 10, 23), (2, 0, 19)]
    assert recordstore.passage_in(texts, {"start": "the job ran", "end": "", "prefix": "gone"}) == [(0, 0, 11)], "its first place"
    assert recordstore.passage_in(texts, {"start": "never said", "end": "", "prefix": ""}) is None
    assert recordstore.passage_in(texts, {"start": "the job", "end": "never said", "prefix": ""}) is None
    again = ["one two three four, more words, one two three four"]
    assert recordstore.passage_in(again, {"start": "one", "end": "three four", "prefix": "", "length": 50}) == [(0, 0, 50)], \
        "its last words said twice: as long as it was"
    assert recordstore.passage_in(again, {"start": "one", "end": "three four", "prefix": "", "length": 18}) == \
        [(0, 0, 18)]


def find_all(root, kind):
    """Every widget of a kind under root."""
    out, kid = [], root.get_first_child()
    while kid is not None:
        out += ([kid] if isinstance(kid, kind) else []) + find_all(kid, kind)
        kid = kid.get_next_sibling()
    return out


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
        rect = chatfind.passage_rects(label, i, i + 1, win)[0]
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
    assert len(copied) == 1 and recordstore.CITE_SCHEME in copied[0], copied

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
    hit = recordstore.MD_LINK_RE.search(other.text())
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
    win.open_citation(recordstore.MD_LINK_RE.fullmatch(recordstore.citation_link("cited-short", "m2", "", "Short reply 2 of the chat.")).group(2))
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
    win.open_citation(recordstore.MD_LINK_RE.fullmatch(recordstore.citation_link("cited-code", "m0", "", "line_300 = 300")).group(2))
    c = win.open_chat(coded)
    wait_for(lambda: c.cited, "the passage shown", 5)
    pump(0.3)
    q = c.cited[0]
    inner = chatfind.inner_scroller(q["label"], c.box)
    assert inner is not None and inner.get_vadjustment().get_value() > 100, "the block's own view moved"
    rect = chatfind.passage_rects(q["label"], q["start"], q["end"], inner)[0]
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
        hand, start = mouse.Mouse(win), chatfind.passage_rects(labels[0], 0, 1, win)[0]
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


def test_full_width(app):
    """☰ → Full-width text, as Baude's: the chat read and the diary across the whole pane, kept for next time."""
    win = app.win
    win.open_note(recordstore.HOME)
    assert win.reader.clamp.get_maximum_size() == recordreader.Reader.WIDTH
    win.activate_action("win.fullwidth", None)
    try:
        assert win.lookup_action("fullwidth").get_state().get_boolean()
        assert win.reader.clamp.get_maximum_size() == win.diary.clamp.get_maximum_size() == 100_000
        assert recordstore.Store().full_width, "kept in records.json"
    finally:
        win.activate_action("win.fullwidth", None)
    assert win.diary.clamp.get_maximum_size() == diaryeditor.DiaryEditor.WIDTH and not recordstore.Store().full_width


def test_renamed_then_another_page_clicked(app):
    """A new page, nothing written in it, its title typed over and another page clicked in the list with real clicks: the
    page takes its new name, the page clicked is shown, and Record is still there. (Going from the title renames it, and
    that lists the pages again, under the click that's still being handled: it took the row away from under GTK.) (#83)"""
    import mouse
    if not mouse.available():
        return
    win = app.win
    select(win, "a")
    home = win.open_note(recordstore.HOME)
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
        row = next(r for r in listed_rows(win.pages.index) if r.page == recordstore.HOME)
        ok, p = row.compute_point(win, widgets.Graphene.Point().init(30, 10))
        hand.down(p.x, p.y)
        hand.up(p.x, p.y)
        pump(0.5)
        assert win.tabs.current() is home, win.tabs.current()
        assert win.tabs.get(("note", "Renamed page")) is d and win.tabs.get(("note", "Brand new")) is None
        assert d.page == "Renamed page" and "Renamed page" in win.current["pages"] and "Brand new" not in win.current["pages"]
        assert [r.page for r in listed_rows(win.pages.index)] == list(win.pages.names()[0])
        assert win.pages.index.get_selected_row().page == recordstore.HOME, "the one clicked, selected"
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
    assert win.current["name"] == "Monorepo notes" and recordstore.full_name(win.current) == "Monorepo notes"


def settings_opened(win):
    """Settings, opened as ☰ does: nothing in Python has hold of it till it's asked for here."""
    win.activate_action("win.settings", None)
    wait_for(lambda: isinstance(win.get_visible_dialog(), recordwindow.Settings), "Settings")
    return win.get_visible_dialog()


def export_chosen(win, i):
    """Settings opened, if it isn't, and its i-th export clicked: what's over the window then."""
    open_now = win.get_visible_dialog()
    row = (open_now if isinstance(open_now, recordwindow.Settings) else settings_opened(win)).export_rows[i]
    pump(0.2)
    row.get_parent().emit("row-activated", row)
    pump(0.2)
    return win.get_visible_dialog()


def forget_zip_sizes():
    with recordui._ZIP_SIZES:
        recordui._ZIPPED.clear()


def cite(*paths):
    """Chats cited, as a passage's citation has them (Reader.citation): what makes them a notebook's (chats_of)."""
    return " ".join(recordstore.CITE_SCHEME + os.path.basename(p)[:-len(".jsonl")] for p in paths)


def cite_chat(win, path=CHAT):
    """A chat cited on the open notebook's Home, so its export has it: Home as it was, for home_back."""
    home = win.current["pages"].get(recordstore.HOME, "")
    win.current["pages"][recordstore.HOME] = f"{home}\n\n{cite(path)}"
    return home


def home_back(win, home):
    """The open notebook's Home as it was (cite_chat), kept."""
    win.current["pages"][recordstore.HOME] = home
    win.store.save_now()


def test_export_sizes_are_the_zips(app):
    """What an export's said to come to, both ways, is what export_zip makes, to the byte, with nothing written. A chat's read
    once, whichever notebooks have it, and again only when its transcript has changed. (#124)"""
    out = os.path.join(TMP, "sizes")
    os.makedirs(out, exist_ok=True)
    named = transcript("chat-sizes", ["Ünï wörds " * 300])
    with open(named, "a") as f:
        f.write(json.dumps({"type": "custom-title", "customTitle": "Ça va, a/b"}) + "\n")
    book = {"id": "x1", "name": "Shöp notes", "pages": {recordstore.HOME: "Home words " * 500 + cite(CHAT, named), "Plans:Nëxt": "Next", "Empty": " "},
            "messages": [{"role": "user", "text": "What is it?", "quote": "a passage"}, {"role": "record", "text": "A thing."}]}
    other = {"id": "x2", "name": "Elsewhere", "pages": {recordstore.HOME: "Other words " + cite(CHAT)}, "messages": []}
    nothing = {"id": "x3", "name": "Nothing", "pages": {}, "messages": []}

    def made(notebooks, extras):
        path = os.path.join(out, "made.zip")
        recordui.export_zip(path, notebooks, extras)
        return os.path.getsize(path)
    forget_zip_sizes()
    reads, read = [], recordui.chat_text

    def counted(*a):
        reads.append(a[0])
        return read(*a)
    recordui.chat_text = counted
    try:
        def data():
            return sorted((os.path.join(d, f), os.stat(os.path.join(d, f)).st_mtime_ns)
                          for d, _, files in os.walk(os.environ["RECORD_DATA"]) for f in files)
        before, zipping = data(), recordui.zipfile.ZipFile
        recordui.zipfile.ZipFile = None  # (it makes no zip to see how big it is)
        try:
            both = recordui.zip_sizes([book, other])
        finally:
            recordui.zipfile.ZipFile = zipping
        assert data() == before and not os.listdir(out), "nothing written"
        stopped = threading.Event()
        stopped.set()
        assert recordui.zip_sizes([book, other], stopped) is None, "not wanted any more"
        assert named in reads and CHAT in reads and len(reads) == len(set(reads)), reads
        first = list(reads)
        assert recordui.zip_sizes([book, other]) == both and recordui.zip_sizes([other]) and reads == first, "none read again"
        with open(named, "a") as f:
            f.write(json.dumps({"type": "assistant", "cwd": REPO, "uuid": "a9", "message": {
                "id": "m9", "role": "assistant", "content": [{"type": "text", "text": "More, and more words."}]}}) + "\n")
        del reads[:]
        changed = recordui.zip_sizes([book, other])
        assert reads == [named], reads
        assert changed[0] == both[0] and changed[1] > both[1], (both, changed)
        recordui.chat_text = read
        for notebooks in ([book, other], [book], [other], [nothing], []):
            assert recordui.zip_sizes(notebooks) == (made(notebooks, False), made(notebooks, True)), notebooks
    finally:
        recordui.chat_text = read
        os.remove(named)
    assert [recordui.size_said(n) for n in (412, 3_400, 12_340, 999_600, 3_400_000, 9_960_000, 177_000_000, 1_500_000_000)] == [
        "412 bytes", "3.4 KB", "12 KB", "1.0 MB", "3.4 MB", "10 MB", "177 MB", "1.5 GB"]


def test_export_to_a_zip(app):
    """Settings → Export Notebook… and Export All…: the notes and the conversation in a zip, as Markdown; with the option, the
    glossary and what's shown of the chats a notebook has to do with, and none it hasn't. Every 10 minutes all of them go to the
    one zip in a .local folder if they've changed, in place of the last. Nothing's left half-made. (#86)"""
    import zipfile
    win = app.win
    select(win, "a")
    out = os.path.join(TMP, "exports")
    os.makedirs(out, exist_ok=True)
    home, tools = None, os.path.join(CHATS, "tools-1.jsonl")
    book = {"id": "x1", "name": "Shop notes", "pages": {recordstore.HOME: "Home words " + cite(CHAT, tools), "Plans:Next": "Next words", "Empty": " "},
            "messages": [{"role": "user", "text": "What is it?", "quote": "a passage"}, {"role": "record", "text": "A thing."}]}
    other = {"id": "x2", "name": "Elsewhere", "pages": {recordstore.HOME: "Other words"}, "messages": []}
    with open(tools, "w") as f:  # (what's not shown of a chat stays out)
        for d in ({"type": "user", "cwd": REPO, "uuid": "u1", "message": {"role": "user", "content": "Run it"}},
                  {"type": "assistant", "cwd": REPO, "uuid": "a1", "message": {"id": "m1", "role": "assistant", "content": [
                      {"type": "tool_use", "name": "Bash", "input": {"command": "secret-command"}}, {"type": "text", "text": "It ran."}]}},
                  {"type": "user", "cwd": REPO, "uuid": "u2", "toolUseResult": {}, "message": {"role": "user", "content": [
                      {"type": "tool_result", "content": "secret-output"}]}}):
            f.write(json.dumps(d) + "\n")
    try:
        # the notes alone
        path = os.path.join(out, "plain.zip")
        recordui.export_zip(path, [book, other])
        with zipfile.ZipFile(path) as z:
            names = sorted(z.namelist())
            assert names == ["conversations/Shop notes.md", "notes/Elsewhere/Home.md", "notes/Shop notes/Home.md",
                             "notes/Shop notes/Plans/Next.md"], names
            assert z.read("notes/Shop notes/Plans/Next.md").decode() == "Next words"
            said = z.read("conversations/Shop notes.md").decode()
            assert "What is it?" in said and "> a passage\n" in said and "A thing." in said
        two = {**book, "messages": [{"role": "user", "text": "So?", "quote": "one\ntwo"}]}
        assert "> one\n> two\n" in recordui.conversation_text(two), recordui.conversation_text(two)
        # no two notebooks in one folder, even one named as the other's would be
        same = [{"id": "a1", "name": "Foo", "pages": {}}, {"id": "b1", "name": "Foo", "pages": {}}, {"id": "c1", "name": "Foo (a1)", "pages": {}}]
        folders = recordstore.book_folders(same)
        assert len(set(folders.values())) == 3 and folders["b1"] == "Foo (b1)", folders
        # with the glossary and chats: those its notes cite and their folders' glossary, not the other's
        path = os.path.join(out, "all.zip")
        recordui.export_zip(path, [book, other], extras=True)
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
        for words in (f"[“a”]({recordstore.CITE_SCHEME}{session}?message=#:~:text=a)", f"[[@{recordstore.session_info(CHAT)[0]}]]"):
            cited = {**other, "pages": {recordstore.HOME: words}}
            found = recordui.chats_of(cited, [(CHAT, *recordstore.session_info(CHAT))])
            assert [c[0] for c in found] == [CHAT], words
        assert not recordui.chats_of(other, [(CHAT, *recordstore.session_info(CHAT))]), "nothing to do with it"
        # one that can't be made leaves what was there, and nothing beside it
        def broken(*_a):
            raise OSError("no")
        was, recordui.conversation_text = recordui.conversation_text, broken
        try:
            with open(path, "rb") as f:
                before = f.read()
            try:
                recordui.export_zip(path, [book])
                assert False, "it should have failed"
            except OSError:
                pass
            with open(path, "rb") as f:
                assert f.read() == before and not os.path.exists(path + ".tmp")
        finally:
            recordui.conversation_text = was
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
            if isinstance(win.get_visible_dialog(), recordwindow.Settings):
                win.get_visible_dialog().close()
            wait_for(lambda: win.get_visible_dialog() is None, "Settings closed")
        # one that fails says so, whatever it was: it isn't taken for done
        said = []
        made, recordwindow.export_zip = recordui.export_zip, lambda *_a: (_ for _ in ()).throw(MemoryError())
        try:
            win.export([win.current], os.path.join(out, "never.zip"), False, said.append)
            wait_for(lambda: said, "said", 5)
        finally:
            recordwindow.export_zip = made
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
        rolling = recordui.backup_zip()
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
        real, win.store.notebooks = win.store.notebooks, [{"id": "n", "name": "New", "pages": {recordstore.HOME: ""}, "messages": []}]
        try:
            win.store.unzipped = True
            win.roll_backup()
            pump(0.3)
            assert os.stat(rolling).st_mtime_ns == kept and not win.zipping, "nothing written anywhere: left as it is"
        finally:
            win.store.notebooks = real
        # with the glossary and chats, as Settings says, whether or not the notebooks have changed; kept
        select(win, "a")
        home = cite_chat(win)
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
        if home is not None:
            home_back(win, home)


def test_unexplained_nouns():
    """Read's red: nouns.py reads a reply's prose (no code blocks, no markup, a block at a time), your notes' To learn
    list isn't what you know, and a reply's phrases the detector flagged are drawn red, but not in code (a code span,
    a link: set aside first). (NLTK itself isn't run here: it's fetched by uv.)"""
    import nouns
    md = ("## The platform\n\nThe **platform** runs [the job](https://x.y/z) and `build_app`.\n\n"
          "```\nthe code isn't prose\n```\n- an item\n- [ ] a box\n\n| col | the cell |\n|---|---|\n> a quote")
    assert nouns.prose_blocks(md) == ["The platform", "The platform runs the job and build_app.", "an item", "a box",
                                      "col", "the cell", "a quote"], nouns.prose_blocks(md)
    assert recordui.nouns_command() is None, "RECORD_NOUNS=off: not run"
    page = "- platform: where apps run\n\n## To learn\n- backend\n\n## Later\n- more"
    assert recordstore.without_to_learn(page) == "- platform: where apps run\n\n## Later\n- more"
    names = recordui.ReaderNames(type("NoNames", (), {"items": lambda self: []})())
    reply = "Then the platform's backend restarts `the platform`, as the platform does."
    names.said = {markdownview.message_key(reply):
                  re.compile(r"(?<![\w-])(?:the platform's backend|the platform)(?![\w-])")}
    was = markdownview.SIGNAL
    markdownview.SIGNAL = names
    try:
        markup = markdownview.markdown_blocks(reply)[0][1]
    finally:
        markdownview.SIGNAL = was
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
    real = recordstore.desktop_sessions_dir
    recordstore.desktop_sessions_dir = lambda: desk
    try:
        names = [os.path.basename(p) for p in recordstore.chat_transcripts() if os.path.dirname(p) == folder]
        assert sorted(names) == sorted([ids["new"] + ".jsonl", ids["other"] + ".jsonl"]), names
        listed = [s["title"] for s in recordstore.recent_sessions() if os.path.dirname(s["path"]) == folder]
        assert listed == ["Other chat", "Pages planning"], listed  # (newest first)
        chats = recordui.ChatNames()
        chats._read()
        mine = [c for c in chats.chats if os.path.dirname(c["path"]) == folder]
        assert [os.path.basename(c["path"]) for c in mine] == [ids["other"] + ".jsonl", ids["new"] + ".jsonl"], mine
        with open(os.path.join(desk, "account", "org", "local_x.json"), "w") as f:
            f.write("{not json")  # (one the app's halfway through writing: nothing left behind, for now)
        assert ids["old"] + ".jsonl" in [os.path.basename(p) for p in recordstore.chat_transcripts()]
    finally:
        recordstore.desktop_sessions_dir = real
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
