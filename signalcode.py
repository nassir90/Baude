"""signalcode — Signal through the language servers: pyright and typescript-language-server fetched and run
(LspServer), a code name's definition and type (RefCode, ref_code_sites, ref_py_scope), and Signal's own use of them
(SignalCode).
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

from lookup import Names, Ref

from refkinds import _REF_METHODS, ref_command  # noqa: E402,F401
from pickers import _data_home  # noqa: E402,F401

from signalrefs import REF_CODE_PATHS, ref_def_role, ref_is_test, ref_route_score
from toolcards import _first_line


# ------------------------------------------------ signal: language servers --
# A search finds where a name is defined, not which definition is meant: `previous.job_id` is a field of whatever
# class `previous` is, and only a type checker knows that. So code names also go to a language server: pyright for
# Python (one per package, with that package's .venv) and typescript-language-server for TypeScript and JavaScript
# (one per TypeScript install, so a package gets the version it builds with). Baude fetches them once, with your
# OK, at pinned versions into its own data folder, and starts one when a code name first needs it. Signal only
# asks them questions (a definition, a type, a file's symbols); it never asks them to edit, format or fix anything.

REF_LSP_PACKAGES = (("pyright", "1.1.414"), ("typescript-language-server", "5.3.0"), ("typescript", "5.9.3"))
REF_LSP_LANG = {".py": "python", ".pyi": "python", ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
                ".tsx": "typescriptreact", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
                ".jsx": "javascriptreact"}
# the parts of each server Baude starts, under lsp_dir()/node_modules, and the command if one is on PATH instead
_REF_LSP_SERVERS = (("python", "BAUDE_LSP_PYTHON", "pyright", "langserver.index.js", "pyright-langserver"),
                    ("typescript", "BAUDE_LSP_TYPESCRIPT", "typescript-language-server", "lib/cli.mjs",
                     "typescript-language-server"))


def lsp_dir():
    """Where Baude keeps the language servers it fetched (an npm folder of its own)."""
    return os.path.join(_data_home(), "baude", "lsp")


def lsp_node():
    """Node.js, which runs both servers: on PATH, else the newest one nvm installed (a launcher's PATH has no nvm)."""
    if node := shutil.which("node"):
        return node
    import glob
    found = glob.glob(os.path.expanduser("~/.nvm/versions/node/v*/bin/node"))
    return max(found, key=lambda p: [int(x) for x in re.findall(r"\d+", p.rsplit("/node/v", 1)[-1])]) if found else None


def lsp_commands():
    """How to start each language server that's there: {"python": argv, "typescript": argv}. Baude's own (fetched
    at the pinned version) come first, then ones on PATH; BAUDE_LSP_PYTHON and BAUDE_LSP_TYPESCRIPT (a command
    line each) override both, as the tests do."""
    import shlex
    out, node, pins = {}, lsp_node(), dict(REF_LSP_PACKAGES)
    for lang, env, package, script, exe in _REF_LSP_SERVERS:
        mine = os.path.join(lsp_dir(), "node_modules", package)
        try:
            with open(os.path.join(mine, "package.json")) as f:
                version = json.load(f).get("version")
        except (OSError, ValueError):
            version = None
        if os.environ.get(env):
            out[lang] = shlex.split(os.environ[env])
        elif node and version == pins[package]:
            out[lang] = [node, os.path.join(mine, script), "--stdio"]
        elif shutil.which(exe) and version is None:  # (an older fetch of Baude's own is updated instead)
            out[lang] = [shutil.which(exe), "--stdio"]
    return out


def lsp_fetch():
    """Install the pinned servers into lsp_dir() with npm (a minute or so: run it in a thread of its own).
    Returns (ok, what happened)."""
    node = lsp_node()
    npm = os.path.join(os.path.dirname(node), "npm") if node else None
    if not npm or not os.path.exists(npm):
        return False, "they need Node.js (node and npm), which isn't installed"
    folder = lsp_dir()
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(os.path.join(folder, "package.json")):
        with open(os.path.join(folder, "package.json"), "w") as f:
            json.dump({"private": True, "description": "Language servers for Baude's Signal"}, f)
    env = {**os.environ, "PATH": os.path.dirname(node) + os.pathsep + os.environ.get("PATH", "")}
    try:
        p = subprocess.run([npm, "install", "--no-audit", "--no-fund", "--ignore-scripts", "--save-exact",
                            *(f"{name}@{version}" for name, version in REF_LSP_PACKAGES)],
                           cwd=folder, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"npm failed ({e})"
    if p.returncode:
        lines = (p.stderr or p.stdout or "").strip().splitlines()
        return False, f"npm failed: {_first_line(lines[-1] if lines else str(p.returncode), 200)}"
    return True, "fetched " + ", ".join(f"{name} {version}" for name, version in REF_LSP_PACKAGES)


def lsp_uri(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


def lsp_path(uri):
    return urllib.parse.unquote(urllib.parse.urlparse(uri).path) if isinstance(uri, str) and uri.startswith("file:") \
        else None


def lsp_places(result):
    """(absolute path, 1-based line) of each Location or LocationLink in a definition's answer."""
    out = []
    for x in result if isinstance(result, list) else [result] if isinstance(result, dict) else []:
        rng = x.get("targetSelectionRange") or x.get("targetRange") or x.get("range") or {}
        path = lsp_path(x.get("targetUri") or x.get("uri"))
        if path and isinstance(rng.get("start"), dict):
            out.append((path, rng["start"].get("line", 0) + 1))
    return out


def ref_inside(path, folder):
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


class LspServer:
    """One language server, over its stdin and stdout (JSON-RPC with Content-Length headers). A request waits for its
    answer; a reader thread answers the server's own requests (its settings) and follows its progress reports, which
    is how a TypeScript project says it has loaded."""

    def __init__(self, argv, root, kind, settings=None, options=None):
        self.root, self.kind, self.settings = root, kind, settings or {}
        self.lock, self.next_id, self.waiting = threading.Lock(), 0, {}
        self.opening, self.users = threading.Lock(), set()  # (several Signals' workers share it: RefCode)
        self.progress, self.loads = set(), 0  # work going on now; loads finished
        self.opened, self.dead, self.used = {}, False, time.monotonic()
        self.proc = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        threading.Thread(target=self._read, daemon=True, name=f"baude-lsp-{kind}").start()
        uri = lsp_uri(root)
        caps = {"window": {"workDoneProgress": True},
                "workspace": {"configuration": True, "workspaceFolders": True, "symbol": {}},
                "textDocument": {"synchronization": {}, "definition": {}, "typeDefinition": {},
                                 "documentSymbol": {"hierarchicalDocumentSymbolSupport": True}}}
        if self.request("initialize", {"processId": os.getpid(), "rootUri": uri, "capabilities": caps,
                                       "workspaceFolders": [{"uri": uri, "name": os.path.basename(root)}],
                                       "initializationOptions": options or {}}, timeout=60) is None:
            self.close()
            raise OSError("it didn't answer")
        self.notify("initialized", {})
        if self.settings:
            self.notify("workspace/didChangeConfiguration", {"settings": self.settings})

    def _send(self, msg):
        data = json.dumps(msg).encode()
        try:
            with self.lock:
                self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
                self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            self.dead = True
            return False

    def _read(self):
        out = self.proc.stdout
        try:
            while True:
                size = None
                while (line := out.readline()) not in (b"\r\n", b"\n"):
                    if not line:
                        return
                    key, _, value = line.partition(b":")
                    if key.strip().lower() == b"content-length":
                        size = int(value)
                msg = json.loads(out.read(size or 0))
                method, mid = msg.get("method"), msg.get("id")
                if method and mid is not None:  # the server asks: for its settings; anything else gets null
                    items = (msg.get("params") or {}).get("items") or []
                    result = [self._section(i.get("section")) for i in items] \
                        if method == "workspace/configuration" else None
                    self._send({"jsonrpc": "2.0", "id": mid, "result": result})
                elif method == "$/progress":
                    p = msg.get("params") or {}
                    kind = (p.get("value") or {}).get("kind")
                    if kind == "begin":
                        self.progress.add(p.get("token"))
                    elif kind == "end" and p.get("token") in self.progress:
                        self.progress.discard(p.get("token"))
                        self.loads += 1
                elif mid is not None and (w := self.waiting.pop(mid, None)):
                    w[1] = msg
                    w[0].set()
        except (OSError, ValueError, TypeError):
            pass
        finally:
            self.dead = True
            for w in list(self.waiting.values()):
                w[0].set()

    def _section(self, section):
        node = self.settings
        for key in section.split(".") if section else ():
            node = node.get(key) if isinstance(node, dict) else None
        return node

    def request(self, method, params, timeout=20):
        """The answer's result; None if it failed, timed out or the server has gone."""
        if self.dead:
            return None
        with self.lock:
            self.next_id += 1
            mid = self.next_id
        w = self.waiting[mid] = [threading.Event(), None]
        self.used = time.monotonic()
        if not self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) \
                or not w[0].wait(timeout):
            self.waiting.pop(mid, None)
            self.notify("$/cancelRequest", {"id": mid})
            return None
        return None if w[1] is None or "error" in w[1] else w[1].get("result")

    def notify(self, method, params):
        if not self.dead:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def open(self, path, lang):
        """Tell the server about a file (once, and again when it has changed). False if it can't be read."""
        with self.opening:  # (two Signals may ask about the same file at once)
            return self._open(path, lang)

    def _open(self, path, lang):
        try:
            mtime = os.stat(path).st_mtime_ns
            if self.opened.get(path) == mtime:
                return True
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(Names.BLOB_LIMIT)
        except OSError:
            return False
        if path in self.opened:
            self.notify("textDocument/didClose", {"textDocument": {"uri": lsp_uri(path)}})
            del self.opened[path]
        while len(self.opened) >= 40:  # (the server keeps open files in memory)
            old = next(iter(self.opened))
            self.notify("textDocument/didClose", {"textDocument": {"uri": lsp_uri(old)}})
            del self.opened[old]
        self.notify("textDocument/didOpen", {"textDocument": {"uri": lsp_uri(path), "languageId": lang,
                                                              "version": 1, "text": text}})
        self.opened[path] = mtime
        return True

    def settle(self):
        """After an empty answer from a TypeScript server: wait for the project it may be loading (it reports that
        as progress, starting a moment after a file opens), then True to ask again. False: nothing was loading."""
        if self.kind != "typescript":
            return False
        end = time.monotonic() + (8 if not self.loads else 1.5)
        while not self.progress and not self.dead and time.monotonic() < end:
            time.sleep(0.1)
        if not self.progress:
            return False
        end = time.monotonic() + 60
        while self.progress and not self.dead and time.monotonic() < end:
            time.sleep(0.1)
        return not self.dead

    def close(self):
        self.dead = True
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


class RefCode:
    """A Signal's language servers (used on its worker thread): which one answers for a file, started the first time
    one is needed. They're shared by every Signal (servers, by package: a chat in a package and a chat in another
    folder of it ask the same one), each stopped when no Signal that used it is running (stop), or left idle; the
    least recently used is stopped when more than MAX run."""

    MAX, IDLE = 4, 900
    servers, failed, lock = {}, {}, threading.Lock()  # (every Signal's: key -> LspServer, key -> why it failed)

    def __init__(self, ref):
        self.ref, self.cmds, self.checked, self.mine = ref, {}, 0, set()  # (mine: the keys of the servers it used)

    def ready(self):
        """Whether any server is installed (looked at again every half minute)."""
        if time.monotonic() - self.checked > 30:
            self.cmds, self.checked = lsp_commands(), time.monotonic()
        return bool(self.cmds)

    def _spec(self, path):
        """(key, root, settings, init options, what it is, language id) of the server for a file; None if none."""
        lang = REF_LSP_LANG.get(os.path.splitext(path)[1].lower())
        kind = "python" if lang == "python" else "typescript" if lang else None
        if kind not in self.cmds:
            return None
        full = os.path.join(self.ref.root, path)
        return (*(self._python(full) if kind == "python" else self._typescript(full)), lang)

    def key(self, path):
        return (spec := self._spec(path)) and spec[0]

    def server(self, path):
        """(server, language id) for a file in the repository (a relative path); (None, None) if none can answer."""
        if (spec := self._spec(path)) is None:
            return None, None
        key, root, settings, options, what, lang = spec
        kind = key[0]
        with RefCode.lock:  # (another Signal's worker may be starting the same one)
            srv = self.servers.get(key)
            if srv is not None and srv.dead:
                del self.servers[key]
                self.failed[key] = "it stopped"
                self.ref._tell(entry=f"{what} stopped")
                srv = None
            if srv is None:
                if key in self.failed:
                    return None, None
                self._trim()
                self.ref._tell(activity=f"Starting {what}…")
                t0 = time.monotonic()
                try:
                    srv = LspServer(self.cmds[kind], root, kind, settings, options)
                except OSError as e:
                    self.failed[key] = str(e)
                    self.ref._tell(entry=f"Couldn't start {what}: {e}")
                    return None, None
                srv.what = what
                self.servers[key] = srv
                self.ref._tell(entry=f"Started {what} in {time.monotonic() - t0:.1f} s")
            elif self not in srv.users:
                self.ref._tell(entry=f"Using {what}, already running")
            srv.users.add(self)
            self.mine.add(key)
        srv.used = time.monotonic()
        return srv, lang

    def _python(self, full):
        """pyright for the package a file is in (the nearest folder with a pyproject.toml, setup.py or setup.cfg),
        with that package's virtual environment, else the nearest one above it."""
        root, rel = self.ref.root, lambda d: os.path.relpath(d, self.ref.root)  # noqa: E731
        d, pkg, python = os.path.dirname(full), None, None
        while ref_inside(d, root):
            if pkg is None and any(os.path.exists(os.path.join(d, m)) for m in ("pyproject.toml", "setup.py", "setup.cfg")):
                pkg = d
            if pkg is not None and (python := next((p for v in (".venv", "venv") if os.path.exists(
                    p := os.path.join(d, v, "bin", "python"))), None)):
                break
            if d == root:
                break
            d = os.path.dirname(d)
        pkg = pkg or root
        analysis = {"typeCheckingMode": "off", "diagnosticMode": "openFilesOnly", "autoSearchPaths": True,
                    "useLibraryCodeForTypes": True}
        settings = {"python": {"analysis": analysis, **({"pythonPath": python} if python else {})}}
        venv = f"with {rel(os.path.dirname(os.path.dirname(python)))}" if python else "without a virtual environment"
        return ("python", pkg, python), pkg, settings, {}, f"pyright for {rel(pkg)} ({venv})"

    def _typescript(self, full):
        """typescript-language-server with the TypeScript the file's package installed (the nearest
        node_modules/typescript that has a tsserver: TypeScript 7 has none), else the one Baude fetched."""
        root, d, tsserver = self.ref.root, os.path.dirname(full), None
        while ref_inside(d, root):
            if os.path.exists(p := os.path.join(d, "node_modules", "typescript", "lib", "tsserver.js")):
                tsserver = p
                break
            if d == root:
                break
            d = os.path.dirname(d)
        if tsserver is None:
            d, own = root, os.path.join(lsp_dir(), "node_modules", "typescript", "lib", "tsserver.js")
            tsserver = own if os.path.exists(own) else None
        try:
            with open(os.path.join(os.path.dirname(os.path.dirname(tsserver)), "package.json")) as f:
                version = f" {json.load(f).get('version', '')}"
        except (OSError, ValueError, TypeError):
            version = ""
        options = {"disableAutomaticTypingAcquisition": True, "tsserver": {"logVerbosity": "off"}}
        if tsserver:
            options["tsserver"]["path"] = tsserver
        where = os.path.relpath(d, root)
        return ("typescript", tsserver), d, None, options, f"typescript-language-server for {where} (TypeScript{version})"

    def _trim(self):
        now = time.monotonic()  # (RefCode.lock held)
        for key, srv in list(self.servers.items()):
            if srv.dead or now - srv.used > self.IDLE:
                self.servers.pop(key).close()
        while len(self.servers) >= self.MAX:
            self.servers.pop(min(self.servers, key=lambda k: self.servers[k].used)).close()

    def _ask(self, path, method, params):
        """A request about a file (opened first); a TypeScript project still loading is waited for."""
        srv, lang = self.server(path)
        full = os.path.join(self.ref.root, path)
        if srv is None or not srv.open(full, lang):
            return None, None
        params = {"textDocument": {"uri": lsp_uri(full)}, **params}
        res = srv.request(method, params, timeout=40 if not srv.loads else 15)
        if not res and srv.settle():
            res = srv.request(method, params, timeout=15)
        return srv, res

    def definitions(self, path, line, character, method="textDocument/definition"):
        """Where the server says the name at a position is defined (or, with textDocument/typeDefinition, its type):
        [(absolute path, 1-based line)]. character counts UTF-16 units, as LSP does (lsp_col)."""
        srv, res = self._ask(path, method, {"position": {"line": line - 1, "character": character}})
        return lsp_places(res)

    def symbols(self, path):
        """A file's symbols: [(name, kind, 1-based line, character, the names of the symbols around it)]."""
        out = []

        def walk(items, parents):
            for s in items if isinstance(items, list) else []:
                if not isinstance(s, dict) or "name" not in s:
                    continue
                at = (s.get("selectionRange") or s.get("range") or (s.get("location") or {}).get("range") or {}) \
                    .get("start") or {}
                around = [s["containerName"]] if "location" in s and s.get("containerName") else parents
                out.append((s["name"], s.get("kind"), at.get("line", 0) + 1, at.get("character", 0), around))
                walk(s.get("children"), parents + [s["name"]])
        walk(self._ask(path, "textDocument/documentSymbol", {})[1], [])
        return out

    def workspace(self, path, name):
        """The symbols named exactly name in the project of a file (its package): [(abs path, line, kind, container)]."""
        srv, lang = self.server(path)
        if srv is None or not srv.open(os.path.join(self.ref.root, path), lang):  # (a TypeScript project loads)
            return []
        res = srv.request("workspace/symbol", {"query": name}, timeout=40 if not srv.loads else 15)
        if not res and srv.settle():
            res = srv.request("workspace/symbol", {"query": name}, timeout=15)
        out = []
        for s in res if isinstance(res, list) else []:
            loc = s.get("location") or {}
            path_, start = lsp_path(loc.get("uri")), (loc.get("range") or {}).get("start")
            if s.get("name") == name and path_ and start:
                out.append((path_, start.get("line", 0) + 1, s.get("kind"), s.get("containerName") or ""))
        return out

    def running(self):
        """This Signal's servers that are running (another's it used too)."""
        return [srv.what for key, srv in list(self.servers.items()) if key in self.mine and not srv.dead]

    def stop(self):
        """This Signal is done with its servers: each stops if no other Signal that used it is running; those that
        failed may be tried again."""
        with RefCode.lock:
            for key in self.mine:
                srv = self.servers.get(key)
                if srv is not None:
                    srv.users.discard(self)
                    if not srv.users:
                        self.servers.pop(key, None)
                        try:
                            srv.close()
                        except OSError:
                            pass
                self.failed.pop(key, None)
            self.mine.clear()


def lsp_col(text, i):
    """A character index in a line as LSP counts it: UTF-16 code units."""
    return len(text[:i].encode("utf-16-le")) // 2


def ref_code_index(text, needle):
    """Where needle is used as code in a line: not in a comment or a string, not part of a longer name. None if not."""
    if text.lstrip().startswith(("#", "//", "*", "/*", '"""', "'''")):
        return None
    for m in re.finditer(re.escape(needle), text):
        i, before, after = m.start(), text[:m.start()], text[m.end():m.end() + 1]
        if (before[-1:].isalnum() or before[-1:] in ("_", "$")) and not needle.startswith("."):
            continue
        if after.isalnum() or after in ("_", "$"):
            continue
        if before.count('"') % 2 or before.count("'") % 2 or before.count("`") % 2 or re.search(r"(^|\s)(#|//)", before):
            continue
        return i
    return None


def ref_code_sites(hits, needle, touched=(), limit=4):
    """The places code uses needle, for a language server to look at: [(path, line, text, index)], one per file,
    the session's own files first and tests last."""
    out, seen = [], set()
    for path, n, text in sorted(hits, key=lambda h: (h[0] not in touched, ref_is_test(h[0]), h[0], h[1])):
        if path in seen or os.path.splitext(path)[1].lower() not in REF_LSP_LANG:
            continue
        if (i := ref_code_index(text, needle)) is not None:
            seen.add(path)
            out.append((path, n, text, i))
            if len(out) >= limit:
                break
    return out


_REF_PY_DEFS = {}  # the last few texts ref_py_scope parsed: their classes and functions (None: it doesn't parse)
_REF_PY_LOCK = threading.Lock()  # (every Signal's worker uses it: one's taking a text out as another puts one in)


def ref_py_scope(text, line):
    """Where a line of Python is, by Python's own parser (no server needed): what it's directly in ("class",
    "function" or "module"; None if it doesn't parse) and the classes around it, outermost first. A text is parsed
    once (_REF_PY_DEFS): parsed again for each of a name's places (60 of them, say), baude.py's parse held Python's
    lock a tenth of a second each time, and the window waited."""
    import ast
    with _REF_PY_LOCK:
        known = text in _REF_PY_DEFS
        defs = _REF_PY_DEFS.get(text)
    if not known:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            defs = None
        else:
            defs = [(n.lineno, n.end_lineno or n.lineno, isinstance(n, ast.ClassDef), n.name) for n in ast.walk(tree)
                    if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
        with _REF_PY_LOCK:
            _REF_PY_DEFS[text] = defs
            while len(_REF_PY_DEFS) > 8:
                _REF_PY_DEFS.pop(next(iter(_REF_PY_DEFS)))
    if defs is None:
        return None, []
    around = sorted((d for d in defs if d[0] < line <= d[1]), key=lambda d: d[0])
    scope = "module" if not around else "class" if around[-1][2] else "function"
    return scope, [name for _first, _last, is_class, name in around if is_class]


def ref_same_name(a, b):
    """Whether an object's name and a class's are the same name (dlg and Dlg, shell_api and ShellApi)."""
    return a.replace("_", "").lower() == b.replace("_", "").lower()


class SignalCode:
    """Signal through the language servers (RefCode): the names a search can't settle."""

    # ---- through the language servers (RefCode): the names a search can't settle
    def _symbol_lsp(self, surface, name, parts, st, touched, code):
        """A dotted name, through the language servers; None if they don't know it. First the definition they give
        where code uses the name (previous.job_id: whatever previous is there); then, for Class.member.member, each
        member in its class, going on through its type (ShellRelay.tools.invoke: tools is a ToolConsumer). A name
        defined outside the repository (the standard library, a dependency) is external."""
        used = self._grep(["-F", "-e", name], REF_CODE_PATHS, limit=3000)
        found, outside = self._lsp_definitions(code, ref_code_sites(used, name, touched), name, parts[-1], st)
        how = "where the code uses it"
        if not found and parts[0][:1].isupper():
            found, how = self._lsp_chain(code, parts, st), f"through the members of {parts[0]}"
        return self._lsp_ref(surface, name, parts[-1], found, outside, used, how)

    def _lsp_bare(self, surface, name, words, st, touched, code):
        """A name no pattern found a definition of: the one a language server gives where code uses it (a field
        assigned in a class body, a name from a dependency), else a symbol of that name in the packages using it."""
        sites = ref_code_sites(words, name, touched)
        found, outside = self._lsp_definitions(code, sites, name, name, st)
        how = "where the code uses it"
        if not found and not outside:  # (a cold package's symbols take a while: only the first package's)
            for path in [s[0] for s in sites][:1]:
                for p, n, _kind, _container in code.workspace(path, name):
                    if (place := self._lsp_place(p, n, st)) and not place[2] and self._lsp_role(*place[:2], name) \
                            and place[:2] not in found:
                        found.append(place[:2])
            how = "among the symbols of the packages that use it"
        return self._lsp_ref(surface, name, name, found, outside, words, how)

    def _lsp_definitions(self, code, sites, needle, last, st):
        """The definitions the servers give for last, the end of needle, at the first site they answer for: the
        repository's ([(path, line)]), or those outside it ([(path, line, what it is)]). Sites are tried in turn,
        starting at most two servers that aren't running yet (a package's server takes seconds to start)."""
        found, outside, new = [], [], 0
        for path, n, text, i in sites:
            if (key := code.key(path)) is None or key not in code.servers and new >= 2:
                continue
            new += key not in code.servers
            self._tell(activity=f"Asking a language server about {needle} ({path}:{n})…")
            for p, line in code.definitions(path, n, lsp_col(text, i + len(needle) - len(last))):
                if (place := self._lsp_place(p, line, st)) is None:
                    continue
                if place[2]:
                    outside.append(place)
                elif self._lsp_role(place[0], place[1], last) and place[:2] not in found:
                    found.append(place[:2])
            if found or outside:
                break
        return found, outside

    def _lsp_chain(self, code, parts, st):
        """Class.member.member: the class's definition, then each member in it, and the type of each member but the
        last (textDocument/typeDefinition: its class) to look in next. [(path, line)] of the last member."""
        heads = [(p, n) for p, n, t in self._grep(["-w", "-F", "-e", parts[0]], REF_CODE_PATHS, limit=5000)
                 if ref_def_role(p, t, parts[0]) in ("class", "type")]
        out = []
        for path, _line in sorted(heads, key=lambda h: ref_is_test(h[0]))[:3]:
            cls = parts[0]
            for i, part in enumerate(parts[1:], 1):
                member = next(((n, ch) for s, _k, n, ch, around in code.symbols(path)
                               if s == part and around and around[-1] == cls), None)
                if member is None:
                    break
                if i == len(parts) - 1:
                    if self._lsp_role(path, member[0], part) and (path, member[0]) not in out:
                        out.append((path, member[0]))
                    break
                types = [t for p, n in code.definitions(path, *member, method="textDocument/typeDefinition")
                         if (t := self._lsp_place(p, n, st)) and not t[2]]
                if not types:
                    break
                path, line = types[0][:2]
                cls = next((s for s, _k, n, _c, _a in code.symbols(path) if n == line), None)
                if cls is None:
                    break
        return out

    def _lsp_place(self, path, line, st):
        """Where a definition a language server gave is: (path relative to the repository, line, None), or for one
        outside it, (its path, line, what it is). A copy of one of the repository's own packages, installed in a
        virtual environment, maps back to the repository's file. None: nowhere worth a link."""
        real = os.path.realpath(path)
        rel = os.path.relpath(real, self.root) if ref_inside(real, self.root) else None
        if rel is not None and not re.search(r"(^|/)(site-packages|node_modules)/", rel):
            return rel, line, None
        if m := re.search(r"/site-packages/(.+)$", real):
            module = m.group(1)
            mine = sorted((f for f in self._files(st)[0] if f.endswith("/" + module) and "site-packages/" not in f), key=len)
            if mine:  # an installed copy of this repository's own package: the line with the same text in the original
                want = (self._lines_of(real)[line - 1:line] or [""])[0].strip()
                hits = [i for i, s in enumerate(self._text(mine[0]).splitlines(), 1) if want and s.strip() == want]
                return (mine[0], min(hits, key=lambda i: abs(i - line)), None) if hits else None
            return real, line, f"in the Python package {re.split(r'[/.-]', module)[0]}"
        if re.search(r"/typeshed[^/]*/stdlib/|/lib/python3[\d.]*/(?!site-packages/)", real):
            return real, line, "in the Python standard library"
        if m := re.search(r"/typeshed[^/]*/stubs/([^/]+)/", real):
            return real, line, f"in the Python package {m.group(1).removeprefix('types-')} (its type stubs)"
        if m := re.search(r"/node_modules/((?:@[^/]+/)?[^/]+)/", real):
            if m.group(1) in self._npm_names(st):
                return None  # this repository's own package, as built into node_modules: not its source
            return real, line, "in TypeScript's own types" if m.group(1) == "typescript" else \
                f"in the npm package {m.group(1)}"
        return real, line, "outside the repository"

    def _lines_of(self, full):
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                return f.read(self.BLOB_LIMIT).splitlines()
        except OSError:
            return []

    def _npm_names(self, st):
        """The names of the repository's own npm packages (each package.json's "name")."""
        def build():
            names = set()
            for f in self._files(st)[0]:
                if os.path.basename(f) == "package.json" and "node_modules/" not in f:
                    try:
                        names.add(json.loads(self._text(f)).get("name"))
                    except (ValueError, AttributeError):
                        pass
            return names - {None}
        return self._index("npm_names", st, build)

    def _lsp_role(self, path, line, name):
        """What a definition a language server gave is (a field, a method, a class attribute...), or None if it isn't
        worth a link: a local variable or a parameter, or a line without the name."""
        text = (self._text(path).splitlines()[line - 1:line] or [""])[0]
        if not re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text):
            return None
        if role := self._def_role(path, line, text, name):
            return role
        if path.endswith((".py", ".pyi")):
            return {"class": "attribute", "module": "variable"}.get(ref_py_scope(self._text(path), line)[0])
        return None

    def _lsp_ref(self, surface, name, last, found, outside, used, how):
        """The Ref for what the language servers found (None: nothing): the definitions, then where it's used."""
        if found:
            locs = [{"role": self._lsp_role(p, n, last) or "definition", "path": p, "line": n, "note": "",
                     "resolver": "lsp:pyright" if p.endswith((".py", ".pyi")) else "lsp:typescript"} for p, n in found[:12]]
            locs += [{"role": "used", "path": p, "line": n, "resolver": "grep", "note": ""}
                     for p, n, _t in used[:8] if (p, n) not in found]
            if len(found) == 1:
                ref = Ref(surface, "symbol", "resolved", f"{found[0][0]}::{name}", locations=locs)
            else:
                ref = Ref(surface, "symbol", "ambiguous", name, locations=locs,
                          reason=f"{len(found)} definitions, by a language server {how}")
            ref.data["lsp"] = how
            return ref
        if outside:
            path, line, what = outside[0]
            return Ref(surface, "external", "resolved", name, data={"note": f"Defined {what}", "lsp": how},
                       locations=[{"role": "definition", "path": path, "line": line, "note": "",
                                   "resolver": "lsp:pyright" if path.endswith((".py", ".pyi")) else "lsp:typescript"}])
        return None

    def _route(self, surface, st):
        m = re.fullmatch(rf"(?:({_REF_METHODS})\s+)?(…|\.\.\.)?(/\S*?)(…|\.\.\.)?", surface)
        if not m:
            return Ref(surface, "route", "not_found", reason="not a route")
        method, tail, target = m.group(1), bool(m.group(2)), m.group(3).split("?")[0]
        prefix_only = target.endswith("/*") or bool(m.group(4)) and not tail  # (/v1/private/jobs/…: what's under it)
        target = (target[:-2] if target.endswith("/*") else target).rstrip("/") or "/"
        idx = self._index("routes", st, lambda: self._build_routes(st))
        handlers = [r for r in idx["handlers"] if not (method and r["methods"] and method not in r["methods"])]

        def best_of(score):  # (score, handler): only the most specific count; catch-alls never do
            found = []
            for r in handlers:
                scores = [x for p in r["paths"] if (x := score(p)) is not None]
                if scores:  # a handler for exactly this method beats one for any method
                    found.append((max(scores), bool(method) and method in r["methods"], r))
            top = max(((f[0], f[1]) for f in found), default=None)
            return [(r, p) for x, exact, r in found if (x, exact) == top and x >= 0
                    for p in sorted(r["paths"])[:1]], top

        written = target.strip("/").split("/")
        literal = sum(1 for g in written if not re.fullmatch(r"\{[^}]*\}|<[^>]*>|:\w+|\*", g))
        if written and re.fullmatch(r"\{[^}]*\}|<[^>]*>|:\w+", written[0]):
            tail = True  # (/{job}/edits: under a prefix the reply named before; /{job} alone can't be placed)

        def suffix(p):  # the end of a route, as replies often write it: /conversations/{id}/browser/{call_id}, …/progress
            t = p.strip("/").split("/")
            if len(written) < (1 if tail else 2) or len(t) <= len(written) or not literal:
                return None
            x = ref_route_score("/".join(t[-len(written):]), target)
            return x if x == literal else None  # every name written must match, not just parameters

        def under(p):  # a prefix (/v1/dlg, /v1/private/gen/*): every route below it
            return p.count("/") if p.startswith(target.rstrip("/") + "/") else None
        hits, top = ([], None) if prefix_only or tail else best_of(lambda p: ref_route_score(p, target))
        how = "handler"
        if not hits and not prefix_only and (hits := best_of(suffix)[0]):
            how = "ends the route"
        if tail and not hits:
            return Ref(surface, "route", "not_found", reason="no FastAPI route in this folder ends with it")
        if not hits and (hits := [(r, p) for r in handlers for p in sorted(r["paths"]) if under(p) is not None][:30]):
            how = "under this prefix"
        locs = []
        for r, full in hits:
            verbs = "/".join(sorted(r["methods"])) or "ANY"
            note = f"{verbs} {r['fn']}".strip() + ("" if how == "handler" else f": {full}")
            locs.append({"role": "handler", "path": r["path"], "line": r["line"], "resolver": "fastapi", "note": note})
        best = top
        for s in idx["specs"]:
            if (not method or method in s["methods"]) and ref_route_score(s["route"], target) is not None:
                locs.append({"role": "spec", "path": s["path"], "line": s["line"], "resolver": "openapi",
                             "note": f"{s['route']}"})
        for e in idx["edges"]:
            if target == e["prefix"] or (not e["exact"] and len(e["prefix"]) > 1 and target.startswith(e["prefix"])):
                locs.append({"role": "edge", "path": e["path"], "line": e["line"], "resolver": "nginx",
                             "note": f"location {'= ' if e['exact'] else ''}{e['prefix']}"})
        locs = locs[:30]
        if any(loc["role"] != "edge" for loc in locs):
            return Ref(surface, "route", "resolved", f"{method + ' ' if method else ''}{target}", locations=locs)
        catchall = " (only catch-all routes match it)" if best else ""
        return Ref(surface, "route", "not_found", locations=locs,
                   reason=f"no FastAPI route or OpenAPI path in this folder matches it{catchall}")

    def _cli(self, surface, st, meta):
        """A command or a flag: the module or script a command runs (python -m pkg.mod, node scripts/shot.mjs), the
        code that defines a flag (add_argument("--from"), click / typer options, argv checks)."""
        flag = surface if re.fullmatch(r"--[A-Za-z][\w-]*", surface) else None
        how = None if flag else ref_command(surface)
        program = None  # (mytool --check: the flag as mytool defines it, if this folder has it)
        if how and how[0] == "flag":
            flag, program = how[1], how[2] if len(how) > 2 else None
        if flag:
            fx = re.escape(flag)  # (a list that passes it to another program is a use, not its definition)
            defining = re.compile(r"add_argument|add_option|\.option\(|\b(?:Option|Argument)\(|(?:sys|process)\.argv|"
                                  rf"\bargv\.includes\(|getopt|^\s*[\"']?{fx}[\"']?(?:\|[^)]*)?\)")
            hits = self._grep(["-F", "-e", f'"{flag}', "-e", f"'{flag}", "-e", f"{flag})", "-e", f"{flag}|"],
                              REF_CODE_PATHS + ["*.sh", "*.bash"], limit=400)
            hits = [h for h in hits if re.search(rf"(?<![\w-]){fx}(?![\w-])", h[2])]
            if not hits:
                return Ref(surface, "cli", "not_found", reason=f"no code in this folder has the flag {flag}")
            texts = {}

            def defines(h):  # the line defines it, or it's "--from", in a call over several lines that does
                if defining.search(h[2]):
                    return True
                if not re.fullmatch(rf"\s*[\"']{fx}[\"'],?\s*", h[2]):
                    return False
                if h[0] not in texts:
                    try:
                        with open(os.path.join(self.root, h[0]), encoding="utf-8", errors="replace") as f:
                            texts[h[0]] = f.read(self.BLOB_LIMIT).splitlines()
                    except OSError:
                        texts[h[0]] = []
                return any(defining.search(l) for l in texts[h[0]][max(0, h[1] - 4):h[1] - 1])
            hits = [(h, defines(h)) for h in hits]
            mine = (lambda path: program.replace("-", "_").lower() not in path.replace("-", "_").lower()) if program \
                else (lambda path: False)
            hits.sort(key=lambda x: (not x[1], mine(x[0][0]), "test" in x[0][0].lower(), len(x[0][0]), x[0][0], x[0][1]))
            defs, hits = [h for h, d in hits if d], [h for h, _d in hits]
            if program and not defs:  # (another program's flag, passed on here: not this folder's)
                return Ref(surface, "cli", "not_found", reason=f"no code in this folder defines {program}'s {flag}")
            defs = [h for h in defs if not mine(h[0])] or defs  # (mytool's own, if it has one)
            role = "definition" if defs else "used"
            files = list(dict.fromkeys(p for p, _n, _t in defs or hits))
            locs = [{"role": role, "path": p, "line": n, "resolver": "grep", "note": _first_line(t.strip(), 80)}
                    for p, n, t in (next(h for h in defs or hits if h[0] == path) for path in files[:20])]
            if len(files) == 1:
                return Ref(surface, "cli", "resolved", f"{files[0]}::{flag}", locations=locs)
            return Ref(surface, "cli", "ambiguous", flag, locations=locs,
                       reason=f"{len(files)} files {'define' if defs else 'use'} {flag}")
        if not how:
            return Ref(surface, "cli", "not_found", reason="not a command Signal can place")
        if how[0] == "module":
            files, dirs, _by_name = self._files(st)
            path = how[1].replace(".", "/")
            hits = sorted((f for f in files for end in (path + ".py", path + "/__main__.py", path + "/__init__.py")
                           if f == end or f.endswith("/" + end)), key=lambda f: (f.endswith("__init__.py"), len(f), f))
            if not hits:
                return Ref(surface, "cli", "not_found", reason=f"no module {how[1]} in this folder")
            locs = [{"role": "module", "path": h, "line": None, "resolver": "files", "note": f"python -m {how[1]}"}
                    for h in hits[:20]]
            return Ref(surface, "cli", "resolved" if len(hits) == 1 else "ambiguous", hits[0], locations=locs,
                       reason="" if len(hits) == 1 else f"{len(hits)} modules named {how[1]}")
        if how[0] == "kube":  # (kubectl rollout restart deploy/x: what it acts on)
            ref = self._kube_ref(surface, how[1], st, meta)
            ref.surface, ref.kind = surface, "cli"
            return ref
        ref = self._file(how[1], st, meta)  # (the script it runs, or the file it's given)
        ref.surface, ref.kind = surface, "cli"
        for loc in ref.locations:
            loc["role"], loc["note"] = "script", f"runs {loc['path']}"
        return ref
