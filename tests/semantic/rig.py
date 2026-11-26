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
    bash = toolcards.ToolCard("Bash", "t1")
    bash.set_input({"command": "pytest tests/test_upload.py -q"})
    bash.set_result("3 passed in 0.41s", False)
    bash.set_state("done")
    agent = toolcards.ToolCard("Agent", "t2")
    agent.set_input({"description": "Find where the release notes are built", "prompt": "Look through the docs"})
    return {
        "h1/a": [("user", "The upload test fails one run in five. Can you find why?"),
                 ("reply", "It races the temp folder's cleanup. Running the test again with the fix:"), ("tool", bash)],
        "h1/b": [("user", "Tidy the release notes for 2.4"), ("tool", agent),
                 ("reply", "Shall I fold the three bug-fix lines into one?")],
        "h1/c": [("user", "What should I plant in March?"), ("reply", "Broad beans, onion sets and early potatoes.")],
    }


def load_theme_css():
    css = Gtk.CssProvider()
    with open(os.path.join(SAMPLE, "theme.css")) as f:
        css.load_from_string(f.read())
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)


def view(scene=None, wd=None, trusted=True, providers=None, opened=None):
    wd = wd or world()
    return scenehost.SceneView(scene or sample_scene(), wd, providers or scenehost.default_providers(wd, transcripts=transcripts()),
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
