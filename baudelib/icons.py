"""Baude's icons (schema/icons: an SVG file each, a folder for each set), read where they're drawn: Baude, Record and
Emission put the symbolic sets in GTK's icon theme and read the others' SVG; the Android app's build makes Kotlin of the
same files. No GTK."""
import json
import os
import re

from baudelib import tokens

FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "schema", "icons")


def folder(name):
    """The set name's folder (adwaita, baude, kube, models: symbolic icons, by their icon names)."""
    return os.path.join(FOLDER, name)


def names(name):
    """The icon names in the set name, sorted."""
    return sorted(f[:-4] for f in os.listdir(folder(name)) if f.endswith(".svg"))


def svg(name, icon):
    """The SVG of icon (its file's name, without .svg) in the set name."""
    with open(os.path.join(folder(name), icon + ".svg"), encoding="utf-8") as f:
        return f.read()


def model_families():
    """[(icon, name, match)]: each model family as schema/icons/models/families.json lists them, in its order: the
    icon (baude-model-<icon>-symbolic), the name its models are headed by, the pattern that finds it in a model's
    name or value."""
    with open(os.path.join(folder("models"), "families.json"), encoding="utf-8") as f:
        return [(x["icon"], x["name"], x["match"]) for x in json.load(f)["families"]]


def model_programs():
    """{program: (icon, copy)}: schema/icons/models/families.json's programs' families, by the program
    (models.model_program) whose every model is of it: its icon (baude-model-<icon>-symbolic), the copy (schema/copy)
    its models are headed by."""
    with open(os.path.join(folder("models"), "families.json"), encoding="utf-8") as f:
        return {x["program"]: (x["icon"], x["copy"]) for x in json.load(f).get("programs", [])}


def model_names():
    """[(match, as)]: how models' ids read as names (schema/icons/models/families.json's families' names, in its order):
    the pattern that's the whole id, the name it reads as ({n}: its group n, - and _ as spaces, a capital first)."""
    with open(os.path.join(folder("models"), "families.json"), encoding="utf-8") as f:
        return [(n["match"], n["as"]) for x in json.load(f)["families"] for n in x.get("names", [])]


def logo(app, accent=None):
    """app's logo (apps/<app>.svg) in accent, an accent's name (schema/tokens' accent): the tile's colour (its first
    rect's fill) the accent's, wherever the logo draws in it. None, or a name that isn't an accent's: as it's drawn."""
    drawn = svg("apps", app)
    if accent not in tokens.names("accent"):
        return drawn
    tile = re.search(r'<rect\b[^>]*\bfill="(#[0-9a-fA-F]{6})"', drawn).group(1)
    return drawn.replace(tile, tokens.color_hex(f"accent.{accent}"))
