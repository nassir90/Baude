"""Files' thumbnails from the system's thumbnailer, as the file manager shows them: QuickLook's on macOS, and on Linux the
freedesktop thumbnail cache (~/.cache/thumbnails), filled by the thumbnailers installed for it (*.thumbnailer, as
Nautilus runs them) when it doesn't have one yet. Baude and Record both use them (the Files page, attachments).

thumbnail(path, px, done) calls done(texture or None) on the main loop: made off it, and kept (the latest few) until the
file changes. Text and code get no thumbnail (a page of text that small says nothing): their icons stay."""

import ctypes
import ctypes.util
import hashlib
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from collections import OrderedDict

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib  # noqa: E402

import widgets  # noqa: E402

POOL = widgets.WorkerPool(2)
KEPT = 400  # (textures kept, newest first)
_kept, _lock = OrderedDict(), threading.Lock()
_waiting = {}  # (key: the done callbacks of a thumbnail being made)

# the kinds that have one: a picture of the file says what's in it. Not text, code or archives
DOCUMENT_TYPES = ("application/pdf", "application/postscript", "application/epub+zip", "application/rtf",
                  "application/vnd.", "application/x-iwork", "application/msword", "application/x-krita",
                  "application/x-blender", "font/", "model/")


def mime_of(path):
    return Gio.content_type_get_mime_type(Gio.content_type_guess(path, None)[0]) or ""


def wanted(path):
    """Whether a file's kind has a thumbnail worth showing (pictures, video, audio's cover, documents)."""
    mime = mime_of(path)
    if mime == "video/mp2t":  # (.ts: TypeScript, far likelier)
        return False
    return mime.split("/")[0] in ("image", "video", "audio") or mime.startswith(DOCUMENT_TYPES)


def thumbnail(path, px, done):
    """done(texture or None), on the main loop, for path's thumbnail at most px device pixels on its longest side."""
    try:
        st = os.stat(path)
    except OSError:
        done(None)
        return
    key = (path, st.st_mtime_ns, st.st_size, px)
    with _lock:
        if key in _kept:
            _kept.move_to_end(key)
            tex = _kept[key]
            GLib.idle_add(lambda: done(tex) and False)
            return
        if key in _waiting:  # (asked for already: told too when it's made)
            _waiting[key].append(done)
            return
        _waiting[key] = [done]

    def work():
        try:
            tex = make(path, px, st)
        except Exception:  # (a thumbnailer's failure is no thumbnail: the icon stays)
            tex = None

        def tell():
            with _lock:
                if tex is not None:
                    _kept[key] = tex
                    while len(_kept) > KEPT:
                        _kept.popitem(last=False)
                waiting = _waiting.pop(key, [])
            for fn in waiting:
                fn(tex)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(tell)
    POOL.submit(work)


def make(path, px, st):
    if sys.platform == "darwin":
        return quicklook(path, px)
    return freedesktop(path, px, st)


def fitted(pb, px):
    w, h = pb.get_width(), pb.get_height()
    tw, th = widgets.fit_size(w, h, px)
    return pb if (tw, th) == (w, h) else pb.scale_simple(tw, th, GdkPixbuf.InterpType.HYPER)


def texture(pb):
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt, pb.read_pixel_bytes(), pb.get_rowstride())


# ------------------------------------------------------------------ macOS --

class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("width", ctypes.c_double),
                ("height", ctypes.c_double)]


def _frameworks():
    """QuickLook, CoreFoundation and CoreGraphics through ctypes (QLThumbnailImageCreate: QuickLook's own thumbnail,
    made in this process, cached by the system as Finder's are)."""
    if (libs := getattr(_frameworks, "libs", None)) is not None:
        return libs
    ql = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/QuickLook.framework/QuickLook")
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    vp, sz = ctypes.c_void_p, ctypes.c_size_t
    cf.CFURLCreateFromFileSystemRepresentation.restype = vp
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
    cf.CFRelease.argtypes = [vp]
    ql.QLThumbnailImageCreate.restype = vp
    ql.QLThumbnailImageCreate.argtypes = [vp, vp, _CGSize, vp]
    cg.CGImageGetWidth.restype = cg.CGImageGetHeight.restype = sz
    cg.CGImageGetWidth.argtypes = cg.CGImageGetHeight.argtypes = [vp]
    cg.CGColorSpaceCreateDeviceRGB.restype = vp
    cg.CGBitmapContextCreate.restype = vp
    cg.CGBitmapContextCreate.argtypes = [vp, sz, sz, sz, sz, vp, ctypes.c_uint32]
    cg.CGContextDrawImage.argtypes = [vp, _CGRect, vp]
    cg.CGContextRelease.argtypes = cg.CGColorSpaceRelease.argtypes = cg.CGImageRelease.argtypes = [vp]
    _frameworks.libs = ql, cf, cg
    return _frameworks.libs


def quicklook(path, px):
    ql, cf, cg = _frameworks()
    raw = os.fsencode(path)
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), os.path.isdir(path))
    if not url:
        return None
    try:
        img = ql.QLThumbnailImageCreate(None, url, _CGSize(px, px), None)
    finally:
        cf.CFRelease(url)
    if not img:
        return None
    try:
        w, h = cg.CGImageGetWidth(img), cg.CGImageGetHeight(img)
        if not w or not h:
            return None
        buf = (ctypes.c_ubyte * (w * h * 4))()
        space = cg.CGColorSpaceCreateDeviceRGB()
        ctx = cg.CGBitmapContextCreate(buf, w, h, 8, w * 4, space, 1)  # (kCGImageAlphaPremultipliedLast)
        cg.CGColorSpaceRelease(space)
        if not ctx:
            return None
        cg.CGContextDrawImage(ctx, _CGRect(0, 0, w, h), img)
        cg.CGContextRelease(ctx)
    finally:
        cg.CGImageRelease(img)
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED, GLib.Bytes.new(bytes(buf)), w * 4)


# ------------------------------------------------------------ freedesktop --
# https://specifications.freedesktop.org/thumbnail-spec/: a file's thumbnail is <cache>/thumbnails/<size>/<md5 of its
# URI>.png, a PNG whose Thumb::URI and Thumb::MTime say what it's of (stale once the file's modified since). A file no
# thumbnailer could do is marked under fail/<app>, so it isn't tried again until it changes.

SIZES = (("normal", 128), ("large", 256), ("x-large", 512), ("xx-large", 1024))
FAIL_DIR = "baude-1"


def cache_root():
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "thumbnails")


def file_uri(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


def cached(path, uri, mtime, size_dirs):
    """A valid thumbnail in the cache (one of size_dirs), as a pixbuf, else None; or False if it's marked failed."""
    name = hashlib.md5(uri.encode()).hexdigest() + ".png"
    for size in size_dirs:
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(os.path.join(cache_root(), size, name))
        except GLib.Error:
            continue
        if pb.get_option("tEXt::Thumb::URI") in (None, uri) and pb.get_option("tEXt::Thumb::MTime") == str(mtime):
            return pb
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(os.path.join(cache_root(), "fail", FAIL_DIR, name))
        if pb.get_option("tEXt::Thumb::MTime") == str(mtime):
            return False
    except GLib.Error:
        pass
    return None


def thumbnailers():
    """MIME type → the Exec line of the thumbnailer installed for it (the first found in XDG_DATA_DIRS, user's first)."""
    if (found := getattr(thumbnailers, "found", None)) is not None:
        return found
    found = {}
    dirs = [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]
    for d in dirs:
        folder = os.path.join(d, "thumbnailers")
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".thumbnailer"):
                continue
            kf = GLib.KeyFile()
            try:
                kf.load_from_file(os.path.join(folder, name), GLib.KeyFileFlags.NONE)
                exec_line = kf.get_string("Thumbnailer Entry", "Exec")
                mimes = kf.get_string_list("Thumbnailer Entry", "MimeType")
            except GLib.Error:
                continue
            for mime in mimes:
                found.setdefault(mime, exec_line)
    thumbnailers.found = found
    return found


def run_thumbnailer(exec_line, path, uri, px):
    """The thumbnailer's PNG (as a pixbuf) for path, px on its longest side, or None."""
    with tempfile.TemporaryDirectory(prefix="baude-thumb-") as tmp:
        out = os.path.join(tmp, "out.png")
        subs = {"%i": path, "%u": uri, "%o": out, "%s": str(px), "%%": "%"}
        args = []
        for word in shlex.split(exec_line):
            for code, value in subs.items():
                word = word.replace(code, value)
            args.append(word)
        try:
            subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=False)
            return GdkPixbuf.Pixbuf.new_from_file(out)
        except (OSError, subprocess.SubprocessError, GLib.Error):
            return None


def save(pb, folder, name, uri, mtime):
    """Into the cache as the spec has it: written aside, its permissions the owner's alone, then moved into place."""
    os.makedirs(folder, mode=0o700, exist_ok=True)
    final = os.path.join(folder, name)
    fd, tmp = tempfile.mkstemp(prefix=".baude-", suffix=".png", dir=folder)
    os.close(fd)
    try:
        pb.savev(tmp, "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime", "tEXt::Software"],
                 [uri, str(mtime), "Baude"])
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except (OSError, GLib.Error):
        try:
            os.unlink(tmp)
        except OSError:
            pass


def freedesktop(path, px, st):
    uri, mtime = file_uri(path), int(st.st_mtime)
    fits = [s for s, n in SIZES if n >= px] or [SIZES[-1][0]]
    hit = cached(path, uri, mtime, fits)
    if hit is False:
        return None
    if hit is not None:
        return texture(fitted(hit, px))
    size, n = next(((s, n) for s, n in SIZES if n >= px), SIZES[-1])
    mime = mime_of(path)
    exec_line = thumbnailers().get(mime)
    pb = run_thumbnailer(exec_line, path, uri, n) if exec_line else None
    if pb is None and mime.startswith("image/"):  # (no thumbnailer for it: decoded here, as file managers do too)
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(path, n, n)
        except GLib.Error:
            pb = None
    name = hashlib.md5(uri.encode()).hexdigest() + ".png"
    if pb is None:
        if exec_line or mime.startswith("image/"):  # (tried and failed: not again until it changes)
            mark = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1)
            save(mark, os.path.join(cache_root(), "fail", FAIL_DIR), name, uri, mtime)
        return None
    pb = fitted(pb, n)
    if not path.startswith(cache_root() + os.sep):  # (a thumbnail of a thumbnail isn't kept: the spec's rule)
        save(pb, os.path.join(cache_root(), size), name, uri, mtime)
    return texture(fitted(pb, px))
