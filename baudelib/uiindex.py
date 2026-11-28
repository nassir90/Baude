"""uiindex — schema/ui read as the index of the UI every app shares (uitrack's, and a host's for what its apps tell
it): each entry's classes (`file.py:Class` in its code), each part's copy keys and literal texts, each entry's kind.
No GTK."""
import glob
import json
import os

from baudelib import wire

_INDEX = {}


def index():
    """schema/ui read once: {"classes": {module.Class: [entities]}, "copy": {copy key: [(entity, part)]},
    "text": {literal text: [(entity, part)]}, "kinds": {entity: its kind}}."""
    if _INDEX:
        return _INDEX
    classes, copies, texts, kinds = {}, {}, {}, {}

    def parts(entity, items):
        for p in items or []:
            if not isinstance(p, dict) or not isinstance(p.get("name"), str):
                continue
            for key in p.get("copy") or []:
                copies.setdefault(key, []).append((entity, p["name"]))
            for t in p.get("text") or [] if isinstance(p.get("text"), list) else [p.get("text")]:
                if isinstance(t, str) and t:
                    texts.setdefault(t, []).append((entity, p["name"]))
            parts(entity, p.get("parts"))
    for path in sorted(glob.glob(os.path.join(wire.SCHEMA_DIR, "ui", "*.json"))):
        entity = os.path.basename(path)[:-5]
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            continue
        kinds[entity] = entity.rsplit(".", 1)[-1]
        for ref in entry.get("code") or []:
            file, _, name = ref.partition(":")
            if file.endswith(".py") and name and "." not in name and name[:1].isupper():
                classes.setdefault(f"{file[:-3].replace('/', '.')}.{name}", []).append(entity)
        parts(entity, entry.get("parts"))
    _INDEX.update(classes=classes, copy=copies, text=texts, kinds=kinds)
    return _INDEX


def part_by_copy(entity, key):
    """The part of entity whose copy has key (a schema/copy key), or None; any entity's, if entity has none."""
    candidates = index()["copy"].get(key) or []
    return next((p for e, p in candidates if e == entity), None)
