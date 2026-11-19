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
WAIT_S = 90  # (a request's answer waited for this long: a page's load is 30 s of it at most)

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
    "screenshot": ("A picture of a tab's page as it's shown. Its pixels are what click and scroll's x and y are in.",
                   {"tab": TAB, "host": HOST}, []),
    "read_page": ("The page's elements as a tree: each one's role, name and ref (links, buttons, fields, headings; "
                  "filter all adds the rest, text too). Refs are what click, type, form_input and scroll take.",
                  {"tab": TAB, "host": HOST, "filter": {"type": "string", "enum": ["interactive", "all"]},
                   "ref": {"type": "string", "description": "Only this element's part of the page"},
                   "max_chars": {"type": "integer"}}, []),
    "find": ("Elements whose role, name, placeholder, id or link match words: their refs.",
             {"query": {"type": "string"}, "tab": TAB, "host": HOST}, ["query"]),
    "get_text": ("The page's text (its main part's, if it has one).",
                 {"tab": TAB, "host": HOST, "max_chars": {"type": "integer"}}, []),
    "click": ("Click an element (by its ref: scrolled to and clicked in its middle) or a point of the last "
              "screenshot, with the mouse, as a person would; then wait for any page it opens.",
              {"ref": REF, "x": {"type": "number"}, "y": {"type": "number"}, "tab": TAB, "host": HOST,
               "button": {"type": "string", "enum": ["left", "right"]},
               "count": {"type": "integer", "description": "2 for a double click"},
               "modifiers": {"type": "array", "items": {"type": "string", "enum": ["shift", "ctrl", "alt", "cmd"]}}},
              []),
    "hover": ("Move the mouse over an element or a point (menus that open on hover).",
              {"ref": REF, "x": {"type": "number"}, "y": {"type": "number"}, "tab": TAB, "host": HOST}, []),
    "type": ("Type text into the focused field (or the field with ref: focused first), a character at a time.",
             {"text": {"type": "string"}, "ref": REF, "tab": TAB, "host": HOST}, ["text"]),
    "key": ("Press keys, one after another (space between them): Enter, Tab, Escape, Backspace, Delete, Up, Down, "
            "Left, Right, Home, End, PageUp, PageDown, a character, or a combination like cmd+a or shift+Tab.",
            {"keys": {"type": "string"}, "tab": TAB, "host": HOST}, ["keys"]),
    "scroll": ("Scroll the page, or what's scrollable under an element or a point, by dy (and dx) pixels "
               "(dy 600 if none's given; up is negative).",
               {"dy": {"type": "integer"}, "dx": {"type": "integer"}, "ref": REF, "x": {"type": "number"},
                "y": {"type": "number"}, "tab": TAB, "host": HOST}, []),
    "form_input": ("Set a form field by its ref: a select's option (value or text), a checkbox or radio (true or "
                   "false), or a field's value.",
                   {"ref": REF, "value": {}, "tab": TAB, "host": HOST}, ["ref", "value"]),
    "javascript": ("Run JavaScript in the page (an async function's body: return what you want back, as JSON).",
                   {"code": {"type": "string"}, "tab": TAB, "host": HOST}, ["code"]),
    "prompts": ("What the pages are waiting for you to answer: an alert, a confirm, a prompt, a site's own sign-in "
                "box, or files to upload (tools' answers also show them, as prompts).", {"host": HOST}, []),
    "answer_prompt": ("Answer what a page is waiting on (its id from prompts): accept (OK, or false for Cancel), "
                      "with text for a prompt, user and password for a sign-in, files (paths on that host) for an "
                      "upload.",
                      {"id": {"type": "string"}, "accept": {"type": "boolean"}, "text": {"type": "string"},
                       "user": {"type": "string"}, "password": {"type": "string"},
                       "files": {"type": "array", "items": {"type": "string"}}, "host": HOST}, []),
}
OPS = {"tabs": "tabs", "new_tab": "new_tab", "navigate": "navigate", "close_tab": "close_tab",
       "screenshot": "screenshot", "read_page": "read_page", "find": "find", "get_text": "get_text", "click": "click",
       "hover": "hover", "type": "type", "key": "key", "scroll": "scroll", "form_input": "form_input",
       "javascript": "javascript", "prompts": "prompts", "answer_prompt": "answer_prompt"}


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
        msg = {"op": OPS[tool], **args}
        got = self.mesh.request(where, {"op": "emission", "bot": {"id": self.me, "name": self.bot_name()},
                                        "call": msg}, timeout=WAIT_S)
        result = got.get("result") or {}
        if tool == "tabs" and args.get("sites"):
            sites = self.mesh.request(where, {"op": "emission", "bot": {"id": self.me, "name": self.bot_name()},
                                              "call": {"op": "sites"}}, timeout=WAIT_S).get("result") or {}
            result["sites"] = sites.get("sites", [])
        return result


def content(tool, result):
    """An MCP tool result's content: a screenshot's picture as an image, the rest as text."""
    if tool == "screenshot" and result.get("image"):
        image = result.pop("image")
        return [{"type": "image", "data": image, "mimeType": result.pop("mime", "image/jpeg")},
                {"type": "text", "text": json.dumps(result, indent=1, ensure_ascii=False)}]
    text = result.pop("page", None) or result.pop("text", None) if tool in ("read_page", "get_text") else None
    out = [{"type": "text", "text": json.dumps(result, indent=1, ensure_ascii=False)}]
    if text is not None:
        out.append({"type": "text", "text": meshtools.cut([text]) if len(text) > 60000 else text})
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
            result = {"content": content(name, browser.call(name, args))}
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
