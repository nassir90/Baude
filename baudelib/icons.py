"""Baude's icons (schema/icons: an SVG file each, a folder for each set), read where they're drawn: Baude, Record and
Emission put the symbolic sets in GTK's icon theme and read the others' SVG; the Android app's build makes Kotlin of the
same files. No GTK."""
import json
import os

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
