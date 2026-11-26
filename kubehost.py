"""The Kubernetes and database views on another host (the one picked in the window's host dropdown, as ☰ → Terminal's
new tabs are): what they read there, asked of its host process (wire's kube, kube_follow and databases ops:
baudelib/hostkube.py, baudelib/hostdb.py), in the shapes the views read on this machine (kubedata's rows, schemadata's
schema). This machine's own clusters and databases are read here, as ever."""
import threading
import time

from gi.repository import GLib  # noqa: E402

from baudelib import wire, wording  # noqa: E402


class Host:
    """The host the views read from: remote, its Remote (baudelib.client's: None for this machine), where (wire.reach's)
    and its name."""

    def __init__(self, remote, where, name):
        self.remote, self.where, self.name = remote, where, name

    @property
    def here(self):
        return self.remote is None

    def key(self):
        return None if self.remote is None else repr(sorted(wire.place(self.where).items()))

    def ask(self, op, msg, done):
        """A request of the host process there: done(reply) on the main thread; an error's reply is {"error": why}."""
        def got(r):
            done({"error": wire.said(r) or wording.say("host_status.no_answer")} if r.get("op") == "error" else r)
        self.remote.request({"op": op, **{k: v for k, v in msg.items() if v is not None}}, got)


def picked(win):
    """The host picked in win's host dropdown (☰ → Terminal's: terminal_host, view_remote), this machine if none."""
    where, name = win.terminal_host()
    remote = win.view_remote() if where.get("via") != "local" and hasattr(win, "view_remote") else None
    return Host(remote, where, name) if remote is not None else Host(None, {"via": "local"}, name)


class Follow:
    """A kube_follow on a host, on a connection of its own (the host ends its kubectl when that goes): on_text(f, text)
    as a log's lines come, on_rows(f, reply) as a watched list changes, on_contexts(f, reply) as the kubeconfig's
    contexts do, on_end(f, code, said, removed) once it ends by itself (removed: its context went from the kubeconfig);
    all on the main thread. stop() ends it: nothing more is handed on."""
    REQ = 1

    def __init__(self, where, msg, on_end, on_text=None, on_rows=None, on_contexts=None):
        self.on_end, self.on_text, self.on_rows, self.on_contexts = on_end, on_text, on_rows, on_contexts
        self.ch, self.stopped, self.seen = None, False, 0
        threading.Thread(target=self._run, args=(where, msg), daemon=True, name="kube-follow").start()

    def _run(self, where, msg):
        try:
            ch, _info = wire.reach(where, timeout=15)
        except (OSError, ValueError) as e:
            GLib.idle_add(self._ended, 1, str(e) or wording.say("host_status.no_answer"), False)
            return
        self.ch = ch
        if self.stopped:
            ch.close()
            return
        ch.keep_alive()
        ch.send({"op": "kube_follow", "req": self.REQ, **{k: v for k, v in msg.items() if v is not None}})
        end = (1, wording.say("host_status.connection_dropped"), False)
        try:
            for r in ch:
                if r.get("req") != self.REQ:
                    continue
                op = r.get("op")
                if op == "kube_text" and self.on_text is not None:
                    GLib.idle_add(self._hand, self.on_text, r.get("text") or "")
                elif op == "kube_rows" and self.on_rows is not None:
                    GLib.idle_add(self._hand, self.on_rows, r)
                elif op == "kube_contexts" and self.on_contexts is not None:
                    GLib.idle_add(self._hand, self.on_contexts, r)
                elif op == "kube_end":
                    end = (r.get("code") or 0, r.get("said") or "", bool(r.get("removed")))
                    break
                elif op == "error":
                    end = (1, wire.said(r), False)
                    break
        except (OSError, ValueError) as e:
            end = (1, str(e), False)
        ch.close()
        GLib.idle_add(self._ended, *end)

    def _hand(self, fn, what):
        if not self.stopped:
            self.seen += 1
            fn(self, what)
        return False

    def _ended(self, code, said, removed):
        if not self.stopped:
            self.stopped = True
            self.on_end(self, code, said, removed)
        return False

    def stop(self):
        self.stopped = True
        if self.ch is not None:
            self.ch.close()


def _key(k):
    return tuple(_key(x) for x in k) if isinstance(k, list) else k


def rows(reply_rows):
    """A host's rows (hostkube's) as kubedata.kube_rows makes them here: (texts, sort keys, obj), obj with what the
    view asks of an object without its JSON: its containers, its controller, its default container, its cells' tones."""
    now, out = time.time(), []
    for r in reply_rows or []:
        out.append((list(r.get("cells") or []), [_key(k) for k in r.get("keys") or []], {
            "name": r.get("name") or "", "namespace": r.get("namespace") or "", "raw": None, "now": now,
            "uid": r.get("uid") or "", "version": r.get("version") or "", "tones": r.get("tones") or [],
            "containers": [(c.get("name") or "", c.get("look") or "done", c.get("words") or "", bool(c.get("init")))
                           for c in r.get("containers") or []],
            "owner": {"kind": r["owner_kind"], "name": r.get("owner_name") or ""} if r.get("owner_kind") else None,
            "default": r.get("default") or None}))
    return out


def columns(reply_columns):
    """A host's columns as the view lays them out (their tones come with each row)."""
    return [{"title": c.get("title") or "", "kind": c.get("kind") or "text", "tone": None} for c in reply_columns or []]


def page(p, known=None):
    """A host's page (hostkube's _page_json) as the view's pages are: one it lists itself (known: by its resource), or a
    custom resource's."""
    if known is not None:
        return known
    return {"title": p.get("title") or "", "resource": p.get("resource") or "", "crd": p.get("crd"),
            "namespaced": bool(p.get("namespaced")), "source": p.get("source") or "kubectl", "api": None,
            "columns": [], "known": False, "host_crd": p.get("crd") or p.get("resource")}


def schema(reply):
    """A host's schema of a database (hostdb's) as schemadata.pg_schema has it: tables by (schema, name), and links."""
    tables = {}
    for t in reply.get("tables") or []:
        tables[(t["schema"], t["name"])] = {"schema": t["schema"], "name": t["name"], "kind": t.get("kind") or "r",
                                            "rows": t.get("rows") or 0,
                                            "columns": [{"name": c["name"], "type": c.get("type") or "",
                                                         "null": bool(c.get("null")), "pk": bool(c.get("pk")),
                                                         "fk": c.get("fk")} for c in t.get("columns") or []]}
    links = [((ln["from_schema"], ln["from_table"]), ln["from_column"], (ln["to_schema"], ln["to_table"]),
              ln["to_column"]) for ln in reply.get("links") or []]
    return {"tables": tables, "links": links}
