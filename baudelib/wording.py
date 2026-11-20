"""Baude's copy: the words every app shows, read from schema/copy as this is imported. Baude, Record and Emission
say them with say(); the Android app's build makes Kotlin of the same files (Wording.Group.key). No GTK.

schema/copy has a file for each group of them (a feature, or what a part of the window shows): {"description": what
the group is, "messages": {key: message}}. A message is Unicode MessageFormat 2's (LDML 47), as much of it as the
copy needs: text with {$name} in it for what's put in ({$n :number} for a number, said with its thousands grouped);
\\{, \\} and \\\\ for the characters themselves; or .input {$n :number} (or :string), then .match $n and its
variants, each its key (an exact value, one or other for a number's plural category, * for the rest) and its
{{pattern}}. A message is said by its group's file's name and its key: say("hosts.looking", host=name).
Each wording is there once, under one key."""
import json
import os
import re

FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "schema", "copy")
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_messages = {}  # ("group.key": its message, parsed)


class CopyError(Exception):
    """A file in schema/copy isn't as it must be, said with its name and the message's key."""


def _pairs(kv):
    obj = {}
    for k, v in kv:
        if k in obj:
            raise ValueError(f"{k!r} is there twice in one object")
        obj[k] = v
    return obj


class _Parser:
    """A message's text read into (declared: {name: its function}, selectors, [(keys, pattern)]), a pattern a list of
    text and (name, function or None): a simple message is one variant with no keys."""

    def __init__(self, text):
        self.s, self.i = text, 0

    def fail(self, what):
        raise ValueError(f"{what} at {self.i}: {self.s!r}")

    def peek(self, word):
        return self.s.startswith(word, self.i)

    def take(self, word):
        if not self.peek(word):
            self.fail(f"{word!r} expected")
        self.i += len(word)

    def space(self, needed=False):
        start = self.i
        while self.i < len(self.s) and self.s[self.i] in " \t\n\r　":
            self.i += 1
        if needed and self.i == start:
            self.fail("a space expected")

    def name(self):
        m = re.compile(r"[a-z][a-z0-9_]*").match(self.s, self.i)
        if not m:
            self.fail("a name expected")
        self.i = m.end()
        return m[0]

    def literal(self):
        if self.peek("|"):
            end = self.s.find("|", self.i + 1)
            if end < 0:
                self.fail("an unclosed |literal|")
            self.i, out = end + 1, self.s[self.i + 1:end]
            return out
        m = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|[A-Za-z_][\w.-]*").match(self.s, self.i)
        if not m:
            self.fail("a key expected")
        self.i = m.end()
        return m[0]

    def expression(self):
        """{$name} or {$name :function}, as (name, function or None)."""
        self.take("{")
        self.space()
        self.take("$")
        var = self.name()
        self.space()
        fn = None
        if self.peek(":"):
            self.i += 1
            fn = self.name()
            if fn not in ("number", "string"):
                self.fail(f"no function :{fn}")
            self.space()
        self.take("}")
        return var, fn

    def pattern(self, quoted):
        parts, text = [], []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                if self.i + 1 >= len(self.s) or self.s[self.i + 1] not in "\\{}|":
                    self.fail("an escape of \\, {, } or | expected")
                text.append(self.s[self.i + 1])
                self.i += 2
            elif quoted and self.peek("}}"):
                break
            elif c == "{":
                if text:
                    parts.append("".join(text))
                    text = []
                parts.append(self.expression())
            elif c == "}":
                self.fail("a lone }")
            else:
                text.append(c)
                self.i += 1
        if text:
            parts.append("".join(text))
        return parts

    def message(self):
        self.space()
        if not self.peek("."):
            self.i = 0
            return {}, [], [((), self.pattern(False))]
        declared = {}
        while self.peek(".input"):
            self.i += len(".input")
            self.space()
            var, fn = self.expression()
            if var in declared or fn is None:
                self.fail(f"${var} declared twice, or with no function")
            declared[var] = fn
            self.space()
        if self.peek("{{"):
            self.i += 2
            out = self.pattern(True)
            self.take("}}")
            self.space()
            if self.i != len(self.s):
                self.fail("more after the pattern")
            return declared, [], [((), out)]
        self.take(".match")
        selectors = []
        while True:
            self.space(needed=True)
            if not self.peek("$"):
                break
            self.i += 1
            var = self.name()
            if var not in declared:
                self.fail(f"${var} is matched on, but not declared with .input")
            selectors.append(var)
        if not selectors:
            self.fail("a selector expected")
        variants = []
        while self.i < len(self.s):
            keys = []
            while not self.peek("{{"):
                if self.peek("*"):
                    self.i += 1
                    keys.append("*")
                else:
                    keys.append(self.literal())
                self.space(needed=not self.peek("{{"))
            if len(keys) != len(selectors):
                self.fail(f"{len(selectors)} keys expected")
            self.i += 2
            out = self.pattern(True)
            self.take("}}")
            variants.append((tuple(keys), out))
            self.space()
        if not any(all(k == "*" for k in keys) for keys, _ in variants):
            self.fail("no variant for the rest (* for each selector)")
        return declared, selectors, variants


def parse(text):
    """A message's text, read: (declared, selectors, variants), as _Parser has them."""
    return _Parser(text).message()


def _number(v):
    if isinstance(v, int):
        return f"{v:,}"
    return f"{v:,.3f}".rstrip("0").rstrip(".")


def _category(n):
    """n's plural category, as English has them."""
    return "one" if n == 1 else "other"


def _rank(fn, key, value):
    """How well key fits value (0 an exact value, 1 a plural category, 2 the rest), or None if it doesn't."""
    if key == "*":
        return 2
    if fn == "number":
        try:
            if float(key) == value:
                return 0
        except ValueError:
            pass
        return 1 if key == _category(value) else None
    return 0 if key == str(value) else None


def params(message):
    """The names a parsed message is given, in the order they first come."""
    declared, _, variants = message
    out = list(declared)
    for _, pattern in variants:
        out += [p[0] for p in pattern if isinstance(p, tuple) and p[0] not in out]
    return out


def _load():
    seen = {}
    for f in sorted(os.listdir(FOLDER)):
        if f.startswith(".") or not f.endswith(".json"):
            continue
        group, path = f[:-5], f"schema/copy/{f}"
        if not _NAME.fullmatch(group):
            raise CopyError(f"{path}: not named in lower case, words joined by _")
        try:
            with open(os.path.join(FOLDER, f), "rb") as fh:
                doc = json.load(fh, object_pairs_hook=_pairs)
        except ValueError as e:
            raise CopyError(f"{path}: {e}") from None
        if set(doc) != {"description", "messages"}:
            raise CopyError(f"{path}: a description and messages, and nothing else")
        for key, text in doc["messages"].items():
            if not _NAME.fullmatch(key) or not isinstance(text, str):
                raise CopyError(f"{path}: {key}: a lower-case key and a message's text")
            if text in seen:
                raise CopyError(f"{path}: {key} says what {seen[text]} says")
            seen[text] = f"{group}.{key}"
            try:
                _messages[f"{group}.{key}"] = parse(text)
            except ValueError as e:
                raise CopyError(f"{path}: {key}: {e}") from None


def say(key, /, **args):
    """The message key ("group.key") says, args put in it: each name it's given, and no others."""
    declared, selectors, variants = message = _messages[key]
    if set(args) != set(want := params(message)):
        raise TypeError(f"{key} is given {', '.join(want) or 'nothing'}, not {', '.join(args) or 'nothing'}")
    best = None
    for keys, pattern in variants:
        ranks = [_rank(declared[s], k, args[s]) for s, k in zip(selectors, keys)]
        if None not in ranks and (best is None or ranks < best[0]):
            best = (ranks, pattern)
    out = []
    for part in best[1]:
        if isinstance(part, str):
            out.append(part)
        else:
            name, fn = part
            out.append(_number(args[name]) if (fn or declared.get(name)) == "number" else str(args[name]))
    return "".join(out)


_load()
