#!/usr/bin/env python3
"""baude-mcp: Baude's mesh and Record's notebooks for any agent, as an MCP server on stdin and stdout (a JSON-RPC
message a line), with no GTK and no Baude window. It reaches this machine's host process (baude_headless.py), and
through it every member of the mesh, as Baude does (wire.reach). Its tools are the mesh's, as a Bot has them
(baudelib/meshtools.py: hosts, chats, start_subagent, read_chat, message_chat), and Record's notebooks on any host
(list_notebooks, list_pages, read_page, and propose_edit, list_edits, revise_edit, withdraw_edit). It's a thin client:
each tool is one or two of the wire's requests (notes, edits, edit_propose, edit_revise, edit_withdraw), which the
notebook's host carries out; an edit is kept there, and any Record showing that notebook shows it, for you to accept
or reject. What it sends a chat is from it, by its name, not from the chat's user. Being in the mesh is being
trusted: no tool asks first.

    baude-mcp [--socket PATH] [--name NAME]   # the MCP server (claude mcp add baude -- ~/.local/bin/baude-mcp)
    baude-mcp docs [--out FILE]               # its reference, and the wire's: site/docs/mcp.md

--socket: a host process listening on another socket than this user's. --name: who it is, to the chats it messages
and on the edits it proposes (its MCP client's name, if none's given; else baude-mcp).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from baudelib import meshtools, notebooktools, wire  # noqa: E402

NAME = "baude"  # (its tools, to Claude Code: mcp__baude__hosts, ...)
DEFAULT_BY = "baude-mcp"  # (who it is with no --name, and no name from its MCP client)
DOCS = os.path.join(os.path.dirname(os.path.realpath(__file__)), "site", "docs", "mcp.md")

NOTEBOOK = {"type": "string", "description": "A notebook's name (list_notebooks)"}
HOST = {"type": "string", "description": "The host it's on, by its id or name (hosts); this one if none"}
NOTEBOOK_TOOLS = {
    "list_notebooks": ("Record's notebooks on a host: each one's name and how many pages it has.", {"host": HOST}, []),
    "list_pages": ("A notebook's pages, Home first: each one's name (a page under another is Parent:Child) and how many "
                   "characters it has.", {"host": HOST, "notebook": NOTEBOOK}, ["notebook"]),
    "read_page": ("A page's text, in Markdown ([[Page]] links to another page), and the edits waiting on it.",
                  {"host": HOST, "notebook": NOTEBOOK,
                   "page": {"type": "string", "description": "Its name (list_pages)"}}, ["notebook", "page"]),
    "propose_edit": ("Propose a change to a page: old_text (copied exactly from the page, there once) replaced with "
                     "new_text; an empty old_text adds new_text at the page's end. It waits on the notebook's host for "
                     "the user, who accepts or rejects it in Record: nothing's written until they accept. A change to "
                     "text one of the edits waiting puts in or replaces revises that edit (it's yours then).",
                     {"host": HOST, "notebook": NOTEBOOK, "page": {"type": "string"},
                      "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                     ["notebook", "page", "new_text"]),
    "list_edits": ("The edits waiting for the user on a notebook's pages, each as its page has its passage now and "
                   "with who proposed it (by: yours, Meta's, another agent's); with all, those accepted, rejected or "
                   "withdrawn too.",
                   {"host": HOST, "notebook": NOTEBOOK, "page": {"type": "string"},
                    "all": {"type": "boolean", "description": "Decided ones too"}}, ["notebook"]),
    "revise_edit": ("Change an edit that's waiting: its new_text, and the passage it replaces if old_text is given. It "
                    "stays one edit, yours now.",
                    {"host": HOST, "notebook": NOTEBOOK, "id": {"type": "string", "description": "The edit's (list_edits)"},
                     "new_text": {"type": "string"}, "old_text": {"type": "string"}}, ["notebook", "id", "new_text"]),
    "withdraw_edit": ("Take back an edit that's waiting.",
                      {"host": HOST, "notebook": NOTEBOOK, "id": {"type": "string"}}, ["notebook", "id"]),
}
SCHEMAS = {**meshtools.SCHEMAS, **NOTEBOOK_TOOLS}


def edit_said(e):
    """An edit as a tool gives it: {id, page, old_text, new_text, by, status?, note?}."""
    out = {"id": e["id"], "page": e["page"], "old_text": e["old"], "new_text": e["new"]}
    if e.get("by"):
        out["by"] = e["by"]
    if e.get("status") != "pending":
        out["status"] = e.get("status")
    if e.get("gone"):
        out["note"] = "what it replaces isn't on the page any more: revise its old_text, or withdraw it"
    return out


def edit_answer(got):
    """edit_propose's, edit_revise's or edit_withdraw's answer (edit) as the tool gives it."""
    e, took = got["edit"], got.get("took_in") or []
    if e["status"] == "pending":
        joined = f" It took in {', '.join(took)}, now taken back." if took else ""
        text = (f"Edit {e['id']} waits on {e['page']} for the user to accept or reject in Record; nothing is written "
                f"until they accept (list_edits with all says which).{joined}")
    else:
        text = f"Edit {e['id']} is taken back."
    return {"id": e["id"], "page": e["page"], "status": e["status"], **({"took_in": took} if took else {}),
            "text": text}


class Tools:
    """The tools, on the host process at socket (this user's usual one if None); by: who it is (to chats it messages,
    and on its edits)."""

    def __init__(self, socket=None, by=None):
        self.socket, self.by = socket, by
        self.mesh = meshtools.Mesh(socket=socket, agent=by or DEFAULT_BY)

    def named(self, by):
        self.by = by
        self.mesh.agent = by or DEFAULT_BY

    def call(self, name, args):
        args = {k: v for k, v in args.items() if v is not None}  # (a field given as null: not given)
        if name in meshtools.SCHEMAS:
            return self.mesh.hosts() if name == "hosts" else getattr(self.mesh, name)(args)
        where = self.mesh.where(args.get("host"))
        by = self.by or DEFAULT_BY
        if name == "propose_edit":
            return edit_answer(self.ask(where, {"op": "edit_propose", "by": by, **self.fields(
                args, "notebook", "page", "old_text", "new_text")}))
        if name == "revise_edit":
            return edit_answer(self.ask(where, {"op": "edit_revise", "by": by, **self.fields(
                args, "notebook", "id", "new_text", "old_text")}))
        if name == "withdraw_edit":
            return edit_answer(self.ask(where, {"op": "edit_withdraw", **self.fields(args, "notebook", "id")}))
        if name == "list_edits":
            got = self.ask(where, {"op": "edits", **self.fields(args, "notebook", "page", "all")})
            return {"notebook": args.get("notebook"), "waiting": [edit_said(e) for e in got["edits"]],
                    **({"decided": [edit_said(e) for e in got.get("decided") or ()]} if args.get("all") else {})}
        books = self.ask(where, {"op": "notes"}, 60).get("notebooks") or []
        if name == "list_notebooks":
            return {"host": self.mesh.named(where)["host_name"],
                    "notebooks": [{"name": notebooktools.full_name(t), "pages": len(t.get("pages") or {})}
                                  for t in books]}
        nb = notebooktools.notebook_named(books, args.get("notebook"))
        pages = nb.get("pages") or {}
        if name == "list_pages":
            return {"notebook": notebooktools.full_name(nb),
                    "pages": [{"page": p, "chars": len(pages[p])} for p in notebooktools.sorted_pages(pages)]}
        page = notebooktools.page_named(pages, args.get("page"))
        if page is None:
            raise ValueError(f"There's no page {args.get('page')!r} in {notebooktools.full_name(nb)}: list_pages "
                             "lists them.")
        waiting = self.ask(where, {"op": "edits", "notebook": nb["id"], "page": page})["edits"]
        return {"notebook": notebooktools.full_name(nb), "page": page, "text": pages[page],
                "edits_waiting": [edit_said(e) for e in waiting]}

    @staticmethod
    def fields(args, *keys):
        return {k: args[k] for k in keys if k in args}

    def ask(self, where, msg, timeout=30):
        got = self.mesh.request(where, msg, timeout=timeout)
        if got.get("error"):
            raise ValueError(got["error"])
        return got


def serve(socket=None, by=None, rfile=None, wfile=None):
    tools = Tools(socket, by)

    def started(params):  # (none named: it's its MCP client, by its name)
        if tools.by is None:
            tools.named(" ".join(str((params.get("clientInfo") or {}).get("name") or "").split())[:80] or None)
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


def _rows(d, skip=(), at=""):
    """A table's rows for an object's fields, and, under each that's an object or a list of them with fields of its
    own, theirs (parent.field, parent[].field)."""
    req, rows = set(d.get("required") or ()), []
    for f, p in (d.get("properties") or {}).items():
        if f in skip:
            continue
        rows.append(f"| `{at}{f}` | {_kind(p)} | {'yes' if f in req else ''} | {_cell(p.get('description'))} |")
        if p.get("properties"):
            rows += _rows(p, at=f"{at}{f}.")
        elif (p.get("items") or {}).get("properties"):
            rows += _rows(p["items"], at=f"{at}{f}[].")
    return rows


def _fields(d, skip=("op", "req")):
    rows = _rows(d, skip)
    return ["| Field | Type | Required | What it is |", "|---|---|---|---|", *rows] if rows else []


def docs():
    """site/docs/mcp.md: baude-mcp's tools (their MCP definitions) and the wire's messages and entities
    (schema/types), as Markdown."""
    out = ["# Baude's MCP server and wire API", "",
           "Made by `baude-mcp docs` from the tools' definitions (`baudelib/meshtools.py`, `baude_mcp.py`) and "
           "`schema/`. Run it again after changing either.", "",
           "## baude-mcp", "",
           "`baude-mcp` is an MCP server on stdin and stdout. Any agent on a machine of the mesh can run it: it needs "
           "no Baude window and no GTK. It reaches the machine's host process (`baude-headless`), and through it "
           "every member of the mesh. Being in the mesh is being trusted: no tool asks first.", "",
           "Installing Baude writes it to `~/.local/bin` with the other commands (`baude-headless install`, or an "
           "app's `--install`). To give it to Claude Code:", "",
           "```sh", "claude mcp add baude -- ~/.local/bin/baude-mcp", "```", "",
           "Options: `--socket PATH` reaches a host process listening on another socket; `--name NAME` is who it is: "
           "what it sends a chat (`message_chat`) is shown as from it, not from the chat's user, and the edits it "
           "proposes are said to be its (its MCP client's name if not given, else `baude-mcp`).", "",
           "Each tool is one or two of the wire's requests, below: the notebook's host does the work. An edit it "
           "proposes to a page is kept on the notebook's host (`edit_propose`), and any Record showing that notebook, "
           "on that machine or another, shows it on its page with who proposed it, for you to accept or reject. "
           "Nothing is written to the page until you accept.", "",
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
    p.add_argument("--name", help="who it is, to the chats it messages and on the edits it proposes")
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
