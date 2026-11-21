#!/usr/bin/env python3
"""Writes Android's icons (app/src/main/java/dev/baude/android/Adwaita.kt) from the ones the desktop draws: GNOME's
Adwaita symbolic icons, copied into vendor/adwaita-icons/, and Baude's own (widgets.BAUDE_ICONS). Each is a Compose
ImageVector, its 16-square drawing in the middle of a 19.2 square (so drawn at the sizes Material's 24-square ones
were, it's as big as they looked). Run from anywhere: python3 android/tools/adwaita_icons.py"""
import ast
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor" / "adwaita-icons"
OUT = ROOT / "android/app/src/main/java/dev/baude/android/Adwaita.kt"
SYSTEM = Path("/opt/homebrew/share/icons/Adwaita/symbolic")  # (where a new one's copied from, if it isn't yet)

ICONS = """go-previous go-next go-up go-down window-close view-refresh tab-new pan-down pan-end list-add list-remove
input-keyboard changes-prevent changes-allow web-browser edit-clear phone network-server document-new system-search
view-more object-select document-edit edit-undo edit-redo media-playback-start media-playback-stop user-trash alarm
system-users utilities-terminal dialog-question folder media-playlist-shuffle insert-link process-stop dialog-error
dialog-warning content-loading x-office-spreadsheet x-office-calendar x-office-document x-office-presentation
image-x-generic audio-x-generic applications-games mail-unread edit-copy text-x-generic folder-open""".split()
BAUDE = ["baude-return", "baude-claude", "baude-meta", "baude-map"]


def baude_icons():
    """widgets.BAUDE_ICONS, read from the source (importing widgets wants GTK)."""
    tree = ast.parse((ROOT / "widgets.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "BAUDE_ICONS" for t in node.targets):
            return ast.literal_eval(node.value)
    sys.exit("widgets.BAUDE_ICONS not found")


def num(s):
    return float(s) if s else 0.0


def rect_d(e):
    x, y, w, h = (num(e.get(k)) for k in ("x", "y", "width", "height"))
    r = min(num(e.get("rx") or e.get("ry")), w / 2, h / 2)
    if not r:
        return f"M{x},{y}h{w}v{h}h{-w}z"
    return (f"M{x + r},{y}H{x + w - r}A{r},{r} 0 0 1 {x + w},{y + r}V{y + h - r}A{r},{r} 0 0 1 {x + w - r},{y + h}"
            f"H{x + r}A{r},{r} 0 0 1 {x},{y + h - r}V{y + r}A{r},{r} 0 0 1 {x + r},{y}z")


def circle_d(e):
    cx, cy, r = num(e.get("cx")), num(e.get("cy")), num(e.get("r"))
    return f"M{cx - r},{cy}a{r},{r} 0 1 0 {2 * r},0a{r},{r} 0 1 0 {-2 * r},0z"


def transform(t):
    """An SVG transform as Compose group arguments (scale, translate and rotate about a point, which is all these use)."""
    g = {}
    for kind, args in re.findall(r"(\w+)\(([^)]*)\)", t or ""):
        a = [float(v) for v in re.split(r"[ ,]+", args.strip())]
        if kind == "scale":
            g["scaleX"], g["scaleY"] = a[0], a[1] if len(a) > 1 else a[0]
        elif kind == "translate":
            g["translationX"], g["translationY"] = a[0], a[1] if len(a) > 1 else 0.0
        elif kind == "rotate":
            g["rotate"] = a[0]
            if len(a) == 3:
                g["pivotX"], g["pivotY"] = a[1], a[2]
        else:
            sys.exit(f"transform {kind} not handled")
    return g


def alpha(e):
    style = dict(p.split(":", 1) for p in (e.get("style") or "").replace(" ", "").split(";") if ":" in p)
    a = 1.0
    for k in ("opacity", "fill-opacity"):
        v = e.get(k) or style.get(k)
        if v:
            a *= float(v)
    return a


def walk(e, out, depth, inherited=1.0):
    """Kotlin lines drawing e's shapes (groups for transforms), each path's alpha as its own and its groups' times."""
    tag = e.tag.split("}")[-1]
    a = inherited * alpha(e)
    if e.get("fill") == "none" or tag in ("defs", "title", "metadata", "clipPath", "style"):
        return
    pad = "        " + "    " * depth
    if tag in ("path", "rect", "circle"):
        d = e.get("d") if tag == "path" else rect_d(e) if tag == "rect" else circle_d(e)
        if not d:
            return
        rule = "EvenOdd" if (e.get("fill-rule") or e.get("clip-rule")) == "evenodd" else "NonZero"
        line = f'{pad}p("{d}", {a:g}f, PathFillType.{rule})'
        t = transform(e.get("transform"))
        if t:
            out.append(f"{pad}addGroup({', '.join(f'{k} = {v:g}f' for k, v in t.items())})")
            out.append("    " + line)
            out.append(f"{pad}clearGroup()")
        else:
            out.append(line)
        return
    if tag not in ("svg", "g"):
        sys.exit(f"<{tag}> not handled")
    t = transform(e.get("transform")) if tag == "g" else {}
    if t:
        out.append(f"{pad}addGroup({', '.join(f'{k} = {v:g}f' for k, v in t.items())})")
    for c in e:
        walk(c, out, depth + 1 if t else depth, a)
    if t:
        out.append(f"{pad}clearGroup()")


def camel(name):
    return "".join(w.capitalize() for w in name.split("-"))


def main():
    VENDOR.mkdir(parents=True, exist_ok=True)
    own = baude_icons()
    blocks = []
    for name in ICONS + BAUDE:
        if name in BAUDE:
            svg = f'<svg xmlns="http://www.w3.org/2000/svg">{own[name + "-symbolic"]}</svg>'
        else:
            f = VENDOR / f"{name}-symbolic.svg"
            if not f.exists():
                src = next(SYSTEM.rglob(f"{name}-symbolic.svg"), None)
                if src is None:
                    sys.exit(f"{name}-symbolic.svg isn't in Adwaita here")
                shutil.copy(src, f)
            svg = f.read_text()
        lines = []
        walk(ET.fromstring(svg), lines, 0)
        if not lines:
            sys.exit(f"{name}: nothing drawn")
        blocks.append(f'    val {camel(name.removeprefix("baude-") if name in BAUDE else name)}: ImageVector by lazy {{\n'
                      f'        icon("{name}") {{\n' + "\n".join("    " + l for l in lines) + "\n        }\n    }\n")
    OUT.write_text(f'''// Written by android/tools/adwaita_icons.py from vendor/adwaita-icons/ (GNOME's Adwaita icon theme,
// CC-BY-SA-3.0 or LGPL-3.0: vendor/adwaita-icons.LICENSE) and widgets.BAUDE_ICONS. Don't edit: run it again.
package dev.baude.android

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.dp

/** The icons the desktop draws (Adwaita's symbolic ones, and Baude's own), for the same things here. */
object Adwaita {{
    private class Draw(val b: ImageVector.Builder) {{
        fun p(d: String, alpha: Float, rule: PathFillType) {{
            b.addPath(PathParser().parsePathString(d).toNodes(), fill = SolidColor(Color.Black), fillAlpha = alpha,
                pathFillType = rule)
        }}
        fun addGroup(rotate: Float = 0f, pivotX: Float = 0f, pivotY: Float = 0f, scaleX: Float = 1f, scaleY: Float = 1f,
                     translationX: Float = 0f, translationY: Float = 0f) {{
            b.addGroup(rotate = rotate, pivotX = pivotX, pivotY = pivotY, scaleX = scaleX, scaleY = scaleY,
                translationX = translationX, translationY = translationY)
        }}
        fun clearGroup() {{ b.clearGroup() }}
    }}

    /** A 16-square drawing in the middle of a 19.2 square: at Material's sizes, as big as its 24-square ones looked. */
    private fun icon(name: String, draw: Draw.() -> Unit): ImageVector {{
        val b = ImageVector.Builder(name, 24.dp, 24.dp, 19.2f, 19.2f)
        b.addGroup(translationX = 1.6f, translationY = 1.6f)
        Draw(b).draw()
        b.clearGroup()
        return b.build()
    }}

{chr(10).join(blocks)}}}
''')
    print(f"{len(blocks)} icons -> {OUT.relative_to(ROOT)}")


main()
