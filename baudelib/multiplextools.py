"""A Bot's terminal tools: an MCP server on stdin and stdout (as baudelib.emissiontools is), run on the Bot's host. Each
call reaches the terminals of a host of the mesh (this one if none's named) through that host's process (wire's
multiplex op): Multiplex's own (multiplex.py, started there for open if it isn't running) and the shells Baude's and
Record's Kubernetes view has open in pods. The terminal glows round its edges while the Bot's at it. Each is called by
the agent as mcp__multiplex__<tool>."""
import json
import os
import sys
import threading

from baudelib import meshtools, toolnames

NAME = "multiplex"
WAIT_S = 160  # (a request's answer waited for this long: a wait's longest, Multiplex's start, and then some)

S, N, B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}
HOST = {"type": "string", "description": "A mesh host (mcp__mesh__hosts); none: this one"}
TERM = {"type": "string", "description": "list_terminals' id"}

SCHEMAS = {
    "list_terminals": ("The terminals open on a host: Multiplex's tabs and the Kubernetes view's pod shells, the user's "
                       "and yours, each with its id, title, folder, command and size. query: only those whose title, "
                       "folder, command or text (screen and scrollback) has all its words, with the lines that do.",
                       {"query": S, "host": HOST}, []),
    "open_terminal": ("A new tab in Multiplex (its window opened if it isn't): command run by your shell there, or the "
                      "shell itself if none; in cwd. Answers once it's drawn its first output, with its screen.",
                      {"command": S, "cwd": S, "title": S, "host": HOST}, []),
    "read_terminal": ("What's on a terminal's screen now and where its cursor is; lines_above: that many lines of "
                      "scrollback above the screen too.",
                      {"terminal": TERM, "lines_above": N, "host": HOST}, ["terminal"]),
    "send_text": ("Type text into a terminal, as if typed (a newline is Enter); enter: press Enter after it. Answers "
                  "with its screen once output stops for a moment (wait_for for longer).",
                  {"terminal": TERM, "text": S, "enter": B, "host": HOST}, ["terminal", "text"]),
    "send_keys": ("Press keys in a terminal in turn, a space between: Enter, Tab, Escape, Backspace, Delete, Up, Down, "
                  "Left, Right, Home, End, PageUp, PageDown, F1–F12, a character, or a combination like ctrl+c, "
                  "ctrl+d, alt+b, shift+Tab. Answers with its screen once output stops for a moment.",
                  {"terminal": TERM, "keys": S, "host": HOST}, ["terminal", "keys"]),
    "wait_for": ("Wait till pattern (a regex) shows on a terminal's screen, or its output's been quiet for quiet "
                 "seconds (2 if no pattern), or its program ends, or timeout seconds (30; 120 at most) pass: which "
                 "it was, and the screen.",
                 {"terminal": TERM, "pattern": S, "quiet": N, "timeout": N, "host": HOST}, ["terminal"]),
    "close_terminal": ("End a terminal's program (as closing its tab would) and close it.",
                       {"terminal": TERM, "host": HOST}, ["terminal"]),
}
OPS = {"list_terminals": "list", "open_terminal": "open", "read_terminal": "read", "send_text": "type",
       "send_keys": "key", "wait_for": "wait", "close_terminal": "close"}


class Terminals:
    """The Bot's reach into each host's terminals (me: its chat's id; its name asked of its host)."""

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
        props = SCHEMAS[tool][1]
        where = self.mesh.where(args.get("host"))
        call = {**{k: v for k, v in args.items() if k in props and k != "host"}, "op": OPS[tool]}
        got = self.mesh.request(where, {"op": "multiplex", "bot": {"id": self.me, "name": self.bot_name()},
                                        "call": call}, timeout=WAIT_S)
        return got.get("result") or {}


def content(result):
    """An MCP tool result's content: its screen as text of its own (as it is: its lines, not JSON's escapes), the rest
    as JSON."""
    screen = result.pop("screen", None)
    out = [{"type": "text", "text": json.dumps(result, indent=1, ensure_ascii=False)}]
    if screen is not None:
        out.append({"type": "text", "text": meshtools.cut([screen]) if len(screen) > 60000 else screen})
    return out


def serve(me=None, rfile=None, wfile=None, socket=None):
    """The MCP server (as emissiontools.serve: a message a line, each call on a thread of its own; a cancelled call
    answered with nothing)."""
    rfile, wfile = rfile or sys.stdin, wfile or sys.stdout
    terms, lock, cancelled = Terminals(me, socket), threading.Lock(), set()

    def send(msg):
        with lock:
            wfile.write(json.dumps(msg) + "\n")
            wfile.flush()

    def key(mid):
        return json.dumps(mid, sort_keys=True)

    def call(mid, name, args):
        try:
            if name not in SCHEMAS:
                raise ValueError(f"no tool {name}")
            result = {"content": content(terms.call(name, args))}
        except Exception as e:  # (said to the model, which may try again)
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        if key(mid) not in cancelled:
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        cancelled.discard(key(mid))
    listed = [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props,
                                                            **({"required": req} if req else {})}}
              for n, (d, props, req) in SCHEMAS.items()]

    def take(msg):
        method, mid = msg.get("method"), msg.get("id")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if method == "notifications/cancelled":
            cancelled.add(key(params.get("requestId")))
            return
        if mid is None:
            return
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": NAME, "title": toolnames.SERVERS[NAME], "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": listed}})
        elif method == "tools/call":
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            threading.Thread(target=call, args=(mid, params.get("name"), args), daemon=True).start()
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})
    for line in rfile:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            if isinstance(msg, dict):
                take(msg)
        except Exception:  # (a message that's wrong: said, and the next one read)
            sys.excepthook(*sys.exc_info())


if __name__ == "__main__":
    serve(os.environ.get("BAUDE_SESSION"))
