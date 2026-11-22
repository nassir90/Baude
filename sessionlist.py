"""sessionlist — the sessions listed: transcripts found and folded (list_sessions, fork_tree, Claude Desktop's),
their info and its cache, groups and folders (session_group, main_folder), a chat's claude (ClaudeProc, RemoteProc,
background_proc), the network (network, remote_info, bot_picture, chat_groups, host_key) and git checkouts.
"""
import base64
import json
import os
import re
import sys
import threading
import time

from baudelib import claude as claudelib  # (its ClaudeProc: Baude's is it, on the GTK main loop)
from baudelib.transcripts import _pack_keys, _scan_line, _scan_state, _unpack_keys, projects_dir, scan_session
import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import models as modelslib  # noqa: E402  (what runs a model: claude, apple-claude or pi-claude)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from lookupgit import _git

from gi.repository import Gdk, Gio, GLib  # noqa: E402

import update  # noqa: E402  (a new version from git, restarted onto once idle: Record's too)
import meshui  # noqa: E402
from widgets import load_settings, pretty_path  # noqa: E402,F401


# --------------------------------------------------------------- sessions --
# (where Claude Code keeps transcripts, and reading one: baudelib.transcripts)


def collapse_copies(sessions, keep=()):
    """Drop stale copies of a conversation. When Claude Desktop continues a session it starts a
    new one and copies the whole history into it, leaving the old file behind: the sidebar showed
    "Baude GTK harness prototype" four times. An older session counts as a copy when a newer one
    in the same folder has the same title and holds at least 95% of its records (the last few
    differ) — or the newer one is itself 95% made of the older's records (Desktop sometimes copies
    only the history since the last compaction). Forks keep their own title ("... (fork)"), so they stay. Sessions in `keep` (the
    open one) are never dropped, nor is a chat Claude Desktop lists for another it lists (a side session of the same
    title, say: Desktop names a chat's copies itself, see apply_desktop_index). Survivors get "copies": how many were
    folded into them."""
    kept, newest_first = [], sorted(sessions, key=lambda i: i["mtime"], reverse=True)
    for info in newest_first:
        mine = record_ids(info)
        owner = None
        if info["id"] not in keep and sum(map(len, mine)) >= 5:
            for newer in kept:
                if info.get("desktop") and newer.get("desktop") and info["id"] != newer["id"]:
                    continue  # (two chats Claude Desktop lists are two chats: it says which are copies itself)
                theirs = record_ids(newer)
                if newer.get("title") == info.get("title") and newer.get("cwd") == info.get("cwd") \
                        and shared_records(mine, theirs) >= 0.95 * min(sum(map(len, mine)), sum(map(len, theirs))):
                    owner = newer
                    break
        if owner is None:
            kept.append({**info, "copies": info.get("copies", 0), "copy_paths": list(info.get("copy_paths", []))})
        else:
            owner["copies"] += 1
            owner["copy_paths"].append(info["path"])
            # the survivor stands for the whole conversation (where it forked from, when it began)
            fold_records(owner, info)
            owner["copied_from"] = (owner.get("copied_from") or frozenset()) | (info.get("copied_from") or frozenset())
            if info.get("born") and (not owner.get("born") or info["born"] < owner["born"]):
                owner["born"] = info["born"]
    return kept


def record_ids(info):
    """A session's record ids (record_key) as disjoint frozensets: its own, and the ones copies folded into it
    add (fold_records). The survivor of copies stands for the whole conversation without a union of them all:
    those were sets as big as the transcripts', made again on every sidebar refresh."""
    return [ids for ids in (info.get("uuids"), info.get("more_uuids")) if ids]


def shared_records(a, b):
    """How many records two record_ids have in common."""
    if len(a) == 1 == len(b):
        return len(a[0] & b[0])
    return sum(len(x & y) for x in a for y in b)


def fold_records(owner, copy):
    """Take a copy's records into the session that stands for it: those it doesn't have itself (the last few
    of a continued conversation's copy, or what a copy made since a compaction leaves out)."""
    own = owner.get("uuids") or frozenset()
    owner["more_uuids"] = frozenset().union(owner.get("more_uuids") or (), *(ids - own for ids in record_ids(copy)))


def desktop_parents(info):
    """The id of the session Claude Desktop shows a chat of its under, as a list ([] for none): the one that started it
    as a side session (spawned_from), or the one it was forked from (fork_of); apply_desktop_index sets one at most,
    as Desktop picks one (desktop_link). Where that one isn't listed, the chat is a top-level one."""
    return [i for i in (info.get("spawned_from"), info.get("fork_of")) if i]


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


_FORK_SUFFIX = re.compile(r"^(.*\S)\s+\(([^()]+)\)$")


def fork_tree(items):
    """[(session, depth, is_last_sibling)] with forks under the session they came from.
    Nothing in a transcript says "forked from", but a fork starts as a copy of its parent's history:
    a session is a fork of the one it shares the most records with (10+) among those older than it.
    Older: the CLI's --fork-session copies keep the parent's sessionId; otherwise the earlier first
    timestamp; on a tie, Claude Desktop's naming ("<title> (fork)", or renamed "(Slug Colission)"). A Desktop
    chat is under the one Desktop says it came from (desktop_parents). A fork named "<parent's title> (...)" shows
    just the "..."."""
    def older(a, b):  # is a the elder of the pair?
        if a["id"] in (b.get("copied_from") or ()):
            return True
        if b["id"] in (a.get("copied_from") or ()):
            return False
        if a.get("born") and b.get("born") and a["born"] != b["born"]:
            return a["born"] < b["born"]
        m = _FORK_SUFFIX.match(b.get("title") or "")
        return bool(m) and m.group(1) == a.get("title")
    # (the open session has no id until its first message is sent: no one's parent, not even an unforked Desktop
    # chat's, whose fork_of is None too)
    parent, by_id, ids = {}, {i["id"]: i for i in items if i["id"]}, [record_ids(i) for i in items]
    for info, mine in zip(items, ids):
        if info.get("desktop"):  # Claude Desktop knows what each of its chats came from
            if (up := next((i for i in desktop_parents(info) if i in by_id), None)) is not None:
                parent[info["id"]] = by_id[up]
            continue
        if info.get("fork_from") or info.get("spawned_by"):  # (a host's fork, or a chat a Bot started: its host
            came = info.get("fork_from") or info.get("spawned_by")  # says what it came from)
            up = next((c for c in items if c is not info and came in (c["id"], c.get("cli_id"))), None)
            if up is not None:
                parent[info["id"]] = up
            continue
        best, shared = None, 9
        if sum(map(len, mine)) <= shared:
            continue  # (too few records to share 10 with anything)
        for cand, theirs in zip(items, ids):
            if cand is info or not older(cand, info):  # (before counting: half the pairs need no count)
                continue
            n = shared_records(mine, theirs)
            if n > shared:
                best, shared = cand, n
        if best is not None:
            parent[info["id"]] = best
    for info in items:  # a loop (shouldn't happen) is cut where it closes
        seen, cur = {info["id"]}, parent.get(info["id"])
        while cur is not None:
            if cur["id"] in seen:
                parent.pop(info["id"], None)
                break
            seen.add(cur["id"])
            cur = parent.get(cur["id"])
    for info in items:
        info.pop("fork_label", None)
        if (p := parent.get(info["id"])) is not None:
            m = _FORK_SUFFIX.match(info.get("title") or "")
            if m and m.group(1) == p.get("title"):
                info["fork_label"] = m.group(2)
    parent = {k: v["id"] for k, v in parent.items()}
    for info in items:
        info["fork_parent"] = parent.get(info["id"])
    children = {}
    for info in items:
        if info["id"] in parent:
            children.setdefault(parent[info["id"]], []).append(info)
    out = []

    def place(info, depth, last):
        out.append((info, depth, last))
        kids = children.get(info["id"], [])
        for i, kid in enumerate(kids):
            place(kid, depth + 1, i == len(kids) - 1)
    for info in items:
        if info["id"] not in parent:
            place(info, 0, True)
    return out


def desktop_index():
    """Claude Desktop's own list of its chats ({cli session id: entry}), from claude-code-sessions/*/*/local_*.json in
    its folder (claude_desktop_dir: on a Mac, in ~/Library/Application Support): title, archived, when it was last
    used, which earlier transcripts are copies of it (priorCliSessionIds) and what it came from. Empty without
    Desktop. Entries are cached by file mtime (they're small, but carry big tool lists)."""
    base = os.path.join(claude_desktop_dir(), "claude-code-sessions")
    with _info_lock:
        _load_info_cache()
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
    _info_dirty[0] = True  # (kept with the session index: these files are big, the entries small)
    return entry


def apply_desktop_index(sessions, index, keep=()):
    """Show Claude Desktop's chats the way Desktop does. A Desktop transcript its index doesn't list is
    gone from Desktop (deleted, or a stray from a test run with Desktop's environment): hidden. One
    it lists as an earlier copy of a chat folds into that chat. Listed chats take Desktop's title,
    archived state, when it was last used (desktop_used) and the one chat Desktop shows them under (desktop_link): the
    one that started them as a side session (spawned_from) or the one they were forked from (fork_of). Sessions from
    elsewhere (terminal, baude) are left alone."""
    if not index:
        return sessions
    prior = {p: cli for cli, e in index.items() for p in e.get("priorCliSessionIds") or ()}
    local = {e.get("sessionId"): cli for cli, e in index.items()}
    out, owners = [], {}
    for info in sessions:
        e = index.get(info["id"])
        if e is not None:
            kind, up = desktop_link(e)
            up = local.get(up) if up else None
            info = {**info, "desktop": True, "title": e.get("title") or e.get("worktreeName") or info.get("title"),
                    "desktop_archived": e["_archived"], "desktop_used": desktop_used(e),
                    "fork_of": up if kind == "fork" else None, "spawned_from": up if kind != "fork" else None}
            owners[info["id"]] = info
            out.append(info)
        elif info["id"] in prior and info["id"] not in keep:
            info["_copy_of"] = prior[info["id"]]
            out.append(info)
        elif info.get("source") == "Claude Desktop" and info["id"] not in keep:
            continue
        else:
            out.append(info)
    result = []
    for info in out:
        owner = owners.get(info.pop("_copy_of", None)) if "_copy_of" in info else None
        if owner is None:
            result.append(info)
            continue
        owner["copies"] = owner.get("copies", 0) + 1
        owner["copy_paths"] = owner.get("copy_paths", []) + [info["path"]]
        fold_records(owner, info)
        if info.get("born") and (not owner.get("born") or info["born"] < owner["born"]):
            owner["born"] = info["born"]
    return result


def list_sessions(progress=None):
    """All resumable sessions, newest first. Files are read newest first, and progress(so_far) is
    called (soon, then every 1.5 s) while unread ones remain: the first ever start reads hundreds of MB."""
    root = projects_dir()
    paths = []
    try:
        folders = os.listdir(root)
    except OSError:
        return []
    for folder in folders:
        try:
            names = os.listdir(os.path.join(root, folder))
        except OSError:
            continue
        for name in names:
            if name.endswith(".jsonl"):
                path = os.path.join(root, folder, name)
                try:
                    paths.append((os.path.getmtime(path), path))
                except OSError:
                    pass
    out, reported, shown = [], time.monotonic(), False
    for _mtime, path in sorted(paths, reverse=True):
        info = cached_session_info(path)
        if info:
            out.append(info)
        if progress is not None and time.monotonic() - reported > (0.4 if not shown else 1.5):
            reported, shown = time.monotonic(), True  # (often enough to see it come, not to slow it)
            progress(sorted(out, key=last_used, reverse=True))
    out.sort(key=last_used, reverse=True)
    return out


def last_used(info):
    """When a listed chat was last used: the last thing typed in it (scan_session's said), for the open one not saved
    yet now; for a Claude Desktop chat, when Desktop last saw it used (desktop_used, what Desktop's sidebar goes by),
    unless it was typed in since (carried on in a terminal, or here). What the sidebar's order and its days go by."""
    said = info.get("said") or info.get("mtime") or 0.0
    return max(said, info["desktop_used"]) if info.get("desktop_used") else said


_info_cache = {}  # path -> (inode, size, mtime, scan state, info); None -> loaded from disk yet
_info_lock = threading.Lock()


def cached_session_info(path):
    """read_session_info, remembered: an unchanged file isn't read again, a grown one (sessions are
    only ever appended to) is read from where it was left, and the memory survives restarts."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    with _info_lock:
        _load_info_cache()
        hit = _info_cache.get(path)
    if hit and hit[:3] == (st.st_ino, st.st_size, st.st_mtime):
        return hit[4]
    state = None
    if hit and hit[0] == st.st_ino and st.st_size > hit[1]:  # appended to: carry on from there
        state = hit[3]
    state, info = scan_session(path, state)
    with _info_lock:
        _info_cache[path] = (st.st_ino, st.st_size, st.st_mtime, state, info)
        _info_dirty[0] = True
    return info


def session_cost(path):
    """What claude had spent on a session when it last exited (its last cost-state), as cached_session_info says: from
    it while it has the transcript as it is, or has only what was appended since to read; else from the transcript's
    end back to that record (a chat opened before the session index has read it: read whole, a 160 MB one took 0.26 s
    of opening it). 0.0 for none."""
    try:
        st = os.stat(path)
    except OSError:
        return 0.0
    with _info_lock:
        _load_info_cache()
        hit = _info_cache.get(path)
    if hit and (hit[:3] == (st.st_ino, st.st_size, st.st_mtime) or hit[0] == st.st_ino and st.st_size > hit[1]):
        return (cached_session_info(path) or {}).get("cost") or 0.0
    sid = os.path.splitext(os.path.basename(path))[0]
    try:
        with open(path, "rb") as f:
            at, carry = st.st_size, b""
            while at > 0:
                start = max(0, at - (1 << 20))
                f.seek(start)
                data = f.read(at - start) + carry
                whole = data.find(b"\n") + 1 if start else 0  # (the line it begins in is read with the block before)
                carry, at = data[:whole], start
                hit = data.rfind(b'"cost-state"', whole)
                while hit >= 0:
                    begin, stop = data.rfind(b"\n", 0, hit) + 1, data.find(b"\n", hit)
                    if stop >= 0:  # (a whole line, as scan_session reads them: not one still being written)
                        tally = {**_scan_state(), "cost": None}
                        _scan_line(tally, str(memoryview(data)[begin:stop], "utf-8", "replace"), sid)
                        if tally["cost"] is not None:
                            return tally["cost"]
                    hit = data.rfind(b'"cost-state"', whole, begin)
    except OSError:
        pass
    return 0.0


_info_dirty = [False]
_INFO_CACHE_VERSION = 6  # (4: record ids as packed record_keys; 5: cost-state's cost; 6: when last typed in)


def _info_cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "baude", "sessions.json")


def _load_info_cache():
    """(caller holds _info_lock) Once per run: what was read last time, from disk."""
    if _info_cache.get(None) is not None:
        return
    _info_cache[None] = True  # (loaded marker)
    try:
        with open(_info_cache_path(), encoding="utf-8") as f:
            raw = json.load(f)
        if raw.get("version") == _INFO_CACHE_VERSION:  # (another version's files are read again: not the rest, the
            seen = {}                                   # worktrees seen above all, which can be gone since)
            for path, (ino, size, mtime, state, info) in raw["files"].items():
                state["uuids"], state["foreign"] = _unpack_keys(state["uuids"], seen), frozenset(state["foreign"].split())
                if info is not None:  # (its ids are the state's: stored once, and shared in memory too)
                    info["uuids"], info["copied_from"] = state["uuids"], state["foreign"]
                _info_cache[path] = (ino, size, mtime, state, info)
        if raw.get("desktop_keys") == list(_DESKTOP_KEYS):  # (entries kept with other keys are read again)
            for path, (mtime, entry) in raw.get("desktop", {}).items():
                _desktop_cache.setdefault(path, (mtime, entry))
        for path, seen in raw.get("worktrees", {}).items():  # (see main_folder)
            _WORKTREES_SEEN.setdefault(path, seen)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass


_info_saved = [0.0]


def save_info_cache(force=False):
    """Write the session index to disk if it changed (files that are gone are dropped). At most
    every 30 s unless forced (on exit): a live session changes it constantly, and encoding a few
    MB holds the interpreter lock, which the UI thread needs too."""
    if not force and time.monotonic() - _info_saved[0] < 30:
        return
    with _info_lock:
        if not _info_dirty[0]:
            return
        _load_info_cache()  # (a worktree learned before the index was read doesn't write over it)
        _info_dirty[0] = False
        _info_saved[0] = time.monotonic()
        files = {}
        for path, entry in _info_cache.items():
            if path is None or not os.path.exists(path):
                continue
            ino, size, mtime, state, info = entry
            files[path] = [ino, size, mtime,
                           {**state, "uuids": _pack_keys(state["uuids"]), "foreign": " ".join(state["foreign"])},
                           None if info is None else {k: v for k, v in info.items()
                                                      if k not in ("uuids", "copied_from")}]
        desktop = {p: list(v) for p, v in _desktop_cache.items() if os.path.exists(p)}
        worktrees = dict(_WORKTREES_SEEN)
    target = _info_cache_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        data = json.dumps({"version": _INFO_CACHE_VERSION, "files": files, "desktop": desktop,
                           "desktop_keys": _DESKTOP_KEYS, "worktrees": worktrees},
                          separators=(",", ":"))  # (dumps: the C encoder; dump() to a file isn't)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, target)
    except OSError:
        pass


# where Claude Desktop makes its "No folder" chats' folders: in its own folder (claude_desktop_dir), a Mac's and
# Linux's
SCRATCH_MARKS = tuple(os.sep + os.path.join(*parts, "Claude", "scratch-workspaces") + os.sep
                      for parts in ((".config",), ("Library", "Application Support")))


def is_scratch(path):
    """Is path in one of Claude Desktop's per-chat scratch folders (SCRATCH_MARKS)?"""
    return any(mark in path for mark in SCRATCH_MARKS)


def session_group(cwd):
    """(group key, label) for a session's folder; Claude Desktop's per-chat scratch folders
    are one group, like its "No folder" chats."""
    if not cwd:
        return "?", wording.say("sidebar.unknown_folder")
    if is_scratch(cwd):
        return "scratch", wording.say("chats.no_folder_desktop")
    return cwd, pretty_path(cwd)


def short_group_label(key, label):
    """A group's name as its header shows it: a folder's last part, the rest as they are."""
    if key in ("scratch", "?", "archived") or key.startswith("date:"):
        return label
    return os.path.basename(label.rstrip("/")) or label


def date_group(mtime, now):
    """(key, label) of a chat's day by its last activity, in local time, as Claude Desktop's sidebar has them: "Today",
    "Yesterday", each of the 5 days before ("Oct 20"), else "Older". Keys say how many days ago (what's collapsed stays
    as the days move on)."""
    import datetime
    day = datetime.date.fromtimestamp(mtime)
    ago = (datetime.date.fromtimestamp(now) - day).days
    if ago <= 0:  # (a clock that's behind: today)
        return "date:0", wording.say("sidebar.today")
    if ago == 1:
        return "date:1", wording.say("sidebar.yesterday")
    return (f"date:{ago}", f"{day:%b} {day.day}") if ago <= 6 else ("date:older", wording.say("sidebar.older"))


def next_midnight(now):
    """The next local midnight after now (a timestamp), summer time and all."""
    import datetime
    day = datetime.date.fromtimestamp(now) + datetime.timedelta(days=1)
    return datetime.datetime.combine(day, datetime.time()).timestamp()


WORKTREES_MARK = os.sep + os.path.join(".claude", "worktrees") + os.sep  # where Claude Code and Desktop make theirs
_MAINS = {}           # folder -> main_folder's answer (this run)
_WORKTREES_SEEN = {}  # a worktree's folder -> [its main checkout, its top folder], kept with the session index


def main_folder(path):
    """(the folder a chat that ran in path belongs to, the top folder of the worktree it ran in, or None). A git
    worktree, a sibling made with `git worktree add` or the .claude/worktrees/<name> Claude Code and Claude Desktop
    make in a repository, isn't a folder of its own: its chats belong to the repository's main checkout (the parent of
    its common git folder, else `git worktree list`'s first; a bare repository's worktrees stay themselves), and a
    folder in a worktree to the same folder there. One that's gone is known from when it was last seen, or by its
    .claude/worktrees/ path; else it is its own folder, like a plain one. git runs once per folder, and only in one
    whose nearest .git is a file (a worktree's or a submodule's), so hundreds of sessions cost a few stats."""
    if not path or is_scratch(path):
        return path, None
    if (hit := _MAINS.get(path)) is not None:
        return hit
    found = (path, None)
    if os.path.isdir(path):
        up = path
        while not os.path.lexists(os.path.join(up, ".git")) and os.path.dirname(up) != up:
            up = os.path.dirname(up)
        if os.path.isfile(os.path.join(up, ".git")) and (co := git_checkout(path)) is not None:
            top, common = co
            main = os.path.dirname(common) if os.path.basename(common) == ".git" else None
            if main is None:  # (a separate git folder, or a bare repository: no main checkout there)
                first = (_git(path, "worktree", "list", "--porcelain") or "").split("\n\n")[0].splitlines()
                if first and first[0].startswith("worktree ") and "bare" not in first:
                    main = os.path.realpath(first[0][9:])
            if main and main != top:
                found = (os.path.normpath(os.path.join(main, os.path.relpath(os.path.realpath(path), top))), top)
                if _WORKTREES_SEEN.get(path) != [found[0], top]:
                    with _info_lock:
                        _WORKTREES_SEEN[path] = [found[0], top]
                        _info_dirty[0] = True
    else:
        with _info_lock:
            _load_info_cache()
        if (seen := _WORKTREES_SEEN.get(path)) is not None:
            found = tuple(seen)
        elif WORKTREES_MARK in path:
            repo, _, rest = path.partition(WORKTREES_MARK)
            name, _, sub = rest.partition(os.sep)
            found = (os.path.join(repo, sub) if sub else repo, repo + WORKTREES_MARK + name)
    _MAINS[path] = found
    return found


def place_sessions(sessions):
    """Each session's "folder", the one it's listed under (main_folder: a worktree's chat is its repository's), and
    "worktree", the worktree it ran in, if it did. Run off the main loop: git runs here, the sidebar only reads."""
    for info in sessions:
        info["folder"], info["worktree"] = main_folder(info.get("cwd"))
    return sessions


def turn_said(msg):
    """A turn's result (its stream-json result message), for a notification: what it said last, or that it ended."""
    said = (msg.get("result") or "").strip()
    if msg.get("is_error"):
        return wording.say("meta.ended_error", error=said or msg.get("subtype") or wording.say("meta.error_unknown"))
    return said or wording.say("meta.finished")


def background_proc(session_id, window):
    """The claude a Baude window (a window: BaudeWindow, handed in) keeps at a turn for a chat left mid-turn
    (BaudeWindow.detach_proc), if any."""
    app = Gio.Application.get_default()
    for w in app.get_windows() if app is not None and session_id else []:
        if isinstance(w, window) and session_id in w.background:
            return w.background[session_id]
    return None


class ClaudeProc(claudelib.ClaudeProc):
    """Owns the claude subprocess (baudelib.claude's). Callbacks are delivered on the GTK main loop."""

    dispatch = staticmethod(GLib.idle_add)


class RemoteProc(baudelib.client.RemoteProc):
    """A session on a server on the network, talked to as a ClaudeProc's claude is (baudelib.client's), its callbacks
    on the GTK main loop too."""

    dispatch = staticmethod(GLib.idle_add)


def on_main(fn):
    """Run fn on the GTK main loop, once (whatever it returns)."""
    GLib.idle_add(lambda: fn() and False)


_NETWORK = []
_LOCAL_SERVER = {"wanted": None}
_HOST_APART = []  # (meshui.HostApart: this Mac's host process on other code than Baude's, said in the banner)  # (local_server's: what waits for the server on this machine it's started)


def network():
    """The hosts Baude knows, and their sessions (baudelib.client.Network): one for the app, started when it's first
    wanted: this machine's host process, by its socket, and the hosts given by where ssh goes (Add Host…, and
    BAUDE_HOSTS=dest,…). None is looked for on the network."""
    if not _NETWORK:
        _NETWORK.append(baudelib.client.Network(
            on_main, hosts=[a for a in os.environ.get("BAUDE_HOSTS", "").split(",") if a.strip()]))
    return _NETWORK[0]


def remote_info(s):
    """A session on the network (Network.sessions) as the sidebar and resume_session take a chat: its transcript's
    mirror here (baudelib.client.Remote.mirror) as its path."""
    r = s["server"]
    when = s.get("said") or s.get("created") or 0.0
    return {"id": s["id"], "path": r.mirror(s["id"]), "title": s.get("title"), "cwd": s.get("cwd"),
            "folder": s.get("cwd"), "worktree": None, "mtime": s.get("updated") or when, "said": when,
            "prompts": s.get("prompts") or 0, "source": f"{r.info.get('name') or r.host}, on the network", "copies": 0,
            # (this machine's host process, asleep: its sessions' claudes went with it, whatever was last listed)
            "remote": r, "remote_state": "stopped" if r.mine and r.state != "alive" else s.get("state"),
            "dead": r.state == "dead" and not r.mine, "off": r.deactivated,  # (off: its host's deactivated)
            "group": s.get("group"), "model": s.get("model"), "effort": s.get("effort"), "mode": s.get("mode"),
            "archived": bool(s.get("archived")), "bot": s.get("bot"),
            "fork_from": s.get("fork_from"), "cli_id": s.get("cli_id"), "spawned_by": s.get("spawned_by"),
            "mesh_tools": bool(s.get("mesh_tools")),
            "held": len(s.get("held") or [])}  # (messages its host holds for it, to send or drop: HeldTurn)


BOT_PICTURES = {}  # (session id, its picture's mtime) -> Gdk.Texture: a Bot's picture, fetched from its host once
BOT_FETCHING = {}  # (the same) -> [then]: those waiting for a picture asked of its host, not come yet


def bot_picture(info, then):
    """A Bot's picture (its host's: op bot_picture), as a texture: then(texture), or then(None) if it has none (or
    it couldn't be got: asked again next time). Asked once while it's coming, whoever wants it."""
    bot, r = info.get("bot") or {}, info.get("remote")
    if not bot.get("picture") or r is None:
        return then(None)
    key = (info["id"], bot["picture"])
    if key in BOT_PICTURES:
        return then(BOT_PICTURES[key])
    if key in BOT_FETCHING:
        return BOT_FETCHING[key].append(then)
    BOT_FETCHING[key] = [then]

    def got(reply):
        tex = None
        if reply.get("picture"):
            try:
                tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(base64.b64decode(reply["picture"])))
            except (GLib.Error, ValueError):
                tex = None
        if tex is not None:
            BOT_PICTURES[key] = tex
        for fn in BOT_FETCHING.pop(key, []):
            fn(tex)
    r.request({"op": "bot_picture", "session": info["id"]}, got)


def can_be_bot(info):
    """Can a chat be made a Bot (or is one, its settings changeable): one on a host that's there and takes bot, on a
    model whose program's told who it is (claude's, pi's), not a fork (a Bot's a top-level chat)."""
    r = info.get("remote")
    return r is not None and not info.get("dead") and r.state == "alive" and baudelib.wire.takes(r.info, "bot") and \
        modelslib.model_program(info.get("model")) in ("claude", "pi")


def chat_groups(st=None):
    """{session id: the group a chat's been put in, "" for none}: settings.json's chat_groups (st: the settings, read
    already)."""
    found = (load_settings() if st is None else st).get("chat_groups")
    return {k: v for k, v in found.items() if isinstance(v, str)} if isinstance(found, dict) else {}


def made_groups(st=None):
    """The groups made (New Group…: SessionSidebar.make_group), empty or not: settings.json's group_names."""
    found = (load_settings() if st is None else st).get("group_names")
    return [g for g in found if isinstance(g, str) and g] if isinstance(found, list) else []


def chat_group(info, assigned=None):
    """A chat's group (⋮ → Groups), "" for none: Baude's own, whatever host the chat is on. The one it's been put in
    (assigned: chat_groups, read if not given); else, for a host's session (remote_info's, or Network.sessions' own),
    the one it was started in (baude_oneshot.py --group), unless that's only its host's own."""
    if (put := (chat_groups() if assigned is None else assigned).get(info.get("id"))) is not None:
        return put
    r = info.get("remote") or info.get("server")
    if r is None or not info.get("group") or info["group"] == r.info.get("group"):
        return ""
    return info["group"]


def remote_name(r):
    return r.label


def stopping_said(r):
    """What a host said as it stopped (Remote.said_stopping), as its banner says it while it's gone, or None."""
    return None if r is None else r.said_stopping("Daemon" if r.mine else remote_name(r))


def local_host():
    """This machine's host process (its socket: Remote.mine), if it's there, which runs every chat of Baude's."""
    return next((r for r in network().alive() if r.mine), None)


def host_apart(app):
    """The app's meshui.HostApart, made as it's first wanted: what it says, in every window's banner."""
    if not _HOST_APART:
        _HOST_APART.append(meshui.HostApart(app, update.STARTED, lambda: [w.update_banner() for w in app.windows()
                                                                          if not getattr(w, "let_go_done", False)]))
    return _HOST_APART[0]


def is_desktop(session_id):
    """Is a chat Claude Desktop's (in its records: desktop_index)? Those stay outside the host process."""
    return bool(session_id) and session_id in desktop_index()


HERE_HOST = "here"  # (New session's hosts: this machine's key; another's is its server's)
THIS_MACHINE = wording.say("senders.local")  # (this machine, as every host is written: by its name, which here is local)


def host_key(r):
    """The host a server's sessions are listed under (New session's hosts): this machine, for a server on it, as its
    own chats are; else the server's own."""
    return HERE_HOST if baudelib.client.is_here(r.info) else r.key


def remote_path(path, r):
    """A folder on a server as people say it there: its home folder is ~ (this machine's own: pretty_path)."""
    if baudelib.client.is_here(r.info):
        return pretty_path(path)
    home = (r.info.get("home") or "").rstrip("/")
    if home and path and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


_CHECKOUTS = {}


def git_checkout(path):
    """(the checkout's top folder, its repository's common git folder) for a folder in a git checkout, else None.
    A worktree has its own top folder and its main checkout's common folder."""
    if path not in _CHECKOUTS:
        out = _git(path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir") or ""
        parts = out.split("\n")
        _CHECKOUTS[path] = (os.path.realpath(parts[0]), os.path.realpath(parts[1])) if len(parts) >= 2 and parts[1] \
            else None
    return _CHECKOUTS[path]
