"""Baude's design tokens (schema/tokens: W3C Design Tokens, the 2025.10 format, its resolver putting the light and the
dark ones together), read as Baude, Record and Emission start: their CSS's measures and colours, and their drawing's.
The Android app's build makes Kotlin of the same files. No GTK."""
import json
import os
import re

FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "schema", "tokens")
RESOLVER = "baude.resolver.json"
NAMED = "libadwaita"  # (the group of libadwaita's named colours: GTK's CSS names each, @<its name>)
GTK = "dev.baude.gtk"  # ($extensions: a colour as GTK's CSS makes it, {aliases} in it, where it's not just a name)
REF = re.compile(r"\{([\w-]+(?:\.[\w-]+)+)\}")  # (an alias in a CSS template: {gap.inside})
_trees = {}


def _read(name):
    with open(os.path.join(FOLDER, name), encoding="utf-8") as f:
        return json.load(f)


def _merge(into, tokens):
    """tokens merged into into: a group's into the group of that name, a token over the one at its path."""
    for key, v in tokens.items():
        if isinstance(v, dict) and "$value" not in v and isinstance(into.get(key), dict) and "$value" not in into[key]:
            _merge(into[key], v)
        else:
            into[key] = v


def tree(theme=None):
    """The tokens for theme (a context of the resolver's theme modifier, "light" or "dark"; None: its default), the
    resolver's sets and that theme's context merged in its order, as they're written: aliases left as they are."""
    if theme not in _trees:
        doc, merged = _read(RESOLVER), {}
        for step in doc["resolutionOrder"]:
            kind, name = step["$ref"].split("/")[1:]
            part = doc[kind][name]
            if kind == "modifiers":
                part = part["contexts"][theme or part["default"]]
            else:
                part = part["sources"]
            for source in part:
                _merge(merged, _read(source["$ref"]) if "$ref" in source else source)
        _trees[theme] = merged
    return _trees[theme]


def raw(path, theme=None):
    """The token at path (dotted: "gap.inside") as it's written, with the $type its groups give it, and the
    $extensions they have under it."""
    node, kind, ext = tree(theme), None, {}
    for name in path.split("."):
        if "$value" in node or name not in node:
            raise KeyError(f"no token {path}")
        kind, ext = node.get("$type", kind), {**ext, **node.get("$extensions", {})}
        node = node[name]
    if "$value" not in node:
        raise KeyError(f"{path} is a group, not a token")
    return {**node, "$type": node.get("$type", kind), "$extensions": {**ext, **node.get("$extensions", {})}}


def alias(value):
    """The path an alias ("{libadwaita.view_bg_color}") stands for; None if value isn't one."""
    return value[1:-1] if isinstance(value, str) and value.startswith("{") and value.endswith("}") else None


def token(path, theme=None, seen=()):
    """The token at path, its value resolved: an alias's, the token it stands for's (and its type)."""
    if path in seen:
        raise ValueError(f"an alias's loop: {' -> '.join(seen + (path,))}")
    t = raw(path, theme)
    if (to := alias(t["$value"])) is not None:
        target = token(to, theme, seen + (path,))
        return {**t, "$value": target["$value"], "$type": t["$type"] or target["$type"]}
    return t


def value(path, kind, theme=None):
    t = token(path, theme)
    if t["$type"] != kind:
        raise TypeError(f"{path} is a {t['$type']}, not a {kind}")
    return t["$value"]


def px(path):
    """A dimension's pixels (points, the pointer's)."""
    v = value(path, "dimension")
    if v["unit"] != "px":
        raise ValueError(f"{path} is in {v['unit']}, not px")
    return v["value"]


def seconds(path):
    v = value(path, "duration")
    return float(v["value"]) if v["unit"] == "s" else v["value"] / 1000


def ms(path):
    v = value(path, "duration")
    return v["value"] if v["unit"] == "ms" else v["value"] * 1000


def number(path):
    return value(path, "number")


def point(path):
    """A group's x and y (dimensions), as (x, y)."""
    return px(path + ".x"), px(path + ".y")


def names(path):
    """What's in the group at path, as it's written: its tokens' and groups' names."""
    group = tree()
    for name in path.split("."):
        group = group[name]
    return [k for k in group if not k.startswith("$")]


def points(path):
    """A group of points named 1, 2, 3...: [(x, y)] in turn."""
    return [point(f"{path}.{n}") for n in sorted(names(path), key=int)]


def gtk_color(path):
    """A colour token as GTK's CSS has it: one of libadwaita's, by its name (@view_bg_color); an alias, the one it
    stands for's; else as its $extensions make it."""
    t = raw(path)
    if path.split(".")[0] == NAMED:
        return "@" + path.split(".", 1)[1]
    if (to := alias(t["$value"])) is not None:
        return gtk_color(to)
    if GTK in t["$extensions"]:
        return REF.sub(lambda m: gtk_color(m.group(1)), t["$extensions"][GTK])
    raise ValueError(f"{path}: no colour GTK's CSS can name")


def css_value(path):
    """A token as CSS has it: 8px, 260ms, 0.85, 600, @sidebar_bg_color."""
    t = token(path)
    v = t["$value"]
    if t["$type"] == "color":
        return gtk_color(path)
    if t["$type"] in ("dimension", "duration"):
        return f"{v['value']}{v['unit']}"
    if t["$type"] in ("number", "fontWeight"):
        return str(v)
    raise ValueError(f"{path}: a {t['$type']} isn't put in CSS")


def css(template):
    """template (CSS) with each {path} in it the token's CSS value."""
    return REF.sub(lambda m: css_value(m.group(1)), template)
