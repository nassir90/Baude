"""hostfixes — Ongoing Feedback for a head elsewhere (a phone's), read and acted on with this host's gh as Baude's
Ongoing Feedback is: the feedback and where each stands (list), an image of a fix (shot), and what's done (merge,
discard, reopen, reply, fix, stop). No GTK here."""
import base64
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import fixes  # noqa: E402

GH_CHECKED_S = 60  # (where feedback is read from, asked of GitHub this seldom)
_seen = {"at": None, "repo": None}
_lock = threading.Lock()


def repo():
    """Where feedback is read from (owner/name): Baude's repository, or the private one for feedback if gh shows you
    that, if gh is there and signed in to GitHub; else None."""
    with _lock:
        now = time.monotonic()
        if _seen["at"] is not None and now - _seen["at"] < GH_CHECKED_S:
            return _seen["repo"]
        found = fixes.submit_issue_target() if fixes.gh_path() else None
        if found is not None and not fixes.gh("api", f"repos/{found}", "--silent", timeout=20)[0]:
            found = None
        _seen.update(at=now, repo=found)
        return found


def answer(server, msg):
    """The reply's fields (an error for a request that couldn't be done)."""
    what = msg.get("what")
    try:
        return {"what": what, **_do(what, msg)}
    except Exception as e:  # noqa: BLE001 (gh gone wrong, or a bad request: said)
        return {"what": what, "error": str(e) or type(e).__name__}


def _do(what, msg):
    item, found = msg.get("item"), repo()
    if what == "list":
        if found is None:
            return {"repo": None, "items": [], "can_write": False, "locations": []}
        try:
            data = fixes.load(found)
        except Exception as e:  # noqa: BLE001
            return {"repo": found, "error": str(e) or type(e).__name__}
        fixes.save_list(found, data)
        return {"repo": found, **data}
    if what == "shot":
        data = fixes.shot_bytes(msg.get("path"))
        if not data:
            return {"error": "no image"}
        return {"data": base64.b64encode(data).decode("ascii")}
    if what not in ("merge", "discard", "reopen", "reply", "fix", "stop"):
        raise ValueError(f"fixes doesn't take {what!r}")
    if not isinstance(item, dict) or "number" not in item:
        raise ValueError("fixes needs the issue, as list gave it")
    if found is None:
        raise ValueError("GitHub isn't connected")
    if what == "merge":
        fixes.merge(found, item)
    elif what == "discard":
        fixes.discard(found, item)
    elif what == "reopen":
        fixes.reopen(found, item)
    elif what == "reply":
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("reply needs its text")
        fixes.reply(found, item, text.strip())
    elif what == "fix":
        fixes.start_fix(fixes.fix_key(item))
    else:
        fixes.stop(fixes.fix_key(item))
    return {}
