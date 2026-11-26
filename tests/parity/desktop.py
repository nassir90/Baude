#!/usr/bin/env python3
"""Baude's side of the parity rig: each scene of tests/parity/scenes.json drawn from tests/parity/fixtures, offscreen,
as <out>/desktop/<theme>/<scene>.png, and what's on screen in it as <out>/desktop/<scene>.json (dump.py's items: the
text, buttons, inputs, switches and images a user sees, top to bottom). A render rig, not a test: nothing's checked.

    python3 tests/parity/desktop.py [--out DIR] [-k NAME] [--theme light|dark]

Runs against tests/fake_claude.py in a throwaway HOME and config dirs (tests/test_baude.py's), never the real claude,
never the real data; on macOS its windows never reach the screen (macos.stay_offscreen)."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
tempfile.tempdir = "/tmp"  # (every throwaway path lands under /tmp: none of it names a user's own temp folder)
with open(os.path.join(HERE, "fixtures", "sessions.json")) as f:
    HOME = json.load(f)["host"]["home"]  # (the phone's fixture host has the same one)
shutil.rmtree(HOME, ignore_errors=True)
os.makedirs(HOME)
os.environ["HOME"] = HOME  # (before anything reads it: ~ is this run's)
os.environ.setdefault("GSK_RENDERER", "cairo")
sys.path.insert(0, ROOT)
if sys.platform == "darwin":
    import macos
    macos.stay_offscreen()
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, HERE)

import argparse  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402

import screenshots as S  # noqa: E402  (tests/test_baude's throwaway env, the window machinery, capture)
import scenelib  # noqa: E402
import scenes_chat  # noqa: E402
import scenes_panels  # noqa: E402
from screenshots import T, Adw, Gtk, baude  # noqa: E402
import update  # noqa: E402
import baudelib.wording as wording  # noqa: E402
import dump  # noqa: E402
import gitfixture  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
SIZE = (scenelib.W, scenelib.H)


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def folder(name):
    """A folder of the fixture host's home: fixtures/<name>/'s files in it, if the fixtures have any."""
    path = os.path.join(HOME, name)
    src = os.path.join(FIX, name)
    if os.path.isdir(src):
        shutil.copytree(src, path, dirs_exist_ok=True)
    os.makedirs(path, exist_ok=True)
    return path


def transcript_lines(name, cwd, lines=None):
    with open(os.path.join(FIX, name)) as f:
        recs = [r.replace("@CWD@", cwd) for r in f.read().splitlines() if r.strip()]
    title = [r for r in recs if '"custom-title"' in r]
    body = [r for r in recs if r not in title]
    return (body if lines is None else body[:lines]) + title


def write_sessions(lines=None, transcript=None, subagents=None):
    """fixtures/sessions.json's sessions as this machine's Claude Code keeps them, each its age (the first's from the
    fixture named by transcript, if given); their paths, in the fixture's order."""
    paths = []
    now = time.time()
    for s in fixture("sessions.json")["sessions"]:
        cwd = folder(s["folder"])
        if s["transcript"]:
            recs = transcript_lines(transcript if not paths and transcript else s["transcript"], cwd, lines)
        else:
            recs = [json.dumps(r) for r in (
                {"type": "user", "cwd": cwd, "sessionId": s["id"], "message": {"role": "user", "content": s["title"]}},
                {"type": "assistant", "cwd": cwd, "sessionId": s["id"],
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}]}},
                {"type": "custom-title", "customTitle": s["title"], "sessionId": s["id"]})]
        d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{s['id']}.jsonl")
        with open(path, "w") as f:
            f.write("\n".join(recs) + "\n")
        os.utime(path, (now - s["ago"], now - s["ago"]))
        if subagents and not paths:  # (the first session's subagents' transcripts, where claude keeps them)
            into = os.path.join(d, s["id"], "subagents")
            os.makedirs(into, exist_ok=True)
            for name in os.listdir(os.path.join(FIX, subagents)):
                with open(os.path.join(FIX, subagents, name)) as f:
                    text = f.read().replace("@CWD@", cwd)
                with open(os.path.join(into, name), "w") as f:
                    f.write(text)
        paths.append(path)
    return paths


def apply_settings():
    keep = {k: v for k, v in fixture("settings.json").items() if k != "description"}
    baude.save_settings(**keep)


def fixture_changelog():
    log = fixture("changelog.json")
    update.changelog = lambda commit, root=None: [tuple(c) for c in log["commits"]]
    update.version = lambda commit, root=None: (log["commit"], log["made"])
    update.STARTED = log["commit"]


def pick_header(win, s):
    """The chat's model, effort and mode as the session has them, picked in the window's pickers as a user would."""
    T.wait_for(lambda: s["model"] in win.model_dd.values, "its models")
    row = next(r for r in win.model_dd._rows() if r.value == s["model"])
    win.model_dd._activate(row)
    T.pump(0.5)
    for dd, value in ((win.effort_dd, s["effort"]), (win.mode_dd, s["mode"])):
        T.wait_for(lambda dd=dd, value=value: value in dd.values, f"the {value} pick")
        dd.set_selected(dd.values.index(value))
    T.pump(0.5)


def git_scene(win, sc):
    """The Git page read and set as the scene says: the branches picked, every branch, GitHub's part, the branch list
    open, a commit's card."""
    git, page = sc["git"], win.panel.git_page
    T.wait_for(lambda: page.top and page.commits, "the log", 20)
    if git.get("pick"):
        page.pick(git["pick"])
        T.wait_for(lambda: page.state.get("picked") == git["pick"], "the branches picked", 20)
    if git.get("every"):
        page.all_btn.set_active(True)
        T.wait_for(lambda: page.state.get("every"), "every branch", 20)
    if sc.get("github"):
        T.wait_for(lambda: page.pulls_scroll.get_visible(), "the pull requests", 20)
        if len(git.get("pick") or ()) == 1:
            T.wait_for(lambda: "Pull request #128" in page.info.get_text(), "the branch's pull request", 20)
    if git.get("branches"):
        page.picker.popup()
    if git.get("commit"):
        page.show_commit(next(c["sha"] for c in page.commits if c["subject"] == git["commit"]))
        T.wait_for(lambda: any("garden/upload.py" in label.get_text() for label in T.find(page, Gtk.Label)),
                   "the commit's files", 20)
    T.pump(0.4)


def write_glossary(cwd, name, session):
    """fixtures/<name>'s names as this machine's Signal keeps them: <cwd>/.baude/signal.db, each mentioned in session."""
    import sqlite3
    import lookup
    baude.Signal._instances.clear()  # (each scene's Signal opens its glossary afresh)
    store = os.path.join(cwd, ".baude")
    shutil.rmtree(store, ignore_errors=True)
    if not name:
        return
    os.makedirs(store)
    con = sqlite3.connect(os.path.join(store, "signal.db"))
    con.executescript(lookup.REF_SCHEMA)
    for r in fixture(name)["refs"]:
        ref = lookup.Ref(r["surface"], r["kind"], r["status"], r["canonical"], r["reason"], r["data"], r["locations"])
        assert ref.summary() == r["summary"], (r["surface"], ref.summary())  # (the fixture says what Baude says)
        eid = con.execute("INSERT INTO entity (checkout, scope, kind, canonical, status, reason, data) "
                          "VALUES ('', '', ?, ?, ?, ?, ?)",
                          (r["kind"], r["canonical"], r["status"], r["reason"], json.dumps(r["data"]))).lastrowid
        con.execute("INSERT INTO alias (checkout, scope, surface, entity_id) VALUES ('', '', ?, ?)", (r["surface"], eid))
        for loc in r["locations"]:
            con.execute("INSERT INTO location (entity_id, role, path, line, note, rev) VALUES (?, ?, ?, ?, ?, ?)",
                        (eid, loc["role"], loc["path"], loc.get("line"), loc.get("note"), loc.get("rev")))
        con.execute("INSERT INTO mention (entity_id, session, message, surface) VALUES (?, ?, '', ?)",
                    (eid, session, r["surface"]))
    con.commit()
    con.close()


def open_chat(app, sc):
    shutil.rmtree(os.path.join(HOME, "garden"), ignore_errors=True)  # (what an earlier scene put in the folder)
    shown = fixture("sessions.json")["sessions"][sc.get("session", 0)]
    write_glossary(folder("garden"), sc.get("signal"), shown["id"])
    if sc.get("files"):  # (the scene's files join the chat's folder)
        shutil.copytree(os.path.join(FIX, sc["files"]), folder("garden"), dirs_exist_ok=True)
    if sc.get("git") is not None:  # (the chat's folder is the fixture repository)
        gitfixture.make(folder("garden"))
    path = write_sessions(sc.get("lines"), sc.get("transcript"), sc.get("subagents"))[sc.get("session", 0)]
    if sc.get("meta"):  # (Meta's conversation, as its store keeps it)
        baude._META[:] = [{k: v for k, v in fixture(sc["meta"]).items() if k != "description"}]
    win = app.window(scenelib.opts(cwd=folder("garden")))
    info = baude.read_session_info(path)
    win.resume_session(info)
    T.wait_for(lambda: win.session_id == info["id"] and win.transcript.get_first_child() is not None, "its history")
    T.pump(0.8)
    pick_header(win, shown)
    if shown["state"] == "working":  # (at a turn, as tests/screenshots.py's 03-working)
        win.set_busy(True)
        T.pump(0.4)
    if sc.get("open"):
        for g in T.find(win, baude.ToolGroup):
            g.toggle.set_expanded(True)
        for card in list(win.tool_cards.values()):
            for t in ("input_toggle", "output_toggle"):
                if getattr(card, t, None) is not None:
                    getattr(card, t).set_expanded(True)
        T.pump(0.4)
    if sc.get("ask"):
        card = win.tool_cards[sc["ask"]]
        if card.state == "interrupted":  # (a finished chat's call with no result: asking now)
            card.set_state("running")
        win.permission_card(f"perm-{sc['ask']}", card.name, card.input, sc["ask"])
        T.pump(0.4)
    if sc.get("page"):
        win.show_panel(True, {"signal": "refs"}.get(sc["page"], sc["page"]))
        T.pump(0.4)
        if sc.get("git") is not None:
            git_scene(win, sc)
        if sc.get("meta") and sc.get("open"):  # (Meta's cards' Input and Output opened)
            for b in T.find(win.panel.meta, Gtk.ToggleButton):
                if b.has_css_class("tool-toggle"):
                    b.set_active(True)
            T.pump(0.4)
    if sc.get("card"):
        win.panel.signal_page.show_ref(sc["card"])
        T.pump(0.4)
    if sc.get("signal_menu"):
        win.signal_btn.popup()
        T.pump(0.4)
    for t in sc.get("running", []):  # (background tasks heard as started)
        win.on_task_event("task_started", dict(t))
    if sc.get("process"):
        row = win.panel.proc_rows[sc["process"]]
        win.panel.open_process(row) if sc.get("full") else row.set_expanded(True)
    if sc.get("view"):
        win.tool_cards[sc["view"]].view_btn.set_active(True)
    if sc.get("reveal"):
        win.show_folder(os.path.join(HOME, "garden", sc["reveal"]))
    if sc.get("gallery"):
        win.panel.files_page.show_gallery(True, os.path.join(HOME, "garden", sc["gallery"]), save=False)
        T.pump(1.5)  # (the pictures' shapes and thumbnails load off the main thread)
    if sc.get("hold"):
        exp = next(e for e in T.find(win.panel.files_page.view, Gtk.TreeExpander)
                   if getattr(e, "node", None) is not None and os.path.basename(e.node.path) == sc["hold"])
        win.panel.files_page.menu(exp, 40, 12)
    if sc.get("running") or sc.get("process") or sc.get("view") or sc.get("reveal") or sc.get("gallery") or sc.get("hold"):
        T.pump(0.6)
    if sc.get("state") == "working":
        win.set_busy(True)
    if sc.get("queued"):
        win.input.get_buffer().set_text(sc["queued"])
        win.queue_message()
        win.queued[-1].show_buttons(True)
        T.pump(0.4)
    if sc.get("down"):  # (the rig's chats are this machine's: stand in for a network host gone, as update_banner shows it)
        win.banner.set_title(wording.say("hosts.down", host=fixture("sessions.json")["host"]["name"]))
        win.banner.set_revealed(True)
        win.composer_box.gate("host", True)
    if sc.get("draft"):
        win.input.get_buffer().set_text(sc["draft"])
    if sc.get("cut"):
        win.cut_pending = time.time()
        win.show_cut(win.cut_pending)
    if sc.get("typed"):  # (a character at a time, as typing: the / or @ that starts a list is the one just typed)
        if sc["typed"].startswith("/"):
            T.wait_for(lambda: len(win.completer.commands) > len(baude.LOCAL_COMMANDS), "commands")
        buf = win.input.get_buffer()
        buf.set_text("")
        for ch in sc["typed"]:
            buf.insert_at_cursor(ch)
        T.pump(0.6)
    if sc.get("open"):
        for m in T.find(win, (baude.chatparts.CompactMark, baude.chatparts.LoneSummary)):
            if m.fold is not None:
                m.fold.set_expanded(True)
    vadj = win.scroller.get_vadjustment()
    vadj.set_value(vadj.get_upper())  # (at its end, as a chat opens)
    T.pump(0.4)
    if "into" in sc:  # (scrolled to the open summary's heading: that many px above the chat's top)
        fold = next(m.fold for m in T.find(win, (baude.chatparts.CompactMark, baude.chatparts.LoneSummary)) if m.fold is not None)
        ok, at = fold.compute_point(win.scroller, baude.Graphene.Point())
        win.hold_value(vadj.get_value() + at.y + sc["into"])
        T.pump(0.6)
    if sc.get("ref"):  # (a tap on the Signal link in the reply that names it)
        label = next(w for w in T.find(win, Gtk.Label) if sc["ref"] in (w.get_label() or "") and w.get_mapped())
        ok, at = label.compute_point(win, baude.Graphene.Point().init(10, 10))
        win.pointer = (at.x, at.y)
        win.open_ref(sc["ref"], label)
        T.pump(0.4)
    if sc.get("lightbox"):  # (the first picture of the chat, opened large)
        baude.lightbox.Lightbox.FLY_MS = 0
        w = next(w for w in baude.widgets.walk(win.transcript)
                 if getattr(w, "image_actions", None) is not None and not isinstance(w, Gtk.Overlay))
        w.image_actions.view()
        T.pump(0.6)
    return win


def bots_scene(app, sc):
    """fixtures/bots.json's chats, made Bots on the network's hosts (scenes_panels: lab-mac is up, build-box gone, its
    chats as last seen). Baude lists one host's chats at a time: home-bots has the gone host picked, chat-bot the one
    that is up, with its Bot open."""
    fx = fixture(sc["bots"])
    gone = sc["name"] == "home-bots"
    win = scenes_panels.network_window(app, None, host="box" if gone else "lab")
    ids, server = scenes_panels.NET["ids"], scenes_panels.NET["lab"][0]
    mine = [s for s in fx["sessions"] if s["gone"] == gone]
    for s in mine:
        if gone:
            scenes_panels.NET["box"][1].sessions[ids[s["title"]]]["bot"] = {"name": s["bot"]}
        else:
            with server.lock:
                server.set_bot(server.sessions[ids[s["title"]]], {"name": s["bot"]})
    win.connect("unrealize", lambda *_: [server.set_bot(server.sessions[ids[s["title"]]], None) for s in mine
                                         if not gone])
    win.sidebar.refresh()
    T.pump(1.5)
    if not gone:
        win.sidebar.on_activate(win.sidebar.list, scenes_panels.session_row(win, ids[mine[0]["title"]]))
        T.wait_for(lambda: win.session_id == ids[mine[0]["title"]], "the Bot open")
        T.pump(0.8)
    return win


THREADS = {}  # (a threads fixture's hosts, made once: name -> (Server, Remote); "ids": fixture id -> the host's)


def threads_network(name):
    """A threads fixture's chats, started on two hosts of their own (each saying it's on a machine of its own, as
    scenes_panels' are), its Bot made one and the chats it started started by it (spawned_by: its id there)."""
    if THREADS:
        return THREADS
    import threading
    from baudelib.server import Server

    class Server(Server):
        def _info(self):
            return {**super()._info(), "host": f"{self.me['name']}.lan"}
    fx = fixture(name)
    net = baude.network()
    env = {"FAKE_CLAUDE_SESSION_IDS": "1", "FAKE_CLAUDE_TRANSCRIPTS": "1"}
    for host in (fx["host"]["name"], fx["other"]["name"]):
        s = Server(state_dir=os.path.join(T.TMP, "servers", host), group="Lab", name=host,
                   socket_path=os.path.join(os.environ["BAUDE_RUNTIME_DIR"], f"{host}.sock"), log=lambda _text: None,
                   env=env)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        THREADS[host] = (s, net.add({"via": "unix", "path": s.socket_path}))
    ids = THREADS["ids"] = {}
    for s in sorted(fx["sessions"], key=lambda s: -s["ago"]):  # (the oldest first: said in the fixture's order)
        server = THREADS[s["on"]][0]
        started = server.start({"prompt": s["title"], "title": s["title"], "cwd": folder(s["folder"]), "mode": s["mode"],
                                "spawned_by": ids.get(s.get("spawned_by"))})
        ids[s["id"]] = started.id
        T.wait_for(lambda: started.asks, f"{s['title']}: asking")
        with server.lock:  # (let do what it asks: idle, as the fixture has it)
            rid, ask = next(iter(started.asks.items()))
            server.take_input(None, started, {"type": "control_response", "response": {
                "subtype": "success", "request_id": rid,
                "response": {"behavior": "allow", "updatedInput": ask["request"]["input"]}}})
        T.wait_for(lambda: started.state() == "idle", f"{s['title']}: done")
        if s.get("bot"):
            with server.lock:
                server.set_bot(started, {"name": s["bot"]})
        time.sleep(0.05)
    remotes = [THREADS[fx["host"]["name"]][1], THREADS[fx["other"]["name"]][1]]
    T.wait_for(lambda: sum(x["server"] in remotes for x in net.sessions()) == len(ids), "the hosts' sessions")
    return THREADS


def threads_scene(app, sc):
    """A threads fixture's chats on their two hosts, the one sc picks listed (the fixture's first, without)."""
    fx = fixture(sc["threads"])
    net = threads_network(sc["threads"])
    host = sc.get("pick") or fx["host"]["name"]
    server = THREADS[fx["host"]["name"]][0]  # (the Bot, the fixture's first chat: archived, or deleted, on its host as
    bot = next((x for x in server.sessions.values() if x.meta.get("bot")), None)  # the scene says; scenes.json has
    if bot and sc.get("bot") == "trashed":  # the trashed one last, as a deleted chat stays so)
        server.delete(bot, lambda *_a, **_k: None)
    elif bot and bot.meta.get("archived") != (sc.get("bot") == "archived"):
        with server.lock:
            bot.meta["archived"] = sc.get("bot") == "archived"
            server.save(bot)
            server.changed()
    baude.save_settings(new_session_host=baude.host_key(net[host][1]), sidebar_groups=None,
                       collapsed_folders=[] if sc.get("bot") == "archived" else ["archived"])
    win = app.window(scenelib.opts(cwd=folder("garden")))
    T.pump(1.5)
    win.sidebar.refresh()
    T.pump(1)
    return win


def build(app, sc):
    name = sc["name"]
    if sc.get("bots"):
        return bots_scene(app, sc)
    if sc.get("threads"):
        return threads_scene(app, sc)
    if sc.get("github"):  # (gh signed in, with the fixture's pull requests)
        gitfixture.stub_github(True, os.path.join(HOME, "garden"))
    if name.startswith("home"):
        write_sessions()
        win = app.window(scenelib.opts(cwd=folder("garden")))
        if name == "home":
            return win
        title = fixture("sessions.json")["sessions"][0]["title"]
        T.wait_for(lambda: scenelib.sidebar_row(win, title) is not None, "rows")
        T.pump(1)  # (the list settled: the row kept)
        if name == "home-row-menu":
            win.sidebar.show_menu(scenelib.sidebar_row(win, title), 120, 14)
        elif name == "home-host-menu":
            scenes_panels.host_right_click(win)
        elif name == "home-add-host":
            win.ask_add_host()
        return win
    if name.startswith("chat") or name.startswith("panel"):
        return open_chat(app, sc)
    if name == "menu":
        win = open_chat(app, sc)
        button = next(b for b in T.find(win, Gtk.MenuButton) if b.get_icon_name() == "open-menu-symbolic")
        button.popup()
        return win
    if name == "feedback":
        win = open_chat(app, sc)
        win.open_feedback()
        return win
    if name == "settings":
        win = open_chat(app, sc)
        win.open_settings()
        return win
    if name == "changelog":
        win = open_chat(app, sc)
        update.changelog_dialog().present(win)
        return win
    if name == "databases":
        win = app.window(scenelib.opts())
        fx = fixture("databases.json")
        sv = win.schema
        sv.set_visible(True)
        sv.schema, sv.database, sv.focus_column = baude.pg_normalize(fx["raw"]), fx["database"], None
        sv.servers, sv.dbs = [fx["server"]], {fx["server"]["container"]: [fx["database"]]}
        sv.syncing = True  # (drawn from the fixture, not read from a server)
        sv.server_dd.set_model(Gtk.StringList.new([f"{fx['server']['container']} (Postgres {fx['server']['version']})"]))
        sv.db_dd.set_model(Gtk.StringList.new([fx["database"]]))
        sv.syncing = False
        sv.show_namespace("public")
        T.wait_for(lambda: sv.canvas.get_width() > 0 and sv.canvas.cards, "laid out")
        T.pump(0.3)
        sv.canvas.fit()
        return win
    if name == "kubernetes":
        return scenes_chat.kube_window(app)[0]
    if name == "kubernetes-home":
        return scenes_chat.kube_home(app)[0]
    if name == "kubernetes-column-filter":
        baude.save_settings(kube_hidden={"pods": {"Status": ["Failed", "Succeeded"]}})
        win, kv = scenes_chat.kube_window(app)
        T.pump(0.3)
        filters = kv.col_filters
        hovered = filters.buttons[filters.titles.index("Namespace")].get_parent().get_parent()
        hovered.set_state_flags(Gtk.StateFlags.PRELIGHT, False)  # (the pointer over its heading)
        filters.buttons[filters.titles.index("Status")].popup()
        return win
    if name == "docker-column-filter":
        baude.save_settings(docker_hidden={"containers": {"Status": ["Exited"]}})
        return scenes_chat.docker_window(app, page="containers")[0]
    if name == "kubernetes-shell":
        win, kv = scenes_chat.kube_window(app)
        T.pump(0.3)
        kv.show_shell("shop/api-6f7c-mnopq")
        T.wait_for(lambda: kv.shell_term is not None and "/app#" in kv.shell_term.screen()["screen"], "its prompt")
        kv.shell_term.type("ls\n")
        T.pump(0.5)
        win.connect("destroy", lambda *_: kv.hide_shell())
        return win
    if name == "kubernetes-dock":
        win, kv = scenes_chat.kube_window(app)
        T.pump(0.3)
        kv.show_logs("jobs/worker-5c4b-klmno")
        kv.show_shell("shop/api-6f7c-mnopq")
        T.wait_for(lambda: kv.shell_term is not None and "/app#" in kv.shell_term.screen()["screen"], "its prompt")
        kv.shell_term.type("ls\n")
        T.pump(0.5)
        win.connect("destroy", lambda *_: kv.dock.close_all())
        return win
    if name == "bot-settings":
        win = open_chat(app, sc)
        title = fixture("sessions.json")["sessions"][0]["title"]
        T.wait_for(lambda: scenelib.sidebar_row(win, title) is not None, "rows")
        row = scenelib.sidebar_row(win, title)
        win.sidebar.ask_bot({**row.info, "remote": types.SimpleNamespace(request=lambda *a, **k: None)})
        return win
    if name.startswith("feedback-ongoing"):
        with open(os.path.join(FIX, "gallery", "docs", "flow.png"), "rb") as f:
            png = f.read()
        return scenes_panels.fixes_tab(app, shots={"12/1/1-before.png": png, "12/1/1-after.png": png})[0]
    return None


def capture(win, path):
    """The window as GTK draws it, with its open popovers where they are: the picture grown to hold one that reaches
    past the window's edge (☰'s does), not cut at it."""
    T.wait_for(lambda: (win.get_width(), win.get_height()) == SIZE, f"window at {SIZE}", 5)
    T.pump(0.6)  # (layout, the reveal animations and popovers settled)
    img = S.render_widget(win, path)
    pops = []
    for pop in dump._popovers(win):
        if pop.get_width() and pop.get_height():
            pops.append((S.render_widget(pop, path + ".popover.png"), dump._origin(pop)))
            os.remove(path + ".popover.png")
    w = max([img.width] + [x + shot.width for shot, (x, _) in pops])
    h = max([img.height] + [y + shot.height for shot, (_, y) in pops])
    canvas = S.Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(img, (0, 0))
    for shot, at in pops:
        canvas.paste(shot, at, shot)
    canvas.save(path)


def areas(win):
    """The window's parts, outermost first, as the dump names them."""
    header = next(iter(T.find(win, Adw.HeaderBar)), None)
    return [(header, "header"), (win.sidebar, "sidebar"), (win.panel, "panel"), (win.scroller, "chat"),
            (win.composer, "composer"), (win.status.get_parent(), "status")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("-k", default="")
    ap.add_argument("--theme", choices=("light", "dark"))
    args = ap.parse_args()
    out = os.path.join(os.path.abspath(args.out), "desktop")
    os.makedirs(out, exist_ok=True)
    fixture_changelog()
    shutil.copytree(os.path.join(FIX, "tasks"), "/tmp/parity/tasks", dirs_exist_ok=True)  # (the fixture transcript's path)
    if any(sc.get("bots") and args.k in sc["name"] for sc in fixture_scenes()):
        scenes_panels.network_fixture()  # (its servers first: before any window's host process is among the sessions)
    for sc in fixture_scenes():
        if sc.get("threads") and args.k in sc["name"]:
            threads_network(sc["threads"])  # (the same)
            break
    app = T.App()
    style = Adw.StyleManager.get_default()
    failed = 0
    for sc in fixture_scenes():
        if args.k not in sc["name"]:
            continue
        for theme, scheme in (("light", Adw.ColorScheme.FORCE_LIGHT), ("dark", Adw.ColorScheme.FORCE_DARK)):
            if args.theme not in (None, theme):
                continue
            style.set_color_scheme(scheme)
            S.reset_state()
            apply_settings()
            app.next_size = SIZE
            win = None
            try:
                win = build(app, sc)
                if win is None:
                    with open(os.path.join(out, f"{sc['name']}.json"), "w") as f:
                        json.dump({"scene": sc["name"], "app": "desktop", "missing": "no desktop scene"}, f, indent=1)
                    break
                os.makedirs(os.path.join(out, theme), exist_ok=True)
                capture(win, os.path.join(out, theme, f"{sc['name']}.png"))
                if theme == "light" or args.theme == "dark":
                    with open(os.path.join(out, f"{sc['name']}.json"), "w") as f:
                        json.dump({"scene": sc["name"], "app": "desktop", "size": list(SIZE),
                                   "items": dump.items(win, areas(win))}, f, indent=1, ensure_ascii=False)
                print(f"ok    {sc['name']} ({theme})")
            except Exception as e:  # noqa: BLE001 — keep going, report at the end
                failed += 1
                print(f"FAIL  {sc['name']} ({theme}): {e!r}")
            finally:
                for w in Gtk.Window.list_toplevels():
                    if isinstance(w, baude.BaudeWindow):
                        T.close(w)
                baude._META.clear()
                S.reset_state()
                shutil.rmtree(os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects"), ignore_errors=True)
                gitfixture.stub_github(False)
                gitfixture.unmake(os.path.join(HOME, "garden"))
                baude.GIT_CARDS.clear()
    print(f"{failed} failures -> {out}")
    return 1 if failed else 0


def fixture_scenes():
    with open(os.path.join(HERE, "scenes.json")) as f:
        return json.load(f)["scenes"]


if __name__ == "__main__":
    sys.exit(main())
