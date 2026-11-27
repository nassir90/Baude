"""hostqueue — a chat's queue, kept by its host (ChatQueue): the messages written while its turn runs, the same on
every head that opens the chat, on any machine, as its draft is (draft_save). A head adds what it queues, drops what
it sends, edits or deletes, and names the one whose send-now stops the turn (queue, on the chat's own connection);
every change is told the other heads attached that may write it (queue_changed), as attach's answer tells one
attaching.

Its host leads it: only the host sends it, so a queue every head shows is sent once, whoever's there or not. As a
turn ends it sends it all as one message (up to the one a send-now named, the rest kept), unless a revert stopped the
turn: then it's kept till the next turn's end, or a send-now. A message queued as no turn's going goes at once, with
what's before it. What's sent is told the heads attached live as any message given the agent is (tell_said).
"""
import json
import time
import uuid

from baudelib import wire


def given(s, writes):
    """(lock held) The queue as attach's answer gives it: to a head that may write the chat only."""
    return s.meta.get("queue") if writes else None


def kept(s):
    q = s.meta.get("queue")
    return q if isinstance(q, dict) else {"turns": [], "upto": None, "rev": 0}


def tell(server, s, conn=None):
    """(lock held) The queue as it is now told every head attached that may write it, but conn (whose answer says
    it)."""
    q = kept(s)
    for v in list(s.viewers):
        if v is not conn and server.access.allowed(v, "manage_write", s):
            v.send({"op": "queue_changed", "session": s.id, "queue": q})


def put(server, s, turns, upto, conn=None):
    """(lock held) s's queue kept as turns, its revision one more, and told."""
    q = kept(s)
    s.meta["queue"] = {"turns": turns, "upto": upto if any(t["id"] == upto for t in turns) else None,
                       "rev": int(q.get("rev") or 0) + 1}
    server.save(s)
    tell(server, s, conn)


def change(server, conn, s, msg):
    """(lock held) queue: what msg adds, drops and names kept; sent at once if no turn's going (send); the reply's op
    and fields."""
    if s.id not in server.sessions:  # (a Claude Desktop chat only listed: Baude's own, on this machine, keeps its)
        return "error", {"error": f"no session {s.id}"}
    q = kept(s)
    dropped = {i for i in msg.get("drop") or () if isinstance(i, str)}
    turns = [t for t in q["turns"] if t["id"] not in dropped]
    have = {t["id"] for t in turns} | dropped
    added = [{"id": t["id"], "text": t["text"], "content": t["content"], "at": time.time()}
             for t in msg.get("add") or () if isinstance(t, dict) and wire.is_id(t.get("id") or "") and
             t["id"] not in have and isinstance(t.get("text"), str) and isinstance(t.get("content"), (str, list))]
    turns += added
    if added and len(json.dumps(turns)) > wire.CONSTANTS["QUEUE_CHARS_MAX"]:
        return "error", {"error": wire.SAID[wire.INVALID], "code": wire.INVALID}
    upto = msg["upto"] if isinstance(msg.get("upto"), str) else q.get("upto")
    if turns != q["turns"] or upto != q.get("upto"):
        put(server, s, turns, upto, conn)
    if not s.busy and (added and not getattr(s, "queue_left", False) or isinstance(msg.get("upto"), str)):
        send(server, s, kept(s).get("upto"))  # (none going: at once)
    return "queue", {"session": s.id, "queue": kept(s)}


def asked(s, sub):
    """(lock held) A control request a head sent the chat's agent: a revert made mid-turn keeps the queue past the
    turn it stops."""
    if sub == "rewind_conversation" and s.busy:
        s.queue_held = True


def turn_over(server, s, proc):
    """(lock held, the turn's result told) The queue sent as a turn ends, unless a revert stopped it (kept, till the
    next turn's end or a send-now), or the next turn's begun already, or the chat's going."""
    held, s.queue_held = getattr(s, "queue_held", False), False
    s.queue_left = held and bool(kept(s)["turns"])
    if held or s.busy or server.stopped.is_set() or s.deleting or getattr(proc, "stopping", False):
        return
    if kept(s)["turns"]:
        send(server, s, kept(s).get("upto"))


def exited(server, s):
    """(lock held) A chat's agent gone mid-turn (stopped outright, or failed): the queue sent as at a turn's end."""
    held, s.queue_held = getattr(s, "queue_held", False), False
    s.queue_left = held and bool(kept(s)["turns"])
    if not held and kept(s)["turns"]:
        send(server, s, kept(s).get("upto"))


def content(turns):
    """turns' content as one message's: their pictures and documents first, then their texts joined by a blank line,
    as a head joins them."""
    blocks, texts = [], []
    for t in turns:
        c = t["content"]
        if isinstance(c, str):
            texts.append(c)
            continue
        blocks += [b for b in c if isinstance(b, dict) and b.get("type") != "text"]
        texts += [b["text"] for b in c
                  if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    text = "\n\n".join(t for t in texts if t)
    if not blocks:
        return text
    return blocks + ([{"type": "text", "text": text}] if text else [])


def send(server, s, upto=None):
    """(lock held) The queue sent to the chat's agent as one message, up to upto (all of it: None), the rest kept;
    put back as it was if it can't be."""
    q = kept(s)
    turns = q["turns"]
    n = next((i + 1 for i, t in enumerate(turns) if t["id"] == upto), len(turns))
    going, rest = turns[:n], turns[n:]
    if not going:
        return
    s.queue_left = False
    said = going[0]["id"] if len(going) == 1 else str(uuid.uuid4())  # (alone: by its own id, taken once)
    put(server, s, rest, None)
    try:
        server.take_input(None, s, {"type": "user", "uuid": said,
                                    "message": {"role": "user", "content": content(going)}})
    except Exception as e:  # (its agent couldn't be started, or a Desktop chat written elsewhere: kept, to go again)
        server.log(f"{s.id}: its queue not sent: {type(e).__name__}: {e}")
        put(server, s, going + kept(s)["turns"], None)
