"""emissionwindow — an Emission window (Window): its tabs and bars, its profile, downloads and a page's prompts,
and the requests its profile's socket brings (emissioncontrol.Control's), on the page engine there is here (engine).
"""
import ctypes
import os
import platform
import sys
import threading
import time
import urllib.parse
import zlib

import macos  # noqa: E402  (no GTK: before it's loaded)
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402
from baudelib import emissiontools, wording  # noqa: E402  (its words, Baude's browser's: schema/copy)
from baudelib.emissionlink import (  # noqa
    DEFAULT, add_profile, blocks_tracking, call, call_bot, data_home, forget_profile, hand_over, lock_profile, open_pages,
    pending_note, profile_id, profiles, rename_profile, set_blocks_tracking, unlock_profile,
)
from emissioncore import AGENT_IDLE_S, HOME_PAGE, POLL_MS, ProfileSocket, STARTED, address, once  # noqa: E402
from emissionpointer import BotPointer  # noqa: E402
from emissionmac import Blocks, MacWeb  # noqa: E402
from emissiongtk import Chrome, GtkWeb, Tab  # noqa: E402
from emissioncontrol import Control, Watcher, sites_of  # noqa: E402


def engine():
    """(the page engine here, None) or (None, why there's none)."""
    if sys.platform == "darwin":
        try:
            major = int((platform.mac_ver()[0] or "0").split(".")[0])
        except ValueError:
            major = 0
        if major < 14:
            return None, ("Emission needs macOS 14 or later: that's where WebKit keeps a profile's cookies and logins "
                          "apart from the others'.")
        return MacWeb, None
    from baudelib import deps
    try:
        if "webkit" in deps.hidden():  # (fault injection: as if it weren't there)
            raise ImportError("webkit")
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit  # noqa: F401
        return GtkWeb, None
    except (ValueError, ImportError):
        return None, deps.say(deps.info("webkit", here=False), "bot_browser.title")


class Window(Adw.ApplicationWindow):
    # (a watched page's frames: this often while it changes, MID_MS while it changes now and then, a caret blinking
    # say, SLOW_MS once it's still; Linux's agent draws on a virtual display, in software: half as often there)
    FAST_MS, MID_MS, SLOW_MS = (16 if sys.platform == "darwin" else 33), 100, 250
    SAME_FOR_MID = 6  # (this many frames the same in a row: the middle pace)

    def __init__(self, app, url, profile, agent=False, hosted=False):
        """agent: the window of an Emission started for a Bot's tools (its own profile; never seen: on macOS it's
        kept off the screen, put in unseen only while it's watched, on Linux it's on a display of its own; a page's
        prompts wait for the tools). hosted: an agent's, in Baude's process, its pages shown in Baude's Browser page
        (body, put there): never shown itself, no shortcuts of its own, not gone when idle, not quit when asked."""
        super().__init__(application=None if hosted else app, title="Emission", default_width=1280,
                         default_height=900)
        self.hosted, self.blocked = hosted, False  # (blocked: its pages not to be clicked or typed in, but watched)
        self.open_said = None  # (its tabs, as last written down for the next browser on its profile: open_pages)
        self.kept = False  # (its tabs its profile's to write down: it has them, its last ones opened again)
        self.leaving = None  # (why it's closing, written down with its tabs: "idle", "moved")
        self.page_size = None  # (hosted: the Bot's own size for its pages, out of sight; None: 1280 by 900)
        self.fit_size = None  # (hosted, out of sight, watched: the watcher's size for its pages meanwhile)
        self.asked_at = GLib.get_monotonic_time()  # (the Bot's last request: how long it's left its browser alone)
        self.released = None  # (hosted: what lets its profile go when someone's Emission asks for it)
        self.note = None  # (what the Bot's next request is told besides that: a phone's note)
        self.reopened = None  # (and what it's told of the pages this browser opened again: from this start alone)
        self.pointer = BotPointer()  # (what its tools do with the mouse: drawn over its pages while hosted, and told
        self.accent = None           # watchers; accent: the glow's colour there, Baude's, as rgb)
        if agent:  # (one a browser before it had for the Bot and never gave)
            self.note = pending_note(profile_id(profile) or "")
            self.reopened = pending_note(profile_id(profile) or "", slot="reopen")
        self.Engine, why = engine()
        from baudelib import deps  # (WebKitGTK missing, on Linux: shown as every app shows a missing dependency)
        self.lacks = deps.missing("webkit", here=self.Engine is not None) if sys.platform != "darwin" else None
        self.profile, self.profile_id, self.tabs = profile, profile_id(profile) or add_profile(profile), {}
        self.agent, self.control, self.waiting, self.downloads = agent, Control(self), {}, []
        self.used = GLib.get_monotonic_time()
        self.switching, self.asking, self.later, self.dialogs = False, {}, [], 0  # (later: (when, what), by tick)
        self.status, self.done = None, False
        self.watchers, self.watch_source, self.watch_capturing = [], None, False
        self.watch_fast, self.watch_fast_until, self.watch_same, self.watch_size = True, 0.0, 0, None  # (watch_size:
        # the window's size before a watcher laid its page out at its own: put back once no one watches)
        self.chrome = chrome = Chrome(profile)
        self.back_btn, self.fwd_btn, self.reload_btn, self.entry = chrome.back, chrome.fwd, chrome.reload, chrome.entry
        self.who, self.progress, bar, strip, new_tab = chrome.who, chrome.progress, chrome.bar, chrome.strip, chrome.new
        self.back_btn.connect("clicked", lambda *_: self.current() and self.current().web.back())
        self.fwd_btn.connect("clicked", lambda *_: self.current() and self.current().web.forward())
        self.reload_btn.connect("clicked", lambda *_: self.current() and (
            self.current().web.stop() if self.current().last.get("loading") else self.current().web.reload()))
        self.entry.connect("activate", lambda e: self.current() and (self.current().web.load(address(e.get_text())),
                                                                      self.current().web.focus()))
        new_tab.connect("clicked", lambda *_: self.open(HOME_PAGE))
        self.view = Adw.TabView(vexpand=True)
        chrome.tab_bar.set_view(self.view)
        if hosted:  # (in Baude's window: its tabs' keys are Baude's own tabs')
            self.view.set_shortcuts(Adw.TabViewShortcuts.NONE)
        if self.Engine is MacWeb and not (agent and not hosted):  # (a wheel's and a trackpad's gestures its pages',
            macos.route_events(MacWeb.take_event)                  # and the keys of a page that has them)
        self.view.connect("notify::selected-page", lambda *_: self.selected())
        self.view.connect("close-page", self.closing)
        self.banner = Adw.Banner(button_label=wording.say("common.show"))
        self.banner.connect("button-clicked", lambda *_: self.show_download())
        self.shown_path, self.banner_gen = None, 0
        self.body = body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.banner)
        frame = Adw.ToolbarView(content=body)  # (its bar: back, forward, reload, the address, its profile)
        if not agent and not hosted:  # (a window that's seen: its pages set into the frame, as Baude's and Record's)
            import widgets
            widgets.inside(body, under_bar=True)
            frame.add_css_class("baude-frame")
        frame.add_top_bar(chrome.head)
        frame.add_top_bar(strip)
        if agent and not hosted:  # (never seen: its page the whole window, which goes as small as resize does, so a
            bar.set_visible(False)  # watcher's size is its page's, a phone's narrow one too)
            strip.set_visible(False)
            self.set_size_request(200, 200)
        if agent or hosted:
            self.set_content(frame)
        else:  # (a theme that couldn't be used says so here: themelook's)
            self.toasts = Adw.ToastOverlay(child=frame)
            self.set_content(self.toasts)
        if self.Engine is None:
            self.who.set_sensitive(False)
            new_tab.set_sensitive(False)
        else:  # (the menu made as it opens: another Emission may have changed the profiles)
            self.who.set_create_popup_func(lambda b: b.set_menu_model(self.profile_menu()))
        if self.Engine is not None and not self.have(self.profile_id):
            why = f"The profile “{profile}” is open in another Emission that isn't answering. Close it, or choose " \
                  "another profile here."
        self.show_body(why)
        for name, keys, fn in (("new-tab", ["<Meta>t", "<Control>t"], lambda: self.open(HOME_PAGE)),
                               ("close-tab", ["<Meta>w", "<Control>w"], self.close_current),
                               ("address", ["<Meta>l", "<Control>l"], self.focus_address),
                               ("reload", ["<Meta>r", "<Control>r", "F5"], lambda: self.current()
                                and self.current().web.reload())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, fn=fn: fn())
            self.add_action(act)
            if not hosted:
                app.set_accels_for_action(f"win.{name}", keys)
        if not hosted:  # (a missing dependency's Restart, here: this Emission started again as it was)
            restart = Gio.SimpleAction.new("restart", None)
            restart.connect("activate", lambda *_a: os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]]))
            self.add_action(restart)
        if self.Engine is MacWeb and not hosted:
            clicks = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            clicks.connect("pressed", lambda *_: MacWeb.keys_to_gtk(self))
            self.add_controller(clicks)
        self.connect("close-request", self.closing_window)
        if self.Engine is not None:
            if not why:
                self.restore(url)
            GLib.timeout_add(POLL_MS, self.tick)

    def restore(self, url):
        """Its profile's tabs as its last browser left them, the one in front in front again (as a browser continues
        where it left off); url opened after them, in front. Neither: a new tab (the home page; an agent's, blank). A
        Bot's next request is told which pages it has again (in new tabs, with ids of their own) and why."""
        urls, selected, why = open_pages(self.profile_id)
        tabs = [self.open(u) for u in urls]
        if url:
            self.open(url)
        elif tabs:
            self.view.set_selected_page(next(p for p, t in self.tabs.items() if t is tabs[selected]))
        else:
            self.open("about:blank" if self.agent else HOME_PAGE)
        self.kept = True
        if self.agent:  # (a new sentence each start: the Bot's told what this browser has, not every one's before)
            self.reopened = ((
                "This browser was closed after going unused for a while" if why == "idle" else
                "This browser moved into Baude" if why == "moved" else
                "This browser was restarted (Baude or its host restarted)") + (
                ": its pages were opened again from their addresses, in new tabs (what was typed in them, and their "
                "history, is gone): " + ", ".join(urls[:10]))) if urls and why != "owner" else None
            pending_note(self.profile_id, self.reopened or "", slot="reopen")

    def keep_pages(self, fresh=False):
        """Its tabs, in their order, and the one in front, written down for the next browser on its profile to open
        again (open_pages), if they've changed. fresh: their addresses asked now, not as the last tick saw them."""
        if not self.kept:
            return
        tabs = []
        for i in range(self.view.get_n_pages()):
            tab = self.tabs.get(self.view.get_nth_page(i))
            try:
                url = (tab.web.state() if fresh else tab.last).get("url") if tab is not None else None
            except Exception:  # (its page already gone: as the last tick saw it)
                url = tab.last.get("url")
            if url and url != "about:blank":
                tabs.append((tab, url))
        now = self.current()
        said = ([u for _t, u in tabs], next((i for i, (t, _u) in enumerate(tabs) if t is now), 0),
                self.leaving or (None if self.agent else "owner"))  # ("owner": a window of the owner's, no Bot's)
        if said != self.open_said:
            self.open_said = said
            open_pages(self.profile_id, said[0], why=said[2], selected=said[1])

    def free_from_agent(self, pid):
        """True if pid's profile was an agent's Emission's (a Bot's, out of sight) and now isn't: it's asked to go,
        so the profile can be opened here and seen (the Bot's tools then reach it here)."""
        try:
            if not call(pid, {"op": "info"}, timeout=3).get("agent"):
                return False
            call(pid, {"op": "quit"}, timeout=3)
        except (OSError, ValueError):
            return False
        end = GLib.get_monotonic_time() + 8_000_000
        while GLib.get_monotonic_time() < end:
            if lock_profile(pid):
                return True
            GLib.usleep(100_000)
        return False

    def give_back(self, pid):
        """A Bot's profile this window had, let go of (its socket now, its lock once WebKit's let go of its store):
        the Bot's own Emission can have it again."""
        ProfileSocket.close(pid)
        held = GtkWeb.sessions.pop(pid, None)
        if held is not None:
            GtkWeb.given_back[pid] = held  # (taken up again if this window comes back to it first)

        def let_go():
            if pid != self.profile_id and pid not in ProfileSocket.open:
                GtkWeb.given_back.pop(pid, None)
                unlock_profile(pid)
        self.later.append((GLib.get_monotonic_time() + 10_000_000, let_go))

    def have(self, pid):
        """pid's profile this process's (its lock, and its socket for others' pages), or False if another Emission
        has it."""
        if not lock_profile(pid):
            return False
        ProfileSocket.listen(pid, self.handle)
        return True

    def memory(self):
        """What this process holds, for a look at what its work leaves behind: its Python objects, the widgets, tabs
        and page engines among them (alive, shown or not), the pages WebKit has, and the processes those run in."""
        import gc
        gc.collect()
        things = gc.get_objects()
        engines = [o for o in things if isinstance(o, (MacWeb, GtkWeb))]
        pointers = [o for o in things if isinstance(o, BotPointer)]
        if sys.platform == "darwin":
            pids = {macos.objc_send(v, b"_webProcessIdentifier", ret=ctypes.c_int) for v in MacWeb.views}
        else:
            pids = set()
            for name in os.listdir("/proc"):
                try:
                    with open(f"/proc/{name}/stat") as f:
                        if name.isdigit() and int(f.read().rsplit(")", 1)[1].split()[1]) == os.getpid():
                            pids.add(int(name))
                except (OSError, ValueError, IndexError):
                    pass
        return {"pid": os.getpid(), "web_pids": sorted(p for p in pids if p and p > 0), "objects": len(things),
                "widgets": sum(isinstance(o, Gtk.Widget) for o in things),
                "tab_objects": sum(isinstance(o, Tab) for o in things), "engines": len(engines),
                "tabs": len(self.tabs), "views": len(MacWeb.views) + len(GtkWeb.views),
                "blocks": len(Blocks.live), "blocks_kept": len(Blocks.kept), "queue": len(self.control.queue),
                "pointer_steps": sum(len(getattr(p, "steps", ())) for p in pointers),
                "downloads": len(self.downloads), "threads": threading.active_count()}

    def handle(self, pid, msg, answer, conn=None):
        """A request at pid's socket: pages handed over (open), what this is (info), go (quit), or a Bot's tools'
        (Control's), for pid's profile (this window moved to it if it's elsewhere)."""
        op = msg.get("op")
        busy = bool(self.control.queue or self.waiting or any(t.last.get("loading") for t in self.tabs.values()))
        idle_s = 0.0 if busy else (GLib.get_monotonic_time() - self.asked_at) / 1e6  # (work still going: not idle)
        if op != "info":  # (asked what it is, as the host process does every few seconds: not use)
            self.used = GLib.get_monotonic_time()
        if op not in ("info", "watch", "phone_note") and not msg.get("from_pane"):
            self.asked_at = GLib.get_monotonic_time()
        if op == "open":
            self.take(pid, [u for u in msg.get("urls") or [] if isinstance(u, str)])
            return answer({"ok": True})
        if op == "show":  # (☰ → Browser: this window brought to the front as it is, no page opened)
            if not self.hosted and not self.agent and pid == self.profile_id:
                self.present()
                return answer({"ok": True})
            return answer({"error": "not a window of that profile's"})
        if op == "info":  # (hosted: not an agent's to be asked to go; its pages opened there instead: open)
            return answer({"agent": self.agent, "hosted": self.hosted, "profile": self.profile, "pid": os.getpid(),
                           "tabs": len(self.tabs), "idle_s": idle_s, "commit": STARTED})
        if op == "memory":  # (tests/bench_emission.py's: what this process holds now, counted after a collection)
            return answer(self.memory())
        if op == "phone_note":  # (its browser was on a phone till now: what the Bot's next request is told, in place
            said = str(msg.get("note") or "")  # of what a phone said before; "": that taken out)
            if pid != self.profile_id:
                return answer({"error": "that profile isn't open in this window"})
            last = getattr(self, "phone_said", "")
            now = self.note.replace(last, said) if last and self.note and last in self.note else " ".join(
                n for n in (self.note, said) if n)
            self.note, self.phone_said = " ".join(now.split()) or None, said
            pending_note(self.profile_id, self.note or "")
            return answer({"ok": True})
        if op == "quit" and self.hosted:  # (someone's Emission wants its profile: let go of, the Bot told after)
            answer({"ok": True})
            if self.released is not None:
                GLib.idle_add(lambda: self.released() and False)
            return None
        if op == "quit":
            answer({"ok": True})
            if msg.get("from_pane"):  # (Baude's taking it: the Bot's told its pages moved there)
                self.leaving = "moved"
            GLib.timeout_add(100, lambda: (self.closing_window(), self.get_application().quit()) and False)
            return None
        if self.Engine is None:
            return answer({"error": "Emission has no page engine on this host"})
        listed = next((p for p in profiles() if p["id"] == pid), None)
        own = pid == self.profile_id and not self.agent and not self.hosted and bool(msg.get("from_pane")) and \
            op in emissiontools.PANE_OPS | {"watch"}  # (this window, watched and driven from a Browser pane elsewhere:
        if not own and (listed is None or not listed.get("bot")):  # ☰ → Browser on another host; else a Bot's
            # tools reach a Bot's own profile, nothing else)
            return answer({"error": "Emission takes tools' requests for a Bot's own profile only"})
        if pid != self.profile_id:
            if not self.agent:  # (someone's own window, on another profile now: not moved for a Bot)
                return answer({"error": f"“{listed['name']}” is open in someone's Emission window, which shows "
                                        "another profile now: it's the Bot's again once that window's closed"})
            if not self.switch_profile(listed["name"]):
                return answer({"error": "that profile isn't open here"})
        if op == "watch":
            self.watchers.append(Watcher(self, conn, msg))
            if self.agent and not self.hosted and sys.platform == "darwin":  # (watched: its window in, where no one
                macos.in_unseen(self)  # sees it, so WebKit draws its pages as they change: a picture that moves)
            return self.watch_kick()
        if op == "resize" and self.agent and msg.get("restore"):  # (a watcher gone read-only: the Bot's own size
            if self.watch_size is not None and len(self.watchers) <= 1:  # back, unless another still drives it)
                self.set_default_size(*self.watch_size)
                self.watch_size = None
            if self.fit_size is not None and len(self.watchers) <= 1:
                self.fit_size = None
                for tab in self.tabs.values():
                    tab.web.follow()
            return answer({"ok": True})
        self.watch_kick()  # (something's being done: frames often for a while)
        told = " ".join(n for n in (self.reopened, self.note) if n)
        if told and not msg.get("from_pane"):  # (the first of the Bot's requests told what became of the
            # browser it had: not Baude's Browser page's)
            note, said = told, answer
            self.note = self.reopened = None
            pending_note(self.profile_id, "")
            pending_note(self.profile_id, "", slot="reopen")
            answer = lambda r: said({**r, "note": note} if isinstance(r, dict) else r)  # noqa: E731
        return self.control.run(op, msg, answer)

    # (watchers: Baude's Browser panes)

    def watch_kick(self):
        """Frames often (FAST_MS) for a while: the page may be changing."""
        self.watch_fast_until = time.monotonic() + 3
        self.watch_same = 0
        if self.watch_source is not None and self.watch_fast != self.FAST_MS:  # (timed slower: timed again, so what
            GLib.source_remove(self.watch_source)                             # was done shows at once)
            self.watch_source = None
        if self.watch_source is None and self.watchers:
            self.watch_fast = self.FAST_MS
            self.watch_source = GLib.timeout_add(self.FAST_MS, self.watch_tick)

    def unwatch(self, watcher):
        if watcher in self.watchers:
            self.watchers.remove(watcher)
            watcher.close()
        if not self.watchers and self.agent:
            if sys.platform == "darwin":
                macos.out_unseen(self)  # (no one watching: out again, its pages drawn only as the Bot's tools ask)
            if self.watch_size is not None:  # (the Bot's page at its own size again: a watcher's is gone with it)
                self.set_default_size(*self.watch_size)
                self.watch_size = None
            if self.fit_size is not None:
                self.fit_size = None
                for tab in self.tabs.values():
                    tab.web.follow()

    def watch_state(self):
        tab = self.current()
        return {"profile": self.profile, "agent": self.agent, "view": tab.web.view_size() if tab else None,
                "selected": tab.id if tab else None, "tabs": [self.control.describe(t) for t in self.tabs.values()],
                "prompts": self.prompts(None), "downloads": self.downloads[-5:],
                "now": tab.web.state() if tab is not None and tab.web.view is not None else None,
                "pointer": self.pointer.log}  # (now: the tab in front's back, forward and progress too)

    def watch_tick(self):
        """A frame of the page in front taken, and given to each watcher it's new to; its state too, if it's changed.
        Often while things happen, less after (SLOW_MS)."""
        if not self.watchers or self.done:
            self.watch_source = None
            return False
        state = self.watch_state()
        for w in list(self.watchers):
            if state != w.sent_state and w.say({"state": state}):
                w.sent_state = state
        if not self.watchers:
            self.watch_source = None
            return False
        if self.watch_capturing and time.monotonic() - self.watch_capturing > 5:
            self.watch_capturing = False  # (a frame that never came: the next tried)
        tab = self.current()
        if tab is not None and tab.web.view is not None and min(tab.web.view_size() or [0]) < 50:
            tab = None  # (not laid out yet: no frame of nothing)
        if tab is not None and tab.web.view is not None and not self.watch_capturing:
            self.watch_capturing = time.monotonic()
            want_w = max(w.max_w for w in self.watchers)
            want_h = max(w.max_h for w in self.watchers)

            def got(fw, fh, stride, data, why):
                self.watch_capturing = False
                if data is None:
                    return
                total = zlib.crc32(data)
                if total != getattr(self, "watch_last", None):  # (it's moving, an animation, a video: fast while it
                    self.watch_last = total                       # does)
                    self.watch_fast_until = max(self.watch_fast_until, time.monotonic() + 1)
                    self.watch_same = 0
                else:
                    self.watch_same += 1
                for w in list(self.watchers):
                    w.offer(fw, fh, stride, data, total)
            try:
                tab.web.frame(want_w, want_h, got)
            except Exception:  # (no frame this time: the next one tried)
                self.watch_capturing = False
                sys.excepthook(*sys.exc_info())
        pace = self.SLOW_MS if time.monotonic() >= self.watch_fast_until else \
            self.MID_MS if self.watch_same >= self.SAME_FOR_MID else self.FAST_MS
        if pace != self.watch_fast:  # (its pace changed: timed again)
            self.watch_fast = pace
            self.watch_source = GLib.timeout_add(pace, self.watch_tick)
            return False
        return True

    def take(self, pid, urls):
        """Pages handed over by another Emission, for pid's profile (this window moved to it if it's elsewhere)."""
        name = next((p["name"] for p in profiles() if p["id"] == pid), None)
        if name is None or (pid != self.profile_id and not self.switch_profile(name)):
            return
        for url in urls or [HOME_PAGE]:
            self.open(url)
        if not self.hosted:
            self.present()

    def show_body(self, why):
        """The pages, or (why: there can't be any) a status page saying why."""
        if why:
            if self.status is None:
                if self.view.get_parent() is not None:
                    self.body.remove(self.view)
                if self.lacks is not None:
                    import depsview
                    self.status = depsview.Missing()
                    self.status.show(self.lacks, "bot_browser.title")
                else:
                    self.status = Adw.StatusPage(icon_name="web-browser-symbolic", title="Can't show pages",
                                                 vexpand=True)
                self.body.append(self.status)
            if isinstance(self.status, Adw.StatusPage):
                self.status.set_description(why)
        elif self.status is not None or self.view.get_parent() is None:
            if self.status is not None:
                self.body.remove(self.status)
                self.status = None
            self.body.append(self.view)

    # (profiles)

    def profile_menu(self):
        menu, group = Gio.Menu(), Gio.SimpleActionGroup()
        listed = profiles()
        self.profile = next((p["name"] for p in listed if p["id"] == self.profile_id), self.profile)  # (renamed)
        self.who.set_label(self.profile)
        people, bot_section = Gio.Menu(), Gio.Menu()  # (the Bots' profiles apart, under their heading)
        for i, p in enumerate(listed):
            act = Gio.SimpleAction.new(f"p{i}", None)
            act.connect("activate", lambda *_a, n=p["name"]: self.switch_profile(n))
            group.add_action(act)
            (bot_section if p.get("bot") else people).append(p["name"] + ("  ✓" if p["name"] == self.profile else ""),
                                                             f"who.p{i}")
        menu.append_section(None, people)
        if bot_section.get_n_items():
            menu.append_section(wording.say("common.bots"), bot_section)
        more = Gio.Menu()
        bots = [p for p in listed if p.get("bot") and p["id"] != self.profile_id]
        mine = next((p for p in listed if p["id"] == self.profile_id), {})
        for name, label, fn, on in (("add", "New Profile…", self.ask_profile, True),
                                    ("copy", "Copy Sign-ins to a Bot…", self.ask_copy, bool(bots) and not mine.get("bot")),
                                    ("rename", "Rename Profile…", self.ask_rename, self.profile != DEFAULT
                                     and not mine.get("bot")),
                                    ("remove", "Remove Profile…", self.ask_remove, self.profile != DEFAULT)):
            act = Gio.SimpleAction.new(name, None)
            act.set_enabled(on)
            act.connect("activate", lambda *_a, fn=fn: fn())
            group.add_action(act)
            more.append(label, f"who.{name}")
        menu.append_section(None, more)
        blocked = blocks_tracking(self.profile_id)
        tracking = Gio.SimpleAction.new("tracking", None)
        tracking.connect("activate", lambda *_a: self.block_tracking(not blocked))
        group.add_action(tracking)
        privacy = Gio.Menu()
        privacy.append("Prevent Cross-Site Tracking" + ("  ✓" if blocked else ""), "who.tracking")
        menu.append_section(None, privacy)
        self.insert_action_group("who", group)
        return menu

    def block_tracking(self, on):
        """This profile's pages blocking cross-site cookies and storage (on), as Safari does, or not: from now on."""
        set_blocks_tracking(self.profile_id, on)
        if self.Engine is not None:
            self.Engine.block_tracking(self.profile_id, on)

    def refresh_profiles(self):
        self.who.set_menu_model(self.profile_menu())

    def ask_name(self, heading, body, verb, start, then):
        entry = Gtk.Entry(placeholder_text=wording.say("common.name"), activates_default=True, text=start)
        dlg = Adw.AlertDialog(heading=heading, body=body, extra_child=entry, default_response="ok",
                              close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("ok", verb)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, r):
            name = entry.get_text().strip()
            if r != "ok" or not name:
                return
            if profile_id(name) is not None:
                self.say("Name already in use", f"Choose a name other than “{name}”.")
                return
            then(name)
        dlg.connect("response", once(answered))
        self.present_dialog(dlg)

    def ask_profile(self):
        self.ask_name("New Profile", "A profile keeps its own cookies and logins: sign in once, and it stays signed "
                      "in.", wording.say("common.add"), "",
                      lambda name: (add_profile(name), self.switch_profile(name)))

    def ask_copy(self):
        """Sign-ins (the cookies of sites chosen) copied from this profile into a Bot's own: it's signed in there as
        this profile is."""
        bots = [p for p in profiles() if p.get("bot") and p["id"] != self.profile_id]

        def got(cookies, why):
            if why:
                self.say("Couldn't read the sign-ins", why)
                return
            sites = sites_of(cookies)
            if not sites:
                self.say("No sign-ins yet", "Sign in to some sites in this profile first.")
                return
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            to = Gtk.DropDown.new_from_strings([b["name"] for b in bots])
            box.append(to)
            checks = []
            scroller = Gtk.ScrolledWindow(min_content_height=min(240, 30 * len(sites)), propagate_natural_height=True)
            listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for site in sites:
                c = Gtk.CheckButton(label=site)
                checks.append(c)
                listing.append(c)
            scroller.set_child(listing)
            box.append(scroller)
            dlg = Adw.AlertDialog(heading="Copy Sign-ins to a Bot", body=f"Copy your sign-ins for the chosen sites "
                                  f"from “{self.profile}” to the Bot.", extra_child=box,
                                  default_response="copy", close_response="cancel")
            dlg.add_response("cancel", wording.say("common.cancel"))
            dlg.add_response("copy", wording.say("common.copy"))
            dlg.set_response_appearance("copy", Adw.ResponseAppearance.SUGGESTED)

            def answered(_d, r):
                chosen = {c.get_label() for c in checks if c.get_active()}
                if r != "copy" or not chosen:
                    return
                bot = bots[to.get_selected()]
                picked = [c for c in cookies if set(sites_of([c])) & chosen]
                self.copy_cookies(bot, picked, sorted(chosen))
            dlg.connect("response", once(answered))
            self.present_dialog(dlg)
        self.Engine.cookies(self.profile_id, got)

    def copy_cookies(self, bot, cookies, sites):
        """cookies given to bot's own Emission (started for it, out of sight, if it isn't running: its store's open
        in one process only, its)."""
        def done(n, why):
            if why:
                self.say("Couldn't copy the sign-ins", why)
            else:
                self.say("Sign-ins copied", f"“{bot['name']}” is now signed in to {', '.join(sites)}.")
        if bot["id"] == self.profile_id or bot["id"] in ProfileSocket.open:  # (it's this window's, or this process's)
            self.Engine.add_cookies(bot["id"], cookies, done)
            return

        def over_there():
            try:
                got = call_bot(bot["bot"], None, {"op": "add_cookies", "cookies": cookies}, timeout=30)
                GLib.idle_add(lambda: done(got.get("added", 0), None) and False)
            except (OSError, ValueError) as e:
                why = str(e)  # (e's gone once this block's done: the idle call's after)
                GLib.idle_add(lambda: done(None, why) and False)
        threading.Thread(target=over_there, daemon=True).start()

    def ask_rename(self):
        def rename(name):
            rename_profile(self.profile, name)
            self.profile = name
            self.refresh_profiles()
        self.ask_name("Rename Profile", "Its cookies and logins stay with it.", wording.say("common.rename"),
                      self.profile, rename)

    def ask_remove(self):
        name = self.profile
        dlg = Adw.AlertDialog(heading=f"Remove “{name}”?",
                              body="Its cookies, logins and the sites' data it keeps are deleted. This can't be undone.",
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("remove", wording.say("common.remove"))
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", once(lambda _d, r: r == "remove" and self.remove_profile(name)))
        self.present_dialog(dlg)

    def remove_profile(self, name):
        """name's profile gone, its data deleted: this window moved to Default first (its pages let go of it)."""
        pid = profile_id(name)
        if pid is None:
            return
        if self.profile == name and not self.switch_profile(DEFAULT):
            return
        if not self.have(pid) and not (self.free_from_agent(pid) and self.have(pid)):
            self.say(f"“{name}” is open elsewhere", "Close its window and try again.")
            return

        tries = [15]

        def removed(why):
            if why and self.Engine is MacWeb and tries[0] > 0:  # (WebKit lets go of a store a moment after its pages)
                tries[0] -= 1
                self.later.append((GLib.get_monotonic_time() + 1_000_000, lambda: MacWeb.remove_store(pid, removed)))
                return
            if why:
                if pid != self.profile_id:
                    ProfileSocket.close(pid)
                    unlock_profile(pid)
                self.say(f"Couldn't remove “{name}”", why)
                return
            forget_profile(name)
            open_pages(pid, [])
            try:
                os.unlink(os.path.join(data_home(), "profiles", pid + ".lock"))
            except OSError:
                pass
            ProfileSocket.close(pid)
            unlock_profile(pid)
            self.refresh_profiles()
        self.Engine.remove_store(pid, removed)

    def switch_profile(self, name):
        """The tabs of another profile (its own, as it left them; its cookies: a page signed in there is again there).
        The tabs this window has go to the profile it leaves. False if that profile's open in another Emission."""
        if name == self.profile:
            return True
        pid = profile_id(name)
        if pid is None:
            self.say("Profile not found", f"“{name}” no longer exists.")
            return False
        if not self.have(pid) and not (self.free_from_agent(pid) and self.have(pid)):
            urls = [t.last.get("url") or HOME_PAGE for t in self.tabs.values()]
            if hand_over(pid, urls):
                self.say(f"Opened in “{name}”", f"“{name}” is already open, so the pages went there.")
            else:
                self.say(f"“{name}” isn't responding", "It's open in another Emission window.")
            return False
        self.keep_pages()  # (the profile it leaves: its tabs as they were, for when it's opened again)
        self.switching = True
        try:
            for page in list(self.tabs):
                self.view.close_page(page)
        finally:
            self.switching = False
        old = self.profile_id
        self.profile, self.profile_id, self.open_said = name, pid, None
        self.refresh_profiles()
        self.show_body(None)
        if not self.agent and any(p.get("bot") and p["id"] == old for p in profiles()):
            self.give_back(old)
        urls, selected, _why = open_pages(pid)  # (the profile's own tabs, not the ones this window had)
        tabs = [self.open(u) for u in urls]
        if tabs:
            self.view.set_selected_page(next(p for p, t in self.tabs.items() if t is tabs[selected]))
        else:
            self.open("about:blank" if self.agent else HOME_PAGE)
        return True

    # (asking the user: drawn by GTK in the window, so a macOS page, a view laid over it, is hidden meanwhile)

    def present_dialog(self, dlg, web=None):
        """dlg shown; web: the page it's for (its tab closed, the dialog's closed too: answered as cancelled)."""
        self.dialogs += 1
        self.show_pages()
        if web is not None:
            self.asking.setdefault(web, []).append(dlg)

        def closed(*_):
            self.dialogs -= 1
            if web is not None and dlg in self.asking.get(web, ()):
                self.asking[web].remove(dlg)
            self.show_pages()
        dlg.connect("closed", closed)
        dlg.present(self)

    def say(self, heading, body):
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("ok", wording.say("common.ok"))
        self.present_dialog(dlg)

    def host_of(self, web):
        url = web.state().get("url") or ""
        return urllib.parse.urlsplit(url).hostname or "This page"

    def waits(self, web, kind, answer, **about):
        """A page's prompt kept for the tools (prompts, answer_prompt): answer(msg) given what they say. Its id."""
        tab = next((t for t in self.tabs.values() if t.web is web), None)
        pid = f"prompt{len(self.waiting) + 1}-{GLib.get_monotonic_time() % 100000}"
        self.waiting[pid] = {"about": {"id": pid, "tab": tab.id if tab else None, "kind": kind, **about},
                             "answer": answer, "web": web}
        return pid

    def done_waiting(self, pid):
        self.waiting.pop(pid, None)

    def prompts(self, tab):
        """What's waiting on an answer (tab's only, if it's given)."""
        return [w["about"] for w in self.waiting.values() if tab is None or w["about"]["tab"] == tab.id]

    def answer_prompt(self, msg):
        w = self.waiting.pop(str(msg.get("id") or ""), None) or (
            self.waiting.pop(next(iter(self.waiting)), None) if len(self.waiting) == 1 and not msg.get("id") else None)
        if w is None:
            raise ValueError("no such prompt waiting: prompts lists them")
        w["answer"](msg)
        for dlg in self.asking.get(w["web"], [])[:]:
            if getattr(dlg, "prompt_id", None) == w["about"]["id"]:
                dlg.force_close()

    def page_dialog(self, web, kind, message, text, finish):
        """A page's alert, confirm or prompt: finish(None for an alert; True or False; the text, or None)."""
        pid = self.waits(web, kind, lambda m: finish(None if kind == "alert" else bool(m.get("accept", True))
                                                    if kind == "confirm" else
                                                    (str(m.get("text") or "") if m.get("accept", True) else None)),
                         message=message, **({"default": text or ""} if kind == "prompt" else {}))
        if self.agent:
            return
        dlg = Adw.AlertDialog(heading=f"{self.host_of(web)} says", body=message, close_response="cancel")
        entry = None
        if kind == "prompt":
            entry = Gtk.Entry(text=text or "", activates_default=True)
            dlg.set_extra_child(entry)
        if kind != "alert":
            dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("ok", wording.say("common.ok"))
        dlg.set_default_response("ok")
        if kind == "alert":
            dlg.set_close_response("ok")
        dlg.prompt_id = pid
        dlg.connect("response", lambda _d, r: (self.done_waiting(pid), finish(
            None if kind == "alert" else (r == "ok") if kind == "confirm" else
            (entry.get_text() if r == "ok" else None))))
        self.present_dialog(dlg, web)

    def sign_in(self, web, host, realm, failed_before, answer):
        """A site's own sign-in (HTTP authentication): answer(user, password), or (None, None). Kept for the session
        only."""
        pid = self.waits(web, "sign_in", lambda m: answer(str(m.get("user") or ""), str(m.get("password") or ""))
                         if m.get("accept", True) else answer(None, None),
                         host=host, realm=realm, failed_before=bool(failed_before))
        if self.agent:
            return pid
        user = Gtk.Entry(placeholder_text=wording.say("browser.user_name"), activates_default=True)
        password = Gtk.PasswordEntry(placeholder_text=wording.say("browser.password"), activates_default=True,
                                     show_peek_icon=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(user)
        box.append(password)
        body = wording.say("browser.sign_in_realm", host=host, realm=realm) if realm else \
            wording.say("browser.sign_in", host=host)
        if failed_before:
            body = "That didn't work. " + body
        dlg = Adw.AlertDialog(heading="Sign In", body=body, extra_child=box, default_response="ok",
                              close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("ok", "Sign In")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.prompt_id = pid
        dlg.connect("response", lambda _d, r: (self.done_waiting(pid), answer(user.get_text(), password.get_text())
                                               if r == "ok" else answer(None, None)))
        self.present_dialog(dlg, web)
        return pid

    def choose_files(self, multiple, folders, finish, web=None):
        """Files for a page's file input: finish([paths]) or finish(None)."""
        if web is not None:
            pid = self.waits(web, "files", lambda m: finish([str(f) for f in m.get("files") or [] if os.path.exists(
                str(f))] or None if m.get("accept", True) else None), multiple=bool(multiple), folders=bool(folders))
            if self.agent:
                return
            finish = (lambda f, pid=pid: lambda paths: (self.done_waiting(pid), f(paths)))(finish)
        dialog = Gtk.FileDialog(modal=True)

        def done(d, result):
            try:
                if folders and multiple:
                    files = list(d.select_multiple_folders_finish(result))
                elif folders:
                    files = [d.select_folder_finish(result)]
                elif multiple:
                    files = list(d.open_multiple_finish(result))
                else:
                    files = [d.open_finish(result)]
            except GLib.Error:
                files = []
            finish([f.get_path() for f in files if f is not None and f.get_path()] or None)
        if folders:
            (dialog.select_multiple_folders if multiple else dialog.select_folder)(self, None, done)
        else:
            (dialog.open_multiple if multiple else dialog.open)(self, None, done)

    def saved(self, path, why):
        """A download finished (or why it didn't): said in the banner for a while, and kept for the tools."""
        name = os.path.basename(path)
        self.downloads.append({"name": name, "path": None if why else path, **({"error": why} if why else {})})
        self.banner.set_title(f"Couldn't download {name}: {why}" if why else f"Saved {name} to Downloads")
        self.banner.set_button_label(None if why else wording.say("common.show"))
        self.shown_path = None if why else path
        self.banner.set_revealed(True)
        self.banner_gen += 1
        gen = self.banner_gen
        GLib.timeout_add_seconds(8, lambda: gen == self.banner_gen and self.banner.set_revealed(False) and False)

    def show_download(self):
        if self.shown_path:
            Gtk.FileLauncher(file=Gio.File.new_for_path(self.shown_path)).open_containing_folder(self, None, None)
        self.banner.set_revealed(False)

    # (tabs)

    def focus_address(self):
        if self.Engine is MacWeb:
            MacWeb.keys_to_gtk(self)
        self.entry.grab_focus()

    def open(self, url, opener=None):
        tab = Tab(self, url, opener)
        page = self.view.append(tab.place)
        page.set_title(wording.say("browser.untitled_tab"))
        self.tabs[page] = tab
        self.view.set_selected_page(page)
        return tab

    def current(self):
        page = self.view.get_selected_page()
        return self.tabs.get(page) if page is not None else None

    def show_pages(self):
        """The selected tab's page shown, the others hidden (all of them while a dialog's up: on macOS a page is
        drawn over the window, dialogs included)."""
        for page, tab in self.tabs.items():
            tab.web.show(page is self.view.get_selected_page() and not self.dialogs)

    def selected(self):
        self.show_pages()
        if (tab := self.current()) is not None:
            self.show_state(tab, tab.last, force=True)

    def closing(self, view, page):
        tab = self.tabs.pop(page, None)
        if tab is not None:
            for dlg in self.asking.pop(tab.web, []):
                dlg.force_close()
            for pid in [k for k, w in self.waiting.items() if w["web"] is tab.web]:
                w = self.waiting.pop(pid, None)
                try:
                    w["answer"]({"accept": False})
                except Exception:  # (its page gone with it: nothing more to tell)
                    sys.excepthook(*sys.exc_info())
            tab.web.close()
            self.control.scales.pop(tab.id, None)  # (what the Bot's tools kept of it: closed by hand, too)
            self.control.reads.pop(tab.id, None)
        view.close_page_finish(page, True)
        if not self.tabs and not self.switching:
            if self.hosted:  # (in Baude, its last tab closed: a blank one in its place, as close_tab leaves)
                self.open("about:blank")
            else:
                self.close()
        return True

    def closing_window(self, *_):
        if not self.done:  # (its tabs as it closes, for its profile's next browser; its last one closed: none)
            self.keep_pages(fresh=True)
        for w in list(self.watchers):
            self.unwatch(w)
        for tab in self.tabs.values():
            tab.web.close()
        self.tabs.clear()
        self.done = True
        return False

    def close_tab(self, tab):
        for page, t in self.tabs.items():
            if t is tab:
                self.view.close_page(page)
                break

    def close_current(self):
        if (page := self.view.get_selected_page()) is not None:
            self.view.close_page(page)

    def tick(self):
        if self.done:
            return False
        if self.watchers:
            self.used = GLib.get_monotonic_time()  # (watched: in use)
        if self.agent and not self.hosted and (GLib.get_monotonic_time() - self.used) / 1e6 > AGENT_IDLE_S and \
                not self.waiting:
            self.leaving = "idle"
            self.closing_window()
            self.get_application().quit()
            return False
        if self.later:
            now = GLib.get_monotonic_time()
            due = [fn for when, fn in self.later if when <= now]
            self.later = [(when, fn) for when, fn in self.later if when > now]
            for fn in due:
                fn()
        for page, tab in list(self.tabs.items()):
            tab.web.follow()
            now = tab.web.state()
            if now != tab.last:
                page.set_title(now["title"] or now["url"] or wording.say("browser.untitled_tab"))
                page.set_loading(now["loading"])
                if tab is self.current():
                    self.show_state(tab, now)
                tab.last = now
        self.keep_pages()
        return True

    def typing(self):
        if not self.entry.get_delegate().has_focus():
            return False
        return self.Engine is not MacWeb or MacWeb.gtk_has_keys(self)

    def show_state(self, tab, now, force=False):
        if not now:
            return
        self.chrome.show(now, force or not self.typing())
        self.set_title(f"{now.get('title') or 'Emission'} — {self.profile}")
