"""The Christmas scene's rig (a prototype's): the theme installed as its .baudetheme through this machine's host
(theme_install, the tests' host in this process, its HOME a throwaway's) and trusted (theme_trust), drawn over a real
Baude window (the dashboard) and fixture hosts and chats as the wire has them (Info, Sessions). Import after rig."""
import base64, io, zipfile
from rig import *  # noqa: F401,F403
sys.path.insert(0, os.path.join(W, "tests"))
sys.argv = sys.argv[:1] + [a for a in sys.argv[1:] if not a.startswith("-Apple") and a != "YES"]
import test_baude as T  # noqa: E402  (a throwaway environment, a host in this process, the fake claude)
import themelook  # noqa: E402
from baudelib import wire  # noqa: E402

THEME = os.path.join(HERE, "christmas")

# the mesh as this host's info has it, and each host's sessions answer (fixture names)
INFO = {"id": "h-workshop", "name": "workshop", "version": 1,
        "mesh": {"members": [{"id": "h-attic", "name": "attic-pi", "group": "", "host": "attic-pi", "version": 1, "hops": 1,
                              "through": None, "providers": [], "heads": []}],
                 "links": [], "proxy": None, "inactive": [], "found": [],
                 "gone": [{"id": "h-studio", "name": "studio"}], "newest": None}}
SESSIONS = {
    "h-workshop": [
        {"id": "s1", "title": "Fix the flaky upload", "state": "working", "said": NOW - 60},
        {"id": "s2", "title": "Tidy the release notes", "state": "waiting", "said": NOW - 600},
        {"id": "s3", "title": "Spring planting", "state": "idle", "said": NOW - 3600, "bot": {"name": "Garden planner"}},
        {"id": "s4", "title": "Old notes", "state": "stopped", "said": NOW - 86400},
        {"id": "s5", "title": "Card list", "state": "idle", "said": NOW - 7200}],
    "h-attic": [
        {"id": "s6", "title": "Sensor graphs", "state": "working", "said": NOW - 120},
        {"id": "s7", "title": "Backup check", "state": "idle", "said": NOW - 5000}],
    "h-studio": [
        {"id": "s8", "title": "Mix notes", "state": "stopped", "said": NOW - 90000},
        {"id": "s9", "title": "Lyrics draft", "state": "stopped", "said": NOW - 99000}],
}
LETTERS = {
    "h-workshop/s1": [("user", "The upload test fails one run in five. Can you find why?"),
                      ("reply", "It races the temp folder's cleanup. Running the test again with the fix:")],
    "h-workshop/s2": [("user", "Tidy the release notes for 2.4"),
                      ("reply", "Shall I fold the three bug-fix lines into one?")],
    "h-workshop/s3": [("user", "What should I plant in March?"), ("reply", "Broad beans, onion sets and early potatoes.")],
    "h-workshop/s5": [("user", "Who's still to get a card?"), ("reply", "Aunt May, the Okafors and the neighbours at 12.")],
    "h-attic/s6": [("user", "Graph the greenhouse sensors for the week"), ("reply", "Plotting the seven days now.")],
}


def checked(kind, msg):
    """msg checked against schema/types (the wire's own check): fixtures as the wire has them."""
    out = []
    wire._validate(wire.DEFS[kind], msg, kind, out)
    assert not out, out
    return msg


def world(dashboard=True):
    things = scenehost.World.from_info(checked("Info", INFO))
    for host, sessions in SESSIONS.items():
        [checked("Session", s) for s in sessions]
        things += scenehost.World.from_sessions(host, sessions)
    return scenehost.World(things + ([{"kind": "window", "id": "w1", "name": "Baude", "used": NOW}] if dashboard else []))


def pack():
    """The theme's folder as its .baudetheme (a zip of it)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(THEME):
            for f in files:
                if f.endswith(".pyc") or "__pycache__" in root:
                    continue
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, THEME))
    return buf.getvalue()


def install():
    """Installed and trusted through the host, and picked: the theme as Theme.json has it."""
    got = themelook.ask_host({"op": "theme_install", "pack": base64.b64encode(pack()).decode()}, timeout=20)
    assert "theme" in got, got
    theme = got["theme"]
    assert "error" not in theme, theme
    got = themelook.ask_host({"op": "theme_trust", "name": theme["name"], "code": theme["code"]})
    assert got.get("theme", {}).get("trusted"), got
    themelook.pick(theme["name"])
    return got["theme"]


APP = None


def dashboard():
    """A real Baude window, its content the dashboard's."""
    global APP
    APP = APP or T.App()
    APP.next_size = (1280, 800)
    return APP.window()


def view(theme, wd, win, opened=None, trusted=True):
    return scenehost.SceneView(json.loads(json.dumps(theme["scene"])), wd, {
        **scenehost.default_providers(wd, windows={"w1": win}, transcripts=LETTERS)},
        theme=themepacks_folder(theme["name"]), trusted=trusted, open=(opened.append if opened is not None else None))


def themepacks_folder(name):
    from baudelib import themepacks
    return themepacks.folder(name)


def at_world(v, win, nid, x=0, y=0, z=0):
    """Where a point of a node's is drawn, in the window's coordinates."""
    view_, proj, px, _e = v.matrices(v.get_width(), v.get_height())
    sx, sy, _z, w = scenehost._vec(v.world_matrix(v.nodes[nid]).multiply(view_).multiply(proj).multiply(px), x, y, z)
    ok, q = v.compute_point(v.get_root(), Graphene.Point().init(sx / w, sy / w))
    tx, ty = v.get_root().get_surface_transform()
    return q.x + tx, q.y + ty


def settle(v, seconds, step=1 / 60):
    """The scene's time moved on by seconds, a frame at a time (its code's animations), drawn."""
    for _ in range(int(seconds / step)):
        v.advance(step)
        pump(0.004)
    pump(0.3)


def where_hit(v, win, nid, tries):
    """A point of nid's (one of tries, in its space) drawn in the window and nearest there, in the window's
    coordinates: where a click lands on it."""
    tx, ty = win.get_surface_transform()
    for p in tries:
        x, y = at_world(v, win, nid, *p)
        if 4 < x - tx < v.get_width() - 4 and 4 < y - ty < v.get_height() - 4 and \
                (v.hit(x - tx, y - ty)[0] or scenehost.Node({"id": ""})).id == nid:
            return x, y
    raise AssertionError(f"{nid} isn't to be clicked from here")
