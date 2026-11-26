"""The Space Station scene theme's rig (a prototype's): its world made from what a host says (Session.json's
sessions, a host, TelemetryUsage's days), and Baude's own widgets for each of its panels: the chat panel, the
sidebar's own rows (SessionSidebar.session_row, from sessionlist.remote_info of the same sessions), Meta's cards
(MetaCards), the host, and a real window (Usage: the context bar and the tokens' grid) borrowed through WindowPanel.
Imported by station_render.py and perf.py (SCENE=station)."""
from rig import *  # noqa: F401,F403
import datetime  # noqa: E402

import chatparts, scenegfx, sessionlist, sessionsidebar, sidebarmenu, telemetryview  # noqa: E402,F401

STATION = os.path.join(HERE, "space-station")
HOST = "h1"
SESSIONS = [  # (as the host's sessions answer has them: schema/types/Session.json)
    {"id": "a", "title": "Fix the flaky upload", "state": "working", "said": NOW - 60, "cwd": "/srv/upload"},
    {"id": "b", "title": "Tidy the release notes", "state": "waiting", "said": NOW - 600, "cwd": "/srv/docs"},
    {"id": "c", "title": "Garden planner", "state": "idle", "said": NOW - 3600, "cwd": "/srv/garden"},
    {"id": "d", "title": "Port the CI to arm64", "state": "idle", "said": NOW - 7200, "cwd": "/srv/ci"},
    {"id": "e", "title": "Benchmark the parser", "state": "stopped", "said": NOW - 20000, "cwd": "/srv/parse"},
    {"id": "f", "title": "Old notes", "state": "stopped", "said": NOW - 86400, "cwd": "/srv/notes"},
    {"id": "g", "title": "Draft the March newsletter", "state": "stopped", "said": NOW - 172800, "cwd": "/srv/news"},
]
CARDS = (0, 1, 4)  # (proto3d's make_card: Meta's tool card, Meta's change box, a question form)


class Server:
    """A host as the app's client has it (baudelib.client.Remote), as far as the sidebar's rows look: its name, that
    it's there, and where its transcripts' mirrors would be."""
    host, label, mine, state, deactivated, seen, info = "workshop.test", "workshop", False, "alive", False, NOW, {}

    def mirror(self, sid):
        return os.path.join(os.environ.get("HOME", "/tmp"), "mirror", f"{sid}.jsonl")

    def request(self, msg, then):
        then({})


class Rows:
    """The sidebar's own rows (SessionSidebar.session_row and bot_row), made outside a sidebar: what they read of
    their window (the chat it has open, groups put in, Meta's marks) stood in for."""
    session_row, bot_row = sessionsidebar.SessionSidebar.session_row, sessionsidebar.SessionSidebar.bot_row
    held_tag, held_tip = staticmethod(sessionsidebar.SessionSidebar.held_tag), staticmethod(sessionsidebar.SessionSidebar.held_tip)
    file_state = sidebarmenu.SidebarMenu.file_state

    def __init__(self):
        self.assigned, self.marks, self.server = {}, {}, Server()
        self.win = type("Win", (), {"session_id": None, "session_state": staticmethod(lambda: "inactive")})()

    def show_menu(self, *_):
        pass

    def row(self, obj):
        sid = obj["id"].split("/", 1)[1]
        s = next(s for s in SESSIONS if s["id"] == sid)
        info = sessionlist.remote_info({**s, "state": obj.get("state"), "server": self.server})
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        box.add_css_class("navigation-sidebar")
        box.append(self.session_row(info))
        return box


def usage_days():
    """TelemetryUsage's days, as a host adds them up: a year of turns, busier on weekdays."""
    today, out = datetime.date.today(), []
    for i in range(365):
        day = today - datetime.timedelta(days=i)
        n = (i * 7919) % 13
        if day.weekday() < 5 and n > 3 or n > 10:
            out.append({"day": day.isoformat(), "turns": n, "tokens": n * 41000 + (i % 5) * 9000})
    return out[::-1]


def usage_window():
    """Usage, a window of its own: the open chat's context (ContextUsage, as get_context_usage says it) and the host's
    tokens a day (telemetryview.TokenGrid, as TelemetryUsage says them)."""
    win = Gtk.Window(title="Usage", default_width=420, default_height=170)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=12, margin_start=12, margin_end=12)
    ctx = chatparts.ContextUsage()
    ctx.set_usage({"totalTokens": 48200, "maxTokens": 200000, "categories": [{"name": "Messages", "tokens": 31000}]})
    box.append(ctx)
    grid = telemetryview.TokenGrid()
    grid.set_margin_start(0)
    grid.set_margin_end(0)
    grid.show(usage_days())
    box.append(grid)
    win.set_child(box)
    return win


def world():
    return scenehost.World(scenehost.World.from_sessions(HOST, SESSIONS) + [
        {"kind": "host", "id": HOST, "name": "workshop", "state": "up"},
        {"kind": "window", "id": "usage", "name": "Usage", "used": NOW},
    ] + [{"kind": "meta_card", "id": f"m{i}", "name": f"card {i}", "state": "done", "used": NOW - i} for i in CARDS])


def station_transcripts():
    t = transcripts()
    return {"h1/a": t["h1/a"], "h1/b": t["h1/b"], "h1/c": t["h1/c"]}


def providers(wd, windows):
    rows, cards = Rows(), {f"m{i}": make_card(i)[1] for i in CARDS}
    base = scenehost.default_providers(wd, windows=windows, transcripts=station_transcripts())
    return {**base, "chat_row": rows.row, "meta_card": lambda o: cards[o["id"]]}


def station_scene():
    with open(os.path.join(STATION, "scene.json")) as f:
        return json.load(f)


def load_station_css():
    css = Gtk.CssProvider()
    with open(os.path.join(STATION, "theme.css")) as f:
        css.load_from_string(f.read())
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)


def station_view(opened=None, theme=STATION):
    wd = world()
    windows = {"usage": usage_window()}
    v = scenegfx.scene_view(station_scene(), wd, providers(wd, windows), theme=theme, trusted=False,
                            open=(opened.append if opened is not None else None))
    v.windows = windows
    return v, wd
