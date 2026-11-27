"""Who in the mesh may use what of a host's (Settings → Access, on every app; the access and access_set ops): rules
for each of its capabilities (CAPABILITIES: a shell, its Bots' browsers, its providers' models, their files, new chats
and agents, its chats as they are, its clusters, its Docker, its databases, its subscription accounts, and these rules themselves
with the host's own admin), each everybody, nobody or the members chosen, by their ids, as the mesh has them (a
tunnel's origin: Conn's). A capability whose ops part into reading and changing (SPLIT) has two rules, <cap>_read and
<cap>_write, and write lets a member read too: a member let write is let read, and the rules are kept so read's lets in
everyone write's does (set). The rest have one (RULES: every rule's name, as the wire's AccessRules has them). A chat
may have rules of its own for managing it, kept in its meta (access: AccessChat's), which go before the host's, its
write never letting in one its read doesn't (chat_rules).

Who's asking is Conn's member: a tunnel's origin, as its open was signed (mesh.Mesh.opener), or, for a connection a
chat's own tools make on its host's socket (hello's as), the member that started that chat (its meta's started_by), so
a chat started from elsewhere does nothing its starter couldn't. A head on the host's own machine, or one reaching it
over ssh (no member), is never refused: these rules are for the rest of the mesh. A member whose open wasn't signed by
a key known here (on older code: not verified) is let in by an everybody rule only. A rule not set is everybody's, as a
mesh with no rules has it, but for changing the rules themselves (access_write), which is nobody's: only this machine's
heads, and those over ssh, change them till members are chosen. Kept in access.json in the host's state folder; a rule
kept before its capability was split is both of its sides' (Access Rules': write's, as it only ever guarded changing
them). The rule each op is checked for is OPS's; a chat's (FOUND) are checked as the chat's found (allowed's session),
and some ask more of it (needs). What's refused is answered with wire's access_denied, naming the rule."""
import os
import threading

from baudelib import wire, wording

RULES = tuple(wire.DEFS["AccessRules"]["properties"])
SIDES = ("_read", "_write")


def capability(rule):
    """The capability a rule's for: kube_write's kube, shell's shell."""
    return rule.removesuffix("_read").removesuffix("_write") if rule.endswith(SIDES) else rule


CAPABILITIES = tuple(dict.fromkeys(capability(r) for r in RULES))
SPLIT = tuple(c for c in CAPABILITIES if c + "_read" in RULES)


def rules_of(cap):
    """cap's rules: its read and write, or the one."""
    return (cap + "_read", cap + "_write") if cap in SPLIT else (cap,)


WHO = tuple(wire.DEFS["AccessRule"]["properties"]["who"]["enum"])
EVERYBODY = {"who": "everybody"}
NOBODY = {"who": "nobody"}


def _kube(msg):
    return "kube_write" if msg.get("what") == "delete" else "kube_read"


def _docker(msg):
    return "docker_write" if msg.get("what") in ("start", "stop", "restart", "remove") else "docker_read"


def _accounts(msg):
    return "subscriptions_read" if msg.get("what") in ("list", "usage") else "subscriptions_write"


def _attach(msg, session=None):  # (tools allowed always it doesn't allow yet, or the head's environment for its claude
    # other than it has: a change to the chat (session: as it's found); the same again, only a look)
    return "manage_write" if adds_tools(msg, session) or changes_env(msg, session) else "manage_read"


def adds_tools(msg, session=None):
    """Does an attach's always_allow name tools session doesn't already allow always?"""
    tools = msg.get("always_allow")
    if not isinstance(tools, list):
        return bool(tools)
    return bool({t for t in tools if isinstance(t, str)} - set(getattr(session, "always_allow", None) or ()))


def changes_env(msg, session=None):
    """Does an attach's env set session's claude's environment to other than it is (an empty one: no)?"""
    env = msg.get("env")
    if not isinstance(env, dict):
        return bool(env)
    from baudelib.serverstate import clean_env
    return bool(env) and clean_env(env) != (getattr(session, "env", None) or {})


def _autofix(msg):
    return "manage_write" if msg.get("on") is not None else "manage_read"


def _fixes(msg):
    return "access_read" if msg.get("what") in ("list", "shot") else "access_write"


def _notes(msg):
    return "manage_write" if msg.get("action") == "write" else "manage_read"


def _memory(msg):
    return "manage_write" if msg.get("text") is not None or msg.get("seen") is not None else "manage_read"


def _watch(msg):
    return "browser_write" if msg.get("default") else "browser_read"  # (Emission's own window: driven, not a Bot's)


def _deciders(msg):
    return "access_write" if msg.get("what") in ("add", "remove", "test") else "providers"  # (the host's own setup)


def _tasks(msg):
    return "manage_write" if "set" in msg or "delete" in msg else "manage_read"


OPS = {  # (the rule each op is checked for, before it's taken (a function: msg's, by its fields); FOUND's as it's found)
    **dict.fromkeys(("terminal", "terminal_in", "terminal_resize", "terminal_close", "terminals", "multiplex"), "shell"),
    "emission_watch": _watch,
    "emission_watch_say": "browser_read",
    **dict.fromkeys(("emission", "emission_serve", "emission_answer", "browser_open"), "browser_write"),
    "infer": "providers",
    "decide": "providers",  # (a decision model's: as a provider's model)
    "deciders": _deciders,
    "files": "model_files",
    **dict.fromkeys(("start", "agent"), "spawn"),  # (a chat forked: its manage_read too; adopted: its manage_write)
    "kube": _kube,
    "kube_follow": "kube_read",
    "docker": _docker,
    "docker_follow": "docker_read",
    "databases": "databases",
    "accounts": _accounts,  # (a chat moved to one: its manage_write too)
    **dict.fromkeys(("login", "login_code"), "subscriptions_write"),
    "access": "access_read",
    "telemetry_read": "access_read",  # (the host's own, as telemetry_set's)
    "fixes": _fixes,  # (Ongoing Feedback, acted on with the host's gh: the host's own, as feedback's)
    "notes": _notes,  # (Record's notebooks here, and the edits waiting on their pages: as its chats' are)
    "edits": "manage_read",
    **dict.fromkeys(("edit_propose", "edit_revise", "edit_withdraw", "edit_decide", "edit_adopt"), "manage_write"),
    **dict.fromkeys(("access_set", "quit", "restart", "link", "unlink", "deactivate", "activate", "telemetry_set",
                     "theme_save", "theme_remove", "feedback"), "access_write"),  # (the rules, and the host's own)
    "list_dir": "manage_read",  # (any folder's entries: as much as a chat's, which could list them)
    # (a chat's, or a chat's folder's: checked as it's found)
    **dict.fromkeys(("detach", "transcript", "chat_size", "subagents", "senders", "bot_picture", "task", "task_run",
                     "task_output", "read_file", "git", "signal_names", "catalogue"), "manage_read"),
    "attach": _attach,
    "memory": _memory,
    "tasks": _tasks,
    "autofix": _autofix,  # (no on: only as it is)
    **dict.fromkeys(("input", "held", "stop", "rename", "archive", "unarchive", "delete", "set_cwd", "always_allow",
                     "bot", "rotate", "run_task", "task_stop", "answer_task", "waits_on", "seen", "draft_save"), "manage_write"),
    # (seen: what every head shows of it changed, as memory's seen is a write; a draft: a write)
}
CHATLESS = frozenset(("list_dir", "notes", "edits", "edit_propose", "edit_revise", "edit_withdraw", "edit_decide",
                      "edit_adopt"))  # (Agent Management's, but no chat's: checked as they come)
DEFAULTS = {"access_write": NOBODY}  # (a rule not set, if it isn't everybody's)
SAFE_MODES = (None, "default", "plan", "acceptEdits")  # (permission modes that ask before a command's run)
SHELL_TOOLS = ("Bash", "PowerShell")  # (tools that run commands: approving one, or allowing it always, is a shell's worth)
COPY = {cap: f"access.{cap}" for cap in CAPABILITIES} | {  # (each capability's name, as every app says it)
    "shell": "kube.shell", "providers": "deps.providers", "kube": "window.kubernetes",
    "docker": "window.docker", "databases": "window.database_schemas", "subscriptions": "subscriptions.title"}
FILE = "access.json"
MEMBERS_MAX = 256


def rule_of(msg, session=None):
    """The rule msg's op is checked for (OPS'; session: the chat it's to, as it's found), or None for one no rule
    guards."""
    r = OPS.get(msg.get("op"))
    if r is _attach:
        return r(msg, session)
    return r(msg) if callable(r) else r


FOUND = frozenset(op for op in OPS if op not in CHATLESS and capability(rule_of({"op": op})) == "manage")  # (a chat's)


def named(rule):
    """A rule as every app names it: its capability's name, and Read or Write for a split one's."""
    cap = capability(rule)
    said = wording.say(COPY[cap])
    if cap == rule:
        return said
    return wording.say("access.side", capability=said,
                       side=wording.say("access.read" if rule.endswith("_read") else "access.write"))


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


def wider(a, b):
    """A rule letting in everyone a or b does."""
    if "everybody" in (a["who"], b["who"]) or a["who"] == "nobody" or b["who"] == "nobody":
        return dict(b if a["who"] == "nobody" or b["who"] == "everybody" else a)
    return {"who": "chosen", "members": list(dict.fromkeys(a["members"] + b["members"]))[:MEMBERS_MAX]}


def narrower(a, b):
    """A rule letting in only those both a and b do."""
    if a["who"] == "everybody" or b["who"] == "nobody":
        return dict(b)
    if b["who"] == "everybody" or a["who"] == "nobody":
        return dict(a)
    both = [m for m in a["members"] if m in b["members"]]
    return {"who": "chosen", "members": both} if both else dict(NOBODY)


def kept_together(rules, name, base=None):
    """rules (a capability's, by name; base: what one not in rules is) with name's just set, its other side made to
    match: read widened to let in everyone write does, write narrowed to those read does. Changed in place."""
    cap = capability(name)
    if cap == name:
        return rules
    read, write = cap + "_read", cap + "_write"
    get = lambda r: rules.get(r) if rules.get(r) is not None else (base or {}).get(r, EVERYBODY)  # noqa: E731
    if name == write and rules.get(write) is not None:
        if (r := wider(get(read), rules[write])) != get(read):
            rules[read] = r
    elif name == read and rules.get(read) is not None:
        if (w := narrower(get(write), rules[read])) != get(write):
            rules[write] = w
    return rules


def chat_rules(own, host):
    """A chat's rules for managing it, as they're checked (own: own_rules', host: the host's, by name): each side its
    own or the host's as it is now, write narrowed to those read lets in, so write never lets in one read doesn't."""
    read = own.get("manage_read") or host["manage_read"]
    return {"manage_read": dict(read), "manage_write": narrower(own.get("manage_write") or host["manage_write"], read)}


def own_rules(meta):
    """A chat's own rules (its meta's access), by name: one kept before Agent Management was split, both sides'."""
    own = (meta or {}).get("access") if isinstance(meta, dict) else None
    if not isinstance(own, dict):
        return {}
    if "who" in own:
        try:
            return dict.fromkeys(rules_of("manage"), clean(own))
        except ValueError:
            return dict.fromkeys(rules_of("manage"), dict(NOBODY))
    out = {}
    for r in rules_of("manage"):
        if r in own:
            try:
                out[r] = clean(own[r])
            except ValueError:
                out[r] = dict(NOBODY)
    return out


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
        for name in RULES:
            cap = capability(name)
            was = name if name in kept else cap if cap in kept and (cap != "access" or name == "access_write") else None
            try:
                self.rules[name] = clean(kept[was]) if was else dict(DEFAULTS.get(name, EVERYBODY))
            except ValueError:  # (a file not as it's written: that rule taken as the safest there is)
                self.rules[name] = dict(NOBODY)
        for cap in SPLIT:
            kept_together(self.rules, cap + "_write")

    def rule(self, name):
        with self.lock:
            return dict(self.rules[name])

    def allowed(self, conn, name, session=None):
        """May conn use what rule name guards? A chat's own rules (session: its Session) go before the host's for
        manage's; one let write is let read."""
        member = getattr(conn, "member", None)
        if member is None:  # (this machine's, or over ssh: its user's own)
            return True
        if not getattr(conn, "verified", True):  # (not known to be who it says: nobody a rule could choose)
            member = ""
        if capability(name) == "manage" and session is not None:  # (its write within its read: chat_rules)
            with self.lock:
                host = {r: self.rules[r] for r in rules_of("manage")}
            return lets(chat_rules(own_rules(getattr(session, "meta", None)), host)[name], member)
        sides = (name, capability(name) + "_write") if name.endswith("_read") else (name,)
        return any(lets(self.rule(r), member) for r in sides)

    def needs(self, conn, msg, session):
        """What else than manage_write a chat's op asks of a member (session: the chat's), as it would let it run commands
        or drive a Bot: shell to approve a command or allow one always, to start one with commands allowed, or to send
        to one that runs them without asking, or to make, change or run one of a Bot's tasks (a script run here);
        shell and browser_write for a Bot, which has terminals and a browser of its own, to make one, start or fork one, send
        it a turn, or run or set one of its tasks (which ask it). The first refused, or None."""
        if getattr(conn, "member", None) is None:
            return None
        op, meta = msg.get("op"), getattr(session, "meta", None) or {}
        caps = []
        inner = msg.get("msg") if isinstance(msg.get("msg"), dict) else {}
        if op == "always_allow" or op == "start" and (msg.get("always_allow") or msg.get("env")) or \
                op == "attach" and (adds_tools(msg, session) or changes_env(msg, session)):
            caps.append("shell")  # (an environment's a command's worth too: one that names a library to load)
        if op == "start" and (msg.get("mode") not in SAFE_MODES or msg.get("ask_default") == "allow" or
                              any(t.split("(")[0] in SHELL_TOOLS for t in msg.get("allowed_tools") or ()
                                  if isinstance(t, str))):
            caps.append("shell")
        if op == "input" and inner.get("type") == "control_response" and \
                (session is not None and self.asks_for_shell(session, inner)):
            caps.append("shell")
        if op == "input" and inner.get("type") == "control_request" and \
                (inner.get("request") or {}).get("subtype") == "set_permission_mode" and \
                (inner.get("request") or {}).get("mode") not in SAFE_MODES:
            caps.append("shell")
        if op == "run_task" or op == "tasks" and "set" in msg:  # (a task: a script run here, a shell's worth)
            caps.append("shell")
        sends = op == "input" and inner.get("type") == "user" or op == "held" and msg.get("send") or \
            op == "run_task" or op == "tasks" and "set" in msg
        if sends and (meta.get("mode") not in SAFE_MODES or
                      any(t in SHELL_TOOLS for t in getattr(session, "always_allow", None) or ()) or
                      any(isinstance(t, str) and t.split("(")[0] in SHELL_TOOLS
                          for t in (meta.get("options") or {}).get("allowed_tools") or ())):
            caps.append("shell")  # (a chat that runs commands without asking: driving it is a shell)
        if (op == "bot" and msg.get("bot") is not None or op == "start" and (msg.get("bot") is not None or
                                                                            meta.get("bot")) or sends and meta.get("bot")):
            caps += ["shell", "browser_write"]
        return next((c for c in caps if not self.allowed(conn, c)), None)

    @staticmethod
    def asks_for_shell(session, answer):
        """Does answer (a control_response) allow what session's agent asked to run a command?"""
        response = answer.get("response") or {}
        asked = (getattr(session, "asks", None) or {}).get(response.get("request_id"))
        said = response.get("response") or {}
        tool = ((asked or {}).get("request") or {}).get("tool_name")
        return said.get("behavior") != "deny" and (asked is None or tool in SHELL_TOOLS)

    def refused(self, req, name):
        """access_denied's error for request req: rule name refused."""
        return {**wire.refused(wire.DENIED, req, named(name)), "denied": name}

    def set(self, name, rule):
        """Rule name set (rule: as the wire has it), its other side kept with it (kept_together), and kept: ValueError
        if it can't be."""
        if name not in RULES:
            raise ValueError(wording.say("access.bad_rule"))
        rule = clean(rule)
        from baudelib.serverstate import _write_json
        with self.lock:
            self.rules[name] = rule
            kept_together(self.rules, name)
            _write_json(self.path, {"rules": self.rules})

    def set_own(self, meta, name, rule):
        """A chat's own rule name (manage_read or manage_write) set in its meta (rule None: the host's again), its
        other side kept with it (read the host's again: its own write narrowed to it): ValueError if it can't be.
        Whatever's kept, it's checked as chat_rules has it."""
        if name not in rules_of("manage"):
            raise ValueError(wording.say("access.bad_rule"))
        own = own_rules(meta)
        if rule is None:
            own.pop(name, None)
            if name == "manage_read" and "manage_write" in own:
                with self.lock:
                    own["manage_write"] = narrower(own["manage_write"], self.rules["manage_read"])
        else:
            own[name] = clean(rule)
            with self.lock:
                kept_together(own, name, self.rules)
        if own:
            meta["access"] = own
        else:
            meta.pop("access", None)

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
                "editable": self.allowed(conn, "access_write")}
