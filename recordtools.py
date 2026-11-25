"""recordtools — RecordChat's tools (RecordTools): Record's part of the index, chats, pages, a place as host /
notebook / page, and page edits (propose_edit, list_edits, revise_edit, withdraw_edit).
"""
import copy
import json
import os

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
from baudelib import meshtools  # noqa: E402  (META: who Meta's edits are by)
from recordmeta import REF_LIMIT, page_named, place_change  # noqa: E402
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
                mine = sum(1 for p in waiting if p.get("by") in (None, meshtools.META))
                lines.append(f"edits waiting: {len(waiting)}, {mine} of them yours (list_edits)")
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
        """A notebook's pages moved ({old name: new}): the edits waiting on them go with them, here and on its host
        (told with the next save: Store.moved_pages)."""
        for p in self.pending(notebook):
            if p["page"] in moved:
                p["page"] = moved[p["page"]]
        self.win.store.moved_pages(notebook, moved)

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
            raise ValueError(f"{short_page(waiting[0])} has edits waiting: they're accepted or rejected (or withdrawn) "
                             "before it moves to another notebook.")
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

    def _edit_op(self, turn, msg, then):
        """An edit op (edit_propose, edit_revise, edit_withdraw, edits) on the notebook open, asked of its host (a Later:
        the page as it is saved, or sent to its host, first); then(host, its answer) on the main loop."""
        store, nb = self.win.store, self.notebook
        key = store.host_of(nb) or store.host
        store.save_now()
        msg = {**msg, "notebook": nb["id"]}

        def work():
            return {"got": self.win.edit_call(key, msg)}

        def done(out):
            if "got" not in out:
                return out
            return then(key, out["got"])
        return metaagent.Later(work, done)

    def _edited(self, turn, key, got):
        """An edit op's answer (edit): the edit taken in, kept with the turn (said under its reply), shown on its page;
        the host's edits asked for again (those it took in). (The edit, as kept here; whose it was before: "your" for
        Meta's, else its proposer's.)"""
        store = self.win.store
        known = store.edit_of(got["edit"]["id"])
        by = known.get("by") if known is not None else meshtools.META
        p = store.took_edit(key, got["edit"])
        if known is None:
            turn["proposals"].append(p)
        elif p["status"] != "pending":
            if p["page"] not in turn.setdefault("withdrew", []):
                turn["withdrew"].append(p["page"])
        elif p not in turn["proposals"] and p["page"] not in turn.setdefault("revised", []):
            turn["revised"].append(p["page"])
        for other in got.get("took_in") or ():
            if (x := store.edit_of(other)) is not None:
                x["status"] = "withdrawn"
        self._status(self.agent, f"{'Edited' if p['status'] == 'pending' else 'Took back an edit to'} "
                                 f"{short_page(p['page'])}")
        self.win.edits_changed()
        if p["status"] == "pending" and (editor := self.win.open_note(p["page"])) is not None:
            editor.show_proposal(p, scroll=True)
        self.win.fetch_edits(key)
        return p, "your" if by in (None, meshtools.META) else f"{by}'s"

    def propose(self, turn, args):
        """propose_edit: a change to a page of the notebook's, asked of its host (edit_propose), where the selection
        was if it's on that page. Shown on the page (DiaryEditor.show_proposal) to accept or reject: nothing's written
        here."""
        page, error = self._page(args)
        if error:
            return error
        sel = turn.get("selection")
        near = sel["start"] if sel and sel["page"] == page and sel.get("start") is not None else None
        msg = {"op": "edit_propose", "page": page, "old_text": str(args.get("old_text") or ""),
               "new_text": str(args.get("new_text") or ""), "by": meshtools.META,
               **({"near": int(near)} if near is not None else {})}

        def then(key, got):
            fresh = self.win.store.edit_of(got["edit"]["id"]) is None
            p, whose = self._edited(turn, key, got)
            self._on_card(turn, p)
            if fresh:
                return {"text": f"Edit {p['id']} shown on the page {page} for the user to accept or reject. Nothing is "
                                "written until they accept; you'll be told which."}
            if p["status"] == "withdrawn":
                return {"text": f"That puts back what's on the page: {whose} edit {p['id']} is taken back."}
            took = got.get("took_in") or ()
            joined = f" (and took in {', '.join(took)}, now taken back)" if took else ""
            return {"text": f"Revised {whose} pending edit {p['id']}{joined}: it replaces {p['old']!r} with "
                            f"{p['new']!r} now, still waiting for the user."}
        return self._edit_op(turn, msg, then)

    @staticmethod
    def _on_card(turn, p):
        """The edit a propose_edit made or revised, kept with its card (its Accept and Reject there: card_extra)."""
        if (item := turn.get("at")) is not None and item is not turn.get("nested"):
            item["edit"] = p["id"]

    def list_edits(self, turn, args):
        """list_edits: the edits waiting for the user, page by page, each as the page has its passage now, with who
        proposed it (edits)."""
        msg = {"op": "edits"}
        if args.get("page"):
            page, error = self._page(args)
            if error:
                return error
            msg["page"] = page

        def then(_key, got):
            listed = [{"id": e["id"], "page": e["page"], "old_text": e["old"], "new_text": e["new"],
                       "by": "you" if e.get("by") in (None, meshtools.META) else e["by"],
                       **({"note": "what it replaces isn't on the page any more: revise its old_text, or withdraw it"}
                          if e.get("gone") else {})} for e in got.get("edits") or ()]
            if not listed:
                return {"text": "No edits are waiting" + (f" on {msg['page']}." if msg.get("page") else ".")}
            return {"text": json.dumps(listed, ensure_ascii=False, indent=1)}
        return self._edit_op(turn, msg, then)

    def revise_edit(self, turn, args):
        """revise_edit: a pending edit's new text (and the passage it replaces, if given) changed; it stays one edit
        (edit_revise)."""
        msg = {"op": "edit_revise", "id": str(args.get("id") or ""), "new_text": str(args.get("new_text") or ""),
               "by": meshtools.META, **({"old_text": str(args["old_text"])} if args.get("old_text") is not None else {})}

        def then(key, got):
            p, whose = self._edited(turn, key, got)
            if (item := turn.get("at")) is not None and item is not turn.get("nested"):
                item["replaced"] = p["old"]  # (as it is now: its card's red, old_text being optional)
            if p["status"] == "withdrawn":
                return {"text": f"That puts back what's on the page: {whose} edit {p['id']} is taken back."}
            return {"text": f"Edit {p['id']} revised, still waiting for the user."}
        return self._edit_op(turn, msg, then)

    def withdraw_edit(self, turn, args):
        """withdraw_edit: a pending edit taken back, from its page (edit_withdraw)."""
        def then(key, got):
            p, _whose = self._edited(turn, key, got)
            return {"text": f"Edit {p['id']} taken back."}
        return self._edit_op(turn, {"op": "edit_withdraw", "id": str(args.get("id") or "")}, then)
