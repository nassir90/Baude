"""themepacks — a theme as a package (a prototype): the folder baudelib.themes keeps (themes/<name>/), with its
manifest.json (ThemeManifest.json: its kind, its layers, its assets), its scene.json (Scene.json) and the files they
use, passed round as one file, <name>.baudetheme (the folder zipped). No GTK: the host's, for every app alike.

A theme's layers, each with the ones under it (schema/ui/README.md, "A theme is one for every app"):
- colours: tokens.json, every app's colours (baudelib.themes works them out);
- skin: theme.css and a GTK theme (gtk/<name>/), the desktop apps' look over or in place of libadwaita's;
- scene: scene.json, a space every app draws with Baude's things in it as live widgets, and, for the desktop apps
  alone, code: desktop/*.py and shaders/*.glsl, run only once trusted as they are (trust: their sha256, kept outside
  the theme's folder, so a theme can't trust itself).

Everything but the code is data: checked here (types by schema/types through baudelib.wire, the rest by hand) as a
theme is installed and each time it's read, so no app is given a scene it can't draw."""
import base64
import hashlib
import io
import json
import os
import posixpath
import shutil
import stat
import tempfile
import zipfile

from baudelib import themes, wire

MANIFEST, SCENE = "manifest.json", "scene.json"
PACK_MAX = wire.CONSTANTS["THEME_PACK_MAX"]
FILES_MAX = wire.CONSTANTS["THEME_FILES_MAX"]
NODES_MAX = wire.CONSTANTS["SCENE_NODES_MAX"]
LIGHTS_MAX = 8
PRIMITIVES = ("plane", "box", "cylinder", "sphere")
TOP = (MANIFEST, themes.CSS_FILE, themes.TOKENS_FILE, SCENE)  # (the files a theme may have at its top)
FOLDERS = {  # (each folder a theme may have, and what may be in it)
    "models": (".obj", ".glb"),
    "textures": (".png", ".jpg", ".jpeg", ".webp", ".hdr"),
    "fonts": (".ttf", ".otf"),
    "shaders": (".glsl",),
    "desktop": (".py",),
    "gtk": (".css", ".png", ".svg", ".theme", ".ini", ".txt", ".xml"),
}
CODE = ("desktop", "shaders")  # (what's run, not read: the desktop apps', once trusted)


def folder(name):
    return os.path.join(themes.folder(), themes.check_name(name))


def _trust_path():
    return os.path.join(os.path.dirname(themes.folder()), "theme-trust.json")


# ---- the files in a theme

def check_path(path):
    """path, a file's in a theme ("textures/floor.png"), if a theme may have it: ValueError, saying why, if not."""
    if not isinstance(path, str) or not path or "\\" in path or "\0" in path:
        raise ValueError(f"{path!r} isn't a file's path in a theme.")
    norm = posixpath.normpath(path)
    if norm != path or norm.startswith(("/", "../")) or norm == ".." or "/." in "/" + norm:
        raise ValueError(f"{path}: a theme's files are under its folder, by plain paths (no .., no hidden files).")
    top, _, rest = norm.partition("/")
    if not rest:
        if norm not in TOP:
            raise ValueError(f"{path}: a theme's top holds {', '.join(TOP)} and its folders only.")
        return norm
    if top not in FOLDERS:
        raise ValueError(f"{path}: a theme's folders are {', '.join(FOLDERS)}.")
    if top != "gtk" and "/" in rest:
        raise ValueError(f"{path}: {top}/ holds files, not folders.")
    if not norm.lower().endswith(FOLDERS[top]) and not (top == "gtk" and posixpath.basename(norm) == "index.theme"):
        raise ValueError(f"{path}: {top}/ holds {', '.join(FOLDERS[top])} files.")
    return norm


def listing(name):
    """Each file in a theme's folder, by its path, in order."""
    root, out = folder(name), []
    for here, dirs, fs in os.walk(root):
        dirs.sort()
        for f in sorted(fs):
            out.append(os.path.relpath(os.path.join(here, f), root).replace(os.sep, "/"))
    return out


def read_file(name, path, limit=PACK_MAX):
    """A file of a theme's, as bytes (ThemeFile's)."""
    full = os.path.join(folder(name), check_path(path))
    st = os.lstat(full)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{path}: not a file.")
    if st.st_size > limit:
        raise ValueError(f"{path}: over {limit // 1024} KB.")
    with open(full, "rb") as fh:
        return fh.read()


def _json(name, path):
    try:
        return json.loads(read_file(name, path, themes.SIZE_MAX).decode("utf-8"))
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"{name}'s {path} isn't JSON: {e}") from None


# ---- the manifest

def manifest(name):
    """A theme's manifest, checked: its manifest.json's, or as its files make it where it has none (#446's themes: a
    theme.css and a tokens.json, kind skin or colours)."""
    have = set(listing(name))
    doc = _json(name, MANIFEST)
    if doc is None:
        doc = {"name": name, "kind": "skin" if themes.CSS_FILE in have else "colours",
               "tokens": themes.TOKENS_FILE in have}
        if themes.CSS_FILE in have:
            doc["skin"] = {"css": True}
    check_manifest(doc, have, name)
    return doc


def check_manifest(doc, have, name=None):
    """ValueError, saying what's wrong, for a manifest that isn't as ThemeManifest.json has it, or doesn't fit the files
    the theme has (have: their paths)."""
    out = []
    wire._validate(wire.DEFS["ThemeManifest"], doc, "manifest", out)
    if out:
        raise ValueError("; ".join(out[:5]))
    themes.check_name(doc["name"])
    if name is not None and doc["name"] != name:
        raise ValueError(f"manifest: its name is {doc['name']!r}, its folder's {name!r}.")
    kind, skin, scene = doc["kind"], doc.get("skin"), doc.get("scene")
    if kind == "colours" and (skin or scene) or kind == "skin" and scene:
        raise ValueError(f"manifest: a {kind} theme has no {'scene' if scene else 'skin'}: its kind is the highest layer it has.")
    if kind == "scene" and SCENE not in have:
        raise ValueError("manifest: a scene theme has a scene.json.")
    if kind == "skin" and not skin:
        raise ValueError("manifest: a skin theme has its skin (theme.css, a GTK theme or both).")
    if doc.get("tokens") and themes.TOKENS_FILE not in have:
        raise ValueError("manifest: tokens is true, but there's no tokens.json.")
    if skin and skin.get("css") and themes.CSS_FILE not in have:
        raise ValueError("manifest: skin.css is true, but there's no theme.css.")
    if skin and skin.get("gtk_theme") is not None:
        g = skin["gtk_theme"]
        if not themes.NAME.fullmatch(g):
            raise ValueError("manifest: skin.gtk_theme is a GTK theme's name.")
    for a in doc.get("assets") or ():
        if check_path(a).split("/")[0] in CODE:
            raise ValueError(f"manifest: {a} is code, not an asset: it's the scene's desktop code.")
        if a not in have:
            raise ValueError(f"manifest: there's no {a}.")
    code = (scene or {}).get("desktop") or {}
    if code.get("python") is not None and (not check_path(code["python"]).startswith("desktop/") or code["python"] not in have):
        raise ValueError("manifest: scene.desktop.python is a file in desktop/ the theme has.")
    for shader in (code.get("shaders") or {}).values():
        if not check_path(shader).startswith("shaders/") or shader not in have:
            raise ValueError(f"manifest: shader {shader} is a file in shaders/ the theme has.")
    return doc


# ---- the scene

def _vec(v, n, where):
    if v is not None and len(v) != n:
        raise ValueError(f"{where}: {n} numbers.")


def scene(name):
    """A theme's scene.json, checked (Scene.json, and what the types can't say); None if it has none."""
    doc = _json(name, SCENE)
    if doc is not None:
        check_scene(doc, set(listing(name)))
    return doc


def check_scene(doc, have):
    """ValueError, saying what's wrong, for a scene no app could draw: one not as Scene.json has it, a material, a
    parent or a model that isn't there, a parent's loop, more lights or nodes than a scene may have."""
    out = []
    wire._validate(wire.DEFS["Scene"], doc, "scene", out)
    if out:
        raise ValueError("; ".join(out[:5]))
    cam = doc["camera"]
    _vec(cam["position"], 3, "camera.position")
    _vec(cam["target"], 3, "camera.target")
    if not 5 <= cam.get("fov", 45) <= 150:
        raise ValueError("camera.fov: 5 to 150 degrees.")
    if len(doc.get("lights") or ()) > LIGHTS_MAX:
        raise ValueError(f"scene: at most {LIGHTS_MAX} lights.")
    for i, light in enumerate(doc.get("lights") or ()):
        _vec(light.get("position"), 3, f"lights[{i}].position")
        _vec(light.get("direction"), 3, f"lights[{i}].direction")
    materials = doc.get("materials") or {}
    for m, mat in materials.items():
        for f in ("texture", "normal_texture", "orm_texture"):
            if mat.get(f) is not None and f"textures/{mat[f]}" not in have:
                raise ValueError(f"materials.{m}: there's no textures/{mat[f]}.")
    for f in ("environment", "backdrop"):
        tex = (doc.get(f) or {}).get("texture")
        if tex is not None and f"textures/{tex}" not in have:
            raise ValueError(f"{f}: there's no textures/{tex}.")
    ids, count = {}, 0
    for n in doc["nodes"]:
        if n["id"] in ids:
            raise ValueError(f"nodes: another node is {n['id']} too.")
        ids[n["id"]] = n
    slotted = [(f"{n['id']}/with[{j}] ({w['id']})", w) for n in doc["nodes"]
               for j, w in enumerate((n.get("slots") or {}).get("with") or ())]
    for i, (where, n) in enumerate([(f"nodes[{i}] ({n['id']})", n) for i, n in enumerate(doc["nodes"])] + slotted):
        for f, k in (("position", 3), ("rotation", 3), ("scale", 3)):
            _vec(n.get(f), k, f"{where}.{f}")
        mesh = n.get("mesh")
        if mesh is not None and mesh not in PRIMITIVES and (f"models/{mesh}" not in have or not mesh.endswith(".obj")):
            raise ValueError(f"{where}: its mesh is one of {', '.join(PRIMITIVES)} or a model in models/ (.obj).")
        if n.get("model") is not None and (f"models/{n['model']}" not in have or not n["model"].endswith(".glb")):
            raise ValueError(f"{where}: its model is a .glb in models/.")
        if n.get("model") is not None and mesh is None:
            raise ValueError(f"{where}: a model is drawn in a mesh's place: give it a mesh too.")
        for m in [n.get("material")] + list((n.get("states") or {}).values()):
            if m is not None and m not in materials:
                raise ValueError(f"{where}: there's no material {m}.")
        if sum(x is not None for x in (mesh, n.get("panel"), n.get("slots"))) > 1:
            raise ValueError(f"{where}: a node is a mesh, a panel or slots, one of them.")
        for p in [n.get("panel"), (n.get("slots") or {}).get("panel")]:
            if p is not None:
                _vec(p.get("px"), 2, f"{where}.panel.px")
                _vec(p.get("size"), 2, f"{where}.panel.size")
                if any(not 16 <= x <= 4096 for x in p.get("px") or ()):
                    raise ValueError(f"{where}.panel.px: 16 to 4096 px.")
        if (slots := n.get("slots")) is not None:
            if slots.get("panel") is None and not slots.get("with"):
                raise ValueError(f"{where}: slots have a panel, nodes with them, or both.")
            count += min(slots.get("limit") or NODES_MAX, NODES_MAX) * (1 + len(slots.get("with") or ()))
        elif i < len(doc["nodes"]):
            count += 1
    for n in doc["nodes"]:
        seen, at = set(), n
        while at.get("parent") is not None:
            if at["parent"] not in ids:
                raise ValueError(f"{at['id']}: there's no node {at['parent']} to be in.")
            if at["id"] in seen:
                raise ValueError(f"{n['id']}: its parents loop.")
            seen.add(at["id"])
            at = ids[at["parent"]]
    if count > NODES_MAX:
        raise ValueError(f"scene: at most {NODES_MAX} nodes, its slots' panels counted (give slots a limit).")
    return doc


# ---- its code, and trusting it

def code_hash(name, have=None):
    """The sha256 of a theme's code (desktop/ and shaders/: each file's path and bytes, in order), hex; None if it has
    none."""
    paths = [p for p in (listing(name) if have is None else sorted(have)) if p.split("/")[0] in CODE]
    if not paths:
        return None
    h = hashlib.sha256()
    for p in paths:
        data = read_file(name, p)
        h.update(p.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    return h.hexdigest()


def _trust_read():
    try:
        with open(_trust_path(), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def trusted(name):
    """Whether a theme's code, as it is now, is trusted to run on this machine."""
    code = code_hash(name)
    return code is not None and _trust_read().get(name) == code


def trust(name, code):
    """A theme's code trusted (code: its sha256, as the user was shown it), or not (None). ValueError if code isn't
    what its files come to now."""
    now = code_hash(name)
    if code is not None and code != now:
        raise ValueError(f"{name}'s code isn't what was shown: it's changed since.")
    kept = _trust_read()
    if code is None:
        kept.pop(name, None)
    else:
        kept[name] = code
    os.makedirs(os.path.dirname(_trust_path()), exist_ok=True)
    with open(_trust_path() + ".new", "w", encoding="utf-8") as fh:
        json.dump(kept, fh, indent=2)
    os.replace(_trust_path() + ".new", _trust_path())


# ---- what the wire says of one

def describe(name):
    """What Theme.json has of a theme beyond its colours: its manifest, its scene, its files, its code's hash and
    whether it's trusted. ValueError for a theme that isn't as one is."""
    have = listing(name)
    out = {"manifest": manifest(name), "files": have}
    if (s := scene(name)) is not None:
        out["scene"] = s
    if (code := code_hash(name, have)) is not None:
        out["code"], out["trusted"] = code, _trust_read().get(name) == code
    return out


# ---- a theme as one file

def pack(name):
    """A theme's folder as one file (<name>.baudetheme): its files zipped, as bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in listing(name):
            z.writestr(check_path(p), read_file(name, p))
    return buf.getvalue()


def install(data):
    """A theme put in this host's themes folder from its file (bytes; base64 text too): unzipped to one side, every
    file's path, kind and size checked as it comes out (no path out of the folder, no link, nothing that isn't a
    theme's, PACK_MAX in all whatever the zip says), its manifest, scene and tokens checked, then moved into place,
    over one of its name. Its code isn't trusted. (name, whether one was replaced)."""
    if isinstance(data, str):
        data = base64.b64decode(data, validate=True)
    if len(data) > PACK_MAX:
        raise ValueError(f"A theme's file is at most {PACK_MAX // (1024 * 1024)} MB.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("That isn't a theme's file (a zip of its folder).") from None
    entries = [i for i in z.infolist() if not i.is_dir()]
    if len(entries) > FILES_MAX:
        raise ValueError(f"A theme has at most {FILES_MAX} files.")
    os.makedirs(themes.folder(), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".install-", dir=themes.folder())
    try:
        total = 0
        for info in entries:
            path = check_path(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"{path}: a link, not a file.")
            dest = os.path.join(tmp, *path.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as fh:
                while chunk := src.read(65536):
                    total += len(chunk)
                    if total > PACK_MAX:
                        raise ValueError(f"A theme is at most {PACK_MAX // (1024 * 1024)} MB, unzipped.")
                    fh.write(chunk)
        try:
            with open(os.path.join(tmp, MANIFEST), encoding="utf-8") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            raise ValueError("A theme's file has its manifest.json.") from None
        except ValueError as e:
            raise ValueError(f"manifest.json isn't JSON: {e}") from None
        have = {i.filename for i in entries}
        check_manifest(doc, have)
        name = doc["name"]
        if SCENE in have:
            with open(os.path.join(tmp, SCENE), encoding="utf-8") as fh:
                check_scene(json.load(fh), have)
        if themes.TOKENS_FILE in have:
            with open(os.path.join(tmp, themes.TOKENS_FILE), encoding="utf-8") as fh:
                themes.colours(json.load(fh))
        where = folder(name)
        replaced = os.path.isdir(where)
        if replaced:
            old = where + ".old"
            shutil.rmtree(old, ignore_errors=True)
            os.replace(where, old)
            os.replace(tmp, where)
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.replace(tmp, where)
        return name, replaced
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
