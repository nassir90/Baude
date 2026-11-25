"""scenes_deps — tests/screenshots.py's scenes of a dependency that isn't there (schema/deps): what Kubernetes, Database
Schemas and ☰ → Terminal show in its place, and Settings → Dependencies. Each is made missing with BAUDE_HIDE_DEPS
(baudelib.deps' fault injection), whatever this machine has, and the program it names pointed nowhere besides.
"""
import os

import test_baude as T  # noqa: E402  (sets up the throwaway env: fake claude, temp config dirs)

from scenelib import H, SIDEBAR, opts, scene


def hide(app, names, waits=True, **env):
    """A window, names (schema/deps keys) missing while it's open (from before it's made: its new chat's start too):
    BAUDE_HIDE_DEPS, and env (a program's path, nowhere). waits: for its chat's claude, as the tests' windows do."""
    saved = {k: os.environ.get(k) for k in ("BAUDE_HIDE_DEPS", *env)}
    os.environ.update(BAUDE_HIDE_DEPS=",".join(names), **env)
    if waits:
        win = app.window(opts())
    else:
        win = T.baude.BaudeWindow(app.app, opts())
        if getattr(app, "next_size", None):
            win.set_default_size(*app.next_size)
        win.present()
        T.pump(1.5)

    def put_back(*_):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    win.connect("destroy", put_back)
    return win


@scene("39a-missing-kubectl", "Kubernetes (☰ → Kubernetes) on a host without kubectl: what's missing, the command "
                              "that installs it there with Copy, and Try Again", size=(1200 + SIDEBAR, H))
def _(app):
    import kube_fixture as kf
    win = hide(app, ["kubectl"], BAUDE_KUBECTL=os.path.join(T.TMP, "no-kubectl"),
               KUBECONFIG=kf.make_kubeconfig(os.path.join(T.TMP, "shot-kubeconfig")))
    win.kube.open()
    T.pump(2.0)
    return win


@scene("39b-missing-docker", "Database Schemas (☰ → Database Schemas) on a host without Docker and no server found: "
                             "what's missing, the command that installs it there with Copy, and Try Again",
       size=(1200 + SIDEBAR, H))
def _(app):
    win = hide(app, ["docker"], BAUDE_KUBECTL=os.path.join(T.TMP, "no-kubectl"),
               KUBECONFIG=os.path.join(T.TMP, "no-kubeconfig"))
    import lookupkube
    lookupkube.PG_LISTS.clear()
    win.schema.open()
    T.pump(3.0)
    return win


@scene("39c-missing-vte", "☰ → Terminal without VTE (the app's own library): what's missing, the command that "
                          "installs it with Copy, and Restart", size=(1200 + SIDEBAR, H))
def _(app):
    import multiplexterm
    win = hide(app, ["vte"])
    vte, multiplexterm.Vte = multiplexterm.Vte, None
    win.connect("destroy", lambda *_: setattr(multiplexterm, "Vte", vte))
    win.terminal.open()
    T.pump(1.0)
    return win


@scene("39d-dependencies", "Settings → Dependencies: the host picked beside New session (this machine's here), then "
                           "what its features and its machine's apps need under what needs each (Providers, Git and "
                           "GitHub, Kubernetes, …): Installed, Running with Ollama's models, or the command that "
                           "installs or readies it with Copy (gh signed out), else its page (a kubeconfig with no "
                           "context)", size=(1200 + SIDEBAR, H))
def _(app):
    return dependencies(app)[0]


@scene("39g-dependencies-end", "Settings → Dependencies scrolled to its end: Kubernetes, Database Schemas, Multiplex, "
                               "Emission and Signal", size=(1200 + SIDEBAR, H))
def _(app):
    win, page = dependencies(app)
    from widgets import walk
    from gi.repository import Gtk
    scroller = next(w for w in walk(page) if isinstance(w, Gtk.ScrolledWindow))
    adj = scroller.get_vadjustment()
    adj.set_value(adj.get_upper())
    T.pump(0.5)
    return win


def dependencies(app):
    """39d's: Settings → Dependencies open, the host answered: (the window, the page)."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Ollama(BaseHTTPRequestHandler):  # (a stand-in Ollama with two models)
        def do_GET(self):
            body = json.dumps({"version": "0.12.0"} if self.path == "/api/version" else
                              {"models": [{"name": "qwen3:4b"}, {"name": "gemma3:4b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    empty = os.path.join(T.TMP, "empty-kubeconfig")
    with open(empty, "w") as f:
        f.write("apiVersion: v1\nkind: Config\ncontexts: []\nclusters: []\nusers: []\n")
    gh = os.path.join(T.TMP, "fake-gh-signed-out")
    with open(gh, "w") as f:
        f.write(f'#!/bin/sh\nexec {T.sys.executable} {os.path.join(T.HERE, "fake_gh.py")} "$@"\n')
    os.chmod(gh, 0o755)
    with open(gh + ".json", "w") as f:
        json.dump({"viewer": "ADMIN", "issues": [], "prs": []}, f)
    win = hide(app, ["docker", "codex"], BAUDE_GH=gh, FAKE_GH_STATE=gh + ".json", FAKE_GH_SIGNED_IN="0", KUBECONFIG=empty,
               BAUDE_KUBECTL=os.path.join(T.HERE, "fake_kubectl.py"), OLLAMA_HOST=f"127.0.0.1:{server.server_port}")
    win.connect("destroy", lambda *_: server.shutdown())
    dialog = win.open_settings()
    import depsview
    page = next(p for p in T.find(dialog, depsview.Page))
    dialog.set_visible_page(page)
    T.wait_for(lambda: page.groups, "the host's dependencies", 20)
    T.pump(0.5)
    return win, page


@scene("39e-missing-claude", "A Claude chat on a host without Claude Code: what's missing and the command that "
                             "installs it there, said where the message was sent", size=(1200 + SIDEBAR, H))
def _(app):
    win = hide(app, ["claude"], waits=False, BAUDE_CLAUDE=os.path.join(T.TMP, "no-claude"))
    T.type_and_send(win, "Tidy the README")
    T.pump(3.0)
    return win


@scene("39f-kubectl-installed", "Kubernetes (☰ → Kubernetes) after kubectl appears on the host and Try Again is "
                                "pressed: the table is back, the missing state gone", size=(1200 + SIDEBAR, H))
def _(app):
    import kube_fixture as kf
    win = hide(app, ["kubectl"], BAUDE_KUBECTL=os.path.join(T.TMP, "no-kubectl"),
               KUBECONFIG=kf.make_kubeconfig(os.path.join(T.TMP, "shot-kubeconfig")),
               FAKE_KUBECTL_LOG=os.path.join(T.TMP, "fake-kubectl.log"))
    win.kube.open()
    T.pump(2.0)
    os.environ.update(BAUDE_HIDE_DEPS="", BAUDE_KUBECTL=os.path.join(T.HERE, "fake_kubectl.py"))
    win.kube.missing.button.emit("clicked")
    T.pump(3.0)
    return win
