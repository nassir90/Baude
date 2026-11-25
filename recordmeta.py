"""recordmeta — what Meta, in Record, is told and proposes: its prompt and tools (RECORD_PROMPT, RECORD_TOOLS),
what a notebook, a page, an @-referenced thing or a quoted passage says to it, and its proposed edits as a page reads
them (pending_edits: as the notebook's host keeps them; staged and place_change are baudelib.notebooktools').
"""
import codecs
import os
import re

from baudelib.notes import data_dir  # noqa: E402,F401
from baudelib import notebooktools  # noqa: E402  (what a page's edits are, as their notebook's host works them out)
from baudelib.notebooktools import kept_at, place_change, staged, staged_at  # noqa: E402,F401
import meta  # noqa: E402  (Meta, the helper in the right bar: Baude's too, beside this file)
from recordstore import HOME, QUOTE_LIMIT, REF_RE, full_name, page_paths, plain_refs, short_page
from recordui import chat_text


# ---------------------------------------------------------------- markdown → Pango

_name_link, _inline, md_markup, set_md = meta._name_link, meta._inline, meta.md_markup, meta.set_md


def paned_grip(paned):
    """paned, its rule picked before its end child (as Baude's paned_grip): over the 6px either side of the rule that a
    press drags it from, the pointer is the resize arrows, the end child's side too."""
    ends = (paned.get_start_child(), paned.get_end_child())
    handle = paned.get_first_child()
    while handle is not None and handle in ends:
        handle = handle.get_next_sibling()
    if handle is not None and ends[1] is not None:
        handle.insert_after(paned, ends[1])
    return paned


# ---------------------------------------------------------------- Meta, in Record
# Record's helper is Meta (meta.py, Baude's too), in the right bar: its conversation is the window's (RecordChat, a
# metachat.MetaChat), whichever notebook's open, one session kept for it, and a new one (another chat's folder, Record
# started again, another model) told what was said before; told too when another notebook's opened. It reads (Read,
# Grep, Glob) the notebook open's pages, as their files (Store.mirror_pages), and the folder of the chat read
# (read_folders). A change to a page's text it proposes with propose_edit (RecordChat.propose),
# which the page shows as a diff, to accept or reject (DiaryEditor.show_proposal); the pages themselves it adds,
# deletes and moves at once, as actions, marked in the pages' list till they're kept or undone (PagesList.fill).

RECORD_PROMPT = """The user keeps notebooks of pages in Markdown ([[Page]] links a page to another; a page's name is \
its path, Work:Plans being Plans under Work; @name is a page, a notebook, a file, a chat or anything else the \
folder of the chat they're reading has). You see the notebook they have open: its pages, that folder, and what they \
@-reference or select; with none open, the notebooks there are, and only add_notebook, add_page and move_pages \
(naming its notebooks) change anything. The conversation stays as they open another notebook: you're told when they \
do, and your tools work on the one open then. Read, Grep and Glob read the notebook's pages (each one a file, \
read-only: a page is changed with your tools, never with Edit, Write or a command, and nothing's added beside them) \
and that folder.

The notebooks are on hosts, the user's machines: local is this one. A place is host / notebook / page, and you're \
told where the user is (Open: host / notebook / page) as it changes. The left bar lists the picked host's notebooks; \
your tools work on any host's: the tools that name a notebook take its host too (left out: the one picked), and a \
host's notebooks are listed for you as a tool first names it. To take the user to another host, show button:host \
(items: its name) for them to press.

You never write a page's text yourself. To change it (fill a passage in, rewrite one, add to it), call propose_edit \
with the page, the exact text it replaces and what replaces it: the user sees the edit on the page, and accepts or \
rejects it. When they've selected a passage on a page and ask you to fill it in or change it, the text it replaces is \
that selection, exactly. Write in the page's Markdown and its voice, and only what was asked. Don't announce, \
describe or sum up an edit: the user sees it. You'll be told whether they accepted it.

Your edits stay pending, across turns and sessions, until the user accepts or rejects them: a draft over the page, \
which they may keep writing in meanwhile. Work on that draft. Others may have edits waiting on a page too (another \
agent's, named by who proposed it): list_edits shows every edit pending, each with who proposed it (by: you, or their \
name); only yours are yours. propose_edit's old_text is found in the page as it reads with the pending edits applied, \
so a change to text one of them puts in revises that edit (yours then), and one to text an edit replaces replaces \
it, rather than adding a second; revise_edit changes an edit outright and withdraw_edit takes one back. The page's \
file has none of the pending edits in it.

The pages themselves you organise with add_page, delete_page and move_pages (under another page, or from one \
notebook into another), and the notebooks with add_notebook, which makes one as add_page makes a page: each changes \
them at once, marked in the pages' or the notebooks' list. All you change of the pages in one \
turn is one change for the user, undone as one: so do one goal (moving pages and fixing what refers to them) in one \
turn. Links to the pages you move follow by themselves; edit_page changes a page's text at once, as part of that \
change (what still says a page's old place, say), where propose_edit's draft waits for the user to accept it. \
highlight_pages marks pages without changing them, to show which you mean.

What the user @-references comes with their message: its text, or where to read it. Use it and the conversation as \
what you know of the subject, and read more with your tools when that helps. Don't make facts up: where something \
isn't known, say so, or leave a clear TODO in what you propose.

The User knows quite a fair bit about computer science as-it-was. C++, CLI, HTTP, HTTPS, QUIC, TDD etc. all of \
these acronyms are known. Where things get more complicated are terms such as "golden files" etc., pretentious \
terms used to obfuscate something very simple. I.e. golden files are really just "expected outputs", however \
some people would prefer, to the great misfortune of the onboarding engineer, to use the term "golden files". \
Things like that often come up that the {name} needs to understand is outside of the glossary.

When they ask what a passage from a chat they're reading means, you're their peer, not an expert: explain it through \
their notes (their glossary: what they already understand, in their own words), everyday language and computer \
science basics, reusing their wording; never explain or flag standard computer science. Say the simple thing a piece \
of in-group jargon means if the context makes it clear ("golden files" = expected outputs). If the passage needs an \
idea that's in none of those, say so plainly and give your best guess, marked as a guess. Terms under a "{to_learn}" \
heading are words they've heard but don't understand yet: don't explain through them. A passage's project names come \
with what each one is: those are known, not jargon. Keep it to a few short sentences, and end that reply with exactly \
one last line: "Unknown: " then the jargon and ideas from the passage that are neither computer science basics nor in \
their notes, separated by semicolons, each a short noun phrase, or "Unknown: none". Only a reply explaining a passage \
ends so.

Talk like a peer."""

# Record's own tools, as Claude Code sees them (metaagent.SHARED_TOOLS are Baude's too); RecordChat answers them
RECORD_TOOLS = [
    {"name": "propose_edit",
     "description": "Propose a change to a page of the user's notes, in the notebook open. The user sees it on the "
     "page as a diff and accepts or rejects it; nothing is written until they accept. old_text is the text that "
     "new_text replaces, copied exactly (whitespace and Markdown too), and found once in the page as it reads with "
     "the pending edits applied (list_edits): give more of what's around it if it's there more than once. Text one "
     "of the pending edits puts in, or text it replaces: that edit is revised to take this in, not a second one made. "
     "To fill in or change a passage the user selected, old_text is that selection. An empty old_text adds new_text "
     "at the page's end. Returns the edit's id, or why nothing was shown.",
     "inputSchema": {"type": "object", "required": ["page", "old_text", "new_text"], "properties": {
         "page": {"type": "string", "description": "the page's name (Spec, or Spec:Auth for a sub-page) or its file"},
         "old_text": {"type": "string"}, "new_text": {"type": "string"}}}},
    {"name": "list_edits",
     "description": "The edits still waiting for the user to accept or reject, in the notebook open (or on one "
     "page), in page order: each one's id, page, old_text (what it replaces, as the page has it now), new_text and by (who proposed it: you, or their name).",
     "inputSchema": {"type": "object", "properties": {
         "page": {"type": "string", "description": "only this page's (its name or its file)"}}}},
    {"name": "revise_edit",
     "description": "Change a pending edit (list_edits): new_text is what it puts in now; old_text, if "
     "given, what it replaces now (found once in the page, not in another pending edit's passage). It stays one "
     "edit, waiting for the user. new_text the same as what it replaces takes it back.",
     "inputSchema": {"type": "object", "required": ["id", "new_text"], "properties": {
         "id": {"type": "string"}, "new_text": {"type": "string"}, "old_text": {"type": "string"}}}},
    {"name": "withdraw_edit",
     "description": "Take back a pending edit (list_edits): it goes from the page, the page as it is.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
    {"name": "add_page",
     "description": "Add a page to a notebook, with its text (Markdown). A sub-page's name is its parent's "
     "and its own: Work:Plans. notebook: the one it goes in (left out: the one open).",
     "inputSchema": {"type": "object", "required": ["page"], "properties": {
         "page": {"type": "string"}, "text": {"type": "string"}, "notebook": {"type": "string"},
         "host": {"type": "string", "description": "the host the notebook's on (left out: the one picked)"}}}},
    {"name": "delete_page",
     "description": "Delete a page (not Home); its sub-pages stay. notebook and host: where it is (left out: the one "
     "open).",
     "inputSchema": {"type": "object", "required": ["page"], "properties": {
         "page": {"type": "string"}, "notebook": {"type": "string"}, "host": {"type": "string", "description": "the host the notebook's on (left out: the one picked)"}}}},
    {"name": "move_pages",
     "description": "Move pages, each with its sub-pages, under another page (under: its name; empty: the top), "
     "keeping their own names; links to them follow. from_notebook: the notebook they're in, to_notebook: the one "
     "they go to (either left out: the one open).",
     "inputSchema": {"type": "object", "required": ["pages", "under"], "properties": {
         "pages": {"type": "array", "items": {"type": "string"}}, "under": {"type": "string"},
         "from_notebook": {"type": "string"}, "to_notebook": {"type": "string"},
         "from_host": {"type": "string", "description": "the host from_notebook's on (left out: host's, or the one "
                       "picked)"},
         "to_host": {"type": "string", "description": "the host to_notebook's on (left out: host's, or the one picked)"},
         "host": {"type": "string", "description": "both notebooks' host, where from_host and to_host aren't given"}}}},
    {"name": "add_notebook",
     "description": "Make a notebook, empty but for Home, at once (not opened): add_page notebook and move_pages "
     "to_notebook fill it.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"},
                                                                           "host": {"type": "string", "description": "the host the notebook's on (left out: the one picked)"}}}},
    {"name": "edit_page",
     "description": "Change a page's text at once, as part of the change you're making to the pages this turn "
     "(fixing what refers to pages you moved, say): undone with the rest of it, as one. old_text is the text it "
     "replaces, copied exactly and found once in the page; empty, new_text goes at the page's end. For writing the "
     "user should review first: propose_edit. notebook and host: where the page is (left out: the one open).",
     "inputSchema": {"type": "object", "required": ["page", "old_text", "new_text"], "properties": {
         "page": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
         "notebook": {"type": "string"}, "host": {"type": "string", "description": "the host the notebook's on (left out: the one picked)"}}}},
    {"name": "highlight_pages",
     "description": "Mark pages in the pages' list, changing nothing, to show the user which you mean (till their "
     "next message).",
     "inputSchema": {"type": "object", "required": ["pages"], "properties": {
         "pages": {"type": "array", "items": {"type": "string"}}}}},
]
REF_LIMIT = meta.REF_LIMIT  # characters of a referenced page's, file's or chat's text put in with a message
REFS_LIMIT = 60000  # of all a message references, together
NOTES_LIMIT = 30000  # of the notebook's pages, with a session's first message (past it: their files, to Read)


def what_it_is(item, space):
    """An item of lookup's, said briefly: "Database table, relay (pg-1)", "File, src/jobs.py", "Chat, ~/shop · 2h ago",
    "Page, Garden"."""
    if item.get("kind") == "chat":
        return f"Chat, {item['summary']}"
    if item.get("kind") in ("page", "notebook"):
        return f"{item['what']}, {item['summary']}"
    root = item.get("root") or space.root  # (its own folder's, of several: Spaces)
    where = os.path.relpath(item["path"], root) if item.get("path") and root else item["summary"]
    return f"{item['what']}, {where}"


def said_names(text, space):
    """A page's @names, each followed by what it is (what_it_is), if space knows it; its [[@name]]s as @names."""
    def known(m):
        item = space.find(m.group(1) or m.group(2)) if space is not None else None
        return m.group(0) + (f" ({what_it_is(item, space)})" if item else "")
    return REF_RE.sub(known, plain_refs(text))


attr, trimmed = meta.attr, meta.trimmed


def notebook_said(notebook, store, space, chat=None, limit=NOTES_LIMIT):
    """A session's first words: the notebook open, its pages (each one's file, and its text as far as limit, its
    @names said) and the folder it reads: the chat read's (Window.chat_read), or why there's none."""
    files = page_paths(store.notebooks)
    root = pages_root()
    pages = sorted(notebook["pages"], key=lambda p: (p != HOME, p.lower()))
    parts = [f'<notebook host="{attr(store.host_name)}" name="{attr(full_name(notebook))}">']
    left = limit
    for name in pages:
        text = notebook["pages"][name].strip()
        file = os.path.join(root, files.get((notebook.get("id"), name), ""))
        if not text:
            parts.append(f'<page name="{attr(name)}" file="{attr(file)}">(empty)</page>')
        elif len(text) <= left:
            parts.append(f'<page name="{attr(name)}" file="{attr(file)}">\n{said_names(text, space)}\n</page>')
            left -= len(text)
        else:
            parts.append(f'<page name="{attr(name)}" file="{attr(file)}">(not put here: Read its file)</page>')
    cwd = (chat or {}).get("cwd")
    parts.append("The chat read's folder: " + (
        "none (no chat read)" if not chat else "none (the chat read has none)" if not cwd else
        os.path.realpath(cwd) if os.path.isdir(cwd) else f"none ({cwd}, the chat read's, isn't there any more)"))
    parts.append("</notebook>")
    return "\n".join(parts)


def none_open_said(store):
    """A session's first words while no notebook's open: that, and the notebooks there are, with their pages' names."""
    books = [f'<notebook name="{attr(full_name(t))}">' +
             ", ".join(sorted(t["pages"], key=lambda p: (p != HOME, p.lower()))[:40]) + "</notebook>"
             for t in store.notebooks[:30]]
    none = f'<no-notebook-open host="{attr(store.host_name)}"/>'
    return "\n".join([none, *books]) if books else f"{none} (and there are no notebooks on it yet)"


def referenced(text, space):
    """The things a message @-references that the catalogue knows (Window.space), each once, in order."""
    out, seen = [], set()
    for m in REF_RE.finditer(plain_refs(text)):
        item = space.find(m.group(1) or m.group(2)) if space is not None else None
        if item is not None and item["surface"] not in seen:
            seen.add(item["surface"])
            out.append(item)
    return out


def file_text(path, limit):
    """A file's text, as far as limit, or None: not text, or not to be had."""
    try:
        with open(path, "rb") as f:
            data = f.read(limit * 4 + 1)
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    try:  # (what's read may end part way into a character: that part's left for the rest)
        text = codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
    except UnicodeDecodeError:
        return None
    return trimmed(text, limit, path)


def ref_said(item, store, space, limit=REF_LIMIT):
    """What an @-referenced thing is, for the agent: a page's or a notebook's text (its pages', as far as limit), a
    file's text (or where it is, to read), a chat's, or what it is. (said, paths it may read)."""
    kind, books = item.get("kind"), {t.get("id"): t for t in store.notebooks}
    root, files = pages_root(), page_paths(store.notebooks)
    if kind == "page" and (notebook := books.get(item.get("notebook"))) is not None:
        file = os.path.join(root, files.get((notebook.get("id"), item["page"]), ""))
        text = notebook["pages"].get(item["page"], "")
        return (f'<page notebook="{attr(full_name(notebook))}" name="{attr(item["page"])}" file="{attr(file)}">\n'
                f'{trimmed(said_names(text, space), limit, file) or "(empty)"}\n</page>'), [file]
    if kind == "notebook" and (notebook := books.get(item.get("notebook"))) is not None:
        parts, left, paths = [f'<notebook name="{attr(full_name(notebook))}">'], limit, []
        for name in sorted(notebook["pages"], key=lambda p: (p != HOME, p.lower())):
            file = os.path.join(root, files.get((notebook.get("id"), name), ""))
            text = notebook["pages"][name].strip()
            paths.append(file)
            body = said_names(text, space) if len(text) <= left else "(not put here: Read its file)"
            left -= len(text) if len(text) <= left else 0
            parts.append(f'<page name="{attr(name)}" file="{attr(file)}">\n{body or "(empty)"}\n</page>')
        return "\n".join(parts + ["</notebook>"]), paths
    if kind == "chat" and item.get("path"):
        try:
            text = chat_text(item["path"], item["surface"], item.get("where"))
        except OSError:
            text = None
        if text is not None and len(text) > limit:  # (its end: what was said last)
            text = f"[… its first {len(text) - limit} characters not put here: Read {item['path']}]\n" + text[-limit:]
        return f'<chat title="{attr(item["surface"])}" transcript="{attr(item["path"])}">\n{text or "(nothing said)"}\n' \
               f'</chat>', [item["path"]]
    path = item.get("path")
    if path and os.path.isdir(path):
        return f'<folder name="{attr(item["surface"])}" path="{attr(path)}">{attr(what_it_is(item, space))}: ' \
               f'Glob and Grep it</folder>', [path]
    if path and os.path.isfile(path):
        text = file_text(path, limit)
        line = f' line="{item["line"]}"' if item.get("line") else ""
        return (f'<file name="{attr(item["surface"])}" path="{attr(path)}"{line}>\n{text}\n</file>' if text is not None
                else f'<file name="{attr(item["surface"])}" path="{attr(path)}"{line}>(not text: '
                     f'{attr(what_it_is(item, space))})</file>'), [path]
    return f'<ref name="{attr(item["surface"])}">{attr(what_it_is(item, space))}</ref>', []


def passage_said(quote, space):
    """A passage quoted from a chat being read (Reader.picks), with what its project names are."""
    names = space.mentions(quote[:QUOTE_LIMIT]) if space is not None else []
    said = "".join(f"\n- {it['surface']}: {what_it_is(it, space)}" for it in names[:30])
    tag = "passages" if quote.startswith("[1] ") and "\n\n[2] " in quote else "passage"
    opened = "[passages, numbered as the user sees them]" if tag == "passages" else "[passage]"
    return f"{opened}\n{quote[:QUOTE_LIMIT]}\n[/{tag}]\n" + \
        (f"[project names in it]{said}\n[/project names]\n" if said else "")


def selection_said(selection, notebook, store):
    """A passage selected on a page of notes (DiaryEditor._quote_selection), and where it is."""
    file = os.path.join(pages_root(), page_paths(store.notebooks).get((notebook.get("id"), selection["page"]), ""))
    said = "(Selected on the page: what the user is asking about, or wants filled in or changed. A change to it is a " \
        "propose_edit with this as old_text.)" if selection.get("start") is not None else \
        "(Quoted from the page, where it isn't as it was any more: the page has changed since.)"
    return f'<selection page="{attr(selection["page"])}" file="{attr(file)}">\n{selection["text"]}\n</selection>\n' \
        f'{said}\n'


def page_named(notebook, name, store):
    """The page of notebook a name means (the agent's: its name, any case, its own name past its parents', or its file),
    or None."""
    name = name.strip()
    if (page := notebooktools.page_named(notebook["pages"], name)) is not None:
        return page
    files = {os.path.join(pages_root(), path): page for (book, page), path in page_paths(store.notebooks).items()
             if book == notebook.get("id")}
    path = os.path.realpath(os.path.join(pages_root(), name)) if name else ""
    return next((page for file, page in files.items() if os.path.realpath(file) == path or
                 file.endswith(os.sep + name.lstrip(os.sep))), None)


def pending_edits(notebook, store, agent=None):
    """The edits waiting on a notebook's pages, in the order they were made: as its host last listed them (Record's
    Store.edits_for: Meta's, and anyone's); in a notebook kept with its own changes (a Bot's memory's), those kept with
    its messages."""
    if hasattr(store, "edits_for"):
        return store.edits_for(notebook)
    return [p for m in notebook.get("messages") or () for p in m.get("proposals") or ()
            if p.get("status") == "pending"]


def split_unknown(text):
    """(reply, [unknown terms]) — the reply without its last "Unknown:" line."""
    m = re.search(r"\n?\s*\**Unknown:\**\s*(.*?)\s*$", text, re.I | re.S)
    if not m or "\n" in m.group(1).strip():
        return text.strip(), []
    terms = [t.strip(" .*`\"'") for t in re.split(r"[;,]", m.group(1))]
    terms = [t for t in terms if t and t.lower() not in ("none", "nothing", "n/a")]
    return text[:m.start()].strip(), terms


def pages_root():
    """The folder the pages' files are in (Store.mirror_pages): the agent's working folder."""
    root = os.path.join(data_dir(), "pages")
    os.makedirs(root, exist_ok=True)
    return root
