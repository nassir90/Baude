"""Baude's icons (schema/icons: an SVG file each, a folder for each set), read where they're drawn: Baude, Record and
Emission put the symbolic sets in GTK's icon theme and read the others' SVG; the Android app's build makes Kotlin of the
same files. No GTK."""
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


def logo(app, accent=None):
    """app's logo (apps/<app>.svg) in accent, an accent's name (schema/tokens' accent): the tile's colour (its first
    rect's fill) the accent's, wherever the logo draws in it. None, or a name that isn't an accent's: as it's drawn."""
    drawn = svg("apps", app)
    if accent not in tokens.names("accent"):
        return drawn
    tile = re.search(r'<rect\b[^>]*\bfill="(#[0-9a-fA-F]{6})"', drawn).group(1)
    return drawn.replace(tile, tokens.color_hex(f"accent.{accent}"))
