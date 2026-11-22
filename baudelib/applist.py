"""An app in the platform's list of apps, for this user: on macOS ~/Applications/NAME.app, elsewhere a .desktop entry
and the app's logo in the user's hicolor theme. Baude, Record, Emission and Multiplex go in and out of it together
(APPS: install_all, uninstall_all), whichever app's Settings or --install asks. No GTK until a logo's drawn."""
import os
import re
import shlex
import shutil
import hashlib
import struct
import subprocess
import sys

from baudelib import bins, icons

LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework" \
    "/Support/lsregister"


def _registers(folder):
    """Whether a launcher in folder is told to LaunchServices and Spotlight: the user's own list, not a test's or a
    rig's (BAUDE_LAUNCHERS)."""
    return folder is None and not os.environ.get("BAUDE_LAUNCHERS")


def exec_quote(arg):
    """Quote an argument for a desktop-entry Exec= line (not shell quoting)."""
    arg = arg.replace("%", "%%")  # % starts a field code
    if re.fullmatch(r"[\w@%+=:,./-]+", arg):
        return arg
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'


def launchers(app_id, name, small=False, folder=None):
    """Where install puts an app's launcher: its .app (macOS), else its .desktop entry and its logo (small: and the
    logo drawn for 16 px). folder: macOS's Applications, or Linux's data home, where it isn't the user's (a test's
    or a rig's: BAUDE_LAUNCHERS names one for every app that starts)."""
    folder = folder or os.environ.get("BAUDE_LAUNCHERS")
    if sys.platform == "darwin":
        return [os.path.join(folder or os.path.expanduser("~/Applications"), f"{name}.app")]
    share = folder or os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
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


def icns(svg):
    """A logo as macOS's .icns: PNGs of it (icon_png), its ic07 to ic10 entries, 128 to 1024 px."""
    entries = b"".join(kind + struct.pack(">I", 8 + len(png)) + png for kind, png in
                       ((k, icon_png(svg, n)) for k, n in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
                                                           (b"ic10", 1024))))
    return b"icns" + struct.pack(">I", 8 + len(entries)) + entries


def drawn_in(bundle, name):
    """Where an .app keeps what its .icns was drawn from (a hash of the SVG)."""
    return os.path.join(bundle, "Contents", "Resources", f"{name}.drawn")


def keep_drawn(bundle, name, svg):
    with open(drawn_in(bundle, name), "w") as f:
        f.write(hashlib.sha1(svg.encode()).hexdigest())


def installed_by(app_id, bundle):
    """Whether an .app is the one install() made for app_id (its Info.plist's identifier), not another app's of the
    name."""
    import plistlib
    try:
        with open(os.path.join(bundle, "Contents", "Info.plist"), "rb") as f:
            return plistlib.load(f).get("CFBundleIdentifier") == app_id
    except (OSError, ValueError):
        return False


def redraw_logo(app_id, name, svg, folder=None):
    """An installed launcher's logo drawn again as svg (the accent picked since: icons.logo), where it isn't already:
    the .app's .icns (the bundle touched, and LaunchServices told, for the Finder and the Dock) or the hicolor theme's
    SVG (replaced, and the theme's folder touched, as menus look for a change). Whether it was drawn again. On macOS,
    only a bundle install() made, and one not drawn from this svg already (kept beside it) is rendered at all. No
    GTK: it runs on a worker thread."""
    paths = launchers(app_id, name, folder=folder)
    if not os.path.exists(paths[0]):
        return False
    if sys.platform == "darwin":
        if not installed_by(app_id, paths[0]):
            return False
        try:
            with open(drawn_in(paths[0], name)) as f:
                if f.read() == hashlib.sha1(svg.encode()).hexdigest():
                    return False
        except OSError:
            pass
        path, data = os.path.join(paths[0], "Contents", "Resources", f"{name}.icns"), icns(svg)
    else:
        path, data = paths[1], svg.encode()
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                if sys.platform == "darwin":
                    keep_drawn(paths[0], name, svg)
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".new", "wb") as f:
        f.write(data)
    os.replace(path + ".new", path)
    if sys.platform == "darwin":
        keep_drawn(paths[0], name, svg)
        os.utime(paths[0])
        if _registers(folder) and os.path.exists(LSREGISTER):
            subprocess.Popen([LSREGISTER, "-f", paths[0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        os.utime(os.path.dirname(os.path.dirname(os.path.dirname(path))))  # (hicolor)
    return True


def install(app_id, name, svg, command, category, entry, small=None, folder=None):
    """The app in the list. macOS: an .app (an .icns of svg; category its LSApplicationCategoryType) started through
    the login shell, for Homebrew's PATH. Elsewhere: a .desktop entry (entry: its lines besides the ones every app's
    has; command's arguments quoted for it, a field code like %u among them left as it is) and svg in the user's hicolor
    theme (small: the logo drawn for 16 px, there too). The paths written."""
    paths = launchers(app_id, name, small is not None, folder)
    if sys.platform == "darwin":
        import plistlib
        contents = os.path.join(paths[0], "Contents")
        os.makedirs(os.path.join(contents, "MacOS"), exist_ok=True)
        os.makedirs(os.path.join(contents, "Resources"), exist_ok=True)
        with open(os.path.join(contents, "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleName": name, "CFBundleDisplayName": name, "CFBundleIdentifier": app_id,
                           "CFBundlePackageType": "APPL", "CFBundleExecutable": name, "CFBundleIconFile": name,
                           "LSApplicationCategoryType": category, "NSHighResolutionCapable": True}, f)
        with open(os.path.join(contents, "Resources", f"{name}.icns"), "wb") as f:
            f.write(icns(svg))
        keep_drawn(paths[0], name, svg)
        script = os.path.join(contents, "MacOS", name)
        with open(script, "w") as f:
            f.write('#!/bin/sh\ncd "$HOME"\nexec "${SHELL:-/bin/zsh}" -lc \'exec "$0" "$@"\' '
                    + " ".join(shlex.quote(a) for a in command if not re.fullmatch(r"%[a-zA-Z]", a)) + ' "$@"\n')
        os.chmod(script, 0o755)
        if _registers(folder) and os.path.exists(LSREGISTER):  # (listed now, ⌘Space too: in time, a minute or more)
            subprocess.run([LSREGISTER, "-f", paths[0]], capture_output=True, check=False)
        if _registers(folder) and shutil.which("mdimport"):
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


def uninstall(app_id, name, folder=None):
    """The app's launcher taken out of the list: the paths that were there."""
    there = [p for p in launchers(app_id, name, True, folder) if os.path.exists(p)]
    # (told while the bundle's still there to read)
    if sys.platform == "darwin" and there and _registers(folder) and os.path.exists(LSREGISTER):
        subprocess.run([LSREGISTER, "-u", there[0]], capture_output=True, check=False)
    for p in there:
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    return there


# ------------------------------------------------------------ all of them
# The apps one switch puts in the list (Settings → Show in Applications, Baude's and Record's; every app's --install):
# name -> its app id, its logo's icon (schema/icons/apps), whether the logo takes the accent, the command it starts
# (bins, then its arguments), its macOS category, its .desktop lines besides every app's, and its 16 px logo's icon.
APPS = {
    "Baude": ("dev.baude.Baude", "baude", True, ["baude"], "public.app-category.developer-tools",
              ["GenericName=Claude Code client", "Comment=GTK front-end for the claude CLI",
               "Categories=Development;GTK;", "Keywords=claude;ai;agent;chat;", "StartupNotify=true"], None),
    "Record": ("dev.baude.Record", "record", True, ["record"], "public.app-category.productivity",
               ["Comment=Read AI prose with a peer who knows what you know", "Categories=Utility;GTK;"], None),
    "Emission": ("dev.baude.Emission", "emission", False, ["emission", "%u"], "public.app-category.productivity",
                 ["GenericName=Web Browser", "Comment=Baude's browser", "Categories=Network;WebBrowser;GTK;"],
                 "emission-16"),
    "Multiplex": ("dev.baude.Multiplex", "multiplex", False, ["multiplex"], "public.app-category.developer-tools",
                  ["GenericName=Terminal", "Comment=Baude's terminal", "Categories=System;TerminalEmulator;GTK;"],
                  "multiplex-16"),
}


def place():
    """The list of apps they go in, as the platform names it (schema/copy install)."""
    from baudelib import wording
    return wording.say("install.applications") if sys.platform == "darwin" else wording.say("install.application_menu")


def app_logo(name, accent=None):
    """name's logo (APPS) as its launcher draws it: Baude's and Record's in accent (icons.logo), the others' as
    drawn."""
    _, icon, follows, *_ = APPS[name]
    return icons.logo(icon, accent) if follows else icons.svg("apps", icon)


def all_paths(folder=None):
    """Each app's launcher (its .app, or its .desktop entry), in APPS's order."""
    return [launchers(app_id, name, folder=folder)[0] for name, (app_id, *_) in APPS.items()]


def all_installed(folder=None):
    """Whether every one of APPS is in the list: what each app's switch shows."""
    return all(os.path.exists(p) for p in all_paths(folder))


def install_all(accent=None, folder=None):
    """Every one of APPS in the list (install), with ~/.local/bin's commands they start (bins.install): the paths
    written."""
    written = bins.install()
    for name, (app_id, icon, _, command, category, entry, small) in APPS.items():
        written += install(app_id, name, app_logo(name, accent), [bins.path(command[0]), *command[1:]], category,
                           entry, small=small and icons.svg("apps", small), folder=folder)
    return written


def uninstall_all(folder=None):
    """Every one of APPS taken out of the list (the commands in ~/.local/bin stay: other machines reach this one's
    host process through them): the paths that were there."""
    return [p for name, (app_id, *_) in APPS.items() for p in uninstall(app_id, name, folder)]


def redraw_logos(accent=None, folder=None):
    """The installed launchers' logos that take the accent (Baude's, Record's) drawn again in it (redraw_logo). No
    GTK: it runs on a worker thread."""
    for name, (app_id, _, follows, *_) in APPS.items():
        if follows:
            redraw_logo(app_id, name, app_logo(name, accent), folder)
