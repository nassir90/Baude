"""The fonts Baude and Record draw with, set up before GTK is (no GTK here): text through FontConfig and FreeType, the
code font Settings → Code Font says (the "monospace" every code view asks for), found among the system's fonts, those
beside this file (fonts/: open fonts bundled with it, their licences with them) and, on macOS, SF Mono where Terminal
keeps it. Its size beside the prose is the widgets' (widgets.code_scale)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
BUNDLED = os.path.join(HERE, "fonts")
TERMINAL_FONTS = "/System/Applications/Utilities/Terminal.app/Contents/Resources/Fonts"  # (SF Mono: macOS's own)
DEFAULT_CODE_FONT = "Menlo" if sys.platform == "darwin" else "monospace"


def settings():
    """Baude's settings, read as widgets.load_settings does, without GTK (this runs before it's loaded)."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    try:
        with open(os.path.join(base, "baude", "settings.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def code_font():
    """Settings → Code Font: the family code is drawn in (DEFAULT_CODE_FONT unless one's been picked)."""
    name = settings().get("code_font")
    return name if isinstance(name, str) and name.strip() else DEFAULT_CODE_FONT


def font_dirs():
    """Where fonts beyond the system's are: those bundled, and (macOS) Terminal's."""
    return [d for d in (BUNDLED, TERMINAL_FONTS if sys.platform == "darwin" else None) if d and os.path.isdir(d)]


def setup():
    """Before Pango is loaded: a FontConfig config of Baude's own (not yours), with the fonts beyond the system's and
    "monospace" made the code font. On macOS, text through FreeType rather than CoreText, too: Pango's CoreText path
    puts glyphs on whole pixels, so letters are spaced unevenly and look thin; FontConfig + FreeType2 places them as on
    Linux (https://discourse.gnome.org/t/font-antialiasing-issue-on-gtk-in-macos/28071), and FontConfig's own
    sans-serif there (Verdana) is made the system's font. PANGOCAIRO_BACKEND or FONTCONFIG_FILE set already: left as
    they are. No FontConfig: nothing (CoreText it is, on macOS)."""
    if "PANGOCAIRO_BACKEND" in os.environ or "FONTCONFIG_FILE" in os.environ:
        return
    candidates = ("/opt/homebrew/etc/fonts/fonts.conf", "/usr/local/etc/fonts/fonts.conf") if sys.platform == "darwin" \
        else ("/etc/fonts/fonts.conf",)
    main = next((p for p in candidates if os.path.exists(p)), None)
    if main is None:
        return
    alias = '  <alias binding="strong"><family>{}</family><prefer><family>{}</family></prefer></alias>\n'
    font = code_font()
    conf = ('<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n<fontconfig>\n'
            f'  <include ignore_missing="yes">{main}</include>\n'
            + "".join(f"  <dir>{d}</dir>\n" for d in font_dirs())
            + (alias.format("sans-serif", "System Font") + alias.format(".AppleSystemUIFont", "System Font")
               if sys.platform == "darwin" else "")
            + (alias.format("monospace", font) if font != "monospace" else "") + '</fontconfig>\n')
    folder = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "baude")
    path = os.path.join(folder, "fonts.conf")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w") as f:
            f.write(conf)
    except OSError:
        return
    if sys.platform == "darwin":
        os.environ["PANGOCAIRO_BACKEND"] = "fc"
    os.environ["FONTCONFIG_FILE"] = path
