"""The semantic themes rig's pieces (a prototype's): an offscreen window (macos.stay_offscreen), the sample theme
(three-chats) and a world of fixture chats and hosts. Run under tests/proto3d/env.sh's throwaway environment."""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
W = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, W); sys.path.insert(1, os.path.join(W, "tests", "proto3d"))
from common import *  # noqa: F401,F403  (proto3d's: offscreen window, pump, frame, make_card)
import scenehost, toolcards  # noqa: E402

SAMPLE = os.path.join(HERE, "three-chats")
NOW = 1_800_000_000


def sample_scene():
    with open(os.path.join(SAMPLE, "scene.json")) as f:
        return json.load(f)


def world():
    return scenehost.World([
        {"kind": "host", "id": "h1", "name": "workshop", "state": "up"},
        {"kind": "chat", "id": "h1/a", "host": "h1", "name": "Fix the flaky upload", "state": "working", "used": NOW - 60},
        {"kind": "chat", "id": "h1/b", "host": "h1", "name": "Tidy the release notes", "state": "waiting", "used": NOW - 600},
        {"kind": "chat", "id": "h1/c", "host": "h1", "name": "Garden planner", "state": "idle", "bot": True, "used": NOW - 3600},
        {"kind": "chat", "id": "h1/d", "host": "h1", "name": "Old notes", "state": "stopped", "used": NOW - 86400},
    ])


def transcripts():
    """The fixture chats' transcripts: (kind, what), a tool call's {name, input, result}."""
    bash = {"name": "Bash", "id": "toolu_t1", "input": {"command": "pytest tests/test_upload.py -q"},
            "result": "3 passed in 0.41s"}
    agent = {"name": "Agent", "id": "toolu_t2",
             "input": {"description": "Find where the release notes are built", "prompt": "Look through the docs"}}
    return {
        "h1/a": [("user", "The upload test fails one run in five. Can you find why?"),
                 ("reply", "It races the temp folder's cleanup. Running the test again with the fix:"), ("tool", bash)],
        "h1/b": [("user", "Tidy the release notes for 2.4"), ("tool", agent),
                 ("reply", "Shall I fold the three bug-fix lines into one?")],
        "h1/c": [("user", "What should I plant in March?"), ("reply", "Broad beans, onion sets and early potatoes.")],
    }


APP = []


def app():
    """tests/test_baude.py's App, made once: a BaudeApp with a host in this process, a throwaway environment of its
    own (its claude config, where the chats below are written) and the fake claude."""
    if not APP:
        sys.path.insert(0, os.path.join(W, "tests"))
        sys.argv = sys.argv[:1] + [a for a in sys.argv[1:] if not a.startswith("-Apple") and a != "YES"]
        import test_baude
        APP.append(test_baude.App())
    return APP[0]


def session_id(oid):
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "baude-scene:" + oid))


def write_chats(wd, transcripts):
    """Each of wd's chats written as claude writes a session (in the claude config's projects, its folder one of its
    own in the home folder, named for it), its transcript's messages and tool calls in it, titled with its name: real chats for the app's pages to
    open. Returns thing → its session's id, for the providers (default_providers' session)."""
    import re
    for o in wd.objects:
        if o["kind"] != "chat":
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", (o.get("name") or o["id"]).lower()).strip("-")
        sid, cwd = session_id(o["id"]), os.path.join(os.path.expanduser("~"), slug)
        os.makedirs(cwd, exist_ok=True)
        d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
        os.makedirs(d, exist_ok=True)
        base = {"cwd": cwd, "sessionId": sid, "isSidechain": False}
        recs = []
        for kind, what in transcripts.get(o["id"]) or [("user", o.get("name") or "")]:  # (one with nothing said yet:
            # its name, what it was begun with, so that it's listed)
            if kind == "user":
                recs.append({**base, "type": "user", "message": {"role": "user", "content": [{"type": "text", "text": what}]}})
            elif kind == "reply":
                recs.append({**base, "type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "text", "text": what}]}})
            elif kind == "tool":
                recs.append({**base, "type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": what["id"], "name": what["name"], "input": what["input"]}]}})
                if "result" in what:
                    recs.append({**base, "type": "user", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": what["id"], "content": what["result"]}]}})
        recs.append({"type": "custom-title", "customTitle": o.get("name") or "", "sessionId": sid})
        with open(os.path.join(d, f"{sid}.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")
    return lambda o: session_id(o["id"])


def load_theme_css():
    css = Gtk.CssProvider()
    with open(os.path.join(SAMPLE, "theme.css")) as f:
        css.load_from_string(f.read())
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)


def chat_providers(wd, transcripts, **more):
    """The providers with the app's own chat pages (ChatPage): wd's chats written as sessions (write_chats) and opened
    by the app's windows."""
    return scenehost.default_providers(wd, transcripts=transcripts, app=app().app,
                                       session=write_chats(wd, transcripts), **more)


def chat_page(v, oid):
    """The chat page of the panel showing chat oid, once its chat's open in it."""
    n = next(n for n in v.nodes.values() if isinstance(n.widget, scenehost.ChatPage) and (v.obj_of(n) or {}).get("id") == oid)
    import test_baude
    test_baude.wait_for(lambda: n.widget.win is not None and n.widget.win.session_id == n.widget.sid, f"{oid} opened")
    return n.widget


def view(scene=None, wd=None, trusted=True, providers=None, opened=None):
    wd = wd or world()
    return scenehost.SceneView(scene or sample_scene(), wd, providers or chat_providers(wd, transcripts()),
                               theme=SAMPLE, trusted=trusted, open=(opened.append if opened is not None else None))


def true_point(v, widget, x=None, y=None):
    """Where a point of a panel's widget is drawn, in the window's coordinates: through the panel's true projection
    (not GTK's fitted transform)."""
    n = next(n for n in v.nodes.values() if n.face is not None and (widget is n.face or widget.is_ancestor(n.face)))
    x = widget.get_width() / 2 if x is None else x
    y = widget.get_height() / 2 if y is None else y
    ok, p = widget.compute_point(n.face, Graphene.Point().init(x, y))
    sx, sy = cards3d.project(v.stage_matrix(n), p.x, p.y)
    ok, q = v.compute_point(v.get_root(), Graphene.Point().init(sx, sy))
    tx, ty = v.get_root().get_surface_transform()
    return q.x + tx, q.y + ty
