"""Edits proposed to the pages of Record's notebooks, on the notebooks' host, with no GTK: proposed, revised, taken
back, listed, and accepted or rejected (Edits), as the host process does them for its wire messages (edit_propose,
edit_revise, edit_withdraw, edit_decide, edits: notes.edit_op). An edit belongs to its notebook, on its host: whoever
proposes it (Record's Meta, an agent through baude-mcp) and whatever Record shows it (on that machine or another)
asks the host. Nothing here writes a page: an edit is accepted by the one who puts its text in the page (Record)."""
import re
import time
import uuid

HOME = "Home"  # (recordstore.HOME: the page every notebook has)
STATUSES = ("pending", "accepted", "rejected", "withdrawn")
DECIDED_KEPT = 200  # (decided edits kept on a host, the last made)


def base_name(text):
    return re.sub(r"\s+", " ", str(text or "").strip())


def full_name(notebook):
    return base_name(notebook.get("name")) or "Untitled"


def short_page(name):
    return name.rpartition(":")[2] or name


def page_named(pages, name):
    """The page of pages ({name: text}) a name means: its name, any case, or its own name past its parents'; or
    None."""
    name = str(name or "").strip()
    if name in pages:
        return name
    for test in (lambda p: p.lower() == name.lower(), lambda p: short_page(p).lower() == name.lower()):
        found = [p for p in pages if test(p)]
        if len(found) == 1:
            return found[0]
    return None


def notebook_named(books, name):
    """The notebook of books a name (or id) means, any case; or ValueError."""
    name = base_name(name)
    found = next((t for t in books if t.get("id") == name), None) or \
        next((t for t in books if full_name(t).lower() == name.lower()), None)
    if found is None:
        raise ValueError(f"There's no notebook {name!r}: list_notebooks lists them.")
    return found


def sorted_pages(pages):
    return sorted(pages, key=lambda p: (p != HOME, p.lower()))


# ---- edits as a page reads them

def staged(text, edits):
    """A page's text as it reads with edits applied: (that text, its pieces). edits: [(start, old, new, edit)], in the
    page's own places; one that overlaps one before it is left out. At one place an insertion (old empty) comes before
    a replacement, and edits otherwise in the order they were made. A piece: (its start and end in the staged text,
    its start and end in the page's, the edit it's of or None)."""
    out, pieces, at, pos = [], [], 0, 0
    order = lambda x: (x[0], bool(x[1]), x[3].get("t", 0) if isinstance(x[3], dict) else 0)  # noqa: E731
    for start, old, new, edit in sorted(edits, key=order):
        if start < at:
            continue
        if start > at:
            out.append(text[at:start])
            pieces.append((pos, pos + start - at, at, start, None))
            pos += start - at
        out.append(new)
        pieces.append((pos, pos + len(new), start, start + len(old), edit))
        pos, at = pos + len(new), start + len(old)
    out.append(text[at:])
    pieces.append((pos, pos + len(text) - at, at, len(text), None))
    return "".join(out), pieces


def staged_at(pieces, at):
    """A place in the page's own text as one in its staged text (in a passage an edit replaces: that edit's start)."""
    for ss, se, ks, ke, edit in pieces:
        if ks <= at <= ke:
            return ss + (at - ks) if edit is None else ss
    return at


def kept_at(pieces, at, pick=min):
    """A place in the staged text as one in the page's own (where an edit's new text starts or ends: its passage's
    start or end). Where it's more than one (an edit that puts nothing in is all of its passage at one place): pick's
    of them, min the first, max the last."""
    places = []
    for ss, se, ks, ke, edit in pieces:
        if edit is None and ss <= at <= se:
            places.append(ks + (at - ss))
        elif edit is not None and at in (ss, se):
            places += [ks] if at == ss else []
            places += [ke] if at == se else []
    return pick(places) if places else None


def place_change(text, old, near=None):
    """Where a proposed change goes in a page's text: (offset, the text it replaces), or (None, why not). old there once,
    or, if more than once, the one nearest near (where the selection was); with its spaces either side left out, if it's
    there so and not as given. An empty old: the page's end."""
    if not old:
        return len(text), ""
    for want in dict.fromkeys((old, old.strip())):
        if not want:
            continue
        at = [m.start() for m in re.finditer(re.escape(want), text)]
        if len(at) == 1 or at and near is not None:
            return min(at, key=lambda i: abs(i - near)) if near is not None else at[0], want
        if len(at) > 1:
            return None, f"old_text is in the page {len(at)} times: give more of what's around it, so it's there once."
    return None, "old_text isn't in the page as it is now: copy it exactly from the page as it is now."


def spans(text, pending, page, placed=None):
    """The edits waiting on a page (of pending), where each is in its text now: [(start, old, new, edit)] (one whose
    passage isn't on the page as it was: left out). placed(edit): where a page open shows it, or None."""
    out = []
    for p in pending:
        if p["page"] != page:
            continue
        at = placed(p) if placed is not None else None
        if at is None and not p["old"]:
            at = len(text)
        elif at is None:
            at = p.get("start", 0) if text[p.get("start", 0):p.get("start", 0) + len(p["old"])] == p["old"] else \
                place_change(text, p["old"], p.get("start"))[0]
        if at is not None:
            out.append((at, p["old"], p["new"], p))
    return out


def plan_propose(text, spans_, old, new, near=None):
    """What propose_edit does to a page (text, with the edits waiting on it, spans_), its old text found once (or
    nearest near, a place in text) in the page as it reads with them applied (staged). Text only in such an edit's new
    text, or in what it replaces: that edit revised to take this in, one edit for the passage they all cover (the
    others in it taken back); else a new one. {error}, {add: (start, old, new)} or {merge: (edits, start, old, new)}."""
    if old == new:
        return {"error": "new_text is old_text: that changes nothing."}
    view, pieces = staged(text, spans_)
    if not old:
        if view and not view.endswith("\n") and not new.startswith("\n"):
            new = "\n" + new  # (added at the end, after what's there and what's waiting: on a line of its own)
        return {"add": (len(text), "", new)}
    at, found = place_change(view, old, staged_at(pieces, near) if near is not None else None)
    if at is None and any(want and want in view for want in (old, old.strip())):
        return {"error": found}  # (in the page as the edits have it, more than once)
    if at is None:  # (not in the page as the edits have it: text one of them replaces, perhaps)
        k, kept_found = place_change(text, old, near)
        if k is None:
            return {"error": found}
        end = k + len(kept_found)
        hit = [x for x in spans_ if x[0] < end and x[0] + len(x[1]) > k]
        if not hit:
            return {"add": (k, kept_found, new)}
        k0, k1 = min(k, hit[0][0]), max(end, max(x[0] + len(x[1]) for x in hit))
        return {"merge": ([x[3] for x in hit], k0, text[k0:k1], text[k0:k] + new + text[end:k1])}
    end = at + len(found)
    hit = [piece for piece in pieces if piece[4] is not None and
           (piece[0] < end and piece[1] > at or piece[0] == piece[1] and at < piece[0] < end)]
    if not hit:  # (none of a pending edit's passage in it: one of its own)
        k0, k1 = kept_at(pieces, at, max), kept_at(pieces, end, min)
        return {"add": (k0, text[k0:k1], new)}
    s0, s1 = min(at, hit[0][0]), max(end, hit[-1][1])  # (all of theirs in it, and none of another's)
    k0 = hit[0][2] if s0 == hit[0][0] else kept_at(pieces, at, max)
    k1 = hit[-1][3] if s1 == hit[-1][1] else kept_at(pieces, end, min)
    return {"merge": ([piece[4] for piece in hit], k0, text[k0:k1], view[s0:at] + new + view[end:s1])}


def plan_revise(p, args, text, spans_):
    """What revise_edit does to a pending edit p (on a page whose text, with the edits waiting on it, are text and
    spans_): {error}, or {new: its new text, fields: what else of it changes (old, start)}."""
    new, fields = str(args.get("new_text") or ""), {}
    if not p["old"] and not ("old_text" in args and args["old_text"]) and not new.startswith("\n"):
        before = staged(text, [x for x in spans_ if x[3] is not p and (x[1] or x[3].get("t", 0) < p.get("t", 0))])[0]
        if before and not before.endswith("\n"):  # (an addition: after what's there and what's waiting before it)
            new = "\n" + new
    if "old_text" in args and str(args["old_text"]) != p["old"]:
        near = next((x[0] for x in spans_ if x[3] is p), p.get("start"))
        at, old = place_change(text, str(args["old_text"]), near)
        if at is None:
            return {"error": old}
        if any(x[3] is not p and x[0] < at + len(old) and x[0] + len(x[1]) > at for x in spans_):
            return {"error": "That's in the passage another pending edit replaces: revise that one."}
        fields = {"old": old, "start": at}
    return {"new": new, "fields": fields}




# ---- a host's edits (the host process's, kept in edits.json beside records.json: notes.edit_op)

def _text(msg, key):
    """A field of a request that's text, or None if it's not given (or given as null)."""
    v = msg.get(key)
    return None if v is None else str(v)


def _who(by):
    return " ".join(str(by or "").split())[:80] or None


class Edits:
    """The edits proposed to a host's notebooks (edits: its list, changed in place) on books (its notebooks as
    records.json has them: id, name, pages). changed: whether edits was."""

    def __init__(self, edits, books):
        self.edits, self.books, self.changed = edits, books, False

    def _book(self, msg):
        return notebook_named(self.books, msg.get("notebook"))

    def _page(self, nb, name):
        page = page_named(nb.get("pages") or {}, name)
        if page is None:
            raise ValueError(f"There's no page {name!r} in {full_name(nb)}. Its pages: "
                             f"{', '.join(sorted_pages(nb.get('pages') or {})[:40])}.")
        return page

    def waiting(self, nb):
        return [e for e in self.edits if e.get("notebook") == nb["id"] and e.get("status") == "pending"]

    def _spans(self, nb, page):
        text = (nb.get("pages") or {}).get(page, "")
        return text, spans(text, self.waiting(nb), page)

    def _waiting_named(self, nb, msg):
        e = next((e for e in self.waiting(nb) if e["id"] == str(msg.get("id") or "")), None)
        if e is None:
            raise ValueError(f"No edit with the id {msg.get('id')!r} is waiting in {full_name(nb)}.")
        return e

    def _done(self, e, took_in=()):
        """An edit made or changed: what edit_* answers ({edit, took_in?}); one that now puts back what's on the page,
        taken back."""
        self.changed = True
        if e["status"] == "pending" and e["old"] == e["new"]:
            e["status"] = "withdrawn"
        self._prune()
        return {"edit": dict(e), **({"took_in": list(took_in)} if took_in else {})}

    def _prune(self):
        decided = [e for e in self.edits if e.get("status") != "pending"]
        for e in decided[:max(0, len(decided) - DECIDED_KEPT)]:
            self.edits.remove(e)

    def propose(self, msg):
        """edit_propose: old_text replaced with new_text on a page, found once (or nearest near) in the page as it reads
        with the edits waiting on it applied; text one of them puts in or replaces: that one revised to take this in
        (the others it reaches taken back), by whoever proposed this; else a new edit."""
        nb = self._book(msg)
        page = self._page(nb, msg.get("page"))
        text, sp = self._spans(nb, page)
        near = msg.get("near") if isinstance(msg.get("near"), int) else None
        plan = plan_propose(text, sp, _text(msg, "old_text") or "", _text(msg, "new_text") or "", near)
        if "error" in plan:
            raise ValueError(plan["error"])
        by = _who(msg.get("by"))
        if "add" in plan:
            start, old, new = plan["add"]
            e = {"id": uuid.uuid4().hex[:10], "notebook": nb["id"], "page": page, "old": old, "new": new,
                 "start": start, "status": "pending", "t": time.time(), **({"by": by} if by else {})}
            self.edits.append(e)
            return self._done(e)
        edits, start, old, new = plan["merge"]
        for other in edits[1:]:
            other["status"] = "withdrawn"
        first = edits[0]
        first.update(old=old, new=new, start=start)
        if by:  # (it's what this proposer made it now: theirs; one who doesn't say who they are leaves it whose it was)
            first["by"] = by
        return self._done(first, [other["id"] for other in edits[1:]])

    def revise(self, msg):
        """edit_revise: a waiting edit's new text, and the passage it replaces if old_text's given; it stays one edit,
        by whoever revised it."""
        nb = self._book(msg)
        e = self._waiting_named(nb, msg)
        text, sp = self._spans(nb, e["page"])
        args = {"new_text": _text(msg, "new_text") or ""}
        if _text(msg, "old_text") is not None:
            args["old_text"] = _text(msg, "old_text")
        plan = plan_revise(e, args, text, sp)
        if "error" in plan:
            raise ValueError(plan["error"])
        e.update(new=plan["new"], **plan["fields"])
        if by := _who(msg.get("by")):  # (theirs now; one who doesn't say who they are leaves it whose it was)
            e["by"] = by
        return self._done(e)

    def withdraw(self, msg):
        """edit_withdraw: a waiting edit taken back."""
        e = self._waiting_named(self._book(msg), msg)
        e["status"] = "withdrawn"
        return self._done(e)

    def decide(self, msg):
        """edit_decide: a waiting edit accepted (its text put in the page by whoever asks: their own notes write, before
        this) or rejected."""
        e = self._waiting_named(self._book(msg), msg)
        e["status"] = "accepted" if msg.get("accept") else "rejected"
        return self._done(e)

    def adopt(self, msg):
        """edit_adopt: edits waiting that another machine's Record kept for this host's notebooks (Meta's, from before
        they were the host's), taken in as they are, with their ids; one whose id is here already, or whose notebook
        isn't, left out. {edits: those taken in}."""
        have, ids, out = {e.get("id") for e in self.edits}, {b["id"] for b in self.books}, []
        for e in msg.get("edits") or ():
            if not isinstance(e, dict) or e.get("id") in have or e.get("notebook") not in ids or \
                    not isinstance(e.get("page"), str) or e.get("status", "pending") != "pending":
                continue
            have.add(e["id"])
            out.append({"id": str(e["id"]), "notebook": e["notebook"], "page": e["page"],
                        "old": str(e.get("old") or ""), "new": str(e.get("new") or ""),
                        "start": int(e.get("start") or 0), "status": "pending",
                        "t": float(e.get("t") or time.time()), "by": _who(e.get("by")) or "Meta"})
        if out:
            self.edits += out
            self.changed = True
        return {"edits": [dict(e) for e in out]}

    def listed(self, msg):
        """edits: the edits waiting on a host's notebooks (one notebook's, one page's, if given), page by page, each as
        its page has its passage now (gone: it isn't there as it was); with all, the decided ones kept too."""
        books = [self._book(msg)] if msg.get("notebook") else [b for b in self.books if b.get("id")]
        out, decided = [], []
        for nb in books:
            waiting = self.waiting(nb)
            page = self._page(nb, msg["page"]) if msg.get("page") else None
            for pg in sorted_pages({e["page"] for e in waiting if page is None or e["page"] == page}):
                sp = self._spans(nb, pg)[1]
                out += [{**p, "old": old, "start": at} for at, old, _new, p in sorted(sp, key=lambda x: x[0])]
                placed = {id(x[3]) for x in sp}
                out += [{**p, "gone": True} for p in waiting if p["page"] == pg and id(p) not in placed]
            if msg.get("all"):
                decided += [dict(e) for e in self.edits if e.get("notebook") == nb["id"] and
                            e.get("status") != "pending" and (page is None or e["page"] == page)]
        return {"edits": out, **({"decided": decided} if msg.get("all") else {})}


def imported(saved):
    """The edits waiting in records.json (saved: its JSON), from before they were the host's: those Meta kept with its
    conversation and with a notebook's own messages, on this machine's notebooks; by Meta, if they say no one else."""
    books = [t for t in saved.get("records") or () if isinstance(t, dict) and t.get("id")]
    ids, out, seen = {t["id"] for t in books}, [], set()
    meta = saved.get("meta") if isinstance(saved.get("meta"), dict) else {}
    for nb_id, messages in [(t["id"], t.get("messages") or ()) for t in books] + [(None, meta.get("messages") or ())]:
        for m in messages:
            for p in (m.get("proposals") or ()) if isinstance(m, dict) else ():
                if not isinstance(p, dict) or p.get("status") != "pending" or p.get("id") in seen:
                    continue
                book_id = p.get("notebook", nb_id)
                if book_id not in ids or not isinstance(p.get("page"), str):
                    continue
                seen.add(p.get("id"))
                out.append({"id": str(p["id"]), "notebook": book_id, "page": p["page"], "old": str(p.get("old") or ""),
                            "new": str(p.get("new") or ""), "start": int(p.get("start") or 0), "status": "pending",
                            "t": float(p.get("t") or time.time()), "by": _who(p.get("by")) or "Meta"})
    return out
