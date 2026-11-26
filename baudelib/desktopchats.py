"""Claude Desktop's chats: its own list of them (desktop_index) and what it says of each (its title, what it came from,
when it was last used), and a host's listing of them (DesktopChats), so every head on the mesh sees a machine's Desktop
chats, in the Desktop group, and can read them and carry them on. No GTK: the headless server (baudelib.server) lists
them too."""
import json
import os
import sys
import time

from baudelib import transcripts, wording

LOOK_S = 5  # (Desktop's list read again this often, at most, while a head's attached)
LIVE_S = 60  # (a Desktop chat written this recently, not by the host: Claude Desktop's at it, livefeed's LIVE_SECONDS)
READS = frozenset({"attach", "detach", "transcript", "subagents", "senders", "stop", "chat_size"})  # (ops a chat listed only is
# read by, as it is: anything else takes it up first, takes_up)


def group():
    """The group every app lists Claude Desktop's chats in, apart from the rest and first among groups (its name, as
    each app says it)."""
    return wording.say("sidebar.desktop_group")


dirty = [False]  # (an entry read since sessionlist last kept them)


def desktop_parents(info):
    """The id of the session Claude Desktop shows a chat of its under, as a list ([] for none): the one that started it
    as a side session (spawned_from), or the one it was forked from (fork_of); apply_desktop_index sets one at most,
    as Desktop picks one (desktop_link); a host's listing of one says it as fork_from (DesktopChats.look). Where that
    one isn't listed, the chat is a top-level one."""
    return [i for i in (info.get("spawned_from"), info.get("fork_of"), info.get("fork_from")) if i]


_DESKTOP_LINKS = ("side_session", "hand_off", "suggested_task", "fork")


def desktop_link(entry):
    """(kind, local id) of the chat Claude Desktop nests one of its chats under, or (None, None), as its sidebar picks
    it: none once it's detached from it (lineageDetached); else its lineage; else the chat that started it as a side
    session (spawnedFrom, a side session's only); else the one it was forked from. kind is "fork" for a fork."""
    if entry.get("lineageDetached"):
        return None, None
    lineage = entry.get("lineage")
    if isinstance(lineage, dict) and isinstance(lineage.get("sessionId"), str) and lineage["sessionId"] \
            and lineage.get("kind") in _DESKTOP_LINKS and lineage.get("initiator") in ("claude", "user"):
        return lineage["kind"], lineage["sessionId"]
    spawned = entry.get("spawnedFrom") if isinstance(entry.get("spawnedFrom"), dict) else {}
    if spawned.get("sideSession") and isinstance(spawned.get("sessionId"), str):
        return "side_session", spawned["sessionId"]
    if isinstance(entry.get("forkedFromSessionId"), str):
        return "fork", entry["forkedFromSessionId"]
    return None, None


def desktop_used(entry):
    """When Claude Desktop says one of its chats was last used (what its sidebar's order and days go by), as a
    timestamp, or None: the last turn's start or end (lastTurnEdgeAt), else its last activity, else its creation."""
    ms = next((entry[k] for k in ("lastTurnEdgeAt", "lastActivityAt", "createdAt")
               if isinstance(entry.get(k), (int, float)) and entry[k] > 0), None)
    return ms / 1000 if ms is not None else None


def claude_desktop_dir():
    """The Claude desktop app's own folder: $XDG_CONFIG_HOME/Claude when that's set (Linux's way, and how the tests
    point it elsewhere), else ~/Library/Application Support/Claude on a Mac (where Electron keeps it), ~/.config/Claude
    elsewhere."""
    if os.environ.get("XDG_CONFIG_HOME"):
        return os.path.join(os.environ["XDG_CONFIG_HOME"], "Claude")
    return os.path.join(os.path.expanduser("~/Library/Application Support" if sys.platform == "darwin" else "~/.config"),
                        "Claude")


def desktop_index():
    """Claude Desktop's own list of its chats ({cli session id: entry}), from claude-code-sessions/*/*/local_*.json in
    its folder (claude_desktop_dir: on a Mac, in ~/Library/Application Support): title, archived, when it was last
    used, which earlier transcripts are copies of it (priorCliSessionIds) and what it came from. Empty without
    Desktop. Entries are cached by file mtime (they're small, but carry big tool lists; Baude keeps them with its session
    index: sessionlist.desktop_index)."""
    base = os.path.join(claude_desktop_dir(), "claude-code-sessions")
    out, archived = {}, set()
    try:
        accounts = os.listdir(base)
    except OSError:
        return out
    for acct in accounts:
        for org in (os.listdir(os.path.join(base, acct)) if os.path.isdir(os.path.join(base, acct)) else []):
            d = os.path.join(base, acct, org)
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for name in names:
                path = os.path.join(d, name)
                if name == "archived-sessions.idx":
                    entry = _desktop_file(path)
                    archived.update((entry or {}).get("archived") or [])
                elif name.startswith("local_") and name.endswith(".json"):
                    entry = _desktop_file(path)
                    if entry and entry.get("cliSessionId"):
                        out[entry["cliSessionId"]] = entry
    for e in out.values():
        e["_archived"] = bool(e.get("isArchived")) or e.get("sessionId") in archived
    return out


_desktop_cache = {}
# what's kept of each Desktop chat's file
_DESKTOP_KEYS = ("sessionId", "cliSessionId", "title", "worktreeName", "isArchived", "priorCliSessionIds",
                 "forkedFromSessionId", "spawnedFrom", "lineage", "lineageDetached", "lastTurnEdgeAt", "lastActivityAt",
                 "createdAt", "cwd", "archived")


def _desktop_file(path):
    try:
        mtime = os.path.getmtime(path)
        hit = _desktop_cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    entry = {k: raw[k] for k in _DESKTOP_KEYS if isinstance(raw, dict) and k in raw}
    _desktop_cache[path] = (mtime, entry)
    dirty[0] = True  # (kept with Baude's session index, sessionlist's: these files are big, the entries small)
    return entry


def transcript_paths(ids):
    """{id: its transcript} for the ids given that have one, from one look through every folder of Claude Code's
    (transcripts.projects_dirs), not one each."""
    out, ids = {}, set(ids)
    for root in transcripts.projects_dirs() if ids else ():
        try:
            folders = os.listdir(root)
        except OSError:
            continue
        for d in folders:
            try:
                names = os.listdir(os.path.join(root, d))
            except OSError:
                continue
            for name in names:
                if name.endswith(".jsonl") and name[:-6] in ids and name[:-6] not in out:
                    out[name[:-6]] = os.path.join(root, d, name)
    return out


def takes_up(msg):
    """Does what a head sends a Desktop chat the host only lists need it taken up first (DesktopChats.take_up)? A
    message written in it, a revert, a held message sent, or any op but those that read it (READS): yes. An initialize,
    a model or mode picked, a stop: answered from what's listed, nothing started."""
    op = msg.get("op")
    if op == "input":
        sent = msg.get("msg") or {}
        return sent.get("type") == "user" or sent.get("type") == "control_request" and \
            (sent.get("request") or {}).get("subtype") == "rewind_conversation"
    return op not in READS


class DesktopChats:
    """A host's Claude Desktop chats, listed to its heads beside its own sessions (Server.heads_listing): each one the
    host hasn't taken up is a Session listed only (listed_only: never kept, nothing of it run), with Desktop's title,
    folder and dates, read by the transcript op as any session is. Writing in one takes it up (take_up), as Baude's
    adopt does: claude --resume here from then on, still marked desktop. While Claude Desktop is writing one (elsewhere),
    every app keeps it read-only."""

    def __init__(self, server):
        self.server, self.chats, self.looked, self.seen = server, {}, None, None

    def look(self, force=False):
        """(lock held) Desktop's list read again, at most every LOOK_S unless forced: a Session for each of its chats with
        a transcript here that the host hasn't taken up; and each one's transcript looked at. True if what's listed of
        them changed (a Desktop chat the host has taken up too: Desktop writing it now or not, elsewhere)."""
        if not force and self.looked is not None and time.monotonic() - self.looked < LOOK_S:
            return False
        from baudelib.serverstate import Session  # (serverstate's Server imports this)
        self.looked = time.monotonic()
        index = desktop_index()
        local = {e.get("sessionId"): cli for cli, e in index.items()}
        wanted = {cli: e for cli, e in index.items() if cli not in self.server.sessions}
        paths = transcript_paths([cli for cli in wanted
                                  if cli not in self.chats or not os.path.exists(self.chats[cli].meta.get("path") or "")])
        chats = {}
        for cli, e in wanted.items():
            path = paths.get(cli) or (self.chats[cli].meta.get("path") if cli in self.chats else None)
            if not path or not os.path.exists(path):
                continue
            kind, up = desktop_link(e)
            created = e.get("createdAt") / 1000 if isinstance(e.get("createdAt"), (int, float)) else None
            meta = {"id": cli, "cwd": e.get("cwd") if isinstance(e.get("cwd"), str) else None, "path": path,
                    "title": e.get("title") or e.get("worktreeName") or None, "prompt": "",
                    "created": created, "said": desktop_used(e) or created, "archived": e["_archived"],
                    "fork_from": local.get(up) if up else None, "desktop": True, "listed_only": True,
                    "init": self.server.me.get("init") or {}}
            s = self.chats.get(cli)
            if s is None:
                s = Session(self.server, {**meta, "model": None})
            else:  # (its model, mode and effort as picked here before it's taken up: take_up's)
                s.meta.update(meta)
            try:
                st = os.stat(path)
                s.stat = (st.st_size, st.st_mtime)
            except OSError:
                s.stat = None
            chats[cli] = s
        for cli, s in self.chats.items():  # (gone from Desktop's list, or taken up: its viewers with it, if taken up)
            if cli not in chats and (kept := self.server.sessions.get(cli)) is not None:
                kept.viewers |= s.viewers
                kept.live |= s.live
        self.chats = chats
        seen = sorted((s.id, s.meta.get("title"), s.meta.get("archived"), s.meta.get("said"), elsewhere(s))
                      for s in [*chats.values(), *(s for s in self.server.sessions.values() if s.meta.get("desktop"))])
        changed, self.seen = seen != self.seen, seen
        return changed

    def listings(self, archived=False):
        """(lock held) Its chats as a head lists them (Session.listing, taken_up false), archived ones only if asked."""
        return [{**s.listing(), "taken_up": False} for s in self.chats.values() if archived or not s.meta.get("archived")]

    def take_up(self, sid):
        """(lock held) A Desktop chat the host only lists made one of the host's sessions (Server.adopt), in its folder,
        with Desktop's title, marked desktop; those viewing it go on viewing it. None if it isn't one; ValueError (said)
        if it can't be."""
        d = self.chats.get(sid)
        if d is None:
            return None
        s = self.server.adopt({"adopt": sid, "cwd": d.meta.get("cwd") or "~", "title": d.meta.get("title"),
                               "group": "", **{k: d.meta[k] for k in ("model", "mode", "effort") if d.meta.get(k)}})
        s.meta["desktop"] = True
        s.note_written()  # (as Desktop left it: what's written after it is the host's own, or another's: elsewhere)
        if d.meta.get("archived"):  # (archived in Desktop: archived here too)
            s.meta["archived"] = True
        s.viewers |= d.viewers
        s.live |= d.live
        s.always_allow |= d.always_allow
        s.env = s.env or d.env
        del self.chats[sid]
        self.server.save(s)
        self.server.changed()
        return s


class Elsewhere(ValueError):
    """A message for a Desktop chat refused: someone else is writing it now (elsewhere)."""


def restat(s):
    """(lock held) A Desktop chat's transcript's (size, mtime) as it is now, for elsewhere, not as last looked at."""
    try:
        st = os.stat(s.transcript() or "")
        s.stat = (st.st_size, st.st_mtime)
    except OSError:
        pass


def refuse_or_resync(s):
    """(lock held) Before a message is given a Desktop chat's agent: refused (Elsewhere, said) while someone else is
    writing it (elsewhere); and the host's own claude, idle, let go of if its transcript's grown since it last left it
    (written), so the next one --resumes it as it is now, not over what it doesn't know of."""
    if not s.meta.get("desktop"):
        return
    restat(s)
    if elsewhere(s):
        raise Elsewhere(wording.say("host_errors.active_elsewhere"))
    written = s.meta.get("written")
    if s.proc is not None and not s.busy and s.stat and written is not None and s.stat[0] > written:
        s.proc.stopping = s.proc.restarting = True  # (ensure_proc starts it again: those attached not told it went)


def elsewhere(s):
    """Is Claude Desktop writing a Desktop chat now (a host's Session marked desktop): written in the last LIVE_S, with
    no agent of the host's at a turn in it, and (one it's taken up) grown since its own agent last left it (written)?
    Every app keeps it read-only while it is (guard-only-desktop-chats)."""
    if not s.meta.get("desktop") or s.busy or s.asks or not s.stat:
        return False
    size, mtime = s.stat
    written = s.meta.get("written")
    return time.time() - mtime < LIVE_S and (written is None or size > written)
