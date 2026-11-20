#!/usr/bin/env python3
"""Build Baude's page for mpts.ie/apps/baude into public/apps/baude/.

Takes the screenshot matrix (../screenshots/, made by tests/screenshots.py or tests/screenshots.sh),
converts the scenes listed below to WebP in both themes (full size, and a smaller one for the grid),
makes the square card for mpts.ie/apps, writes index.html from page.html with the grid filled in, and puts the Android
app beside it (baude-android.apk, from android/: npm run build:apk builds it), with what the app looks at to update
itself (baude-android.json: the APK's versionCode and versionName, its size and sha256).
Needs Pillow.
"""
import hashlib
import html
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request

try:
    from PIL import Image
except ImportError:
    sys.exit("build_shots.py needs Pillow (pip install pillow, or brew install pillow)")

HERE = os.path.dirname(os.path.abspath(__file__))
MATRIX = os.path.join(HERE, "..", "screenshots")
APK = os.path.join(HERE, "..", "android", "app", "build", "outputs", "apk", "debug", "app-debug.apk")
OUT = os.path.join(HERE, "public", "apps", "baude")
FEED = "https://mpts.ie/apps/baude/baude-android.json"  # (what the app looks at, as it's live: publish_apk's)
THEMES = ("light", "dark")
GRID_WIDTH = 760  # the grid's image width: two columns on a 1040px page, sharp on 2x screens

# (scene in the matrix, caption), in the order they appear on the page
SHOTS = [
    ("02-turn-complete", "A finished turn: thinking, the reply in markdown, a tool call, and what it cost"),
    ("29-tool-group-open", "Tool calls in a row fold into one line; open it for each call, a line each, its Command and Output a click away"),
    ("10-permission-bash", "Manual mode asks before each call, shown as what it means: a command and its description"),
    ("11-permission-edit", "An edit to approve, as a red and green diff"),
    ("14-permission-mcp", "MCP tools under friendly names, their fields as rows"),
    ("20-tool-views", "Readable tool calls: Read, Grep, Glob, a to-do list, a subagent"),
    ("21-output-text", "Commands and their output, in separate sections"),
    ("21b-output-long", "Long output scrolls in its own pane instead of pages of chat"),
    ("22-output-error", "A failing command, marked as such"),
    ("25-image-read", "When Claude reads an image, the card is just the image"),
    ("26-image-viewer", "Click any image for a full-window view; ← and → step through the chat's images"),
    ("30-markdown-gallery", "Markdown: headings, lists, quotes, code blocks, tables"),
    ("35-table-wide", "A table wider than the chat scrolls sideways instead of squashing its columns"),
    ("36-auto-links", "Links, artifacts by their titles, and file paths that open in your editor at the line"),
    ("32-user-bubble", "Your messages, with markdown and attachments"),
    ("40-composer-markdown", "The input box previews markdown as you type"),
    ("42-composer-attachments", "Attach images, PDFs and files with +, paste or drag and drop"),
]
CARD = ("02-turn-complete", "dark", (0, 0, 760, 760))  # the square still on mpts.ie/apps


def webp(src, dest, width=None):
    im = Image.open(src).convert("RGB")
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(dest, "WEBP", quality=86, method=6)
    return im.size


def publish_apk(built_apk, out, live=FEED):
    """built_apk copied into out as baude-android.apk, and baude-android.json beside it: the build's versionCode and
    versionName (from the output-metadata.json Gradle writes beside built_apk), the APK's name, size and sha256. The
    app (android/.../Update.kt) downloads a higher versionCode and installs it only if it's the size and sha256 said
    here. Refused (the build stops, nothing copied) for a build with changes that aren't committed or a versionCode
    Gradle was given, or one whose versionCode is below the one live (live: the feed now published, None not to
    look); the same versionCode (nothing in the app changed) is published again, and phones leave it be."""
    with open(os.path.join(os.path.dirname(built_apk), "output-metadata.json")) as f:
        built = json.load(f)["elements"][0]
    code, name = built["versionCode"], built["versionName"]
    if not re.fullmatch(r"[0-9a-f]{4,40}", name):
        sys.exit(f"not publishing the Android app built as {name}: build it from a clean checkout "
                 "(android/, emission.py and schema/ committed)")
    if live:
        try:
            with urllib.request.urlopen(urllib.request.Request(live, headers={"Cache-Control": "no-cache", "User-Agent": "baude-site-build"}),
                                        timeout=30) as r:
                was = json.load(r)["versionCode"]
        except urllib.error.HTTPError as e:
            if e.code != 404:  # (404: none published yet)
                sys.exit(f"not publishing the Android app: couldn't read {live}: {e}")
            was = 0
        except (OSError, ValueError, KeyError) as e:
            sys.exit(f"not publishing the Android app: couldn't read {live}: {e}")
        if code < was:
            sys.exit(f"not publishing the Android app: its versionCode {code} is below the live one's, {was} "
                     "(an older checkout, or a branch behind main?)")
        if code == was:
            print(f"the Android app is versionCode {code}, as the live one is: phones won't update")
    apk = os.path.join(out, "baude-android.apk")
    shutil.copyfile(built_apk, apk)
    sha = hashlib.sha256()
    with open(apk, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    feed = {"versionCode": code, "versionName": name,
            "apk": os.path.basename(apk), "size": os.path.getsize(apk), "sha256": sha.hexdigest()}
    with open(os.path.join(out, "baude-android.json"), "w") as f:
        json.dump(feed, f, indent=1)
        f.write("\n")


def main():
    missing = [f"{name}-{t}.png" for name, _ in SHOTS for t in THEMES
               if not os.path.exists(os.path.join(MATRIX, f"{name}-{t}.png"))]
    if missing:
        sys.exit(f"missing from {os.path.normpath(MATRIX)}: {', '.join(missing)}\n"
                 "run tests/screenshots.py (macOS) or tests/screenshots.sh (Linux) first")
    shots = os.path.join(OUT, "shots")
    os.makedirs(shots, exist_ok=True)
    items = []
    for name, caption in SHOTS:
        for t in THEMES:
            src = os.path.join(MATRIX, f"{name}-{t}.png")
            full = webp(src, os.path.join(shots, f"{name}-{t}.webp"))
            small = webp(src, os.path.join(shots, f"{name}-{t}-sm.webp"), GRID_WIDTH)
        cap = html.escape(caption)
        items.append(
            f'    <figure class="shot"><a href="shots/{name}-light.webp" data-name="{name}">'
            f'<img src="shots/{name}-light-sm.webp" width="{small[0]}" height="{small[1]}" alt="{cap}" '
            f'loading="lazy" decoding="async"></a><figcaption>{cap}</figcaption></figure>')
    name, theme, box = CARD
    card = Image.open(os.path.join(MATRIX, f"{name}-{theme}.png")).convert("RGB").crop(box).resize((720, 720), Image.LANCZOS)
    card.save(os.path.join(OUT, "card.jpg"), "JPEG", quality=88, optimize=True, progressive=True)
    with open(os.path.join(HERE, "page.html")) as f:
        page = f.read().replace("<!-- SHOTS -->", "\n".join(items))
    with open(os.path.join(OUT, "index.html"), "w") as f:
        f.write(page)
    if not os.path.exists(APK):
        sys.exit(f"missing {os.path.normpath(APK)}: run npm run build:apk first")
    publish_apk(APK, OUT)
    total = sum(os.path.getsize(os.path.join(shots, n)) for n in os.listdir(shots))
    print(f"{len(SHOTS)} screenshots in two themes ({total / 1e6:.1f} MB) -> {os.path.relpath(OUT, HERE)}")


if __name__ == "__main__":
    main()
