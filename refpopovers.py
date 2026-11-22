"""refpopovers — Signal's views: where a ref is (ref_location_text, the chat's folders and checkouts), its popovers
(AnchoredPopover, LookupPopover, RefPopover), ⌘K (SearchSpace, RefPalette, palette_open) and in_chat.
"""
import html
import json
import os
import re
import time

from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from lookup import REF_KINDS, ref_icon
from lookupkube import kube_running
from lookupgit import _git

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

import fixview  # noqa: E402  (☰ → Ongoing Feedback: the fixes, approved or rejected)
import pickers  # noqa: E402
from widgets import RefIcon  # noqa: E402,F401
from widgets import Spinner  # noqa: E402,F401
from widgets import discard, time_ago, time_tip  # noqa: E402,F401
from pickers import RefPaletteRow  # noqa: E402,F401
from kubeview import KUBE_POOL  # noqa: E402,F401

from signalrefs import KUBE_WORKLOADS, kube_page_for  # noqa: E402
from sessionlist import git_checkout, host_key, remote_name  # noqa: E402
from baudemeta import MetaNames, all_chats  # noqa: E402


# ------------------------------------------------------- signal views --

def ref_location_text(loc):
    where = f"{loc['path']}:{loc['line']}" if loc.get("line") else loc["path"]
    return f"{loc['role']} · {where}" + (f"  ({loc['note']})" if loc.get("note") else "")


_FOLDERS = {}
_REF_WHERE = re.compile(rb'"(cwd|file_path|notebook_path|command|name)":[ \t\r\f\v]*"((?:[^"\\\n]|\\.)*)"')  # (in a line)
_REF_CD = re.compile(r"(?:^|&&|;|\|\||\n|\(|\{|\bthen\b|\bdo\b)\s*(?:cd|pushd)\s+(\"[^\"]+\"|'[^']+'|(?:\\ |[^\s;&|()])+)")
_REF_GIT_C = re.compile(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|(?:\\ |\S)+)\s+(?:commit|push|pull|merge|rebase|checkout|switch|"
                        r"cherry-pick|reset|revert|add|rm|mv|stash|tag|am|apply|restore)\b")
_REF_WRITERS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def transcript_folders(path):
    """Where a chat worked, the most recent first, each folder once: (folder, acted). The folder each message ran in
    (acted False: prompts are recorded there whatever the chat does), and, acted, the folders its commands cd into or
    change with git -C (cd ../wt && ..., git -C ../wt commit), and the folders of the files it edits or writes. Reading
    a file somewhere isn't working there. A worktree beside the repository is outside the project, so Claude Code puts
    the shell back after each command there, and only the commands and edits say the chat worked in it. Read from the
    transcript's end back, only as far as they're asked for: chat_checkout wants the latest that says something, mostly
    in the last few hundred KB (read whole, a 50 to 85 MB chat took 0.3 to 1.5 s of opening it). Kept per transcript, and
    as it grows only what it grew by is read (this runs for each line another program appends)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            trail = _folder_trail(path, f, size)
            i = 0
            while True:
                while i < len(trail["keys"]):
                    yield trail["keys"][i]
                    i += 1
                if not _trail_take(trail, f):
                    return
    except OSError:
        return


def _folder_events(data, cwd=..., writing=...):
    """What transcript_folders reads in whole transcript lines (bytes), in order, from the state the lines before them
    leave: cwd, the folder the last message ran in (None before any); writing, whether the call whose input comes next
    writes files; `...` for either where those lines aren't read yet. ([(folder, acted)], cwd, writing): the state they
    leave, `...` still where they don't say. One that hangs on a state not read is (target, "cd") (a cd into a relative
    folder, before their first message's folder) or (folder, "write") (a file's, before their first call: written if the
    call before them writes). Folders as found: _trail_folders normalizes them, and drops relative ones."""
    events = []
    for m in _REF_WHERE.finditer(data):
        try:
            value = json.loads('"' + m[2].decode("utf-8", "replace") + '"')
        except ValueError:
            continue
        if m[1] == b"name":
            writing = value in _REF_WRITERS  # (the tool whose input follows)
        elif m[1] == b"cwd":
            cwd = value
            events.append((value, False))
        elif m[1] == b"command":
            for t in _REF_CD.findall(value) + _REF_GIT_C.findall(value):
                d = os.path.expanduser(t.strip("\"'").replace("\\ ", " "))
                if os.path.isabs(d):
                    events.append((d, True))
                elif cwd is ...:
                    events.append((d, "cd"))
                elif cwd:
                    events.append((os.path.join(cwd, d), True))
        elif writing is not False and os.path.isabs(value):
            events.append((os.path.dirname(value), True if writing is True else "write"))
            writing = False
    return events, cwd, writing


def _folder_trail(path, f, size):
    """transcript_folders' memory of a transcript: "keys", its folders found so far, newest first; "seen", them as a set;
    "blocks", what's read of it, newest first, each [start, events (_folder_events; None once in keys), cwd, writing];
    the first "taken" of them in keys. The lines added since it was last looked at are read now (a line still being
    written isn't), and their folders go first; one rewritten (shorter, or its end not what was read) is read again."""
    trail = _FOLDERS.get(path)
    if trail is not None and trail["size"] == size:
        return trail
    end, at, step = 0, size, 1 << 16
    while at > 0:  # (the whole lines end at the last newline)
        start = max(0, at - step)
        f.seek(start)
        if (nl := f.read(at - start).rfind(b"\n")) >= 0:
            end = start + nl + 1
            break
        at, step = start, step * 2
    if trail is not None:
        f.seek(trail["last"][0])
        if end < trail["end"] or hash(f.read(trail["end"] - trail["last"][0])) != trail["last"][1]:
            trail = None
    if trail is None:
        trail = _FOLDERS[path] = {"end": end, "keys": [], "seen": set(), "blocks": [], "taken": 0}
    elif end > trail["end"]:
        f.seek(trail["end"])
        trail["blocks"].insert(0, [trail["end"], *_folder_events(f.read(end - trail["end"]))])
        trail["taken"] += 1
        fresh = _trail_folders(trail, f, 0)
        trail["seen"].update(fresh)
        fresh_set = set(fresh)
        trail["keys"] = fresh + [k for k in trail["keys"] if k not in fresh_set]
    back = max(0, end - 4096)
    f.seek(back)
    trail.update(size=size, end=end, last=(back, hash(f.read(end - back))))  # (to tell it's the file read, next time)
    return trail


def _trail_read(trail, f):
    """The lines of a trail's transcript before the earliest read, parsed: 256 KB of them, twice as many each time
    after, to 8 MB (a line longer than that, all of it). False at its start."""
    blocks = trail["blocks"]
    at = blocks[-1][0] if blocks else trail["end"]
    size = (1 << 18) << min(len(blocks), 5)
    while at > 0:
        start = max(0, at - size)
        f.seek(start)
        data = f.read(at - start)
        nl = data.find(b"\n") if start else -1  # (the line it begins in: part of the block before)
        if start and not 0 <= nl < len(data) - 1:
            size *= 2
            continue
        blocks.append([start + nl + 1, *_folder_events(data[nl + 1:])])
        return True
    return False


def _trail_state(trail, f, i):
    """(cwd, writing) where a trail's block i begins: as the blocks before it leave them, read back as far as it takes."""
    blocks, cwd, writing = trail["blocks"], ..., ...
    while cwd is ... or writing is ...:
        if blocks[i][0] == 0:  # (the transcript's start: none yet)
            return None if cwd is ... else cwd, False if writing is ... else writing
        i += 1
        if i == len(blocks):
            _trail_read(trail, f)
        cwd = blocks[i][2] if cwd is ... else cwd
        writing = blocks[i][3] if writing is ... else writing
    return cwd, writing


def _trail_folders(trail, f, i):
    """A trail's block i as its folders, newest first, each once: what hangs on the lines before it, as they say."""
    events, trail["blocks"][i][1] = trail["blocks"][i][1], None
    if any(acted in ("cd", "write") for _d, acted in events):
        cwd, writing = _trail_state(trail, f, i)
    out, seen = [], set()
    for d, acted in reversed(events):
        if acted == "cd":
            d, acted = os.path.join(cwd, d) if cwd else None, True
        elif acted == "write":
            if not writing:
                continue
            acted = True
        if d and os.path.isabs(d):  # (a folder acted in and one only run in are two entries, each where it was last)
            key = (os.path.normpath(d), acted)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _trail_take(trail, f):
    """The next block back of a trail's transcript into its keys (read now if it isn't yet). False at its start."""
    if trail["taken"] == len(trail["blocks"]) and not _trail_read(trail, f):
        return False
    for key in _trail_folders(trail, f, trail["taken"]):
        if key not in trail["seen"]:
            trail["seen"].add(key)
            trail["keys"].append(key)
    trail["taken"] += 1
    return True


_TREES = {}


def git_trees(common):
    """A repository's checkouts (its main one and its worktrees), by its common git folder: their top folders, read
    again when a worktree is added or removed (its worktrees folder changes), or after 30 s."""
    try:
        stamp = os.stat(os.path.join(common, "worktrees")).st_mtime_ns
    except OSError:
        stamp = 0
    hit = _TREES.get(common)
    if hit is None or hit[1] != stamp or time.time() - hit[0] > 30:
        out = _git(os.path.dirname(common) if os.path.basename(common) == ".git" else common,
                   "worktree", "list", "--porcelain") or ""
        hit = _TREES[common] = (time.time(), stamp, [os.path.realpath(l[9:]) for l in out.splitlines()
                                                     if l.startswith("worktree ")])
    return hit[2]


def chat_checkout(folder, transcript):
    """The checkout a chat works in, when it isn't its folder's: the latest other checkout of the same repository
    (a worktree) it worked in (transcript_folders: where its messages ran, its commands went, the files it wrote), if
    it's still there. Else None. Its own folder counts against it only where the chat acted there (a cd, an edit):
    prompts are recorded there, and the shell goes back there between commands."""
    here = git_checkout(os.path.realpath(folder)) if transcript else None
    if here is None:
        return None
    trees = sorted(git_trees(here[1]), key=len, reverse=True)  # (the deepest first: .claude/worktrees/x in its repo)
    for d, acted in transcript_folders(transcript):  # (the latest first)
        real = os.path.realpath(d)
        tree = next((t for t in trees if real == t or real.startswith(t + os.sep)), None)
        if tree == here[0] and acted:
            return None  # (it worked in its own checkout since)
        if tree is not None and tree != here[0] and os.path.isdir(tree):
            return tree
    return None


_REF_TABLE_OF = re.compile(r"""(?:\bTable\(|create_table\(|add_column\(|alter_column\(|drop_column\(|__tablename__\s*=)\s*["'](\w+)["']"""
                           r"""|\b(?:CREATE|ALTER)\s+TABLE\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?["`]?(?:\w+\.)?(\w+)""", re.I)


def ref_column_table(path, line, back=300):
    """The table a column's definition belongs to: the nearest sa.Table("t", / create_table("t", /
    add_column("t", / __tablename__ = "t" / CREATE or ALTER TABLE t at or above its line. None if none."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read(4 * 1024 * 1024).splitlines()
    except OSError:
        return None
    for i in range(min(len(lines), line or 1) - 1, max(-1, (line or 1) - 1 - back), -1):
        if m := _REF_TABLE_OF.search(lines[i]):
            return m.group(1) or m.group(2)
        if re.search(r"\b(?:Table|create_table|add_column|alter_column|drop_column)\(\s*$", lines[i]) and \
                i + 1 < len(lines) and (m := re.match(r"""\s*["'](\w+)["']""", lines[i + 1])):
            return m.group(1)  # (sa.Table(\n    "svc_job",)
    return None


def ref_busy(ref):
    """Whether Signal is working: a lookup, replies waiting, or Sonnet with a batch or replies to come."""
    return bool(ref.activity or ref.waiting or (ref.agent is not None and ref.agent.busy()))


def ref_late(ref):
    """How long after its message a name was looked up, as its status says it; None if that's not worth saying."""
    return time_ago(ref.written_at).replace(" ago", "") if ref.after_fact and ref.written_at else None


def ref_status_tip(label, ref):
    """A status line (ref_status_text): when the message was written, with the pointer on how long after."""
    late = ref_late(ref)
    return time_tip(label, ref.written_at if late else None, late)


def ref_status_text(ref):
    """The status line of a popover or card."""
    if ref.status == "pending":
        return wording.say("lookup.looking_it_up")
    if ref.status == "not_found":
        return wording.say("signal.status_not_found", reason=ref.reason)
    if ref.status == "stale":
        return wording.say("signal.status_stale")
    if ref.status == "ambiguous":
        return wording.say("signal.status_matches", n=len(ref.locations), why=ref.reason)
    note = ""
    if late := ref_late(ref):
        note = f" Looked up {late} after the message was written, so the line is the one there now."
    return ref.summary() + "." * (not ref.summary().endswith((".", ")"))) + note


def ref_location_buttons(win, ref, box, limit=None, on_open=None):
    """A row per location: click to open it where it is now; a stale one can also be opened as it was."""
    locs = ref.locations if limit is None else ref.locations[:limit]
    for i, loc in enumerate(locs):
        row = Gtk.Box(spacing=4)
        btn = Gtk.Button(hexpand=True, tooltip_text=os.path.join(win.signal.root, loc["path"]))
        btn.add_css_class("flat")
        btn.add_css_class("ref-loc")
        lbl = Gtk.Label(label=ref_location_text(loc), xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        btn.set_child(lbl)
        btn.connect("clicked", lambda *_, i=i: win.open_location(ref, i) and on_open and on_open())
        row.append(btn)
        if ref.status == "stale" and loc.get("blob"):
            was = Gtk.Button(label="As it was", tooltip_text="Open the version that was read when it was looked up")
            was.add_css_class("flat")
            was.connect("clicked", lambda *_, i=i: (win.open_as_it_was(ref, i), on_open and on_open()))
            row.append(was)
        box.append(row)
    return len(ref.locations) - len(locs)


def ref_run_names(ref):
    """The names a link's thing could be running under in a cluster: a service's, a workload's (a Deployment the
    charts define), a folder's (services/web-svc: web-svc). Only names Kubernetes allows."""
    if ref.status not in ("resolved", "ambiguous") or ref.data.get("context") or ref.data.get("db"):
        return []
    if ref.kind == "k8s" and ref.data.get("kind") not in KUBE_WORKLOADS + ("Service",):
        return []
    names = [ref.data.get("name"), ref.surface, ref.canonical] if ref.kind in ("k8s", "service") else []
    names += [os.path.basename(l["path"].rstrip("/")) for l in ref.locations if l["role"] == "folder"]
    return list(dict.fromkeys(n for n in names if n and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", n)))


def kube_running_rows(win, names, box, on_open=None):
    """Where a link's thing runs in your local clusters (kube_running, asked in the background), added to box once
    it's known; nothing if it runs nowhere. A row per workload, which opens it in the Kubernetes view."""
    holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, visible=False)
    box.append(holder)

    def show(found):
        if not found or holder.get_root() is None:
            return False
        heading = Gtk.Label(label=wording.say("signal.running"), xalign=0)
        heading.add_css_class("ref-kind")
        holder.append(heading)
        for r in found:
            btn = Gtk.Button(tooltip_text=f"Open it in the Kubernetes view: {r['context']}, namespace {r['namespace']}")
            btn.add_css_class("flat")
            btn.add_css_class("ref-loc")
            line = Gtk.Box(spacing=6)
            dot = Gtk.Label(label="●")
            dot.add_css_class("success" if r["ok"] else "warning")
            line.append(dot)
            line.append(time_tip(Gtk.Label(label=f"{r['kind']} {r['name']} · {r['context']} · {r['namespace']} · {r['state']}",
                                           xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE),
                                 r.get("at"), r["state"]))
            btn.set_child(line)
            btn.connect("clicked", lambda *_, r=r: (on_open and on_open(), win.kube.open(
                context=r["context"], page=kube_page_for(r["resource"]), search=r["name"], namespace=r["namespace"])))
            holder.append(btn)
        holder.set_visible(True)
        if isinstance(pop := holder.get_ancestor(Gtk.Popover), AnchoredPopover):
            pop.refit()  # (taller now, after it was shown)
        return False

    def work():
        try:
            found = kube_running(names)
        except Exception:  # noqa: BLE001 (kubectl gone: nothing to say)
            found = []
        GLib.idle_add(show, found)
    KUBE_POOL.submit(work)
    return holder


def ref_known_text(win, surface):
    """What else a name is, when the name catalogue has it from more than one kind of source (a branch and a table
    both called it, say): "Known as: Branch · Database table (relay, postgres)". None otherwise."""
    sources = win.signal.known_from(surface) if getattr(win, "signal", None) is not None else []
    if len({what for what, _where in sources}) < 2:
        return None
    return "Known as: " + " · ".join(what + (f" ({where})" if where else "") for what, where in sources)


def ref_kind_label(ref):
    """What a name is, as a popover's title says it: nothing while it's being looked up (it may be anything),
    a database table or column when that's what the code defines, else its kind."""
    if ref.status == "pending":
        return None
    if (live := ref.data.get("db")):  # (a running database's: no code defines it)
        if not live.get("table"):
            return wording.say("databases.database")
        return "Database collection" if live.get("engine") == "ArangoDB" else wording.say("databases.database_table")
    roles = [l["role"] for l in ref.locations if l["role"] != "used"]
    if ref.kind == "symbol" and roles and all(r in ("table", "column") for r in roles):
        return wording.say("signal.database_column") if all(r == "column" for r in roles) \
            else wording.say("databases.database_table")
    if ref.data.get("context"):
        return wording.say("lookup.kubeconfig_cluster") if ref.data.get("cluster_only") else \
            wording.say("lookup.kubeconfig_context")
    if ref.data.get("issue"):  # (GitHub's list says so: a # in a reply may be either)
        return wording.say("lookup.issue")
    return REF_KINDS.get(ref.kind, ("Name",))[0]


def name_markup(name):
    """A title's name: <tt>name</tt>."""
    return f"<tt>{html.escape(name)}</tt>"


class AnchoredPopover(Gtk.Popover):
    """A popover pointing into a reply's label; it goes when the label does (a new session)."""

    def __init__(self):
        super().__init__()
        self.add_css_class("ref-popover")
        self.anchor_handler, self.is_closed = None, False
        self.connect("closed", self._on_closed)

    def _on_closed(self, *_):
        self.is_closed = True
        GLib.idle_add(self.detach)

    def attach(self, anchor, rect=None):
        """Point at rect in anchor (a reply's label); leave with it if it goes (a new session)."""
        self.set_parent(anchor)
        if rect is not None:
            self.set_pointing_to(rect)
        self.anchor_handler = anchor.connect("unrealize", lambda *_: self.detach())

    def detach(self):
        anchor = self.get_parent()
        if anchor is not None:
            if self.anchor_handler:
                anchor.disconnect(self.anchor_handler)
                self.anchor_handler = None
            self.unparent()
        return False

    def refit(self):
        """What it shows changed while it's open: shown again at its new size. GTK closes a popover whose content
        outgrows the size it was shown at unless the widget holding it presents it again as it's allocated, which a
        label (what these point into) only does for its own menu."""
        if self.get_visible():
            self.present()


class LookupPopover(AnchoredPopover):
    """A click on a name whose place isn't known yet: what's happening ("Looking up…", "Finding it in the running
    databases…"), then what was found, with buttons to open it, pick one of several, or try again."""

    def __init__(self, name, kind=None):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4, margin_bottom=4,
                      margin_start=4, margin_end=4, width_request=320)
        self.title = Gtk.Label(xalign=0, wrap=True, max_width_chars=60, wrap_mode=Pango.WrapMode.WORD_CHAR)
        box.append(self.title)
        line = Gtk.Box(spacing=6)
        self.spinner = Spinner(visible=False)
        line.append(self.spinner)
        self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=60, hexpand=True)
        self.status.add_css_class("dim-label")
        line.append(self.status)
        box.append(line)
        self.choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self.choices)
        self.actions = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        box.append(self.actions)
        self.set_child(box)
        self.name = name
        self.set_title(kind)

    def set_title(self, kind=None):
        self.title.set_markup((f'<span alpha="65%">{html.escape(kind)}</span>  ' if kind else "") +
                              name_markup(self.name))

    def state(self, text, busy=False, choices=(), actions=()):
        """Say text (with a spinner while busy); choices: [(label, tooltip, fn)] as a list of places, actions:
        [(label, fn)] as buttons, the first the default."""
        self.status.set_label(text)
        self.spinner.set_visible(busy)
        for box in (self.choices, self.actions):
            while (c := box.get_first_child()) is not None:
                box.remove(c)
                discard(c)
        for label, tip, fn in choices:
            btn = Gtk.Button(tooltip_text=tip or None)
            btn.add_css_class("flat")
            btn.add_css_class("ref-loc")
            btn.set_child(Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE))
            btn.connect("clicked", lambda *_, fn=fn: (self.popdown(), fn()))
            self.choices.append(btn)
        for i, (label, fn) in enumerate(actions):
            btn = Gtk.Button(label=label)
            if i == 0:
                btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda *_, fn=fn: fn())
            self.actions.append(btn)
        if (first := self.actions.get_first_child()) is not None:
            self.set_default_widget(first)
        self.refit()  # (choices and buttons where there was a spinner: bigger)


class RefPalette(pickers.RefPalette):
    """⌘K in Baude: what @ lists (MetaNames): every host's chats, and the names the open chat's folder has, made on its
    host (this machine's Signal's, or fetched from the host it runs on, listed as they come). Opened: palette_open."""

    def __init__(self, win):
        super().__init__(win, MetaNames(win), lambda item: palette_open(win, item))


def palette_open(win, item):
    """A ⌘K row opened: a name of this machine's as its link is (its open, ref_palette_items); a chat, as the sidebar
    opens it (the open one: its tab shown); another host's pull request or issue on GitHub, its file (or a manifest's
    object, at its manifest) read from it and shown in a tab, and anything else of it said, as Baude's views of
    branches, clusters and databases look at this machine."""
    if (opened := item.get("open")) is not None:
        return opened(win)
    if item.get("kind") == "chat":
        info = next((i for i in all_chats(win.sidebar) if i["id"] == item.get("id")), None)
        if info is None:
            return None
        if info["id"] == win.session_id:
            return win.tabs.show("chat")
        if info.get("remote") is not None:
            return win.open_remote_soon(info)
        win.opening_remote = None  # (another host's, still being fetched: not opened after all, as the sidebar's)
        return win.switch_to(lambda: win.resume_session(info))
    entry = item.get("entry") or {}
    if (listed := entry.get("github")) is not None:
        return fixview.open_github(win, listed)
    r, host = win.remote, item.get("host")
    on = remote_name(r) if r is not None and host == host_key(r) else "another host"
    first = (entry.get("sources") or [["", ""]])[0]
    manifest = item.get("kind") == "k8s" and entry.get("hint") != "context" and \
        not str(first[0]).endswith(" running") and first[1]  # (an object written in a file there, not one running)
    if (item.get("kind") == "file" or manifest) and item.get("host_path") and on != "another host":
        return win.show_remote_file(r, item["host_path"], item.get("host_line"))
    return win.ref_toast(f"{item['surface']}: {(item.get('what') or 'a name').lower()} on {on}")


class RefPopover(AnchoredPopover):
    """What a Signal link points at: its places, as ⌘K's rows (click one to open it), why it wasn't found, and at
    the top right, icons to search again and to open its card (and its cluster, or its schema)."""

    ROWS = 12  # places shown before the table scrolls

    def __init__(self, win, ref):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4, margin_bottom=4,
                      margin_start=4, margin_end=4, width_request=520 if ref.locations else 320)
        head = Gtk.Box(spacing=2)
        title = Gtk.Label(xalign=0, wrap=True, max_width_chars=60, wrap_mode=Pango.WrapMode.WORD_CHAR, hexpand=True)
        kind = ref_kind_label(ref)
        title.set_markup((f'<span alpha="65%">{html.escape(kind)}</span>  ' if kind else "") +
                         name_markup(ref.canonical))  # (not selectable: it would open selected)
        self.title = title
        head.append(title)

        def icon_button(child, tip, fn):
            btn = Gtk.Button(child=child, tooltip_text=tip, valign=Gtk.Align.START)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_: fn())
            head.append(btn)
            return btn
        if ref.kind == "k8s" and ref.data.get("context") and not ref.data.get("cluster_only"):
            icon_button(RefIcon("k8s"), "Open this cluster in the Kubernetes view",
                        lambda: (self.popdown(), win.kube.open(context=ref.data["context"])))
        if any(l["role"] in ("table", "column") for l in ref.locations):
            icon_button(RefIcon("db"), "Show it in its database's schema diagram",
                        lambda: (win.show_schema_for(ref, self.get_parent()), self.popdown()))
        icon_button(Gtk.Image.new_from_icon_name("view-refresh-symbolic"),
                    wording.say("common.try_again") if ref.status in ("not_found", "stale")
                    else wording.say("signal.search_again_one"),
                    lambda: (win.signal.refresh([ref.surface], win.touched_paths()), self.popdown()))
        icon_button(Gtk.Image.new_from_icon_name("baude-open-symbolic"), wording.say("common.open"),
                    lambda: (self.popdown(), win.show_ref(ref.surface)))
        box.append(head)
        status = ref_status_tip(Gtk.Label(label=ref_status_text(ref), xalign=0, wrap=True, max_width_chars=60), ref)
        status.add_css_class("dim-label" if ref.status != "stale" else "warning")
        box.append(status)
        if known := ref_known_text(win, ref.surface):
            box.append(Gtk.Label(label=known, xalign=0, wrap=True, max_width_chars=60, css_classes=["dim-label"]))
        if ref.locations:
            self.places = Gio.ListStore(item_type=RefPaletteRow)
            self.places.splice(0, 0, [RefPaletteRow(it) for it in ref_place_items(win, ref, self.popdown)])
            self.table = RefPalette.make_table(Gtk.NoSelection(model=self.places), (200, 100, 220),
                                               lambda pos: self.places.get_item(pos).item["open"](win))
            box.append(Gtk.ScrolledWindow(child=self.table, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          propagate_natural_height=True, max_content_height=self.ROWS * 28))
        if names := ref_run_names(ref):  # (and where it runs, in your local clusters)
            self.running = kube_running_rows(win, names, box, self.popdown)
        self.set_child(box)


def ref_place_items(win, ref, on_open=None):
    """A Signal link's places as ⌘K's rows (RefPaletteRow items): the file (and line) with its logo, what's there
    (its role), the folder it's in; opened where it is now. A stale link's places have a row after each for the
    version read when it was looked up (As it was)."""
    items = []
    for i, loc in enumerate(ref.locations):
        path = loc["path"].rstrip("/")
        name = (os.path.basename(path) or path) + ("/" if loc["role"] == "folder" else "") + \
            (f":{loc['line']}" if loc.get("line") else "")
        where = os.path.dirname(path) + (f"  ({loc['note']})" if loc.get("note") else "")
        items.append({"surface": name, "canonical": "", "faint": False, "what": loc["role"].capitalize(),
                      "summary": where, "icon": ref_icon(ref.kind, ref.data, [loc["role"]]) or "code",
                      "open": lambda win, i=i: win.open_location(ref, i) and on_open and on_open()})
        if ref.status == "stale" and loc.get("blob"):
            items.append({**items[-1], "what": "As it was", "faint": True,
                          "open": lambda win, i=i: (win.open_as_it_was(ref, i), on_open and on_open())})
    return items


def in_chat(widget):
    """Whether a widget is in a chat on the screen, what the chat's zoom sizes (.zoomable: the chat, its box, a
    subagent's chat in the side panel). Not the box hidden under a code tab, which the Kubernetes and database views
    give the focus back to as they close."""
    if widget is None or not widget.get_mapped():
        return False
    while widget is not None and not widget.has_css_class("zoomable"):
        widget = widget.get_parent()
    return widget is not None
