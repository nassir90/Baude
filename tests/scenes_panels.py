"""scenes_panels — tests/screenshots.py's scenes of the network (hosts and their sessions), the sidebar's folders
and live sessions, background tasks, the side panel's pages (processes, subagents, side questions, artifacts), Send
Feedback and the Feedback tab, models, context, width and zoom.
"""
import json
import os
import shutil
import sys
import time

import fixes_fixture  # noqa: E402
import test_baude as T  # noqa: E402  (sets up the throwaway env: fake claude, temp config dirs)
from test_baude import Gdk, GLib, Gtk, baude  # noqa: E402
from baudelib import wording  # noqa: E402

from scenelib import H, HERE, SIDEBAR, W, age, opts, scene, sidebar_row, sidebar_sessions


# ---------------------------------------------------------------- network --

NET = {}


def network_fixture():
    """Servers as Baude finds them on the network (baudelib.server, here on loopback, running the fake claude, each
    saying it's on a machine of its own): lab-mac, there, with sessions in the Lab and Planning groups (one asking
    whether it may run a command); and build-box, with sessions in Lab, gone once Baude has seen them (dead). Made once,
    for every scene that wants it."""
    if NET:
        return NET
    import threading
    from baudelib.server import Server

    class Server(Server):  # (another machine's: its own name, not this one's, so not listed as This Mac's)
        def _info(self):
            return {**super()._info(), "host": f"{self.me['name']}.lan"}
    net = baude.network()
    env = {"FAKE_CLAUDE_SESSION_IDS": "1", "FAKE_CLAUDE_TRANSCRIPTS": "1"}  # (its sessions' ids and transcripts)
    parser, site = (os.path.join(T.TMP, "net", name) for name in ("parser", "site"))
    for d in (parser, site):
        os.makedirs(d, exist_ok=True)
    servers = {}
    for name in ("lab-mac", "build-box"):  # (each on a socket of its own: reached as another host's is)
        s = Server(state_dir=os.path.join(T.TMP, "servers", name), group="Lab", name=name,
                   socket_path=os.path.join(os.environ["BAUDE_RUNTIME_DIR"], f"{name}.sock"), log=lambda _text: None,
                   env=env)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers[name] = (s, net.add({"via": "unix", "path": s.socket_path}))
    started = {}
    for name, params, answer in [
            ("build-box", {"prompt": "Run the nightly benchmarks", "cwd": parser}, True),
            ("build-box", {"prompt": "Bisect the flaky login test", "cwd": site}, True),
            ("lab-mac", {"prompt": "Plan the Q1 roadmap", "cwd": site, "group": "Planning"}, True),
            ("lab-mac", {"prompt": "Write the release notes for 2.4", "cwd": site}, True),
            ("lab-mac", {"prompt": "Speed up the tokenizer's hot loop", "cwd": parser, "model": "opus",
                         "effort": "high"}, True),
            ("lab-mac", {"prompt": "List the files in the build folder", "cwd": parser}, False)]:
        server = servers[name][0]
        s = server.start(params)
        started[params["prompt"]] = s.id
        T.wait_for(lambda: s.asks, f"{params['prompt']}: asking")
        if answer:
            with server.lock:
                rid, ask = next(iter(s.asks.items()))
                server.take_input(None, s, {"type": "control_response", "response": {
                    "subtype": "success", "request_id": rid,
                    "response": {"behavior": "allow", "updatedInput": ask["request"]["input"]}}})
            T.wait_for(lambda: s.state() == "idle", f"{params['prompt']}: done")
        time.sleep(0.05)  # (in order: each said a little after the one before)
    box, box_r = servers["build-box"]
    T.wait_for(lambda: len([x for x in net.sessions() if not x["server"].mine]) == 6 and all(  # (this machine's:
        # a draft an earlier scene's window left, say, not counted)
        x.get("size") for x in net.sessions() if x["server"] is box_r), "the network's sessions")
    for sid in box_r.sessions:
        box_r.sync(sid)  # (what Baude had of them, from before it went)
    box.shutdown()
    T.wait_for(lambda: box_r.state == "dead", "build-box gone", 20)
    NET.update(net=net, lab=servers["lab-mac"], box=servers["build-box"], ids=started, folders=(parser, site))
    return NET


def network_window(app, group="Lab", sid=None, host="lab"):
    """A window listing a group on the network (⋮ → Groups; None: every group), on a host (New session's: lab, lab-mac;
    None: this machine), in Planning, sid: the session waited for."""
    network_fixture()
    baude.save_settings(new_session_host=baude.host_key(NET[host][1]) if host else None,
                        sidebar_groups=[group] if group else None)
    win = app.window(opts(cwd=NET["folders"][0]))
    if sid is None and host == "lab":
        T.wait_for(lambda: sidebar_row(win, "Write the release notes for 2.4") is not None, "the network's sessions")
    elif sid is None:
        T.pump(0.5)
    else:
        T.wait_for(lambda: session_row(win, sid) is not None, "the session")
    return win


def session_row(win, sid):
    for i in range(200):
        row = win.sidebar.list.get_row_at_index(i)
        if row is None:
            return None
        if getattr(row, "info", {}).get("id") == sid:
            return row


def subagent_session():
    """A session on lab-mac, in Planning, that had a subagent count the tests in the background: the subagent's own
    transcript kept beside the session's, as claude keeps it."""
    if "agent" in NET:
        return NET["agent"]
    network_fixture()
    server = NET["lab"][0]
    s = server.start({"prompt": "Have a SUBAGENT count the test functions", "title": "Count the test functions",
                      "cwd": NET["folders"][0], "group": "Planning"})
    T.wait_for(lambda: s.transcript() and "subagent counted" in open(s.transcript()).read(), "its subagent done", 15)
    NET["agent"] = s.id
    return s.id


def open_network_session(win, prompt):
    win.sidebar.on_activate(win.sidebar.list, sidebar_row(win, prompt))
    T.wait_for(lambda: win.remote is not None and win.session_id == NET["ids"][prompt] and win.proc is not None,
               f"{prompt}: opened")
    T.pump(0.3)


def models_server():
    """A server that can run more than Claude's models, as this machine can't: gpu-mac, in a process of its own with
    stand-ins for apple-claude and pi-claude (the fake claude) and two of pi's models said to be downloaded; given to
    Baude by address. Its session: "Sketch the release checklist", on Claude's model."""
    if "gpu" in NET:
        return NET["gpu"]
    import subprocess
    network_fixture()
    d = os.path.join(T.TMP, "gpu-mac")
    hub = os.path.join(d, "hf")
    for repo in ("mlx-community--Qwen3-8B-4bit", "mlx-community--Qwen3-4B-Instruct-2507-4bit"):
        os.makedirs(os.path.join(hub, "models--" + repo), exist_ok=True)
    fake = os.path.join(T.HERE, "fake_claude.py")
    for name, body in (("apple", f"exec env -u FAKE_CLAUDE_TRANSCRIPTS {sys.executable} {fake} \"$@\""),
                       ("pi", f"exec env -u FAKE_CLAUDE_TRANSCRIPTS {sys.executable} {fake} \"$@\""), ("agent", "exit 0")):
        with open(os.path.join(d, name), "w") as f:
            f.write(f"#!/bin/sh\n{body}\n")
        os.chmod(os.path.join(d, name), 0o755)
    env = {**os.environ, "BAUDE_APPLE_CLAUDE": os.path.join(d, "apple"), "BAUDE_PI_CLAUDE": os.path.join(d, "pi"),
           "BAUDE_PI": os.path.join(d, "agent"), "HF_HUB_CACHE": hub, "FAKE_CLAUDE_SESSION_IDS": "1",
           "FAKE_CLAUDE_TRANSCRIPTS": "1"}
    proc = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(T.HERE), "baude_headless.py"), "serve",
                             "--group", "Lab", "--name", "gpu-mac", "--state", os.path.join(d, "state"),
                             "--socket", os.path.join(os.environ["BAUDE_RUNTIME_DIR"], "gpu-mac.sock")],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
    import atexit
    atexit.register(proc.terminate)  # (it goes with this run, its claudes with it)
    r = baude.network().add({"via": "unix", "path": json.loads(proc.stdout.readline())["socket"]})
    T.wait_for(lambda: r.state == "alive", "gpu-mac there")
    got = []
    r.request({"op": "start", "prompt": "Sketch the release checklist", "cwd": NET["folders"][1], "model": "opus"},
              got.append)
    T.wait_for(lambda: got and (r.sessions.get(got[0]["session"]["id"]) or {}).get("state") == "waiting", "its session")
    NET["gpu"] = (proc, r, got[0]["session"]["id"])
    return NET["gpu"]


def popup_choices(dropdown):
    """A ChoiceDropDown's list, open (a picture of what it offers)."""
    next(w for w in T.walk(dropdown) if isinstance(w, Gtk.Popover)).popup()


@scene("56-host-older", "This Mac's host process, which runs Baude's chats, older than Baude after an update: said, with "
                       "Restart Daemon (once nothing's at a turn there: its sessions resume as they're next written to)")
def _(app):
    win = app.window()
    r = baude.local_host()
    r.info = {**r.info, "commit": baude.update.git(baude.update.HERE, "rev-parse", "HEAD~1", timeout=5)}  # (as one
    # started before the update says)
    baude._HOST_APART.clear()
    baude.baudelib.client.APART_S = 0  # (said at once, not after an update's time to be picked up)
    win.check_host_version()
    T.wait_for(lambda: win.banner.get_revealed(), "said")
    T.pump(0.5)
    return win


def offer_update(app, result=("0123456789", None), busy=False):
    """A new version's there (or why it can't be restarted onto, or Update clicked and being checked), as a look at git
    with nothing applied finds it: in this app's updater."""
    u = app.app.updater
    if u is None:
        u = app.app.updater = baude.update.Updater("baude", app.app.idle, app.app.restart,
                                                   blocked=app.app.update_waiting)
    u.seen(result)
    u.busy = busy
    u.changed()


def update_scene(app, result, busy=False):
    sidebar_sessions(T.TMP)
    win = app.window()
    offer_update(app, result, busy)
    T.wait_for(lambda: win.sidebar.update_bar.get_visible(), "Update bar shown")
    T.pump(0.3)
    return win


@scene("56c-update-offered", "A newer version there, Auto restart on update off: Update at the foot of the sessions list, "
                            "restarted onto only when it's clicked")
def _(app):
    return update_scene(app, ("0123456789", None))


@scene("56d-update-second-window", "A newer version offered, then a second window opened: Update at the foot of its "
                                   "sessions list too")
def _(app):
    sidebar_sessions(T.TMP)
    first = app.window()
    offer_update(app)
    T.wait_for(lambda: first.sidebar.update_bar.get_visible(), "Update shown")
    second = app.window()
    T.wait_for(lambda: second.sidebar.update_bar.get_visible(), "Update shown in the second window")
    T.pump(0.3)
    return second


@scene("56e-update-checking", "Update clicked: a spinner on the button while the new version is fetched in and checked")
def _(app):
    return update_scene(app, ("0123456789", None), busy=True)


@scene("56f-update-dirty", "A newer version there, something tracked changed in the checkout: why, in place of Update")
def _(app):
    return update_scene(app, (None, "dirty"))


@scene("56g-update-diverged", "A newer version there that won't fast-forward: why, in place of Update")
def _(app):
    return update_scene(app, (None, "diverged"))


@scene("56h-update-broken", "A newer version that didn't start: why, in place of Update")
def _(app):
    return update_scene(app, (None, "broken"))


RESTART_BLOCKERS = [{"kind": "chat", "session": "11111111-0000-4000-8000-000000000001", "name": "Refactor settings",
                     "what": "npm test -- --watch=false"},
                    {"kind": "task", "session": "22222222-0000-4000-8000-000000000002", "task": "t1", "run": "r1",
                     "name": "Nightly deploy", "what": "a task's run"},
                    {"kind": "agent", "session": "33333333-0000-4000-8000-000000000003", "host": "lab-mac-id",
                     "name": "apple-foundation", "what": "Running apple-foundation for a chat on lab-mac"},
                    {"kind": "terminal", "terminal": "abc123", "name": "zsh", "what": "/bin/zsh -l"}]


def _restart_pending(app, paused):
    """This Mac's host process (the run's own, in this process) keeping a restart that a chat running a command and a
    task's run hold up (RESTART_BLOCKERS, as its blockers say them), and paused if paused: as the heads hear it."""
    sidebar_sessions(T.TMP)
    win = app.window()
    srv = baude.baudelib.hosts._HERE[0]
    srv.blockers = lambda: RESTART_BLOCKERS
    if paused:
        srv.restart_holds.add("a head")
    srv.restart_when_free("head")
    baude.meshui._TOASTED.clear()  # (toasted in a scene before: this one's is new)
    win.get_application().show_update_bars()
    T.wait_for(lambda: win.sidebar.restart_toasts.shown, "the restart toasted")
    T.pump(0.3)
    return win


@scene("56d-daemon-restart-pending", "Restart Daemon with a chat running a command and a task running: the daemon "
                                     "keeps the restart, toasted with Details till they're done")
def _(app):
    return _restart_pending(app, False)


@scene("56e-daemon-restart-blockers", "Details on that toast: what the restart waits for, the restart paused meanwhile "
                                      "(every app says so), with Restart (once they're done) and Force Restart (now)")
def _(app):
    win = _restart_pending(app, False)
    toast = next(iter(win.sidebar.restart_toasts.shown.values()))
    toast.emit("button-clicked")
    T.wait_for(lambda: isinstance(win.get_visible_dialog(), baude.Adw.AlertDialog), "Waiting For")
    T.pump(0.5)
    return win


@scene("55-network-group-menu", "⋮ beside the search: Group by, then which groups are listed: each "
                                "group the sessions are in. Here lab-mac's (New session's host), in Lab")
def _(app):
    win = network_window(app)
    win.sidebar.view_btn.popup()
    return win


@scene("55b-network-group", "A group on lab-mac, New session's host: its sessions there, by folder")
def _(app):
    return network_window(app)


@scene("55c-network-session-asking", "A session on lab-mac, opened: its transcript, fetched; what it's asking, to "
                                     "answer here; written in at once, though it's at a turn there (a host's session "
                                     "takes several heads)")
def _(app):
    win = network_window(app)
    open_network_session(win, "List the files in the build folder")
    T.wait_for(lambda: T.button(win, "Allow"), "its question")
    return win


@scene("55d-network-session-steered", "Allowed, and written to: Baude takes it up on lab-mac, and what it does comes "
                                      "as it's done")
def _(app):
    win = network_window(app)
    open_network_session(win, "List the files in the build folder")
    if T.button(win, "Allow"):
        T.button(win, "Allow").emit("clicked")
    T.wait_for(lambda: (NET["lab"][1].sessions.get(win.session_id) or {}).get("state") == "idle", "its turn over")
    T.wait_for(lambda: not win.live_elsewhere(), "the banner gone")
    T.type_and_send(win, "Now count them")
    T.wait_for(lambda: T.button(win, "Allow"), "asked again")
    T.button(win, "Allow").emit("clicked")
    T.wait_for(lambda: not win.busy, "turn end")
    return win


def folder_plus_chat(app):
    """A window on lab-mac, a folder's + there pressed: the blank chat it opens on lab-mac, in that folder."""
    win = network_window(app)
    plus = T.find(win.sidebar, Gtk.Button, lambda b: (b.get_tooltip_text() or "").startswith("New session in "))
    plus[0].emit("clicked")
    T.wait_for(lambda: win.remote is not None and (win.remote.sessions.get(win.session_id) or {}).get("draft"),
               "the new chat")
    T.pump(0.5)
    return win


@scene("55f-network-folder-new-session", "A folder's + on lab-mac: a blank chat there, in that folder, opened at once "
                                         "with the top bar's model, effort and mode; not listed till it's written in")
def _(app):
    return folder_plus_chat(app)


def open_folder_step(app, typed=None, into=None):
    """A window on lab-mac, Open folder… pressed: "Open a folder on lab-mac" from the folder last used there, its
    folders listed; typed: a path typed instead; into: a folder in the list clicked."""
    win = network_window(app)
    r = NET["lab"][1]
    net = os.path.dirname(NET["folders"][0])
    for d in ("docs", "src", "tests"):
        os.makedirs(os.path.join(NET["folders"][0], d), exist_ok=True)
    baude.save_settings(remote_folders={**(baude.load_settings().get("remote_folders") or {}), r.key: net})
    win.sidebar.folder_btn.emit("clicked")
    T.wait_for(lambda: getattr(win, "folder_parts", None) and win.folder_parts[1].get_row_at_index(1), "its folders")
    entry, rows = win.folder_parts

    def listed(name):
        return next((row for i in range(50) if (row := rows.get_row_at_index(i)) is not None and
                     row.path.endswith("/" + name)), None)
    if typed is not None:
        entry.set_text(typed)
        T.wait_for(lambda: listed("parser") and not listed("site"), "the folders it begins")
    if into is not None:
        rows.emit("row-activated", listed(into))
        T.wait_for(lambda: listed("src"), "its folders")
    T.pump(0.5)
    return win


@scene("55f3-network-open-folder", "Open folder… with lab-mac picked: \"Open a folder on lab-mac\", from the folder "
                                   "last used there, its folders listed under the path")
def _(app):
    return open_folder_step(app)


@scene("55f4-network-open-folder-typed", "A path typed in it: the folders there that its last part begins")
def _(app):
    return open_folder_step(app, typed=os.path.join(os.path.dirname(NET["folders"][0]), "pa"))


@scene("55f5-network-open-folder-browsed", "A folder in the list clicked: it's the path, its folders listed")
def _(app):
    return open_folder_step(app, typed=os.path.join(os.path.dirname(NET["folders"][0]), "pa"), into="parser")


@scene("55f6-network-open-folder-opened", "Open: a blank chat on lab-mac, in that folder")
def _(app):
    win = open_folder_step(app, into="parser")
    win.folder_dialog.emit("response", "open")
    win.folder_dialog.force_close()
    T.wait_for(lambda: win.remote is not None and (win.remote.sessions.get(win.session_id) or {}).get("draft"),
               "the new chat")
    T.pump(0.5)
    return win


@scene("55f7-network-slash-new", "/new in a chat on lab-mac: a blank chat on lab-mac, in that chat's folder")
def _(app):
    win = network_window(app)
    open_network_session(win, "Speed up the tokenizer's hot loop")
    was = win.session_id
    T.type_and_send(win, "/new")
    T.wait_for(lambda: win.remote is not None and win.session_id != was and
               (win.remote.sessions.get(win.session_id) or {}).get("draft"), "the new chat")
    T.pump(0.5)
    return win


@scene("55f2-network-folder-new-session-sent", "That chat's first message sent: lab-mac keeps it from then on, and it's "
                                              "listed under its folder")
def _(app):
    win = folder_plus_chat(app)
    T.type_and_send(win, "Tidy the changelog for 2.4")
    T.wait_for(lambda: not (win.remote.sessions.get(win.session_id) or {}).get("draft"), "kept", 20)
    T.wait_for(lambda: session_row(win, win.session_id) is not None, "listed", 20)
    T.wait_for(lambda: T.button(win, "Allow"), "its question")
    T.button(win, "Allow").emit("clicked")
    T.wait_for(lambda: not win.busy, "turn end", 20)
    T.pump(0.5)
    return win


@scene("55h-network-models", "A network session's models are its server's: gpu-mac runs Apple's model and two of "
                             "its own, which this machine doesn't have")
def _(app):
    models_server()
    win = network_window(app, host=None)  # (gpu-mac is on this machine: its sessions are local's)
    T.wait_for(lambda: sidebar_row(win, "Sketch the release checklist") is not None, "gpu-mac's session")
    win.sidebar.on_activate(win.sidebar.list, sidebar_row(win, "Sketch the release checklist"))
    T.wait_for(lambda: win.remote is not None and win.proc is not None and "apple" in win.model_dd.values, "its models")
    popup_choices(win.model_dd)
    return win


@scene("55k-network-add-server", "New session's host right-clicked → Add Host…: another machine's host, by where ssh goes (an "
                                 "alias, or user@host); kept from then on, reached or not")
def _(app):
    win = network_window(app)
    win.ask_add_host().set_text("lab@build-box.lan")
    return win


@scene("55l-network-hosts", "New session's host, lab-mac, picked: lab-mac's sessions listed, in every group; ⋮ "
                            "has the groups")
def _(app):
    win = network_window(app, None)
    win.sidebar.view_btn.popup()
    return win


@scene("55l2-network-host-picker", "New session's hosts: the one picked is the one whose chats are listed")
def _(app):
    win = network_window(app, None)
    popup_choices(win.sidebar.host_dd)
    return win


@scene("55m-network-all", "Every group, on this machine (New session's host, as at first): its own chats, with its "
                           "host process's sessions")
def _(app):
    network_fixture()
    for n, title in enumerate(("Tidy the changelog", "Fix the login redirect")):  # (this machine's own, in site)
        T.write_session(NET["folders"][1], f"5555555{n}-0000-4000-8000-000000000055", title=title)
    win = network_window(app, None, host=None)
    T.wait_for(lambda: sidebar_row(win, "Tidy the changelog") is not None, "this machine's chats")
    return win


def studio(win):
    """A host added by hand, over ssh (never reached: the tests' ssh is false): studio."""
    r = baude.network().add({"via": "ssh", "dest": "lab@studio.lan"}, by_hand=True)
    T.wait_for(lambda: r.state == "dead", "studio tried")
    win.sidebar.fill_filters()
    return r


def host_right_click(win):
    """New session's host right-clicked: its menu."""
    ok, rect = win.sidebar.host_dd.compute_bounds(win.sidebar.host_dd)
    win.sidebar.host_menu(rect.get_width() / 2, rect.get_height() / 2)


@scene("55r-network-host-gone", "build-box, gone, picked beside New session: said over its chats, as they were last "
                                "seen")
def _(app):
    win = network_window(app, None, host="box")
    T.wait_for(lambda: sidebar_row(win, "Run the nightly benchmarks") is not None, "build-box's chats")
    return win


@scene("55r2-network-host-unknown", "A host picked that isn't known now (forgotten, or a mesh member not listed yet): "
                                    "still picked, by the name it had")
def _(app):
    network_fixture()
    baude.save_settings(new_session_host="mesh:0123abcd", new_session_host_name="old-mini", sidebar_groups=None)
    win = app.window(opts(cwd=NET["folders"][0]))
    T.pump(1)
    return win


@scene("55s-network-host-menu", "Right-click New session's host, one reached over ssh: Deactivate, then Add Host…")
def _(app):
    win = network_window(app, None, host=None)
    r = studio(win)
    win.sidebar.pick_host(baude.host_key(r))
    win.sidebar.fill_new_hosts()
    T.pump(0.2)
    host_right_click(win)
    return win


@scene("55t-network-host-deactivated", "lab-mac deactivated (its right-click → Deactivate): said over its chats, "
                                       "grayed; its right-click has Activate")
def _(app):
    win = network_window(app, None)
    win.connect("unrealize", lambda *_: baude.network().deactivate(NET["lab"][1], False))  # (as it was, for the next)
    win.sidebar.deactivate_host(baude.host_key(NET["lab"][1]), True)
    T.wait_for(lambda: win.sidebar.host_note.get_revealed(), "the host note")
    T.pump(0.3)
    host_right_click(win)
    return win


BOTS = {"Write the release notes for 2.4": "Juniper", "Plan the Q1 roadmap": "Fern"}


def lab_bots(win):
    """Two of lab-mac's chats made Bots (Juniper and Fern), as Make Bot… makes them, and the sidebar showing them; made
    chats again as the window goes, for the next scene."""
    server = NET["lab"][0]

    def made(on):
        with server.lock:
            for prompt, name in BOTS.items():
                server.set_bot(server.sessions[NET["ids"][prompt]], {"name": name} if on else None)
    win.connect("unrealize", lambda *_: made(False))
    made(True)
    T.wait_for(lambda: sum(win.sidebar.list.get_row_at_index(i).has_css_class("bot-row") for i in range(40)
                           if win.sidebar.list.get_row_at_index(i) is not None) == len(BOTS), "the Bots' rows", 30)


@scene("55v-network-bots", "Two of lab-mac's chats made Bots: listed first, each by its picture (its initials) and its "
                           "name; Juniper open, its name in the header")
def _(app):
    win = network_window(app, None)
    lab_bots(win)
    win.sidebar.on_activate(win.sidebar.list, session_row(win, NET["ids"]["Write the release notes for 2.4"]))
    T.wait_for(lambda: win.session_id == NET["ids"]["Write the release notes for 2.4"], "Juniper open")
    T.pump(0.5)
    return win


@scene("55w-network-make-bot", "Right-click a chat on lab-mac → Make Bot…: its picture, Choose Picture…, and the "
                               "Bot's name, the chat's title to keep or type over")
def _(app):
    win = network_window(app, None)
    lab_bots(win)
    win.sidebar.ask_bot(sidebar_row(win, "Speed up the tokenizer's hot loop").info)
    T.pump(0.3)
    return win


@scene("55u-network-meta-host-button", "Meta's button:host for lab-mac, pressed, with this machine picked: lab-mac's "
                                       "chats listed, and New session on it")
def _(app):
    win = network_window(app, None, host=None)
    btn = win.panel.meta.button("host", ["lab-mac"])
    btn.emit("clicked")
    T.wait_for(lambda: sidebar_row(win, "Write the release notes for 2.4") is not None, "lab-mac's chats")
    return win


@scene("55g-network-session-menu", "Right-click a network session: Rename…, Archive and Move to Trash…, done on its "
                                   "host; one at a turn: Stop; one whose host is gone: Delete (it comes back when it's "
                                   "used again)")
def _(app):
    win = network_window(app)
    win.sidebar.show_menu(sidebar_row(win, "Write the release notes for 2.4"), 120, 14)
    return win


@scene("55i-network-subagent", "A network session's subagent in the side panel: its own transcript, fetched from its "
                               "server with the session's", size=(1200 + SIDEBAR, H))
def _(app):
    sid = subagent_session()
    win = network_window(app, "Planning", sid)
    win.sidebar.on_activate(win.sidebar.list, session_row(win, sid))
    T.wait_for(lambda: win.remote is not None and win.session_id == sid and win.proc is not None, "opened")
    T.wait_for(lambda: "toolu_ag1" in win.panel.agent_rows, "its subagent")
    win.show_panel(True, "agents")
    win.panel.open_agent("toolu_ag1")
    end = time.monotonic() + 5  # (its details: what's mirrored of its transcript, read as it's there)
    while "28" not in T.labels_text(win.panel.agent_view("toolu_ag1")) and time.monotonic() < end:
        T.pump(0.1)
    return win


@scene("55j-network-renamed", "A network session renamed (Rename…, or F2): its server renames it, and it's listed "
                              "and shown by its new name")
def _(app):
    network_fixture()
    sid = NET["ids"]["Plan the Q1 roadmap"]
    win = network_window(app, "Planning", sid)
    win.sidebar.on_activate(win.sidebar.list, session_row(win, sid))
    T.wait_for(lambda: win.remote is not None and win.session_id == sid and win.proc is not None, "opened")
    win.sidebar.rename(session_row(win, sid).info, "Q1 roadmap: first draft")
    T.wait_for(lambda: (session_row(win, sid).info.get("title") or "").startswith("Q1 roadmap"), "renamed")
    T.pump(0.3)
    return win


@scene("53c-folder-collapse", "Folders collapse: the arrow appears beside the name when the pointer is near "
                              "(and stays while collapsed); archived sessions sit in a collapsed group at the end")
def _(app):
    proj = os.path.join(T.TMP, "sessions-proj")
    sidebar_sessions(proj)
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: sidebar_row(win, "App creation test") is not None, "rows")
    win.sidebar.archive(sidebar_row(win, "Refactor settings").info)
    win.sidebar.toggle_folder(os.path.join(T.TMP, "acme-app"))
    for i in range(60):  # the pointer near "No folder (Claude Desktop)"
        row = win.sidebar.list.get_row_at_index(i)
        if row is not None and getattr(row, "key", None) == "scratch":
            row.get_child().add_css_class("hover")
            break
    return win


DESKTOP = {"Plan the onboarding flow": "d5300000-0000-4000-8000-000000000001",
           "Onboarding, shorter": "d5300000-0000-4000-8000-000000000002",
           "Review the pricing page": "d5300000-0000-4000-8000-000000000003"}


def desktop_chats():
    """Claude Desktop's chats here, in its own list (its chats' files, as test_desktop_index writes them): two in
    desk-app, one of them forked from the other, and one in desk-site; with two terminal chats in desk-app beside them.
    Returns desk-app."""
    app_dir, site = (os.path.join(T.TMP, name) for name in ("desk-app", "desk-site"))
    for d in (app_dir, site):
        os.makedirs(d, exist_ok=True)
    now, entries = time.time(), []
    for n, (title, folder, ago, fork) in enumerate((
            ("Plan the onboarding flow", app_dir, 1800, None), ("Onboarding, shorter", app_dir, 2400, "local_0"),
            ("Review the pricing page", site, 90000, None))):
        age(T.write_session(folder, DESKTOP[title], title=title), ago)  # (DESKTOP's ids, the transcripts' too)
        entries.append({"sessionId": f"local_{n}", "cliSessionId": DESKTOP[title], "title": title, "cwd": folder,
                        "createdAt": (now - ago - 600) * 1000, "lastActivityAt": (now - ago) * 1000,
                        **({"forkedFromSessionId": fork} if fork else {})})
    for n, (title, ago) in enumerate((("Fix the login bug", 600), ("Tidy the changelog", 7200))):
        age(T.write_session(app_dir, f"d5310000-0000-4000-8000-00000000000{n}", title=title), ago)
    T.write_desktop_index(entries)
    return app_dir


@scene("53e-desktop-group", "Claude Desktop's chats in the Desktop group: apart from the folders, first, nested as "
                            "Desktop nests them")
def _(app):
    win = app.window(opts(cwd=desktop_chats()))
    T.wait_for(lambda: sidebar_row(win, "Review the pricing page") is not None, "Desktop's chats")
    return win


@scene("53e2-desktop-group-menu", "⋮: Desktop first among the groups, while there are Desktop chats")
def _(app):
    win = app.window(opts(cwd=desktop_chats()))
    T.wait_for(lambda: sidebar_row(win, "Review the pricing page") is not None, "Desktop's chats")
    win.sidebar.view_btn.popup()
    return win


@scene("55x-network-desktop", "lab-mac picked: its Claude Desktop chats, which it lists, in the Desktop group, apart "
                              "and first")
def _(app):
    network_fixture()
    desktop_chats()
    win = network_window(app, None)
    T.wait_for(lambda: sidebar_row(win, "Review the pricing page") is not None, "lab-mac's Desktop chats", 20)
    return win


@scene("55x2-network-desktop-elsewhere", "One of lab-mac's Desktop chats open while Claude Desktop writes it: said, "
                                         "and no text box till it's done")
def _(app):
    network_fixture()
    desktop_chats()
    win = network_window(app, None)
    T.wait_for(lambda: sidebar_row(win, "Plan the onboarding flow") is not None, "lab-mac's Desktop chats", 20)
    win.sidebar.on_activate(win.sidebar.list, sidebar_row(win, "Plan the onboarding flow"))
    T.wait_for(lambda: win.remote is not None and win.session_id == DESKTOP["Plan the onboarding flow"], "opened")
    with open(T.baudelib.transcripts.find_session(DESKTOP["Plan the onboarding flow"])["path"], "a") as f:  # (Claude Desktop, writing it now)
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Splitting the flow into three screens."}]}}) + "\n")
    T.wait_for(lambda: getattr(win, "banner_kind", None) == "live", "said", 20)
    return win


@scene("53d-live-tail", "A session active elsewhere: new messages appear as they're written, a spinner while "
                        "it works, and the input is off until it goes quiet")
def _(app):
    proj = os.path.join(T.TMP, "tail-proj")
    os.makedirs(proj, exist_ok=True)
    path = T.write_session(proj, "44444444-0000-4000-8000-000000000004", title="Running in Claude Desktop")
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: sidebar_row(win, "Running in Claude Desktop") is not None, "rows")
    win.resume_session(sidebar_row(win, "Running in Claude Desktop").info)
    with open(path, "a") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "now add a test for it"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Adding a regression test for the login fix."},
            {"type": "tool_use", "id": "live1", "name": "Bash",
             "input": {"command": "pytest -q tests/test_auth.py", "description": "Run the auth tests"}}]}}) + "\n")
    T.wait_for(lambda: "Adding a regression test" in T.labels_text(win.transcript), "tailed")
    win.sidebar.set_state(win.session_state())
    return win


@scene("51b-resumed-after-writing", "Writing to a past session takes it up: '— resumed —' marks where, above "
                                    "your message")
def _(app):
    proj = os.path.join(T.TMP, "resume-write-proj")
    os.makedirs(proj, exist_ok=True)
    path = T.write_session(proj, "99999999-0000-4000-8000-00000000000b", title="Login bug")
    old = time.time() - 3600
    os.utime(path, (old, old))
    win = app.window(opts(cwd=proj))
    win.resume_session(baude.read_session_info(path))
    T.pump(0.3)
    T.type_and_send(win, "and the logout bug?")
    T.wait_for(lambda: T.button(win, "Allow"), "the reply's permission prompt")
    return win


@scene("51c-claude-events", "Claude Code's own events in a turn: a commit and a push (each with Show in Git), "
                           "a commit in another folder, background agents stopped, a notification, a warning; the "
                           "rest say nothing, never a raw name; then notes the chat makes itself")
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "SYSTEM EVENTS")
    T.wait_for(lambda: not win.busy, "turn end")
    for note in (wording.say("chat_header.model_note", name="Opus 4.7"), wording.say("tool_calls.denied_tool", tool="Bash"),
                 wording.say("chat_notes.interrupted")):
        win.note(note)
    return win


@scene("54-task-notifications", "Background tasks that ended while Claude worked: one quiet line each, "
                                "✓ or ✗, the tool and its description (a link back to its card), not the raw "
                                "command or the XML they arrive in")
def _(app):
    proj = os.path.join(T.TMP, "tasknote-proj")
    os.makedirs(proj, exist_ok=True)
    path = T.write_session(proj, "99999999-0000-4000-8000-00000000000c", title="Build and test")

    def started(tool_id, cmd, description):
        return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_id, "name": "Bash",
             "input": {"command": cmd, "description": description, "run_in_background": True}}]}}) + "\n" + \
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content":
                 f"Command running in background with ID: {tool_id}. Output is being written to: /tmp/{tool_id}"}]}}) + "\n"

    def note(status, summary, tool_id):
        return json.dumps({"type": "user", "message": {"role": "user", "content": (
            f"<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>{tool_id}</tool-use-id>\n"
            f"<output-file>/tmp/claude-501/tasks/b1.output</output-file>\n<status>{status}</status>\n"
            f"<summary>{summary}</summary>\n</task-notification>")}}) + "\n"
    with open(path, "a") as f:
        f.write(started("bg_dev", "cd web && npm run dev -- --port 5173 > /tmp/dev.log 2>&1", "Launch the dev server"))
        f.write(started("bg_e2e", "cd web && npx playwright test --reporter=line 2>&1 | tail -40", "Run the e2e suite"))
        f.write(note("completed", 'Background command "cd web &amp;&amp; npm run dev -- --port 5173 &gt; /tmp/dev.log '
                                  '2&gt;&amp;1" completed (exit code 0)', "bg_dev"))
        f.write(json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": "The dev server is up on port 5173."}]}}) + "\n")
        f.write(note("failed", 'Background command "Run the e2e suite" failed with exit code 1', "bg_e2e"))
        f.write(json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": "Two e2e tests failed; both time out waiting for the login form."}]}}) + "\n")
    old = time.time() - 3600
    os.utime(path, (old, old))
    win = app.window(opts(cwd=proj))
    win.resume_session(baude.read_session_info(path))
    return win


@scene("70-background-running", "A background command: card keeps spinning, status bar shows it running")
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "BACKGROUND HOLD")
    T.wait_for(lambda: "toolu_bg1" in win.tool_cards and win.tool_cards["toolu_bg1"].state == "background", "bg")
    return win


@scene("71-background-finished", "It finished: ✓ with its output, then the turn the CLI started by itself")
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "BACKGROUND")
    T.wait_for(lambda: "The build finished" in T.labels_text(win.transcript) and not win.busy, "follow-up", 10)
    win.tool_cards["toolu_bg1"].output_toggle.set_expanded(True)
    return win


@scene("72-panel-processes", "Side panel, Processes: live output tail and a stop button", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "BACKGROUND HOLD")
    T.wait_for(lambda: "bg1" in win.panel.proc_rows, "row")
    win.show_panel(True, "processes")
    win.panel.proc_rows["bg1"].set_expanded(True)
    T.pump(1.2)  # one output poll
    return win


@scene("72b-panel-processes-long", "Side panel, Processes: commands without a description are one line of code "
                                   "(the whole command when opened, highlighted)", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    cmds = [
        ("J=gen_79fd3c530feb4fd29bbb9a; D=/home/naza/projects/demo-app/.local/widget-bench/v7-smoke-2/$J; while docker "
         "exec demo-app-runner-1 test -d /var/lib/runner/$J 2>/dev/null; do docker exec demo-app-runner-1 sh -c "
         "\"cd /var/lib/runner/$J && tar cf - --exclude=node_modules workspace\" > $D/snap.tar.tmp; sleep 30; done"),
        "until [ $(docker exec demo-postgres psql -tAc 'select count(*) from installs') -ge 8 ]; do sleep 10; done",
    ]
    for n, cmd in enumerate(cmds):
        tid = f"lp{n}"
        win.render_assistant([{"type": "tool_use", "id": f"toolu_{tid}", "name": "Bash",
                               "input": {"command": cmd, "run_in_background": True}}])
        win.on_msg(win.proc, {"type": "system", "subtype": "task_started", "task_id": tid, "tool_use_id": f"toolu_{tid}",
                              "description": cmd, "task_type": "local_bash"})
    win.render_assistant([{"type": "tool_use", "id": "toolu_lp9", "name": "Bash",
                           "input": {"command": "npm run dev", "description": "Start the dev server",
                                     "run_in_background": True}}])
    win.on_msg(win.proc, {"type": "system", "subtype": "task_started", "task_id": "lp9", "tool_use_id": "toolu_lp9",
                          "description": "Start the dev server", "task_type": "local_bash"})
    win.show_panel(True, "processes")
    win.panel.proc_rows["lp0"].set_expanded(True)
    return win


@scene("73-panel-subagents", "Side panel, Subagents: each run with status, tool uses, tokens, time", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "SUBAGENT")
    T.wait_for(lambda: "subagent counted" in T.labels_text(win.transcript) and not win.busy, "done", 10)
    win.show_panel(True, "agents")
    return win


@scene("74-panel-subagent-detail", "A subagent's own transcript: its prompt, tool calls and answer", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "SUBAGENT")
    T.wait_for(lambda: "subagent counted" in T.labels_text(win.transcript) and not win.busy, "done", 10)
    win.show_panel(True, "agents")
    win.panel.open_agent("toolu_ag1")
    return win


@scene("74b-panel-nested-subagents", "Nested subagents: an agent's own agents sit under it in the list "
                                    "(here the outer one is still running)", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "SUBAGENT")
    T.wait_for(lambda: "subagent counted" in T.labels_text(win.transcript) and not win.busy, "done", 10)
    T.type_and_send(win, "NESTED HOLD")
    T.wait_for(lambda: "toolu_ag2_inner" in win.panel.agent_rows
               and win.panel.agent_rows["toolu_ag2_inner"].status.state == "done", "inner done", 10)
    win.show_panel(True, "agents")
    return win


@scene("74c-panel-nested-detail", "The outer agent's transcript: the inner agent is one of its tool calls, "
                                  "linked to its own transcript", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "NESTED HOLD")
    T.wait_for(lambda: "toolu_ag1_inner" in win.panel.agent_rows
               and win.panel.agent_rows["toolu_ag1_inner"].status.state == "done", "inner done", 10)
    T.wait_for(lambda: "helper counted" in T.labels_text(win.panel.agent_view("toolu_ag1")), "outer reply")
    win.show_panel(True, "agents")
    win.panel.open_agent("toolu_ag1")
    for group in T.find(win.panel.agent_view("toolu_ag1"), baude.ToolGroup):
        group.toggle.set_expanded(True)
    win.tool_cards["toolu_ag1_inner"].output_toggle.set_expanded(True)
    return win


ARTIFACT_URL = "https://claude.ai/artifact/EXAMPLEart1fact2demo3x"


def publish_artifact(win, tool_id="art", title="Acme Fleet Ops", icon="chart", url=ARTIFACT_URL):
    page = os.path.join(T.TMP, f"{tool_id}.html")
    with open(page, "w") as f:
        f.write(f"<!doctype html><title>{title}</title><body>dashboard</body>")
    win.render_assistant([{"type": "tool_use", "id": tool_id, "name": "Artifact",
                           "input": {"file_path": page, "icon": icon, "description": "Day's jobs, runs and numbers"}}])
    win.render_tool_results([{"type": "tool_result", "tool_use_id": tool_id, "content":
                              f'Published {page} at {url} (Version 2, version id 1-a) Icon: "{icon}".'}])


@scene("75-side-questions", "/btw side questions: each its own forked, unsaved claude; answers here, the chat "
                             "carries on (two threads, one with a follow-up)", size=(1200 + SIDEBAR, H))
def _(app):
    proj = os.path.join(T.TMP, "btw-proj")
    os.makedirs(proj, exist_ok=True)
    win = app.window(opts(cwd=proj))
    # (its transcript, as claude would have it, so the side questions fork it: the host process's chat's id)
    T.write_session(proj, win.session_id, title="Main chat")
    T.type_and_send(win, "list the files")
    T.wait_for(lambda: T.button(win, "Allow"), "main turn")
    first = win.ask_side_question("what does make do here?")
    win.ask_side_question("is the build cached?")
    T.wait_for(lambda: all(not q.busy for q in win.panel.side_questions()), "answers")
    first.ask("and how long does it take?")
    T.wait_for(lambda: not first.busy, "follow-up")
    return win


@scene("75b-side-question-new", "Side questions' + (or the box over the list): a new one asked, and another "
                                "waiting for its question", size=(1200 + SIDEBAR, H))
def _(app):
    proj = os.path.join(T.TMP, "btw-new-proj")
    os.makedirs(proj, exist_ok=True)
    win = app.window(opts(cwd=proj))
    T.write_session(proj, win.session_id, title="Main chat")
    win.show_panel(True, "btw")
    ask = getattr(win.panel, "btw_ask", None)
    plus = getattr(win.panel, "btw_new", None)
    if ask is not None:  # (the box over the list: one asked in it, another typed and not yet sent)
        ask.input.get_buffer().set_text("what does make do here?")
        first = win.panel.ask_typed()
        T.wait_for(lambda: first.answer is not None and not first.busy, "answer")
        ask.input.get_buffer().set_text("is the build cached?")
    elif plus is not None:  # (before the +: the page as it was)
        plus.emit("clicked")
        first = win.panel.side_questions()[0]
        if hasattr(first, "composer"):
            first.composer.get_buffer().set_text("what does make do here?")
            first.submit()
        else:
            first.follow.set_text("what does make do here?")
            first.follow.emit("activate")
        T.wait_for(lambda: first.answer is not None and not first.busy, "answer")
        plus.emit("clicked")
    return win


def _side_questions_three(app, name):
    proj = os.path.join(T.TMP, name)
    os.makedirs(proj, exist_ok=True)
    win = app.window(opts(cwd=proj))
    T.write_session(proj, win.session_id, title="Main chat")
    for q in ("what does make do here?", "is the build cached?", "which tests are slow?"):
        win.ask_side_question(q)
    T.wait_for(lambda: all(not q.busy for q in win.panel.side_questions()), "answers")
    return win


@scene("75c-side-questions-rows", "Side questions as rows titled with their questions: one open under its line, two "
                                  "closed", size=(1200 + SIDEBAR, H))
def _(app):
    win = _side_questions_three(app, "btw-rows-proj")
    for q in win.panel.side_questions()[1:]:
        if hasattr(q, "set_expanded"):
            q.set_expanded(False)
    return win


@scene("75d-side-question-full", "A side question filling the Side questions page, a back button out",
       size=(1200 + SIDEBAR, H))
def _(app):
    win = _side_questions_three(app, "btw-full-proj")
    q = win.panel.side_questions()[1]
    if hasattr(win.panel, "open_side_question"):
        win.panel.open_side_question(q)
    return win


@scene("75e-side-questions-empty", "The Side questions page with none asked yet", size=(1200 + SIDEBAR, H))
def _(app):
    proj = os.path.join(T.TMP, "btw-empty-proj")
    os.makedirs(proj, exist_ok=True)
    win = app.window(opts(cwd=proj))
    win.show_panel(True, "btw")
    return win


@scene("76-tail-background-tasks", "Viewing a session running elsewhere: its background command and subagent "
                                   "(with its own transcript) rebuilt from the files, in the side panel",
       size=(1200 + SIDEBAR, H))
def _(app):
    proj = os.path.join(T.TMP, "bg-tail-proj")
    os.makedirs(proj, exist_ok=True)
    T.write_bg_session(proj, "abababab-0000-4000-8000-0000000000aa", live=True)
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: win.sidebar.find_info("abababab-0000-4000-8000-0000000000aa"), "listed")
    win.resume_session(win.sidebar.find_info("abababab-0000-4000-8000-0000000000aa"))
    T.pump(0.3)
    win.show_panel(True, "agents")
    win.panel.open_agent("ta1")
    return win


@scene("80-artifact-published", "Claude published an artifact: its card shows the page's title, version, Open and copy")
def _(app):
    win = app.window(opts())
    win.add(win.user_bubble("Build me an ops dashboard", []))
    publish_artifact(win)
    win.render_assistant([{"type": "text", "text": f"Your dashboard is live: {ARTIFACT_URL}"}])
    return win


@scene("81-artifact-links", "Artifact links in a reply become cards (known title, or 'Claude artifact'); links are clickable")
def _(app):
    win = app.window(opts())
    publish_artifact(win, "a1", "Ops Dashboard", "chart")
    win.render_assistant([{"type": "text", "text": "Two pages to compare:\n\n"
                           f"- the new one: {ARTIFACT_URL}\n- last month's: https://claude.ai/artifact/OLD123xyz\n\n"
                           "Docs: https://docs.gtk.org/gtk4/"}])
    return win


@scene("82-panel-artifacts", "Side panel, Artifacts: everything published or linked in this session", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    publish_artifact(win, "p1", "Ops Dashboard", "chart")
    publish_artifact(win, "p2", "Weekly Report", "doc", "https://claude.ai/artifact/WEEKLYrep0rt")
    publish_artifact(win, "p3", "Depot Map", "map", "https://claude.ai/artifact/DEPOTmap99")
    win.show_panel(True, "artifacts")
    return win


@scene("83-artifact-history", "A resumed Claude Desktop session: artifacts it published come back as cards")
def _(app):
    proj = os.path.join(T.TMP, "artifact-proj")
    os.makedirs(proj, exist_ok=True)
    page = os.path.join(proj, "quote-builder.html")
    with open(page, "w") as f:
        f.write("<title>Quote Builder</title>")
    sid = "77777777-0000-4000-8000-000000000007"
    d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", proj.replace("/", "-"))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{sid}.jsonl")
    recs = [{"type": "user", "cwd": proj, "entrypoint": "claude-desktop", "message": {"content": "Make a quote builder"}},
            {"type": "assistant", "cwd": proj, "message": {"content": [{"type": "text", "text": "Done — it's published."}]}},
            {"type": "frame-link", "frameUrl": "https://claude.ai/artifact/QUOTEbuild3r", "path": page,
             "title": "Quote Builder", "artifactCount": 1},
            {"type": "custom-title", "customTitle": "Quote builder", "sessionId": sid}]
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in recs) + "\n")
    age(path)
    win = app.window(opts(cwd=proj))
    win.resume_session(baude.read_session_info(path))
    T.wait_for(lambda: win.session_id and win.proc, "resume")
    return win


def feedback_with_capture(win, regions=True):
    """A draft: text, and a capture of a chat with a reply (and two marked regions)."""
    win.render_assistant([{"type": "text", "text": "The background build is still running; I'll check it in a minute."},
                          {"type": "tool_use", "id": "fb1", "name": "Bash",
                           "input": {"command": "make -j8", "description": "Build everything"}}])
    win.render_tool_results([{"type": "tool_result", "tool_use_id": "fb1", "content": "ok"}])
    T.pump(0.4)
    draft = baude.FeedbackDraft()
    draft.text = "The process rows in the side panel wrap the whole command over dozens of lines."
    cap = baude.capture_window(win)
    if regions:
        for want in ("background build", "Build everything"):
            it = next(i for i in cap.items if want in i["text"])
            cap.regions.append((it["x"] - 6, it["y"] - 6, it["w"] + 12, it["h"] + 12))
    draft.captures.append(cap)
    win.feedback_draft = draft
    return cap


@scene("90-feedback-dialog", "Menu → Send Feedback…: what's the issue or suggestion, and an empty box with + to "
                             "add a screen capture")
def _(app):
    win = app.window(opts())
    dlg = win.open_feedback()
    dlg.text.grab_focus()
    return win


@scene("91-feedback-capture-bar", "After +: the dialog steps aside and a bar at the bottom waits — go to what you "
                                  "want to show, then Capture")
def _(app):
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text": "Here's the table you asked for."}])
    dlg = win.open_feedback()
    T.pump(0.2)
    win.start_feedback_capture(dlg)
    T.pump(0.4)
    return win


@scene("92-feedback-with-capture", "Back in the dialog with a capture (the window as an image, plus its text on "
                                   "screen); click it to mark regions, + for another")
def _(app):
    win = app.window(opts())
    feedback_with_capture(win)
    win.open_feedback()
    return win


@scene("93-feedback-regions", "Marking regions on a capture: drag to outline what you mean, right-click one to "
                              "remove it; the text inside each is saved with it", size=(1200 + SIDEBAR, 860))
def _(app):
    win = app.window(opts())
    cap = feedback_with_capture(win)
    baude.RegionEditor(cap).present(win)
    return win


@scene("60m-models-any-case", "The same model on two hosts, named in different case (gpt-oss:20b here, gpt-oss:20B on "
                              "another host): listed once, as this machine names it")
def _(app):
    here = {"value": "ollama/gpt-oss:20b", "displayName": "gpt-oss:20b", "provider": "ollama"}
    there = {"value": "openai/mesh-other/gpt-oss:20B", "id": "gpt-oss:20B", "displayName": "gpt-oss:20B",
             "provider": "openai/mesh-other"}
    win = app.window(opts())
    T.wait_for(lambda: win.model_dd.values, "models")
    win.provider_choices = lambda: [("local|ollama", "This Mac", "Ollama", [here]),
                                    ("other|ollama", "other-host", "Ollama", [there])]
    win.sync_providers()
    popup_choices(win.model_dd)
    return win


@scene("60n-models-mesh", "The model picker open on a chat whose host has eight Ollama models and whose mesh has another "
                         "host with three more (one of them also here), an MLX model and Apple's: each listed once, under its "
                         "family, with its family's logo")
def _(app):
    def m(value, name, pid, **more):
        return {"value": value, "displayName": name, "provider": pid, **more}
    here = [m(f"ollama/{n}", n, "ollama", id=n, window=w) for n, w in (
        ("qwen3:8b", 40960), ("qwen3:30b", 40960), ("gemma3:4b", 32768), ("gemma3:12b", 32768),
        ("llama3.1:8b", 131072), ("deepseek-r1:8b", 131072), ("mistral:7b", 32768), ("phi4:14b", 16384))]
    there = "openai/mesh-studio-ollama"
    studio = [m(f"{there}/{n}", n, there, id=n, window=w) for n, w in (
        ("llama3.2:3b", 131072), ("gpt-oss:20b", 131072), ("qwen3:8b", 40960))]
    mlx = [m("agent/studio/mlx-community/Mistral-7B-Instruct-v0.3-4bit", "Mistral-7B-Instruct-v0.3-4bit", "agent/studio/mlx")]
    apple = [m("agent/studio/apple", "Apple Foundation Model", "agent/studio/apple")]
    win = app.window(opts())
    T.wait_for(lambda: win.model_dd.values, "models")
    win.provider_choices = lambda: [("local|ollama", "This Mac", "Ollama", here), ("studio|ollama", "studio", "Ollama", studio),
                                    ("studio|mlx", "studio", "MLX", mlx), ("studio|apple", "studio", "Apple", apple)]
    win.sync_providers()
    popup_choices(win.model_dd)
    return win


@scene("60-effort-unavailable", "A model without thinking effort: the effort picker is greyed out")
def _(app):
    win = app.window(opts())
    T.wait_for(lambda: win.model_dd.values, "models")
    win.model_dd.set_selected(win.model_dd.values.index("claude-haiku-4-5"))
    return win


@scene("61-context-high", "Context meter close to full (red), with a message to show it in use")
def _(app):
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text": "Long conversation…"}])
    win.show_context({"totalTokens": 181000, "maxTokens": 200000,
                      "categories": [{"name": "Messages", "tokens": 160000, "kind": "used"}]})
    return win


@scene("62-full-width", "Full-width text on a wide window", size=(1400 + SIDEBAR, 760))
def _(app):
    win = app.window(opts())
    win.lookup_action("fullwidth").change_state(GLib.Variant.new_boolean(True))
    win.render_assistant([{"type": "text", "text": "This paragraph stretches across the whole window "
                                                    "instead of stopping at 860 pixels. " * 4}])
    return win


@scene("63-zoomed", "Text zoomed to 140% (Ctrl +)")
def _(app):
    win = app.window(opts())
    win.set_zoom(1.4)
    win.render_assistant([{"type": "text", "text": "## Zoomed\nBigger text in the chat **and** the input box."}])
    win.input.get_buffer().set_text("typing at 140%")
    return win


# ---- the Feedback tab: a fake GitHub with feedback at every stage, the images of real windows (fixview.py)

def real_drag(win, labels, start, end):
    """A real mouse (tests/mouse.py) dragged from a character of a label to one of another, through those between."""
    import mouse
    hand = mouse.Mouse(win)

    def at(label, i):
        ox, oy = label.get_layout_offsets()
        pos = label.get_layout().index_to_pos(len(label.get_text()[:i].encode()))
        return label.compute_point(win, T.widgets.Graphene.Point().init(
            ox + pos.x / T.Pango.SCALE + 1, oy + (pos.y + pos.height / 2) / T.Pango.SCALE))[1]

    points = [at(labels[start[0]], start[1]), at(labels[start[0]], start[1] + 8),
              *(at(labels[i], 4) for i in range(start[0] + 1, end[0])), at(labels[end[0]], end[1])]
    hand.down(points[0].x, points[0].y)
    T.pump(0.05)
    for p in points[1:]:
        hand.drag(p.x, p.y)
        T.pump(0.05)
    hand.up(points[-1].x, points[-1].y)
    T.pump(0.1)


@scene("97-chat-selection", "A drag from one paragraph of a reply to another: what it passes is selected, and copied")
def _(app):
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text":
                           "The nightly job stopped at the export step, because the bucket's credentials had expired "
                           "an hour before it ran.\n\nThree things follow from that:\n\n"
                           "- the export is still queued, so nothing was lost\n"
                           "- the retry won't help until the credentials are renewed\n"
                           "- yesterday's numbers are the newest in the dashboard\n\n"
                           "Once they're renewed, run the job again by hand and it picks up where it stopped, with no "
                           "need to start the export from the beginning."}])
    T.pump(0.4)
    labels = [w for w in T.widgets.walk(win.transcript) if isinstance(w, Gtk.Label) and w.get_selectable()]
    real_drag(win, labels, (0, 20), (len(labels) - 1, 40))
    return win


def window_png(app, css=None):
    """A real window as a PNG, offscreen or not: the empty chat, with css added (what a fix might have changed)."""
    app.next_size = (900, 560)
    win = app.window(opts())
    provider = None
    if css:
        provider = Gtk.CssProvider()
        provider.load_from_string(css)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    T.pump(0.5)
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, win.get_width(), win.get_height())
    png = win.get_native().get_renderer().render_texture(snap.to_node(), None).save_to_png_bytes().get_data()
    if provider:
        Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), provider)
    T.close(win)
    return png


def saved_note(name, text, png, issue=None):
    """A note as Send Feedback… leaves it, with a capture."""
    folder = os.path.join(baude.feedback_dir(), name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "feedback.md"), "w") as f:
        f.write(f"# Feedback 2026-10-{name[6:8]} 14:20\n\n{text}\n\n## Context\n\n- session: s1 (Runner Dashboard)\n"
                f"- folder: {T.TMP}\n- Baude: x\n")
    with open(os.path.join(folder, "capture-1.png"), "wb") as f:
        f.write(png)
    if issue:
        with open(os.path.join(folder, "issue.json"), "w") as f:
            json.dump(issue, f)
    return folder


def fixes_tab(app, select=None, show_all=False, viewer="ADMIN", notes=False, private=False, shots=None):
    """A window with ☰ → Ongoing Feedback open, read from a fake GitHub: returned with the page ready. private: there's
    a private repository for feedback (fixes.PRIVATE_FEEDBACK) too, that it's shown. shots: {path: PNG} for the fix's
    images, in place of the window's own."""
    fake = os.path.join(T.TMP, "fake-gh")
    with open(fake, "w") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(HERE, "fake_gh.py")}" "$@"\n')
    os.chmod(fake, 0o755)
    for path, png in (shots or {"12/1/1-before.png": window_png(app),
                                "12/1/1-after.png": window_png(app, ".composer-box { box-shadow: 0 0 0 2px @accent_color; }")}).items():
        kept = os.path.join(T.fixes.fixes_home(), "shots", path)
        os.makedirs(os.path.dirname(kept), exist_ok=True)
        with open(kept, "wb") as f:
            f.write(png)
    state = fixes_fixture.state(viewer, private)
    path = os.path.join(T.TMP, "fake-gh.json")
    with open(path, "w") as f:
        json.dump(state, f)
    os.environ.update(BAUDE_GH=fake, FAKE_GH_STATE=path, FAKE_GH_SIGNED_IN="1")
    T.feedback._gh_seen.update(at=None, repo=None)
    T.fixes._can_write.clear()
    T.fixes._private_seen.clear()
    if notes:
        png = window_png(app)
        saved_note("20261020-142000", "The sessions sidebar jumps when a chat finishes\n\nIt scrolls back to the top, and I "
                                      "lose my place.", png)
        saved_note("20261021-142000", "The input box is hard to find when the chat is empty", png,
                   {"url": "https://github.com/nassir90/Baude/issues/12", "number": 12, "repo": T.feedback.code_repo()})
    app.next_size = (W, H)
    win = app.window(opts())
    win.activate_action("win.fixes")
    page = win.tabs.get(("fixes",))
    T.wait_for(lambda: page.read_at, "the feedback read")
    if show_all:
        page.all_btn.set_active(True)
    if select:
        page.rows.select_row(next(page.rows.get_row_at_index(n) for n in range(len(page.listed()))
                                  if page.rows.get_row_at_index(n).number == select))
    T.pump(0.6)
    return win, page


@scene("96-feedback-ready", "☰ → Ongoing Feedback: a fix ready for review, its images before and after, Merge or Discard")
def _(app):
    return fixes_tab(app)[0]


@scene("96b-feedback-open", "Feedback nobody's started on: Fix here starts a fix on this machine")
def _(app):
    return fixes_tab(app, select=10)[0]


@scene("96c-feedback-working", "A round running on someone's machine, seen by everyone")
def _(app):
    return fixes_tab(app, select=11)[0]


@scene("96d-feedback-all", "All of it: merged and rejected too, a rejected one reopened from here")
def _(app):
    return fixes_tab(app, select=7, show_all=True)[0]


@scene("96e-feedback-merge", "Merging a fix asks first: main reaches everyone")
def _(app):
    win, page = fixes_tab(app)
    page.merge()
    T.pump(0.4)
    return win


@scene("96g-feedback-offline-note", "A note saved on this machine and never filed: its own row, with its captures")
def _(app):
    return fixes_tab(app, select=os.path.join(baude.feedback_dir(), "20261020-142000"), notes=True)[0]


@scene("96h-feedback-note-on-issue", "A note filed as an issue: shown under that issue, with its captures")
def _(app):
    return fixes_tab(app, select=12, notes=True)[0]


@scene("96i-feedback-private", "Feedback kept on a private repository, beside Baude's: each says where it is, its fix "
                                "a PR on Baude")
def _(app):
    return fixes_tab(app, select="BaudeInternal-3", private=True, notes=True)[0]


@scene("96f-feedback-read-only", "Without write access to the repository: read only")
def _(app):
    return fixes_tab(app, viewer="READ")[0]


@scene("96j-feedback-loading", "Reading it again: the refresh icon, in its full colour, turning (here, part way round)")
def _(app):
    win, page = fixes_tab(app)
    page.refresh_btn.set_loading(True)
    page.refresh_btn.turning.at = lambda _now: (0.125, T.imageviews.Turning.SPEED)  # (a picture holds still)
    page.refresh_btn.queue_draw()
    return win


def held_read(seconds=5):
    """The feedback's read kept back a while, for a picture of the page as it opens; the read itself put back after."""
    import threading
    hold, repo = threading.Event(), T.feedback.issues_repo
    T.feedback.issues_repo = lambda: (hold.wait(seconds), repo())[1]
    GLib.timeout_add(int(seconds * 1000) + 500, lambda: setattr(T.feedback, "issues_repo", repo) or False)


@scene("96k-feedback-first-open", "Opened for the first time, reading: the page's there, the list says it's loading, nothing on the right")
def _(app):
    win = app.window(opts())
    if os.path.exists(T.fixes.fixes_home()):
        shutil.rmtree(T.fixes.fixes_home(), ignore_errors=True)
    held_read()
    win.open_fixes()
    T.pump(0.3)
    return win


@scene("96l-feedback-opened-again", "Opened again: what was read last time, at once, as it's read again")
def _(app):
    win, page = fixes_tab(app)
    win.tabs.close(("fixes",))
    T.pump(0.2)
    held_read()
    win.open_fixes()
    T.pump(0.3)
    return win


TASK_AGENTS = '''import baudetask as task
task.say("deploying", "site")
task.agent("The deploy of site failed at its upload. Find out why.", title="Why the deploy failed")
task.agent("LINGER: check the release links", wait=False, title="Check the release links")
'''


def task_agents(win):
    """Juniper's task Deploy site run, falling back to two one-shot agents (task.agent): one done (what it said told
    Juniper), one still working; both nested under Juniper and kept with the run. The run's id."""
    server = NET["lab"][0]
    bot = NET["ids"]["Write the release notes for 2.4"]
    os.environ["FAKE_LINGER_S"] = "120"  # (the second agent's turn: going on as it's drawn)
    with server.lock:
        s = server.sessions[bot]
        st = server.tasks.store(s)
        task, script = baude.baudelib.tasks.clean({"name": "Deploy site", "runtime": "python", "script": TASK_AGENTS})
        st.put_script(task, script)
        st.tasks.append(task)
        st.save()
        rid = server.tasks.start(s, task, None, "manual", {})["id"]
    T.wait_for(lambda: st.runs[rid]["status"] == "done" and len(st.runs[rid].get("chats") or ()) == 2, "the run", 30)
    T.wait_for(lambda: any(x["kind"] == "agent" for x in st.read_log(rid)), "what the agent said, logged", 20)
    def gone(*_):  # (for the next scene: the task, and the chats it started archived)
        with server.lock:
            for c in st.runs[rid].get("chats") or ():
                server.sessions[c].meta["archived"] = True
                server.save(server.sessions[c])
            server.changed()
            st.tasks = [t for t in st.tasks if t["id"] != task["id"]]
            st.forget(task["id"])
            st.save()
    win.connect("unrealize", gone)
    return bot, rid, st.runs[rid]["chats"]


@scene("57a-task-started-chats", "A Bot's task run that started two one-shot agents: nested under the Bot in the "
                                 "sessions list, and listed on the run's page as Started, each with its state "
                                 "(one done, one working); what the done one said is in the log, and told the Bot",
       size=(1200 + SIDEBAR, H))
def _(app):
    win = network_window(app, None)
    lab_bots(win)
    bot, rid, _chats = task_agents(win)
    win.sidebar.on_activate(win.sidebar.list, session_row(win, bot))
    T.wait_for(lambda: win.session_id == bot and win.panel.tasks.sid == bot, "Juniper open")
    win.show_panel(True, "tasks")
    win.panel.tasks.open_run({"id": rid, "name": "Deploy site", "status": "done"})
    T.wait_for(lambda: win.panel.tasks.chats_drawn and len(win.panel.tasks.chats_drawn) == 2, "the run's chats")
    T.pump(3)
    return win


@scene("57b-task-agent-chat", "A chat a Bot's task run started, open: its first message from the task, its line a "
                              "link to that run on the Bot's Tasks page", size=(1200 + SIDEBAR, H))
def _(app):
    win = network_window(app, None)
    lab_bots(win)
    _bot, _rid, chats = task_agents(win)
    T.wait_for(lambda: session_row(win, chats[0]) is not None, "the agent's row")
    win.sidebar.on_activate(win.sidebar.list, session_row(win, chats[0]))
    T.wait_for(lambda: win.session_id == chats[0], "the agent's chat open")
    T.pump(1.5)
    return win


@scene("57c-task-agent-link", "That chat's first line clicked: the Bot's chat opened on its Tasks page, at the run "
                              "that started it", size=(1200 + SIDEBAR, H))
def _(app):
    win = network_window(app, None)
    lab_bots(win)
    bot, rid, chats = task_agents(win)
    T.wait_for(lambda: session_row(win, chats[0]) is not None, "the agent's row")
    win.sidebar.on_activate(win.sidebar.list, session_row(win, chats[0]))
    T.wait_for(lambda: win.session_id == chats[0], "the agent's chat open")
    def links():
        return T.find(win, Gtk.Label, lambda x: x.has_css_class("sender-from") and x.get_mapped())
    T.wait_for(lambda: links(), "the line it's from")
    links()[0].emit("activate-link", "sender")
    T.wait_for(lambda: win.session_id == bot and win.panel.tasks.run and win.panel.tasks.run["id"] == rid and
               win.panel.tasks.chats_drawn, "the run, open")
    T.pump(2)
    return win
