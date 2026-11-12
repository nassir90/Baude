"""Baude's viewers hosted by a window that isn't Baude's: a folder's code, files and Git history on pages, as Baude's
side panel has them, with the Kubernetes and database views over the window, as in Baude's. ViewerHost (mixed in)
is what each viewer asks of its window: Record's window has it, the pages among its own tabs; ViewerWindow is a window
of nothing else, for a folder's viewers on their own. There's no chat here, so the Git page shows none of a chat's
commits or cards."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

import widgets  # noqa: E402
from codeview import CODE_LIMIT, CodePage, FilesPage  # noqa: E402
from gitview import GitPage  # noqa: E402
from kubeview import KubeView  # noqa: E402
from schemaview import SchemaView  # noqa: E402
from widgets import RefIcon, themed_icon  # noqa: E402


class ViewerHost:
    """A window hosting Baude's viewers (mixed in). add_viewer_pages(stack) puts the Code, Files and Git pages in a stack
    of its (named code, files, git); viewer_overlays(child) lays the Kubernetes and database views over child (hidden
    till opened). It then has what the viewers ask of Baude's window: show_code, show_folder, show_git, open_item,
    kube, schema, input, stack, panel, cwd and run_cwd (viewer_folder(): the window's to say), and no Signal,
    checkout or tool cards."""

    signal = ref_checkout = None
    has_chat = False  # (the Git page lists the pull requests of the folder's checkout's branches: no chat's)

    def viewer_folder(self):
        """The folder the viewers show (None: none)."""
        return None

    @property
    def cwd(self):
        return self.viewer_folder()

    @property
    def run_cwd(self):
        return self.viewer_folder()

    @property
    def panel(self):
        return self  # (the Git page asks if its page is showing: get_visible(), stack's visible child)

    def add_viewer_pages(self, stack, code=True):
        """The viewers' pages in stack: Code (unless code is False: the window shows code its own way, Record in a tab
        of its own, show_code), Files and Git."""
        self.stack, self.tool_cards = stack, {}
        self.files_page, self.git_page = FilesPage(self), GitPage(self)
        pages = [(self.files_page, "files", "Files", "folder-symbolic"),
                 (self.git_page, "git", "Git", themed_icon("vcs-branch-symbolic", "media-playlist-shuffle-symbolic"))]
        if code:
            self.code_page = CodePage(self)
            pages.insert(0, (self.code_page, "code", "Code",
                             themed_icon("text-x-script-symbolic", "text-x-generic-symbolic")))
        for page, name, title, icon in pages:
            stack.add_titled_with_icon(page, name, title, icon)
        stack.connect("notify::visible-child-name", lambda *_: self.viewer_shown())
        if getattr(self, "input", None) is None:
            self.input = stack  # (what a view gives the focus back to as it closes)

    def viewer_overlays(self, child):
        widgets.load_css()
        self.schema, self.kube = SchemaView(self), KubeView(self)
        host = Gtk.Overlay(child=child)
        host.add_overlay(self.schema)
        host.add_overlay(self.kube)
        self.lightbox = widgets.Lightbox(self)  # (an image large: over all of it)
        host.add_overlay(self.lightbox)
        return host

    def image_source(self):
        """What the large image view goes through with ← and →: what's shown (Baude's: the chat, or its Feedback tab)."""
        return self.stack

    def viewer_shown(self):
        name = self.stack.get_visible_child_name()
        if name == "files":
            self.files_page.ensure()
        elif name == "git":
            self.git_page.ensure()

    def viewer_folder_changed(self):
        """Another folder to show (Record's notebook changed): the Files page's tree and the Git page's history
        read again from it (as they're shown)."""
        self.files_page.root = None
        self.git_page.chat_changed()
        self.viewer_shown()

    def find_in_code(self):
        """⌘F / Ctrl+F on the Code page: find in the file shown. False if it isn't showing."""
        if self.stack.get_visible_child_name() == "code" and self.code_page.code.get_visible():
            self.code_page.code.open_search()
            return True
        return False

    # ---- what the viewers ask of their window (Baude's names for them)
    def ref_transcript(self):
        return None  # (no chat)

    def jump_to_card(self, _card):
        pass

    def show_code(self, path, line=None, text=None, note=None):
        """A file (or a version's text) on the Code page. False if it isn't something to read here (a folder, a
        binary or very big file): its opener then opens it in the editor."""
        if text is None:
            if os.path.isdir(path):
                return False
            try:
                with open(path, "rb") as f:
                    data = f.read(CODE_LIMIT + 1)
            except OSError:
                return False
            if len(data) > CODE_LIMIT or b"\0" in data[:8192]:
                return False
            text = data.decode("utf-8", "replace")
        self.code_page.show(path, text, line, note)
        self.stack.set_visible_child_name("code")
        self.present()
        return True

    def show_folder(self, path, select=None):
        self.files_page.reveal(path, select)
        self.stack.set_visible_child_name("files")
        self.present()
        return True

    def show_git(self, rev=None, sha=None, folder=None):
        self.stack.set_visible_child_name("git")
        self.git_page.focus(rev, sha, folder)
        self.present()

    def open_item(self, item):
        """A name lookup found (lookup.items' item: a file, a branch, a Kubernetes object or context, a table, a name
        Signal looked up), where it is: its file, the Git page, the Kubernetes view, the database diagram. False if
        there's nowhere to show it."""
        entry = item.get("entry") or {}
        sources = entry.get("sources") or []
        kind = item.get("kind")
        if kind == "branch":
            self.show_git(rev=item["surface"])
        elif kind == "k8s":
            running = next((w for what, w in sources if what.endswith(" running")), None)
            if running is not None:
                context, _, namespace = running.partition(" · ")
                self.kube.open(context=context, search=item["surface"], namespace=namespace or None)
            elif entry.get("hint") == "context":
                self.kube.open(context=next((w for _what, w in sources if w), None) or item["surface"])
            elif item.get("path"):
                return self.show_code(item["path"], item.get("line"))
            else:
                self.kube.open(search=item["surface"])
            self.present()
        elif entry.get("db") or (sources and sources[0][0].startswith("Database")):
            live = (entry.get("db") or [{}])[0]
            self.schema.open(table=live.get("table") or (item["surface"] if sources[0][0] == "Database table"
                                                         else None))
            self.present()
        elif item.get("path") and os.path.exists(item["path"]):
            if os.path.isdir(item["path"]):
                return self.show_folder(item["path"])
            return self.show_code(item["path"], item.get("line"))
        else:
            return False
        return True


class ViewerWindow(Adw.Window, ViewerHost):
    """A folder's viewers in a window of their own: the Code, Files and Git pages, and the Kubernetes and Databases
    views over them (the header's buttons). One per folder (for_folder). Record shows them in its own window; this is
    for a folder's on their own."""

    _windows = {}

    @classmethod
    def for_folder(cls, folder, application=None):
        folder = os.path.realpath(folder)
        win = cls._windows.get(folder)
        if win is None:
            win = cls._windows[folder] = cls(folder, application)
        return win

    def __init__(self, folder, application=None):
        super().__init__(title=os.path.basename(folder.rstrip(os.sep)) or folder, default_width=1100,
                         default_height=780, application=application)
        self.folder = folder
        head = Adw.HeaderBar()
        self.add_viewer_pages(Adw.ViewStack())
        head.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        for icon, tip, fn in (("k8s", "Kubernetes: your clusters' objects", lambda: self.kube.open()),
                              ("db", "Databases: the running ones' tables, as a diagram", lambda: self.schema.open())):
            btn = Gtk.Button(child=RefIcon(icon), tooltip_text=tip)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, fn=fn: fn())
            head.pack_end(btn)
        tv = Adw.ToolbarView(content=self.stack)
        tv.add_top_bar(head)
        self.toasts = Adw.ToastOverlay(child=tv)
        self.set_content(self.viewer_overlays(self.toasts))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", ViewerWindow._closing)

    def viewer_folder(self):
        return self.folder

    @staticmethod
    def _closing(win):
        ViewerWindow._windows.pop(win.folder, None)
        return False

    def _key(self, _c, keyval, _code, state):
        if keyval == Gdk.KEY_f and state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
            return self.find_in_code()
        return False
