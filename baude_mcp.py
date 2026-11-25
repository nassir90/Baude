#!/usr/bin/env python3
"""baude-mcp: Baude's mesh and Record's notebooks for any agent, as an MCP server on stdin and stdout (a JSON-RPC
message a line), with no GTK and no Baude window. It reaches this machine's host process (baude_headless.py), and
through it every member of the mesh, as Baude does (wire.reach). Its tools are the mesh's, as a Bot has them
(baudelib/meshtools.py: hosts, chats, start_subagent, read_chat, message_chat), and Record's notebooks on any host
(baudelib/notebooktools.py: list_notebooks, list_pages, read_page, and propose_edit, list_edits, revise_edit,
withdraw_edit). An edit it proposes is kept in Record's store on the notebook's host, shown on its page in Record as
Meta's are, for you to accept or reject. Being in the mesh is being trusted: no tool asks first.

    baude-mcp [--socket PATH] [--name NAME]   # the MCP server (claude mcp add baude -- ~/.local/bin/baude-mcp)
    baude-mcp docs [--out FILE]               # its reference, and the wire's: site/docs/mcp.md

--socket: a host process listening on another socket than this user's. --name: who its edits are said to be from
(the agent's own name, its MCP client's, if none's given).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from baudelib import meshtools, notebooktools, wire  # noqa: E402

NAME = "baude"  # (its tools, to Claude Code: mcp__baude__hosts, ...)
SCHEMAS = {**meshtools.SCHEMAS, **notebooktools.SCHEMAS}
DOCS = os.path.join(os.path.dirname(os.path.realpath(__file__)), "site", "docs", "mcp.md")


class Tools:
    """The tools, on the host process at socket (this user's usual one if None); by: who edits are from."""

    def __init__(self, socket=None, by=None):
        self.mesh, self.by = meshtools.Mesh(socket=socket), by

    def call(self, name, args):
        if name in meshtools.SCHEMAS:
            return self.mesh.hosts() if name == "hosts" else getattr(self.mesh, name)(args)
        where = self.mesh.where(args.get("host"))
        if name in notebooktools.EDIT_TOOLS:
            return self.edit(where, name, args)
        books = self.notebooks(where)
        if name == "list_notebooks":
            return {"host": self.mesh.named(where)["host_name"], "notebooks": notebooktools.notebooks_listed(books)}
        nb = notebooktools.notebook_named(books, args.get("notebook"))
        if name == "list_pages":
            return notebooktools.pages_listed(nb)
        waiting = self.edit(where, "list_edits", {"notebook": nb["id"]})["waiting"]
        return notebooktools.page_read(nb, args.get("page"), waiting)

    def notebooks(self, where):
        got = self.mesh.request(where, {"op": "notes"}, timeout=60)
        if got.get("error"):
            raise ValueError(got["error"])
        return got.get("notebooks") or []

    def edit(self, where, tool, args):
        args = {k: v for k, v in args.items() if k != "host"}
        got = self.mesh.request(where, {"op": "notes", "action": "edit",
                                        "edit": {"tool": tool, "args": args, "by": self.by}}, timeout=30)
        if got.get("error"):
            raise ValueError(got["error"])
        return got.get("result") or {}


def serve(socket=None, by=None, rfile=None, wfile=None):
    tools = Tools(socket, by)

    def started(params):  # (none named: its edits are its MCP client's)
        if tools.by is None:
            tools.by = ((params.get("clientInfo") or {}).get("name") or "").strip()[:80] or None
    meshtools.serve_tools(NAME, SCHEMAS, tools.call, rfile, wfile, started)


# ---- its reference

def _kind(p):
    if "$ref" in p:
        return p["$ref"].removesuffix(".json")
    for s in p.get("anyOf") or ():
        if "$ref" in s:
            return s["$ref"].removesuffix(".json") + "?"
    if "enum" in p:
        return " \\| ".join(f"`{v}`" for v in p["enum"])
    t = p.get("type", "any")
    t = "/".join(t) if isinstance(t, list) else t
    return t + (f" of {_kind(p['items'])}" if "items" in p else "")


def _cell(text):
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _fields(d, skip=("op", "req")):
    req = set(d.get("required") or ())
    rows = [f"| `{f}` | {_kind(p)} | {'yes' if f in req else ''} | {_cell(p.get('description'))} |"
            for f, p in (d.get("properties") or {}).items() if f not in skip]
    return ["| Field | Type | Required | What it is |", "|---|---|---|---|", *rows] if rows else []


def docs():
    """site/docs/mcp.md: baude-mcp's tools (their MCP definitions) and the wire's messages and entities
    (schema/types), as Markdown."""
    out = ["# Baude's MCP server and wire API", "",
           "Made by `baude-mcp docs` from the tools' definitions (`baudelib/meshtools.py`, "
           "`baudelib/notebooktools.py`) and `schema/`. Run it again after changing either.", "",
           "## baude-mcp", "",
           "`baude-mcp` is an MCP server on stdin and stdout. Any agent on a machine of the mesh can run it: it needs "
           "no Baude window and no GTK. It reaches the machine's host process (`baude-headless`), and through it "
           "every member of the mesh. Being in the mesh is being trusted: no tool asks first.", "",
           "Installing Baude writes it to `~/.local/bin` with the other commands (`baude-headless install`, or an "
           "app's `--install`). To give it to Claude Code:", "",
           "```sh", "claude mcp add baude -- ~/.local/bin/baude-mcp", "```", "",
           "Options: `--socket PATH` reaches a host process listening on another socket; `--name NAME` is who its "
           "edits are from (its MCP client's name if not given).", "",
           "An edit it proposes to a page is kept in Record's store on the notebook's host, and shown on the page in "
           "Record as Meta's edits are, for you to accept or reject. Nothing is written to the page until you "
           "accept.", "",
           "## Tools", ""]
    for name, (description, props, required) in SCHEMAS.items():
        out += [f"### `{name}`", "", _cell(description), ""]
        table = _fields({"properties": props, "required": required})
        out += (table + [""]) if table else []
    out += ["## The wire", "",
            "Baude's apps, `baude-mcp` and the phone talk to a host process in JSON, one object a line, each with an "
            "`op`. On the same machine, connect to the Unix socket `baude-headless socket` prints; from another, run "
            "`ssh HOST ~/.local/bin/baude-headless connect` and talk to its stdin and stdout. A request carries a "
            "`req` number of the client's choosing, and its answer carries it back; anything wrong is an `error` "
            "with that `req`. `hello` comes first on every connection.", ""]
    for kind, title in (("request", "Requests"), ("reply", "Answers"), ("event", "Events"), ("frame", "Mesh frames")):
        out += [f"### {title}", ""]
        for name in sorted((n for n in wire.MESSAGES if wire.KIND[n] == kind),
                           key=lambda n: wire.DEFS[n]["properties"]["op"]["const"]):
            d = wire.DEFS[name]
            out += [f"#### `{d['properties']['op']['const']}` ({name})", "", _cell(d.get("description")), ""]
            table = _fields(d)
            out += (table + [""]) if table else []
    out += ["### Entities", ""]
    for name in sorted(n for n in wire.DEFS if n not in wire.MESSAGES):
        d = wire.DEFS[name]
        out += [f"#### {name}", "", _cell(d.get("description")), ""]
        table = _fields(d, skip=())
        out += (table + [""]) if table else []
    out += ["### Constants", "", "| Name | Value | What it is |", "|---|---|---|"]
    out += [f"| `{k}` | `{v['const']}` | {_cell(v.get('description'))} |" for k, v in wire.VARIABLES.items()]
    return "\n".join(out) + "\n"


def main():
    p = argparse.ArgumentParser(prog="baude-mcp", description="Baude's mesh and Record's notebooks as an MCP server.")
    p.add_argument("--socket", help="a host process's socket, if not this user's usual one")
    p.add_argument("--name", help="who its edits are from")
    sub = p.add_subparsers(dest="command")
    d = sub.add_parser("docs", help="its reference, and the wire's, as Markdown")
    d.add_argument("--out", help=f"the file written (- for stdout; default {os.path.relpath(DOCS)})")
    args = p.parse_args()
    if args.command == "docs":
        text = docs()
        if args.out == "-":
            return sys.stdout.write(text) and 0
        out = args.out or DOCS
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        return print(f"wrote {out}", file=sys.stderr)
    return serve(args.socket, args.name)


if __name__ == "__main__":
    sys.exit(main())
