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

S, N, B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}
TAB = S  # (tabId: a tab's id, from tabs_context; the one in front if none)
HOST = {"type": "string", "description": "A mesh host (mcp__mesh__hosts); none: this one"}
REF = {"type": "string", "description": "ref_12: from read_page, find or scroll"}
SHOT = "x, y: the last screenshot's pixels"  # (said where a tool takes them)

SCHEMAS = {
    "tabs_context": ("Your browser's tabs (started if need be) and recent downloads; sites: the sites it's signed in "
                     "to. A tab's id is the tabId the tools take (none: the front tab). Each host has its own "
                     "browser and sign-ins.", {"host": HOST, "sites": B}, []),
    "tabs_create": ("Open a tab (on url: an address or a search) and wait for it to load.",
                    {"url": S, "host": HOST}, []),
    "navigate": ("Go to url (an address, a search, or back, forward, reload) and wait for it to load.",
                 {"url": S, "tabId": TAB, "host": HOST}, ["url"]),
    "tabs_close": ("Close a tab.", {"tabId": TAB, "host": HOST}, ["tabId"]),
    "screenshot": ("A picture of the page, for what text can't show: layout, images, a canvas, how it looks. "
                   "read_page, find and get_page_text read pages for less. Its pixels are what x, y are in.",
                   {"tabId": TAB, "host": HOST}, []),
    "zoom": ("The page as it is now, at full detail, in region [x0, y0, x1, y1] of the last screenshot's pixels "
             "(which x, y stay in).",
             {"region": {"type": "array", "items": N, "minItems": 4, "maxItems": 4}, "tabId": TAB, "host": HOST},
             ["region"]),
    "read_page": ("How to read a page: its elements on screen and a screen above and below (links, buttons, "
                  "fields, headings; filter all: the rest, text too), each with its role, name and ref, the same each "
                  "read. "
                  "ref_id: only that element's part.",
                  {"tabId": TAB, "host": HOST, "filter": {"type": "string", "enum": ["interactive", "all"]},
                   "ref_id": S, "max_chars": N}, []),
    "find": ("Elements anywhere on the page matching words (role, name, placeholder, id, link), with refs.",
             {"query": S, "tabId": TAB, "host": HOST}, ["query"]),
    "get_page_text": ("The page's text (its main part's, if it has one).", {"tabId": TAB, "host": HOST,
                                                                            "max_chars": N}, []),
    "click": (f"Click ref_id's element (its middle) or x, y, as a person would ({SHOT}); count 2: a double click.",
              {"ref_id": REF, "x": N, "y": N, "tabId": TAB, "host": HOST,
               "button": {"type": "string", "enum": ["left", "right"]}, "count": N,
               "modifiers": {"type": "array", "items": {"type": "string", "enum": ["shift", "ctrl", "alt", "cmd"]}}},
              []),
    "drag": (f"Drag along path [[x, y], ...] ({SHOT}): select text, move a slider, drag into place.",
             {"path": {"type": "array", "items": {"type": "array", "items": N}}, "tabId": TAB, "host": HOST},
             ["path"]),
    "hover": (f"Move the mouse over ref_id's element or x, y ({SHOT}).",
              {"ref_id": REF, "x": N, "y": N, "tabId": TAB, "host": HOST}, []),
    "type": ("Type text, key by key, into the focused field (or ref_id's, focused first).",
             {"text": S, "ref_id": REF, "tabId": TAB, "host": HOST}, ["text"]),
    "key": ("Press keys in turn, a space between: Enter, Tab, Escape, Backspace, Delete, Up, Down, Left, Right, Home, "
            "End, PageUp, PageDown, a character, or a combination like cmd+a.",
            {"keys": S, "tabId": TAB, "host": HOST}, ["keys"]),
    "scroll": (f"Turn the mouse wheel over ref_id's element, x, y ({SHOT}) or the middle, by dy, dx CSS pixels (dy "
               "600 if none; up is negative). Says what came into view, with refs.",
               {"dy": N, "dx": N, "ref_id": REF, "x": N, "y": N, "tabId": TAB, "host": HOST}, []),
    "form_input": ("Set ref_id's field: a select's option (value or text), a checkbox or radio (true, false), or a "
                   "value.", {"ref_id": REF, "value": {}, "tabId": TAB, "host": HOST}, ["ref_id", "value"]),
    "javascript_tool": ("Run an async function's body in the page and return its JSON value. Its coordinates are CSS "
                        "pixels, not the screenshot's.", {"code": S, "tabId": TAB, "host": HOST}, ["code"]),
    "read_console_messages": ("The page's console messages and errors, newest last (pattern: a regex they match).",
                              {"tabId": TAB, "host": HOST, "pattern": S, "limit": N, "clear": B}, []),
    "read_network_requests": ("What the page loaded: each URL, its kind, status, time and size (pattern: a regex "
                              "they match).", {"tabId": TAB, "host": HOST, "pattern": S, "limit": N}, []),
    "resize_window": ("Make the page's viewport width by height CSS pixels.",
                      {"width": N, "height": N, "tabId": TAB, "host": HOST}, ["width", "height"]),
    "prompts": ("What pages wait on you to answer: an alert, confirm or prompt, a site's sign-in box, files to "
                "upload.", {"host": HOST}, []),
    "answer_prompt": ("Answer prompt id: accept (false: Cancel), text for a prompt, user and password for a sign-in, "
                      "files (paths on that host) for an upload.",
                      {"id": S, "accept": B, "text": S, "user": S, "password": S,
                       "files": {"type": "array", "items": S}, "host": HOST}, []),
    "browser_batch": ("These tools in one call, in order, stopping at the first error: actions [{name, input}], input "
                      "as the tool takes it (not javascript_tool or browser_batch). Each one's answer comes back.",
                      {"actions": {"type": "array", "maxItems": 25, "items": {
                          "type": "object", "properties": {"name": S, "input": {"type": "object"}},
                          "required": ["name", "input"]}}}, ["actions"]),
}
OPS = {"tabs_context": "tabs", "tabs_create": "new_tab", "navigate": "navigate", "tabs_close": "close_tab",
       "screenshot": "screenshot", "zoom": "zoom", "read_page": "read_page", "find": "find",
       "get_page_text": "get_text", "click": "click", "hover": "hover", "drag": "drag", "type": "type", "key": "key",
       "scroll": "scroll", "form_input": "form_input", "javascript_tool": "javascript",
       "read_console_messages": "console", "read_network_requests": "network", "resize_window": "resize",
       "prompts": "prompts", "answer_prompt": "answer_prompt"}  # (each tool's op: the ops keep their own names)
ARGS = {"tabId": "tab", "ref_id": "ref"}  # (the tools' arguments, named as Claude in Chrome's: the ops' names for them)
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
        got = self.mesh.request(where, {"op": "emission", "bot": {"id": self.me, "name": self.bot_name()},
                                        "call": request(tool, args)}, timeout=WAIT_S)
        result = answered(got.get("result") or {})
        if tool == "tabs_context" and args.get("sites"):
            sites = self.mesh.request(where, {"op": "emission", "bot": {"id": self.me, "name": self.bot_name()},
                                              "call": {"op": "sites"}}, timeout=WAIT_S).get("result") or {}
            result["sites"] = sites.get("sites", [])
        return result


def request(tool, args):
    """A tool's call as its op's request: its arguments named as the ops name them, its op the tool's (whatever the
    arguments say)."""
    return {**{ARGS.get(k, k): v for k, v in args.items()}, "op": OPS[tool]}


def answered(value):
    """An op's answer in the tools' names: a tab's id as tabId."""
    if isinstance(value, dict):
        return {("tabId" if k == "tab" else k): answered(v) for k, v in value.items()}
    if isinstance(value, list):
        return [answered(v) for v in value]
    return value


def content(tool, result):
    """An MCP tool result's content: a screenshot's or zoom's picture as an image, the rest as text (a page's, and
    what a scroll brought into view, as text of their own)."""
    if tool in ("screenshot", "zoom") and result.get("image"):
        image = result.pop("image")
        return [{"type": "image", "data": image, "mimeType": result.pop("mime", "image/jpeg")},
                {"type": "text", "text": json.dumps(result, indent=1, ensure_ascii=False)}]
    text = result.pop("page", None) or result.pop("text", None) if tool in ("read_page", "get_page_text") else None
    if tool == "scroll":
        text = result.pop("in_view", None)
    out = [{"type": "text", "text": json.dumps(result, indent=1, ensure_ascii=False)}]
    if text is not None:
        out.append({"type": "text", "text": meshtools.cut([text]) if len(text) > 60000 else text})
    return out


BATCH_MAX = 25  # (a batch's actions at most)
BATCH_CHARS = 60000  # (a batch's whole answer at most, its pictures counted as their tokens' worth: well under what
# an agent takes from a tool)
NOT_BATCHED = {"browser_batch", "javascript_tool"}  # (a batch is the tools only: no script in it, nor a batch)
SAID = ("tabId", "title", "url", "loading", "selected")  # (a tab's description: in a batch, said only as it changes)


class Cancelled(Exception):
    """The call was cancelled (notifications/cancelled): no answer's sent."""


def batch(browser, actions, cancelled=None):
    """browser_batch: each action ({name, input}) called as that tool would be, in order, until one fails (or the
    call's cancelled: between actions); (their answers in one content, whether one failed). Each one's tab, title and
    address are said only where they've changed; past BATCH_CHARS, the rest's text is cut and its pictures left out."""
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
            raise ValueError(f"action {i}: no tool {name!r} (the tools: "
                             f"{', '.join(n for n in SCHEMAS if n not in NOT_BATCHED)})")
        args = a.get("input", {})
        if not isinstance(args, dict):
            raise ValueError(f"action {i}: its input is an object of {name}'s arguments")
        steps.append((name, args))
    out, last, room, trimmed, failed = [], {}, BATCH_CHARS, False, None
    done = 0
    for i, (name, args) in enumerate(steps, 1):
        if cancelled is not None and cancelled.is_set():
            raise Cancelled()
        try:
            result = browser.call(name, args)
        except Exception as e:
            failed = f"{type(e).__name__}: {e}"
            out.append({"type": "text", "text": f"{i}. {name} failed: {failed}"})
            break
        done = i
        now = {k: result[k] for k in SAID if k in result}
        for k in SAID:
            if k in result and last.get(k) == result[k]:
                del result[k]
        last.update(now)
        size = (result.get("width") or 0) * (result.get("height") or 0) // 750 * 4  # (a picture: its tokens' worth)
        got = content(name, result)
        texts = [c for c in got if c["type"] == "text"]
        texts[0]["text"] = f"{i}. {name}" + (": " + texts[0]["text"] if result else "")
        for j, c in enumerate(texts):
            if len(c["text"]) > room:
                keep = c["text"][:room] if room > 0 else f"{i}. {name}" if j == 0 else ""
                c["text"] = keep + "\n(… trimmed: the batch's answer is at its limit)"
                trimmed = True
            room -= len(c["text"])
        pictures = [c for c in got if c["type"] == "image"]
        if pictures and size > room:
            texts.append({"type": "text", "text": "(its picture left out: the batch's answer is at its limit)"})
            pictures, trimmed = [], True
        room -= size if pictures else 0
        out += texts[:1] + pictures + texts[1:]  # (each picture after the line naming it)
    names = [n for n, _ in steps]
    summary = f"Done {done} of {len(steps)}" + (f"; {names[done]} failed" if failed else "") + (
        f"; not done: {', '.join(names[done + 1:])}" if failed and done + 1 < len(names) else "") + (
        "; later answers trimmed" if trimmed else "") + "."
    out.append({"type": "text", "text": summary})
    return out, failed is not None


def serve(me=None, rfile=None, wfile=None, socket=None):
    """The MCP server (as meshtools.serve: a message a line, each call on a thread of its own)."""
    rfile, wfile = rfile or sys.stdin, wfile or sys.stdout
    browser, lock = Browser(me, socket), threading.Lock()

    def send(msg):
        with lock:
            wfile.write(json.dumps(msg) + "\n")
            wfile.flush()

    cancels = {}  # (each call's being done: its id's event, set if it's cancelled)

    def call(mid, name, args):
        cancelled = cancels.setdefault(mid, threading.Event())
        try:
            if name not in SCHEMAS:
                raise ValueError(f"no tool {name}")
            if name == "browser_batch":
                got, failed = batch(browser, args.get("actions"), cancelled)
                result = {"content": got, **({"isError": True} if failed else {})}
            else:
                if cancelled.is_set():
                    raise Cancelled()
                result = {"content": content(name, browser.call(name, args))}
        except Cancelled:
            result = None
        except Exception as e:  # (said to the model, which may try again)
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        finally:
            cancels.pop(mid, None)
        if result is not None and not cancelled.is_set():  # (a cancelled call's answered with nothing, as MCP has it)
            send({"jsonrpc": "2.0", "id": mid, "result": result})
    listed = [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props,
                                                            **({"required": req} if req else {})}}
              for n, (d, props, req) in SCHEMAS.items()]
    for line in rfile:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if method == "notifications/cancelled":  # (a call given up on: a batch stops before its next action, and
            gone = (msg.get("params") or {}).get("requestId")  # nothing's said for it)
            if gone in cancels:
                cancels[gone].set()
            continue
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
            cancels.setdefault(mid, threading.Event())
            threading.Thread(target=call, args=(mid, p.get("name"), p.get("arguments") or {}), daemon=True).start()
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})


if __name__ == "__main__":
    serve(os.environ.get("BAUDE_SESSION"))
