"""A Bot's browser tools: an MCP server on stdin and stdout (as baudelib.meshtools is), run on the Bot's host. Each
call drives the Bot's own Emission profile on a host of the mesh (this one if none's named): through that host's
process (wire's emission op), which starts Emission there for the Bot when it isn't running. A Bot only ever reaches
its own profile; the sites it's signed in to are the ones its owner signed in to there, or copied in. Each is called
by the agent as mcp__emission__<tool>."""
import json
import os
import sys
import threading

from baudelib import meshtools, wire

NAME = "emission"
HOST_WAIT_S = 85  # (the host's wait on Emission's answer: a page's load is 30 s of it at most)
WAIT_S = 130  # (a request's answer waited for this long: the host's start of Emission and its wait, and then some)

TAB = {"type": "string", "description": "A tab's id (tabs); the one in front if none"}
HOST = {"type": "string", "description": "A host's id or name (mcp__mesh__hosts); this one if none. Each host has "
                                         "its own browser for you, with its own sign-ins"}
REF = {"type": "string", "description": "An element's ref, as read_page or find gave it (ref_12)"}

SCHEMAS = {
    "tabs": ("Your browser's tabs on a host (it's started if it isn't running), the sites it's signed in to, and its "
             "recent downloads. Each tab's id is what the other tools take.",
             {"host": HOST, "sites": {"type": "boolean", "description": "Also list the sites it's signed in to"}},
             []),
    "new_tab": ("Open a new tab, on an address or a search, and wait for it to load.",
                {"url": {"type": "string", "description": "An address, or words to search for"}, "host": HOST}, []),
    "navigate": ("Go to an address (or search), or back, forward, or reload, in a tab, and wait for it to load.",
                 {"url": {"type": "string", "description": "An address, words to search for, or back, forward, "
                                                           "reload"}, "tab": TAB, "host": HOST}, ["url"]),
    "close_tab": ("Close a tab.", {"tab": TAB, "host": HOST}, ["tab"]),
    "screenshot": ("A picture of a tab's page as it's shown, for what text can't show: layout, images, a canvas, "
                   "how something looks. To read a page, read_page, find and get_text are cheaper. Its pixels are "
                   "what click, scroll and zoom's x and y are in.",
                   {"tab": TAB, "host": HOST}, []),
    "zoom": ("A region of the last screenshot at full detail, for small text or fine detail: region [x0, y0, x1, "
             "y1] in that screenshot's pixels (click and scroll keep to the screenshot's).",
             {"region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
              "tab": TAB, "host": HOST}, ["region"]),
    "read_page": ("How to read a page: its elements on screen and a screen above and below, each one's role, name "
                  "and ref (links, buttons, fields, headings; filter all adds the rest, text too). Refs are what "
                  "click, type, form_input and scroll take, the same for an element each read.",
                  {"tab": TAB, "host": HOST, "filter": {"type": "string", "enum": ["interactive", "all"]},
                   "ref": {"type": "string", "description": "Only this element's part of the page"},
                   "max_chars": {"type": "integer"}}, []),
    "find": ("Elements anywhere on the page whose role, name, placeholder, id or link match words: their refs.",
             {"query": {"type": "string"}, "tab": TAB, "host": HOST}, ["query"]),
    "get_text": ("The page's text (its main part's, if it has one): to read what it says.",
                 {"tab": TAB, "host": HOST, "max_chars": {"type": "integer"}}, []),
    "click": ("Click an element (by its ref: scrolled to and clicked in its middle) or a point of the last "
              "screenshot, with the mouse, as a person would; then wait for any page it opens.",
              {"ref": REF, "x": {"type": "number"}, "y": {"type": "number"}, "tab": TAB, "host": HOST,
               "button": {"type": "string", "enum": ["left", "right"]},
               "count": {"type": "integer", "description": "2 for a double click"},
               "modifiers": {"type": "array", "items": {"type": "string", "enum": ["shift", "ctrl", "alt", "cmd"]}}},
              []),
    "drag": ("Drag with the mouse along a path of points of the last screenshot (pressed at the first, let go at the "
             "last): to select text, move a slider, drag something into place.",
             {"path": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
              "tab": TAB, "host": HOST}, ["path"]),
    "hover": ("Move the mouse over an element or a point (menus that open on hover).",
              {"ref": REF, "x": {"type": "number"}, "y": {"type": "number"}, "tab": TAB, "host": HOST}, []),
    "type": ("Type text into the focused field (or the field with ref: focused first), a key press a character at "
             "a time, as a keyboard types.",
             {"text": {"type": "string"}, "ref": REF, "tab": TAB, "host": HOST}, ["text"]),
    "key": ("Press keys, one after another (space between them): Enter, Tab, Escape, Backspace, Delete, Up, Down, "
            "Left, Right, Home, End, PageUp, PageDown, a character, or a combination like cmd+a or shift+Tab.",
            {"keys": {"type": "string"}, "tab": TAB, "host": HOST}, ["keys"]),
    "scroll": ("Turn the mouse wheel over an element, a point, or the page's middle: dy (and dx) pixels (dy 600 if "
               "none's given; up is negative). Says what came into view, with refs.",
               {"dy": {"type": "integer"}, "dx": {"type": "integer"}, "ref": REF, "x": {"type": "number"},
                "y": {"type": "number"}, "tab": TAB, "host": HOST}, []),
    "form_input": ("Set a form field by its ref: a select's option (value or text), a checkbox or radio (true or "
                   "false), or a field's value.",
                   {"ref": REF, "value": {}, "tab": TAB, "host": HOST}, ["ref", "value"]),
    "javascript": ("Run JavaScript in the page (an async function's body: return what you want back, as JSON).",
                   {"code": {"type": "string"}, "tab": TAB, "host": HOST}, ["code"]),
    "console": ("What the page has written to its console, and its errors, newest last.",
                {"tab": TAB, "host": HOST, "pattern": {"type": "string", "description": "Only messages matching "
                                                                                        "this regular expression"},
                 "limit": {"type": "integer"}, "clear": {"type": "boolean", "description": "Clear them after"}}, []),
    "network": ("What the page has loaded (itself and its resources): each URL, its kind, status, time and size.",
                {"tab": TAB, "host": HOST, "pattern": {"type": "string", "description": "Only URLs matching this "
                                                                                        "regular expression"},
                 "limit": {"type": "integer"}}, []),
    "resize": ("Make the page's size (its viewport) width by height pixels.",
               {"width": {"type": "integer"}, "height": {"type": "integer"}, "tab": TAB, "host": HOST},
               ["width", "height"]),
    "prompts": ("What the pages are waiting for you to answer: an alert, a confirm, a prompt, a site's own sign-in "
                "box, or files to upload (tools' answers also show them, as prompts).", {"host": HOST}, []),
    "answer_prompt": ("Answer what a page is waiting on (its id from prompts): accept (OK, or false for Cancel), "
                      "with text for a prompt, user and password for a sign-in, files (paths on that host) for an "
                      "upload.",
                      {"id": {"type": "string"}, "accept": {"type": "boolean"}, "text": {"type": "string"},
                       "user": {"type": "string"}, "password": {"type": "string"},
                       "files": {"type": "array", "items": {"type": "string"}}, "host": HOST}, []),
    "browser_batch": ("Several of these tools in one call, done in order, stopping at the first error: actions, "
                      "each {name, input} as that tool takes it (not javascript, nor browser_batch), each checked "
                      "as it is on its own. Each one's answer comes back, pictures too.",
                      {"actions": {"type": "array", "items": {
                          "type": "object", "properties": {"name": {"type": "string"}, "input": {"type": "object"}},
                          "required": ["name", "input"]}}}, ["actions"]),
}
OPS = {"tabs": "tabs", "new_tab": "new_tab", "navigate": "navigate", "close_tab": "close_tab",
       "screenshot": "screenshot", "zoom": "zoom", "read_page": "read_page", "find": "find", "get_text": "get_text",
       "click": "click", "hover": "hover", "drag": "drag", "type": "type", "key": "key", "scroll": "scroll",
       "form_input": "form_input", "javascript": "javascript", "console": "console", "network": "network",
       "resize": "resize", "prompts": "prompts", "answer_prompt": "answer_prompt"}
HOST_OPS = {*OPS.values(), "sites", "select_tab"}  # (what a host passes on to a Bot's Emission: its tools' requests, nothing else)


class Browser:
    """The Bot's browser on each host (me: its chat's id; its name asked of its host, for its profile's)."""

    def __init__(self, me, socket=None):
        self.me = me
        self.mesh = meshtools.Mesh(me, socket)
        self.name = None

    def bot_name(self):
        if self.name is None:
            for s in self.mesh.sessions(self.mesh.local):
                if s["id"] == self.me:
                    self.name = (s.get("bot") or {}).get("name") or s.get("title") or "Bot"
                    break
        return self.name or "Bot"

    def call(self, tool, args):
        if not self.me:
            raise ValueError("these tools are a Bot's (its chat's id wasn't given)")
        args = dict(args)
        where = self.mesh.where(args.pop("host", None))
        msg = {**args, "op": OPS[tool]}  # (its op the tool's, whatever the arguments say)
        got = self.mesh.request(where, {"op": "emission", "bot": {"id": self.me, "name": self.bot_name()},
                                        "call": msg}, timeout=WAIT_S)
        result = got.get("result") or {}
        if tool == "tabs" and args.get("sites"):
            sites = self.mesh.request(where, {"op": "emission", "bot": {"id": self.me, "name": self.bot_name()},
                                              "call": {"op": "sites"}}, timeout=WAIT_S).get("result") or {}
            result["sites"] = sites.get("sites", [])
        return result


def content(tool, result):
    """An MCP tool result's content: a screenshot's or zoom's picture as an image, the rest as text (a page's, and
    what a scroll brought into view, as text of their own)."""
    if tool in ("screenshot", "zoom") and result.get("image"):
        image = result.pop("image")
        return [{"type": "image", "data": image, "mimeType": result.pop("mime", "image/jpeg")},
                {"type": "text", "text": json.dumps(result, indent=1, ensure_ascii=False)}]
    text = result.pop("page", None) or result.pop("text", None) if tool in ("read_page", "get_text") else None
    if tool == "scroll":
        text = result.pop("in_view", None)
    out = [{"type": "text", "text": json.dumps(result, indent=1, ensure_ascii=False)}]
    if text is not None:
        out.append({"type": "text", "text": meshtools.cut([text]) if len(text) > 60000 else text})
    return out


BATCH_MAX = 25  # (a batch's actions at most)
NOT_BATCHED = {"browser_batch", "javascript"}  # (a batch is the tools only: no script in it, nor a batch)
SAID = ("tab", "title", "url", "loading", "selected")  # (a tab's description: in a batch, said only as it changes)


def batch(browser, actions):
    """browser_batch: each action ({name, input}) called as that tool would be, in order, until one fails; their
    answers in one content (each one's tab, title and address only where they've changed)."""
    if not isinstance(actions, list) or not actions:
        raise ValueError("browser_batch needs actions: a list of {name, input}")
    if len(actions) > BATCH_MAX:
        raise ValueError(f"a batch takes {BATCH_MAX} actions at most")
    steps = []
    for i, a in enumerate(actions, 1):
        name = a.get("name") if isinstance(a, dict) else None
        name = name.removeprefix(f"mcp__{NAME}__") if isinstance(name, str) else name
        if name in NOT_BATCHED:
            raise ValueError(f"action {i}: {name} isn't done in a batch: call it on its own")
        if name not in SCHEMAS:
            raise ValueError(f"action {i}: no tool {name!r}")
        args = a.get("input", {})
        if not isinstance(args, dict):
            raise ValueError(f"action {i}: its input is an object of {name}'s arguments")
        steps.append((name, args))
    out, last = [], {}
    for i, (name, args) in enumerate(steps, 1):
        try:
            result = browser.call(name, args)
        except Exception as e:
            left = len(steps) - i
            out.append({"type": "text", "text": f"{i}. {name} failed: {type(e).__name__}: {e}" + (
                f" (the {left} after it not done)" if left > 1 else " (the one after it not done)" if left else "")})
            break
        now = {k: result[k] for k in SAID if k in result}
        for k in SAID:
            if k in result and last.get(k) == result[k]:
                del result[k]
        last.update(now)
        got = content(name, result)
        first = next(c for c in got if c["type"] == "text")
        first["text"] = f"{i}. {name}" + (": " + first["text"] if result else "")
        out += got
    return out


def serve(me=None, rfile=None, wfile=None, socket=None):
    """The MCP server (as meshtools.serve: a message a line, each call on a thread of its own)."""
    rfile, wfile = rfile or sys.stdin, wfile or sys.stdout
    browser, lock = Browser(me, socket), threading.Lock()

    def send(msg):
        with lock:
            wfile.write(json.dumps(msg) + "\n")
            wfile.flush()

    def call(mid, name, args):
        try:
            if name not in SCHEMAS:
                raise ValueError(f"no tool {name}")
            result = {"content": batch(browser, args.get("actions")) if name == "browser_batch" else
                      content(name, browser.call(name, args))}
        except Exception as e:  # (said to the model, which may try again)
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    listed = [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props, "required": req}}
              for n, (d, props, req) in SCHEMAS.items()]
    for line in rfile:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            continue
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}}, "serverInfo": {"name": NAME, "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": listed}})
        elif method == "tools/call":
            p = msg.get("params") or {}
            threading.Thread(target=call, args=(mid, p.get("name"), p.get("arguments") or {}), daemon=True).start()
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})


if __name__ == "__main__":
    serve(os.environ.get("BAUDE_SESSION"))
