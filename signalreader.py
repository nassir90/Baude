"""signalreader — Signal's reader: a model that reads the repository for a ref (its prompts, the MCP relay it
calls tools through, SignalChat, SignalReader) and the evidence it found (ref_evidence, ref_seen).
"""
import hashlib
import json
import os
import re
import sys
import time

from baudelib.claude import spent_since
from baudelib.transcripts import _line_sum, history_items
from lookup import ref_key

from gi.repository import GLib  # noqa: E402

from widgets import load_settings  # noqa: E402,F401

from signalrefs import ref_agent_bash_ok, ref_kubeconfig_touch, ref_money  # noqa: E402
from toolcards import _first_line  # noqa: E402
from sessionlist import ClaudeProc  # noqa: E402


# ------------------------------------------------------------- signal: the reader --
# The rules above find what has a recognizable shape, at once. The reader does the rest: one model session per
# chat (Sonnet, or Haiku) that reads it from the start, the way a person following along would: your messages,
# what Claude ran and saw (its tool calls and the start of each result, and its subagents' work), and each
# reply. For every name in every reply it decides what the name points at there, with Signal's own lookups
# as its tools (what's known, a fresh search like the rules', the live database's schema) beside its own Read
# and Grep, and records each decision through a tool. Baude checks each one mechanically (the reply writes the
# name, the file exists, the name is near the line) and keeps it for that reply: the same word can be a field in
# one reply and English in the next. Separately, what Claude itself read or grepped before a reply is evidence
# no model is needed for (ref_evidence): a name found there in exactly one place is linked to it.

SIGNAL_READER_INTRO = """You are Baude's Signal reader. Baude shows a person an AI coding assistant's conversation \
(Claude Code) and turns the names in Claude's replies into links. You read the conversation in order, part by \
part, the way a person following along would, and decide for each reply which names in it a reader may want to \
jump to, and where each one points, in this repository ({root}).

Each part has, in order: the user's messages ([you]){what}, and Claude's replies ([reply N]). After it: the \
names Baude's rules found in those replies, with the rules' answers{seen}.

Decide every name in the part's replies a reader may want to open: files and folders, functions, classes, \
methods, fields, database tables and columns, constants, env vars, HTTP routes, config keys, Kubernetes and \
Helm objects, services and components, packages, commands, tests, commits, branches, PRs, external tools, and \
the tools Claude itself calls. Read every sentence: many of these are written in plain prose, without \
backticks: services and components ("the Scheduler", "the API gateway", "the Admin Console", "the nginx edge"), \
Kubernetes objects ("the NodePool", "the Argo Rollout"), products ("Karpenter"). Baude's rules only see \
backticked shapes, so the prose names are yours to add; a long reply usually names 20 to 60 things. A proper \
name of a part of this system ("the Admin Console", "pgsync", "the Dev Box") is a name: find its folder. Look everywhere: \
prose, backticks, and inside code blocks and diagrams. Spans of code and config count as well as names: a URL \
or host (`http://api-svc:8001`, `job-<hex>.example.com`: where it's built or configured), a command (`helm \
upgrade`, `--set imageTag=$SHA`: where it's run), a SQL fragment, an error message (`failed: runner stopped`: \
where it's raised), a header. When a backticked span is longer than a name (`strategy: Recreate`, \
`keep_dist=package == "x"`), decide the name inside it as well as the span. A word is a name only where the \
reply uses it as one: `step` in "the slider's `step`" is a field; "the next step" is English. Skip generic \
ideas, numbers and secrets.

Every name gets a decision, including the ones that aren't in this repository: outside tools, products, \
libraries, services and their commands (Postgres, Vite, FastAPI, Keycloak, Chrome, the Anthropic API, `npm ci`, \
`uv sync`, `docker exec`) are not_in_code, why external, except well-known cloud and hosting providers and \
their products (Cloudflare, Vercel, Supabase, Shopify, AWS Lambda…), which need no decision: they're never \
linked; a name the conversation proposes that isn't written \
yet is proposed; one in another checkout or branch, on another machine, or only in a running system (a job id, \
an org or app someone made, a container, a file in another repository) is elsewhere. Marking them tells the \
person they won't find it in this code.

Commands and flags: `python -m pkg.mod` opens the module; `node scripts/shot.mjs`, `bash scripts/x.sh`, \
`build.mjs --check` open the script they run; a flag (`--from`, `--org`) opens where this repository's command \
defines it (its add_argument or option), in the command the reply is about. Routes, however they're written, \
open their handler: in full (`GET /v1/orgs/{{org}}/jobs/{{job}}`), the end of one \
(`POST …/jobs/{{job}}/retry`), a prefix (`/v1/private/jobs/…`: the router), relative to a prefix the reply named \
just before (`GET /{{job}}`, `GET ?status=` under `/v1/orgs/{{org}}/jobs`), or in a diagram; \
lookup with kind route finds most, and a relative one is in the router of the prefix it follows.

Tools:
- mcp__signal__known: what Baude knows about names (the rules' places, earlier decisions{seen_tool}).
- mcp__signal__lookup: search the repository for a name the way Baude's rules do (definitions, files, env \
vars, routes, config keys, Kubernetes objects and Helm values, and language servers where they're on).
- mcp__signal__schema: the live database's tables and their columns (read-only).
- mcp__signal__kube: the Kubernetes objects this repository defines (raw manifests, Helm templates with the names \
their releases render them to, kustomize overlays) and the kubeconfig's contexts; by name or part of one (a pod's \
name finds its workload), kind or namespace, or an overview with nothing given. Kubeconfig files hold credentials: \
never read them yourself.
- Read, Grep, Glob and read-only Bash (git grep / log / show, rg, ls, find; one command per call), to read the \
code yourself.
- mcp__signal__decide: record your decisions for the part, all in one call; call it again to fix any it \
rejects. Each decision: reply (its number), name (exactly as the reply writes it, without backticks), verdict, \
and: for "link", path (repository-relative), line, role (definition, column, table, field, set, read, handler, \
folder, file...) and kind; for "not_in_code", why (proposed: the conversation suggests it but it isn't written; \
external: a third-party tool or product; elsewhere: another checkout, branch or machine; not_found); a short \
note on what it is, always.
  - link: the place a click should open: the definition; a table's model or migration; a column in its table's \
definition; a service's or package's folder; for an env var, the code that reads it (its settings class, \
os.environ), not where it's set; for a config key or a Kubernetes field, the template or values file the reply \
is talking about (the one it names nearby, or Claude just read), not the first file that has the word; for a \
Kubernetes object (a Deployment, a Service, a pod of one), the manifest or Helm template that defines it (kube \
gives its place). A kubeconfig context kube lists is Baude's to link: leave it.
  - plain: ordinary English there, not a name. A name written as code (a path, a dotted or snake_case name, a \
route, a flag, a command) is never plain: link it, or say why it isn't in the code.
  - not_in_code: after you've searched for it (lookup, or Grep); external needs no search.
decide answers with what it kept and rejected, and the names in the part that still have no decision: decide \
those too. A decision carries forward: a later reply that uses the name the same way gets it without a new decision, so \
decide a name again only where it means something else (known shows the decisions so far). A couple of \
searches per name is usually enough. Work read-only and never repeat secrets (keys, tokens, \
passwords). When the part's decisions are in, answer with one short line."""

SIGNAL_READER_PART = """Part {k}: replies {a} to {b}.

{events}

{names}
Decide every name in replies {a} to {b} with mcp__signal__decide."""

SIGNAL_READER_RESUME = """(Replies 1 to {n} were read in an earlier session: their decisions are known to \
mcp__signal__known. The conversation goes on from here.)"""

# the reader's session's environment: Signal's tools loaded at the start (not through a ToolSearch turn), no auto
# memory, no claude.ai connectors, no telemetry or update checks (SignalReader.session_args)
SIGNAL_READER_ENV = {"ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                     "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}

SIGNAL_READER_FRESH = """(Replies 1 to {n} were read before, in other sessions; you see only this part. What was \
decided for the names in it is listed under it, and mcp__signal__known has the rest. A decision carries forward: \
decide those names again only where they mean something else here.)"""

# the reader's tools (MCP), as Claude Code sees them; Baude runs them (Signal._job_tool)
SIGNAL_READER_TOOLS = [
    {"name": "known", "description": "What Baude knows about some names: the rules' answer (places, or why not "
     "found), the decisions made for them in earlier replies, and the places Claude itself read or grepped "
     "before the given reply that mention them.",
     "inputSchema": {"type": "object", "required": ["names"], "properties": {
         "names": {"type": "array", "items": {"type": "string"}, "maxItems": 60},
         "reply": {"type": "integer", "description": "the reply they're in (for what Claude had seen by then)"}}}},
    {"name": "lookup", "description": "Search the repository for a name as Baude's rules do: files by the end of "
     "their path, definitions (def, class, a field, a table or column), env vars (where set and read), HTTP "
     "routes, config keys, Kubernetes objects and Helm values; with language servers where they're on. Returns the "
     "matches.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string"},
         "kind": {"type": "string", "description": "file, symbol, env, constant, route, config, k8s, commit, "
                                                   "branch"}}}},
    {"name": "schema", "description": "The live databases (Postgres and ArangoDB, in Docker or a local cluster; "
     "read-only): the tables (an ArangoDB database's collections) named "
     "table, with their columns; or the tables that have a column named column.",
     "inputSchema": {"type": "object", "properties": {"table": {"type": "string"}, "column": {"type": "string"}}}},
    {"name": "kube", "description": "The Kubernetes objects this repository defines (raw manifests; Helm chart "
     "templates, with the names the releases its helm upgrade --install lines name render them to; kustomize "
     "overlays, with their name prefixes and generated ConfigMaps), each with its place, and the kubeconfig's "
     "contexts (names, clusters and namespaces only). By name or part of one (a pod's name finds its workload), "
     "kind (Deployment, svc, ...) or namespace; with none of them, an overview of the charts, releases and kinds.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "kind": {"type": "string"},
                                                      "namespace": {"type": "string"}}}},
    {"name": "decide", "description": "Record decisions about names in replies. Each is checked: the reply must "
     "write the name, a linked path must exist (the line is moved to the nearest one with the name), and "
     "not_in_code needs a search first. Returns what was kept and what was rejected, and why.",
     "inputSchema": {"type": "object", "required": ["decisions"], "properties": {"decisions": {
         "type": "array", "maxItems": 200, "items": {"type": "object", "required": ["reply", "name", "verdict"],
                                                     "properties": {
             "reply": {"type": "integer"}, "name": {"type": "string"},
             "verdict": {"type": "string", "enum": ["link", "plain", "not_in_code"]},
             "kind": {"type": "string"}, "path": {"type": "string"}, "line": {"type": "integer"},
             "role": {"type": "string"}, "why": {"type": "string", "enum": ["proposed", "external", "elsewhere",
                                                                             "not_found"]},
             "note": {"type": "string"}}}}}}},
]

# the relay Claude Code starts as the reader's MCP server: stdio JSON-RPC in, Baude's socket out (no GTK)
SIGNAL_READER_MCP = r'''import json, socket, sys

sock = sys.argv[1]


def call(tool, args):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock)
    s.sendall((json.dumps({"tool": tool, "args": args}) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = s.recv(1 << 16)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data or b"{}")


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        continue  # (notifications)
    try:
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "signal", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": call("_tools", {}).get("tools", [])}
        elif method == "tools/call":
            p = msg.get("params") or {}
            res = call(p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}],
                      "isError": bool(res.get("error"))}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})
            continue
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    except Exception as e:  # noqa: BLE001 (Baude went away: say so)
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(e)}})
'''


def signal_reader_mcp_path():
    """The relay script, written into Baude's cache (where Claude Code can run it)."""
    path = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "baude", "signal_mcp.py")
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == SIGNAL_READER_MCP:
                return path
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(SIGNAL_READER_MCP)
    return path


def _ref_trim(text, limit):
    """text cut to about limit characters, its start and end kept."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:int(limit * 0.7)].rstrip() + f" […{len(text) - limit} more…] " + text[-int(limit * 0.3):].lstrip()


def _ref_result_text(content):
    """A tool result's text (a string, or text blocks)."""
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def _ref_call_line(name, inp):
    """A tool call in one line: its name and what it's about."""
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit"):
        what = inp.get("file_path") or inp.get("notebook_path") or ""
        if inp.get("offset") or inp.get("limit"):
            what += f" (from line {inp.get('offset') or 1}" + (f", {inp['limit']} lines)" if inp.get("limit") else ")")
    elif name == "Grep":
        what = f"{inp.get('pattern', '')!r}" + (f" in {inp['path']}" if inp.get("path") else "") + \
            (f" ({inp['glob']})" if inp.get("glob") else "")
    elif name == "Glob":
        what = f"{inp.get('pattern', '')}" + (f" in {inp['path']}" if inp.get("path") else "")
    elif name == "Bash":
        what = inp.get("command", "")
    elif name in ("Agent", "Task"):
        what = f"{inp.get('description', '')} ({inp.get('subagent_type') or 'general-purpose'})"
    else:
        what = json.dumps(inp, ensure_ascii=False)
    return f"{name}: {_first_line(str(what), 220)}"


def signal_reader_events(path):
    """A transcript, in order, as what a reader of it sees: [("you", text), ("call", id, name, input),
    ("result", id, text), ("reply", text), ("sub", tool_use_id, [events])]; a subagent's own events go under the
    call that started it (its transcript beside the session's names that call in its meta.json). Read at once: a
    chat that's followed as it grows is read on from where it was (SignalChat), to the same events."""
    if not os.path.isfile(path):
        return []
    import glob
    subs = {}
    sub_dir = os.path.join(os.path.splitext(path)[0], "subagents")
    for meta in glob.glob(os.path.join(sub_dir, "*.meta.json")):
        try:
            with open(meta, encoding="utf-8") as f:
                tid = json.load(f).get("toolUseId")
            with open(meta[:-len(".meta.json")] + ".jsonl", encoding="utf-8", errors="replace") as f:
                sub_items = history_items(f, sidechain=True)
        except (OSError, ValueError):
            continue
        if tid:
            subs[tid] = _ref_events_of(sub_items, {})
    try:  # (a line at a time: the events are a sliver of a transcript, which gets to hundreds of MB of tool output)
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ev for line in f for ev in _ref_events_of(history_items((line,)), subs)]
    except OSError:
        return []


def _ref_events_of(items, subs):
    out = []
    for kind, content, *_rest in items:
        if kind == "user":
            text = content if isinstance(content, str) else "\n".join(
                c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
            if text.strip():
                out.append(("you", text.strip()))
        elif kind == "assistant":
            for c in content if isinstance(content, list) else []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text", "").strip():
                    out.append(("reply", c["text"]))
                elif c.get("type") == "tool_use":
                    out.append(("call", c.get("id"), c.get("name"), c.get("input") or {}))
                    if c.get("id") in subs:
                        out.append(("sub", c.get("id"), subs[c["id"]]))
        elif kind == "tool_results":
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    out.append(("result", c.get("tool_use_id"), _ref_result_text(c.get("content"))))
    return out


def signal_reader_render(events, full, sub=False):
    """Events as the text of a part: in full, Claude's tool calls with the start of each result and its subagents'
    work; else only your messages and the replies."""
    lines, calls = [], {}
    for ev in events:
        if ev[0] == "you" and not sub:
            lines.append(f"[you] {_ref_trim(ev[1], 2500)}")
        elif ev[0] == "you" and sub and full:
            lines.append(f"    [asked] {_ref_trim(ev[1], 400)}")
        elif ev[0] == "reply" and not sub:
            lines.append(f"[reply {ev[2]}]\n{ev[1]}")
        elif ev[0] == "reply" and sub and full:
            lines.append(f"    [subagent said] {_ref_trim(ev[1], 500)}")
        elif ev[0] == "call" and full:
            calls[ev[1]] = ev[2]
            lines.append(("    " if sub else "") + f"[Claude → {_ref_call_line(ev[2], ev[3])}]")
        elif ev[0] == "result" and full:
            name, pad = calls.get(ev[1]), "      " if sub else "  "
            limit = (300 if sub else 900) if name not in ("Agent", "Task") else 1200
            body = "\n".join(_ref_trim(ev[2] or "", limit).splitlines()[:12 if sub else 24])
            if body.strip():
                lines.append("\n".join(pad + "| " + l for l in body.splitlines()))
        elif ev[0] == "sub" and full:
            inner = signal_reader_render(ev[2], full, sub=True)
            if inner:
                lines.append(_ref_trim(inner, 6000))
    return "\n".join(lines)


_REF_READ_LINE = re.compile(r"^\s*(\d+)→(.*)$")  # Claude Code's Read: "   186→    text"
_REF_HIT_LINE = re.compile(r"^([\w./@+-]+?\.[\w]+)[:-](\d+)[:-](.*)$")  # grep / rg / git grep -n: path:line:text


_REF_SEEN_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "Grep", "Bash")  # (the calls whose results show places)


def ref_evidence(events, root, checkouts, carry=None):
    """What Claude saw in its tools' results, in order: [(replies before it, path, line, text)]: the lines of the
    files it read, and grep's path:line hits. Paths are made relative to root (from root itself, or another
    checkout of the same repository: checkouts). carry ({"calls", "replies"}) is where the events before these left
    off, and goes on from here (SignalChat reads a chat on as it grows)."""
    out = []
    carry = {"calls": {}, "replies": 0} if carry is None else carry
    calls = carry["calls"]

    def rel(p):
        if not p:
            return None
        p = os.path.normpath(p)
        if not os.path.isabs(p):
            return p
        for top in (root, *checkouts):
            if top and p.startswith(top.rstrip("/") + os.sep):
                return os.path.relpath(p, top)
        return None
    for ev in events:
        if ev[0] == "reply":
            carry["replies"] += 1
        elif ev[0] == "call":
            if ev[2] in _REF_SEEN_TOOLS:  # (only these are kept: the carry lasts as long as the chat is followed)
                calls[ev[1]] = (ev[2], ev[3] if isinstance(ev[3], dict) else {})
            else:
                calls.pop(ev[1], None)
        elif ev[0] == "result" and ev[1] in calls:
            name, inp = calls[ev[1]]
            text, replies = ev[2] or "", carry["replies"]
            if name == "Read" and (path := rel(inp.get("file_path"))):
                out.append((replies, path, None, ""))
                for line in text.splitlines()[:4000]:
                    if m := _REF_READ_LINE.match(line):
                        out.append((replies, path, int(m.group(1)), m.group(2)[:300]))
            elif name in ("Edit", "Write", "MultiEdit") and (path := rel(inp.get("file_path"))):
                out.append((replies, path, None, ""))
            elif name in ("Grep", "Bash"):
                base = inp.get("path") if name == "Grep" else None
                for line in text.splitlines()[:2000]:
                    if m := _REF_HIT_LINE.match(line.strip()):
                        p = m.group(1)
                        if base and not os.path.isabs(p) and not p.startswith(base.rstrip("/")):
                            p = os.path.join(base, p) if os.path.isabs(base) else p
                        if path := rel(p):
                            out.append((replies, path, int(m.group(2)), m.group(3)[:300]))
        elif ev[0] == "sub":
            for r, p, n, t in ref_evidence(ev[2], root, checkouts):
                out.append((carry["replies"], p, n, t))
    return out


class SignalChat:
    """A chat as Signal reads it (main loop): its events (signal_reader_events, the replies numbered), its replies'
    texts and hashes, and what Claude saw (ref_evidence), read on from where it was as the transcript grows.
    Transcripts are only appended to, so a read parses only the whole lines added since (a line still being written
    waits till it's done), and each subagent's transcript likewise, its events going on under the call that started
    it. One rewritten or cut short (the last line read isn't there any more) is read again from the start. (Read
    whole whenever it grew, a 15 MB chat took 60 ms of the main loop per line another program appended, and the
    largest, 120 to 180 MB, close to a second.) The lists are replaced, not changed: the reader and the worker keep
    the ones they were given."""

    SUB_CALLS = ("Agent", "Task")  # the calls that start subagents: a place is kept after each for its evidence

    def __init__(self, path, root, checkouts):
        self.path, self.root, self.checkouts = path, root, checkouts
        self.sub_dir = os.path.join(os.path.splitext(path)[0], "subagents")
        self.stamp = None  # (the files' sizes and times when last read: while they're the same, nothing's new)
        self.behind = False  # (a read stopped for time: the rest next time, whatever the stamp)
        self._files = {}
        self.restart()

    def restart(self):
        self.events, self.texts, self.replies, self.evidence = [], [], [], []
        self.offset, self.last = 0, None  # the whole lines read, and the last one's (start, _line_sum)
        self.carry = {"calls": {}, "replies": 0}  # (ref_evidence's, from one read to the next)
        # each subagent, by its meta.json: {"stamp", "tid", "offset", "last", "events" (None: no transcript yet),
        # "carry", "seen" (its transcript's size and time)}; and each call that started one, in order (slots, and by
        # tool_use_id): where its evidence goes,
        # {"tid", "replies" (before it), "at", "n" (evidence[at:at + n]), "placed" (its events are in), "i"}
        self.subs, self.slots, self.slot_of = {}, [], {}

    def read(self, until=None):
        """Read what's new: all of it, or what there's time for till until (a time.monotonic()), the rest next time
        (behind; a line or a subagent's transcript at least, then the time's looked at). Returns the number of the
        first reply whose evidence may have changed (a new reply, or one after new evidence: 1 when read from the
        start, one past the last when only other events changed), or None if nothing did."""
        stamp = self._stamp()
        if stamp is None:
            self.behind = False  # (gone: what was read is all there is, till it's back)
            return None
        if stamp == self.stamp and not self.behind:
            return None
        self.stamp, self.behind = stamp, False
        self._owned, self._first, self._changed = False, None, False
        if self._read(until) is False:  # (rewritten or cut short: from the start)
            self.restart()
            self._owned, self._first, self._changed = False, 1, True
            if self._read(until) is False:
                self.stamp = None  # (still changing under it: again next time)
        return (self._first or len(self.replies) + 1) if self._changed else None

    def _stamp(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        self._files = {}  # (the subagents' files: name -> (size, time))
        try:
            with os.scandir(self.sub_dir) as found:
                for e in found:
                    if e.name.endswith((".meta.json", ".jsonl")) and not e.name.startswith("."):
                        s = e.stat()
                        self._files[e.name] = (s.st_size, s.st_mtime_ns)
        except OSError:
            pass
        return st.st_size, st.st_mtime_ns, sorted(self._files.items())

    def _own(self):
        """About to change the events or the evidence: copies of them, once a read."""
        if not self._owned:
            self.events, self.evidence, self._owned = list(self.events), list(self.evidence), True
        self._changed = True

    def _affects(self, reply):
        self._first = reply if self._first is None else min(self._first, reply)

    def _read(self, until=None):
        """On from where it was: False if a file isn't the one read. until: as read's."""
        # the subagents first (a call read below takes its subagent's events), each read only if its files changed
        metas = sorted(n for n in self._files if n.endswith(".meta.json"))
        if set(self.subs) - set(metas):
            return False  # (one is gone)
        for name in metas:
            sub = self.subs.setdefault(name, {"stamp": None, "tid": None, "offset": 0, "last": None, "events": None,
                                              "carry": {"calls": {}, "replies": 0}, "seen": None})
            meta = os.path.join(self.sub_dir, name)
            try:
                if self._files[name] != sub["stamp"]:  # (it's written again when the subagent finishes)
                    with open(meta, encoding="utf-8") as f:
                        tid = json.load(f).get("toolUseId")
                    if sub["tid"] and tid != sub["tid"]:
                        return False
                    sub["stamp"], sub["tid"] = self._files[name], tid
            except (OSError, ValueError, AttributeError):
                continue  # (still being written: next time)
            jsonl = name[:-len(".meta.json")] + ".jsonl"
            if not sub["tid"] or sub["events"] is not None and self._files.get(jsonl) == sub["seen"]:
                continue  # (its transcript is as it was)
            new = []
            got = self._lines(os.path.join(self.sub_dir, jsonl), sub["offset"], sub["last"],
                              lambda line: new.extend(_ref_events_of(history_items((line,), sidechain=True), {})))
            if got is None or got is False and sub["events"] is not None:
                return False  # (rewritten, or gone)
            if got is False:
                continue  # (no transcript yet)
            sub["offset"], sub["last"], sub["seen"] = *got, self._files.get(jsonl)
            if sub["events"] is None or new:
                sub["events"] = (sub["events"] or []) + new
                if self._sub_grew(sub, new) is False:
                    return False
            if until is not None and time.monotonic() > until:
                self.behind = True  # (the other subagents next time, then the chat: as if they were all read first)
                return None
        present = {s["tid"]: s["events"] for s in self.subs.values() if s["tid"] and s["events"] is not None}
        new = []
        got = self._lines(self.path, self.offset, self.last,
                          lambda line: new.extend(_ref_events_of(history_items((line,)), present)), until)
        if got is None:
            return False
        if got is False:
            return None
        self.offset, self.last = got
        self.behind = until is not None and time.monotonic() > until  # (stopped for time, or just done: next time)
        if not new:
            return None
        self._own()
        texts, hashes = [], []
        for i, ev in enumerate(new):  # (numbered, for the parts)
            if ev[0] == "reply":
                new[i] = ("reply", ev[1], len(self.texts) + len(texts) + 1)
                texts.append(ev[1])
                hashes.append(hashlib.sha1(ev[1].encode("utf-8", "replace")).hexdigest()[:16])
        if texts:
            self._affects(len(self.texts) + 1)
            self.texts, self.replies = self.texts + texts, self.replies + hashes
        self._evidence_of(new)
        self.events += new
        return None

    def _evidence_of(self, events):
        """New top-level events' evidence, after the rest; each call that starts a subagent keeps a place after it for
        the subagent's, which grows with its transcript."""
        run, after = [], None  # (after: the place kept by the call just before)
        by_tid = {s["tid"]: s for s in self.subs.values() if s["tid"]}

        def flush():
            if run:
                rows = ref_evidence(run, self.root, self.checkouts, self.carry)
                if rows:
                    self.evidence += rows
                    self._affects(rows[0][0] + 1)
                run.clear()
        for ev in events:
            if ev[0] == "sub":
                flush()
                slot = after if after is not None and after["tid"] == ev[1] else self._slot(ev[1])
                slot["placed"] = True
                rows = [(slot["replies"], p, n, t)
                        for _r, p, n, t in ref_evidence(ev[2], self.root, self.checkouts, by_tid[ev[1]]["carry"])]
                if rows:  # (at the end: the place was kept just now)
                    self.evidence += rows
                    slot["n"] += len(rows)
                    self._affects(slot["replies"] + 1)
                after = None
                continue
            run.append(ev)
            after = None
            if ev[0] == "call" and ev[2] in self.SUB_CALLS:
                flush()
                after = self._slot(ev[1])
        flush()

    def _slot(self, tid):
        slot = {"tid": tid, "replies": self.carry["replies"], "at": len(self.evidence), "n": 0, "placed": False,
                "i": len(self.slots)}
        self.slots.append(slot)
        self.slot_of.setdefault(tid, []).append(slot)
        return slot

    def _sub_grew(self, sub, new):
        """A subagent's transcript grew by new (or has just appeared): its events go on under each call that started
        it (put in after the call where it was read before the subagent appeared), and so does its evidence, in the
        place kept for it. False if the events aren't as read."""
        tid, slots = sub["tid"], self.slot_of.get(sub["tid"]) or ()
        if not slots:
            if any(ev[0] == "call" and ev[1] == tid for ev in self.events):
                return False  # (started by a call that isn't an Agent call, read before it appeared: from the start)
            return None  # (its call isn't read yet: its events go in with it)
        self._own()
        events, fresh = sub["events"], [s for s in slots if not s["placed"]]
        if len(fresh) < len(slots):
            for i, ev in enumerate(self.events):
                if ev[0] == "sub" and ev[1] == tid:
                    self.events[i] = ("sub", tid, events)
        if fresh:
            calls = [i for i, ev in enumerate(self.events) if ev[0] == "call" and ev[1] == tid and not (
                i + 1 < len(self.events) and self.events[i + 1][0] == "sub" and self.events[i + 1][1] == tid)]
            if len(calls) != len(fresh):
                return False
            for i in reversed(calls):
                self.events.insert(i + 1, ("sub", tid, events))
        grown = ref_evidence(new, self.root, self.checkouts, sub["carry"]) if len(fresh) < len(slots) else []
        whole = ref_evidence(events, self.root, self.checkouts, sub["carry"]) if fresh else []
        for slot in slots:
            rows = [(slot["replies"], p, n, t) for _r, p, n, t in (grown if slot["placed"] else whole)]
            slot["placed"] = True
            if rows:
                at = slot["at"] + slot["n"]
                self.evidence[at:at] = rows
                slot["n"] += len(rows)
                for later in self.slots[slot["i"] + 1:]:
                    later["at"] += len(rows)
                self._affects(slot["replies"] + 1)
        return None

    @staticmethod
    def _lines(path, offset, last, take, until=None):
        """Hands take each whole line of a transcript past offset, decoded, a line at a time (they're mostly tool
        output, up to hundreds of MB), and returns (offset, last) on from there. None if it isn't the file read (it's
        shorter, or its last line read, last: (start, _line_sum), isn't there any more), False if there's no file.
        until (a time.monotonic()): no more lines once it's passed, the rest left for next time."""
        try:
            f = open(path, "rb")
        except FileNotFoundError:
            return False
        except OSError:
            return offset, last
        with f:
            try:
                if os.fstat(f.fileno()).st_size < offset:
                    return None
                if last is not None:
                    f.seek(last[0])
                    if _line_sum(f.readline()) != last[1]:
                        return None
                f.seek(offset)
                for raw in f:
                    if not raw.endswith(b"\n") and not SignalChat._whole(raw):
                        break  # (still being written: read once it's done)
                    last, offset = (offset, _line_sum(raw)), offset + len(raw)
                    take(raw.decode("utf-8", "replace"))
                    if until is not None and time.monotonic() > until:
                        break
            except OSError:
                pass
        return offset, last

    @staticmethod
    def _whole(raw):
        """Whether a last line with no newline yet is a whole record (read at once, a file's last line counts)."""
        if not raw.rstrip().endswith(b"}"):
            return False
        try:
            json.loads(raw)
        except ValueError:
            return False
        return True


_REF_FILE_EXT = re.compile(r"\.(py|pyi|ts|tsx|js|jsx|mjs|cjs|css|scss|json|ya?ml|toml|md|sh|go|rs|java|rb|sql|html|"
                            r"txt|lock|cfg|ini|tpl|conf|template|tsp|mmd|env|example|tf|hcl|dockerfile)$", re.I)


def ref_seen(evidence, name, before):
    """The places Claude had seen name before reply number before (1-based): [(path, line)], latest first.
    A file name matches the files it read or edited; a code name, the lines that have it as a word."""
    word = re.sub(r":\d+(:\d+)?$", "", ref_key(name).strip("`")).rstrip("/")
    filish = "/" in word or bool(_REF_FILE_EXT.search(word))  # (a file: matched by path, never by its words)
    last = word if filish else re.split(r"[.:]", re.sub(r"\(.*\)$", "", word))[-1]
    if len(last) < 3:
        return []
    pat = re.compile(r"(?<![\w$])" + re.escape(last) + r"(?![\w$])")
    found = []
    for r, path, line, text in reversed(evidence):
        if r >= before:
            continue
        if filish:
            if path == word or path.endswith("/" + word) or (line is None and os.path.basename(path) == word):
                found.append((path, None))
        elif line is not None and pat.search(text):
            found.append((path, line))
        if len(found) >= 8:
            break
    seen, out = set(), []
    for p, n in found:
        if (p, n) not in seen:
            seen.add((p, n))
            out.append((p, n))
    return out


class SignalReader:
    """Signal's reader (see above): one session per chat, reading it in order; its decisions come back
    through Signal's tools (_job_tool). Main loop, apart from what it hands the worker.
    With "signal_history" "glossary", each part is read by a session of its own: what it knows of the replies
    before is the glossary (the decisions for the part's names come with it, and known has the rest), so its
    context doesn't grow along the chat."""

    PART_CHARS = {"full": 18000, "light": 9000}

    def __init__(self, ref):
        settings = load_settings()
        self.ref = ref
        self.model = settings.get("signal_model") or "sonnet"
        self.effort = settings.get("signal_effort") or "medium"
        self.full = (settings.get("signal_context") or "full") != "light"
        self.glossary_only = settings.get("signal_history") == "glossary"  # (a session per part)
        self.part_chars = int(settings.get("signal_part_chars") or 0) or None  # (characters a part, about)
        self.budget = float(settings.get("signal_budget", 5.0))  # dollars per glossary, per Baude run
        self.proc, self.path, self.events, self.offset = None, None, [], 0
        self.texts, self.replies = [], []  # the chat's replies (number - 1 -> text, hash)
        self.pos = 0  # the next event to read
        self.part = None  # the part being read: {"k", "a", "b", "started", "end"}
        self.parts, self.cost = 0, 0.0
        self.spent, self.soon, self.intro, self.over_budget_said = {}, None, True, False
        self.spare = None  # (glossary-only history: the next part's session, started while this part is read)
        self.evidence, self.stopped, self.searches = [], False, []  # (searches: its own Grep / Bash patterns)

    # ---- what to read
    def follow(self, path):
        """The chat to read (its transcript): from the start, or on from where it was."""
        if not path:
            return
        if path != self.path:
            self.restart_chat(path)
        ref = self.ref
        if len(ref.chat_events) <= len(self.events) and self.events:
            return
        self.events, self.texts, self.replies, self.evidence = \
            ref.chat_events, ref.chat_texts, ref.chat_replies, ref.chat_evidence
        self.schedule()

    def restart_chat(self, path):
        if self.proc is not None:
            self.proc.kill()
        self.drop_spare()
        self.proc, self.path, self.events, self.pos, self.part, self.intro = None, path, [], 0, None, True

    def schedule(self, ms=600):
        if self.soon is None and not self.stopped:
            self.soon = GLib.timeout_add(ms, self.pump)

    # ---- the steps (main loop)
    def pump(self):
        """Read the next part, once the rules have looked at its replies."""
        self.soon = None
        if self.part is not None or self.stopped or self.pos >= len(self.events):
            return False
        if self.cost >= self.budget:
            self.drop_spare()
            if not self.over_budget_said:
                self.over_budget_said = True
                self.ref.note(f"{self.model.capitalize()} stopped reading: it has spent {ref_money(self.cost)}, the "
                              f"cap (\"signal_budget\" in settings)")
            return False
        if self.ref.waiting:
            self.schedule(800)
            return False
        if self.pos == 0 and self.intro:  # (decided before: go on from the first reply without decisions)
            done = self.ref.decided_upto
            first = next((i for i, h in enumerate(self.replies) if h not in done), len(self.replies))
            if first >= len(self.replies):
                self.pos = len(self.events)
                return False
            if first > 0:
                self.pos = next(i for i, ev in enumerate(self.events) if ev[0] == "reply" and ev[2] == first + 1)
                while self.pos > 0 and self.events[self.pos - 1][0] != "reply":
                    self.pos -= 1  # (with what led to it)
                self.resume_from = first
        limit = self.part_chars or self.PART_CHARS["full" if self.full else "light"]
        start, size, end, last_reply = self.pos, 0, self.pos, None
        while end < len(self.events):
            ev = self.events[end]
            size += len(ev[1]) if ev[0] in ("you", "reply") else (len(str(ev[3])) // 3 + 60 if ev[0] == "call" else
                                                                  min(len(ev[2] or ""), 900) if ev[0] == "result" else 3000)
            end += 1
            if ev[0] == "reply":
                last_reply = ev[2]
                if size >= limit:
                    break
        if last_reply is None:  # (no reply yet past here: wait for one)
            return False
        while end < len(self.events) and self.events[end - 1][0] != "reply":
            end -= 1
        part_events = self.events[start:end]
        numbers = [ev[2] for ev in part_events if ev[0] == "reply"]
        a, b = numbers[0], numbers[-1]
        if self.proc is None:
            self.start_session()
        self.parts += 1
        self.part = {"k": self.parts, "a": a, "b": b, "end": end, "started": time.monotonic()}
        self.ref.jobs.put(("reader_part", {"k": self.parts, "a": a, "b": b, "events": part_events,
                                           "full": self.full, "glossary": self.glossary_only}, None))
        return False

    def instructions(self):
        """What the reader is (its system prompt: the same for every session of this chat, so it's cached once)."""
        seen = ", and the places Claude itself looked at that mention them" if self.full else ""
        return SIGNAL_READER_INTRO.format(
            root=self.ref.root, seen=seen, seen_tool=", the places Claude saw" if self.full else "",
            what=", what Claude ran and saw before replying ([Claude → Tool] lines, with the start of each "
                 "result under them; a subagent's work indented under the call that started it)" if self.full
            else "")

    def send(self, k, a, b, text):
        """The part, rendered by the worker (with the rules' answers for its names), to the session (main loop)."""
        if self.part is None or self.part["k"] != k or self.proc is None:
            return False
        if self.intro:  # (a new session: where it starts reading)
            first = a - 1 if self.glossary_only else getattr(self, "resume_from", 0)
            resume = SIGNAL_READER_FRESH if self.glossary_only else SIGNAL_READER_RESUME
            text = (resume.format(n=first) + "\n\n" if first else "") + text
            self.intro = False
        self.proc.user(text)
        self.ref.say(f"{self.model.capitalize()}: reading part {k} (replies {a} to {b} of {len(self.replies)})…")
        self.ref.reader_said("part", f"Part {k} · replies {a} to {b} of {len(self.replies)}")
        self.warm_spare()
        return False

    def start_session(self):
        """A session for the next part: the spare, if one was started (it's done its setup), else a new one."""
        self.proc, self.spare = self.spare or self.spawn(), None
        self.intro = True

    def spawn(self):
        cfg = os.path.join(self.ref.tool_socket_dir(), f"signal-mcp-{id(self)}.json")
        with open(cfg, "w") as f:
            json.dump({"mcpServers": {"signal": {"command": sys.executable,
                                                 "args": [signal_reader_mcp_path(), self.ref.tool_socket()]}}}, f)
        made = {}
        made["proc"] = ClaudeProc(self.on_msg, lambda code: self.on_exit(code, made.get("proc")), self.ref.root,
                                  self.model, "default", None, self.effort, extra=self.session_args(cfg),
                                  env=SIGNAL_READER_ENV)
        return made["proc"]

    def warm_spare(self):
        """Glossary-only history: start the next part's session now, so it's ready when this part is done (a new
        claude takes about a second to set itself up; an idle one costs nothing)."""
        if self.glossary_only and self.spare is None and not self.stopped and self.cost < self.budget and \
                self.part is not None and any(ev[0] == "reply" for ev in self.events[self.part["end"]:]):
            self.spare = self.spawn()

    def drop_spare(self):
        if self.spare is not None:
            self.spare.kill()
            self.spare = None

    def session_args(self, mcp_config):
        """A clean session, not a copy of your Claude Code: it runs as Baude's signal-reader agent (its instructions
        are the system prompt, not Claude Code's coding agent's), with only the tools it uses, Signal's MCP server
        and no other (none of yours, no claude.ai connectors), your user settings but not the project's (its hooks,
        agents, CLAUDE.md), no auto memory, no skills or commands. What's left is the same for every session of a
        chat, so a new one reads it from the cache."""
        agent = {"signal-reader": {"description": "Baude Signal's reader: links the names in a chat's replies",
                                   "prompt": self.instructions()}}
        settings = {"autoMemoryEnabled": False, "disableClaudeAiConnectors": True,
                    "pluginConfigs": {"cc-plugin-agents-md@builtin": {"options": {"instructionFiles": "claude-md"}}}}
        return ["--no-session-persistence", "--mcp-config", mcp_config, "--strict-mcp-config", "--max-turns", "80",
                "--agents", json.dumps(agent), "--agent", "signal-reader", "--tools", "Read,Grep,Glob,Bash",
                "--disable-slash-commands", "--setting-sources", "user", "--settings", json.dumps(settings)]

    def account(self, proc, msg):
        delta = spent_since(proc, msg)
        self.cost += delta
        return delta

    # ---- the session's messages (main loop)
    def on_msg(self, proc, msg):
        if proc is not self.proc:
            return False
        t = msg.get("type")
        if t == "assistant" and not msg.get("parent_tool_use_id"):
            self.ref.reader_said("assistant", msg.get("message", {}).get("content") or [])
            for c in msg.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    if c.get("name") in ("Grep", "Glob", "Bash"):
                        self.searches.append(str(inp.get("pattern") or inp.get("command") or ""))
                    what = inp.get("name") or inp.get("pattern") or inp.get("command") or inp.get("file_path") or \
                        inp.get("table") or inp.get("column") or (f"{len(inp.get('decisions') or [])} decisions"
                                                                  if "decisions" in inp else "")
                    self.ref.say(f"{self.model.capitalize()}, part {self.part['k'] if self.part else '?'}: "
                                 f"{c.get('name', '').removeprefix('mcp__signal__')} {_first_line(str(what), 70)}")
        elif t == "user" and isinstance(content := msg.get("message", {}).get("content"), list):
            self.ref.reader_said("results", content)
        elif t == "control_request" and msg.get("request", {}).get("subtype") == "can_use_tool":
            self.permission(proc, msg)
        elif t == "result":
            delta, part = self.account(proc, msg), self.part
            if part is not None:
                self.pos, self.part = part["end"], None
                self.ref.note(f"{self.model.capitalize()}: part {part['k']} (replies {part['a']} to {part['b']}) read "
                              f"in {time.monotonic() - part['started']:.0f} s ({ref_money(delta)}; "
                              f"{ref_money(self.cost)} so far)")
                self.ref.reader_said("done", f"Part {part['k']} read in {time.monotonic() - part['started']:.0f} s "
                                             f"({ref_money(delta)}; {ref_money(self.cost)} so far)")
                if self.pos >= len(self.events):
                    self.ref.say(None)
                    self.drop_spare()  # (read to the end: a chat that goes on starts a session when it does)
                if self.glossary_only:  # (the next part gets a session of its own)
                    self.proc, self.intro = None, True
                    proc.close_stdin()
                self.schedule(50)
        return False

    def permission(self, proc, msg):
        req = msg["request"]
        name, inp = req.get("tool_name") or "", req.get("input") or {}
        ok = name.startswith("mcp__signal__") or name in ("Read", "Grep", "Glob", "LS") or \
            (name == "Bash" and ref_agent_bash_ok(inp.get("command", "")))
        if ok and not name.startswith("mcp__signal__") and ref_kubeconfig_touch(json.dumps(inp)):
            proc.respond(msg["request_id"], {"behavior": "deny", "message": "Signal's reader doesn't read kubeconfig "
                                             "files (they hold cluster credentials): mcp__signal__kube lists their "
                                             "contexts' names, clusters and namespaces."})
            self.ref.note(f"{self.model.capitalize()}: refused to read a kubeconfig ({name})")
            self.ref.reader_said("refused", f"Refused: reading a kubeconfig ({name})")
            return
        if ok:
            proc.respond(msg["request_id"], {"behavior": "allow", "updatedInput": inp})
        else:
            proc.respond(msg["request_id"], {"behavior": "deny", "message": "Signal's reader is read-only: use Grep, "
                                             "Glob, Read, read-only git commands, or the mcp__signal__ tools."})
            self.ref.note(f"{self.model.capitalize()}: refused {name} {_first_line(str(inp.get('command') or inp), 60)}")
            self.ref.reader_said("refused", f"Refused: {name} {_first_line(str(inp.get('command') or inp), 100)}")

    def on_exit(self, _code, proc=None):
        if proc is not None and proc is self.spare:  # (the spare died before it was needed: start another later)
            self.spare = None
            return False
        if proc is not None and proc is not self.proc:
            return False  # (a session that was done: glossary-only history ends one per part)
        if self.part is not None:  # (it ended mid-part: a new session reads that part again)
            self.ref.note(f"{self.model.capitalize()}: its session ended during part {self.part['k']}")
            self.part = None
        self.proc, self.intro = None, True
        if self.pos < len(self.events):
            self.schedule(2000)
        return False

    def busy(self):
        return not self.stopped and (self.part is not None or
                                     (self.pos < len(self.events) and self.cost < self.budget and
                                      any(ev[0] == "reply" for ev in self.events[self.pos:])))

    def stop(self):
        self.stopped = True
        if self.soon is not None:
            GLib.source_remove(self.soon)
            self.soon = None
        if self.proc is not None:
            self.proc.kill()
        self.drop_spare()
        self.proc, self.part = None, None

    def start(self):
        self.stopped = False
        self.schedule()
