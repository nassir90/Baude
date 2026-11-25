"""A chat's own tools: an MCP server on stdin and stdout every chat's agent has (Claude Code's and pi's), run on the
chat's host, for what's the chat's alone; the mesh's tools (baudelib.meshtools) stay a Bot's and Meta's. Each call is
a request of the chat's host process (the wire's), which does the work: a thin client. Each is called by the agent as
mcp__chat__<tool>."""
import os

from baudelib import meshtools

NAME = "chat"  # (not baude: baude-mcp's, which an agent may have too)

SCHEMAS = {
    "autofix": ("Auto-fix for this chat's pull request (the open one of the branch checked out in this chat's folder): "
                "on, this chat's host reads it every five minutes and sends you a message from Auto-fix when a check "
                "fails, it conflicts with its base, or someone comments on it or reviews it, between your turns; it "
                "goes off by itself once the pull request is merged or closed. on: true to turn it on, false to turn "
                "it off, left out to say whether it's on. Answers with the pull request it follows (null: it's off).",
                {"on": {"type": "boolean", "description": "On or off; left out: as it is"}}, []),
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


def serve(me=None, rfile=None, wfile=None, socket=None):
    """The MCP server (meshtools.serve_tools: a message a line, each call on a thread of its own)."""
    chat = Chat(me, socket)
    meshtools.serve_tools(NAME, SCHEMAS, lambda name, args: getattr(chat, name)(args), rfile, wfile)


def server(session, socket_path, python, headless):
    """Its entry in an agent's --mcp-config: run by the host's own Python, on its socket."""
    return {"command": python, "args": [headless, "chat-tools", "--socket", os.path.abspath(socket_path)],
            "env": {"BAUDE_SESSION": session}}
