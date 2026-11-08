#!/usr/bin/env python3
"""A stand-in language server for Referencer's tests: speaks LSP over stdio like pyright or
typescript-language-server, with answers worked out from the files by a few simple rules.

    fake_lsp.py python|typescript --stdio

- definition of obj.attr: obj's declared type (`obj: Class` in that file) or object literal (`const obj = {`)
  and the member declared in it; obj a module with a stub in FAKE_LSP_STDLIB (shutil.pyi): its function there.
- typeDefinition of self.attr where `self.attr = Class(...)`: the class.
- documentSymbol (Python): classes with their methods, class-level fields and self.attr assignments, as pyright
  lists them. workspace/symbol: `def`, `class` and `NAME =` lines named exactly so.
- typescript: the first opened file starts a "project load", reported as progress, during which definitions
  answer [] (as tsserver does).
Every message it gets, and its settings as the client answered, go to FAKE_LSP_LOG (JSON lines).
"""
import ast
import json
import os
import re
import sys
import threading
import time
import urllib.parse

KIND = sys.argv[1] if len(sys.argv) > 1 else "python"
LOG = os.environ.get("FAKE_LSP_LOG")
STDLIB = os.environ.get("FAKE_LSP_STDLIB", "")
lock = threading.Lock()
root, texts, state = None, {}, {"loading": False, "loaded": False}


def log(entry):
    if LOG:
        with open(LOG, "a") as f:
            f.write(json.dumps({"server": KIND, **entry}) + "\n")


def send(msg):
    data = json.dumps(msg).encode()
    with lock:
        sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
        sys.stdout.buffer.flush()


def read():
    size = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.partition(b":")
        if key.strip().lower() == b"content-length":
            size = int(value)
    return json.loads(sys.stdin.buffer.read(size))


def path_of(uri):
    return urllib.parse.unquote(urllib.parse.urlparse(uri).path)


def uri_of(path):
    return "file://" + urllib.parse.quote(path)


def lines_of(path):
    if path in texts:
        return texts[path].splitlines()
    try:
        with open(path) as f:
            return f.read().splitlines()
    except OSError:
        return []


def files():
    for d, subs, names in os.walk(root):
        subs[:] = [s for s in subs if not s.startswith(".") and s != "node_modules"]
        for n in names:
            if n.endswith((".py", ".ts", ".tsx")):
                yield os.path.join(d, n)


def loc(path, line, col=0, length=1):
    return {"uri": uri_of(path), "range": {"start": {"line": line, "character": col},
                                           "end": {"line": line, "character": col + length}}}


def member(path, start, name):
    """The line declaring name in the block that starts at line start (indented under it, or up to its })."""
    ls = lines_of(path)
    indent = len(ls[start]) - len(ls[start].lstrip())
    for i in range(start + 1, len(ls)):
        s = ls[i]
        if s.strip() and len(s) - len(s.lstrip()) <= indent and not s.strip().startswith(("}", ")")):
            break
        if m := re.match(rf"\s*(?:def\s+|async\s+def\s+|self\.)?({re.escape(name)})\s*[:=(]", s):
            return loc(path, i, m.start(1), len(name))
        if s.strip().startswith("}") and len(s) - len(s.lstrip()) == indent:
            break
    return None


def declared(name):
    """Where class/interface/const name is declared: (path, line index)."""
    for p in files():
        for i, s in enumerate(lines_of(p)):
            if re.match(rf"\s*(export\s+)?(class|interface)\s+{re.escape(name)}\b|(export\s+)?const\s+{re.escape(name)}\s*=", s):
                return p, i
    return None


def definition(path, line, col):
    ls = lines_of(path)
    if line >= len(ls):
        return []
    text = ls[line]
    m = next((m for m in re.finditer(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)", text) if m.start(2) <= col <= m.end(2)), None)
    if not m:
        return []
    obj, name = m.groups()
    if STDLIB and os.path.exists(stub := os.path.join(STDLIB, obj + ".pyi")):
        for i, s in enumerate(lines_of(stub)):
            if re.match(rf"def\s+{name}\b", s):
                return [loc(stub, i, 4, len(name))]
    whole = "\n".join(ls)
    if t := re.search(rf"\b{obj}\s*:\s*([A-Z]\w*)", whole):  # obj: Class
        if (d := declared(t.group(1))) and (found := member(*d, name)):
            return [found]
    if (d := declared(obj)) and (found := member(*d, name)):  # const obj = {...}
        return [found]
    return []


def type_definition(path, line, col):
    text = (lines_of(path) + [""])[line] if line < len(lines_of(path)) else ""
    if m := re.search(r"self\.\w+\s*=\s*([A-Z]\w*)\(", text):
        if d := declared(m.group(1)):
            i = d[1]
            return [loc(d[0], i, lines_of(d[0])[i].index(m.group(1)), len(m.group(1)))]
    return []


def symbols(path):
    if not path.endswith(".py"):
        return []
    try:
        tree = ast.parse("\n".join(lines_of(path)))
    except SyntaxError:
        return []

    def sym(name, kind, line, col, children=None):
        r = {"line": line - 1, "character": col}
        return {"name": name, "kind": kind, "range": {"start": r, "end": r}, "selectionRange": {"start": r, "end": r},
                "children": children or []}
    out = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            kids = []
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kids.append(sym(b.name, 6, b.lineno, b.col_offset + 4))
                    for a in ast.walk(b):
                        if isinstance(a, ast.Attribute) and isinstance(a.ctx, ast.Store) and \
                                isinstance(a.value, ast.Name) and a.value.id == "self":
                            kids.append(sym(a.attr, 13, a.lineno, a.col_offset + 5))
                elif isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name):
                    kids.append(sym(b.target.id, 13, b.lineno, b.col_offset))
            out.append(sym(node.name, 5, node.lineno, node.col_offset + 6, kids))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(sym(node.name, 12, node.lineno, node.col_offset + 4))
    return out


def workspace(query):
    out = []
    for p in files():
        for i, s in enumerate(lines_of(p)):
            if m := re.match(rf"\s*(?:def|class|function|const)\s+({re.escape(query)})\b|({re.escape(query)})\s*=", s):
                out.append({"name": query, "kind": 12, "location": loc(p, i, m.start(1 if m.group(1) else 2))})
    return out


def finish_loading():
    time.sleep(1.0)
    state["loading"], state["loaded"] = False, True
    send({"jsonrpc": "2.0", "method": "$/progress", "params": {"token": "load", "value": {"kind": "end"}}})


def main():
    global root
    while (msg := read()) is not None:
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        log({"method": method or "(answer)", "params": params if method not in ("textDocument/didOpen",) else
             {"uri": params.get("textDocument", {}).get("uri")}, "result": msg.get("result")})
        if method is None:
            continue
        result = None
        if method == "initialize":
            root = path_of(params["rootUri"])
            result = {"capabilities": {"definitionProvider": True, "typeDefinitionProvider": True,
                                       "documentSymbolProvider": True, "workspaceSymbolProvider": True}}
        elif method == "initialized" and KIND == "python":  # pyright asks for its settings
            send({"jsonrpc": "2.0", "id": "cfg-1", "method": "workspace/configuration",
                  "params": {"items": [{"scopeUri": uri_of(root), "section": "python"}]}})
        elif method == "textDocument/didOpen":
            doc = params["textDocument"]
            texts[path_of(doc["uri"])] = doc["text"]
            if KIND == "typescript" and not state["loading"] and not state["loaded"]:
                state["loading"] = True
                send({"jsonrpc": "2.0", "id": "progress-1", "method": "window/workDoneProgress/create",
                      "params": {"token": "load"}})
                send({"jsonrpc": "2.0", "method": "$/progress",
                      "params": {"token": "load", "value": {"kind": "begin", "title": "Initializing JS/TS"}}})
                threading.Thread(target=finish_loading, daemon=True).start()
        elif method in ("textDocument/definition", "textDocument/typeDefinition"):
            pos = params["position"]
            path = path_of(params["textDocument"]["uri"])
            if state["loading"]:
                result = []
            elif method == "textDocument/definition":
                result = definition(path, pos["line"], pos["character"])
            else:
                result = type_definition(path, pos["line"], pos["character"])
        elif method == "textDocument/documentSymbol":
            result = symbols(path_of(params["textDocument"]["uri"]))
        elif method == "workspace/symbol":
            result = workspace(params.get("query", ""))
        elif method == "exit":
            return
        if mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": result})


if __name__ == "__main__":
    main()
