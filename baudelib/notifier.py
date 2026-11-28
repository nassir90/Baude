"""Baude's and Record's notifier apps on macOS: a small app each (tools/notifier/main.swift), built here with swiftc and
signed by tools/macos-sign.sh, that posts the app's notifications through Notification Center under the app's own name
and logo, with an Open button and a Bot's picture attached. osascript's are credited to Script Editor, take no picture
and open Script Editor when clicked; they're what's posted when there's no notifier, or Notification Center turns it
away. Each lives in ~/.local/state/baude/notifier (not a temporary folder: Notification Center ignores an app there)
and is built only once the signing certificate exists (its signature then stays the same from one build to the next,
so macOS keeps the permission you gave it), as the app starts or is installed (build) and again when its source, logo
or certificate change. No GTK."""
import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SOURCE = os.path.join(ROOT, "tools", "notifier", "main.swift")
SIGN = os.path.join(ROOT, "tools", "macos-sign.sh")
APPS = {"Baude": "dev.baude.Baude.Notifier", "Record": "dev.baude.Record.Notifier"}  # (name -> its bundle id)
EXECUTABLE = "notifier"
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework" \
    "/Support/lsregister"


def folder():
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "baude", "notifier")


def bundle(name):
    return os.path.join(folder(), f"{name} Notifier.app")


def executable(name):
    return os.path.join(bundle(name), "Contents", "MacOS", EXECUTABLE)


def launcher_id(name):
    """The bundle id of name's launcher (applist.APPS): what a click opens once this process is gone."""
    from baudelib import applist
    return applist.APPS[name][0]


def _stamp_path(app):
    return os.path.join(app, "Contents", "Resources", "built")


def _source_path(app):
    return os.path.join(app, "Contents", "Resources", "source")


def _failed_path(name):
    return os.path.join(folder(), f".{name}.failed")


def identity():
    """The SHA-1 of the identity the notifiers are signed with (the certificate tools/macos-sign.sh made, or the one
    BAUDE_SIGN_IDENTITY named, as it keeps it), or None: none made yet, or BAUDE_SIGN_IDENTITY's isn't in a keychain."""
    try:
        out = subprocess.run(["/bin/sh", SIGN, "--identity"], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _source():
    """What a notifier's executable is compiled from: its source, for this Mac's architecture."""
    with open(SOURCE, "rb") as f:
        return hashlib.sha1(f.read() + platform.machine().encode()).hexdigest()


def _stamp(name, svg, signer):
    """What a notifier's built from: its source, its name and logo, the certificate it's signed with."""
    return hashlib.sha1(b"\0".join([_source().encode(), name.encode(), APPS[name].encode(), svg.encode(),
                                    signer.encode()])).hexdigest()


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _swiftc():
    """What runs swiftc (through xcrun, which finds the SDK), or None: no Command Line Tools (asked without running
    /usr/bin/swiftc, which would offer to install them in a dialog)."""
    try:
        if subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, stdin=subprocess.DEVNULL,
                          timeout=10).returncode != 0:
            return None
        out = subprocess.run(["/usr/bin/xcrun", "--find", "swiftc"], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    ok = out.returncode == 0 and os.access(out.stdout.strip(), os.X_OK)
    return ["/usr/bin/xcrun", "swiftc"] if ok else None


def built(name):
    """Whether name's notifier is there to post through."""
    return os.path.exists(_stamp_path(bundle(name))) and os.access(executable(name), os.X_OK)


def build(name, svg, say=lambda text: print(text, file=sys.stderr)):
    """name's notifier (APPS) built and signed, with svg its logo, where it isn't already from these: its path, or None
    (nothing to do, not macOS, no certificate yet, no swiftc, or a build from these failed before: what's new said with
    say). The executable's compiled again only when the source has changed (a new logo or certificate reuses it)."""
    if sys.platform != "darwin":
        return None
    signer = identity()
    if signer is None:
        return None
    stamp = _stamp(name, svg, signer)
    if stamp in (_read(_stamp_path(bundle(name))), _read(_failed_path(name))):  # (built, or failed: until these change)
        return None
    old = os.path.join(bundle(name), "Contents", "MacOS", EXECUTABLE)
    reuse = _read(_source_path(bundle(name))) == _source() and os.access(old, os.X_OK)
    swiftc = None if reuse else _swiftc()
    if not reuse and swiftc is None:
        say(f"no swiftc (xcode-select --install): {name}'s notifications stay osascript's")
        return None
    from baudelib import applist
    os.makedirs(folder(), exist_ok=True)
    work = tempfile.mkdtemp(prefix=".build-", dir=folder())  # (signed where it's kept: not a temporary folder)
    try:
        app = os.path.join(work, os.path.basename(bundle(name)))
        contents = os.path.join(app, "Contents")
        os.makedirs(os.path.join(contents, "MacOS"))
        os.makedirs(os.path.join(contents, "Resources"))
        arch = "arm64" if platform.machine() == "arm64" else "x86_64"
        if reuse:
            shutil.copy2(old, os.path.join(contents, "MacOS", EXECUTABLE))
        else:
            out = subprocess.run([*swiftc, "-O", "-target", f"{arch}-apple-macos13", "-o",
                                  os.path.join(contents, "MacOS", EXECUTABLE), SOURCE],
                                 capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if out.returncode != 0:
                return _failed(name, stamp, "build", out, say)
        with open(os.path.join(contents, "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleName": name, "CFBundleDisplayName": name, "CFBundleIdentifier": APPS[name],
                           "CFBundleExecutable": EXECUTABLE, "CFBundleIconFile": EXECUTABLE,
                           "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1", "CFBundleVersion": "1",
                           "CFBundleInfoDictionaryVersion": "6.0", "LSMinimumSystemVersion": "13.0",
                           "LSUIElement": True, "NSHighResolutionCapable": True}, f)
        with open(os.path.join(contents, "Resources", f"{EXECUTABLE}.icns"), "wb") as f:
            f.write(applist.icns(svg))
        with open(_stamp_path(app), "w") as f:  # (before signing: the signature seals it)
            f.write(stamp)
        with open(_source_path(app), "w") as f:
            f.write(_source())
        out = subprocess.run(["/bin/sh", SIGN, app], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if out.returncode != 0:
            return _failed(name, stamp, "sign", out, say)
        try:
            if os.path.exists(bundle(name)):
                os.rename(bundle(name), os.path.join(work, "old.app"))
            os.rename(app, bundle(name))
        except OSError as e:  # (built at once by another start)
            say(f"couldn't put {name}'s notifier in place: {e}")
            return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    try:
        os.remove(_failed_path(name))
    except OSError:
        pass
    if not os.environ.get("BAUDE_LAUNCHERS") and os.path.exists(LSREGISTER):  # (a test's or a rig's: not told)
        subprocess.run([LSREGISTER, "-f", bundle(name)], capture_output=True, check=False)
    say(f"built {bundle(name)}")
    return bundle(name)


def _failed(name, stamp, what, out, say):
    """A build that failed, said and remembered (_failed_path), so it's tried again only once what it's built from
    changes: None."""
    say(f"couldn't {what} {name}'s notifier: {(out.stderr or out.stdout).strip()[-400:]}")
    try:
        with open(_failed_path(name), "w") as f:
            f.write(stamp)
    except OSError:
        pass
    return None


def build_all(accent=None, say=lambda text: print(text, file=sys.stderr)):
    """Every notifier (APPS) built where it isn't already (build), each with its app's logo in accent: the paths built.
    No GTK: it runs on a worker thread."""
    from baudelib import applist
    return [p for name in APPS if (p := build(name, applist.app_logo(name, accent), say)) is not None]


RESTART_S = 10  # (a notifier that quits again this soon after it's started isn't started again for the posts it lost)


class Client:
    """name's notifier, run (--serve) for this process: post and withdraw sent to it, one JSON object a line, and what it
    says back (ready or refused once, a post that failed, a click) handed to the main loop with main(fn, *args)
    (GLib.idle_add). fallback(key, title, body) posts one the notifier couldn't (osascript's); clicked(key, mine, args)
    a click on one of this app's notifications: mine, whether this process posted it (since it started: not before an
    execv restart), args what starts the app on its chat (or None). Started again when the notifier's rebuilt under it,
    and when it quits under it, with what it hadn't posted yet sent again."""

    def __init__(self, name, main, fallback, clicked):
        self.name, self.main, self.fallback, self.clicked = name, main, fallback, clicked
        self.proc, self.started, self.refused, self.launched = None, None, False, None
        self.sent, self.posted = {}, set()  # (key → its post, till withdrawn; the keys Notification Center took)

    def usable(self):
        return not self.refused and built(self.name)

    def _running(self):
        try:
            stamp = os.stat(_stamp_path(bundle(self.name))).st_mtime_ns
        except OSError:
            return False
        if self.proc is not None and self.proc.poll() is None and self.started == stamp:
            return True
        self.stop()
        try:
            self.proc = subprocess.Popen([executable(self.name), "--serve"], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError:
            return False
        self.started, self.launched = stamp, time.monotonic()
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        return True

    def _read(self, proc):
        for line in proc.stdout:
            try:
                said = json.loads(line)
            except ValueError:
                continue
            self.main(self._heard, proc, said)
        self.main(self._heard, proc, {"event": "gone"})

    def _heard(self, proc, said):
        if proc is not self.proc:
            return False
        event, key = said.get("event"), said.get("key")
        if event == "refused":
            self.refused = True  # (notifications off for it, in System Settings → Notifications: osascript's till on)
            print(f"baude: {self.name}'s notifier can't post: {said.get('error')}", file=sys.stderr)
        elif event == "ready":
            self.refused = False  # (turned on again: it says so as the app comes to the front)
        elif event == "posted":
            self.posted.add(key)
        elif event == "failed" and key in self.sent:
            msg = self.sent.pop(key)
            self.fallback(key, msg["title"], msg["body"])
        elif event == "clicked" and key is not None:
            self.clicked(key, said.get("pid") == os.getpid(), said.get("args"))
        elif event == "gone":
            self.proc = None
            self._again()
        return False

    def _again(self):
        """The notifier quit under this process: started again, so clicks on what it posted still reach it, with the
        posts it hadn't taken yet sent again (osascript's, where it can't be, or it quit again within RESTART_S)."""
        if not self.sent:
            return
        lost = [msg for key, msg in self.sent.items() if key not in self.posted]
        again = self.launched is None or time.monotonic() - self.launched > RESTART_S
        if again and self.usable() and self._running() and all(self._send(msg) for msg in lost):
            return
        for msg in lost:
            self.sent.pop(msg["key"], None)
            self.fallback(msg["key"], msg["title"], msg["body"])

    def _send(self, msg):
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def post(self, key, title, body, label, picture=None, app=None, args=None):
        """Whether it's sent to the notifier (else it's the caller's to post another way)."""
        if not self.usable() or not self._running():
            return False
        msg = {"op": "post", "key": key, "title": title, "body": body, "open_label": label}
        if picture:
            msg["picture"] = picture
        if app:
            msg["app"] = app
            msg["args"] = list(args or [])
        if not self._send(msg):
            return False
        self.sent[key] = msg
        self.posted.discard(key)
        return True

    def withdraw(self, key):
        self.sent.pop(key, None)
        self.posted.discard(key)
        if self.proc is not None and self.proc.poll() is None:
            self._send({"op": "withdraw", "key": key})

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()  # (it quits as its stdin closes)
            except OSError:
                pass
            self.proc = None
