"""markdownview — markdown as Baude and Record draw it: links to files (resolve_path, link_markup, open_in_editor),
Signal's refs and pull requests as links and pills (ref_markup, ref_link, pr_pill, pr_link_markup), inline
markdown (_inline_markup), its blocks (markdown_parse), tables (TableScroll, markdown_table), code (CodeBlock), a
message's text (sel_label, MarkdownView) and its links under the pointer (markup_links, hover_uri). SIGNAL (the
Signal its refs are read with) and REF_MESSAGE are set here, by Baude and Record.
"""
import hashlib
import html
import os
import re
import shutil
import weakref
import subprocess
import sys
import time
import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402,F401

import lookupgit  # noqa: E402
from lookup import _CATALOGUE_TOKEN, catalogue_shaped, ref_icon, ref_key  # noqa: E402,F401
from baudelib import wording  # noqa: E402
from widgets import IMAGE_POOL, WrapLabel, code_markup_size, discard, load_settings  # noqa: E402
from refkinds import (  # noqa: E402
    LinkLabel, REF_SCHEME, _REF_PLAIN, pill_code_scale, pr_pill_markup, pr_title, ref_cloud_provider, ref_icon_slot,
    ref_quiet,
)
from codespans import CODE_HIGHLIGHT_MAX, code_attrs, code_spans, line_spans, numbered_spans  # noqa: E402
from imageviews import fetch_web_image, image_widget, with_image_link  # noqa: E402


# ---------------------------------------------------------------- markdown --

# ------------------------------------------------------------- file links --
# Paths in Claude's text ("baude.py:42", `tests/run.sh`, /etc/hosts, ~/notes.md) become links when
# they exist, relative to the session folder; the link's target is the path itself (file_href), and clicking one opens
# it on the side panel's Code page, or in the editor (open_code).


_PATH = re.compile(r"(?<![\w/.:~@-])("
                   r"(?:~|\.{1,2})?/(?:[\w.@+-]+/)*[\w.@+-]*"           # /abs, ~/x, ./x, ../x (files or folders)
                   r"|(?:[\w.@+-]+/)*[\w@+-][\w.@+-]*\.[A-Za-z0-9]{1,8}"  # relative, with an extension
                   r"|(?:[\w.@+-]+/)+(?:[\w@+-][\w.@+-]*)?"  # relative, in a folder: services/x/Dockerfile, a folder
                   r")(?::(\d+)(?::\d+)?)?(?![\w/])")


_exists_cache = {}  # (base, path) -> (checked at, exists)


EXISTS_FOR = 2  # seconds a path's answer is used for before it's looked at again


_checked_since = float("inf")  # the oldest answer about a path used since _inline started on a text (see _inline)


def resolve_path(text):
    """(absolute path, line) if text names an existing file or folder, else None."""
    global _checked_since
    m = _PATH.fullmatch(text.strip())
    if not m or len(m.group(1)) < 3:
        return None
    raw, line = m.group(1), int(m.group(2)) if m.group(2) else None
    path = os.path.expanduser(raw)
    if not os.path.isabs(path):
        path = os.path.join(MarkdownView.base_dir, path)
    path = os.path.normpath(path)
    key = (MarkdownView.base_dir, path)
    when, ok = _exists_cache.get(key, (0, False))
    if time.monotonic() - when > EXISTS_FOR:  # streaming re-renders often; don't stat every frame
        ok = os.path.exists(path)
        if len(_exists_cache) > 4096:  # (every path every chat named: only the last couple of seconds' are used)
            _exists_cache.clear()
        when = time.monotonic()
        _exists_cache[key] = (when, ok)
    _checked_since = min(_checked_since, when)
    return (path, line) if ok else None


def resolve_link(url):
    """(absolute path, line) for a [link](target) whose target is a file or folder, relative to the chat's folder
    (a #L12 or :12 after it is the line), else None. A link says it's a path: unlike one in prose, it needs no
    extension or folder (Dockerfile, Makefile)."""
    if re.match(r"[a-z][a-z0-9+.-]*:", url, re.I) and not url.startswith("file:"):
        return None  # (a URL)
    m = re.fullmatch(r"(?:file://)?(.+?)(?:#L?(\d+)(?:-L?\d+)?|:(\d+)(?::\d+)?)?", url)
    if not m:
        return None
    line = m[2] or m[3]
    for raw in dict.fromkeys((m[1], urllib.parse.unquote(m[1]))):  # (a path as it is first: one with a % in its name)
        path = os.path.normpath(os.path.join(MarkdownView.base_dir, os.path.expanduser(raw)))
        if os.path.exists(path):
            return path, int(line) if line else None
    return None


def file_href(path, line=None):
    """A file link's target: the path itself, :line after it, so Copy Link Address gives what a terminal or an editor
    takes (on_label_link opens it, as it does a [link](path))."""
    return path + (f":{line}" if line else "")


def link_paths(escaped_text):
    """Wrap existing paths in plain (already escaped) text in editor links."""
    def sub(m):
        hit = resolve_path(html.unescape(m.group(0)))
        if not hit:
            return m.group(0)
        return f'<a href="{html.escape(file_href(*hit))}" title="{html.escape(hit[0])}">{m.group(0)}</a>'
    return _PATH.sub(sub, escaped_text)


def editor_command(path, line=None):
    """How to open path (at line) in the user's editor: settings.json's "editor" (a template
    with {path} and {line}), else $VISUAL / $EDITOR if graphical, else VS Code if installed
    (it can jump to a line). None means: the desktop's default app for the file."""
    template = load_settings().get("editor")
    if template:
        return [a.replace("{path}", path).replace("{line}", str(line or 1)) for a in template.split()]
    for var in ("VISUAL", "EDITOR"):
        cmd = os.environ.get(var, "").split()
        if cmd and os.path.basename(cmd[0]) in GUI_EDITORS:
            return editor_args(cmd, path, line)
    if shutil.which("code"):
        return editor_args(["code"], path, line)
    return None


GUI_EDITORS = {"code", "codium", "cursor", "zed", "subl", "gedit", "gnome-text-editor", "kate", "kwrite", "geany"}


def editor_args(cmd, path, line):
    name = os.path.basename(cmd[0])
    if not line:
        return cmd + [path]
    if name in ("code", "codium", "cursor"):
        return cmd + ["-g", f"{path}:{line}"]
    if name in ("zed", "subl"):
        return cmd + [f"{path}:{line}"]
    return cmd + [f"+{line}", path]  # gedit, gnome-text-editor, kate, geany


def open_in_editor(path, line=None, widget=None):
    if os.path.isdir(path):  # folders: the side panel's Files page (the file manager, outside a Baude window)
        win = widget.get_root() if widget is not None else None
        if hasattr(win, "show_folder"):
            win.show_folder(path)
        else:
            Gtk.FileLauncher(file=Gio.File.new_for_path(path)).launch(win, None, None)
        return
    cmd = editor_command(path, line)
    if cmd is None:
        Gtk.FileLauncher(file=Gio.File.new_for_path(path)).launch(widget.get_root() if widget else None, None, None)
        return
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        Gtk.FileLauncher(file=Gio.File.new_for_path(path)).launch(widget.get_root() if widget else None, None, None)


GIT_SCHEME = "baude-git:"  # (a note's link to the Git page: baude-git:<branch>, or none; ?<folder>: another one's)


def on_label_link(label, uri):
    """Labels' link handler: a file's link opens it (open_code), a name what it names (open_ref); everything else, the
    default."""
    if uri.startswith(GIT_SCHEME):  # (a note's Show in Git: the Git page, on the branch it names, if any)
        win = label.get_root()
        if hasattr(win, "show_git"):
            rev, _, folder = uri[len(GIT_SCHEME):].partition("?")
            win.show_git(rev=urllib.parse.unquote(rev) or None, folder=urllib.parse.unquote(folder) or None)
        return True
    if uri.startswith(REF_SCHEME):
        win = label.get_root()
        if hasattr(win, "open_ref"):
            name, _, message = uri[len(REF_SCHEME):].partition("#")
            win.open_ref(urllib.parse.unquote(name), label, message or None)
        return True
    if (hit := resolve_link(uri)) is not None:  # (a file's: its path, :line after it; or a [link](path))
        open_code(*hit, label)
        return True
    win = label.get_root()  # (a file on the server of a session on the network: Baude's open_remote_link)
    return bool(hasattr(win, "open_remote_link") and win.open_remote_link(uri))


def open_code(path, line=None, widget=None):
    """A file link: shown in Baude (the side panel's Code page), unless settings.json says "code_viewer":
    false or it isn't text to read (a folder, a binary or huge file): then the editor or its app."""
    win = widget.get_root() if widget is not None else None
    if hasattr(win, "show_code") and load_settings().get("code_viewer", True) and win.show_code(path, line):
        return
    open_in_editor(path, line, widget)


ARTIFACT_TITLES = {}  # artifact url -> title, filled in as Claude publishes (see BaudeWindow.register_artifact)


_ARTIFACT_URL = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]+")


def link_markup(url, text=None, markup=None):
    """A Pango link. Artifact links read as their title, inline at text size: 'Ops Dashboard ↗'. markup: the text
    as markup already (a [link](…)'s, with its code and bold)."""
    global _checked_since
    inner = markup if markup is not None else html.escape(text or url, quote=False)
    if "://" not in url and (hit := resolve_path(url) or resolve_link(url)):  # [text](relative/or/abs/path)
        return f'<a href="{html.escape(file_href(*hit))}" title="{html.escape(hit[0])}">{inner}</a>'
    href = html.escape(url)
    if _ARTIFACT_URL.fullmatch(url):
        if text and text != url:
            label = inner
        else:  # (a title that may come later: what _inline draws with it isn't kept)
            label, _checked_since = html.escape(ARTIFACT_TITLES.get(url, wording.say("references.claude_artifact")),
                                                quote=False), float("-inf")
        return f'<a href="{href}" title="{href}">{label} ↗</a>'
    return f'<a href="{href}">{inner}</a>'


REF_MESSAGE = None  # the hash of the reply being drawn (markdown_blocks): its own decisions apply


SIGNAL = None  # the active window's Signal while it's on: _inline turns the names it knows into links


def chat_repo():
    """The GitHub repository (owner/name) the reply being drawn is in, what a bare #12 in it means: its Signal's (the
    chat's checkout's origin), else its folder's as GITHUB has read it. None if there's none."""
    return getattr(SIGNAL, "github", None) or \
        lookupgit.GITHUB.own_repository(getattr(SIGNAL, "folder", None) or MarkdownView.base_dir)


def pr_pill(repo, number, inner, href=None, known=False, any_repo=False):
    """A pull request of repo's as a pill (pr_pill_markup), linked to href (else its page); None if repo isn't the
    chat's (chat_repo: another's keeps its link as it was) unless any_repo (a link to one, whose shape says it is: its
    pill, in where it stands once that's read, and a link's colour till then), with no gh or gh signed out, if
    nothing's been read of its pull requests (and not any_repo), or (known) if number isn't one of them as far as is
    known: an issue, or nothing (a bare #12 may be either, or a colour). Its own #12 in inner reads 12 (pr_numbered),
    at code's size (pill_code_scale: a number among `names` is as big as they are; LinkLabel's logo and patch follow)."""
    if not repo or not any_repo and repo.lower() != (chat_repo() or "").lower():
        return None
    if lookupgit.PULLS.states(repo) is None and \
            not (any_repo and lookupgit.gh_path() and not lookupgit.PULLS.signed_out()):
        return None
    p = lookupgit.PULLS.pull(repo, number)
    if p is None and known:
        return None
    # (a size="…%" is a share of the label's own size, not of the span it's in: code in a link's text keeps its)
    inner = f'<span size="{round(pill_code_scale() * 100)}%">{pr_numbered(inner, number)}</span>'
    return pr_pill_markup(inner,
                          href or (p or {}).get("url") or f"https://github.com/{repo}/pull/{number}",
                          pr_title(p, repo, number), (p or {}).get("state"))


_PR_NAMED = re.compile(r"(?<![\w&;#/.-])(?:(?:PR|pull request) ?)?(?:[\w.-]+/[\w.-]+)?#(\d+)(?![\w-])(?![^<]*>)", re.I)


def pr_numbered(markup, number):
    """markup with what names pull request number in it (#12, PR #12, pull request #12, owner/name#12) as the number
    alone, for after its logo: the logo says it's one, so a # would say it twice. Its tags' own left alone."""
    return _PR_NAMED.sub(lambda m: m[1] if int(m[1]) == number else m[0], markup)


def pr_unbroken(markup):
    """A pill's text (markup) with its spaces no place to end a line (PR #12 on one), its tags' own left alone."""
    return re.sub(r" (?![^<]*>)", "\u00a0", markup)


def pr_unremembered():
    """The text being drawn names a pull request: _inline doesn't remember its markup (where it stands may change, and
    its repository's pull requests may not be read yet). True."""
    global _checked_since
    _checked_since = float("-inf")
    return True


_PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)(?:[/?#]\S*)?")


_BARE_PR = re.compile(r"(?<![\w&#/.-])#(\d{1,7})(?![\w-])")  # (#12; not &#12; nor owner/name#12)


def pr_link_markup(url, markup=None):
    """A link to a pull request, any repository's (pr_pill's any_repo), as its pill: what the link says (markup), its
    #12 or PR #12 the number alone, else its number. None for any other link, and when pr_pill makes none."""
    m = _PR_URL.fullmatch(url)
    if not m or not pr_unremembered():
        return None
    return pr_pill(m[1], int(m[2]), markup or m[2], href=url, any_repo=True)  # (the logo says it's one: no "#")


def ref_markup(ref, inner, icon=True):
    """inner (markup) as a Signal link, drawn by how far its lookup got: plain when found, a count
    when there are several matches, faint while pending, grey when not found, amber and wavy when stale. A pull
    request found is its pill (pr_pill), if its repository's have been read; else its logo and its number."""
    href = html.escape(REF_SCHEME + urllib.parse.quote(ref.surface, safe=""))
    pr = icon and ref.kind == "pr" and ref.status == "resolved" and not ref.data.get("issue") and \
        isinstance(ref.data.get("number"), int)
    if pr and (pill := pr_pill(ref.data.get("repo"), ref.data["number"], str(ref.data["number"]),
                               href=REF_SCHEME + urllib.parse.quote(ref.surface, safe=""))) is not None:
        return pill
    if pr:  # (after its logo, as on a pill: PR #12 and owner/name#12 read 12)
        inner = pr_numbered(inner, ref.data["number"])
    if ref.status == "pending":
        inner = f'<span alpha="55%">{inner}</span>'
    elif ref.status == "not_found":
        inner = f'<span foreground="#8a8a8a" underline="single" underline_color="#c0c0c0">{inner}</span>'
    elif ref.status == "stale":
        inner = f'<span foreground="#c27b00" underline="error" underline_color="#c27b00">{inner}</span>'
    elif ref.status == "ambiguous":
        inner += f"<sup><small>{len(ref.locations)}</small></sup>"
    title = wording.say("lookup.looking_it_up") if ref.status == "pending" else ref.summary()
    # (its logo once it's found: a name still being looked up, or not there, claims no kind)
    slot = ref_icon_slot(ref_icon(ref.kind, ref.data, [l["role"] for l in ref.locations])) \
        if icon and ref.status in ("resolved", "ambiguous", "stale") else ""
    return f'{slot}<a href="{href}" title="{html.escape(title)}">{inner}</a>'


REF_BARE_NUMBER = re.compile(r"#\d+")  # (in code it's no link: a colour, a comment; owner/name#12 says what it is)


def ref_code_markup(code, message=None):
    """A code block's text as markup with the names linked: as the reply decided them (message: its hash), else
    the ones Signal placed (None: nothing to link)."""
    if SIGNAL is None or not code or len(code) > 200_000:
        return None

    def markup(s, inner):
        if ref_cloud_provider(s):
            return None
        if (dec := SIGNAL.decision(message, s)) is not None:  # (no logos in code: its columns stay put)
            return ref_decision_markup(s, dec, inner, icon=False) if dec["verdict"] != "plain" else None
        r = SIGNAL.refs.get(s)  # (a plain word: not in code blocks, where nothing decides it)
        if r is None or r.status not in ("resolved", "ambiguous") or re.fullmatch(r"[A-Za-z][a-z]+", s):
            return None
        return ref_markup(r, inner, icon=False)
    names = [s for s in set(SIGNAL.refs) | (set(SIGNAL._by_surface) if message else set())
             if len(s) >= 3 and s in code and not REF_BARE_NUMBER.fullmatch(s) and markup(s, "") is not None]
    if not names:
        return None
    pattern = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) +
                         r")(?![\w/-])")
    out, at = [], 0
    for m in pattern.finditer(code):
        out.append(html.escape(code[at:m.start()], quote=False))
        out.append(markup(m.group(0), html.escape(m.group(0), quote=False)))
        at = m.end()
    if not at:
        return None
    out.append(html.escape(code[at:], quote=False))
    return "".join(out)


def ref_link(text, inner, backticked):
    """inner linked to what SIGNAL knows about text, or None. Names that appear only in plain text
    are linked once found; a backticked one shows every state, unless it's a piece of code (ref_quiet)."""
    if SIGNAL is None:
        return None
    key = ref_key(text)
    if ref_cloud_provider(key):  # (Cloudflare, Vercel…: noise as a link)
        return None
    if backticked and REF_BARE_NUMBER.fullmatch(key):  # (code: a colour's #333 as like as not)
        return None
    git = SIGNAL.refs.get(key)
    if git is not None and git.kind in ("commit", "branch", "pr") and git.status == "resolved":
        return ref_markup(git, inner)  # (a git ref: Baude knows it exactly)
    if (dec := SIGNAL.decision(REF_MESSAGE, key)) is not None:  # (what it means in this reply)
        return ref_decision_markup(key, dec, inner) if dec["verdict"] != "plain" else None  # (plain English here)
    ref = SIGNAL.refs.get(key)
    if ref is None or not (backticked or ref.surface in SIGNAL.strong or ref.status in ("resolved", "ambiguous", "stale")):
        return None
    if ref.data.get("plain") or (SIGNAL.reading() and re.fullmatch(r"[A-Za-z][a-z]{2,}", ref.surface)):
        return None  # (a plain word: the reader decides, per reply)
    if ref.status not in ("resolved", "ambiguous", "stale") and ref_quiet(ref.surface) and \
            not (ref.kind == "k8s" and ref.surface in SIGNAL.strong):  # (one a reply called a Deployment is missing)
        return None  # (code that isn't in the repository isn't a name that's missing)
    return ref_markup(ref, inner)


REF_ABSENT_WHY = {"proposed": wording.say("references.proposed"), "external": wording.say("references.external"),
                  "elsewhere": wording.say("references.elsewhere"), "not_found": wording.say("references.not_in_code")}


def ref_decision_markup(surface, dec, inner, icon=True):
    """inner linked to where the reader (or the evidence) placed this name in this reply; or, for one it says isn't
    in the code, to what it is: an outside tool as a link, anything else grey, like a name not found. A pull request
    it placed is its pill (pr_pill), if its repository's have been read; else its logo and its number."""
    href = html.escape(REF_SCHEME + urllib.parse.quote(surface, safe="") + "#" + dec["message"])
    pr = icon and dec.get("kind") == "pr" and dec["verdict"] != "not_in_code" and \
        re.fullmatch(r"(?:(?:PR|pull request) )?(?:([\w.-]+/[\w.-]+))?#(\d+)", surface, re.I)
    if pr and (pill := pr_pill(pr[1] or chat_repo(), int(pr[2]), pr[2], known=True,
                               href=REF_SCHEME + urllib.parse.quote(surface, safe="") + "#" + dec["message"])):
        return pill
    if pr:  # (after its logo, as on a pill: PR #12 reads 12)
        inner = pr_numbered(inner, int(pr[2]))
    if dec["verdict"] == "not_in_code":
        why = REF_ABSENT_WHY.get(dec.get("why"), REF_ABSENT_WHY["not_found"])
        title = html.escape(" · ".join(x for x in (why, dec.get("note")) if x))
        if dec.get("why") != "external":
            inner = f'<span foreground="#8a8a8a" underline="single" underline_color="#c0c0c0">{inner}</span>'
        slot = ref_icon_slot("external") if icon and dec.get("why") == "external" else ""
        return f'{slot}<a href="{href}" title="{title}">{inner}</a>'
    where = (dec.get("path") or "") + (f":{dec['line']}" if dec.get("line") else "")
    title = html.escape(" · ".join(x for x in (dec.get("note"), where) if x))
    slot = ref_icon_slot(ref_icon(dec.get("kind"), None, [dec.get("role") or ""])) if icon else ""
    return f'{slot}<a href="{href}" title="{title}">{inner}</a>'


_CODE_SPAN = re.compile(r"`([^`\n]+)`")


_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


_BARE_URL = re.compile(r"(?<![\w/])(https?://[^\s<>()\[\]`*\x00]+[^\s<>()\[\]`*.,;:!?'\"\x00])")


_BOLD_ITALIC = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", re.S)


_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)


_STAR_ITALIC = re.compile(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])")


_UNDERSCORE_ITALIC = re.compile(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)")


_STRIKE = re.compile(r"~~(.+?)~~")


_KEPT_LINK = re.compile(r"\x00(\d+)\x00")


EM_DASH = re.compile(r"(?<=\s)--(?=\s)")  # (-- on its own, whitespace both sides: an em dash, outside code)


INLINE_MEMO_BYTES = 512 * 1024  # what _inline remembers, at most: its texts and their markup


_inline_memo, _inline_memo_bytes = {}, 0  # (text, folder) -> (good until, markup), oldest first


def _memo_bytes(text, markup):
    return sys.getsizeof(text) + sys.getsizeof(markup) + 200  # (and the tuples, the float, the dict's slot)


def _inline(text):
    """Escape and apply inline markdown (code, bold, italic, links) as Pango markup. With no Signal links shown,
    the same text in the same folder is drawn the same, so it's remembered (a streaming reply draws its last
    lines every frame): till an answer it used about a path is due to be looked at again (resolve_path), and not
    at all if it drew an artifact's title (a title may come later)."""
    global _checked_since, _inline_memo_bytes
    if SIGNAL is not None:  # (names are drawn as Signal knows them now, in this reply: never remembered)
        return _inline_markup(text)
    key = (text, MarkdownView.base_dir)
    if (hit := _inline_memo.get(key)) is not None and hit[0] > time.monotonic():
        return hit[1]
    _checked_since = float("inf")
    markup = _inline_markup(text)
    size = _memo_bytes(text, markup)
    if _checked_since > float("-inf") and size <= INLINE_MEMO_BYTES // 16:
        if (old := _inline_memo.pop(key, None)) is not None:
            _inline_memo_bytes -= _memo_bytes(text, old[1])
        _inline_memo[key] = (_checked_since + EXISTS_FOR, markup)
        _inline_memo_bytes += size
        while _inline_memo_bytes > INLINE_MEMO_BYTES:
            oldest = next(iter(_inline_memo))
            _inline_memo_bytes -= _memo_bytes(oldest[0], _inline_memo.pop(oldest)[1])
    return markup


def _inline_markup(text):
    """_inline's markup. Code spans and links are set aside first, as placeholders, so emphasis goes round them:
    **`deployer-svc`** is bold code, and a link's text can be code ([`app.py`](app.py)). (Split at the code spans
    instead, a bold with code in it showed its **.)"""
    marks, bare = [], []  # (what each placeholder stands for, and the same without a link of its own)

    def keep(markup, plain=None):
        marks.append(markup)
        bare.append(markup if plain is None else plain)
        return f"\x00{len(marks) - 1}\x00"

    def code(m):
        markup = (f'<span font_family="monospace" size="{code_markup_size()}" bgcolor="#7f7f7f" bgalpha="22%">'
                  f'{html.escape(m[1], quote=False)}</span>')
        hit = resolve_path(m[1])
        return keep(f'<a href="{html.escape(file_href(*hit))}" title="{html.escape(hit[0])}">{markup}</a>' if hit
                    else ref_link(m[1], markup, True) or markup, markup)

    def link(m):  # (its text as markup: code and bold in it, but no link inside a link)
        inner = _KEPT_LINK.sub(lambda k: bare[int(k[1])], _emphasis(html.escape(m[1], quote=False)))
        return keep(pr_link_markup(m[2], inner) or link_markup(m[2], m[1], inner))  # (a pull request's: its pill)
    p = _CODE_SPAN.sub(code, text) if "`" in text else text  # (each pattern only where it could match: most
    if "](" in p:                                             # lines have none of them)
        p = _MD_LINK.sub(link, p)
    if "://" in p:
        p = _BARE_URL.sub(lambda m: keep(pr_link_markup(m[1]) or link_markup(m[1])), p)
    if SIGNAL is not None:  # names Signal found (commits, env vars, routes...) outside backticks
        p = _REF_PLAIN.sub(lambda m: keep(ref) if (ref := ref_link(m[0], html.escape(m[0], quote=False), False))
                           else m[0], p)
        phrases = SIGNAL.message_phrases(REF_MESSAGE) if SIGNAL.reading() else SIGNAL.phrase_re
        if phrases is not None:  # and the ones the reader linked in this reply's prose ("the Scheduler")
            p = phrases.sub(lambda m: keep(ref) if (ref := ref_link(m[0], html.escape(m[0], quote=False),
                                                                            False)) else m[0], p)
        if catalogue := getattr(SIGNAL, "catalogue", None):  # and the distinctive names the catalogue has
            p = _CATALOGUE_TOKEN.sub(lambda m: keep(ref) if catalogue_shaped(m[0]) and m[0] in catalogue and (
                ref := ref_link(m[0], html.escape(m[0], quote=False), False)) else m[0], p)
        if (unexplained := getattr(SIGNAL, "unexplained", None)) is not None and \
                (said := unexplained(REF_MESSAGE)) is not None:  # (Record's: the "the X"s nothing introduced, red)
            p = said.sub(lambda m: keep(unexplained_markup(m[0])), p)
    if "#" in p and _BARE_PR.search(p) and pr_unremembered() and (repo := chat_repo()):  # (a bare #12: a pill if
        # it's one of the repository's pull requests)
        p = _BARE_PR.sub(lambda m: keep(pill) if (pill := pr_pill(repo, int(m[1]), m[1], known=True)) else m[0], p)
    if "--" in p:  # (the code spans are set aside by now: -- in them stays)
        p = EM_DASH.sub("\u2014", p)
    s = _emphasis(link_paths(html.escape(p, quote=False)))
    return _KEPT_LINK.sub(lambda m: marks[int(m[1])], s) if "\x00" in s else s


def unexplained_markup(text):
    """text in red, the theme's (light or dark): a noun its chat takes as known that nothing introduced (nouns.py)."""
    red = "#ff7b63" if Adw.StyleManager.get_default().get_dark() else "#c01c28"
    return f'<span foreground="{red}">{html.escape(text, quote=False)}</span>'


def _emphasis(s):
    """Bold, italic and strike-through in escaped text. As CommonMark has it, a run that opens isn't followed by a
    space and one that closes doesn't follow one (2 * 3 * 4 isn't italic), and _ doesn't work inside a word."""
    if "*" in s:
        s = _BOLD_ITALIC.sub(r"<b><i>\1</i></b>", s)
        s = _BOLD.sub(r"<b>\1</b>", s)
        s = _STAR_ITALIC.sub(r"<i>\1</i>", s)
    if "_" in s:
        s = _UNDERSCORE_ITALIC.sub(r"<i>\1</i>", s)
    return _STRIKE.sub(r"<s>\1</s>", s) if "~~" in s else s


_ITEM = re.compile(r"^(\s*)([-*+•◦]|\d+[.)])\s+(.*)$")


BULLET, SUB_BULLET = "•", "◦"


def bullet_for(depth):
    """• at the top level, ◦ one level in, alternating further down."""
    return BULLET if depth % 2 == 0 else SUB_BULLET


INDENT_STEP = 18  # px per nesting level, in the chat and in the input box


_IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$")


_FENCE = re.compile(r"^\s*```\s*(\S*)")
_FENCE_LINE = re.compile(r"\s*(```|~~~)\s*(\S*)")


def fence_spans(lines, tildes=True, typing=()):
    """Where the code blocks are in text being written, of its lines: [(first line, last line, closed)], in order. A fence
    that a later bare one closes goes to it. One that nothing closes has only the lines right after it, up to a blank one:
    never all the rest of the page. With an odd number, one's left over: one on a line being typed on (typing) if there is
    one, as it's the new one; else the one that leaves the fewest lines in code, so a fence typed above blocks that were
    there doesn't turn them inside out."""
    fences = []
    for n, line in enumerate(lines):
        if "```" in line or tildes and "~~~" in line:
            m = _FENCE_LINE.match(line)
            if m and (tildes or m.group(1) == "```"):
                fences.append((n, not m.group(2)))

    def paired(some):
        closed, loose, i = [], [], 0
        while i < len(some):
            if i + 1 < len(some) and some[i + 1][1]:
                closed.append((some[i][0], some[i + 1][0]))
                i += 2
            else:
                loose.append(some[i][0])
                i += 1
        return closed, loose
    closed, loose = paired(fences)
    if len(fences) % 2 and len(fences) <= 64:
        def without(j):
            kept, left = paired(fences[:j] + fences[j + 1:])
            return kept, left + [fences[j][0]]
        here = [j for j, f in enumerate(fences) if f[0] in typing]
        if here:
            closed, loose = without(here[-1])
        else:
            closed, loose = min((without(j) for j in reversed(range(len(fences)))),
                                key=lambda found: (len(found[1]), sum(b - a for a, b in found[0])))
    at_fence = {f[0] for f in fences}
    spans = [(a, b, True) for a, b in closed]
    for first in loose:
        last = first
        while last + 1 < len(lines) and lines[last + 1].strip() and last + 1 not in at_fence:
            last += 1
        spans.append((first, last, False))
    return sorted(spans)


_TABLE_RULE = re.compile(r":?-{2,}:?")


_HEADING = re.compile(r"^(#{1,6})\s+(.*)")


_RULE = re.compile(r"\s*([-*_])(\s*\1){2,}\s*")


_QUOTE_MARK = re.compile(r"^>\s?")


def markdown_blocks(md):
    """Parse markdown into a flat list of blocks:
    ('para', markup) ('heading', level, markup) ('item', depth, marker, markup)
    ('quote', markup) ('hr',) ('code', lang, raw) ('image', alt, src) ('table', rows)
    """
    return markdown_parse(md)[0]


def message_key(md):
    """A reply's hash, as REF_MESSAGE has it while the reply is drawn."""
    return hashlib.sha1(md.encode("utf-8", "replace")).hexdigest()[:16]


def markdown_parse(md, since=None, message=None):
    """markdown_blocks, and the place to read on from once more is appended to md (a reply streaming in is read
    from there each frame, not from the top). since: that place, from an earlier parse of the start of md (None:
    the top); message: md's hash, if it's known (REF_MESSAGE). The place is the end of the last whole line after
    which nothing was left open (a paragraph's or a table's lines, a fence): no line after it can change the blocks
    before it but the last (a list item a line carries on, a quote the next > line joins), kept as it was there."""
    global REF_MESSAGE
    REF_MESSAGE = message or message_key(md)  # (this reply's decisions)
    at, before, n, last = since or (0, (), 0, None)
    blocks, para, code, lang, table = [*before[:n - 1], last] if n else [], [], None, "", []
    mark = (at, n, last)

    def flush():
        nonlocal para, table
        if para:
            blocks.append(("para", "\n".join(_inline(l) for l in para)))
            para = []
        if table:
            blocks.append(("table", table))
            table = []

    lines = md[at:].split("\n") if at else md.split("\n")
    for i, line in enumerate(lines):
        if i:  # (the line before is whole: if it left nothing open, what's read so far is settled up to here)
            at += len(lines[i - 1]) + 1
            if code is None and not para and not table:
                mark = (at, len(blocks), blocks[-1] if blocks else None)
        fence = _FENCE.match(line) if "```" in line else None
        if code is not None:
            if fence and not fence.group(1):
                blocks.append(("code", lang, "\n".join(code)))
                code = None
            else:
                code.append(line)
            continue
        if fence:
            flush()
            code, lang = [], fence.group(1)
            continue
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            if para:
                flush()
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            if not all(_TABLE_RULE.fullmatch(c) for c in cells):  # skip the |---| rule
                table.append(cells)
            continue
        if table:
            flush()
        if not stripped:
            flush()
            continue
        if line[:1] == "#" and (h := _HEADING.match(line)):
            flush()
            blocks.append(("heading", len(h.group(1)), _inline(h.group(2))))
        elif _RULE.fullmatch(line):
            flush()
            blocks.append(("hr",))
        elif m := _ITEM.match(line):
            flush()
            depth = len(m.group(1).expandtabs(4)) // 2
            marker = bullet_for(depth) if m.group(2) in "-*+•◦" else m.group(2)
            blocks.append(("item", depth, marker, _inline(m.group(3))))
        elif line.startswith(">"):
            flush()
            text = _inline(_QUOTE_MARK.sub("", line))
            if blocks and blocks[-1][0] == "quote":
                blocks[-1] = ("quote", blocks[-1][1] + "\n" + text)
            else:
                blocks.append(("quote", text))
        elif m := _IMAGE.match(line):
            flush()
            blocks.append(("image", m.group(1), m.group(2)))
        elif blocks and blocks[-1][0] == "item" and not para and line[:1].isspace():
            kind, depth, marker, markup = blocks[-1]  # wrapped continuation of a list item
            blocks[-1] = (kind, depth, marker, markup + " " + _inline(stripped))
        else:
            para.append(line)
    if code is not None:  # unterminated fence while streaming
        blocks.append(("code", lang, "\n".join(code)))
    flush()
    return blocks, (mark[0], blocks, mark[1], mark[2])


class TableScroll(Gtk.ScrolledWindow):
    """A markdown table's scroller. Its long cells wrap at the width there is: the short columns keep their natural
    width and the long ones share what's left of the chat's width, never under MIN_CHARS characters; past that the
    table scrolls sideways instead of squashing its columns. (A scrolled table measures its height at the cells'
    *minimum* widths, so a long cell's minimum is always its real width: its width in characters is set, not left
    to wrap, and set again when the width there is changes.)"""

    MIN_CHARS = 40

    def __init__(self, grid, cells):
        super().__init__(child=grid, vscrollbar_policy=Gtk.PolicyType.NEVER,
                         hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                         propagate_natural_height=True, propagate_natural_width=True)
        self.add_css_class("md-table-scroll")
        self.grid, self.cells = grid, cells  # (cells: [(label, its text, a header cell, column, long)])
        self.fitted, self.fitting = None, None
        self.chars = self.MIN_CHARS

    def do_size_allocate(self, width, height, baseline):
        Gtk.ScrolledWindow.do_size_allocate(self, width, height, baseline)
        if self.fitted != width and self.fitting is None:  # (after this allocation, not inside it)
            self.fitting = GLib.idle_add(self.fit)

    def available(self):
        """The width a table may take: its parent's (the chat column's), less this scroller's margins."""
        parent = self.get_parent()
        return (parent.get_width() if parent is not None else self.get_width()) - self.get_margin_start() - \
            self.get_margin_end()

    def fit(self):
        self.fitting = None
        width = self.available()
        long_cols = sorted({col for _l, _t, _h, col, long in self.cells if long})
        if width <= 0 or not long_cols:
            self.fitted = self.get_width()
            return False
        short = {}
        for lbl, _t, _h, col, long in self.cells:
            if col not in long_cols:
                short[col] = max(short.get(col, 0), lbl.measure(Gtk.Orientation.HORIZONTAL, -1)[1])
        metrics = self.cells[0][0].get_pango_context().get_metrics(None, None)  # (as a label's width_chars counts)
        char = max(1.0, max(metrics.get_approximate_char_width(), metrics.get_approximate_digit_width()) / Pango.SCALE)
        pad = 26  # (a cell's padding and rule)
        share = (width - 4 - sum(short.values())) / len(long_cols) - pad
        chars = max(self.MIN_CHARS, int(share / char))
        if chars != self.chars:
            self.chars = chars
            for lbl, text, _h, col, long in self.cells:
                if long:
                    n = min(chars, len(text))  # (a cell shorter than that is as wide as its text)
                    lbl.set_width_chars(n)
                    lbl.set_max_width_chars(n)
        self.fitted = self.get_width()
        return False


def markdown_table(rows):
    """A grid with a shaded header and light row rules (TableScroll: long cells wrap at the width there is, and a
    table wider than the chat scrolls sideways instead of squashing its columns)."""
    grid = Gtk.Grid(halign=Gtk.Align.START)
    grid.add_css_class("md-table")
    width = max(len(r) for r in rows)
    labels = []  # (label, its cell's text, a header cell, its column, a long column's): relinked as Signal finds
    for r, cells in enumerate(rows):  # names (relink_table), fitted to the width there is (TableScroll.fit)
        for c in range(width):
            cell = cells[c] if c < len(cells) else ""
            lbl = sel_label(f"<b>{_inline(cell)}</b>" if r == 0 else _inline(cell))
            long = len(cell) > TableScroll.MIN_CHARS
            labels.append((lbl, cell, r == 0, c, long))
            if long:  # (wraps at exactly this many characters: its minimum is its real width)
                lbl.set_width_chars(TableScroll.MIN_CHARS)
                lbl.set_max_width_chars(TableScroll.MIN_CHARS)
            else:
                lbl.set_wrap(False)
            lbl.set_valign(Gtk.Align.FILL)
            lbl.set_yalign(0)
            lbl.add_css_class("md-head" if r == 0 else "md-cell")
            grid.attach(lbl, c, r, 1, 1)
    return TableScroll(grid, labels)


def relink_table(scroll):
    """A table's cells drawn again with the links as they are now (its text is the same; what Signal knows may
    not be): only the cells whose markup changed are touched."""
    for lbl, cell, head, *_ in getattr(scroll, "cells", ()):
        markup = f"<b>{_inline(cell)}</b>" if head else _inline(cell)
        if markup != lbl.get_label():
            set_markup_safe(lbl, markup)


def set_markup_safe(label, markup):
    try:
        label.set_markup(markup)
    except GLib.Error:
        label.set_text(markup)


def sel_label(markup="", css=None, mono=False):
    lbl = LinkLabel(xalign=0, wrap=True, selectable=True)
    lbl.connect("activate-link", on_label_link)
    lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if mono:
        lbl.add_css_class("mono")
    if css:
        lbl.add_css_class(css)
    try:
        lbl.set_markup(markup)
    except GLib.Error:
        lbl.set_text(markup)
    return lbl


class CodeBlock(Gtk.Overlay):
    """Monospace code, its long lines scrolled sideways (or wrapped: wrap). The language tag and copy button
    float in the top-right corner, so the block is only as tall as its code.
    diff=[("del"|"add"|"ctx", line), ...] shows a red / green background per line instead.
    A long block is a sub-pane: at most ~2/3 of the transcript's height, scrolled inside itself.
    syntax (a language's name: bash, regex, a code fence's tag) or else path (a file's: its name picks the language)
    highlights it (code_spans), in the light or dark style's colours; numbered: a Read's output, its lines numbered.
    wrap: prose's, its lines wrapped at the block's width."""
    max_height = 600  # set from the window (CodeBlock.set_max_height) as it's resized
    live = weakref.WeakSet()
    styled = False  # (the theme watched: a block's colours follow it)

    @classmethod
    def set_max_height(cls, h):
        h = max(160, int(h))
        if h != cls.max_height:
            cls.max_height = h
            for block in list(cls.live):
                block.scroll.set_max_content_height(h)

    def __init__(self, code, lang="", markup=None, diff=None, syntax=None, path="", numbered=False, wrap=False):
        super().__init__()
        self.add_css_class("codeblock")
        if wrap:
            self.add_css_class("wrapped")
        self.code, self.diff = code, diff
        self.how = (syntax or "", path or "", numbered) if (syntax or path) and markup is None else None
        self.looks, self.generation, self.soon = None, 0, None  # ([(label, its text, its spans)]; a highlight due)
        label_type = WrapLabel if wrap else Gtk.Label  # (wrapped, measured without Pango breaking it at every point)
        if diff is not None:
            self.label = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for kind, line in diff:
                lbl = label_type(label=line or " ", xalign=0, selectable=True, hexpand=True, wrap=wrap,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR)
                lbl.add_css_class("mono")
                lbl.add_css_class(f"diff-{kind}")
                self.label.append(lbl)
        else:
            self.label = label_type(xalign=0, yalign=0, selectable=True, wrap=wrap, wrap_mode=Pango.WrapMode.WORD_CHAR)
            self.label.add_css_class("mono")
            self.own_markup = markup is not None  # (a block drawn with its own markup is left as it is)
            if markup is not None:
                self.label.set_markup(markup)
            else:
                self.label.set_text(code)
        scroll = Gtk.ScrolledWindow(child=self.label, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER if wrap else Gtk.PolicyType.AUTOMATIC,
                                    propagate_natural_height=True, max_content_height=CodeBlock.max_height)
        scroll.add_css_class("code-scroll")
        self.scroll = scroll
        CodeBlock.live.add(self)
        self.set_child(scroll)
        corner = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.START)
        corner.add_css_class("code-corner")
        if lang:
            tag = Gtk.Label(label=lang)
            tag.add_css_class("code-lang")
            corner.append(tag)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=wording.say("common.copy"))
        copy.add_css_class("flat")
        copy.add_css_class("code-copy")
        copy.connect("clicked", lambda *_: self.get_clipboard().set(self.code))
        corner.append(copy)
        self.add_overlay(corner)
        if self.how is not None:
            if not CodeBlock.styled:
                CodeBlock.styled = True
                Adw.StyleManager.get_default().connect("notify::dark", lambda *_: CodeBlock.restyle())
            self.highlight()

    @classmethod
    def restyle(cls):
        """Every block's colours, light or dark as the theme is now."""
        for block in list(cls.live):
            if block.looks:
                block.paint()

    def set_code(self, code):  # a plain block growing while it streams
        grew = code.startswith(self.code)
        self.code = code
        self.label.set_text(code)
        if not grew:  # (its colours, and a highlight on its way, were for other text; grown, it keeps them)
            self.generation += 1
            self.looks = None
            self.label.set_attributes(None)
        self.linked = False
        self.relink()
        if self.how is not None and self.soon is None:  # (as it streams: a few times a second, a long one less often)
            ref = weakref.ref(self)

            def due():
                if (block := ref()) is not None:
                    block.soon = None
                    block.highlight()
                return False
            self.soon = GLib.timeout_add(150 + min(len(code), CODE_HIGHLIGHT_MAX) // 40, due)

    def highlight(self):
        """Colour the code (self.how), at most its first CODE_HIGHLIGHT_MAX characters, as it's made: a long label is
        laid out once, coloured (coloured a moment later, it was laid out twice, and 2,000 lines take half a second
        each time). Off the main thread only while Pygments is loaded, the first time."""
        self.generation += 1
        gen, (syntax, path, numbered) = self.generation, self.how
        if self.diff is not None:  # (each side of the change read as the code it is, then cut into its lines)
            labels, rows = [], []
            child = self.label.get_first_child()
            for kind, line in self.diff:
                labels.append(child)
                rows.append((kind, line or ""))
                child = child.get_next_sibling()
            texts = None
        else:
            labels, texts = [self.label], [self.code]

        def work():
            if texts is None:
                sides = {"old": [r for r in rows if r[0] != "add"], "new": [r for r in rows if r[0] != "del"]}
                spans = {}
                for side, lines in sides.items():
                    src = _highlight_part("\n".join(line for _k, line in lines))
                    for (kind, _line), found in zip(lines, line_spans(src, code_spans(src, path, syntax))):
                        if kind != "ctx" or side == "new":
                            spans.setdefault(kind, []).append(found)
                per = {k: iter(v) for k, v in spans.items()}  # (none past what was highlighted)
                return [(lbl, line, next(per.get(kind, iter(())), [])) for lbl, (kind, line) in zip(labels, rows)]
            src = _highlight_part(texts[0])
            return [(labels[0], texts[0], numbered_spans(src, path) if numbered else code_spans(src, path, syntax))]
        ref = weakref.ref(self)

        def done(looks):
            if (block := ref()) is not None and gen == block.generation:
                block.looks = looks
                block.paint()
            return False
        if _pygments_loaded():
            done(work())
        else:
            IMAGE_POOL.submit(lambda: GLib.idle_add(done, work()))

    def paint(self):
        """Draw its colours (again, as the theme or its links changed). A link in it keeps its own."""
        if not self.looks:
            return
        dark = Adw.StyleManager.get_default().get_dark()
        for label, text, spans in self.looks:
            markup = label.get_label() if label.get_use_markup() else ""
            skip = [(s, e) for s, e, _ in markup_links(markup)] if "<a " in markup else ()
            label.set_attributes(code_attrs(text, spans, dark, skip) if spans else None)

    def relink(self):
        """Link the names Signal placed that this (plain) block's code mentions, as its reply (message, the hash a
        MarkdownView gives it) decided them; unlink when it's off."""
        if not isinstance(self.label, Gtk.Label) or getattr(self, "own_markup", False):
            return
        if not getattr(self, "link_handler", None):
            self.link_handler = self.label.connect("activate-link", on_label_link)
        message = getattr(self, "message", None)
        markup = ref_code_markup(self.code, message)
        if markup is not None and message:  # (a click opens the name as this reply decided it)
            markup = re.sub(r'href="(baude-ref:[^"#]*)"', lambda m: f'href="{m[1]}#{message}"', markup)
        if markup is not None:
            set_markup_safe(self.label, markup)
            self.linked = True
            self.paint()  # (its links keep their own colour)
        elif getattr(self, "linked", False):
            self.label.set_text(self.code)
            self.linked = False
            self.paint()


def diff_block(old, new, path="", wrap=False):
    """An edit's hunk, as Baude's chat shows an Edit's and Meta a change to a page's text: the lines it replaced on red,
    then those it put in on green, highlighted as path's code; wrap: prose's (CodeBlock)."""
    lines = [("del", l) for l in (old or "").splitlines()] + [("add", l) for l in (new or "").splitlines()]
    return CodeBlock(new or "", "", diff=lines or [("ctx", "")], path=path, wrap=wrap)


def _highlight_part(text):
    """What of a block's text is highlighted: its first CODE_HIGHLIGHT_MAX characters, to a line's end (a carriage
    return a space, as Pygments would drop it and the offsets after it be off by one)."""
    if len(text) > CODE_HIGHLIGHT_MAX:
        text = text[:max(text.rfind("\n", 0, CODE_HIGHLIGHT_MAX), 0)]
    return text.replace("\r", " ")


_PYGMENTS = []  # [whether Pygments can be imported], once it's been looked for


def _pygments_loaded():
    """Whether highlighting needs no Pygments loaded first (its lexers take ~0.1 s to import: done off the main
    thread, the first time): it's loaded, or not there to load."""
    if "pygments.lexers" in sys.modules:
        return True
    if not _PYGMENTS:
        import importlib.util
        _PYGMENTS.append(importlib.util.find_spec("pygments") is not None)
    return not _PYGMENTS[0]


def group_header(label, collapsed, toggle, plus=None, tooltip=None, current=False):
    """A list's group header, as Baude's sessions have them (Today, a folder) and Record's pages and notebooks: its
    name small and faint (current: in the accent's colour), an arrow just right of it that folds the group (toggle:
    called), shown while the pointer's near or while it's folded, and plus ((icon, tooltip, fn)), a + at its end. No
    button's card behind any of it. set_collapsed(on) turns its arrow."""
    box = Gtk.Box(spacing=2)
    box.add_css_class("sidebar-header")
    name = Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, tooltip_text=tooltip or label)
    name.add_css_class("sidebar-folder")
    if current:
        name.add_css_class("current")
    box.append(name)
    arrow = Gtk.Button(valign=Gtk.Align.CENTER)
    arrow.add_css_class("flat")
    arrow.add_css_class("folder-arrow")
    arrow.connect("clicked", lambda *_: toggle())
    box.append(arrow)
    box.append(Gtk.Box(hexpand=True))
    near = Gtk.EventControllerMotion()
    near.connect("enter", lambda *_: box.add_css_class("hover"))
    near.connect("leave", lambda *_: box.remove_css_class("hover"))
    box.add_controller(near)
    if plus is not None:
        icon, tip, fn = plus
        btn = Gtk.Button(icon_name=icon, tooltip_text=tip)
        btn.add_css_class("flat")
        btn.add_css_class("sidebar-plus")
        btn.connect("clicked", lambda *_: fn())
        box.append(btn)
        box.plus = btn

    def set_collapsed(on):
        arrow.set_icon_name("pan-end-symbolic" if on else "pan-down-symbolic")
        arrow.set_tooltip_text(wording.say("common.expand") if on else wording.say("common.collapse"))
        (arrow.add_css_class if on else arrow.remove_css_class)("collapsed")
    set_collapsed(collapsed)
    box.arrow, box.name, box.set_collapsed = arrow, name, set_collapsed
    return box


_MARKUP_PART = re.compile(r"<(/?)([a-zA-Z]+)([^>]*)>|&(#x?[0-9a-fA-F]+|[a-zA-Z]+);|[^<&]+", re.S)
_LINKS_OF = {}  # markup -> [(start, end, href)] (labels' links, as hover_uri reads them)


def markup_links(markup):
    """A label's links: [(start, end, href)], start and end bytes of its text as Pango lays it out (the markup's tags
    gone, its entities read)."""
    if (hit := _LINKS_OF.get(markup)) is not None:
        return hit
    out, at, opened = [], 0, None
    for m in _MARKUP_PART.finditer(markup):
        if m.group(2) is not None:
            if m.group(2) == "a":
                if m.group(1) and opened is not None:
                    out.append((opened[0], at, opened[1]))
                    opened = None
                elif not m.group(1):
                    href = re.search(r'href="([^"]*)"', m.group(3))
                    opened = (at, html.unescape(href.group(1)) if href else "")
        else:
            at += len((html.unescape(m.group(0)) if m.group(4) is not None else m.group(0)).encode("utf-8"))
    if len(_LINKS_OF) > 2000:
        _LINKS_OF.clear()
    _LINKS_OF[markup] = out
    return out


def hover_uri(label, x, y):
    """The link of label's under (x, y) (label's coordinates), or None. (Gtk.Label.get_current_uri is the focused
    link's, not the one the pointer is on.)"""
    if not label.get_use_markup() or "<a " not in (markup := label.get_label()):
        return None
    ox, oy = label.get_layout_offsets()
    inside, index, _trailing = label.get_layout().xy_to_index(int((x - ox) * Pango.SCALE), int((y - oy) * Pango.SCALE))
    if not inside:
        return None
    return next((href for start, end, href in markup_links(markup) if start <= index < end), None)


COPY_LINK = wording.say("tabs.copy_link_to_page")
COPY_LINK_KEY = "<Meta><Shift>l" if sys.platform == "darwin" else "<Control><Shift>l"  # (<primary> is Control here, on a Mac too)


class MarkdownView(Gtk.Box):
    """Markdown as widgets, one per block. While a reply streams in, only what changed is
    touched: finished blocks keep their widgets, the one still growing is updated in place,
    and deltas that arrive between two frames are drawn once. Nor is the text read again
    from the top each frame: only from where the blocks before were settled (markdown_parse)."""

    base_dir = os.getcwd()  # for relative image paths; the window keeps this in sync with its cwd

    def __init__(self, text=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.text, self.blocks, self.widgets, self.pending = "", [], [], None
        self.streaming = None  # while text is appended: [where to read on from (markdown_parse), its hash so far]
        if text:
            self.set_text(text)

    relink_due = None  # (its "map" handler, while it's out of sight with links that changed, and the links then:
    # SIGNAL and base_dir are every chat's in turn, Record's each Reader's as it's shown, after a page is mapped)

    def relink(self):
        """Draw the same text again with the links as they are now: blocks whose markup changed are
        updated in place (a table's cells, a code block's names, the same way); nothing else is touched. Out of
        sight (a far part of a long chat, a subagent's view not open), as it's next seen: Signal finds names all
        through a scroll, and a long chat has many replies naming each."""
        if not self.get_mapped():
            if self.relink_due is None:
                self.relink_due = self.connect("map", MarkdownView._relink_seen)
            self.relink_with = (SIGNAL, MarkdownView.base_dir)
            return
        if self.streaming is not None:
            self.streaming[0] = None  # (the blocks it settled have the links as they were: the next frame reads all)
        if self.pending is not None:
            return  # the next frame draws it anyway
        blocks = markdown_blocks(self.text)
        if len(blocks) != len(self.blocks):
            self.set_text(self.text)
            return
        for i, (old, new) in enumerate(zip(self.blocks, blocks)):
            if old != new and not self.update(self.widgets[i], old, new):
                w = self.render(new)
                self.insert_child_after(w, self.widgets[i])
                self.remove(self.widgets[i])
                self.widgets[i] = w
            elif new[0] == "code" and isinstance(self.widgets[i], CodeBlock):
                self.widgets[i].message = REF_MESSAGE
                self.widgets[i].relink()  # (its text is the same; what Signal knows may not be)
            elif new[0] == "table":
                relink_table(self.widgets[i])  # (likewise: a table's blocks hold its text, not its markup)
        self.blocks = blocks

    def _relink_seen(self):
        global SIGNAL
        self.disconnect(self.relink_due)
        self.relink_due = None
        was, (SIGNAL, MarkdownView.base_dir) = (SIGNAL, MarkdownView.base_dir), self.relink_with
        self.relink_with = None
        try:
            self.relink()
        finally:
            SIGNAL, MarkdownView.base_dir = was

    def append(self, delta):
        if self.streaming is None:
            self.streaming = [None, hashlib.sha1(self.text.encode("utf-8", "replace"))]
        self.streaming[1].update(delta.encode("utf-8", "replace"))  # (the hash markdown_parse would take of it all)
        self.text += delta
        if self.pending is None:  # drawn before the next frame, however many deltas come first
            self.pending = GLib.idle_add(self.flush, priority=GLib.PRIORITY_HIGH_IDLE)

    def flush(self):
        self.pending = None
        if self.streaming is None:
            self.set_text(self.text)
            return False
        since, digest = self.streaming
        blocks, self.streaming[0] = markdown_parse(self.text, since, digest.hexdigest()[:16])
        # (the blocks before the last it settled are the very ones shown: not compared again)
        self.draw(blocks, since[2] - 1 if since is not None and since[1] is self.blocks and since[2] else 0)
        return False

    def finish(self):
        """The message is complete: draw what's still waiting for the next frame now. A streamed text is
        read once more from the top, as every frame once was: the blocks settled while it came in were read
        with what was known then (a path that exists now, a name Signal has found since)."""
        if self.pending is not None or self.streaming is not None:
            self.set_text(self.text)

    def set_text(self, text):
        if self.pending is not None:
            GLib.source_remove(self.pending)
            self.pending = None
        self.text, self.streaming = text, None
        self.draw(markdown_blocks(text))

    def draw(self, blocks, same=0):
        """Show blocks in place of the ones shown: the widgets of the blocks that are the same are kept, and
        the first that changed is updated in place if it can be. same: how many at the start are known to be."""
        while same < min(len(blocks), len(self.blocks)) and blocks[same] == self.blocks[same]:
            same += 1
        stale = self.widgets[same:]
        del self.widgets[same:]
        widgets = self.widgets
        if stale and same < len(blocks) and self.update(stale[0], self.blocks[same], blocks[same]):
            widgets.append(stale.pop(0))
        for w in stale:
            self.remove(w)
            discard(w)
        for b in blocks[len(widgets):]:
            w = self.render(b)
            Gtk.Box.append(self, w)
            widgets.append(w)
        self.blocks, self.widgets = blocks, widgets

    @staticmethod
    def update(widget, old, new):
        """Change a block's widget to show new content of the same kind; False if it can't."""
        kind = new[0]
        if kind != old[0]:
            return False
        if kind in ("para", "quote"):
            set_markup_safe(widget, new[1])
        elif kind == "heading" and new[1] == old[1]:
            set_markup_safe(widget, f"<b>{new[2]}</b>")
        elif kind == "item" and new[1:3] == old[1:3]:
            set_markup_safe(widget.body, new[3])
        elif kind == "code" and new[1] == old[1] and isinstance(widget, CodeBlock):
            widget.message = REF_MESSAGE
            widget.set_code(new[2])
        else:
            return False
        return True

    def render(self, b):
        kind = b[0]
        if kind == "para":
            return sel_label(b[1])
        if kind == "heading":
            lbl = sel_label(f"<b>{b[2]}</b>")
            lbl.add_css_class(f"md-h{min(b[1], 3)}")
            return lbl
        if kind == "item":
            row = Gtk.Box(spacing=6, margin_start=6 + INDENT_STEP * b[1])
            marker = Gtk.Label(label=b[2], yalign=0, xalign=1, width_chars=2)
            marker.add_css_class("md-marker")
            body = sel_label(b[3])
            body.set_hexpand(True)
            body.list_prefix = "  " * b[1] + b[2] + " "  # (copied with it: ChatSelection)
            row.append(marker)
            row.append(body)
            row.body = body
            return row
        if kind == "quote":
            lbl = sel_label(b[1])
            lbl.add_css_class("md-quote")
            return lbl
        if kind == "hr":
            return Gtk.Separator(margin_top=4, margin_bottom=4)
        if kind == "code":
            block = CodeBlock(b[2], b[1], syntax=b[1] or None)  # (highlighted as its fence says: ```bash)
            block.message = REF_MESSAGE  # (its reply: markdown_blocks just read it)
            block.relink()
            return block
        if kind == "image":
            return self.render_image(b[1], b[2])
        if kind == "table":
            return markdown_table(b[1])
        return sel_label(html.escape(str(b)))

    def render_image(self, alt, src):
        written = src[7:] if src.startswith("file://") else src  # (the path, as the reply has it: over the image)
        path = os.path.join(self.base_dir, os.path.expanduser(written))
        if "://" not in src or src.startswith("file://"):
            try:
                with open(path, "rb") as f:
                    return with_image_link(image_widget(f.read(), path=path, name=alt), written)
            except (OSError, GLib.Error):
                pass
        link = sel_label(wording.say("images.link", link=f'<a href="{html.escape(src)}">{html.escape(alt or src)}</a>'))
        if not src.startswith(("http://", "https://")):
            return link
        holder = Gtk.Box()  # the link until the image arrives (or if it can't be loaded)
        holder.append(link)

        def done(data, error):
            if data is not None:
                try:
                    pic = image_widget(data, url=src, name=alt)
                except GLib.Error as e:
                    link.set_tooltip_text(wording.say("images.couldnt_show", why=e.message))
                    return
                holder.remove(link)
                holder.append(with_image_link(pic, src))
            else:
                link.set_tooltip_text(wording.say("images.couldnt_load", why=error))
        fetch_web_image(src, done)
        return holder
