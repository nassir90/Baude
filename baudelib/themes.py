"""themes — the themes this machine's host keeps (the themes, theme_save and theme_remove ops) and what each comes to:
a folder, $XDG_DATA_HOME/baude/themes/<name>/, holding theme.css (GTK CSS, for the desktop apps, drawn over all of
theirs) and tokens.json (colours over schema/tokens', for every app, the phone's too). Either may be left out.

tokens.json is a resolver document as schema/tokens/baude.resolver.json is (W3C Design Tokens, 2025.10), its sources
written in it: its sets go over Baude's own, then its theme modifier's light or dark context over Baude's light or
dark. A theme sets colours only, and only libadwaita's and baude's (the groups every app draws in): the phone's
measures are made as it's built. What it comes to is worked out here, for every app alike: each colour that isn't
Baude's own, light and dark, as #rrggbb or #rrggbbaa (a colour another is made of, set, makes that one too). No GTK."""
import copy
import json
import os
import re
import shutil

from baudelib import tokens

GROUPS = ("libadwaita", "baude")  # (what a theme's colours may set)
CONTEXTS = ("light", "dark")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}")
CSS_FILE, TOKENS_FILE = "theme.css", "tokens.json"
SIZE_MAX = 256 * 1024  # (a theme's file, at most)
ALPHA = re.compile(r"alpha\(\{([\w.-]+)\}, ([\d.]+)\)")  # (a colour made of another: dev.baude.gtk's)


def folder():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "themes")


def check_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("A theme's name is 1 to 40 letters, digits, spaces, - or _, starting with a letter or digit.")
    return name


def names():
    """The themes here, by name, in order."""
    try:
        listed = os.listdir(folder())
    except OSError:
        return []
    return sorted((n for n in listed if NAME.fullmatch(n) and any(
        os.path.isfile(os.path.join(folder(), n, f)) for f in (CSS_FILE, TOKENS_FILE))), key=str.lower)


def files(name):
    """A theme's files as they're written: {"css": text or None, "tokens": data or None}; None if it isn't here."""
    where = os.path.join(folder(), check_name(name))
    if not os.path.isdir(where):
        return None
    out = {"css": None, "tokens": None}
    for key, f in (("css", CSS_FILE), ("tokens", TOKENS_FILE)):
        try:
            with open(os.path.join(where, f), encoding="utf-8") as fh:
                text = fh.read(SIZE_MAX + 1)
        except FileNotFoundError:
            continue
        if len(text) > SIZE_MAX:
            raise ValueError(f"{name}'s {f} is over {SIZE_MAX // 1024} KB.")
        if key == "css":
            out["css"] = text
        else:
            try:
                out["tokens"] = json.loads(text)
            except ValueError as e:
                raise ValueError(f"{name}'s {f} isn't JSON: {e}") from None
    return out


def _sources(doc, context):
    """The token trees a theme's resolver document puts over Baude's for context, in its order."""
    if not isinstance(doc, dict):
        raise ValueError("tokens.json is a resolver document: {\"sets\": ..., \"modifiers\": {\"theme\": "
                         "{\"contexts\": {\"light\": [...], \"dark\": [...]}}}}.")
    order = doc.get("resolutionOrder") or ([{"$ref": f"#/sets/{s}"} for s in doc.get("sets") or {}] +
                                           [{"$ref": f"#/modifiers/{m}"} for m in doc.get("modifiers") or {}])
    out = []
    for step in order:
        ref = step.get("$ref", "") if isinstance(step, dict) else ""
        parts = ref.split("/")
        if len(parts) != 3 or parts[0] != "#" or parts[1] not in ("sets", "modifiers"):
            raise ValueError(f"tokens.json: {ref!r} isn't a set or a modifier of its own (#/sets/<name>).")
        part = (doc.get(parts[1]) or {}).get(parts[2])
        if not isinstance(part, dict):
            raise ValueError(f"tokens.json: there's no {ref}.")
        if parts[1] == "modifiers":
            contexts = part.get("contexts") or {}
            if set(contexts) - set(CONTEXTS):
                raise ValueError("tokens.json: a modifier's contexts are light and dark.")
            part = contexts.get(context) or []
        else:
            part = part.get("sources") or []
        for source in part:
            if not isinstance(source, dict) or "$ref" in source:
                raise ValueError("tokens.json: its sources are written in it (no $ref to other files).")
            out.append(source)
    return out


def _flat(node, path=(), kind=None, ext=None, into=None):
    """Every token under node, by its dotted path: (its $value, its $type, its $extensions)."""
    into = {} if into is None else into
    kind, ext = node.get("$type", kind), {**(ext or {}), **node.get("$extensions", {})}
    if "$value" in node:
        into[".".join(path)] = (node["$value"], kind, ext)
        return into
    for k, v in node.items():
        if not k.startswith("$") and isinstance(v, dict):
            _flat(v, path + (k,), kind, ext, into)
    return into


def _rgba(value, where):
    """A colour's value as (r, g, b, a), 0 to 1: sRGB's components, or its hex alone."""
    if not isinstance(value, dict):
        raise ValueError(f"{where}: a colour is {{\"colorSpace\": \"srgb\", \"components\": [r, g, b], "
                         "\"alpha\"?: a}} or {\"hex\": \"#rrggbb\"}, or an alias ({libadwaita.view_bg_color}).")
    if "components" in value:
        if value.get("colorSpace", "srgb") != "srgb":
            raise ValueError(f"{where}: a theme's colours are sRGB.")
        c = value["components"]
        if not (isinstance(c, list) and len(c) == 3 and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in c)):
            raise ValueError(f"{where}: components are three numbers from 0 to 1.")
        rgb = list(c)
    elif isinstance(value.get("hex"), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value["hex"]):
        rgb = [int(value["hex"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
    else:
        raise ValueError(f"{where}: a colour needs its components or its hex.")
    a = value.get("alpha", 1)
    if not isinstance(a, (int, float)) or not 0 <= a <= 1:
        raise ValueError(f"{where}: alpha is from 0 to 1.")
    return (*rgb, a)


def _resolve(flat, path, seen=()):
    """path's colour as (r, g, b, a), its aliases followed."""
    if path in seen:
        raise ValueError(f"tokens.json: an alias's loop, {' -> '.join(seen + (path,))}")
    if path not in flat:
        raise ValueError(f"tokens.json: there's no token {path}.")
    value, kind, _ext = flat[path]
    if kind != "color":
        raise ValueError(f"{path} is a {kind}: a theme sets colours.")
    if (to := tokens.alias(value)) is not None:
        return _resolve(flat, to, seen + (path,))
    return _rgba(value, path)


def hex_of(rgba):
    r, g, b, a = (round(x * 255) for x in rgba)
    return f"#{r:02x}{g:02x}{b:02x}" + (f"{a:02x}" if a < 255 else "")


def _colour(flat, path, set_=()):
    """path's colour in flat: one made of another (dev.baude.gtk's alpha({other}, n)) as GTK's CSS makes it, unless
    the theme sets it itself."""
    made = ALPHA.fullmatch(flat[path][2].get(tokens.GTK, "")) if path not in set_ else None
    if made and made.group(1) in flat:
        r, g, b, a = _resolve(flat, made.group(1))
        return r, g, b, a * float(made.group(2))
    return _resolve(flat, path)


def colours(doc):
    """What a theme's tokens.json comes to: {"light": {path: hex}, "dark": {path: hex}}, each colour of libadwaita's
    and baude's that isn't Baude's own. ValueError, saying what's wrong, for one that isn't as a theme's is."""
    out = {}
    for context in CONTEXTS:
        base = tokens.tree(context)
        own, merged, set_ = _flat(base), copy.deepcopy(base), set()
        for source in _sources(doc, context) if doc is not None else ():
            for path in _flat(source):
                if path.split(".")[0] not in GROUPS:
                    raise ValueError(f"{path}: a theme sets libadwaita's and baude's colours only.")
                if path not in own:
                    raise ValueError(f"tokens.json: there's no token {path} (schema/tokens has those it can set).")
                set_.add(path)
            tokens._merge(merged, copy.deepcopy(source))
        flat = _flat(merged)
        out[context] = {}
        for path, (_value, kind, _ext) in flat.items():
            if path.split(".")[0] in GROUPS and kind == "color":
                if (now := hex_of(_colour(flat, path, set_))) != hex_of(_colour(own, path)):
                    out[context][path] = now
    return out


def theme(name):
    """A theme as the wire has it (Theme.json): its name, its CSS and its colours light and dark; an error in place
    of them if its files aren't as a theme's are."""
    try:
        f = files(name)
        if f is None:
            return {"name": name, "error": f"There's no theme {name}."}
        return {"name": name, "css": f["css"] or "", **colours(f["tokens"])}
    except (OSError, ValueError) as e:
        return {"name": name, "error": str(e)}


def listing():
    return [theme(n) for n in names()]


def save(name, css=None, tokens_doc=None):
    """A theme written (made, or its files replaced), once its tokens are checked: what it had before
    ({"css", "tokens"}, or None if it's new), for its undo."""
    check_name(name)
    if css is not None and (not isinstance(css, str) or len(css.encode()) > SIZE_MAX):
        raise ValueError(f"A theme's CSS is text, at most {SIZE_MAX // 1024} KB.")
    if css is None and tokens_doc is None:
        raise ValueError("A theme has CSS, tokens or both.")
    if tokens_doc is not None:
        colours(tokens_doc)
        if len(json.dumps(tokens_doc).encode()) > SIZE_MAX:
            raise ValueError(f"A theme's tokens are at most {SIZE_MAX // 1024} KB.")
    before = files(name)
    where = os.path.join(folder(), name)
    os.makedirs(where, exist_ok=True)
    for f, data in ((CSS_FILE, css), (TOKENS_FILE, None if tokens_doc is None else json.dumps(tokens_doc, indent=2))):
        path = os.path.join(where, f)
        if data is None:
            if os.path.exists(path):
                os.remove(path)
            continue
        with open(path + ".new", "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(path + ".new", path)
    return before


def remove(name):
    """A theme taken away: what it had ({"css", "tokens"}), for its undo; None if it wasn't here."""
    before = files(name)
    if before is not None:
        shutil.rmtree(os.path.join(folder(), name))
    return before
