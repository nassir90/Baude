"""Who in the mesh may use what of a host's (Settings → Access, on every app; the access and access_set ops): a rule
for each of its capabilities (CAPABILITIES: a shell, its Bots' browsers, its providers' models, their files, new chats
and agents, its chats as they are, its clusters, its databases, and these rules themselves), each everybody, nobody or
the members chosen, by their ids, as the mesh has them (a tunnel's origin: Conn's). A chat may have a rule of its own
for managing it, kept in its meta (access), which goes before the host's manage rule.

A head on the host's own machine, or one reaching it over ssh (no origin), is never refused: these rules are for the
rest of the mesh. A rule not set is everybody's, as a mesh with no rules has it. Kept in access.json in the host's
state folder. The ops each capability takes are OPS; a chat's (those with a session) are manage's, checked as the chat's
found (allowed's session). What's refused is answered with wire's access_denied, naming the capability."""
import os
import threading

from baudelib import wire, wording

CAPABILITIES = tuple(wire.DEFS["AccessRules"]["properties"])
WHO = tuple(wire.DEFS["AccessRule"]["properties"]["who"]["enum"])
EVERYBODY = {"who": "everybody"}
OPS = {  # (the ops each capability's rule is checked for, before they're taken; a chat's ops: manage, as it's found)
    **dict.fromkeys(("terminal", "terminal_in", "terminal_resize", "terminal_close", "terminals", "multiplex"), "shell"),
    **dict.fromkeys(("emission", "emission_watch", "emission_watch_say", "emission_serve", "emission_answer"),
                    "browser"),
    "infer": "providers",
    "files": "model_files",
    **dict.fromkeys(("start", "agent"), "spawn"),
    **dict.fromkeys(("kube", "kube_follow"), "kube"),
    "databases": "databases",
}
COPY = {cap: f"access.{cap}" for cap in CAPABILITIES} | {  # (each capability's name, as every app says it)
    "shell": "kube.shell", "providers": "deps.providers", "kube": "window.kubernetes"}
FILE = "access.json"
MEMBERS_MAX = 256


def named(cap):
    """cap as every app names it."""
    return wording.say(COPY[cap])


def clean(rule):
    """A rule as it's kept: who, and chosen's members (ids, each once, in order); ValueError for one that isn't."""
    if not isinstance(rule, dict) or rule.get("who") not in WHO:
        raise ValueError(wording.say("access.bad_rule"))
    if rule["who"] != "chosen":
        return {"who": rule["who"]}
    members = rule.get("members") if isinstance(rule.get("members"), list) else []
    return {"who": "chosen", "members": list(dict.fromkeys(m for m in members if wire.is_id(m)))[:MEMBERS_MAX]}


def lets(rule, member):
    """Does rule let member (an id) in?"""
    who = (rule or EVERYBODY).get("who")
    return who == "everybody" or who == "chosen" and member in (rule.get("members") or ())


class Access:
    """A host's rules (server: the Server they're for), read from its state folder as it starts."""

    def __init__(self, server):
        self.server = server
        self.path = os.path.join(server.state_dir, FILE)
        self.lock = threading.Lock()
        from baudelib.serverstate import _read_json
        kept = _read_json(self.path)
        kept = kept.get("rules") if isinstance(kept, dict) and isinstance(kept.get("rules"), dict) else {}
        self.rules = {}
        for cap in CAPABILITIES:
            try:
                self.rules[cap] = clean(kept.get(cap)) if cap in kept else dict(EVERYBODY)
            except ValueError:  # (a file not as it's written: that rule taken as the safest there is)
                self.rules[cap] = {"who": "nobody"}

    def rule(self, cap):
        with self.lock:
            return dict(self.rules[cap])

    def allowed(self, conn, cap, session=None):
        """May conn (a Conn) use cap? A chat's own rule (session: its Session) goes before the host's for manage."""
        member = getattr(conn, "origin", None)
        if member is None:  # (this machine's, or over ssh: its user's own)
            return True
        if cap == "manage" and session is not None and isinstance(getattr(session, "meta", None), dict) \
                and (own := session.meta.get("access")) is not None:
            return lets(own, member)
        return lets(self.rule(cap), member)

    def refused(self, req, cap):
        """access_denied's error for request req: cap refused."""
        return {**wire.refused(wire.DENIED, req, named(cap)), "denied": cap}

    def set(self, cap, rule):
        """cap's rule set (rule: as the wire has it) and kept: ValueError if it can't be."""
        if cap not in CAPABILITIES:
            raise ValueError(wording.say("access.bad_rule"))
        rule = clean(rule)
        from baudelib.serverstate import _write_json
        with self.lock:
            self.rules[cap] = rule
            _write_json(self.path, {"rules": self.rules})

    def members(self, extra=()):
        """The members a rule can name: those the mesh reaches or reached lately, then any rule names (extra too) that
        isn't one, by its id's start."""
        view = self.server.mesh.view()
        me = self.server.me["id"]
        out = {}
        for m in view["members"] + view["gone"]:
            if m.get("id") != me and wire.is_id(m.get("id") or "") and m["id"] not in out:
                out[m["id"]] = str(m.get("name") or m["id"][:8])
        with self.lock:
            named = [x for r in self.rules.values() for x in r.get("members") or ()]
        for x in [*named, *extra]:
            if x not in out and x != me:
                out[x] = self.server.mesh.name_of(x)
        return [{"id": k, "name": v} for k, v in out.items()]

    def state(self, conn, extra=()):
        """As the wire's AccessState has it, for conn."""
        with self.lock:
            rules = {cap: dict(r) for cap, r in self.rules.items()}
        return {"host": self.server.me["name"], "rules": rules, "members": self.members(extra),
                "editable": self.allowed(conn, "access")}
