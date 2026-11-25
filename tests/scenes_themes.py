"""scenes_themes — tests/screenshots.py's scenes of themes (baudelib.themes, themelook): three themes saved on this
machine's host as Meta's create_theme saves them (theme_save), each drawn over a finished turn, and Settings → Theme
listing them."""
import test_baude as T  # noqa: E402  (sets up the throwaway env: fake claude, temp config dirs)

from scenelib import H, SIDEBAR, opts, scene


def colours(**contexts):
    """A theme's tokens.json: each context's libadwaita colours, by name, as hex."""
    return {"version": "2025.10", "modifiers": {"theme": {"contexts": {
        context: [{"libadwaita": {name: {"$value": {"hex": hex_}} for name, hex_ in named.items()}}]
        for context, named in contexts.items()}}}}


def look(sidebar, view, window, fg, accent, dialog):
    return {"sidebar_bg_color": sidebar, "view_bg_color": view, "window_bg_color": window, "window_fg_color": fg,
            "view_fg_color": fg, "accent_bg_color": accent, "dialog_bg_color": dialog}


THEMES = {
    "Sepia": (colours(light=look("#ede3cf", "#fbf6ea", "#f3ead7", "#3b2f22", "#b5651d", "#fbf6ea"),
                      dark=look("#2b241c", "#1f1a14", "#261f18", "#efe3cc", "#c07a30", "#2b241c")), None),
    "Midnight": (colours(light=look("#dfe6f3", "#f5f8fd", "#e8eef8", "#1b2540", "#2f6fdb", "#f5f8fd"),
                         dark=look("#141b2d", "#0d1220", "#111827", "#dbe4f5", "#2a8fa6", "#141b2d")),
                 ".user-bubble { border-radius: 4px; }\n"),
    "Forest": (colours(light=look("#dde8d9", "#f6faf3", "#e7efe3", "#1f3320", "#3a8a4a", "#f6faf3"),
                       dark=look("#1a2419", "#121a12", "#162015", "#dbe9d6", "#3f8f4f", "#1a2419")), None),
}


def save_themes():
    """The three themes, saved on this machine's host (its theme_save op)."""
    import themelook
    for name, (tokens, css) in THEMES.items():
        r = themelook.ask_host({"op": "theme_save", "name": name, "css": css, "tokens": tokens})
        assert "error" not in r, r


def themed_turn(app, name):
    import themelook
    save_themes()
    themelook.pick(name)
    win = app.window(opts())
    T.type_and_send(win, "list the files")
    T.wait_for(lambda: T.button(win, "Allow"), "permission")
    T.button(win, "Allow").emit("clicked")
    T.wait_for(lambda: not win.busy, "turn end")
    return win


@scene("39i-theme-midnight", "A finished turn under the Midnight theme: its colours (the frame, the inside, the text, "
                             "the accent) and its CSS's square bubble")
def _(app):
    return themed_turn(app, "Midnight")


@scene("39j-theme-forest", "A finished turn under the Forest theme")
def _(app):
    return themed_turn(app, "Forest")


@scene("39k-theme-settings", "Settings → Theme under Sepia, its list open: Default, then the host's themes",
       size=(1000 + SIDEBAR, H + 100))
def _(app):
    import themelook
    from baudelib import wording
    win = themed_turn(app, "Sepia")
    dialog = win.open_settings()
    row = next(r for r in T.find(dialog, T.Adw.ComboRow) if r.get_title() == wording.say("shared_settings.theme"))
    T.wait_for(lambda: row.get_model().get_n_items() == 1 + len(THEMES), "the host's themes", 20)
    assert themelook.shown_name() == "Sepia"
    row.activate()
    T.pump(0.5)
    return win


@scene("39l-theme-unreadable", "A theme whose text can't be seen (its labels transparent), picked: Default drawn "
                               "instead, and the toast saying why")
def _(app):
    import themelook
    r = themelook.ask_host({"op": "theme_save", "name": "Unreadable",
                            "css": "label, text { color: transparent; }\n", "tokens": None})
    assert "error" not in r, r
    themelook.pick("Unreadable")
    assert themelook.drawn() is None
    win = app.window(opts())
    T.type_and_send(win, "list the files")
    T.wait_for(lambda: T.button(win, "Allow"), "permission")
    T.button(win, "Allow").emit("clicked")
    T.wait_for(lambda: not win.busy, "turn end")
    T.pump(0.5)
    return win


def accent_settings(app, accent):
    """Settings under Sepia (whose accent is its own, a brown), with Accent Colour's list open: accent picked (None:
    System)."""
    import widgets
    from baudelib import wording
    widgets.save_settings(accent=accent)
    win = themed_turn(app, "Sepia")
    dialog = win.open_settings()
    row = next(r for r in T.find(dialog, T.Adw.ComboRow)
               if r.get_title() == wording.say("shared_settings.accent_colour"))
    T.pump(0.3)
    row.activate()
    T.pump(0.5)
    return win


@scene("39m-theme-accent-system", "Accent Colour at System under Sepia: the theme's accent drawn, and System's "
                                  "dot in it", size=(1000 + SIDEBAR, H + 100))
def _(app):
    return accent_settings(app, None)


@scene("39n-theme-accent-picked", "Accent Colour at Red under Sepia: Red drawn over the theme's accent",
       size=(1000 + SIDEBAR, H + 100))
def _(app):
    return accent_settings(app, "red")
