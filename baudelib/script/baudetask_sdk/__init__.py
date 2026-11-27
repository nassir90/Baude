"""The task-script SDK (site/docs/tasks.md): everything a Bot can reach, for its tasks' scripts, as baudetask's
emission, terminal, kube, docker, db, chats, subagents, ops, hosts() and models(). Its calls are generated from the
schema (generated.py: don't change it there); this is the hand-written glue over them: the typed objects a list
answers, which carry where they're from so they can be passed back (kube.logs(pod)), and what's more than one request
(hosts, models, a subagent on another host, a command run in a terminal). Nothing here but Python's own library."""
import base64
import re
import threading
import time

from . import generated
from .client import Conn, Obj, Refused, bot, call, failed, here, where

__all__ = ["emission", "terminal", "kube", "docker", "db", "chats", "subagents", "ops", "hosts", "models", "Host",
           "Model", "Refused", "Obj"]

_DENIED = "Nobody was there to ask (a task's agent)."
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]")


def _host_id(host):
    """A call's host as an object carries it: its member id (this host's own id for this one)."""
    to = where(host)
    return to if to is not None else here().get("id")


# ---- hosts and models

class Model(Obj):
    """A model a host can run: value (what start's model takes), name, host (its id), and, where its provider says,
    tools, thinking, window."""


class Host(Obj):
    """A host of the mesh: id, name, here (this one), reachable, models (its Models), error (why it isn't
    reachable). Pass it as host= to any call."""


def _models(info, host_id):
    detail = {m.get("value"): m for p in info.get("providers") or [] for m in p.get("models") or []}
    out = []
    for m in info.get("models") or []:
        d = detail.get(m.get("value")) or {}
        out.append(Model(value=m.get("value"), name=m.get("displayName") or m.get("value"), host=host_id,
                         **{k: d[k] for k in ("tools", "thinking", "window") if k in d}))
    return out


def hosts():
    """The mesh's hosts, this one first, each a Host with its models; those out of reach have reachable False and
    error saying why."""
    info = here(fresh=True)
    out = [Host(id=info.get("id"), name=info.get("name"), here=True, reachable=True,
                models=_models(info, info.get("id")))]
    view = info.get("mesh") or {}
    found = [Host(id=m.get("id"), name=m.get("name"), here=False, reachable=False, models=[])
             for m in view.get("members") or [] if m.get("id") and m["id"] not in (view.get("inactive") or [])]
    gone = [Host(id=m.get("id"), name=m.get("name"), here=False, reachable=False, models=[],
                 error="out of reach now") for m in view.get("gone") or []]

    def look(h):
        try:
            c = Conn(h.id).connect(timeout=10)
            c.close()
            h.update(reachable=True, models=_models(c.info, h.id))
        except (OSError, ValueError, Refused) as e:
            h["error"] = str(e)
    threads = [threading.Thread(target=look, args=(h,), daemon=True) for h in found]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return out + found + gone


def models(host=None):
    """The models host can run (this one's if none's given), each a Model: pass one as a subagent's model."""
    to = where(host)
    if to is None:
        info = here(fresh=True)
        return _models(info, info.get("id"))
    c = Conn(to).connect()
    c.close()
    return _models(c.info, to)


# ---- Kubernetes

class KubeObject(generated.KubeRow):
    """An object of a cluster, as its kind's list shows it (KubeRow's fields): name, namespace, and besides kind (its
    resource: pods, deployments, ...), context, host, status (its Status column, if it has one: Running) and cells
    (each column's text, by its title)."""


class Kube(generated.Kube):
    __doc__ = generated.Kube.__doc__

    def _context(self, context, host):
        if context:
            return context
        got = self.contexts(host=host)
        current = next((c for c in got if c.current), got[0] if got else None)
        if current is None:
            raise Refused("kube: that host's kubeconfig has no contexts")
        return current.name

    def contexts(self, *, host=None, **_):
        """The host's kubeconfig's contexts, each a KubeContext (name, server, current, local) with its host."""
        host = self._host(host)
        got = super().contexts(host=host)
        hid = _host_id(host)
        for c in got.contexts or []:
            c["host"] = hid
        return got.contexts or []

    def objects(self, kind="pods", *, context=None, namespace=None, selector=None, crd=None, host=None):
        """A kind's objects (kind: its resource, as the Kubernetes view's pages have them: pods, deployments,
        services, ...; crd: a custom resource's definition), each a KubeObject; namespace: only those in it;
        selector: only those whose labels match (app=web,tier!=cache). context: the kubeconfig's current one if none."""
        host = self._host(host)
        context = self._context(context, host)
        got = self.list(context=context, page=None if crd else kind, crd=crd, selector=selector, host=host)
        titles = [c.get("title") for c in got.columns or []]
        status = next((i for i, c in enumerate(got.columns or []) if c.get("kind") == "status"), None)
        hid, out = _host_id(host), []
        for row in got.rows or []:
            if namespace and row.namespace != namespace:
                continue
            cells = row.cells or []
            out.append(KubeObject({**row, "kind": kind if not crd else crd, "context": context, "host": hid,
                                   "status": cells[status] if status is not None and status < len(cells) else None,
                                   "cells": dict(zip(titles, cells))}))
        return out

    def pods(self, *, context=None, namespace=None, selector=None, host=None):
        """The cluster's pods (objects("pods", ...)): pick one by its status, namespace or name and pass it on."""
        return self.objects("pods", context=context, namespace=namespace, selector=selector, host=host)

    @staticmethod
    def _container(pod, container):
        """The container a call's for: the one named, else the pod's default, else its first that isn't an init one."""
        if container or pod is None:
            return container
        return pod.get("default") or next((c.get("name") for c in pod.get("containers") or [] if not c.get("init")),
                                          None)

    @staticmethod
    def _of(obj, context, namespace, name, host):
        if obj is not None:
            return obj.get("context"), obj.get("namespace"), obj.get("name"), obj.get("host")
        return context, namespace, name, host

    def get(self, obj=None, *, kind=None, name=None, namespace=None, context=None, host=None):
        """An object's YAML (obj: a KubeObject; or kind, name and namespace)."""
        context, namespace, name, host = self._of(obj, context, namespace, name, self._host(host))
        kind = obj.get("kind") if obj is not None else kind
        return self.yaml(context=self._context(context, host), page=kind, namespace=namespace, name=name,
                         host=host).text

    def logs(self, pod=None, *, container=None, tail=None, previous=None, context=None, namespace=None, name=None,
             host=None, **_):
        """A pod's log as it is now (pod: a KubeObject, or context, namespace and name): its last tail lines, its
        container's (the default one if none), its previous run's with previous."""
        context, namespace, name, host = self._of(pod, context, namespace, name, self._host(host))
        return super().logs(context=self._context(context, host), namespace=namespace, name=name,
                            container=self._container(pod, container), tail=tail, previous=previous, host=host).text

    def exec(self, pod=None, command=(), *, container=None, timeout=None, context=None, namespace=None, name=None,
             host=None, **_):
        """A command (argv) run in a pod's container, no terminal: an Obj with text (what it wrote) and status."""
        context, namespace, name, host = self._of(pod, context, namespace, name, self._host(host))
        got = super().exec(context=self._context(context, host), namespace=namespace, name=name,
                           container=self._container(pod, container), command=list(command), timeout=timeout,
                           host=host)
        return Obj(text=got.text, status=got.status)

    def delete(self, obj=None, *, force=None, context=None, namespace=None, name=None, host=None, **_):
        """A pod deleted (obj: a KubeObject, or context, namespace and name): what kubectl said."""
        context, namespace, name, host = self._of(obj, context, namespace, name, self._host(host))
        return super().delete(context=self._context(context, host), namespace=namespace, name=name, force=force,
                              host=host).said

    def apply(self, manifest=None, *, context=None, namespace=None, host=None, **_):
        """Objects applied from a manifest (YAML or JSON text): what kubectl said."""
        host = self._host(host)
        return super().apply(context=self._context(context, host), manifest=manifest, namespace=namespace,
                             host=host).said


# ---- Docker

class DockerObject(generated.DockerRow):
    """A Docker object, as its page lists it (DockerRow's fields): id, name, running, actions, and besides page
    (containers, running, images, volumes, networks, compose), context, host and cells (each column's text, by its
    title)."""


class Docker(generated.Docker):
    __doc__ = generated.Docker.__doc__

    def _context(self, context, host):
        if context:
            return context
        got = self.contexts(host=host)
        current = next((c for c in got if c.current), got[0] if got else None)
        if current is None:
            raise Refused("docker: no Docker context on that host")
        return current.name

    def contexts(self, *, host=None, **_):
        """The Docker contexts the host reaches, each a DockerContext (name, current) with its host."""
        host = self._host(host)
        got = super().contexts(host=host)
        for c in got.contexts or []:
            c["host"] = _host_id(host)
        return got.contexts or []

    def objects(self, page="containers", *, context=None, host=None):
        """A page's objects (containers, running, images, volumes, networks, compose), each a DockerObject.
        context: docker's current one if none."""
        host = self._host(host)
        context = self._context(context, host)
        got = self.list(context=context, page=page, host=host)
        titles, hid = [c.get("title") for c in got.columns or []], _host_id(host)
        return [DockerObject({**r, "page": page, "context": context, "host": hid,
                              "cells": dict(zip(titles, r.cells or []))}) for r in got.rows or []]

    def ps(self, *, all=False, context=None, host=None):  # noqa: A002 (docker ps's own word)
        """The containers (the running ones, unless all), each a DockerObject."""
        return self.objects("containers" if all else "running", context=context, host=host)

    def containers(self, *, context=None, host=None):
        """Every container, running or not, each a DockerObject."""
        return self.objects("containers", context=context, host=host)

    def images(self, *, context=None, host=None):
        return self.objects("images", context=context, host=host)

    def _of(self, obj, page, oid, context, host):
        if obj is not None:
            return obj.get("page"), obj.get("id"), obj.get("context"), obj.get("host")
        return page or "containers", oid, context, self._host(host)

    def inspect(self, obj=None, *, format=None, page=None, id=None, context=None, host=None, **_):  # noqa: A002
        """What docker inspect says of an object (obj: a DockerObject; or page and id), as JSON or YAML text."""
        page, oid, context, host = self._of(obj, page, id, context, host)
        return super().inspect(context=self._context(context, host), page=page, id=oid, format=format,
                               host=host).text

    def logs(self, obj=None, *, tail=None, page=None, id=None, context=None, host=None, **_):  # noqa: A002
        """A container's or Compose project's log as it is now: its last tail lines."""
        page, oid, context, host = self._of(obj, page, id, context, host)
        return super().logs(context=self._context(context, host), page=page, id=oid, tail=tail, host=host).text

    def exec(self, obj=None, command=(), *, timeout=None, page=None, id=None, context=None, host=None, **_):  # noqa
        """A command (argv) run in a running container, no terminal: an Obj with text (what it wrote) and status."""
        page, oid, context, host = self._of(obj, page, id, context, host)
        got = super().exec(context=self._context(context, host), page=page, id=oid, command=list(command),
                           timeout=timeout, host=host)
        return Obj(text=got.text, status=got.status)

    def _act(self, what, obj, page, oid, context, host):
        page, oid, context, host = self._of(obj, page, oid, context, host)
        return getattr(super(), what)(context=self._context(context, host), page=page, id=oid, host=host).said

    def start(self, obj=None, *, page=None, id=None, context=None, host=None, **_):  # noqa: A002
        """A container or Compose project started: what docker said."""
        return self._act("start", obj, page, id, context, host)

    def stop(self, obj=None, *, page=None, id=None, context=None, host=None, **_):  # noqa: A002
        """A container or Compose project stopped: what docker said."""
        return self._act("stop", obj, page, id, context, host)

    def restart(self, obj=None, *, page=None, id=None, context=None, host=None, **_):  # noqa: A002
        """A container or Compose project restarted: what docker said."""
        return self._act("restart", obj, page, id, context, host)

    def remove(self, obj=None, *, page=None, id=None, context=None, host=None, **_):  # noqa: A002
        """An object removed (a container stopped first; a Compose project taken down): what docker said."""
        return self._act("remove", obj, page, id, context, host)

    rm = remove


# ---- databases

class Db(generated.Databases):
    __doc__ = generated.Databases.__doc__

    def servers(self, *, host=None, **_):
        """The database servers running on the host (in Docker, or a local cluster), each a DbServer with its
        databases (their names) and host."""
        host = self._host(host)
        got = super().servers(host=host)
        dbs = got.databases or {}
        return [generated.DbServer({**s, "databases": dbs.get(s.container) or [], "host": _host_id(host)})
                for s in got.servers or []]

    connections = servers

    @staticmethod
    def _of(server, database):
        if isinstance(server, dict):
            database = database or (server.get("databases") or [None])[0]
            return server.get("container"), database, server.get("host")
        return server, database, None

    def schema(self, server=None, database=None, *, host=None, **_):
        """A database's tables (server: a DbServer, or its container; database: its first if none), each a DbTable
        with its columns."""
        container, database, at = self._of(server, database)
        return super().schema(container=container, database=database, host=host or at or self.host).tables or []

    def rows(self, server=None, database=None, table=None, *, schema="public", host=None, **_):
        """A table's first rows (table: a DbTable or its name), each {column: value}, secret-looking ones masked."""
        container, database, at = self._of(server, database)
        if isinstance(table, dict):
            schema, table = table.get("schema") or schema, table.get("name")
        return super().rows(container=container, database=database, schema=schema, table=table,
                            host=host or at or self.host).rows or []

    def query(self, sql, server=None, database=None, *, host=None, **_):
        """A query's rows (one statement that reads, run read-only), each {column: value}, secret-looking ones
        masked. server: a DbServer or its container; the host's first if none."""
        if server is None:
            found = self.servers(host=host)
            if not found:
                raise Refused("db: no database server on that host")
            server = found[0]
        container, database, at = self._of(server, database)
        return super().query(container=container, database=database, sql=sql, host=host or at or self.host).rows or []


# ---- Emission and terminals

class Emission(generated.Emission):
    __doc__ = generated.Emission.__doc__

    def sites(self, *, host=None):
        """The sites the Bot's own profile on host is signed in to (its cookies' sites)."""
        return self._tool("emission", {"op": "sites"}, host).get("sites") or []

    def profile(self, *, host=None):
        """The Bot's own Emission profile on host (each host has its own; it's made as it's first used): its name
        and the sites it's signed in to."""
        got = self._tool("emission", {"op": "sites"}, host)
        return Obj(name=got.get("profile") or (bot() or {}).get("name"), sites=got.get("sites") or [],
                   host=_host_id(self._host(host)))

    def select(self, tab, *, host=None):
        """Tab brought to the front of the Bot's browser."""
        return self._tool("emission", {"op": "select_tab", "tab": tab}, host)


class Terminal(generated.Terminal):
    __doc__ = generated.Terminal.__doc__

    def run(self, argv, *, cwd=None, timeout=60, host=None):
        """argv run in a terminal of its own on host (a pseudo-terminal, as Multiplex's are), waited for: an Obj with
        output (what it wrote, its escapes taken out) and status (its exit status; None if it was still going after
        timeout seconds, when it's left to the host to end)."""
        c = Conn(where(self._host(host))).connect()
        out, status = [], None
        try:
            for got in c.stream({"op": "terminal", "argv": [str(a) for a in argv], **({"cwd": cwd} if cwd else {})},
                                lambda m: m.get("op") == "terminal_end", timeout):
                if got.get("op") == "terminal_out":
                    out.append(base64.b64decode(got.get("data") or ""))
                elif got.get("op") == "terminal_end":
                    if got.get("said"):
                        raise Refused(f"terminal: {got['said']}")
                    status = got.get("status")
        except TimeoutError:
            pass
        finally:
            c.close()
        text = _ANSI.sub("", b"".join(out).decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", "")
        return Obj(output=text, status=status)


# ---- chats and subagents

def _listing(host):
    c = Conn(where(host)).connect()  # (its own connection: one subscribed is told of every change after)
    try:
        return c.request({"op": "subscribe", "archived": True}).get("sessions") or []
    finally:
        c.close()


class Chats(generated.Area):
    """The chats on a host of the mesh, Bots too: listed, and messaged as from the task's Bot."""

    def list(self, *, bots=False, host=None):
        """A host's chats, newest first, each a Session (id, title, state, bot, ...) with its host; bots: Bots only."""
        host = self._host(host)
        hid = _host_id(host)
        got = [generated.Session({**s, "host": hid}) for s in _listing(host) if not bots or s.get("bot")]
        return sorted(got, key=lambda s: -(s.get("said") or s.get("created") or 0))

    def message(self, session, text, *, wait=True, timeout_s=600, host=None):
        """text sent to a chat (session: a Session or its id), as from the task's Bot, and unless not wait its
        answer waited for: an Obj with session, text (what it said as its turn ended) and failed."""
        if isinstance(session, dict):
            host, session = host or session.get("host"), session.get("id")
        c = Conn(where(self._host(host))).connect()
        try:
            c.request({"op": "attach", "session": session, "live": True, "answers": False})
            me = bot()
            sent = {"op": "input", "session": session, **({"as": me["id"]} if me else {}),
                    "msg": {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}}
            if not wait:
                c.request(sent)
                return Obj(session=session, text=None, failed=False)
            return _turn(c, session, timeout_s, sent)
        finally:
            c.close()


def _turn(c, sid, timeout_s, sent=None):
    """What a chat said as its turn ended, read off c (attached to it, live): {session, text, failed}. sent: sent
    first (an input), its answer passed by."""
    said = Obj(session=sid, text=None, failed=True)
    end = time.monotonic() + timeout_s
    if sent is not None:
        c.req += 1
        c._send({**sent, "req": c.req})
    try:
        while True:
            got = c.line(end)
            if got.get("op") == "error" and sent is not None and got.get("req") == c.req:
                raise Refused(f"input: {got.get('error')}")
            if got.get("session") != sid:
                continue
            msg = got.get("msg") or {}
            if got.get("op") == "claude" and msg.get("type") == "control_request" and \
                    (msg.get("request") or {}).get("subtype") == "can_use_tool":  # (no one's here to ask: refused)
                c._send({"op": "input", "session": sid, "msg": {
                    "type": "control_response", "response": {"subtype": "success", "request_id": msg.get("request_id"),
                                                             "response": {"behavior": "deny", "message": _DENIED}}}})
                continue
            if got.get("op") == "exited":
                said["text"] = "its agent ended before its turn did"
                return said
            if got.get("op") == "claude" and msg.get("type") == "result":
                said.update(text=str(msg.get("result") or ""), failed=bool(msg.get("is_error")))
                return said
    except TimeoutError:
        said["text"] = f"no answer within {timeout_s} s"
        return said


class Subagents(generated.Area):
    """Subagents of the task's Bot: chats started on any host of the mesh with any model it runs, as the Bot's own
    start_subagent starts them (Auto at most; their asks for permission refused)."""

    def start(self, prompt, *, host=None, model=None, cwd=None, title=None, mode="auto", wait=True, timeout_s=3600):
        """A subagent started on host (this one if none's given) with model (a Model, or its value), given prompt:
        an Obj with session, host, text (what it said as its first turn ended; None if not wait) and failed. On this
        host it's baudetask.agent's: nested under the Bot, kept with the run."""
        import baudetask
        host = self._host(host)
        if isinstance(model, dict):
            host, model = host or model.get("host"), model.get("value")
        to = where(host)
        if to is None:
            got = baudetask.agent(prompt, wait=wait, cwd=cwd, model=model, title=title, mode=mode,
                                  timeout_s=timeout_s)
            return Obj(got, host=here().get("id"))
        me = bot() or {}
        start = {"op": "start", "prompt": str(prompt), "title": title or " ".join(str(prompt).split())[:60],
                 "mode": mode or "auto", "ask_timeout": 60, "ask_default": "deny", "attach": bool(wait),
                 "answers": False, **({"spawned_by": me["id"], "as": me["id"]} if me.get("id") else {}),
                 **{k: v for k, v in (("cwd", cwd), ("model", model)) if v}}
        c = Conn(to).connect()
        try:
            sid = c.request(start)["session"]["id"]
            if not wait:
                return Obj(session=sid, host=to, text=None, failed=False)
            return Obj(_turn(c, sid, timeout_s), host=to)
        finally:
            c.close()

    def list(self, *, host=None):
        """The Bot's subagents on host (the chats it, or its tasks' runs, started there), each a Session."""
        me = (bot() or {}).get("id")
        return [s for s in chats.list(host=host) if me and s.get("spawned_by") == me]


emission = Emission()
terminal = Terminal()
kube = Kube()
docker = Docker()
db = Db()
chats = Chats()
subagents = Subagents()
ops = generated.Ops()
AREAS = {"emission": emission, "terminal": terminal, "kube": kube, "docker": docker, "db": db, "chats": chats,
         "subagents": subagents}
TOP = ("hosts", "models")
