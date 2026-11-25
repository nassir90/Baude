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
                              "that installs it there with Copy, and Try again", size=(1200 + SIDEBAR, H))
def _(app):
    import kube_fixture as kf
    win = hide(app, ["kubectl"], BAUDE_KUBECTL=os.path.join(T.TMP, "no-kubectl"),
               KUBECONFIG=kf.make_kubeconfig(os.path.join(T.TMP, "shot-kubeconfig")))
    win.kube.open()
    T.pump(2.0)
    return win


@scene("39b-missing-docker", "Database Schemas (☰ → Database Schemas) on a host without Docker and no server found: "
                             "what's missing, the command that installs it there with Copy, and Try again",
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


@scene("39d-dependencies", "Settings → Dependencies: the host picked beside New session (this machine's here), each "
                           "program its features need, Installed or the command that installs it with Copy; then "
                           "the app's own", size=(1200 + SIDEBAR, H))
def _(app):
    win = hide(app, ["kubectl", "docker", "codex", "vte"])
    dialog = win.open_settings()
    import depsview
    page = next(p for p in T.find(dialog, depsview.Page))
    dialog.set_visible_page(page)
    T.wait_for(lambda: page.host_rows.get_first_child() is not None, "the host's dependencies", 20)
    T.pump(0.5)
    return win


@scene("39e-missing-claude", "A Claude chat on a host without Claude Code: what's missing and the command that "
                             "installs it there, said where the message was sent", size=(1200 + SIDEBAR, H))
def _(app):
    win = hide(app, ["claude"], waits=False, BAUDE_CLAUDE=os.path.join(T.TMP, "no-claude"))
    T.type_and_send(win, "Tidy the README")
    T.pump(3.0)
    return win
