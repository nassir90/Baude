"""scenes_telemetry — tests/screenshots.py's scenes of telemetry (baudelib.telemetry, telemetryview): Settings →
Telemetry, what this machine's apps were told to have been used for, as its host adds it up."""
import test_baude as T  # noqa: E402  (sets up the throwaway env: fake claude, temp config dirs)

from scenelib import H, SIDEBAR, opts, scene


def tell(msg):
    """A telemetry request to this machine's host, as an app's Recorder sends one: its answer."""
    from baudelib import wire
    ch, _info = wire.reach({"via": "local"}, timeout=10)
    try:
        ch.send({**msg, "req": 1})
        return next(m for m in ch if m.get("req") == 1)
    finally:
        ch.close()


@scene("39g-telemetry", "Settings → Telemetry: the host picked beside New session (this machine's here), the period, "
                        "then each app's time in each place and its uses of each menu item, the most first",
       size=(1200 + SIDEBAR, H + 100))
def _(app):
    win = app.window(opts())
    for msg in [{"op": "telemetry", "app": "baude", "seconds": {"chats.chat": 3000}}] * 3 + [
                {"op": "telemetry", "app": "baude", "seconds": {"chats.chat": 420, "git.title": 1310,
                                                               "meta_settings.title": 640, "window.kubernetes": 95},
                 "uses": {"window.settings": 4, "meta_settings.full_width": 2, "window.kubernetes": 3, "win.restart": 1}},
                {"op": "telemetry", "app": "record", "seconds": {"telemetry.notes": 5120, "chats.chat": 700},
                 "uses": {"window.ongoing_feedback": 1}}]:
        assert tell(msg).get("op") == "telemetry_kept", msg
    dialog = win.open_settings()
    import telemetryview
    page = next(p for p in T.find(dialog, telemetryview.Page))
    dialog.set_visible_page(page)
    T.wait_for(lambda: page.groups, "the host's telemetry", 20)
    T.pump(0.5)
    return win
