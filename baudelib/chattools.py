"""A chat's own tools: an MCP server on stdin and stdout every chat's agent has (Claude Code's, pi's and Codex's,
Bots' too), run on the chat's host, for what's the chat's alone; the mesh's tools (baudelib.meshtools) stay a Bot's
and Meta's. Each call is a request of the chat's host process (the wire's), which does the work: a thin client, but
for an ask's pictures given as files, read here (on the chat's host, as its agent would). Each is called by the agent
as mcp__chat__<tool>. The Inbox's (ask_user, revise_ask, withdraw_ask, read_ask): the asks a task raises
(baudelib/inbox.py), raised by the chat's model itself, answered back into the chat."""
import base64
import os

from baudelib import meshtools, wire

NAME = "chat"  # (not baude: baude-mcp's, which an agent may have too)

SCHEMAS = {
    "autofix": ("Auto-fix for this chat's pull request (the open one of the branch checked out in this chat's folder): "
                "on, this chat's host reads it every five minutes and sends you a message from Auto-fix when a check "
                "fails, it conflicts with its base, or someone comments on it or reviews it, between your turns; it "
                "goes off by itself once the pull request is merged or closed. on: true to turn it on, false to turn "
                "it off, left out to say whether it's on. Answers with the pull request it follows (null: it's off).",
                {"on": {"type": "boolean", "description": "On or off; left out: as it is"}}, []),
}
WAIT_MAX_S = 600  # (an ask's answer waited for in one call, at most: baudelib/inbox.py's)
PICTURE_MAX = wire.CONSTANTS["IMAGE_BYTES_MAX"]
_TITLE = {"type": "string", "description": "What it's called in the Inbox's list (its body's first line if none)"}
_BODY = {"type": "string", "description": "What it says, in Markdown, as a message is written: headings, bold, lists, "
                                         "tables, code. ![alt](name) on a line of its own, or alone in a table's "
                                         "cell, shows one of its pictures there, by its name"}
_BUTTONS = {"type": "array", "description": "What the person may press, one at least: each a label, or {label, "
                                            "style?: primary|secondary|blue|teal|green|yellow|orange|red|pink|purple|"
                                            "slate, picture?: a picture's name, shown over its label}",
            "items": {"type": ["object", "string"]}}
_FORM = {"type": "array", "description": "Fields for the person to fill, their values sent with the button pressed: "
                                         "each {name, type: text|number|bool|choice, label?, choices? (a choice's), "
                                         "default?, required?}", "items": {"type": "object"}}
_PICTURES = {"type": "array", "description": "Its pictures, for the body and buttons to show by name: each {name, "
                                             "path} (a picture file on this chat's host: one you made, say; relative "
                                             "to this chat's folder) or {name, data} (its bytes, base64). PNG, JPEG, "
                                             "GIF or WebP, 3.75 MB each at most",
             "items": {"type": "object"}}
_ASK = {"type": "string", "description": "The ask's id (ask_user gave it)"}
_WAIT = {"type": "integer", "description": f"Seconds to wait for the answer ({WAIT_MAX_S} at most; 0 or left out: "
                                          f"not at all)"}
SCHEMAS |= {
    "ask_user": ("Ask the person something in their Inbox, as a message they answer by pressing a button: a title, "
                 "a Markdown body (tables, pictures between paragraphs and in table cells: an artefact you compose, "
                 "pictures you made attached), buttons in styles or with pictures, and a form to fill. It's shown on "
                 "every device of theirs (the desktop app and the phone), from this chat. Their answer (the button's "
                 "label, the form's values) comes to you as a message from the Inbox once you're idle, as your next "
                 "turn: end your turn rather than wait. For a short ask, wait_s waits for it here instead. Answers "
                 "with the ask's id.",
                 {"title": _TITLE, "body": _BODY, "buttons": _BUTTONS, "form": _FORM, "pictures": _PICTURES,
                  "wait_s": _WAIT}, ["body", "buttons"]),
    "revise_ask": ("Put a new version of one of this chat's asks (ask_user's): what it says, its pictures, its "
                   "buttons, its form; a field left out stays as it was (pictures given replace them all). The person "
                   "sees it at once, with the versions before under History. One already answered is open again, "
                   "waiting.",
                   {"ask": _ASK, "title": _TITLE, "body": _BODY, "buttons": _BUTTONS, "form": _FORM,
                    "pictures": _PICTURES}, ["ask"]),
    "withdraw_ask": ("Take back one of this chat's asks that's still waiting: it leaves the person's Inbox.",
                     {"ask": _ASK}, ["ask"]),
    "read_ask": ("One of this chat's asks as it is: its status (waiting, answered, withdrawn, stopped), its answer "
                 "(the button pressed, its label, the form's values, by whom if not the person), how many versions it "
                 "has. wait_s waits for an answer first (its answer then isn't sent you as a message as well).",
                 {"ask": _ASK, "wait_s": _WAIT}, ["ask"]),
}


class Chat:
    """The chat's reach into its own host process (me: its id)."""

    def __init__(self, me, socket=None):
        self.me = me
        self.mesh = meshtools.Mesh(me, socket)

    def autofix(self, args):
        if not self.me:
            raise ValueError("these tools are a chat's (its id wasn't given)")
        on = args.get("on")
        msg = {"op": "autofix", "session": self.me, **({"on": bool(on)} if isinstance(on, bool) else {})}
        got = self.mesh.request(self.mesh.local, msg, timeout=90)
        return {"autofix": got.get("autofix")}

    # ---- the Inbox's
    def _mine(self):
        if not self.me:
            raise ValueError("these tools are a chat's (its id wasn't given)")
        return self.me

    def _wait(self, aid, wait_s):
        try:
            wait_s = max(0, min(WAIT_MAX_S, int(wait_s or 0)))
        except (TypeError, ValueError):
            wait_s = 0
        got = self.mesh.request(self.mesh.local, {"op": "inbox_wait", "session": self.me, "ask": aid,
                                                  "timeout": wait_s}, timeout=wait_s + 30)
        return {k: got.get(k) for k in ("ask", "status", "answer", "version")}

    def folder(self):
        """This chat's folder on its host, as its host lists it now (a picture's relative path is from it)."""
        return next((s.get("cwd") for s in self.mesh.sessions(self.mesh.local) if s.get("id") == self.me), None)

    def ask_user(self, args):
        msg = {"op": "inbox_raise", "session": self._mine(), **_ask_fields(args, self.folder)}
        got = self.mesh.request(self.mesh.local, msg, timeout=60)
        out = {"ask": got["ask"], "version": got.get("version"), "status": "waiting"}
        if args.get("wait_s"):
            out |= self._wait(got["ask"], args["wait_s"])
        return out

    def revise_ask(self, args):
        self._mine()
        msg = {"op": "inbox_revise", "ask": args.get("ask"), **_ask_fields(args, self.folder)}
        got = self.mesh.request(self.mesh.local, msg, timeout=60)
        return {"ask": got["ask"], "version": got.get("version")}

    def withdraw_ask(self, args):
        got = self.mesh.request(self.mesh.local, {"op": "inbox_withdraw", "session": self._mine(),
                                                  "ask": args.get("ask")}, timeout=30)
        return {"ask": got["ask"], "status": "withdrawn"}

    def read_ask(self, args):
        self._mine()
        return self._wait(args.get("ask"), args.get("wait_s"))


def _ask_fields(args, folder):
    """An ask's fields as the tools give them, as inbox_raise and inbox_revise take them: its pictures' files read
    (folder(): where a relative path is from)."""
    out = {k: args[k] for k in ("title", "body", "buttons", "form") if args.get(k) is not None}
    if isinstance(out.get("buttons"), list):  # (a label alone: a plain button)
        out["buttons"] = [{"label": b} if isinstance(b, str) else b for b in out["buttons"]]
    if args.get("pictures") is not None:
        if not isinstance(args["pictures"], list):
            raise ValueError("pictures is a list: each {name, path} or {name, data}")
        here = []

        def base():
            if not here:
                here.append(folder() or os.getcwd())
            return here[0]
        out["pictures"] = [_picture(p, base) for p in args["pictures"]]
    return out


def _picture(p, base):
    """A picture as the tools give it ({name, path} or {name, data}; a path alone named by its file's name) as the
    host takes it ({name, data}). ValueError for a file that can't be read, or one too big."""
    if isinstance(p, str):
        p = {"path": p}
    if not isinstance(p, dict):
        raise ValueError("a picture is {name, path} or {name, data}")
    name = p.get("name") or os.path.basename(str(p.get("path") or ""))
    if isinstance(p.get("data"), str) and p["data"]:
        data = p["data"]
        if data.startswith("data:") and "," in data:  # (a data: URL's)
            data = data.split(",", 1)[1]
        return {"name": name, "data": "".join(data.split())}
    path = p.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"{name or 'a picture'}: give its path or its data")
    path = os.path.expanduser(path)
    path = path if os.path.isabs(path) else os.path.join(base(), path)
    try:
        if os.path.getsize(path) > PICTURE_MAX:
            raise ValueError(f"{name}: larger than {PICTURE_MAX} bytes")
        with open(path, "rb") as f:
            return {"name": name, "data": base64.b64encode(f.read()).decode()}
    except OSError as e:
        raise ValueError(f"{name}: {e.strerror or e}") from None


def serve(me=None, rfile=None, wfile=None, socket=None):
    """The MCP server (meshtools.serve_tools: a message a line, each call on a thread of its own)."""
    chat = Chat(me, socket)
    meshtools.serve_tools(NAME, SCHEMAS, lambda name, args: getattr(chat, name)(args), rfile, wfile)


def server(session, socket_path, python, headless):
    """Its entry in an agent's --mcp-config: run by the host's own Python, on its socket."""
    return {"command": python, "args": [headless, "chat-tools", "--socket", os.path.abspath(socket_path)],
            "env": {"BAUDE_SESSION": session}}
