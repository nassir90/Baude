"""scenelib — what tests/screenshots.py's scenes are made with: the registry (SCENES, filled by @scene in
tests/scenes_chat.py and tests/scenes_panels.py, in the order screenshots.py imports them), a scene's size, and the
helpers more than one scene file uses.
"""
import argparse
import os
import time

import test_baude as T  # noqa: E402  (sets up the throwaway env: fake claude, temp config dirs)
from test_baude import GdkPixbuf  # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))


SIDEBAR = 250  # the sessions sidebar is on in every scene; scenes are this much wider for it
W, H = 1000 + SIDEBAR, 760
SCENES = []


def scene(name, description, size=(W, H)):
    def register(fn):
        SCENES.append((name, description, size, fn))
        return fn
    return register


def opts(cwd=None, model=None):
    return argparse.Namespace(cwd=cwd or T.TMP, model=model, resume=None)


def age(path, seconds=3600):
    """A past session nobody is writing (a fresh file would look active elsewhere)."""
    old = time.time() - seconds
    os.utime(path, (old, old))


def png_bytes(w, h, rgba):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pb.fill(rgba)
    return pb.save_to_bufferv("png", [], [])[1]


def ask_permission(win, name, inp, tool_id):
    """What manual mode shows: the tool card, asking whether to allow it."""
    win.render_assistant([{"type": "tool_use", "id": tool_id, "name": name, "input": inp}])
    win.on_msg(win.proc, {"type": "control_request", "request_id": f"perm-{tool_id}",
                          "request": {"subtype": "can_use_tool", "tool_name": name, "input": inp,
                                      "tool_use_id": tool_id}})


def tool_with_output(win, name, inp, tool_id, content, is_error=False, open_input=True):
    win.render_assistant([{"type": "tool_use", "id": tool_id, "name": name, "input": inp}])
    win.render_tool_results([{"type": "tool_result", "tool_use_id": tool_id, "content": content,
                              "is_error": is_error}])
    card = win.tool_cards[tool_id]
    card.input_toggle.set_expanded(open_input)
    card.output_toggle.set_expanded(True)
    return card


def sidebar_sessions(proj):
    """Sessions in this folder, another project, and Claude Desktop's scratch chats."""
    other = os.path.join(T.TMP, "acme-app")
    scratch = os.path.join(T.TMP, ".config", "Claude", "scratch-workspaces", "a", "b", "scratch-2026-10-14-0aa1ab")
    for d in (proj, other, scratch):
        os.makedirs(d, exist_ok=True)
    home = os.path.expanduser("~")  # (only the recorded folder: nothing is written there)
    for i, (folder, title) in enumerate([(home, "Restart the touchscreen"), (other, "App creation test"),
                                         (scratch, "Install uv and node"),
                                         (other, "Acme code questions"), (proj, "Refactor settings"),
                                         (scratch, "Recipe box web app"), (proj, "Login bug")]):
        path = T.write_session(folder, f"{i}{i}{i}{i}{i}{i}{i}{i}-0000-4000-8000-00000000000{i}", title=title)
        when = time.time() - 3600 + i * 60  # written a while ago: idle, so no dot (and still in this order)
        os.utime(path, (when, when))


def sidebar_row(win, title):
    for i in range(200):
        row = win.sidebar.list.get_row_at_index(i)
        if row is None:
            return None
        if getattr(row, "info", {}).get("title") == title:
            return row
