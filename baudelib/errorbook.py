"""errorbook — a process's errors (the wire's TelemetryError), each kept once by its fingerprint with how many times it
happened, till they're taken (take) to be kept by its machine's host (baudelib.telemetry): what nothing caught, on any
thread (install: sys.excepthook, which PyGObject calls for an exception in a main-loop callback too, and
threading.excepthook), and what's noted where it happens (note: an error answered on the wire, an agent exiting by
itself, a draft not saved, a page not loaded). Each is told with the code it ran (commit and wire version, by its
telling) and, in an app, what was on screen and the user's last actions (context: uitrack's). No GTK."""
import os
import re
import sys
import threading
import time
import traceback

from baudelib import lineprofile, wire

STACK_MAX = 20
TEXT_MAX = 300
PENDING_MAX = 500  # (fingerprints held for a host that isn't there yet; each counts on)
KINDS = tuple(wire.DEFS["TelemetryError"]["properties"]["kind"]["enum"])
_NUMBERS = re.compile(r"0x[0-9a-fA-F]+|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}|\d+")


def plain(text):
    """text's first line, as an error's message says it."""
    text = str(text or "").strip()
    return text.splitlines()[0][:TEXT_MAX] if text else ""


def shape(text):
    """text as a fingerprint has it: its first line with numbers, addresses and ids each a #."""
    return _NUMBERS.sub("#", plain(text))[:120]


def ours(path):
    real = os.path.realpath(path)
    return real.startswith(lineprofile.CHECKOUT + os.sep) and f"{os.sep}vendor{os.sep}" not in real


def of_exception(exc_type, exc, tb, kind="exception"):
    """An exception as an error (without its count and times): its fingerprint its kind, type and the innermost
    frame of Baude's own code in its stack (the innermost frame, if none is)."""
    frames = traceback.extract_tb(tb) if tb is not None else []
    top = next((f for f in reversed(frames) if ours(f.filename)), frames[-1] if frames else None)
    name = getattr(exc_type, "__qualname__", None) or str(exc_type)
    out = {"kind": kind, "type": name, "message": plain(exc) or name, "file": None, "line": None, "function": None,
           "stack": [f"{lineprofile.shown_path(f.filename)}:{f.lineno} {f.name}" for f in frames[-STACK_MAX:]]}
    if top is not None:
        out.update(file=lineprofile.shown_path(top.filename), line=top.lineno, function=top.name)
    out["fingerprint"] = f"{kind}:{name}@{out['file']}:{out['line']}" if top is not None else f"{kind}:{name}"
    return out


def of_note(kind, type_, message, key=None, **more):
    """An error noted where it happened (no stack): its fingerprint its kind, type and key (else its message's
    shape)."""
    return {"kind": kind, "type": str(type_)[:TEXT_MAX], "message": plain(message) or str(type_),
            "fingerprint": f"{kind}:{type_}:{shape(message) if key is None else key}", "stack": [], **more}


class Book:
    """A process's errors not yet taken, by fingerprint. app: the process that took it (claim's): the one whose
    errors those nothing caught are; wake: told as one comes, to take them soon; context: () -> what was on screen
    and the user's last actions (an app's); in_loop: () -> whether a main loop is running on this thread, so an
    exception sys.excepthook is given there is a callback's."""

    def __init__(self):
        self.lock, self.pending = threading.Lock(), {}
        self.app, self.wake, self.context, self.in_loop = None, None, None, None

    def claim(self, app, wake, context=None, in_loop=None):
        """The process's errors this app's (the first to claim them), told to wake; whether it has them."""
        with self.lock:
            if self.app is None:
                self.app, self.wake, self.context, self.in_loop = app, wake, context, in_loop
            return self.app == app

    def add(self, err, at=None):
        at = time.time() if at is None else at
        try:
            ctx = self.context() if self.context is not None else {}
        except Exception:  # noqa: BLE001 (what was on screen couldn't be told: kept without it)
            ctx = {}
        err = {**err, "app": err.get("app") or self.app or "host"}
        with self.lock:
            got = self.pending.get(err["fingerprint"])
            if got is not None:
                got.update(err, **ctx, count=got["count"] + 1, last=at, first=got["first"])
            elif len(self.pending) < PENDING_MAX:
                self.pending[err["fingerprint"]] = {**err, **ctx, "count": 1, "first": at, "last": at}
        if self.wake is not None:
            try:
                self.wake()
            except Exception:  # noqa: BLE001 (taken at the next telling instead)
                pass

    def take(self):
        """What's happened since it was last taken, each once with its count."""
        with self.lock:
            out, self.pending = list(self.pending.values()), {}
        return out

    def give_back(self, errors):
        """Errors taken but not kept (the host wasn't there): held again, counted with any since."""
        with self.lock:
            for e in errors:
                got = self.pending.get(e["fingerprint"])
                if got is None:
                    if len(self.pending) < PENDING_MAX:
                        self.pending[e["fingerprint"]] = e
                else:
                    got.update(count=got["count"] + e["count"], first=min(got["first"], e["first"]))


BOOK = Book()  # (this process's)


def note(kind, type_, message, key=None, **more):
    """An error noted as it happened (kind one of KINDS)."""
    BOOK.add(of_note(kind, type_, message, key, **more))


def caught(kind="exception", **more):
    """The exception being handled noted (from an except block), as nothing caught it would be."""
    t, e, tb = sys.exc_info()
    if t is not None:
        BOOK.add({**of_exception(t, e, tb, kind), **more})


_INSTALLED = []


def install():
    """What nothing catches in this process, on any thread, kept (and handled as before); once a process."""
    if _INSTALLED:
        return
    _INSTALLED.append(True)
    before, before_thread = sys.excepthook, threading.excepthook

    def hook(t, e, tb):
        if not issubclass(t, KeyboardInterrupt):
            try:
                loop = BOOK.in_loop is not None and threading.current_thread() is threading.main_thread() \
                    and BOOK.in_loop()
                BOOK.add(of_exception(t, e, tb, "callback" if loop else "exception"))
            except Exception:  # noqa: BLE001 (the error said as it would be, kept or not)
                pass
        before(t, e, tb)

    def thread_hook(args):
        if args.exc_type is not None and not issubclass(args.exc_type, SystemExit):
            try:
                BOOK.add(of_exception(args.exc_type, args.exc_value, args.exc_traceback, "thread"))
            except Exception:  # noqa: BLE001
                pass
        before_thread(args)
    sys.excepthook, threading.excepthook = hook, thread_hook
