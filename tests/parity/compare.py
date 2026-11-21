#!/usr/bin/env python3
"""The parity rig's comparer: the desktop's and the phone's renders of each scene (tests/parity/desktop.py, the
phone's Parity.kt) side by side, and what each shows, item by item.

    python3 tests/parity/compare.py list                 # each scene, and which side drew it
    python3 tests/parity/compare.py diff SCENE           # what both show, what only one does: copy, buttons, images
    python3 tests/parity/compare.py items SCENE          # every item each side shows, by area, in order
    python3 tests/parity/compare.py pairs [SCENE…]       # out/pairs/<theme>/<scene>.png: desktop | phone
    python3 tests/parity/compare.py all                  # pairs for every scene, and out/diffs/<scene>.txt

Reads tests/parity/out/{desktop,phone}/ (or --out DIR). Nothing's checked: it shows, for a person or an agent to judge."""
import argparse
import json
import os
import re
import sys
import unicodedata

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SIDES = ("desktop", "phone")


def scenes():
    with open(os.path.join(HERE, "scenes.json")) as f:
        return {s["name"]: s for s in json.load(f)["scenes"]}


def dump(out, side, name):
    try:
        with open(os.path.join(out, side, f"{name}.json")) as f:
            return json.load(f)
    except OSError:
        return {"missing": "not rendered"}


def norm(text):
    """Copy as it's compared: case, spacing, ellipses and quotes aside."""
    t = unicodedata.normalize("NFKC", text or "").replace("…", "...").replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", t).strip().casefold()


def overrides(entry):
    """schema/ui/<entry>.json's phone overrides and phone-only parts: (where, what the phone does, why)."""
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(HERE)), "schema", "ui", f"{entry}.json")) as f:
            data = json.load(f)
    except OSError:
        return [(entry, "no such entry", "")]
    out = []

    def walk(obj, where):
        if not isinstance(obj, dict):
            return
        if isinstance(obj.get("phone"), dict):
            out.append((where, obj["phone"].get("does", ""), obj["phone"].get("why", "")))
        if obj.get("apps") == ["phone"] and where != entry:
            out.append((where, "phone only", ""))
        for key in ("parts", "states", "actions"):
            for child in obj.get(key) or []:
                walk(child, f"{where} / {child.get('name') or child.get('by') or key}" if isinstance(child, dict) else where)
    walk(data, entry)
    return out


def line(it):
    bits = [f"[{it.get('area', '?')}]", it["kind"]]
    if it.get("text"):
        t = it["text"].replace("\n", "⏎")
        bits.append(json.dumps(t if len(t) <= 160 else t[:157] + "…", ensure_ascii=False))
    if it.get("sub"):
        bits.append(f"({it['sub']})")
    if it.get("icon"):
        bits.append(f"{{{it['icon']}}}")
    if it.get("tip"):
        bits.append(f"<tip: {it['tip'].splitlines()[0][:100]}>")
    if "on" in it:
        bits.append("on" if it["on"] else "off")
    if it.get("dim"):
        bits.append("dim")
    if it.get("mono"):
        bits.append("mono")
    return " ".join(bits)


def copy_of(items):
    """Every piece of copy a side shows (text, labels, subtitles, tooltips), as compared, with where it's from."""
    out = {}
    for it in items:
        for key in ("text", "sub", "tip"):
            v = it.get(key)
            if not v:
                continue
            for part in v.split("\n") if it["kind"] == "text" and len(v) > 200 else [v]:
                n = norm(part)
                if n:
                    out.setdefault(n, (part.strip(), it["kind"] if key == "text" else key))
    return out


def cmd_list(out, _args):
    for name, sc in scenes().items():
        state = []
        for side in SIDES:
            d = dump(out, side, name)
            state.append(f"{side}: {d['missing']}" if "missing" in d else f"{side}: {len(d.get('items', []))} items")
        print(f"{name:26} {' | '.join(state):60} {sc['what']}")


def cmd_items(out, args):
    for side in SIDES:
        d = dump(out, side, args.scene)
        print(f"=== {side} ===")
        if "missing" in d:
            print(f"  (none: {d['missing']})")
            continue
        for it in d["items"]:
            print("  " + line(it))


def cmd_diff(out, args):
    name = args.scene
    sc = scenes().get(name, {})
    print(f"scene: {name}: {sc.get('what', '')}")
    print(f"inventory: {', '.join('schema/ui/' + e + '.json' for e in sc.get('inventory', []))}")
    said = [o for e in sc.get("inventory", []) for o in overrides(e)]
    if said:
        print("phone overrides (the owner's: judge the phone against these, not the desktop):")
        for where, does, why in said:
            print(f"  {where}: {does}" + (f" ({why})" if why else ""))
    for theme in ("light",):
        print(f"images: {pair_path(out, theme, name)} (desktop left, phone right); "
              + ", ".join(os.path.join(out, s, theme, f"{name}.png") for s in SIDES))
    d, p = dump(out, "desktop", name), dump(out, "phone", name)
    for side, x in (("desktop", d), ("phone", p)):
        if "missing" in x:
            print(f"{side}: NOTHING ({x['missing']})")
    di, pi = d.get("items", []), p.get("items", [])
    dc, pc = copy_of(di), copy_of(pi)
    print("\n--- copy only the desktop shows ---")
    for n, (t, k) in dc.items():
        if n not in pc:
            print(f"  {k}: {t[:200]!r}")
    print("\n--- copy only the phone shows ---")
    for n, (t, k) in pc.items():
        if n not in dc:
            print(f"  {k}: {t[:200]!r}")
    print("\n--- copy both show ---")
    for n, (t, k) in dc.items():
        if n in pc:
            print(f"  {t[:120]!r}")
    for kinds, title in ((("button", "switch", "check", "tab", "input"), "controls"), (("image", "graphic"), "images")):
        print(f"\n--- {title} ---")
        for side, items in (("desktop", di), ("phone", pi)):
            print(f"  {side}:")
            for it in items:
                if it["kind"] in kinds:
                    print("    " + line(it))


def pair_path(out, theme, name):
    return os.path.join(out, "pairs", theme, f"{name}.png")


def font(size):
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def make_pair(out, theme, name, what):
    """desktop | phone at the same height (the phone's scaled to the desktop's), each labelled; a side with nothing
    drawn says so."""
    h, pad, label = 760, 16, 34
    dark = theme == "dark"
    bg, fg = ((30, 30, 30), (232, 232, 232)) if dark else ((246, 245, 244), (34, 34, 34))
    shots = []
    for side in SIDES:
        path = os.path.join(out, side, theme, f"{name}.png")
        if os.path.exists(path):
            im = Image.open(path).convert("RGB")
            im = im.resize((round(im.width * h / im.height), h), Image.LANCZOS)
        else:
            im = Image.new("RGB", (round(h * 411 / 891), h), (60, 60, 60) if dark else (220, 220, 220))
            why = dump(out, side, name).get("missing", "not rendered")
            ImageDraw.Draw(im).multiline_text((20, h // 2 - 20), f"No {side} render:\n{why[:60]}", fill=fg, font=font(18))
        shots.append((side, im))
    w = pad * 3 + sum(im.width for _, im in shots)
    sheet = Image.new("RGB", (w, h + label * 2 + pad * 2), bg)
    draw = ImageDraw.Draw(sheet)
    draw.text((pad, 8), f"{name}: {what}", fill=fg, font=font(20))
    x = pad
    for side, im in shots:
        draw.text((x, label + 6), side, fill=fg, font=font(16))
        sheet.paste(im, (x, label * 2 + pad // 2))
        x += im.width + pad
    path = pair_path(out, theme, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.save(path)
    return path


def cmd_pairs(out, args):
    all_ = scenes()
    for name in args.scenes or list(all_):
        for theme in ("light", "dark"):
            print(make_pair(out, theme, name, all_.get(name, {}).get("what", "")))


def cmd_all(out, args):
    args.scenes = []
    cmd_pairs(out, args)
    os.makedirs(os.path.join(out, "diffs"), exist_ok=True)
    for name in scenes():
        args.scene = name
        path = os.path.join(out, "diffs", f"{name}.txt")
        with open(path, "w") as f:
            stdout, sys.stdout = sys.stdout, f
            try:
                cmd_diff(out, args)
                print("\n\n=== every item ===")
                cmd_items(out, args)
            finally:
                sys.stdout = stdout
        print(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for c in ("diff", "items"):
        sub.add_parser(c).add_argument("scene")
    sub.add_parser("pairs").add_argument("scenes", nargs="*")
    sub.add_parser("all")
    args = ap.parse_args()
    {"list": cmd_list, "diff": cmd_diff, "items": cmd_items, "pairs": cmd_pairs, "all": cmd_all}[args.cmd](
        os.path.abspath(args.out), args)


if __name__ == "__main__":
    main()
