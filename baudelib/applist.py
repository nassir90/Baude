"""An app in the platform's list of apps, for this user (Record's and Emission's): on macOS ~/Applications/NAME.app,
elsewhere a .desktop entry and the app's logo in the user's hicolor theme. No GTK until a logo's drawn."""
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys

LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework" \
    "/Support/lsregister"


def exec_quote(arg):
    """Quote an argument for a desktop-entry Exec= line (not shell quoting)."""
    arg = arg.replace("%", "%%")  # % starts a field code
    if re.fullmatch(r"[\w@%+=:,./-]+", arg):
        return arg
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'


def launchers(app_id, name, small=False):
    """Where install puts an app's launcher: its .app (macOS), else its .desktop entry and its logo (small: and the
    logo drawn for 16 px)."""
    if sys.platform == "darwin":
        return [os.path.expanduser(f"~/Applications/{name}.app")]
    share = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    icons = os.path.join(share, "icons", "hicolor")
    return [os.path.join(share, "applications", f"{app_id}.desktop"),
            os.path.join(icons, "scalable", "apps", f"{app_id}.svg")] + (
        [os.path.join(icons, "16x16", "apps", f"{app_id}.svg")] if small else [])


def icon_png(svg, size=1024, mac=True):
    """A logo (an SVG on a 128 square, its tile 112 of it) as a PNG; mac: with the margin macOS's own icons have (the
    tile 824 of 1024)."""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    if mac:
        k = 112 * 1024 / 824
        svg = svg.replace('viewBox="0 0 128 128"', f'viewBox="{64 - k / 2} {64 - k / 2} {k} {k}"')
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size, size)
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf().save_to_bufferv("png", [], [])[1]


def install(app_id, name, svg, command, category, entry, small=None):
    """The app in the list. macOS: an .app (an .icns of svg; category its LSApplicationCategoryType) started through
    the login shell, for Homebrew's PATH. Elsewhere: a .desktop entry (entry: its lines besides the ones every app's
    has; command's arguments quoted for it, a field code like %u among them left as it is) and svg in the user's hicolor
    theme (small: the logo drawn for 16 px, there too). The paths written."""
    paths = launchers(app_id, name, small is not None)
    if sys.platform == "darwin":
        import plistlib
        contents = os.path.join(paths[0], "Contents")
        os.makedirs(os.path.join(contents, "MacOS"), exist_ok=True)
        os.makedirs(os.path.join(contents, "Resources"), exist_ok=True)
        with open(os.path.join(contents, "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleName": name, "CFBundleDisplayName": name, "CFBundleIdentifier": app_id,
                           "CFBundlePackageType": "APPL", "CFBundleExecutable": name, "CFBundleIconFile": name,
                           "LSApplicationCategoryType": category, "NSHighResolutionCapable": True}, f)
        entries = b"".join(kind + struct.pack(">I", 8 + len(png)) + png for kind, png in
                           ((k, icon_png(svg, n)) for k, n in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
                                                               (b"ic10", 1024))))
        with open(os.path.join(contents, "Resources", f"{name}.icns"), "wb") as f:
            f.write(b"icns" + struct.pack(">I", 8 + len(entries)) + entries)
        script = os.path.join(contents, "MacOS", name)
        with open(script, "w") as f:
            f.write('#!/bin/sh\ncd "$HOME"\nexec "${SHELL:-/bin/zsh}" -lc \'exec "$0" "$@"\' '
                    + " ".join(shlex.quote(a) for a in command if not re.fullmatch(r"%[a-zA-Z]", a)) + ' "$@"\n')
        os.chmod(script, 0o755)
        if os.path.exists(LSREGISTER):  # (listed now, ⌘Space too: they find it in time, a minute or more)
            subprocess.run([LSREGISTER, "-f", paths[0]], capture_output=True, check=False)
        if shutil.which("mdimport"):
            subprocess.run(["mdimport", paths[0]], capture_output=True, check=False)
        return paths
    lines = ["[Desktop Entry]", "Type=Application", f"Name={name}", *entry,
             "Exec=" + " ".join(a if re.fullmatch(r"%[a-zA-Z]", a) else exec_quote(a) for a in command),
             f"Icon={app_id}", "Terminal=false", f"StartupWMClass={app_id}", ""]
    for path, data in zip(paths, ("\n".join(lines), svg, small)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(data)
    return paths


def uninstall(app_id, name):
    """The app's launcher taken out of the list: the paths that were there."""
    there = [p for p in launchers(app_id, name, True) if os.path.exists(p)]
    if sys.platform == "darwin" and there and os.path.exists(LSREGISTER):  # (while the bundle's still there to read)
        subprocess.run([LSREGISTER, "-u", there[0]], capture_output=True, check=False)
    for p in there:
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    return there
