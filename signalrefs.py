"""signalrefs — what Signal's refs are made of: candidates in text, a ref's kind and route, code's shapes and
excerpts (ref_candidates … ref_excerpt_re), Claude Code's tools, subagents, skills and MCP servers (TOOLS,
ToolCatalog, mcp_tool_schemas), what a reader's ref may be (ref_agent_bash_ok, ref_secretish, ref_kube_hint…), and
what a hover fetches ahead (db_prefetch, kube_page_for).
"""
import hashlib
import json
import os
import re
import subprocess
import threading

from baudelib.claude import claude_version
from lookup import ref_key
from lookupkube import PG_LISTS, _KUBE_NAME, _ks, db_names, kubeconfig_paths

from widgets import WorkerPool, pretty_path  # noqa: E402,F401
from refkinds import (  # noqa: E402,F401
    KUBE_KINDS, _REF_METHODS, _REF_PLAIN, ref_hashlike, ref_kind, ref_kube, ref_quiet, ref_symbolish,
)
from markdownview import resolve_path  # noqa: E402,F401
from pickers import _data_home  # noqa: E402,F401
from kubeview import KUBE_PODS, KUBE_SECTIONS  # noqa: E402,F401
from schemaview import PG_CACHE, _FORGOTTEN, _LOCATING, db_locate  # noqa: E402,F401


REF_TOOL_KINDS = ("tool", "subagent", "skill")  # (looked up in memory, never kept from an earlier run)
# what Signal looks up while it isn't running (look_locally: no network, no cluster but the local ones the catalogue
# reads, nothing kept), and what it looks
# for on other branches when this checkout hasn't it (_on_branches), in the REF_BRANCHES most recently changed too
REF_LOCAL_KINDS = ("file", "branch", "env", "constant", "symbol", "config", "service", "k8s", "package", "cli",
                   "external", "route", "tool", "subagent", "skill")
REF_BRANCH_KINDS = ("symbol", "file")
REF_BRANCHES = 12
REF_SENSITIVE = re.compile(r"(^|/)(\.env[^/]*|[^/]*\.env|[^/]*secret[^/]*|[^/]*\.(pem|key|p12|pfx))$", re.I)
_REF_BLOCK_ROUTE = re.compile(rf"(?<![\w-])(?:{_REF_METHODS})\s+(?:…|\.\.\.)?/[\w./{{}}<>:~*-]*[\w}}>*-]")
REF_DECISION_FIELDS = ("message", "surface", "verdict", "kind", "path", "line", "role", "note", "why", "by", "at")


def ref_candidates(text, skipped=None, hints=None):
    """The names in a reply worth looking up: {surface: (kind guess, shown before it's found)}. Backticked
    spans can be anything name-shaped, or a span ref_span reads (a piece of code is shown only once found);
    plain text only the shapes that are rarely ordinary words. Code blocks and paths that already link (they
    exist relative to the folder) are left out. A name the reply calls a Kubernetes object or a context ("the `x`
    Deployment") is one, whatever its shape, and hints gets the kind it was given ({surface: kind})."""
    found = {}
    blocks = re.compile(r"(?ms)^\s*```.*?(?:^\s*```[^\n]*$|\Z)")
    for block in blocks.findall(text):  # (a route in a diagram or a curl line: shown once it's found)
        for m in _REF_BLOCK_ROUTE.finditer(block):
            found.setdefault(ref_key(m.group(0)), ("route", False))
    text = blocks.sub("", text)
    parts = re.split(r"(`[^`\n]+`)", text)
    for i, part in enumerate(parts):
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            raw = part[1:-1]
            before, after = parts[i - 1] if i else "", parts[i + 1] if i + 1 < len(parts) else ""
            if tool := ref_tool_kind(raw, before, after):
                found[raw] = (tool, True)  # a tool, a subagent type or a skill Claude Code has
            elif (hint := ref_kube_hint(before, after)) and re.fullmatch(_KUBE_NAME, raw) and not resolve_path(raw):
                found[raw] = ("k8s", hint != "context")  # (a context may be any word's: shown once it's found)
                if hints is not None:
                    hints[raw] = hint
            elif (kind := ref_kind(raw)) and not resolve_path(raw):
                found[ref_key(raw)] = (kind, not ref_quiet(raw))
            elif re.fullmatch(r"\d{7,12}", raw):  # (a hash that's all digits, or a number: linked only if git has it)
                found[raw] = ("commit", False)
            elif skipped is not None and not kind and re.fullmatch(r"[^\s]{2,80}", raw) and not resolve_path(raw):
                skipped[raw] = skipped.get(raw, 0) + 1  # a name, but not a kind Signal knows yet
            continue
        part = re.sub(r"\[[^\]]*\]\([^)\s]*\)|https?://\S+", " ", part)  # links are links already
        for m in re.finditer(r"\b([A-Z][A-Za-z]+|mcp__[\w-]+) tool\b", part):  # "the Agent tool"
            if m.group(1) in TOOL_NAMES:
                found.setdefault(m.group(0), ("tool", True))
        for m in _REF_PLAIN.finditer(part):
            s, kind = m.group(0), m.lastgroup
            if kind == "commit" and not ref_hashlike(s):
                continue
            if kind == "file" and resolve_path(s):
                continue
            found.setdefault(ref_key(s), (kind, False))
    return found


def ref_tool_kind(name, before="", after=""):
    """Whether a backticked name is a tool Claude Code has (or a subagent type, or a skill): "tool", "subagent",
    "skill" or None. A tool name that's also a plain word (Read, Write, Agent, Skill) counts only with "tool"
    next to it; elsewhere it's left to the model pass, which reads the context."""
    if name in TOOL_NAMES:
        if re.fullmatch(r"[A-Z][a-z]+", name):
            return "tool" if re.search(r"\btools?\b", (before[-40:] + " " + after[:40]).lower()) else None
        return "tool"
    if name in SUBAGENTS and (not SUBAGENTS[name].get("built_in") or "-" in name):
        return "subagent"
    if name in SKILLS and ("-" in name or ":" in name):
        return "skill"
    return None


def ref_codeish(surface):
    """Whether a name is written as code (a path, a route, a flag or command, an env var, a dotted, snake_case or
    camelCase name): never plain English, wherever it's written."""
    kind = ref_kind(surface)
    return kind in ("file", "route", "env", "cli", "commit", "pr") or kind == "symbol" and ref_symbolish(ref_key(surface)) \
        or (kube := ref_kube(surface)) is not None and kube[0] != "name"


def ref_redact(line):
    """A line from a file that may hold secrets, with every value hidden: NAME=•••."""
    return re.sub(r"([=:]\s*)\S.*", r"\1•••", line)


def blob_sha(data):
    """git's hash of a file's bytes (what `git hash-object` prints), without running git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def ref_map_line(old, new, line):
    """line (1-based) of the old lines, carried into the new ones through a diff; None if that line
    itself changed."""
    if max(len(old), len(new)) > 20000:
        return None
    import difflib
    i = line - 1
    for tag, i1, i2, j1, _j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if i1 <= i < i2:
            return j1 + (i - i1) + 1 if tag == "equal" else None
    return None


def ref_find_snippet(loc, new, sensitive=False):
    """Where a location's line is in the new lines, found by the lines stored around it: the same line
    with the most matching neighbours (then the nearest), else a close match within 200 lines."""
    snippet = (loc.get("snippet") or "").split("\n")
    start, line = loc.get("snippet_start"), loc.get("line")
    if not start or not line or not (0 <= line - start < len(snippet)):
        return None
    k = line - start
    norm = ref_redact if sensitive else (lambda s: s)

    def around(j):
        return sum(1 for d in range(-k, len(snippet) - k)
                   if 0 <= j + d < len(new) and norm(new[j + d]) == snippet[k + d])
    if snippet[k].strip():
        exact = [j for j, s in enumerate(new) if norm(s) == snippet[k]]
        if exact:
            return min(exact, key=lambda j: (-around(j), abs(j + 1 - line))) + 1
    import difflib
    want, best, at = "\n".join(snippet), 0.8, None
    for j in range(max(0, line - 201), min(len(new), line + 199)):
        window = "\n".join(norm(s) for s in new[max(0, j - k):j - k + len(snippet)])
        m = difflib.SequenceMatcher(None, want, window)
        if m.quick_ratio() >= best and (r := m.ratio()) >= best:
            best, at = r, j
    return at + 1 if at is not None else None


def ref_in_order(want, have):
    """Whether the names in want all appear in have, in that order (others may come between)."""
    it = iter(have)
    return all(any(w == h for h in it) for w in want)


def ref_route_score(template, target):
    """How well a route template (/jobs/{job_id}) matches a path as written (/jobs/abc, /jobs/{id}):
    None if it doesn't, else the number of segments that match literally. A catch-all ({rest:path})
    scores below every route that matches without one."""
    t, g = template.strip("/").split("/"), target.strip("/").split("/")
    param = re.compile(r"\{[^}]*\}|<[^>]*>|:\w+|\*")
    score = 0
    for i, a in enumerate(t):
        if re.fullmatch(r"\{\w+:path\}", a):
            return score - 1000
        if i >= len(g) or not (param.fullmatch(a) or param.fullmatch(g[i]) or a == g[i]):
            return None
        score += a == g[i] and not param.fullmatch(a)  # (a parameter named the same isn't a literal match)
    return score if len(t) == len(g) else None


def ref_name_role(path, text, name):
    """What a line does with an UPPER_SNAKE name: sets it (an env var), reads it from the environment,
    is its secret source, defines it (a constant), uses it, or is documentation."""
    base, n = os.path.basename(path).lower(), re.escape(name)
    if "doppler" in path.lower():
        return "secret source"
    if (base.startswith(".env") or base.endswith(".env")) and re.match(rf"\s*(export\s+)?{n}\s*=", text):
        return "set"
    if base.endswith((".yaml", ".yml")) and (re.match(rf"\s*-?\s*[\"']?{n}[\"']?\s*[:=]", text)
                                             or re.match(rf"\s*-?\s*name:\s*[\"']?{n}[\"']?\s*(#.*)?$", text)):
        return "set"
    if (base.startswith("dockerfile") or base.endswith((".dockerfile", ".containerfile"))) \
            and re.match(rf"\s*(ENV|ARG)\s+{n}\b", text):
        return "set"
    if base.endswith((".sh", ".bash", ".zsh", ".envrc")) and re.match(rf"\s*(export\s+)?{n}=", text):
        return "set"
    if re.search(rf"environ(?:\.get)?\s*[\[(]\s*[\"']{n}[\"']|getenv\(\s*[\"']{n}[\"']|env::var\(\s*\"{n}\""
                 rf"|process\.env(?:\.{n}\b|\[\s*[\"']{n}[\"'])|import\.meta\.env\.{n}\b"
                 rf"|(?:validation_)?alias\s*=\s*[\"']{n}[\"']", text):
        return "read"
    code = base.endswith((".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".go"))
    if not code and re.search(rf"\$\{{?{n}\b", text):
        return "read"  # ${NAME} in a shell script, a compose file, a workflow
    if code and re.match(rf"\s*(?:export\s+)?(?:pub\s+)?(?:(?:const|let|var|static|final)\s+)?{n}\s*(?::[^=]*)?=(?!=)",
                         text):
        return "defined"
    return "doc" if base.endswith((".md", ".rst", ".txt")) else "used"


REF_CODE_PATHS = ["*.py", "*.pyi", "*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs", "*.cjs", "*.rs", "*.go", "*.sql"]


def ref_is_test(path):
    base = os.path.basename(path)
    return any(d in "/" + path for d in ("/tests/", "/test/", "/__tests__/")) or base.startswith("test_") or \
        ".test." in base or ".spec." in base


def ref_def_role(path, text, name):
    """What kind of definition of name a line is (None if it isn't one)."""
    n = re.escape(name)
    if re.search(rf"(create_table\(|__tablename__\s*=|CREATE\s+TABLE\s+(IF NOT EXISTS\s+)?)\s*[\"']?{n}\b", text, re.I):
        return "table"
    if re.search(rf"(Column|ForeignKey)\(\s*[\"']{n}[\"']", text):
        return "column"
    if re.match(rf"\s*revision\s*(:[^=]*)?=\s*[\"']{n}[\"']", text):
        return "migration"  # alembic: revision = "relay_0039"
    if re.match(rf"\s*(async\s+)?def\s+{n}\s*\(", text):
        return "method" if text[:1].isspace() else "function"
    if re.match(rf"\s*class\s+{n}\b", text) or re.search(rf"\b(class|struct|trait)\s+{n}\b", text):
        return "class"
    if re.search(rf"\b(interface|type|enum)\s+{n}\b", text):
        return "type"
    if re.search(rf"\b(function|fn|func)\s+(\([^)]*\)\s*)?{n}\b", text):
        return "function"
    if re.match(rf"(export\s+)?(const|let|var)\s+{n}\b", text) or re.match(rf"{n}\s*(:[^=]*)?=[^=]", text):
        return "variable"  # top-level only: a local variable inside a function isn't a definition worth a link
    if re.search(rf"\bexport\s*(type\s*)?\{{[^}}]*\bas\s+{n}\b", text):
        return "re-export"
    if re.search(rf"self\.{n}\s*(:[^=]*)?=[^=]", text):
        return "attribute"
    if re.match(rf"\s+(async\s+)?(static\s+)?([gs]et\s+)?{n}\s*(<[^>]*>)?\([^)]*\)[^=;{{]*\{{", text):
        return "method"  # (a TypeScript method, its body on that line or below)
    if re.match(rf"\s+(readonly\s+|public\s+|private\s+)?{n}\??\s*:", text) and not path.endswith(".sql"):
        return "field"
    return None


def ref_member_re(name):
    """A line that declares name as a member of something: a field or parameter (name: str, name?: T), a dict or
    object key ("name": ..., name: ...), a column (Column("name", ...)), a model's attribute (name = Column(...))
    or a YAML key."""
    n = re.escape(name)
    return re.compile(rf"(?:^|[\s{{(,;])(?:readonly\s+)?{n}\??\s*:(?![:=])|[\"']{n}[\"']\s*:|Column\(\s*[\"']{n}[\"']"
                      rf"|^\s+{n}\s*(?::[^=]*)?=\s*[\w.]*(?:Column|Field|column|field)\(")


_REF_ANY_MEMBER = re.compile(r"^\s*(?:readonly\s+)?[\"']?[A-Za-z_]\w*[\"']?\??\s*:(?![:=])|^\s*(?:\w+\.)?Column\("
                             r"|^\s+\w+\s*(?::[^=]*)?=\s*[\w.]*(?:Column|Field|column|field)\(")


_REF_SIGNATURE = re.compile(r"\s*(?:(?:export|async|static|public|private|protected|pub)\s+)*(?:def|function|fn|func|class)\b"
                            r"|\s*(?!(?:\w+\.)?Column\()[\w.]+\s*(?:<[^>]*>)?\(")  # (whose parameters aren't the block's members)


def ref_shape_blocks(lines, names):
    """Where a file's lines declare every one of names as members of one thing (a class, an interface or type, a
    table, a YAML mapping, an object or dict, a parameter list): [(the 0-based line that opens it, how many other
    members it has)]. It opens on the line above the members that's indented less, or on their line when
    they're all on one."""
    decls, leads = {}, {}  # lines declaring each name (a one-line object), and those that aren't a signature (a block's)
    for n in names:
        pat = ref_member_re(n)
        decls[n] = {i for i, s in enumerate(lines) if n in s and pat.search(s)}
        leads[n] = {i for i in decls[n] if not _REF_SIGNATURE.match(lines[i])}
        if not decls[n]:
            return []
    indent = lambda s: len(s) - len(s.lstrip())  # noqa: E731
    out, rare = {}, min(names, key=lambda n: len(decls[n]))
    for i in sorted(decls[rare])[:200]:
        if all(i in decls[n] for n in names):
            h, extra = i, len(re.findall(r"[\"']?\b[A-Za-z_]\w*[\"']?\??\s*:(?![:=])", lines[i])) - len(names)
        elif i in leads[rare]:
            ind, h, e = indent(lines[i]), i - 1, i + 1
            while h >= 0 and (not lines[h].strip() or indent(lines[h]) >= ind):
                h -= 1
            while e < len(lines) and (not lines[e].strip() or indent(lines[e]) >= ind):
                e += 1
            if h < 0 or not all(h in decls[n] or any(h < j < e and indent(lines[j]) == ind for j in leads[n])
                                for n in names):
                continue
            extra = sum(1 for j in range(h + 1, e) if indent(lines[j]) == ind and _REF_ANY_MEMBER.match(lines[j])) - \
                sum(1 for n in names if h not in decls[n])
        else:
            continue
        out.setdefault(h, max(0, extra))
    return list(out.items())


def ref_container(lines, h, path):
    """What opens on line h (0-based) of a file, holding members: (role, name). The role is class, type, table,
    entity (a data model's fields in YAML), parameters or object."""
    s = lines[h]
    if m := re.match(r"\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)", s):
        return "class", m.group(1)
    if m := re.search(r"\b(?:interface|type|struct|enum)\s+(\w+)", s):
        return "type", m.group(1)
    if re.search(r"\b(?:Table|create_table)\(", s):
        m = re.search(r"(?:Table|create_table)\(\s*[\"'](\w+)", s) or \
            (h + 1 < len(lines) and re.match(r"\s*[\"'](\w+)[\"']", lines[h + 1]))
        return "table", m.group(1) if m else ""
    if path.endswith((".yaml", ".yml")) and re.match(r"\s*fields\s*:\s*$", s):
        ind = len(s) - len(s.lstrip())
        return "entity", next((m.group(1) for t in reversed(lines[:h]) if len(t) - len(t.lstrip()) < ind
                               and (m := re.match(r"\s*-\s*name:\s*[\"']?([\w.-]+)", t))), "")
    if m := re.search(r"\b(?:def|function|fn|func)\s+(\w+)", s):
        return "parameters", m.group(1)
    m = re.search(r"(\w+)[\"']?\??\s*[:=]\s*(?:[\w.\[\]]+\s*)?[{(\[]", s) or re.match(r"\s*-?\s*([\w.-]+)\s*:\s*$", s)
    return "object", m.group(1) if m else ""


_REF_TYPING = {"Literal", "None", "Optional", "Union", "Field", "Any", "List", "Dict", "Set", "Tuple", "Annotated",
               "Mapped", "Column", "True", "False", "ClassVar", "Final", "Sequence", "Mapping", "Callable", "Iterable",
               "Record", "Partial", "Required", "Readonly", "Array", "Promise", "Pick", "Omit", "Type", "String",
               "Integer", "Boolean", "Text", "JSON", "DateTime", "BaseModel", "Self", "Path", "Exception"}


def ref_excerpt_re(text):
    """A piece of code as a pattern: any spacing where it has spaces (and none), either quote for a quote, and
    anything for an ellipsis (create_app(...).openapi()). Not inside a longer name."""
    out = []
    for tok in re.split(r"(\s+|\.\.\.|…)", text):
        if tok.isspace():
            out.append(r"\s*")
        elif tok in ("...", "…"):
            out.append(r".*?")
        elif tok:
            out.append("".join("[\"'`]" if c in "\"'" else re.escape(c) for c in tok))
    return re.compile((r"(?<![\w.])" if re.match(r"\w", text) else "") + "".join(out) +
                      (r"(?!\w)" if re.search(r"\w$", text) else ""))


# --------------------------------------------------------- tool schemas --
# Claude Code's tools as Anthropic describes them, for Signal's tool cards (a reply that talks about the Agent
# tool, `TodoWrite`, `Read`): input and output schemas from the Agent SDK package that matches the installed CLI
# (SDK 0.3.N ships sdk-tools.d.ts, generated from CLI 2.1.N's own JSON Schemas), descriptions and permissions
# from the tools reference in the docs; and what's local: the calls a session made, subagent and skill
# definitions, and the tools/list of MCP servers you configured. Fetched once per CLI version, with your OK.

TOOL_SDK_PACKAGE = "@anthropic-ai/claude-agent-sdk"
TOOL_DOCS_MD = "https://code.claude.com/docs/en/tools-reference.md"
TOOL_DOCS_PAGE = "https://code.claude.com/docs/en/tools-reference"
TOOL_NAMES = set()     # every tool name seen: the CLI's init lists, the catalogue, the calls in the chat
SUBAGENTS = {}         # subagent type -> {"description", "model", "tools", "path"} (definitions; built-ins too)
SKILLS = {}            # skill name -> {"description", "path"}
TOOL_SDK_RENAME = {"FileRead": "Read", "FileEdit": "Edit", "FileWrite": "Write", "Mcp": None,
                   "ListMcpResources": "ListMcpResourcesTool", "ReadMcpResource": "ReadMcpResourceTool"}
SUBAGENTS_BUILT_IN = {"general-purpose": "researches, searches and runs multi-step tasks (all tools)",
                      "Explore": "read-only search across a codebase", "Plan": "designs an implementation plan",
                      "claude-code-guide": "answers questions about Claude Code, the Agent SDK and the API",
                      "statusline-setup": "sets up the status line", "fork": "a copy of the current conversation"}


def tool_dir():
    return os.path.join(_data_home(), "baude", "tool-schemas")


# (MACOS_DESKTOP_CLAUDE, claude_exe and claude_version: baudelib.claude)


def tool_fetch(cli):
    """Fetch the Agent SDK package for this CLI (from npm's registry: only its sdk-tools.d.ts and manifest.json
    are kept) and the docs' tools reference, into tool_dir()/<cli>. Returns (ok, what happened)."""
    import io
    import tarfile
    import urllib.request

    def get(url, limit=20_000_000):
        req = urllib.request.Request(url, headers={"User-Agent": "baude"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(limit)
    major, minor, patch = (cli or "0.0.0").split(".")
    version = f"0.3.{patch}" if (major, minor) == ("2", "1") else None
    base = f"https://registry.npmjs.org/{TOOL_SDK_PACKAGE}"
    folder = os.path.join(tool_dir(), cli or "unknown")
    try:
        try:
            if version is None:
                raise OSError("no matching SDK version")
            tgz = get(f"{base}/-/claude-agent-sdk-{version}.tgz")
        except OSError:  # no SDK release for this CLI: the latest one's (its manifest says which CLI)
            version = json.loads(get(f"{base}/latest"))["version"]
            tgz = get(f"{base}/-/claude-agent-sdk-{version}.tgz")
        os.makedirs(folder, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tar:
            for name in ("sdk-tools.d.ts", "manifest.json"):
                member = tar.extractfile(f"package/{name}")
                if member is not None:
                    with open(os.path.join(folder, name), "wb") as f:
                        f.write(member.read())
        with open(os.path.join(folder, "sdk-version"), "w") as f:
            f.write(version)
        with open(os.path.join(folder, "tools-reference.md"), "wb") as f:
            f.write(get(TOOL_DOCS_MD, 3_000_000))
    except (OSError, ValueError, KeyError, tarfile.TarError) as e:
        return False, f"couldn't fetch the tool schemas: {e}"
    return True, f"Agent SDK {version} and the tools reference, into {pretty_path(folder)}"


def _ts_fields(body):
    """The top-level fields of a generated TypeScript interface body: [{name, type, required, doc}]."""
    fields, doc, i, lines = [], "", 0, body.split("\n")
    while i < len(lines):
        line = lines[i]
        if re.match(r"^  /\*\*", line):  # a field's JSDoc
            chunk = [line]
            while "*/" not in lines[i] and i + 1 < len(lines):
                i += 1
                chunk.append(lines[i])
            doc = " ".join(re.sub(r"^\s*(/\*\*|\*/|\*)\s?", "", c).replace("*/", "").strip() for c in chunk).strip()
        elif m := re.match(r"^  (\"?[\w$-]+\"?)(\??): (.*)$", line):
            text, depth = m.group(3), m.group(3).count("{") - m.group(3).count("}")
            while (depth > 0 or not text.rstrip().endswith(";")) and i + 1 < len(lines):
                i += 1
                text += "\n" + lines[i]
                depth += lines[i].count("{") - lines[i].count("}")
            typ = re.sub(r"\s+", " ", re.sub(r"/\*\*.*?\*/", "", text, flags=re.S)).strip().rstrip(";").strip()
            fields.append({"name": m.group(1).strip('"'), "type": typ, "required": not m.group(2), "doc": doc})
            doc = ""
        elif line.strip() and not line.startswith("  "):
            doc = ""
        i += 1
    return fields


def tool_parse_sdk(text):
    """{tool name: {"input": [field], "output": type text}} from the SDK's sdk-tools.d.ts."""
    tools, starts = {}, [(m.start(), m) for m in re.finditer(r"^export (?:interface (\w+) \{|type (\w+) =)", text, re.M)]
    for k, (at, m) in enumerate(starts):
        name = m.group(1) or m.group(2)
        chunk = text[at:starts[k + 1][0] if k + 1 < len(starts) else len(text)]
        chunk = re.sub(r"\n/\*\*(?:(?!\*/).)*\*/\s*$", "", chunk, flags=re.S).rstrip()  # (the next one's JSDoc)
        for suffix, key in (("Input", "input"), ("Output", "output")):
            if not name.endswith(suffix) or name in ("ToolInputSchemas", "ToolOutputSchemas"):
                continue
            base = name[:-len(suffix)]
            tool = TOOL_SDK_RENAME.get(base, base)
            if not tool:
                continue
            entry = tools.setdefault(tool, {})
            if key == "input" and m.group(1):
                entry["input"] = _ts_fields(chunk[chunk.index("{") + 1:chunk.rindex("}")])
            entry[key + "_text"] = chunk.strip()
    return tools


def tool_parse_docs(text):
    """{tool name: {"description", "permission", "anchor"}} from the docs' tools reference table."""
    tools = {}
    for m in re.finditer(r"^\| `([\w-]+)` \| (.*?) \| (Yes|No)[^|]*\|\s*$", text, re.M):
        desc = m.group(2)
        anchor = next(iter(re.findall(r"\(#([\w-]+)\)", desc)), None)
        desc = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", desc)
        desc = re.sub(r"\s*See [^.|]*(behavior|tool)[^.|]*(\.|$)", "", desc).strip()
        tools[m.group(1)] = {"description": desc, "permission": m.group(3) == "Yes", "anchor": anchor}
    return tools


class ToolCatalog:
    """The tools of the installed Claude Code, merged from the cached SDK types and docs (tool_fetch), and from
    MCP servers' tools/list (mcp_tool_schemas)."""

    def __init__(self):
        self.tools, self.cli, self.sdk, self.loaded = {}, None, None, False

    def load(self):
        """Read the cache for this CLI's version (else the newest cached). False if nothing is cached."""
        cli = claude_version()
        root = tool_dir()
        folder = os.path.join(root, cli or "unknown")
        if not os.path.isdir(folder):
            older = sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))),
                           key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")]) if os.path.isdir(root) else []
            if not older:
                return False
            folder = os.path.join(root, older[-1])
        try:
            sdk = tool_parse_sdk(open(os.path.join(folder, "sdk-tools.d.ts"), encoding="utf-8").read())
        except OSError:
            sdk = {}
        try:
            docs = tool_parse_docs(open(os.path.join(folder, "tools-reference.md"), encoding="utf-8").read())
        except OSError:
            docs = {}
        try:
            self.sdk = open(os.path.join(folder, "sdk-version")).read().strip()
        except OSError:
            self.sdk = None
        self.cli = os.path.basename(folder)
        for name in set(sdk) | set(docs):
            entry = {**docs.get(name, {}), **sdk.get(name, {}), "source": "anthropic"}
            self.tools[name] = {**self.tools.get(name, {}), **entry}
        TOOL_NAMES.update(self.tools)
        self.loaded = bool(self.tools)
        return self.loaded

    def add_mcp(self, server, tools):
        """A server's tools/list: mcp__<server>__<tool>, with its JSON Schema turned into fields."""
        norm = re.sub(r"[^a-zA-Z0-9_-]", "_", server)
        for t in tools:
            schema = t.get("inputSchema") or {}
            required = set(schema.get("required") or [])
            fields = [{"name": k, "type": _json_type(v), "required": k in required, "doc": (v or {}).get("description", "")}
                      for k, v in (schema.get("properties") or {}).items()]
            name = f"mcp__{norm}__{t.get('name')}"
            self.tools[name] = {"description": t.get("description", ""), "input": fields, "source": "mcp",
                                "server": server}
            TOOL_NAMES.add(name)

    def get(self, name):
        return self.tools.get(name)


def _json_type(schema):
    """A JSON Schema's type as a short TypeScript-ish string."""
    schema = schema or {}
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"][:12])
    t = schema.get("type")
    if t == "array":
        return f"{_json_type(schema.get('items'))}[]"
    if isinstance(t, list):
        return " | ".join(t)
    if t == "object" and schema.get("properties"):
        return "{ " + "; ".join(f"{k}: {_json_type(v)}" for k, v in list(schema["properties"].items())[:6]) + " }"
    return t or ("any" if not schema.get("anyOf") else " | ".join(_json_type(x) for x in schema["anyOf"][:6]))


TOOLS = ToolCatalog()


def _frontmatter(path):
    """A markdown file's YAML frontmatter as flat strings (name, description, model, tools...)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(20000)
    except OSError:
        return {}
    m = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    out = {}
    for line in (m.group(1).split("\n") if m else []):
        if mm := re.match(r"([\w-]+):\s*(.*)", line):
            out[mm.group(1)] = mm.group(2).strip().strip("'\"")
    return out


def local_definitions(root):
    """Subagent types and skills defined for a folder (its .claude) and for you (~/.claude):
    ({type: info}, {skill: info}). Built-in subagent types are listed too, without a file."""
    agents = {name: {"description": d, "built_in": True} for name, d in SUBAGENTS_BUILT_IN.items()}
    skills = {}
    homes = [os.path.join(root, ".claude"), os.path.expanduser("~/.claude")]
    for home in homes:
        adir = os.path.join(home, "agents")
        for f in sorted(os.listdir(adir)) if os.path.isdir(adir) else []:
            if f.endswith(".md"):
                fm = _frontmatter(os.path.join(adir, f))
                name = fm.get("name") or f[:-3]
                agents.setdefault(name, {"description": fm.get("description", ""), "model": fm.get("model"),
                                         "tools": fm.get("tools"), "path": os.path.join(adir, f)})
        sdir = os.path.join(home, "skills")
        for dirpath, dirs, files in os.walk(sdir) if os.path.isdir(sdir) else []:
            if dirpath.count(os.sep) - sdir.count(os.sep) >= 3:  # (a skill is a folder or two down, not deeper)
                dirs[:] = []
            if "SKILL.md" in files:
                fm = _frontmatter(os.path.join(dirpath, "SKILL.md"))
                name = fm.get("name") or os.path.basename(dirpath)
                skills.setdefault(name, {"description": fm.get("description", ""),
                                         "path": os.path.join(dirpath, "SKILL.md")})
    return agents, skills


def mcp_servers(root):
    """MCP servers configured for a folder: its .mcp.json, and ~/.claude.json (yours, and this project's)."""
    servers = {}
    try:
        with open(os.path.join(root, ".mcp.json")) as f:
            servers.update(json.load(f).get("mcpServers") or {})
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.expanduser("~/.claude.json")) as f:
            cfg = json.load(f)
        servers.update(cfg.get("mcpServers") or {})
        servers.update(((cfg.get("projects") or {}).get(root) or {}).get("mcpServers") or {})
    except (OSError, ValueError, AttributeError):
        pass
    return {name: s for name, s in servers.items() if isinstance(s, dict)}


def mcp_tool_schemas(server, root, timeout=20):
    """A stdio MCP server's tools/list: start it, initialize, list its tools, stop it. [] for anything else
    (an HTTP server, a failure)."""
    if server.get("type") not in (None, "stdio") or not server.get("command"):
        return []
    try:
        p = subprocess.Popen([server["command"], *(server.get("args") or [])], cwd=root, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                             env={**os.environ, **{k: str(v) for k, v in (server.get("env") or {}).items()}})
    except OSError:
        return []
    answers, ready = {}, {1: threading.Event(), 2: threading.Event()}

    def read():
        for line in p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") in ready:
                answers[msg["id"]] = msg
                ready[msg["id"]].set()
        for ev in ready.values():  # (it exited)
            ev.set()

    def send(msg):
        p.stdin.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
        p.stdin.flush()
    threading.Thread(target=read, daemon=True).start()
    try:
        send({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                          "clientInfo": {"name": "baude-signal", "version": "1"}}})
        if ready[1].wait(timeout) and "result" in answers.get(1, {}):  # (it answers before it's told anything else)
            send({"method": "notifications/initialized"})
            send({"id": 2, "method": "tools/list", "params": {}})
            ready[2].wait(timeout)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            p.kill()
        except OSError:
            pass
    return ((answers.get(2) or {}).get("result") or {}).get("tools") or []


# what a model calls a kind -> the glossary's kind
REF_AGENT_KINDS = {"file": "file", "commit": "commit", "branch": "branch", "pr": "pr", "env": "env",
                   "constant": "constant", "function": "symbol", "class": "symbol", "field": "symbol",
                   "table": "symbol", "migration": "symbol", "route": "route", "config": "config", "k8s": "k8s",
                   "service": "service", "package": "package", "cli": "cli", "external": "external", "test": "symbol",
                   "tool": "tool", "subagent": "subagent", "skill": "skill"}
REF_RULE_KINDS = {"file", "commit", "branch", "pr", "env", "constant", "symbol", "route", "config", "k8s", "cli", "tool",
                  "subagent", "skill"}  # the rules can try these
_REF_AGENT_FIRST = re.compile(r"(git\s+(log|show|grep|ls-files|ls-tree|rev-parse|branch|status|diff|cat-file|"
                              r"for-each-ref|blame|describe)|rg|grep|ls|wc|find|head|tail|cat|file|stat|tree)\b")


def ref_agent_bash_ok(command):
    """Whether a Bash command from the model pass only reads: one allowed command (git's read-only ones, rg,
    ls, find without -exec / -delete...), piped only into head, tail, wc, grep, sort, uniq or cut."""
    command = re.sub(r"^\s*cd\s+[^;&|<>`$]+?\s*(&&|;)\s*", "", command)  # (a leading "cd <folder> &&" is fine)
    if re.search(r"[;&><`\n]|\$\(|\s-(exec|execdir|delete|ok|fprint)\b|--output\b", command):
        return False
    first, *rest = [p.strip() for p in command.split("|")]
    return bool(_REF_AGENT_FIRST.match(first)) and all(re.match(r"(head|tail|wc|grep|sort|uniq|cut)\b", p) for p in rest)


def ref_secretish(text):
    """Whether a name looks like a secret (an API key, a token, a private key): never kept as an entity."""
    return bool(re.search(r"\b(sk-ant-|sk-[A-Za-z0-9]{8}|gh[pousr]_|github_pat_|xox[abprs]-|AKIA[0-9A-Z]{8}|AIza)|"
                          r"-----BEGIN|\b[A-Za-z0-9+/_-]{36,}\b", text))


def ref_junk(surface, texts):
    """Whether a listed name isn't one: a number, or a plain lowercase word written only in prose (backticked,
    `app` may well be a name)."""
    if re.fullmatch(r"[\d.,:]+|[\d.]+\s*\w{0,3}", surface) or len(surface) < 2:
        return True
    return bool(re.fullmatch(r"[a-z]+", surface)) and not any(f"`{surface}`" in t for t in texts)


def ref_money(x):
    return f"${x:.3f}" if x < 1 else f"${x:.2f}"


def ref_surface_in(surface, texts):
    """surface as one of the replies writes it (a model may change the case), or None if none writes it."""
    if not surface:
        return None
    if any(surface in t for t in texts):
        return surface
    m = next((m for t in texts if (m := re.search(re.escape(surface), t, re.I))), None)
    return m.group(0) if m else None


def ref_sentence(texts, surface, width=170):
    """The text around the first place a reply writes surface (for the verifier)."""
    for t in texts:
        i = t.find(surface)
        if i >= 0:
            return re.sub(r"\s+", " ", t[max(0, i - width):i + len(surface) + width]).strip()
    return ""


KUBE_WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Rollout", "CronJob", "Job", "ReplicaSet", "Pod")
KUBE_ORDER = KUBE_WORKLOADS + ("Service", "Ingress", "HTTPRoute", "ConfigMap", "Secret", "ServiceAccount",
                               "HorizontalPodAutoscaler", "PodDisruptionBudget", "Namespace")  # (a name's objects, first first)
_KUBE_HINT = ("Deployment|StatefulSet|DaemonSet|CronJob|ConfigMap|Secret|Service|Ingress|Namespace|Pod|Job|Rollout|"
              "NodePool|ServiceAccount|PersistentVolumeClaim|HorizontalPodAutoscaler|HTTPRoute|deployment|statefulset|"
              "daemonset|cronjob|configmap|ingress|pod|namespace|(?:kube(?:config|ctl)? )?context")


def ref_kube_hint(before, after):
    """The kind a reply gives the backticked name between before and after ("the `x` Deployment", "Service `x`", "its
    `x` pods", "the `staging` context"): a Kubernetes kind, "context" (a kubeconfig's), or None."""
    m = re.match(rf"\s+({_KUBE_HINT})s?\b", after) or re.search(rf"\b({_KUBE_HINT})\s+$", before)
    if not m:
        return None
    word = m.group(1).split()[-1]
    return "context" if word == "context" else KUBE_KINDS.get(word.lower(), word)


def kube_pod_owners(name):
    """The workloads a pod's name may come from, likeliest first, as (kind or None, name): a CronJob's
    (<name>-<minutes since 1970>-<5>), a Deployment's (<name>-<ReplicaSet hash>-<5>), a Job's or a DaemonSet's
    (<name>-<5>), a StatefulSet's (<name>-<ordinal>). Kubernetes' suffixes have no vowels, nor 0, 1 or 3."""
    sfx = "[bcdfghjklmnpqrstvwxz2456789]"
    out = []
    if m := re.fullmatch(rf"(.+)-\d{{8,}}-{sfx}{{5}}", name):
        out.append(("CronJob", m.group(1)))
    if m := re.fullmatch(rf"(.+)-{sfx}{{6,10}}-{sfx}{{5}}", name):
        out.append(("Deployment", m.group(1)))
    if m := re.fullmatch(rf"(.+)-{sfx}{{5}}", name):
        out.append((None, m.group(1)))
    if m := re.fullmatch(r"(.+)-\d{1,4}", name):
        out.append(("StatefulSet", m.group(1)))
    return out


def ref_kubeconfig_touch(text):
    """Whether a tool call's input names a kubeconfig file (one Signal's reader may not read: it holds credentials)."""
    return bool(re.search(r"(?:^|[\s/'\"=~])\.kube(?:/|\b)|kubeconfig", text, re.I)) or \
        any(p in text for p in kubeconfig_paths())


HOVER_POOL = WorkerPool(1)  # (db_prefetch's)


def kube_page_for(resource):
    """The Kubernetes view's page for a kind kubectl calls resource (Pods if it has none)."""
    return next((p for _title, pages in KUBE_SECTIONS for p in pages if p["resource"] == resource), KUBE_PODS)


def pg_forget():
    """Read the servers, databases and schemas again next time (Try again)."""
    PG_LISTS.clear()
    PG_CACHE.clear()
    _FORGOTTEN[0] += 1  # (a lookup on its way began before this: not joined)


def db_prefetch(target):
    """Look for (table, column, hint) now, for the click that may follow (BaudeWindow.hover_link), on a worker of its
    own: only the last name the pointer went onto is looked for, and a click never waits behind it (it joins it, if
    it's for the same name)."""
    _PREFETCH[0] = target
    HOVER_POOL.submit(_prefetch)


def _prefetch():
    target, _PREFETCH[0] = _PREFETCH[0], None
    if target is not None and (*target, _FORGOTTEN[0]) not in _LOCATING:
        db_locate(*target)


_PREFETCH = [None]


def db_named(surface):
    """Where the live databases have a name written in a reply: a table or collection of that name, or (an
    entity's collection is e_<Entity>, a relation's r_<RELATION>) one with that prefix, or a database of
    that name. [(container, database, table or None)], or []."""
    index = db_names()
    for name in (surface, f"e_{surface}", f"r_{surface}"):
        if index.get(name):
            return index[name]
    return []


def _num(path):
    return lambda o, c: _ks(o, *path)
