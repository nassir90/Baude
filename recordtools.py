"""recordtools — RecordChat's tools (RecordTools): Record's part of the index, chats, pages, a place as host /
notebook / page, and page edits (propose_edit, list_edits, revise_edit, withdraw_edit).
"""
import copy
import json
import os
import time
import uuid

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
from baudelib import wording  # noqa: E402  (its words, Baude's: schema/copy)
import baudelib.client  # noqa: E402  (the hosts, as Baude has them: another's notebooks, through its host process)
import baudelib.hosts  # noqa: E402  (this machine's host process started again: Restart Daemon)
import baudelib.serverstate  # noqa: E402  (the sessions this machine's host process keeps: bot_started)
from baudelib import notes  # noqa: E402  (this machine's notebooks, as its host process gives them to another's Record)
import meta  # noqa: E402  (Meta, the helper in the right bar: Baude's too, beside this file)
import metaagent  # noqa: E402
import metalook  # noqa: E402
from recordstore import (  # noqa: E402
    HERE_HOST, HOME, base_name, book, full_name, relinked_pages, remote_name, short_dir, short_page,
)
from recordmeta import REF_LIMIT, kept_at, page_named, place_change, staged, staged_at  # noqa: E402
from recordui import chat_names, chat_text  # noqa: E402
from diaryeditor import DiaryEditor  # noqa: E402
from recordreader import Reader, page_key, page_name  # noqa: E402


class RecordTools:
    """RecordChat's tools: the index (Record's part), chats, pages, a place as host / notebook / page, and page
    edits."""

    # ---- the index: Record's part
    def index_lines(self):
        nb, store, lines = self.notebook, self.win.store, []
        hosts = [f"{n} ({len(store.books_of(k))} notebooks)" if k == HERE_HOST or k in store.remote_base
                 else f"{n} (not listed yet)" for k, n in self.win.note_hosts()]
        lines.append(f"open: {self.win.place()} · hosts: " + ", ".join(hosts) + " (app_index notebooks: every listed "
                     "host's)")
        if store.host in store.loading:
            lines.append(f"{store.host_name}'s notebooks: being listed")
        if nb is not None:
            pages = sorted(nb["pages"], key=lambda p: (p != HOME, p.lower()))
            folders = self.win.read_folders()
            lines.append(f"notebook open: {json.dumps(full_name(nb))} · {len(pages)} pages · "
                         "the chat read's folder: " + (", ".join(short_dir(f) for f in folders) or "none"))
            lines.append("its pages: " + ", ".join(pages[:40]) +
                         (f" (+{len(pages) - 40}: app_index pages)" if len(pages) > 40 else ""))
            if waiting := self.pending(nb):
                lines.append(f"your edits waiting: {len(waiting)} (list_edits)")
        else:
            lines.append("no notebook open")
        if others := [t for t in store.notebooks if t is not nb]:
            lines.append("other notebooks: " + ", ".join(f"{json.dumps(full_name(t))} ({len(t['pages'])} page"
                                                         f"{'s' if len(t['pages']) != 1 else ''})"
                                                         for t in others[:20]) +
                         (f" (+{len(others) - 20}: app_index notebooks)" if len(others) > 20 else ""))
        tabs = [short_page(c.page) if isinstance(c, DiaryEditor) else f"chat {json.dumps(c.chat['title'])}"
                for c in self.win.tabs.children() if isinstance(c, DiaryEditor) or isinstance(c, Reader) and c.chat]
        if tabs:
            lines.append("tabs open: " + ", ".join(tabs))
        lines.append(f"chats: {len(chat_names().chats)} on this computer (app_index chats; an id is its first 8 "
                     "characters)")
        return lines

    def running_lines(self):
        out = []
        if self.agent is not None and not self.agent.closed:
            out.append(f"{meta.NAME}'s {self.agent.runs} (pid {self.agent.proc.p.pid}, {self.agent.model}"
                       f"{f', {self.agent.effort} effort' if self.agent.effort else ''}{', at a turn' if self.ask else ''})")
        if readers := [c for c in self.win.tabs.children() if isinstance(c, Reader) and c.chat]:
            out.append(f"{len(readers)} chat{'s' if len(readers) != 1 else ''} followed as written")
        return out

    def sections(self):
        def notebooks(_f):
            store, names = self.win.store, dict(self.win.note_hosts())
            return [{"host": names.get(k, k), "name": full_name(t), "pages": len(t["pages"]),
                     "open": t is self.notebook}
                    for k in [store.host, *[k for k in [HERE_HOST, *store.remote_base] if k != store.host]]
                    for t in store.books_of(k)]

        def pages(_f):
            open_ = set(self.win.open_pages())
            books = ([self.notebook] if self.notebook else []) + \
                [t for t in self.win.store.notebooks if t is not self.notebook]
            return [{"notebook": full_name(t), "page": p, "chars": len(t["pages"][p]), "mine": t is self.notebook,
                     "tab": t is self.notebook and p in open_}
                    for t in books for p in sorted(t["pages"], key=lambda p: (p != HOME, p.lower()))]

        def chats(_f):
            readers = {c.chat["path"] for c in self.win.tabs.children() if isinstance(c, Reader) and c.chat}
            return [{"id": self.chat_id(c["path"])[:8], "title": c["title"], "folder": short_dir(c["cwd"]) or "no folder",
                     "ago": widgets.time_ago(c["used"]), "open": c["path"] in readers} for c in chat_names().chats]
        return {
            "notebooks": (notebooks, lambda r: f"{r['host']} / {json.dumps(r['name'])} · {r['pages']} pages" +
                          (" · open" if r["open"] else "")),
            "pages": (pages, lambda r: (r["page"] if r["mine"] else f"{r['notebook']}/{r['page']}") +
                      (f" · {r['chars']} characters" if r["chars"] else " · empty") + (" · tab open" if r["tab"] else "")),
            "chats": (chats, lambda r: f"{r['id']} {json.dumps(r['title'])} · {r['folder']} · {r['ago']}" +
                      (" · open" if r["open"] else "")),
        }

    # ---- chats
    @staticmethod
    def chat_id(path):
        return os.path.basename(path).removesuffix(".jsonl")

    def find_chat(self, ref):
        ref = ref.strip().removeprefix("s:")
        if not ref:
            return None
        chats = list(chat_names().chats)
        for c in self.win.store.chats:  # (those open, if the list isn't read yet)
            if not any(x["path"] == c.get("path") for x in chats):
                chats.append({"path": c["path"], "title": c.get("title") or "Untitled chat", "cwd": c.get("cwd")})
        tests = (lambda c: self.chat_id(c["path"]) == ref, lambda c: len(ref) >= 4 and self.chat_id(c["path"]).startswith(ref),
                 lambda c: c["title"].lower() == ref.lower(), lambda c: os.path.realpath(c["path"]) == os.path.realpath(ref))
        for test in tests:
            found = [c for c in chats if test(c)]
            if len(found) == 1:
                c = found[0]
                return {"key": self.chat_id(c["path"]), "title": c["title"], "path": c["path"], "cwd": c.get("cwd"),
                        "used": c.get("used")}
        return None

    def chats_rows(self, refs):
        rows = []
        for ref in refs:
            chat = self._chat(ref)
            ago = f" · {widgets.time_ago(chat['used'])}" if chat.get("used") else ""
            rows.append({"icon": "baude-claude-symbolic", "title": chat["title"],
                         "subtitle": (short_dir(chat.get("cwd")) or wording.say("chats.no_folder")) + ago,
                         "open": lambda path=chat["path"]: self.win.open_chat(path)})
        return rows

    def chat_text(self, chat):
        try:
            text = chat_text(chat["path"], chat["title"], chat.get("cwd"))
        except OSError as e:
            return f"(not read: {e})"
        return text if len(text) <= REF_LIMIT else \
            f"[… its first {len(text) - REF_LIMIT} characters not put here: Read {chat['path']}]\n" + text[-REF_LIMIT:]

    def rename_chat_to(self, chat, title):
        self.win.rename_chat(chat["path"], title)

    # ---- pages
    def pages_rows(self, refs):
        rows, store = [], self.win.store
        for ref in refs:
            book, _, name = ref.rpartition("/")
            nb = next((t for t in store.notebooks if full_name(t).lower() == book.strip().lower()), None) if book \
                else self.notebook
            page = page_named(nb, name, store) if nb is not None else None
            if page is None:
                raise ValueError(f"No page {ref!r}: app_index pages lists them.")
            rows.append({"icon": "text-x-generic-symbolic", "title": short_page(page),
                         "subtitle": f"{full_name(nb)} · {page}" if ":" in page else full_name(nb),
                         "open": lambda nb=nb, page=page: self.open_page(nb, page)})
        return rows

    def forms(self):
        if self.notebook is None:
            return {}
        return {"new_page": (f"make a page in the {book().lower()} open (its name, the page it goes under, its text)",
                             self.page_form)}

    def page_form(self):
        """New page's fields, as the pages' + asks (PagesList.new_page), with where it goes and what it says: for
        Meta's new_page form."""
        nb = self.notebook
        if nb is None:
            raise ValueError(f"No {book().lower()} is open.")
        name = Gtk.Entry(activates_default=True, placeholder_text=wording.say("meta_app.its_name"))
        tops = "(the top)"
        under = Gtk.DropDown.new_from_strings([tops, *sorted(nb["pages"], key=lambda p: (p != HOME, p.lower()))])
        text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6, left_margin=8,
                            right_margin=8)
        frame = Gtk.ScrolledWindow(child=text, min_content_height=72, hscrollbar_policy=Gtk.PolicyType.NEVER)
        frame.add_css_class("card")
        line = Gtk.Box(spacing=8)
        said = Gtk.Label(label="Under")
        said.add_css_class("dim-label")
        line.append(said)
        under.set_hexpand(True)
        line.append(under)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for w in (name, line, frame):
            box.append(w)

        def full():
            parent = metalook.Form.get(under)
            return f"{parent}:{page_name(name.get_text())}" if parent != tops else page_name(name.get_text())

        def check():
            if not page_name(name.get_text()):
                return "A page needs a name"
            if nb["pages"].get(full(), "").strip():
                return f"There's a page {full()} already"
            return None

        def submit():
            if (problem := check()) is not None:
                raise ValueError(problem)
            page = full()
            nb["pages"][page] = metalook.Form.get(text)
            if nb is self.notebook:  # (a page of that name open, empty: shown again with its text, not saved over)
                self.pages_changed({})
                self.win.open_note(page, select=False)
            else:
                self.win.store.save()
            return f"made {page}"
        return metalook.Form("New page", box, [("name", "the page's own name", name),
                                           ("under", "the page it goes under, or (the top)", under),
                                           ("text", "what it says, in Markdown (or empty)", text)],
                         check, submit, verb=wording.say("common.add"))

    def open_page(self, notebook, page):
        if notebook is not self.win.current:
            self.win.select(notebook["id"])
        self.win.open_note(page)

    def buttons(self):
        return {"delete_notebook": f"Delete the {book().lower()} open and its pages (asks first; "
                                   "can't be undone)",
                "host": f"Open a host (items: its name): its {book(plural=True).lower()} listed"}

    def button(self, action, items):
        if action == "host" and items:
            hosts = self.win.note_hosts()
            key = next((k for k, n in hosts if n.lower() == str(items[0]).strip().lower()), None)
            if key is None:
                raise ValueError(f"No host {items[0]!r}: they're {', '.join(n for _k, n in hosts)}.")
            btn = Gtk.Button(label=wording.say("meta.open_item", name=dict(hosts)[key]))
            btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda *_: self.win.pick_host(key))
            return btn
        if action != "delete_notebook" or self.notebook is None:
            raise ValueError(f"There's no button {action}: app_index buttons lists them.")
        btn = Gtk.Button(label=f"Delete {full_name(self.notebook)}…")
        btn.add_css_class("destructive-action")
        btn.connect("clicked", lambda *_: self.win.delete())
        return btn

    def _pages_act(self, turn, said, before, moved=None, marks=(), moved_in=None, on=()):
        """Pages changed (before: {notebook id: {page: its text before, or None}}, of every one touched, on: those
        notebooks; moved: {old name: new}, in notebook moved_in (the one open, if not said)), kept as an action: undone,
        they're put back as they were, and done again, as the action left them; either only while every one it touched
        is as the other left it (apply_app). Each notebook kept with its host (hosts: two hosts' may share an id)."""
        store, moved_in = self.win.store, moved_in or self.notebook
        hosts = {t["id"]: store.host_of(t) for t in (*on, moved_in) if t is not None}
        moved_in = (moved_in or {}).get("id")
        books = {b: self._book(b, hosts.get(b)) for b in before}
        after = {b: {p: books[b]["pages"].get(p) for p in pages} for b, pages in before.items()}
        marked = {m[1] for m in marks}  # (and those whose text it changed, there before and after: edited)
        marks = list(marks) + [("page", f"{b}\n{p}", "edited", "") for b, pages in before.items()
                               for p, text in pages.items() if text is not None and after[b][p] is not None and
                               after[b][p] != text and f"{b}\n{p}" not in marked]
        return self.act(turn, said, "pages",
                        {"books": before, "expect": after, "moved": {v: k for k, v in (moved or {}).items()},
                         "in": moved_in, "hosts": hosts},
                        {"books": after, "expect": before, "moved": dict(moved or {}), "in": moved_in, "hosts": hosts},
                        marks)

    def apply_app(self, kind, data):
        if kind == "notebook":
            return self.apply_notebook(data)
        if kind != "pages":
            raise ValueError(f"No such action: {kind}")
        hosts = data.get("hosts") or {}  # (each notebook's host: Meta's tools change any; none said, from before)
        books = {b: self._book(b, hosts.get(b)) for b in set(data["books"]) | ({data["in"]} if data.get("in") else set())}
        if any(books[b] is None for b in data["books"]):
            raise ValueError(f"its {book().lower()} is gone (or its host's {book(plural=True).lower()} aren't listed)")
        changed = [p for b, pages in (data.get("expect") or {}).items() for p, text in pages.items()
                   if books[b]["pages"].get(p) != text]
        if changed:  # (written in since, or changed by what came after: that would be lost)
            raise ValueError(f"{', '.join(short_page(p) for p in changed[:3])} changed since; undo what came after "
                             "first, or change it back")
        for b, pages in data["books"].items():
            for p, text in pages.items():
                if text is None:
                    books[b]["pages"].pop(p, None)
                else:
                    books[b]["pages"][p] = text
        here = self.notebook is not None and (books.get(data["in"]) is self.notebook if data.get("in") else True)
        if not here and data.get("moved") and (nb := books.get(data.get("in"))) is not None:
            self.follow_moves(nb, data["moved"])
        self.pages_changed(data.get("moved") or {} if here else {})

    def apply_notebook(self, data):
        """A notebook Meta made, taken away again (drop: its id; only while its pages are as it left them, expect) or
        made again (add: as it was made)."""
        store = self.win.store
        if "add" in data:
            host = data.get("host") or store.host
            if host != HERE_HOST and host not in store.remote_base:
                raise ValueError(f"its host's {book(plural=True).lower()} aren't listed here: open it first")
            if self._book(data["add"]["id"], host) is None:
                store.books_of(host).insert(0, copy.deepcopy(data["add"]))
        else:
            nb = self._book(data["drop"])
            if nb is None:
                return
            if nb["pages"] != data["expect"] or nb.get("messages"):
                raise ValueError(f"{full_name(nb)} changed since; undo what came after first")
            if nb is self.win.current:
                self.win.notebook_list.unselect_all()
            books = store.books_of(store.host_of(nb))
            books[:] = [t for t in books if t is not nb]
        store.save()
        self.win.books_changed()

    def pages_changed(self, moved):
        """The notebook open's pages changed under its tabs: those moved go with their pages (Window.note_renamed),
        and so do the edits waiting on them; those gone are closed; the rest shown again; the list again."""
        win, nb = self.win, self.notebook
        if nb is not None:
            self.follow_moves(nb, moved)
        win.store.save()
        if moved:
            win.note_renamed(moved)
        for child in list(win.tabs.children()):
            if isinstance(child, DiaryEditor) and child.notebook is nb:
                if child.page != HOME and child.page not in nb["pages"]:
                    win.tabs.close(("note", child.page))
                else:
                    child.reload()
        win.pages.fill()

    def follow_moves(self, notebook, moved):
        """A notebook's pages moved ({old name: new}): the edits waiting on them go with them."""
        for p in self.pending(notebook):
            if p["page"] in moved:
                p["page"] = moved[p["page"]]

    def _named_page(self, name, notebook=None):
        notebook = notebook or self.notebook
        page = page_named(notebook, str(name or ""), self.win.store)
        if page is None:
            raise ValueError(f"There's no page {name!r} in {full_name(notebook)}: app_index pages lists them.")
        return page

    # ---- a place is host / notebook / page: the tools that name a notebook take its host too (left out: the one
    # picked), and change any host's, listed first if it isn't yet (_on_hosts); the one open's, with neither said
    def _host(self, name):
        """A tool's host, by its name (left out: the one picked): its key."""
        name = str(name or "").strip()
        if not name:
            return self.win.store.host
        hosts = self.win.note_hosts()
        key = next((k for k, n in hosts if n.lower() == name.lower()), None)
        if key is None:
            raise ValueError(f"There's no host {name!r}: they're {', '.join(n for _k, n in hosts)}.")
        return key

    def _on(self, key):
        """ on <host>, for one that isn't the one picked."""
        return "" if key == self.win.store.host else f" on {dict(self.win.note_hosts()).get(key, key)}"

    def _on_hosts(self, keys, then):
        """then(), once every host of keys has its notebooks listed here: one that hasn't, listed first (a Later:
        the main loop free meanwhile)."""
        store, win = self.win.store, self.win
        missing = [k for k in dict.fromkeys(keys) if k != HERE_HOST and (k not in store.remote_base or k in store.loading)]
        if not missing:
            return then()
        remotes = {}
        for k in missing:
            r = win.host_remote(k)
            if not win.reachable(r):
                said = win.host_state(k) or "it isn't there"
                raise ValueError(f"{dict(win.note_hosts()).get(k, k)}'s {book(plural=True).lower()} aren't listed "
                                 f"here yet: {said}.")
            remotes[k] = r

        def work():
            return {"replies": {k: r.call({"op": "notes"}, timeout=30) for k, r in remotes.items()}}

        def done(out):
            for k, reply in out["replies"].items():
                if reply.get("op") != "notes" or reply.get("error"):
                    return {"error": f"{remote_name(remotes[k])}'s {book(plural=True).lower()} weren't listed: "
                                     f"{baudelib.client.wire.said(reply) or 'no answer'}"}
                if k not in store.remote_base or k in store.loading:
                    books = [notes.shared(b) for b in reply.get("notebooks") or ()
                             if isinstance(b, dict) and isinstance(b.get("id"), str) and isinstance(b.get("pages"), dict)]
                    store.take(k, remote_name(remotes[k]), books)
                    if k == store.host:
                        win.notes_changed(True)
            try:
                return then()
            except ValueError as e:
                return {"error": str(e)}
        return metaagent.Later(work, done)

    def add_page(self, turn, args):
        key = self._host(args.get("host"))
        return self._on_hosts([key], lambda: self._add_page(turn, args, key))

    def _add_page(self, turn, args, key):
        nb = self._notebook_named(args.get("notebook"), True, "notebook", turn, key)
        name = page_name(args.get("page"))
        if not name:
            raise ValueError("A page's name is needed.")
        if nb["pages"].get(name, "").strip():
            raise ValueError(f"There's a page {name} in {full_name(nb)}{self._on(key)} already: propose_edit changes "
                             "its text.")
        before = {nb["id"]: {name: nb["pages"].get(name)}}
        nb["pages"][name] = str(args.get("text") or "")
        here = nb is self.notebook
        a = self._pages_act(turn, f"Added {short_page(name)}" + ("" if here else f" to {full_name(nb)}{self._on(key)}"),
                            before, marks=[("page", page_key(nb, name), "added")], moved_in=nb, on=[nb])
        if here:
            self.win.open_note(name, select=False)
        self.pages_changed({})
        return {"text": f"Added {name} to {full_name(nb)}{self._on(key)} (action {a['id']})."}

    def delete_page(self, turn, args):
        key = self._host(args.get("host"))
        return self._on_hosts([key], lambda: self._delete_page(turn, args, key))

    def _delete_page(self, turn, args, key):
        nb = self._notebook_named(args.get("notebook"), True, "notebook", turn, key)
        page = self._named_page(args.get("page"), nb)
        if page == HOME:
            raise ValueError(f"{HOME} can't be deleted.")
        before = {nb["id"]: {page: nb["pages"].get(page)}}
        if nb is self.notebook:
            self.win.tabs.close(("note", page))
        nb["pages"].pop(page, None)
        a = self._pages_act(turn, wording.say("meta_app.deleted", what=short_page(page)) +
                            ("" if nb is self.notebook else f" from {full_name(nb)}{self._on(key)}"), before,
                            marks=[("page", page_key(nb, page), "removed")], moved_in=nb, on=[nb])
        self.pages_changed({})
        return {"text": f"Deleted {page} (action {a['id']}); undo puts it back."}

    def _notebook_named(self, name, open_one, said="from_notebook, to_notebook", turn=None, host=None):
        """The notebook a tool's call names (its name, any case), on host (its key; None: the one picked); not named,
        the one open, if open_one and it's on that host (and it's the one turn was asked with)."""
        store = self.win.store
        host = store.host if host is None else host
        name = base_name(str(name or ""))
        if not name:
            if open_one and host == store.host:
                self._same_open(turn)
                if self.notebook is not None:
                    return self.notebook
            if host != store.host:
                raise ValueError(f"Say which {book().lower()}{self._on(host)} ({said}).")
            raise ValueError(f"No {book().lower()} is open: say which ({said}).")
        nb = next((t for t in store.books_of(host) if full_name(t).lower() == name.lower()), None)
        if nb is None:
            raise ValueError(f"There's no {book().lower()} {name!r}{self._on(host)}: app_index notebooks lists them.")
        return nb

    def move_pages(self, turn, args):
        from_key = self._host(args.get("from_host") or args.get("host"))
        to_key = self._host(args.get("to_host") or args.get("host"))
        return self._on_hosts([from_key, to_key], lambda: self._move_pages(turn, args, from_key, to_key))

    def _move_pages(self, turn, args, from_key, to_key):
        under = page_name(args.get("under"))
        src = self._notebook_named(args.get("from_notebook"), True, turn=turn, host=from_key)
        nb = self._notebook_named(args.get("to_notebook"), True, turn=turn, host=to_key)
        names = [self._named_page(p, src) for p in args.get("pages") or ()]
        if not names:
            raise ValueError("Which pages? pages is empty.")
        if HOME in names:
            raise ValueError(f"{HOME} stays where it is.")
        listed = set(src["pages"]) | (set(self.win.pages.names()[0]) if src is self.notebook else set())
        moved = {}
        for old in names:
            if src is nb and (under == old or under.startswith(old + ":")):
                raise ValueError(f"{old} can't go under itself.")
            new = f"{under}:{short_page(old)}" if under else short_page(old)
            moved.update({n: new + n[len(old):] for n in listed if n == old or n.startswith(old + ":")})
        moved = {k: v for k, v in moved.items() if k != v or src is not nb}
        if not moved:
            return {"text": "They're there already."}
        if src is not nb and src["id"] == nb["id"]:  # (two hosts' copies of one notebook: a records.json copied)
            raise ValueError(f"{full_name(src)}{self._on(from_key)} and {full_name(nb)}{self._on(to_key)} are copies "
                             "of one notebook (the same id): pages can't be moved between them here.")
        if src is not nb and (waiting := [p["page"] for p in self.pending(src) if p["page"] in moved]):
            raise ValueError(f"{short_page(waiting[0])} has edits of yours waiting: they're accepted or rejected (or "
                             "withdrawn) before it moves to another notebook.")
        if clash := [to for to in moved.values() if to in nb["pages"] and (src is not nb or to not in moved)]:
            raise ValueError(f"There's a page {clash[0]} in {full_name(nb)}{self._on(to_key)} already.")
        where = under or "the top"
        said = f"Moved {', '.join(short_page(n) for n in names)} {'under ' + short_page(under) if under else 'to the top'}"
        if src is nb:
            was = dict(nb["pages"])
            renamed = relinked_pages(nb["pages"], moved)
            touched = {k for k in set(was) | set(renamed) if was.get(k) != renamed.get(k)}
            before = {nb["id"]: {k: was.get(k) for k in touched}}
            nb["pages"].clear()  # (the same dict: the notebook keeps it)
            nb["pages"].update(renamed)
            marks = [("page", page_key(nb, new), "moved", f"from {old.rpartition(':')[0] or 'the top'}",
                      page_key(nb, old)) for old, new in moved.items() if old in was]
            a = self._pages_act(turn, said + (f" in {full_name(nb)}{self._on(to_key)}" if nb is not self.notebook
                                              else ""), before,
                                moved, marks, moved_in=nb, on=[nb])
            if nb is not self.notebook:
                self.follow_moves(nb, moved)
            self.pages_changed(moved if nb is self.notebook else {})
        else:
            before = {src["id"]: {old: src["pages"].get(old) for old in moved},
                      nb["id"]: {new: nb["pages"].get(new) for new in moved.values()}}
            for old, new in moved.items():
                if old in src["pages"]:
                    nb["pages"][new] = src["pages"].pop(old)
            how = "added" if self.marks("notebook").get(nb["id"], ("",))[0] == "added" else "moved"  # (new to it)
            marks = [("page", page_key(nb, new), how, f"from {full_name(src)}", page_key(src, old))
                     for old, new in moved.items() if new in nb["pages"]]
            into = f" into {full_name(nb)}{self._on(to_key)}" if nb is not self.notebook else ""
            a = self._pages_act(turn, said + f" from {full_name(src)}{self._on(from_key)}{into}", before, marks=marks,
                                moved_in=nb, on=[src, nb])
            self.pages_changed({})
        return {"text": f"Moved {len(moved)} page{'s' if len(moved) != 1 else ''} under {where}"
                        f"{f' in {full_name(nb)}{self._on(to_key)}' if nb is not self.notebook else ''} "
                        f"(action {a['id']})."}

    def add_notebook(self, turn, args):
        """add_notebook: a notebook made at once, as a page is (add_page), marked in the notebooks' list, part of the
        turn's change and undone with it; not opened. On any host."""
        key = self._host(args.get("host"))
        return self._on_hosts([key], lambda: self._add_notebook(turn, args, key))

    def _add_notebook(self, turn, args, key):
        store, name = self.win.store, base_name(str(args.get("name") or ""))
        if not name:
            raise ValueError(f"A {book().lower()}'s name is needed.")
        if name.lower() in {full_name(t).lower() for t in store.books_of(key)}:
            raise ValueError(f"There's a {book().lower()} called {name}{self._on(key)} already.")
        made = store.new(name, key, dict(self.win.note_hosts()).get(key))
        made.pop("fresh", None)  # (named: kept, Store.is_blank)
        store.save()
        a = self.act(turn, f"Made {name}{self._on(key)}", "notebook",
                     {"drop": made["id"], "expect": copy.deepcopy(made["pages"]), "host": key},
                     {"add": copy.deepcopy(made), "host": key}, [("notebook", made["id"], "added")])
        self.win.books_changed()
        return {"text": f"Made the {book().lower()} {name}{self._on(key)} (action {a['id']}); add_page notebook and "
                        "move_pages to_notebook put pages in it."}

    def edit_page(self, turn, args):
        """edit_page: a page's text changed at once, part of the turn's change to the pages (Meta.act), undone with
        it."""
        key = self._host(args.get("host"))
        return self._on_hosts([key], lambda: self._edit_page(turn, args, key))

    def _edit_page(self, turn, args, key):
        nb = self._notebook_named(args.get("notebook"), True, "notebook", turn, key)
        page = self._named_page(args.get("page"), nb)
        text, old, new = nb["pages"].get(page, ""), str(args.get("old_text") or ""), str(args.get("new_text") or "")
        if old == new:
            raise ValueError("new_text is old_text: that changes nothing.")
        at, found = place_change(text, old)
        if at is None:
            raise ValueError(found)
        before = {nb["id"]: {page: text}}
        nb["pages"][page] = text[:at] + new + text[at + len(found):]
        a = self._pages_act(turn, f"Edited {short_page(page)}" +
                            ("" if nb is self.notebook else f" in {full_name(nb)}{self._on(key)}"), before, moved_in=nb,
                            on=[nb])
        self.pages_changed({})
        return {"text": f"Edited {page} (change {a['id']}, undone as one with the rest of this turn's)."}

    def highlight_pages(self, turn, args):
        nb = self.notebook
        keys = {page_key(nb, self._named_page(p)) for p in args.get("pages") or ()}
        self.lit = {**self.lit, "page": keys}
        self.marks_changed()
        return {"text": f"Highlighted {len(keys)} page{'s' if len(keys) != 1 else ''} in the pages' list."}

    # ---- page edits (propose_edit, list_edits, revise_edit, withdraw_edit)
    def _page(self, args):
        """The page a tool's call names, or (None, the error to answer it with)."""
        notebook = self.notebook
        page = page_named(notebook, str(args.get("page") or ""), self.win.store)
        if page is None:
            names = ", ".join(sorted(notebook["pages"])[:40])
            return None, {"error": f"There's no page {args.get('page')!r} in this notebook. Its pages: {names}."}
        return page, None

    def _spans(self, page):
        """A page's own text, and the edits waiting on it, where each is in that text now: [(start, old, new, edit)]
        (one whose passage isn't on the page as it was: left out)."""
        notebook, editor = self.notebook, self.win.tabs.get(("note", page))
        text = editor.kept() if editor is not None else notebook["pages"].get(page, "")
        out = []
        for p in self.pending(notebook):
            if p["page"] != page:
                continue
            at = editor.edit_start(p) if editor is not None else None
            if at is None and not p["old"]:
                at = len(text)
            elif at is None:
                at = p.get("start", 0) if text[p.get("start", 0):p.get("start", 0) + len(p["old"])] == p["old"] else \
                    place_change(text, p["old"], p.get("start"))[0]
            if at is not None:
                out.append((at, p["old"], p["new"], p))
        return text, out

    def _edited(self, turn, p):
        """A turn's edit to a page, made or changed: kept with the turn (said under its reply), shown on its page."""
        if p["status"] != "pending":
            if p["page"] not in turn.setdefault("withdrew", []):
                turn["withdrew"].append(p["page"])
        elif p not in turn["proposals"] and p["page"] not in turn.setdefault("revised", []):
            turn["revised"].append(p["page"])
        self._status(self.agent, f"{'Edited' if p['status'] == 'pending' else 'Took back an edit to'} "
                                 f"{short_page(p['page'])}")
        if p["status"] == "pending" and (editor := self.win.open_note(p["page"])) is not None:
            editor.show_proposal(p, scroll=True)
        self.show_pending_bar()
        self.edit_cards_changed()

    def _restage(self, turn, p, **fields):
        """A pending edit changed (old, new, start) or taken back (status): off its page, changed, and shown again."""
        if (editor := self.win.tabs.get(("note", p["page"]))) is not None:
            editor.unshow(p)
        p.update(fields)
        if p["status"] == "pending" and p["old"] == p["new"]:
            p["status"] = "withdrawn"  # (what it puts in is what's there: no edit)
        self._edited(turn, p)

    def propose(self, turn, args):
        """propose_edit: a change to a page of the notebook's, its old text found once (or where the selection was) in
        the page as it reads with the agent's pending edits applied (staged). Text only in such an edit's new text, or
        in what it replaces: that edit revised to take this in, one edit for the passage they all cover (the others in
        it taken back); else a new one. Shown on the page (DiaryEditor.show_proposal) to accept or reject: nothing's
        written here."""
        page, error = self._page(args)
        if error:
            return error
        old, new = str(args.get("old_text") or ""), str(args.get("new_text") or "")
        if old == new:
            return {"error": "new_text is old_text: that changes nothing."}
        text, spans = self._spans(page)
        sel = turn.get("selection")
        near = sel["start"] if sel and sel["page"] == page and sel.get("start") is not None else None
        view, pieces = staged(text, spans)
        if not old:
            if view and not view.endswith("\n") and not new.startswith("\n"):
                new = "\n" + new  # (added at the end, after what's there and what's waiting: on a line of its own)
            return self._new_edit(turn, page, len(text), "", new)
        at, found = place_change(view, old, staged_at(pieces, near) if near is not None else None)
        if at is None and any(want and want in view for want in (old, old.strip())):
            return {"error": found}  # (in the page as the edits have it, more than once)
        if at is None:  # (not in the page as the edits have it: text one of them replaces, perhaps)
            k, kept_found = place_change(text, old, near)
            if k is None:
                return {"error": found}
            end = k + len(kept_found)
            hit = [x for x in spans if x[0] < end and x[0] + len(x[1]) > k]
            if not hit:
                return self._new_edit(turn, page, k, kept_found, new)
            k0, k1 = min(k, hit[0][0]), max(end, max(x[0] + len(x[1]) for x in hit))
            return self._merged(turn, [x[3] for x in hit], k0, text[k0:k1], text[k0:k] + new + text[end:k1])
        end = at + len(found)
        hit = [piece for piece in pieces if piece[4] is not None and
               (piece[0] < end and piece[1] > at or piece[0] == piece[1] and at < piece[0] < end)]
        if not hit:  # (none of a pending edit's passage in it: one of its own)
            k0, k1 = kept_at(pieces, at, max), kept_at(pieces, end, min)
            return self._new_edit(turn, page, k0, text[k0:k1], new)
        s0, s1 = min(at, hit[0][0]), max(end, hit[-1][1])  # (all of theirs in it, and none of another's)
        k0 = hit[0][2] if s0 == hit[0][0] else kept_at(pieces, at, max)
        k1 = hit[-1][3] if s1 == hit[-1][1] else kept_at(pieces, end, min)
        return self._merged(turn, [piece[4] for piece in hit], k0, text[k0:k1], view[s0:at] + new + view[end:s1])

    def _new_edit(self, turn, page, start, old, new):
        p = {"id": uuid.uuid4().hex[:10], "notebook": self.notebook.get("id"), "page": page, "old": old, "new": new,
             "start": start, "status": "pending", "t": time.time()}
        turn["proposals"].append(p)
        self._on_card(turn, p)
        self._edited(turn, p)
        return {"text": f"Edit {p['id']} shown on the page {page} for the user to accept or reject. Nothing is written "
                        "until they accept; you'll be told which."}

    @staticmethod
    def _on_card(turn, p):
        """The edit a propose_edit made or revised, kept with its card (its Accept and Reject there: card_extra)."""
        if (item := turn.get("at")) is not None and item is not turn.get("nested"):
            item["edit"] = p["id"]

    def _merged(self, turn, edits, start, old, new):
        """Pending edits a change reaches into, as one: the first revised to replace old with new, the others taken
        back."""
        first = edits[0]
        self._on_card(turn, first)
        for p in edits[1:]:
            self._restage(turn, p, status="withdrawn")
        self._restage(turn, first, old=old, new=new, start=start)
        if first["status"] == "withdrawn":
            return {"text": f"That puts back what's on the page: your edit {first['id']} is taken back."}
        joined = f" (and took in {', '.join(p['id'] for p in edits[1:])}, now taken back)" if edits[1:] else ""
        return {"text": f"Revised your pending edit {first['id']}{joined}: it replaces {old!r} with {new!r} now, still "
                        "waiting for the user."}

    def list_edits(self, turn, args):
        """list_edits: the agent's edits waiting for the user, page by page, each as the page has its passage now."""
        pages = sorted({p["page"] for p in self.pending(self.notebook)})
        if args.get("page"):
            page, error = self._page(args)
            if error:
                return error
            pages = [page] if page in pages else []
        listed = []
        for page in pages:
            spans = self._spans(page)[1]
            listed += [{"id": p["id"], "page": page, "old_text": old, "new_text": new}
                       for _at, old, new, p in sorted(spans, key=lambda x: x[0])]
            placed = {id(x[3]) for x in spans}
            listed += [{"id": p["id"], "page": page, "old_text": p["old"], "new_text": p["new"],
                        "note": "what it replaces isn't on the page any more: revise its old_text, or withdraw it"}
                       for p in self.pending(self.notebook) if p["page"] == page and id(p) not in placed]
        if not listed:
            return {"text": "No edits of yours are waiting" + (f" on {pages[0] if pages else args['page']}."
                                                               if args.get("page") else ".")}
        return {"text": json.dumps(listed, ensure_ascii=False, indent=1)}

    def _pending_named(self, args):
        p = next((p for p in self.pending(self.notebook) if p["id"] == str(args.get("id") or "")), None)
        return p, None if p is not None else {"error": f"No edit of yours with the id {args.get('id')!r} is waiting: "
                                                         "list_edits shows those that are."}

    def revise_edit(self, turn, args):
        """revise_edit: a pending edit's new text (and the passage it replaces, if given) changed; it stays one edit."""
        p, error = self._pending_named(args)
        if error:
            return error
        new, fields = str(args.get("new_text") or ""), {}
        if not p["old"] and not ("old_text" in args and args["old_text"]) and not new.startswith("\n"):
            text, spans = self._spans(p["page"])  # (an addition: after what's there and what's waiting before it)
            before = staged(text, [x for x in spans if x[3] is not p and (x[1] or x[3].get("t", 0) < p.get("t", 0))])[0]
            if before and not before.endswith("\n"):
                new = "\n" + new
        if "old_text" in args and str(args["old_text"]) != p["old"]:
            text, spans = self._spans(p["page"])
            near = next((x[0] for x in spans if x[3] is p), p.get("start"))
            at, old = place_change(text, str(args["old_text"]), near)
            if at is None:
                return {"error": old}
            if any(x[3] is not p and x[0] < at + len(old) and x[0] + len(x[1]) > at for x in spans):
                return {"error": "That's in the passage another of your pending edits replaces: revise that one."}
            fields = {"old": old, "start": at}
        self._restage(turn, p, new=new, **fields)
        if (item := turn.get("at")) is not None and item is not turn.get("nested"):
            item["replaced"] = p["old"]  # (as it is now: its card's red, old_text being optional)
        if p["status"] == "withdrawn":
            return {"text": f"That puts back what's on the page: your edit {p['id']} is taken back."}
        return {"text": f"Edit {p['id']} revised, still waiting for the user."}

    def withdraw_edit(self, turn, args):
        """withdraw_edit: a pending edit taken back, from its page."""
        p, error = self._pending_named(args)
        if error:
            return error
        self._restage(turn, p, status="withdrawn")
        return {"text": f"Edit {p['id']} taken back."}
