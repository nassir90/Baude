"""The Inbox's scenes (schema/ui/Inbox.page.json, InboxAsk.page.json): lab-mac's Bots' tasks' asks of a person, put on
its host's Inbox as a task's script raises them (baudelib/inbox.py: the pictures kept, every head told), drawn in the
row over Search sessions, the Inbox's tab, and an ask's tab: opened, Route to… open, a model's answer to confirm,
History, and one whose task went away; and a chat's own ask, raised by its model with its chat's tool (the fake claude's
CHAT TOOL ask_user), opened, stopped, and answered back into that chat. Fixture pictures only (tests/inbox_fixture.py)."""
import base64
import json
import os
import shutil
import time
import uuid

import test_baude as T  # noqa: E402
from scenelib import SIDEBAR, scene  # noqa: E402
import scenes_panels as P  # noqa: E402
import inbox_fixture  # noqa: E402
import inboxview  # noqa: E402

SIZE = (1210 + SIDEBAR, 860)
_FIX = []


def fix():
    if not _FIX:
        _FIX.append(inbox_fixture.make(os.path.join(T.TMP, "inbox-fixture")))
    return _FIX[0]


ASKS = {
    "limit": dict(
        bot="Juniper", task="Budget guard", title="Work is at 100%: bill extra usage?", ago=3 * 3600,
        buttons=[("Continue on overage", "orange"), ("Switch these chats to Home", "primary"),
                 ("Switch these chats to Team", "blue"), ("Pause until Work resets on Friday", "slate"),
                 ("Keep the tokenizer chat on overage, pause the rest", "purple"), ("Stop all three chats", "red")],
        form=[{"name": "cap", "type": "number", "label": "Spend cap (£)", "default": 20},
              {"name": "notify", "type": "bool", "label": "Notify me", "default": True}],
        pictures=["meter-work.png", "meter-home.png", "meter-team.png"],
        body="""### Work is at 100% and would bill extra usage

Work's weekly limit ran out at 09:12. Three chats are running on it; they stop at their next message unless you choose.

| Subscription | Used this week | | Resets |
|---|---|---|---|
| Work | **100%** | ![Work, 100%](meter-work.png) | Fri 06:00 (in 21 h) |
| Home | 41% | ![Home, 41%](meter-home.png) | Mon 06:00 |
| Team | 17% | ![Team, 17%](meter-team.png) | Mon 06:00 |

| Chat | Bot | Model |
|---|---|---|
| Speed up the tokenizer's hot loop | Juniper | Opus 5.5 |
| Translate docs | Fern | Sonnet 5.5 |
| Update dependencies | Fern | Sonnet 5.5 |
"""),
    "logo": dict(
        bot="Fern", task="Kestrel logo", title="Kestrel logo: which direction?", ago=60,
        buttons=[("A · Badge with the bird", None, "logo-a.png"), ("B · Bean and wordmark", None, "logo-b.png"),
                 ("C · App-style monogram", None, "logo-c.png"), ("None, try again", None)],
        pictures=["logo-a.png", "logo-b.png", "logo-c.png"],
        body="""### Which logo?

Three directions for Kestrel Coffee's new sign and cups. Pick one and I'll make the full set (cup, sign, favicon).
"""),
    "review": dict(
        bot="Fern", task="Review pull requests", title="Review #128: Add an orders chart", ago=1200,
        versions=["### #128 Add an orders chart to the dashboard\n\nReplaces the Recent orders list on Overview with "
                  "a bar chart of orders per day.\n\n![overview, after](dash-before.png)\n"],
        buttons=[("Merge", "green"), ("Ask Bot to fix", None), ("Close", "red")],
        form=[{"name": "note", "type": "text", "label": "Note for the Bot"}],
        pictures=["dash-after.png", "dash-before.png"],
        body="""### #128 Add an orders chart to the dashboard

Replaces the Recent orders list on Overview with a bar chart of orders per day, and moves the list to Orders. **v2** fixes the empty-week case from v1. +214 −61 in 7 files.

![overview, after](dash-after.png)

One test still fails:

| Test | Result |
|---|---|
| chart renders 7 days | ✓ passed |
| chart with no orders | ✓ passed |
| **chart in a time zone east of UTC** | **✗ failed**: Sunday counted twice |
| overview loads under 300 ms | ✓ passed (212 ms) |
"""),
    "deploy": dict(
        bot="Juniper", task="Deploy site", title="Deploy site: deploy 2.5 to production?", ago=4 * 3600,
        buttons=[("Approve", "primary"), ("Deny", "red")], pictures=["tea-after.png"],
        body="""### Deploy site to prod?

Staging has run **2.5** since 08:10 with no errors. This is its home page:

![home, staging](tea-after.png)

Approve to put it on production; deny to keep 2.4 there. Changed routes:
- `/` new hero and product cards
- `/subscriptions` new page
- `/about` moved to `/journal/about`
- `/cart` free delivery over £30
"""),
    "digest": dict(
        bot="Juniper", task="Morning digest", title="Morning digest, Thursday", ago=3.5 * 3600,
        buttons=[("Rerun failed", "primary"), ("Snooze to 13:00", "yellow"), ("Dismiss", None)],
        pictures=["week.png", "use-work.png", "use-home.png", "use-team.png"],
        body="""### Morning digest, Thursday

Work ran out this morning; Home and Team have plenty left.

![used this week](week.png)

| Subscription | Used this week | Resets | Last 7 days |
|---|---|---|---|
| Work | **100%** | Fri 06:00 | ![Work, last 7 days](use-work.png) |
| Home | 41% | Mon 06:00 | ![Home, last 7 days](use-home.png) |
| Team | 17% | Mon 06:00 | ![Team, last 7 days](use-team.png) |
"""),
}
ORDER = ("logo", "review", "limit", "digest", "deploy")


def put(win, keys=ORDER, stopped=(), proposal=None):
    """lab-mac's Inbox given these asks, as their tasks raise them (and stopped: those whose run went), each head
    told; taken off again as the window goes. Waits till the window's host has them all."""
    server = P.NET["lab"][0]
    ib, folder, made = server.inbox, fix(), []
    for key in keys:
        a = ASKS[key]
        pics = [{"name": n, "data": base64.b64encode(open(os.path.join(folder, n), "rb").read()).decode()}
                for n in a["pictures"]]
        buttons = [{"label": b[0], **({"style": b[1]} if b[1] else {}), **({"picture": b[2]} if len(b) > 2 else {})}
                   for b in a["buttons"]]
        versions = []
        for i, body in enumerate([*a.get("versions", ()), a["body"]]):
            v = ib.version({"title": a["title"], "body": body, "pictures": pics, "buttons": buttons,
                            "form": a.get("form") or []})
            v["at"] = time.time() - a["ago"] + i * 600 - 600 * (len(a.get("versions", ())))
            versions.append(v)
        sid = P.NET["ids"][next(p for p, n in P.BOTS.items() if n == a["bot"])]
        ask = {"id": f"scene{key}", "session": sid, "bot": a["bot"], "task": f"task{key}", "task_name": a["task"],
               "run": f"run{key}", "at": versions[0]["at"], "status": "stopped" if key in stopped else "waiting",
               "versions": versions, "proposal": (proposal or {}).get(key), "sent_to": None, "sent_to_name": None,
               "answer": None}
        with ib.lock:
            ib.asks[ask["id"]] = ask
        made.append(ask)
        ib.tell(ask)

    def gone(*_):
        for ask in made:
            with ib.lock:
                ib.asks.pop(ask["id"], None)
            ib.tell(ask, removed=True)
    win.connect("unrealize", gone)
    r = P.NET["lab"][1]
    T.wait_for(lambda: all(f"scene{k}" in r.inbox for k in keys), "the asks heard", 20)
    T.pump(0.5)


def base(app, keys=ORDER, **kw):
    win = P.network_window(app, None)
    P.lab_bots(win)
    jun = P.NET["ids"]["Write the release notes for 2.4"]
    win.sidebar.on_activate(win.sidebar.list, P.session_row(win, jun))
    T.wait_for(lambda: win.session_id == jun, "Juniper open")
    put(win, keys, **kw)
    return win


def pictures(win):
    T.pump(1.5)


def tall(win, page, col):
    win.tall = (page, col)


@scene("inbox-row", "The Inbox row over Search sessions: how many asks wait, and the waiting dot", size=SIZE)
def _(app):
    win = base(app)
    T.pump(0.5)
    return win


@scene("inbox-list", "The Inbox: each ask a card cut a few lines down with Show more, its form and buttons shown; "
                     "then Pending requests and Running", size=SIZE)
def _(app):
    win = base(app)
    page = win.open_inbox()
    pictures(win)
    tall(win, page, page.col)
    return win


@scene("inbox-stopped", "The Inbox with an ask whose task went away: struck through, Not answered — the task stopped, "
                        "its buttons off", size=SIZE)
def _(app):
    win = base(app, stopped=("deploy",))
    page = win.open_inbox()
    pictures(win)
    tall(win, page, page.col)
    return win


def opened(app, key, whole=True, **kw):
    win = base(app, **kw)
    win.open_inbox()
    page = win.open_ask(P.NET["lab"][1].key, f"scene{key}")
    pictures(win)
    if whole:
        tall(win, page.scroll, page.view)
    return win, page


def at_foot(page):
    """Its page scrolled to its foot: its buttons in sight."""
    adj = page.scroll.get_vadjustment()
    adj.set_value(adj.get_upper() - adj.get_page_size())
    T.pump(0.5)


@scene("inbox-ask", "An ask opened in its own tab: a deploy to approve, its picture as wide as the text", size=SIZE)
def _(app):
    return opened(app, "deploy")[0]


@scene("inbox-ask-logo", "An ask with picture buttons", size=SIZE)
def _(app):
    return opened(app, "logo")[0]


@scene("inbox-ask-limit", "An ask with a form and six coloured buttons wrapping as whole buttons", size=SIZE)
def _(app):
    return opened(app, "limit")[0]


@scene("inbox-ask-history", "An ask's ⋯ → History: its versions and when each came", size=SIZE)
def _(app):
    win, page = opened(app, "review", whole=False)
    page.view.more.popup()
    T.pump(0.8)
    win.keep = page.view.more.get_popover()
    return win


@scene("inbox-ask-route", "Route to…: have a model answer, or send it to a Bot", size=SIZE)
def _(app):
    win, page = opened(app, "deploy", whole=False)
    at_foot(page)
    page.view.route.popup()
    T.pump(0.8)
    win.keep = page.view.route.get_popover()
    return win


@scene("inbox-ask-proposal", "A model's answer for the person to confirm: its button, its reason, the form filled",
       size=SIZE)
def _(app):
    win, _page = opened(app, "limit", proposal={"limit": {
        "model": "opus", "name": "Opus 5.5", "status": "ready", "button": 3, "values": {"cap": 0, "notify": True},
        "text": "Work resets on Friday and nothing running is urgent; Home and Team are kept for the week."}})
    return win


@scene("inbox-ask-stopped", "An ask whose task went away, opened: struck through, greyed, its buttons off", size=SIZE)
def _(app):
    return opened(app, "deploy", stopped=("deploy",))[0]


def viewed(app, key, step=0):
    """The ask's first picture button's picture clicked open in the large view, then stepped on (→) step times."""
    win, page = opened(app, key, whole=False)
    at_foot(page)
    pic = next(w for w in T.walk(page.view) if getattr(w, "image_actions", None) is not None
               and isinstance(w, inboxview.PicturePart))
    pic.image_actions.view()
    T.pump(0.5)
    for _ in range(step):
        win.lightbox.step(1)
        T.pump(0.5)
    return win


@scene("inbox-ask-logo-viewer", "A picture button's picture clicked: the large view chat pictures open in", size=SIZE)
def _(app):
    return viewed(app, "logo")


@scene("inbox-ask-logo-viewer-next", "The large view stepped on to the ask's next picture", size=SIZE)
def _(app):
    return viewed(app, "logo", 1)


CHAT = "Speed up the tokenizer's hot loop"  # (lab-mac's chat, not a Bot, whose model asks with its own tools)
CHAT_ASK = {
    "title": "Tokenizer: keep the merge rewrite?",
    "body": """### The hot loop is 2.4× faster

I rewrote byte-pair merging to work on one flat array instead of a list of strings, and split on whitespace with a
lookup table. Every test passes; this is 1 GB of the docs corpus, before and after:

![seconds per stage](timings.png)

| Stage | Before | After |
|---|---|---|
| split on whitespace | 3.6 s | **1.1 s** |
| merge byte pairs | 6.8 s | **2.4 s** |
| map to ids | 1.7 s | **0.6 s** |
""",
    "buttons": [{"label": "Keep it and open a pull request", "style": "green"},
                {"label": "Try a SIMD split too", "style": "blue"}, {"label": "Revert", "style": "red"}],
    "form": [{"name": "branch", "type": "text", "label": "Branch", "default": "tokenizer-flat-merge"},
             {"name": "bench", "type": "bool", "label": "Run the full benchmark first", "default": True}],
    "pictures": [{"name": "timings.png", "path": "bench/timings.png"}]}


def chat_ask(win):
    """The tokenizer chat's model asks the person with its chat's tool (the fake claude's CHAT TOOL ask_user: its
    pictures files it made in its folder); the ask taken off again as the window goes. Its id."""
    server, r = P.NET["lab"]
    sid, folder = P.NET["ids"][CHAT], P.NET["folders"][0]
    os.makedirs(os.path.join(folder, "bench"), exist_ok=True)
    shutil.copy(os.path.join(fix(), "timings.png"), os.path.join(folder, "bench", "timings.png"))
    have = set(r.inbox)
    with server.lock:
        server.take_input(None, server.sessions[sid], {"type": "user", "uuid": str(uuid.uuid4()), "message": {
            "role": "user", "content": "CHAT TOOL ask_user " + json.dumps(CHAT_ASK)}})
    new = lambda: next((a for a in r.inbox if a not in have and not r.inbox[a].get("run")), None)  # noqa: E731
    T.wait_for(new, "the chat's ask heard", 30)
    aid = new()
    T.wait_for(lambda: server.sessions[sid].state() == "idle", "its turn over", 30)

    def gone(*_):
        ib = server.inbox
        with ib.lock:
            ask = ib.asks.pop(aid, None)
        if ask is not None:
            ib.tell(ask, removed=True)
    win.connect("unrealize", gone)
    T.pump(0.5)
    return aid


@scene("inbox-chat-list", "The Inbox with an ask a chat's model raised with its own tool, no task: from the chat",
       size=SIZE)
def _(app):
    win = base(app)
    chat_ask(win)
    page = win.open_inbox()
    pictures(win)
    tall(win, page, page.col)
    return win


def chat_opened(app, stop=False):
    win = base(app, keys=())
    aid = chat_ask(win)
    if stop:
        P.NET["lab"][0].inbox.chat_gone(P.NET["ids"][CHAT])
        T.wait_for(lambda: P.NET["lab"][1].inbox[aid]["status"] == "stopped", "the ask stopped")
    win.open_inbox()
    page = win.open_ask(P.NET["lab"][1].key, aid)
    pictures(win)
    tall(win, page.scroll, page.view)
    return win, aid


@scene("inbox-chat-ask", "A chat's own ask opened: from the chat, its pictures, form and buttons; the box writes to "
                         "that chat", size=SIZE)
def _(app):
    return chat_opened(app)[0]


@scene("inbox-chat-stopped", "A chat's own ask whose chat was archived: struck through, its buttons off", size=SIZE)
def _(app):
    return chat_opened(app, stop=True)[0]


@scene("inbox-chat-answered", "A chat's own ask answered: the button and the form's values come into that chat as a "
                              "message from the Inbox, its next turn", size=SIZE)
def _(app):
    win = base(app, keys=())
    aid = chat_ask(win)
    server, r = P.NET["lab"]
    sid = P.NET["ids"][CHAT]
    r.request({"op": "inbox_press", "ask": aid, "button": 0, "values": {"branch": "tokenizer-flat-merge",
                                                                        "bench": True}})
    T.wait_for(lambda: server.inbox.asks.get(aid, {}).get("status") == "answered" and
               not server.inbox.asks[aid].get("_tell"), "the answer sent the chat", 30)
    T.wait_for(lambda: server.sessions[sid].state() == "idle", "its turn over", 30)
    win.sidebar.on_activate(win.sidebar.list, P.session_row(win, sid))
    T.wait_for(lambda: win.session_id == sid, "the chat open")
    T.pump(2)
    return win
