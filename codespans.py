"""codespans — code highlighted: Pygments' spans of a block (code_spans), a regex's and grep's or rg's patterns in a
command (regex_spans, grep_patterns), numbered lines (numbered_spans, line_spans), and the style's colours
(code_style, code_attrs).
"""
import bisect
import os
import re

from gi.repository import Pango  # noqa: E402,F401


# ---------------------------------------------------------------- code view --
# Code shown in Baude itself (the side panel's Code page, and the chat's code blocks: a tool call's command, a file
# it read or wrote, an edit's diff, a reply's fenced code): highlighted. Highlighting is a backend that turns text
# into (start, end, token type) spans: Pygments (500+ languages) when it's installed, plain text when it isn't.
# Another backend (GtkSourceView once gir1.2-gtksource-5 is installed, tree-sitter) only has to produce the same
# spans. A regex's are Baude's own (regex_spans: Pygments has no lexer for one), in the same style's colours.

CODE_STYLES = {False: "default", True: "github-dark"}  # Pygments style, by dark mode
CODE_HIGHLIGHT_MAX = 20_000  # characters of a code block highlighted (~500 lines): its first, a longer one's rest plain
SHELL_LANGS = {"bash", "sh", "shell", "zsh", "ksh"}  # (a command's; and code fences': a grep's pattern a regex in them)
LINE_NUMBER = "line-number"  # the token type of a Read's line numbers: dim, as the Code page's gutter has them


def code_spans(text, path="", lang=""):
    """Token spans for text: [(start, end, token type)] in character offsets, by lang (a language's name or alias:
    bash, py, a code fence's tag; regex: regex_spans) or else path's file name. [] without Pygments, or for a language
    it has no lexer for. text must already use \\n line ends (Pygments normalizes them). In a shell command a grep's
    or rg's pattern is a regex (grep_patterns), Pygments or not."""
    if lang.lower() == "regex":
        return regex_spans(text)
    spans, lexer = [], None
    try:
        from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
        from pygments.token import Text, Whitespace
        from pygments.util import ClassNotFound
    except ImportError:
        pass
    else:
        opts = {"stripnl": False, "ensurenl": False, "tabsize": 0}
        try:
            lexer = get_lexer_by_name(lang, **opts) if lang else \
                get_lexer_for_filename(os.path.basename(path) or "x.txt", text[:4096], **opts)
        except ClassNotFound:
            pass
    if lexer is not None:
        pos = 0
        for ttype, value in lexer.get_tokens(text):
            end = pos + len(value)
            if value.strip() and ttype not in (Text, Whitespace):
                if spans and spans[-1][2] is ttype and spans[-1][1] == pos:
                    spans[-1] = (spans[-1][0], end, ttype)  # one span for a run of the same token
                else:
                    spans.append((pos, end, ttype))
            pos = end
        if pos != len(text):  # (Pygments dropped something, a byte-order mark: its offsets aren't text's)
            spans = []
    if lang.lower() in SHELL_LANGS or lexer is not None and "bash" in lexer.aliases:
        spans = _with_regexes(text, spans)
    return spans


_REGEX_TOKENS = {"escape": "Literal.String.Escape", "class": "Literal.String", "group": "Keyword",
                 "quantifier": "Name.Function", "anchor": "Name.Entity"}  # (Pygments' names: the code's colours)
# what CODE_STYLES give those (light, dark), for a regex drawn without Pygments
_REGEX_LOOKS = {"Literal.String.Escape": (("#AA5D1F", True, False), ("#79c0ff", False, False)),
                "Literal.String": (("#BA2121", False, False), ("#a5d6ff", False, False)),
                "Keyword": (("#008000", True, False), ("#ff7b72", False, False)),
                "Name.Function": (("#0000FF", False, False), ("#d2a8ff", True, False)),
                "Name.Entity": (("#717171", True, False), ("#ffa657", False, False))}
_RE_ESCAPE = re.compile(r"\\(?:x\{[0-9A-Fa-f]*\}|x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|[pPN]\{[^}]*\}|[pP][A-Za-z]|k<\w+>"
                        r"|\d+|.)", re.S)
_RE_GROUP = re.compile(r"\((?:\?(?:P?<\w+>|'\w+'|P=\w+\)|<[=!]|[:=!>|]|#[^)]*\)|[a-zA-Z^-]+[:)]))?")
_RE_COUNT = re.compile(r"\{(?:\d+(?:,\d*)?|,\d+)\}")


def regex_spans(text, bre=False):
    """A regex's parts as code_spans' spans: escapes, character classes, groups and alternation, quantifiers, anchors
    and the dot (what's left is literal: plain). bre: grep's basic syntax, where \\( \\) \\| \\{ \\} \\+ \\? are the
    operators and the bare ones literal."""
    spans, i, n, unclosed = [], 0, len(text), False  # (unclosed: a "[" that never closed; none after it does)

    def add(a, b, kind):
        spans.append((a, b, _REGEX_TOKENS[kind]))
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            d = text[i + 1]
            if bre and d in "()|":
                add(i, i + 2, "group")
                i += 2
            elif bre and d in "+?":
                add(i, i + 2, "quantifier")
                i += 2
            elif bre and d == "{" and (end := text.find("\\}", i + 2)) > 0 and \
                    re.fullmatch(r"\d+(,\d*)?|,\d+", text[i + 2:end]):
                add(i, end + 2, "quantifier")
                i = end + 2
            else:
                end = _RE_ESCAPE.match(text, i).end()
                add(i, end, "escape")
                i = end
        elif c == "[" and not unclosed and (end := _class_end(text, i, bre)):
            j, k = i, i + 1  # (escapes in it drawn as escapes, the rest the class's)
            while k < end - 1:
                if text[k] == "\\" and not bre and k + 1 < end - 1:
                    e = min(_RE_ESCAPE.match(text, k).end(), end - 1)
                    if k > j:
                        add(j, k, "class")
                    add(k, e, "escape")
                    j = k = e
                else:
                    k += 1
            add(j, end, "class")
            i = end
        elif not bre and c == "(":
            end = _RE_GROUP.match(text, i).end()
            add(i, end, "group")
            i = end
        elif not bre and c in ")|":
            add(i, i + 1, "group")
            i += 1
        elif c == "*" or not bre and (c in "+?" or c == "{" and _RE_COUNT.match(text, i)):
            end = _RE_COUNT.match(text, i).end() if c == "{" else i + 1
            if not bre and end < n and text[end] in "?+":  # (lazy, possessive)
                end += 1
            add(i, end, "quantifier")
            i = end
        elif c in "^$.":
            add(i, i + 1, "anchor")
            i += 1
        else:
            unclosed = unclosed or c == "["
            i += 1
    return spans


def _class_end(text, i, bre=False):
    """Where the character class opened at text[i] ("[") ends (just after its "]"), or None if it doesn't."""
    j, n = i + 1, len(text)
    if j < n and text[j] == "^":
        j += 1
    if j < n and text[j] == "]":  # (first, a "]" is one of its characters)
        j += 1
    while j < n:
        if text[j] == "\\" and not bre:
            j += 2
        elif text.startswith(("[:", "[=", "[."), j) and (k := text.find(text[j + 1] + "]", j + 2)) > 0:
            j = k + 2  # ([:alpha:])
        elif text[j] == "]":
            return j + 1
        else:
            j += 1
    return None


# A shell command's grep and rg: which of their options take a value (the word after them, or the rest of the word),
# and so which word's the pattern
_GREP_SHORT = {"grep": "ABCDdefm", "rg": "ABCEMTdefgjmrt"}
_GREP_LONG = {"--regexp", "--file", "--include", "--exclude", "--exclude-dir", "--max-count", "--context",
              "--after-context", "--before-context", "--label", "--binary-files", "--devices", "--directories",
              "--glob", "--iglob", "--type", "--type-not", "--type-add", "--max-depth", "--replace", "--encoding",
              "--max-columns", "--threads", "--sort", "--sortr", "--engine", "--max-filesize", "--pre", "--ignore-file"}
_RG_LONG = _GREP_LONG | {"--color", "--colors", "--context-separator", "--path-separator", "--field-match-separator",
                         "--pre-glob", "--dfa-size-limit", "--regex-size-limit"}
_GREP_DIALECT = {"grep": "bre", "zgrep": "bre", "egrep": "ere", "fgrep": None, "rg": "ere"}
_SH_TOKEN = re.compile(r"(?P<sep>&&|\|\||\$\(|[;&|()`\n])|(?P<space>[ \t]+|\\\n)|#[^\n]*"
                       r"|(?P<word>(?:'[^']*'?|\"(?:[^\"\\]|\\.)*\"?|\\.|[^\s'\"\\;&|()`])+)|.", re.S)
_SH_PREFIX = {"sudo", "xargs", "time", "nice", "env", "command", "exec", "then", "do", "else", "if", "elif", "while",
              "until", "!", "{"}  # (words before a command that aren't it)


def grep_patterns(cmd):
    """Where the patterns of a shell command's grep, egrep, git grep and rg calls are: [(start, end, bre)], the text
    inside a pattern's quotes. None for a fixed string (-F), a pattern read from a file (-f), or one the shell changes
    before grep sees it (a $ or ` in double quotes, a backslash or $ in a bare word)."""
    words, out, at_cmd = [], [], True
    for m in _SH_TOKEN.finditer(cmd):
        if m.group("sep"):
            words.append(None)
        elif m.group("word"):
            words.append((m.start(), m.end(), m.group()))
    k = 0
    while k < len(words):
        w = words[k]
        k += 1
        if w is None:
            at_cmd = True
            continue
        if not at_cmd:
            continue
        name = os.path.basename(w[2])
        if name in _SH_PREFIX or re.fullmatch(r"\w+=.*", w[2], re.S) or w[2].startswith("-"):
            continue  # (still before the command: sudo, an option of xargs', VAR=value)
        at_cmd = False
        if name == "git" and k < len(words) and words[k] and words[k][2] == "grep":
            name, k = "grep", k + 1
        if name not in _GREP_DIALECT:
            continue
        end = next((j for j in range(k, len(words)) if words[j] is None), len(words))
        out += _grep_args(cmd, "rg" if name == "rg" else "grep", _GREP_DIALECT[name], words[k:end])
        k = end
    return out


def _grep_args(cmd, tool, dialect, args):
    """A grep's (or rg's) patterns, of its words: [(start, end, bre)] (grep_patterns)."""
    given, first, i = [], None, 0  # (-e's patterns: (start, end) in cmd; the first word that's no option's)
    takes = _RG_LONG if tool == "rg" else _GREP_LONG
    while i < len(args):
        s, e, w = args[i]
        i += 1
        if w == "--":
            if first is None and i < len(args):
                first = args[i][:2]
            break
        if w.startswith("--"):
            opt, eq, _ = w.partition("=")
            dialect = {"--fixed-strings": None, "--extended-regexp": "ere", "--basic-regexp": "bre",
                       "--perl-regexp": "ere", "--pcre2": "ere", "--files": None, "--type-list": None}.get(opt, dialect)
            value = (s + len(opt) + 1, e) if eq else args[i][:2] if opt in takes and i < len(args) else None
            if opt in takes and not eq:
                i += 1
            if opt in ("--regexp", "--file") and value:
                given.append(value if opt == "--regexp" else None)
        elif w.startswith("-") and len(w) > 1:
            for j, ch in enumerate(w[1:], 1):
                if ch == "F":
                    dialect = None
                elif ch in "EGP" and not (tool == "rg" and ch == "E"):
                    dialect = "bre" if ch == "G" else "ere"
                if ch in _GREP_SHORT[tool]:
                    value = (s + j + 1, e) if j + 1 < len(w) else args[i][:2] if i < len(args) else None
                    if j + 1 == len(w):
                        i += 1
                    if ch in "ef" and value:
                        given.append(value if ch == "e" else None)
                    break
        elif first is None:
            first = (s, e)
    if dialect is None:
        return []
    out = []
    for span in given if given else [first] if first else []:
        if span is not None and (inside := _shell_literal(cmd, *span)):
            out.append((*inside, dialect == "bre"))
    return out


def _shell_literal(cmd, s, e):
    """(start, end) of what a shell word (cmd[s:e]) is to the command it's given to, if that's the word's own text
    inside its quotes, else None."""
    w = cmd[s:e]
    if len(w) >= 2 and w[0] == w[-1] == "'" and "'" not in w[1:-1]:
        return s + 1, e - 1
    if len(w) >= 2 and w[0] == w[-1] == '"' and not re.search(r'[$`"]|\\["\\$`]', w[1:-1]):
        return s + 1, e - 1
    if w and not re.search(r"[\\$'\"`]", w):
        return s, e
    return None


def _with_regexes(text, spans):
    """A shell command's spans with its greps' patterns drawn as regexes, in place of the strings they're in."""
    cuts = [(a, b, regex_spans(text[a:b], bre)) for a, b, bre in grep_patterns(text) if a < b]
    if not cuts:
        return spans
    out, starts = [], [a for a, _, _ in cuts]
    for s, e, t in spans:  # (what of each is outside the patterns, their quotes say: the patterns are in order)
        k, x = max(bisect.bisect_right(starts, s) - 1, 0), s
        while k < len(cuts) and cuts[k][0] < e:
            a, b, _ = cuts[k]
            if b > x:
                if a > x:
                    out.append((x, a, t))
                x = b
            k += 1
        if x < e:
            out.append((x, e, t))
    for a, _, found in cuts:
        out += [(a + x, a + y, t) for x, y, t in found]
    return sorted(out, key=lambda span: span[0])


_READ_NUMBER = re.compile(r" *\d+(?:→|\t)")  # Claude Code's Read: "   186→    text" (or a tab, as cat -n has it)


def numbered_spans(text, path):
    """A Read's output highlighted as path's code: each line its number (dim: LINE_NUMBER), then the file's line. A
    line that has no number (a note after the file) isn't code: it's left plain."""
    cuts, code, at = [], [], 0
    for line in text.split("\n"):
        m = _READ_NUMBER.match(line)
        cuts.append((at, m.end() if m else None))
        code.append(line[m.end():] if m else "")
        at += len(line) + 1
    src = "\n".join(code)
    out = []
    for (at, cut), spans in zip(cuts, line_spans(src, code_spans(src, path))):
        if cut is not None:
            out.append((at, at + cut, LINE_NUMBER))
            out += [(at + cut + s, at + cut + e, t) for s, e, t in spans]
    return out


def line_spans(text, spans):
    """spans (in text) cut at its line ends: for each line, its [(start, end, token type)] from the line's start."""
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    out = [[] for _ in starts]
    for s, e, t in spans:
        i = bisect.bisect_right(starts, s) - 1
        while i < len(starts) and s < e:
            stop = min(e, starts[i + 1] - 1 if i + 1 < len(starts) else len(text))
            if s < stop:
                out[i].append((s - starts[i], stop - starts[i], t))
            i += 1
            s = max(s, starts[i]) if i < len(starts) else e
    return out


_LOOKS = {}  # (token type, dark) -> code_style's answer


def code_style(ttype, dark):
    """(foreground, bold, italic) for a token type (Pygments', or its name: a regex's) in the light or dark Pygments
    style. Without Pygments, a regex's parts are in the colours those styles give them, and nothing else is coloured."""
    if (look := _LOOKS.get((ttype, dark))) is not None:
        return look
    try:
        from pygments.styles import get_style_by_name
        from pygments.token import string_to_tokentype
    except ImportError:
        look = _REGEX_LOOKS[ttype][dark] if ttype in _REGEX_LOOKS else (None, False, False)
    else:
        try:
            st = get_style_by_name(CODE_STYLES[dark]).style_for_token(
                string_to_tokentype(ttype) if isinstance(ttype, str) else ttype)
            look = (f"#{st['color']}" if st.get("color") else None), bool(st.get("bold")), bool(st.get("italic"))
        except Exception:  # noqa: BLE001 (a style Pygments doesn't have: plain)
            look = None, False, False
    _LOOKS[(ttype, dark)] = look
    return look


def code_attrs(text, spans, dark, skip=()):
    """Pango attributes drawing text's spans (code_spans') in the light or dark style's colours, but none in skip's
    (start, end) byte ranges (a link's: it has its own). Pango counts in UTF-8 bytes."""
    attrs, simple, pos, at = Pango.AttrList(), text.isascii(), 0, 0
    for s, e, ttype in spans:
        if simple:
            b0, b1 = s, e
        else:  # (spans come in order, none in another: each one's bytes counted on from the last's)
            at += len(text[pos:s].encode())
            b0 = at
            at += len(text[s:e].encode())
            b1, pos = at, e
        if skip and any(x < b1 and b0 < y for x, y in skip):
            continue
        if ttype == LINE_NUMBER:
            made = [Pango.attr_foreground_alpha_new(0x7000)]
        else:
            fg, bold, italic = code_style(ttype, dark)
            made = [Pango.attr_foreground_new(*(int(fg[k:k + 2], 16) * 257 for k in (1, 3, 5)))] if fg else []
            if bold:
                made.append(Pango.attr_weight_new(Pango.Weight.BOLD))
            if italic:
                made.append(Pango.attr_style_new(Pango.Style.ITALIC))
        for a in made:
            a.start_index, a.end_index = b0, b1
            attrs.insert(a)
    return attrs
