"""Record's notebooks for an agent, with no GTK: their pages read, and edits to a page proposed for you to accept or
reject (propose_edit, list_edits, revise_edit, withdraw_edit), as Meta proposes them in Record. Record's Meta works
them out here (recordtools.RecordTools), and so does an agent outside the apps (baude_mcp, through a host process:
notes.edit): its edits are kept with the notebook's own messages, in Record's store, shown on their pages as Meta's
are."""
import re
import time
import uuid

HOME = "Home"  # (recordstore.HOME: the page every notebook has)
EDIT_TOOLS = ("propose_edit", "list_edits", "revise_edit", "withdraw_edit")

NOTEBOOK = {"type": "string", "description": "A notebook's name (list_notebooks)"}
HOST = {"type": "string", "description": "The host it's on, by its id or name (hosts); this one if none"}
SCHEMAS = {
    "list_notebooks": ("Record's notebooks on a host: each one's name and how many pages it has.", {"host": HOST}, []),
    "list_pages": ("A notebook's pages, Home first: each one's name (a page under another is Parent:Child) and how many "
                   "characters it has.", {"host": HOST, "notebook": NOTEBOOK}, ["notebook"]),
    "read_page": ("A page's text, in Markdown ([[Page]] links to another page), and the edits waiting on it.",
                  {"host": HOST, "notebook": NOTEBOOK,
                   "page": {"type": "string", "description": "Its name (list_pages)"}}, ["notebook", "page"]),
    "propose_edit": ("Propose a change to a page: old_text (copied exactly from the page, there once) replaced with "
                     "new_text; an empty old_text adds new_text at the page's end. The user sees it on the page in "
                     "Record and accepts or rejects it: nothing's written until they accept. A change to text one of "
                     "the edits waiting puts in or replaces revises that edit.",
                     {"host": HOST, "notebook": NOTEBOOK, "page": {"type": "string"},
                      "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                     ["notebook", "page", "new_text"]),
    "list_edits": ("The edits waiting for the user on a notebook's pages (yours, Meta's and other agents'), each as its "
                   "page has its passage now; with all, those accepted, rejected or withdrawn too.",
                   {"host": HOST, "notebook": NOTEBOOK, "page": {"type": "string"},
                    "all": {"type": "boolean", "description": "Decided ones too"}}, ["notebook"]),
    "revise_edit": ("Change an edit that's waiting: its new_text, and the passage it replaces if old_text is given. It "
                    "stays one edit.",
                    {"host": HOST, "notebook": NOTEBOOK, "id": {"type": "string", "description": "The edit's (list_edits)"},
                     "new_text": {"type": "string"}, "old_text": {"type": "string"}}, ["notebook", "id", "new_text"]),
    "withdraw_edit": ("Take back an edit that's waiting.",
                      {"host": HOST, "notebook": NOTEBOOK, "id": {"type": "string"}}, ["notebook", "id"]),
}


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

def pending_edits(notebook, messages=(), live=()):
    """The edits proposed to a notebook's pages that wait for you, in the order they were made (live, a reply still
    coming's, last): those kept with its own messages, and with messages (Meta's conversation's) whose notebook it
    is."""
    book_id = notebook.get("id")
    said = [*(notebook.get("messages") or ()), *messages]
    props = [p for m in said for p in m.get("proposals") or () if p.get("notebook", book_id) == book_id]
    return [p for p in props + [p for p in live if p not in props and p.get("notebook") == book_id]
            if p.get("status") == "pending"]


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
    return None, "old_text isn't in the page as it is now: copy it exactly from the page (Read its file)."


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
            return {"error": "That's in the passage another of your pending edits replaces: revise that one."}
        fields = {"old": old, "start": at}
    return {"new": new, "fields": fields}


def listed_edits(pending, pages, spans_of):
    """list_edits' list: the edits of pending on pages, page by page, each as its page has its passage now
    (spans_of(page): spans'); one whose passage isn't there any more, said so."""
    listed = []
    for page in pages:
        sp = spans_of(page)
        listed += [{"id": p["id"], "page": page, "old_text": old, "new_text": new, **_by(p)}
                   for _at, old, new, p in sorted(sp, key=lambda x: x[0])]
        placed = {id(x[3]) for x in sp}
        listed += [{"id": p["id"], "page": page, "old_text": p["old"], "new_text": p["new"], **_by(p),
                    "note": "what it replaces isn't on the page any more: revise its old_text, or withdraw it"}
                   for p in pending if p["page"] == page and id(p) not in placed]
    return listed


def _outside(error):
    """An error of place_change's as an agent outside Record reads it (its pages are no files to it)."""
    return error.replace("(Read its file)", "(read_page)")


def _by(p):
    return {"by": p["by"]} if p.get("by") else {}


# ---- the tools, on a host's notebooks (an agent outside the apps': notes.edit)

class Edits:
    """The edit tools on books (a host's notebooks: records.json's, or the Record running there's), with messages
    (Meta's conversation's: its edits waiting too) and live (those of a reply of Meta's still coming). view(notebook,
    page): (the page's text, placed) as a page open shows it, if one is (Record's); by: who's proposing."""

    def __init__(self, books, messages=(), live=(), view=None, by=None):
        self.books, self.messages, self.live, self.view, self.by = books, messages, live, view, by
        self.changed = set()  # (the notebooks changed: their ids)

    def pending(self, nb):
        return pending_edits(nb, self.messages, self.live)

    def _text(self, nb, page):
        got = self.view(nb, page) if self.view is not None else None
        return got if got is not None else (nb["pages"].get(page, ""), None)

    def _spans(self, nb, page):
        text, placed = self._text(nb, page)
        return text, spans(text, self.pending(nb), page, placed)

    def _page(self, nb, name):
        page = page_named(nb.get("pages") or {}, name)
        if page is None:
            raise ValueError(f"There's no page {name!r} in {full_name(nb)}. Its pages: "
                             f"{', '.join(sorted_pages(nb.get('pages') or {})[:40])}.")
        return page

    def call(self, tool, args):
        if tool not in EDIT_TOOLS:
            raise ValueError(f"no tool {tool}")
        nb = notebook_named(self.books, args.get("notebook"))
        return getattr(self, tool)(nb, args)

    def propose_edit(self, nb, args):
        page = self._page(nb, args.get("page"))
        text, sp = self._spans(nb, page)
        plan = plan_propose(text, sp, str(args.get("old_text") or ""), str(args.get("new_text") or ""))
        if "error" in plan:
            raise ValueError(_outside(plan["error"]))
        self.changed.add(nb["id"])
        if "add" in plan:
            start, old, new = plan["add"]
            p = {"id": uuid.uuid4().hex[:10], "notebook": nb["id"], "page": page, "old": old, "new": new,
                 "start": start, "status": "pending", "t": time.time(), **({"by": self.by} if self.by else {})}
            nb.setdefault("messages", []).append({"role": "assistant", "text": "", "t": p["t"], "proposals": [p],
                                                  **({"by": self.by} if self.by else {})})
            return {"id": p["id"], "page": page, "status": "pending",
                    "text": f"Edit {p['id']} is shown on the page {page} in Record, for the user to accept or reject. "
                            "Nothing is written until they accept; list_edits with all says which."}
        edits, start, old, new = plan["merge"]
        for p in edits[1:]:
            p["status"] = "withdrawn"
        first = edits[0]
        first.update(old=old, new=new, start=start)
        if first["old"] == first["new"]:
            first["status"] = "withdrawn"
            return {"id": first["id"], "status": "withdrawn",
                    "text": f"That puts back what's on the page: edit {first['id']} is taken back."}
        joined = f" (and took in {', '.join(p['id'] for p in edits[1:])}, now taken back)" if edits[1:] else ""
        return {"id": first["id"], "status": "pending",
                "text": f"Revised the pending edit {first['id']}{joined}: it replaces {old!r} with {new!r} now, still "
                        "waiting for the user."}

    def list_edits(self, nb, args):
        waiting = self.pending(nb)
        pages = sorted({p["page"] for p in waiting})
        if args.get("page"):
            page = self._page(nb, args["page"])
            pages = [page] if page in pages else []
        out = {"notebook": full_name(nb), "waiting": listed_edits(waiting, pages, lambda pg: self._spans(nb, pg)[1])}
        if args.get("all"):
            said = [*(nb.get("messages") or ()), *self.messages]
            out["decided"] = [{"id": p["id"], "page": p["page"], "status": p.get("status"), "old_text": p["old"],
                               "new_text": p["new"], **_by(p)}
                              for m in said for p in m.get("proposals") or ()
                              if p.get("notebook", nb["id"]) == nb["id"] and p.get("status") != "pending" and
                              (not args.get("page") or p["page"] == args["page"])][-50:]
        return out

    def _pending_named(self, nb, args):
        p = next((p for p in self.pending(nb) if p["id"] == str(args.get("id") or "")), None)
        if p is None:
            raise ValueError(f"No edit with the id {args.get('id')!r} is waiting: list_edits shows those that are.")
        return p

    def revise_edit(self, nb, args):
        p = self._pending_named(nb, args)
        text, sp = self._spans(nb, p["page"])
        plan = plan_revise(p, args, text, sp)
        if "error" in plan:
            raise ValueError(_outside(plan["error"]))
        self.changed.add(nb["id"])
        p.update(new=plan["new"], **plan["fields"])
        if p["old"] == p["new"]:
            p["status"] = "withdrawn"
            return {"id": p["id"], "status": "withdrawn",
                    "text": f"That puts back what's on the page: edit {p['id']} is taken back."}
        return {"id": p["id"], "status": "pending", "text": f"Edit {p['id']} revised, still waiting for the user."}

    def withdraw_edit(self, nb, args):
        p = self._pending_named(nb, args)
        self.changed.add(nb["id"])
        p["status"] = "withdrawn"
        return {"id": p["id"], "status": "withdrawn", "text": f"Edit {p['id']} taken back."}


# ---- reading, from a host's listing (notes.listing's notebooks: id, name, pages)

def notebooks_listed(books):
    return [{"name": full_name(t), "pages": len(t.get("pages") or {})} for t in books]


def pages_listed(nb):
    pages = nb.get("pages") or {}
    return {"notebook": full_name(nb), "pages": [{"page": p, "chars": len(pages[p])} for p in sorted_pages(pages)]}


def page_read(nb, name, waiting=()):
    page = page_named(nb.get("pages") or {}, name)
    if page is None:
        raise ValueError(f"There's no page {name!r} in {full_name(nb)}: list_pages lists them.")
    return {"notebook": full_name(nb), "page": page, "text": nb["pages"][page],
            "edits_waiting": [{"id": e["id"], "old_text": e["old_text"], "new_text": e["new_text"], **_by(e)}
                              for e in waiting if e["page"] == page]}
